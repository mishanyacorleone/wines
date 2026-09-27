"""HTTP-клиент с ретраями и троттлингом."""

from __future__ import annotations

import asyncio
import logging
import random

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

# Коды, на которых есть смысл повторить запрос.
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """Запрос не удался после всех попыток."""


def build_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=settings.timeout_sec,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=settings.concurrency),
    )


async def fetch(
    client: httpx.AsyncClient, url: str, settings: Settings
) -> httpx.Response:
    """GET с экспоненциальным backoff. Бросает FetchError, если не вышло."""
    last_error = "неизвестная ошибка"

    for attempt in range(1, settings.max_retries + 1):
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUS:
                break

        if attempt < settings.max_retries:
            # джиттер, чтобы параллельные воркеры не долбили сервер синхронно
            backoff = settings.delay_sec * (2**attempt) + random.uniform(0, 0.3)
            logger.debug("Повтор %s (%s), попытка %d", url, last_error, attempt)
            await asyncio.sleep(backoff)

    raise FetchError(f"{url}: {last_error}")
