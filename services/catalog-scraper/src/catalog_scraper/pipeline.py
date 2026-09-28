"""Оркестрация обхода каталога: sitemap -> страницы -> фото -> JSONL."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from tqdm.asyncio import tqdm_asyncio

from .config import Settings
from .http_client import FetchError, build_client, fetch
from .images import ImageError, download_png
from .models import Failure, Wine
from .nuxt import NuxtParseError, parse_wine
from .sitemap import load_wine_urls
from .storage import JsonlWriter, completed_slugs, write_failure, write_wine

logger = logging.getLogger(__name__)


@dataclass
class ScrapeStats:
    total: int = 0
    scraped: int = 0
    skipped: int = 0
    failed: int = 0


def slug_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


async def scrape_one(
    client: httpx.AsyncClient, url: str, settings: Settings
) -> Wine | Failure:
    """Обрабатывает одну страницу вина. Исключения наружу не выпускает —
    падение одной страницы не должно ронять весь прогон."""
    url_slug = slug_from_url(url)

    try:
        response = await fetch(client, url, settings)
    except FetchError as exc:
        return Failure(url_slug, url, "fetch_page", str(exc))

    try:
        wine = parse_wine(response.text, url)
    except NuxtParseError as exc:
        return Failure(url_slug, url, "parse", str(exc))

    if wine.slug != url_slug:
        # slug в данных — источник истины (он же нужен в ответе eval),
        # но расхождение стоит видеть в логах
        logger.warning("slug из URL (%s) != slug из данных (%s)", url_slug, wine.slug)

    if not wine.image_url:
        return Failure(wine.slug, url, "parse", "у вина нет image.url")

    dest = settings.images_dir / f"{wine.slug}.png"
    try:
        width, height = await download_png(client, wine.image_url, dest, settings)
    except (FetchError, ImageError) as exc:
        return Failure(wine.slug, url, "image_download", str(exc))

    wine.image_path = str(dest.relative_to(settings.output_dir))
    wine.image_width = width
    wine.image_height = height
    return wine


async def scrape_catalog(
    settings: Settings,
    *,
    limit: int | None = None,
    resume: bool = True,
    refresh_sitemap: bool = False,
) -> ScrapeStats:
    stats = ScrapeStats()
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    async with build_client(settings) as client:
        urls = await load_wine_urls(client, settings, use_cache=not refresh_sitemap)
        stats.total = len(urls)
        logger.info("В sitemap %d вин", len(urls))

        done: set[str] = completed_slugs(settings.catalog_path, settings.images_dir) if resume else set()
        if done:
            logger.info("Уже собрано ранее: %d — пропускаю", len(done))

        pending = [u for u in urls if slug_from_url(u) not in done]
        stats.skipped = len(urls) - len(pending)
        if limit is not None:
            pending = pending[:limit]

        semaphore = asyncio.Semaphore(settings.concurrency)

        with (
            JsonlWriter(settings.catalog_path, append=resume) as catalog,
            JsonlWriter(settings.failures_path, append=False) as failures,
        ):
            lock = asyncio.Lock()

            async def worker(url: str) -> None:
                async with semaphore:
                    result = await scrape_one(client, url, settings)
                    # троттлинг: держим темп даже при параллельных воркерах
                    await asyncio.sleep(settings.delay_sec)

                async with lock:
                    if isinstance(result, Wine):
                        write_wine(catalog, result)
                        stats.scraped += 1
                    else:
                        write_failure(failures, result)
                        stats.failed += 1
                        logger.debug("Не вышло %s: %s", result.slug, result.reason)

            await tqdm_asyncio.gather(
                *(worker(url) for url in pending), desc="Каталог vino-svoe.ru"
            )

    return stats
