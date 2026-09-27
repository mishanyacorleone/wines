"""FastAPI-приложение сервиса поиска вина по фото."""

from __future__ import annotations

import io
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from PIL import Image
from qdrant_client import QdrantClient

from wine_embeddings import get_encoder
from wine_embeddings.runtime import bypass_proxy_for_localhost

from .config import api_settings
from .schemas import (
    Candidate,
    HealthResponse,
    PredictResponse,
    SearchResponse,
    Timings,
)
from .ocr import LabelReader
from .search import InvalidImageError, SearchResult, WineSearcher
from .text_match import TextMatcher
from .vlm import VlmReranker

logger = logging.getLogger(__name__)

_state: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Модель и клиент поднимаются на старте, а не на первом запросе.

    По контракту организатора на запрос отводятся секунды — загрузка весов
    внутри обработчика в них не укладывается.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    bypass_proxy_for_localhost()
    encoder = get_encoder()
    client = QdrantClient(url=api_settings.qdrant_url)
    _state["encoder"] = encoder
    _state["client"] = client
    reader, matcher = _build_ocr(client, gpu=encoder.device != "cpu")
    _state["searcher"] = WineSearcher(
        encoder,
        client,
        api_settings.collection,
        reader=reader,
        matcher=matcher,
        rerank_k=api_settings.rerank_k,
        text_weight=api_settings.text_weight,
        vlm=_build_vlm(encoder.device),
        vlm_top_k=api_settings.vlm_top_k,
    )
    _warmup(_state["searcher"])  # type: ignore[arg-type]
    logger.info("Сервис готов: %s, OCR %s, VLM %s", encoder.settings.model_id,
                "включён" if reader else "выключен",
                "включён" if api_settings.vlm_enabled else "выключен")
    yield
    client.close()


