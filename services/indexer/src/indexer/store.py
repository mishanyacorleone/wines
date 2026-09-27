"""Работа с коллекцией Qdrant."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Sequence

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger(__name__)

# Пространство имён для детерминированных id точек: один и тот же slug
# всегда даёт один и тот же id, поэтому переиндексация не плодит дубли,
# а перезаписывает существующую точку.
POINT_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


def point_id(slug: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, slug))


def ensure_collection(
    client: QdrantClient, collection: str, dimension: int, *, recreate: bool = False
) -> None:
    exists = client.collection_exists(collection)

    if exists and recreate:
        logger.info("Удаляю существующую коллекцию %s", collection)
        client.delete_collection(collection)
        exists = False

    if exists:
        info = client.get_collection(collection)
        current = info.config.params.vectors.size
        if current != dimension:
            raise ValueError(
                f"Коллекция {collection} создана под размерность {current}, "
                f"а модель выдаёт {dimension}. Нужен --recreate."
            )
        return

    logger.info("Создаю коллекцию %s (dim=%d, cosine)", collection, dimension)
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
    )


def upsert_batch(
    client: QdrantClient,
    collection: str,
    slugs: Sequence[str],
    vectors: np.ndarray,
    payloads: Sequence[dict[str, Any]],
) -> None:
    points = [
        PointStruct(id=point_id(slug), vector=vector.tolist(), payload=payload)
        for slug, vector, payload in zip(slugs, vectors, payloads)
    ]
    client.upsert(collection_name=collection, points=points, wait=True)
