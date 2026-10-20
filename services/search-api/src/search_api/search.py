"""Поисковый слой: картинка → вектор (embedder) → кандидаты из Qdrant → выбор VLM (vLLM).

Модели — в своих сервисах; здесь только порядок шагов. Пайплайн асинхронный:
пока один запрос ждёт модель, api принимает следующие, и vLLM собирает
параллельные запросы в батч.

Что падает и что тогда:
- embedder или Qdrant — `ServiceUnavailable`: искать нечем, api отвечает 503;
- vLLM — ответ по одной картинке (`degraded`): порядок векторного поиска,
  решение «уточните» и список вариантов вместо одной карточки.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image
from qdrant_client import QdrantClient

from .remote import EmbedderClient, InvalidImageError, QdrantUnavailable, describe_error
from .vlm import MAX_CANDIDATES, VlmClient, VlmUnavailable

logger = logging.getLogger("search")

__all__ = ["InvalidImageError", "SearchResult", "WineSearcher"]


@dataclass
class SearchResult:
    candidates: list[dict[str, Any]]
    decode_ms: float
    encode_ms: float
    search_ms: float
    vlm_ms: float = 0.0
    vlm_none_prob: float | None = None
    # вероятность, что топ-1 — действительно вино с фото (VlmClient.verify);
    # None — VLM выключен, недоступен или у топ-1 нет эталонного фото
    verify_prob: float | None = None
    # VLM включён, но не ответил: результат — по одной картинке
    degraded: bool = False
    total_ms: float = 0.0


def _softmax(scores: list[float], temperature: float = 0.05) -> list[float]:
    """Переводит косинусные сходства в доли уверенности.

    Косинусы соседних кандидатов различаются на сотые доли, поэтому без
    температуры softmax выдал бы почти равномерное распределение и confidence
    перестал бы что-либо означать.
    """
    if not scores:
        return []
    scaled = [s / temperature for s in scores]
    peak = max(scaled)
    exponents = [math.exp(s - peak) for s in scaled]
    total = sum(exponents)
    return [e / total for e in exponents]


def _decode(raw: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # Pillow бросает разнородные исключения
        raise InvalidImageError(str(exc)) from exc
    return image


class WineSearcher:
    def __init__(
        self,
        embedder: EmbedderClient,
        client: QdrantClient,
        collection: str,
        *,
        vlm: VlmClient | None = None,
        vlm_top_k: int = 5,
    ) -> None:
        if vlm is not None and not 1 <= vlm_top_k <= MAX_CANDIDATES:
            raise ValueError(f"VLM выбирает из 1–{MAX_CANDIDATES} кандидатов")
        self._embedder = embedder
        self._client = client
        self._collection = collection
        self._vlm = vlm
        self._vlm_top_k = vlm_top_k

    @property
    def vlm_enabled(self) -> bool:
        return self._vlm is not None

    async def search(self, raw_image: bytes, top_k: int) -> SearchResult:
        started_total = time.perf_counter()
        started = time.perf_counter()
        image = await asyncio.to_thread(_decode, raw_image)
        decode_ms = (time.perf_counter() - started) * 1000

        # Фото для VLM готовится, пока считается вектор: это CPU api, а не GPU
        prepared = asyncio.create_task(self._vlm.prepare(image)) if self._vlm is not None else None
        try:
            vector, encode_ms = await self._embedder.embed(raw_image)
        except BaseException:
            if prepared is not None:
                prepared.cancel()
            raise

        started = time.perf_counter()
        # VLM выбирает из своих кандидатов даже тогда, когда наружу нужен один
        # ответ (/v1/eval/predict)
        limit = max(top_k, self._vlm_top_k) if self.vlm_enabled else top_k
        try:
            hits = (await asyncio.to_thread(
                self._client.query_points,
                collection_name=self._collection, query=vector, limit=limit, with_payload=True,
            )).points
        except Exception as exc:  # сеть, Qdrant перезапускается
            if prepared is not None:
                prepared.cancel()
            raise QdrantUnavailable("Qdrant", describe_error(exc)) from exc
        search_ms = (time.perf_counter() - started) * 1000

        candidates = [
            {"score": float(hit.score), "image_score": float(hit.score), "vlm_prob": None,
             **(hit.payload or {})}
            for hit in hits
        ]

        vlm_ms, vlm_none_prob, verify_prob, degraded = 0.0, None, None, False
        if prepared is not None and candidates:
            # Картинка надёжно находит линейку, VLM выбирает вино внутри неё —
            # сверяя этикетку с карточками и эталонными фото, — а затем
            # отдельно проверяет, что выбранное вино и есть вино с фото
            started = time.perf_counter()
            try:
                query = await prepared
                judged = candidates[: self._vlm_top_k]
                verdict = await self._vlm.judge(query, judged)
                for candidate, prob in zip(judged, verdict.probs):
                    candidate["vlm_prob"] = prob
                # sorted стабилен: при равных вероятностях остаётся порядок по картинке
                judged = sorted(judged, key=lambda c: c["vlm_prob"], reverse=True)
                candidates = judged + candidates[self._vlm_top_k:]
                vlm_none_prob = verdict.none_prob
                verify_prob = await self._vlm.verify(query, candidates[0])
            except VlmUnavailable:
                # Ответ по картинке: порядок векторного поиска, без вероятностей VLM
                for candidate in candidates:
                    candidate["vlm_prob"] = None
                candidates.sort(key=lambda c: c["score"], reverse=True)
                vlm_none_prob, verify_prob, degraded = None, None, True
            vlm_ms = (time.perf_counter() - started) * 1000
        elif prepared is not None:
            prepared.cancel()

        candidates = candidates[:top_k]

        if self.vlm_enabled and not degraded:
            # С VLM уверенность — его вероятность: именно она решила порядок.
            # Кандидаты за пределами его выборки VLM не видел
            confidences = [c["vlm_prob"] or 0.0 for c in candidates]
        else:
            confidences = _softmax([c["score"] for c in candidates])
        for rank, (candidate, confidence) in enumerate(zip(candidates, confidences), start=1):
            candidate["rank"] = rank
            candidate["confidence"] = confidence

        return SearchResult(
            candidates, decode_ms, encode_ms, search_ms, vlm_ms, vlm_none_prob, verify_prob,
            degraded=degraded, total_ms=(time.perf_counter() - started_total) * 1000,
        )
