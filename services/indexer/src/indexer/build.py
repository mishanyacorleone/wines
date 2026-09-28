"""Построение индекса: каталог -> эмбеддинги -> Qdrant."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from PIL import Image
from qdrant_client import QdrantClient
from tqdm import tqdm

from wine_embeddings import get_encoder
from wine_embeddings.runtime import bypass_proxy_for_localhost, vram_used_gb

from .catalog import load_catalog
from .config import IndexSettings
from .store import ensure_collection, overwrite_payloads, upsert_batch

logger = logging.getLogger(__name__)


@dataclass
class IndexStats:
    indexed: int = 0
    skipped: int = 0
    seconds: float = 0.0
    dimension: int = 0
    vram_peak_gb: float = 0.0


def build_index(
    settings: IndexSettings, *, recreate: bool = False, limit: int | None = None
) -> IndexStats:
    entries = load_catalog(settings.catalog_path, settings.site_base_url)
    if limit is not None:
        entries = entries[:limit]
    logger.info("Записей в каталоге: %d", len(entries))

    bypass_proxy_for_localhost()
    encoder = get_encoder()
    client = QdrantClient(url=settings.qdrant_url)
    ensure_collection(client, settings.collection, encoder.dimension, recreate=recreate)

    stats = IndexStats(dimension=encoder.dimension)
    batch_size = encoder.settings.batch_size
    started = time.perf_counter()

    for start in tqdm(range(0, len(entries), batch_size), desc="Индексация"):
        batch = entries[start : start + batch_size]

        images, kept = [], []
        for entry in batch:
            try:
                with Image.open(entry.image_path) as image:
                    image.load()
                    images.append(image)
                kept.append(entry)
            except Exception as exc:
                # одна битая картинка не должна ронять весь прогон
                logger.warning("Пропускаю %s: %s", entry.slug, exc)
                stats.skipped += 1

        if not kept:
            continue

        vectors = encoder.encode_images(images)
        upsert_batch(
            client,
            settings.collection,
            [e.slug for e in kept],
            vectors,
            [e.payload for e in kept],
        )
        stats.indexed += len(kept)

    stats.seconds = time.perf_counter() - started
    stats.vram_peak_gb = vram_used_gb()
    return stats


def refresh_payloads(settings: IndexSettings, *, batch_size: int = 256) -> IndexStats:
    """Обновляет только payload точек из catalog.jsonl — без модели и GPU.

    Нужен, когда в карточку добавились поля (блюда, температура подачи,
    описание), а картинки не менялись: секунды вместо переиндексации.
    Вина, которых ещё нет в коллекции, пропускаются — их добавляет обычный прогон.
    """
    entries = load_catalog(settings.catalog_path, settings.site_base_url)
    bypass_proxy_for_localhost()
    client = QdrantClient(url=settings.qdrant_url)

    stats = IndexStats()
    started = time.perf_counter()
    for start in tqdm(range(0, len(entries), batch_size), desc="Payload"):
        batch = entries[start : start + batch_size]
        updated = overwrite_payloads(
            client, settings.collection, [e.slug for e in batch], [e.payload for e in batch]
        )
        stats.indexed += updated
        stats.skipped += len(batch) - updated
    stats.seconds = time.perf_counter() - started
    return stats