def _warmup(searcher: WineSearcher) -> None:
    """Холостой поиск на старте.

    Первый проход через CUDA-ядра энкодера, OCR и VLM и первый запрос к Qdrant
    в разы медленнее следующих: без прогрева первый ответ занимал 3,4 с при
    SLA 3 с, и платил бы за это первый запрос организатора.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), "white").save(buffer, format="PNG")
    searcher.search(buffer.getvalue(), 1)


def _build_ocr(client: QdrantClient, *, gpu: bool) -> tuple[LabelReader | None, TextMatcher | None]:
    """OCR и словарь каталога для переранжирования.

    Словарь (веса слов по редкости) строится по payload из Qdrant, а не по
    файлам каталога: API не должен знать, где лежит каталог, и индекс остаётся
    единственным источником истины о том, какие вина вообще можно вернуть.
    """
    if not api_settings.ocr_enabled:
        return None, None

    payloads, offset = [], None
    while True:
        points, offset = client.scroll(
            api_settings.collection, limit=1000, offset=offset, with_payload=True
        )
        payloads.extend(p.payload or {} for p in points)
        if offset is None:
            break
    matcher = TextMatcher(payloads)

    reader = LabelReader(
        api_settings.ocr_models_dir,
        gpu=gpu,
        max_side=api_settings.ocr_max_side,
        min_confidence=api_settings.ocr_min_confidence,
    )
    logger.info("Словарь OCR: %d слов по %d винам", len(matcher), len(payloads))
    return reader, matcher


def _build_vlm(device: str) -> VlmReranker | None:
    """VLM-реранкер, если включён в настройках.

    Лимит VRAM процесса к этому моменту уже выставлен энкодером — VLM
    укладывается в него же, а не в отдельный.
    """
    if not api_settings.vlm_enabled:
        return None
    if not (api_settings.vlm_model_dir / "config.json").exists():
        raise RuntimeError(
            f"WINE_VLM_ENABLED=1, но весов нет в {api_settings.vlm_model_dir}: "
            "скачайте их infra/fetch-vlm.sh"
        )
    return VlmReranker(
        api_settings.vlm_model_dir,
        device=device,
        query_max_pixels=api_settings.vlm_query_max_pixels,
        candidate_max_pixels=api_settings.vlm_candidate_max_pixels,
        with_candidate_images=api_settings.vlm_candidate_images,
        catalog_dir=api_settings.catalog_dir,
    )


app = FastAPI(
    title="Сканер вин «Своё Вино»",
    description="Поиск карточки вина по фотографии этикетки.",
    version="0.1.0",
    lifespan=lifespan,
)


def _searcher() -> WineSearcher:
    searcher = _state.get("searcher")
    if searcher is None:
        raise HTTPException(status_code=503, detail="Сервис ещё не инициализирован")
    return searcher  # type: ignore[return-value]


async def _read_upload(image: UploadFile) -> bytes:
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > api_settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    return raw


def _run_search(raw: bytes, top_k: int) -> SearchResult:
    try:
        return _searcher().search(raw, top_k)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail=f"Не изображение: {exc}") from exc


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    encoder = _state["encoder"]
    client: QdrantClient = _state["client"]  # type: ignore[assignment]
    info = client.get_collection(api_settings.collection)
    return HealthResponse(
        status="ok",
        model_id=encoder.settings.model_id,  # type: ignore[union-attr]
        device=encoder.device,  # type: ignore[union-attr]
        collection=api_settings.collection,
        indexed_points=info.points_count or 0,
        vector_size=info.config.params.vectors.size,
        ocr_enabled=_searcher().ocr_enabled,
        vlm_enabled=_searcher().vlm_enabled,
    )


@app.post("/v1/search", response_model=SearchResponse)
async def search(
    image: UploadFile = File(..., description="фотография этикетки"),
    top_k: int = Query(default=api_settings.default_top_k, ge=1),
) -> SearchResponse:
    """Топ-k кандидатов с confidence и ссылкой на карточку.

    Основная ручка для анализа: отдаёт не один ответ, а всю выдачу, чтобы
    было видно, что именно вытаскивает модель и насколько она уверена.
    """
    top_k = min(top_k, api_settings.max_top_k)
    raw = await _read_upload(image)
    result = _run_search(raw, top_k)

    candidates = [Candidate(**_candidate_fields(c)) for c in result.candidates]
    confidences = [c.confidence for c in candidates]
    # margin — по тому же сигналу, что задал порядок: с VLM score не монотонен
    ranking = confidences if result.vlm_none_prob is not None else [c.score for c in candidates]

    return SearchResponse(
        results=candidates,
        top1_confidence=confidences[0] if confidences else 0.0,
        top5_confidence=sum(confidences[:5]),
        margin=(ranking[0] - ranking[1]) if len(ranking) > 1 else 0.0,
        ocr_text=result.ocr_text,
        vlm_none_prob=result.vlm_none_prob,
        timings=Timings(
            decode_ms=round(result.decode_ms, 2),
            encode_ms=round(result.encode_ms, 2),
            search_ms=round(result.search_ms, 2),
            ocr_ms=round(result.ocr_ms, 2),
            vlm_ms=round(result.vlm_ms, 2),
            total_ms=round(result.total_ms, 2),
        ),
    )


@app.post("/v1/eval/predict", response_model=PredictResponse)
async def predict(image: UploadFile = File(...)) -> PredictResponse:
    """Контракт скрипта оценки кейсодержателя: плоский {"slug": "..."}."""
    raw = await _read_upload(image)
    result = _run_search(raw, 1)
    if not result.candidates:
        raise HTTPException(status_code=404, detail="Ничего не найдено")
    return PredictResponse(slug=result.candidates[0]["slug"])


def _candidate_fields(candidate: dict) -> dict:
    """Оставляет только поля схемы: в payload лежит и служебное (image_path)."""
    allowed = set(Candidate.model_fields)
    return {k: v for k, v in candidate.items() if k in allowed}
