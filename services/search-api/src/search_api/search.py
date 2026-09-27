"""Поисковый слой: картинка -> кандидаты из Qdrant."""

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

from .ocr import LabelReader
from .text_match import TextMatcher
from .vlm import VlmReranker

logger = logging.getLogger(__name__)


class InvalidImageError(Exception):
    """Загруженный файл не является изображением."""


@dataclass
class SearchResult:
    candidates: list[dict[str, Any]]
    decode_ms: float
    encode_ms: float
    search_ms: float
    ocr_ms: float = 0.0
    ocr_text: list[str] | None = None
    vlm_ms: float = 0.0
    vlm_none_prob: float | None = None

    @property
    def total_ms(self) -> float:
        return self.decode_ms + self.encode_ms + self.search_ms + self.ocr_ms + self.vlm_ms


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
        reader: LabelReader | None = None,
        matcher: TextMatcher | None = None,
        rerank_k: int = 20,
        text_weight: float = 0.0,
        vlm: VlmReranker | None = None,
        vlm_top_k: int = 5,
    ) -> None:
        if vlm is not None and not 1 <= vlm_top_k <= 9:
            raise ValueError("VLM выбирает из 1–9 кандидатов: ответ — одна цифра")
        self._encoder = encoder
        self._client = client
        self._collection = collection
        self._reader = reader
        self._matcher = matcher
        self._rerank_k = rerank_k
        self._text_weight = text_weight
        self._vlm = vlm
        self._vlm_top_k = vlm_top_k

    @property
    def ocr_enabled(self) -> bool:
        return self._reader is not None and self._matcher is not None

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
        # С OCR берём кандидатов с запасом: правильное вино одной линейки
        # картинка часто ставит не первым, но почти всегда в топ-20
        limit = max(top_k, self._rerank_k) if self.ocr_enabled else top_k
        # VLM выбирает из своей пятёрки даже тогда, когда наружу нужен один
        # ответ (/v1/eval/predict)
        if self.vlm_enabled:
            limit = max(limit, self._vlm_top_k)
        hits = self._client.query_points(
            collection_name=self._collection,
            query=vector.tolist(),
            limit=limit,
            with_payload=True,
        ).points
        search_ms = (time.perf_counter() - started) * 1000

        candidates = [
            {"image_score": float(hit.score), "text_score": 0.0, "matched_terms": [],
             "conflict_terms": [], "vlm_prob": None, **(hit.payload or {})}
            for hit in hits
        ]

        ocr_ms, ocr_text = 0.0, None
        if self.ocr_enabled:
            started = time.perf_counter()
            ocr_text = [line.text for line in self._reader.read(image)]
            evidence = self._matcher.prepare(ocr_text)
            for candidate in candidates:
                text = self._matcher.score(candidate, evidence)
                candidate["text_score"] = text.score
                candidate["matched_terms"] = text.matched
                candidate["conflict_terms"] = text.conflicts
            ocr_ms = (time.perf_counter() - started) * 1000

        # Косинусы кандидатов одной линейки различаются в сотых, text_score —
        # в десятых: вес держит текст решающим именно внутри линейки, но не
        # даёт ему вытащить вино, на которое картинка совсем не похожа
        for candidate in candidates:
            candidate["score"] = (
                candidate["image_score"] + self._text_weight * candidate["text_score"]
            )
        candidates.sort(key=lambda c: c["score"], reverse=True)

        vlm_ms, vlm_none_prob = 0.0, None
        if self.vlm_enabled and candidates:
            # Картинка и текст надёжно находят линейку, VLM выбирает вино
            # внутри неё — сверяя этикетку с карточками и эталонными фото
            started = time.perf_counter()
            judged = candidates[: self._vlm_top_k]
            verdict = self._vlm.judge(image, judged)
            for candidate, prob in zip(judged, verdict.probs):
                candidate["vlm_prob"] = prob
            # sorted стабилен: при равных вероятностях остаётся порядок по score
            judged = sorted(judged, key=lambda c: c["vlm_prob"], reverse=True)
            candidates = judged + candidates[self._vlm_top_k:]
            vlm_none_prob = verdict.none_prob
            vlm_ms = (time.perf_counter() - started) * 1000

        candidates = candidates[:top_k]

        if self.vlm_enabled:
            # С VLM уверенность — его вероятность: именно она решила порядок.
            # Кандидаты за пределами пятёрки VLM не видел
            confidences = [c["vlm_prob"] or 0.0 for c in candidates]
        else:
            confidences = _softmax([c["score"] for c in candidates])
        for rank, (candidate, confidence) in enumerate(zip(candidates, confidences), start=1):
            candidate["rank"] = rank
            candidate["confidence"] = confidence

        return SearchResult(
            candidates, decode_ms, encode_ms, search_ms, ocr_ms, ocr_text, vlm_ms, vlm_none_prob
        )
