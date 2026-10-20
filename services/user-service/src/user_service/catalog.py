"""Карточки вин из Qdrant — для снимка в истории и для условий достижений.

Сервис не держит свою копию каталога: вина добавляются в индекс каждый день,
и копия бы отставала. Qdrant — та же коллекция, что у сервиса поиска.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

logger = logging.getLogger(__name__)

# Регионы каталога перечитываются не чаще раза в 10 минут: синхронизация с сайтом
# может добавить вино из нового региона без перезапуска сервисов
_REGIONS_TTL_S = 600

# Поля карточки, которые нужны истории и достижениям
_FIELDS = ["slug", "title", "manufacturer", "region", "category", "grapes", "image_path", "source_url"]


class CatalogUnavailable(RuntimeError):
    """Qdrant не отвечает: скан с вином записать нельзя — не из чего взять регион и сорт."""


class Catalog(Protocol):
    def get(self, slug: str) -> dict | None: ...

    def regions(self) -> list[str]: ...

    def wines(self) -> list[dict]: ...


def wine_snapshot(payload: dict) -> dict:
    image_path = payload.get("image_path")
    return {
        "slug": payload["slug"],
        "title": payload.get("title") or payload["slug"],
        "manufacturer": payload.get("manufacturer"),
        "region": payload.get("region"),
        "category": payload.get("category"),
        "grapes": list(payload.get("grapes") or []),
        # Фото бутылки отдаёт сервис поиска (/catalog-images/) — через тот же шлюз
        "image_url": "/catalog-images/" + image_path.removeprefix("images/") if image_path else None,
        "url": payload.get("source_url"),
    }


class QdrantCatalog:
    def __init__(self, url: str, collection: str) -> None:
        self._client = QdrantClient(url=url, timeout=5)
        self._collection = collection
        self._cache: dict[str, dict | None] = {}
        self._regions: list[str] | None = None
        self._regions_at = 0.0
        self._wines: list[dict] | None = None
        self._wines_at = 0.0
        self._lock = threading.Lock()

    def get(self, slug: str) -> dict | None:
        with self._lock:
            if slug in self._cache:
                return self._cache[slug]
        try:
            points, _ = self._client.scroll(
                self._collection,
                scroll_filter=Filter(must=[FieldCondition(key="slug", match=MatchValue(value=slug))]),
                limit=1,
                with_payload=_FIELDS,
                with_vectors=False,
            )
        except Exception as exc:  # сеть, Qdrant перезапускается
            raise CatalogUnavailable(str(exc)) from exc
        wine = wine_snapshot(points[0].payload) if points and points[0].payload else None
        if wine is not None:  # неизвестный slug не кэшируем: вино могут добавить
            with self._lock:
                self._cache[slug] = wine
        return wine

    def regions(self) -> list[str]:
        """Регионы каталога — подсказка админу для правила «вина из региона»."""
        if self._regions is not None and time.monotonic() - self._regions_at < _REGIONS_TTL_S:
            return self._regions
        found: set[str] = set()
        offset = None
        try:
            while True:
                points, offset = self._client.scroll(
                    self._collection, limit=1000, offset=offset, with_payload=["region"], with_vectors=False
                )
                found.update(p.payload["region"] for p in points if p.payload and p.payload.get("region"))
                if offset is None:
                    break
        except Exception as exc:
            logger.warning("Регионы каталога недоступны: %s", exc)
            return self._regions or []
        self._regions, self._regions_at = sorted(found), time.monotonic()
        return self._regions

    def wines(self) -> list[dict]:
        """Все вина каталога (снимки) — для вопросов дня по бутылке. Кэш на 10 минут."""
        if self._wines is not None and time.monotonic() - self._wines_at < _REGIONS_TTL_S:
            return self._wines
        found: list[dict] = []
        offset = None
        try:
            while True:
                points, offset = self._client.scroll(
                    self._collection, limit=500, offset=offset, with_payload=_FIELDS, with_vectors=False
                )
                found.extend(wine_snapshot(p.payload) for p in points if p.payload and p.payload.get("slug"))
                if offset is None:
                    break
        except Exception as exc:
            logger.warning("Вина каталога недоступны: %s", exc)
            return self._wines or []
        found.sort(key=lambda w: w["slug"])  # порядок не зависит от Qdrant: вопрос дня одинаков у всех
        self._wines, self._wines_at = found, time.monotonic()
        return self._wines
