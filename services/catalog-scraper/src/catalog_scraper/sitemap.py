"""Получение списка URL вин из официального sitemap."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .config import Settings
from .http_client import fetch

logger = logging.getLogger(__name__)

_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def parse_sitemap(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    urls = [loc.text.strip() for loc in root.findall(".//sm:loc", _NS) if loc.text]
    # порядок в sitemap не гарантирован и меняется между выгрузками —
    # сортируем, чтобы прогоны были воспроизводимы
    return sorted(set(urls))


async def load_wine_urls(
    client: httpx.AsyncClient, settings: Settings, *, use_cache: bool = True
) -> list[str]:
    """Берёт sitemap из кэша на диске либо скачивает и кэширует."""
    cache: Path = settings.sitemap_cache_path

    if use_cache and cache.exists():
        logger.info("Использую закэшированный sitemap: %s", cache)
        return parse_sitemap(cache.read_text(encoding="utf-8"))

    logger.info("Скачиваю sitemap: %s", settings.sitemap_url)
    response = await fetch(client, settings.sitemap_url, settings)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(response.text, encoding="utf-8")
    return parse_sitemap(response.text)
