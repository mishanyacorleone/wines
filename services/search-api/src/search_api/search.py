"""Поисковый слой: картинка -> кандидаты из Qdrant -> выбор VLM."""

from __future__ import annotations

import io
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image
from qdrant_client import QdrantClient

from wine_embeddings import ImageEncoder

from .vlm import MAX_CANDIDATES, VlmReranker

logger = logging.getLogger(__name__)


class InvalidImageError(Exception):
    """Загруженный файл не является изображением."""


@dataclass
class SearchResult:
    candidates: list[dict[str, Any]]
    decode_ms: float
    encode_ms: float
    search_ms: float
    vlm_ms: float = 0.0
    vlm_none_prob: float | None = None
    # вероятность, что топ-1 — действительно вино с фото (VlmReranker.verify);
    # None — VLM выключен или у топ-1 нет эталонного фото
    verify_prob: float | None = None

    @property
    def total_ms(self) -> float:
        return self.decode_ms + self.encode_ms + self.search_ms + self.vlm_ms


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


class WineSearcher:
    def __init__(
        self,
        encoder: ImageEncoder,
        client: QdrantClient,
        collection: str,
        *,
        vlm: VlmReranker | None = None,
        vlm_top_k: int = 5,
    ) -> None:
        if vlm is not None and not 1 <= vlm_top_k <= MAX_CANDIDATES:
            raise ValueError(f"VLM выбирает из 1–{MAX_CANDIDATES} кандидатов")
        self._encoder = encoder
        self._client = client
        self._collection = collection
        self._vlm = vlm
        self._vlm_top_k = vlm_top_k

    @property
    def vlm_enabled(self) -> bool:
        return self._vlm is not None

    def search(self, raw_image: bytes, top_k: int) -> SearchResult:
        started = time.perf_counter()
        try:
            image = Image.open(io.BytesIO(raw_image))
            image.load()
        except Exception as exc:
            raise InvalidImageError(str(exc)) from exc
        decode_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        vector = self._encoder.encode_image(image)
        encode_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        # VLM выбирает из своих кандидатов даже тогда, когда наружу нужен один
        # ответ (/v1/eval/predict)
        limit = max(top_k, self._vlm_top_k) if self.vlm_enabled else top_k
        hits = self._client.query_points(
            collection_name=self._collection,
            query=vector.tolist(),
            limit=limit,
            with_payload=True,
        ).points
        search_ms = (time.perf_counter() - started) * 1000

        candidates = [
            {"score": float(hit.score), "image_score": float(hit.score), "vlm_prob": None,
             **(hit.payload or {})}
            for hit in hits
        ]

        vlm_ms, vlm_none_prob, verify_prob = 0.0, None, None
        if self.vlm_enabled and candidates:
            # Картинка надёжно находит линейку, VLM выбирает вино внутри неё —
            # сверяя этикетку с карточками и эталонными фото, — а затем
            # отдельно проверяет, что выбранное вино и есть вино с фото
            started = time.perf_counter()
            judged = candidates[: self._vlm_top_k]
            verdict = self._vlm.judge(image, judged)
            for candidate, prob in zip(judged, verdict.probs):
                candidate["vlm_prob"] = prob
            # sorted стабилен: при равных вероятностях остаётся порядок по картинке
            judged = sorted(judged, key=lambda c: c["vlm_prob"], reverse=True)
            candidates = judged + candidates[self._vlm_top_k:]
            vlm_none_prob = verdict.none_prob
            verify_prob = self._vlm.verify(image, candidates[0])
            vlm_ms = (time.perf_counter() - started) * 1000

        candidates = candidates[:top_k]

        if self.vlm_enabled:
            # С VLM уверенность — его вероятность: именно она решила порядок.
            # Кандидаты за пределами его выборки VLM не видел
            confidences = [c["vlm_prob"] or 0.0 for c in candidates]
        else:
            confidences = _softmax([c["score"] for c in candidates])
        for rank, (candidate, confidence) in enumerate(zip(candidates, confidences), start=1):
            candidate["rank"] = rank
            candidate["confidence"] = confidence

        return SearchResult(
            candidates, decode_ms, encode_ms, search_ms, vlm_ms, vlm_none_prob, verify_prob
        )
