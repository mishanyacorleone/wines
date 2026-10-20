"""Каталог в памяти api: карточки вин для сомелье, поиска по тексту, регионов.

Источник истины — коллекция Qdrant (её пополняют индексатор и синхронизация
с сайтом). api читает её целиком при старте и дальше сверяется раз в минуту:
изменилось число вин — перечитывает. Синхронизация после добавления вин
просит перечитать сразу (/internal/catalog/reload).

Qdrant недоступен при старте — api всё равно поднимается: страницы, которым
каталог не нужен, работают, а загрузка повторяется в фоне.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

from qdrant_client import QdrantClient

from .sommelier import SiteMedia, Sommelier

logger = logging.getLogger("catalog")


class CatalogStore:
    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        media: SiteMedia,
        *,
        on_loaded: Callable[[list[dict]], None] | None = None,
    ) -> None:
        self._client = client
        self._collection = collection
        self._media = media
        self._on_loaded = on_loaded
        self._lock = threading.Lock()
        self.sommelier: Sommelier | None = None
        self.points = 0
        self.loaded_at: float | None = None

    @property
    def ready(self) -> bool:
        return self.sommelier is not None

    def points_count(self) -> int:
        return self._client.count(self._collection, exact=True).count

    def load(self, reason: str) -> int:
        """Перечитывает все payload коллекции; возвращает, сколько вин добавилось."""
        with self._lock:
            started = time.perf_counter()
            payloads = self._read_payloads()
            sommelier = Sommelier(payloads, self._media)
            before = len(self.sommelier) if self.sommelier is not None else None
            self.sommelier, self.points, self.loaded_at = sommelier, len(payloads), time.time()
        added = len(sommelier) - before if before is not None else 0
        if before is None:
            logger.info("Каталог загружен: %d вин за %.1f с", len(sommelier), time.perf_counter() - started)
            if not sommelier.has_pairing_data:
                # Индекс построен до того, как в payload появились блюда и подача
                logger.warning("В индексе нет блюд и температуры подачи — обновите payload: "
                               "python -m indexer --payload-only")
        else:
            logger.info("Каталог перечитан (%s): %d вин, изменение %+d", reason, len(sommelier), added)
        if self._on_loaded is not None:
            self._on_loaded(payloads)
        return added

    def _read_payloads(self) -> list[dict]:
        payloads, offset = [], None
        while True:
            # Порции по 256: 1000 payload — 1,2 МБ, на нагруженной машине Qdrant отдавал
            # их 5–9 с и клиент с таймаутом по умолчанию (5 с) ронял старт сервиса (19.10)
            points, offset = self._client.scroll(
                self._collection, limit=256, offset=offset, with_payload=True, with_vectors=False
            )
            payloads.extend(p.payload or {} for p in points)
            if offset is None:
                return payloads

    async def keep_fresh(self, refresh_s: float) -> None:
        """Фоновая задача: первая загрузка с повторами, потом сверка с Qdrant."""
        delay = 2.0
        while not self.ready:
            try:
                await asyncio.to_thread(self.load, "старт")
            except Exception as exc:  # noqa: BLE001 — Qdrant ещё не поднялся
                logger.warning("Каталог пока недоступен (%s: %s) — повтор через %.0f с",
                               type(exc).__name__, str(exc)[:120], delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        while True:
            await asyncio.sleep(refresh_s)
            try:
                count = await asyncio.to_thread(self.points_count)
                if count != self.points:
                    await asyncio.to_thread(self.load, f"в Qdrant {count} вин вместо {self.points}")
            except Exception as exc:  # noqa: BLE001 — переживём до следующей сверки
                logger.warning("Сверка каталога с Qdrant не удалась: %s", str(exc)[:200])
