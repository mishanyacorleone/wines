"""FastAPI-приложение сервиса поиска вина по фото."""

from __future__ import annotations

import io
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from qdrant_client import QdrantClient

from wine_embeddings import get_encoder
from wine_embeddings.runtime import bypass_proxy_for_localhost

from .config import api_settings
from .schemas import (
    Candidate,
    DishListItem,
    DishPairingResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    PredictResponse,
    ScanResponse,
    SearchResponse,
    Timings,
    WineCard,
    WineSummary,
)
from .ocr import LabelReader
from .search import InvalidImageError, SearchResult, WineSearcher
from .sommelier import SiteMedia, Sommelier, dish_icon
from .text_match import TextMatcher
from .verdict import Thresholds, decide
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
    payloads = _load_payloads(client)
    _state["sommelier"] = _build_sommelier(payloads)
    reader, matcher = _build_ocr(payloads, gpu=encoder.device != "cpu")
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


def _load_payloads(client: QdrantClient) -> list[dict]:
    """Все payload коллекции — для словаря OCR и для сомелье.

    Берутся из Qdrant, а не из файлов каталога: API не должен знать, где лежит
    каталог, и индекс остаётся единственным источником истины о том, какие
    вина вообще можно вернуть.
    """
    payloads, offset = [], None
    while True:
        points, offset = client.scroll(
            api_settings.collection, limit=1000, offset=offset, with_payload=True
        )
        payloads.extend(p.payload or {} for p in points)
        if offset is None:
            break
    return payloads


def _build_sommelier(payloads: list[dict]) -> Sommelier:
    sommelier = Sommelier(payloads, SiteMedia.load(image_api=api_settings.site_image_api))
    if not sommelier.has_pairing_data:
        # Индекс построен до того, как в payload появились блюда и подача:
        # карточка будет без рекомендаций, пока не обновить payload
        logger.warning(
            "В индексе нет блюд и температуры подачи — обновите payload: "
            "python -m indexer --payload-only"
        )
    logger.info("Сомелье: %d вин", len(sommelier))
    return sommelier


def _build_ocr(payloads: list[dict], *, gpu: bool) -> tuple[LabelReader | None, TextMatcher | None]:
    """OCR и словарь каталога (веса слов по редкости) для переранжирования."""
    if not api_settings.ocr_enabled:
        return None, None

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
    version="0.2.0",
    lifespan=lifespan,
)

_WEB_DIR = Path(__file__).resolve().parent / "web"
if api_settings.web_enabled:
    # Мобильный сканер: страница и её статика отдаются тем же сервисом, что и
    # API, — один процесс, один порт, никакого CORS
    app.mount("/app", StaticFiles(directory=_WEB_DIR, html=True), name="web")
_CATALOG_IMAGES = api_settings.catalog_dir / "images"
if _CATALOG_IMAGES.is_dir():
    app.mount("/catalog-images", StaticFiles(directory=_CATALOG_IMAGES), name="catalog-images")

_THRESHOLDS = Thresholds(
    min_image_score=api_settings.accept_min_image_score,
    min_margin=api_settings.accept_min_margin,
    min_vlm_prob=api_settings.accept_min_vlm_prob,
    max_vlm_none_prob=api_settings.accept_max_vlm_none_prob,
)
_feedback_lock = threading.Lock()


def _searcher() -> WineSearcher:
    searcher = _state.get("searcher")
    if searcher is None:
        raise HTTPException(status_code=503, detail="Сервис ещё не инициализирован")
    return searcher  # type: ignore[return-value]


def _sommelier() -> Sommelier:
    sommelier = _state.get("sommelier")
    if sommelier is None:
        raise HTTPException(status_code=503, detail="Сервис ещё не инициализирован")
    return sommelier  # type: ignore[return-value]


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
        sommelier_wines=len(_sommelier()),
        pairing_data=_sommelier().has_pairing_data,
    )


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/app/" if api_settings.web_enabled else "/docs")


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
    verdict = decide(result.candidates, vlm_none_prob=result.vlm_none_prob, thresholds=_THRESHOLDS)

    return SearchResponse(
        status=verdict.status,
        top1_confident=verdict.top1_confident,
        reason=verdict.reason,
        results=candidates,
        top1_confidence=confidences[0] if confidences else 0.0,
        top5_confidence=sum(confidences[:5]),
        margin=(ranking[0] - ranking[1]) if len(ranking) > 1 else 0.0,
        ocr_text=result.ocr_text,
        vlm_none_prob=result.vlm_none_prob,
        timings=_timings(result),
    )


@app.post("/v1/scan", response_model=ScanResponse)
async def scan(image: UploadFile = File(..., description="фотография этикетки")) -> ScanResponse:
    """Ручка мобильного сканера: решение + готовая карточка с советами сомелье.

    Уверены в топ-1 — отдаём его карточку, остальные из пятёрки идут в
    «Не то вино?». Не уверены — честно говорим «не найдено» и предлагаем топ-5:
    выбрать своё вино из пяти быстрее, чем переснимать.
    """
    raw = await _read_upload(image)
    result = _run_search(raw, 5)
    verdict = decide(result.candidates, vlm_none_prob=result.vlm_none_prob, thresholds=_THRESHOLDS)

    shortlist = [
        _wine_summary(c, rank=c["rank"], confidence=round(c["confidence"], 4))
        for c in result.candidates
    ]
    wine = None
    if verdict.status == "match":
        wine = _wine_card(result.candidates[0])
        wine.rank, wine.confidence = shortlist[0].rank, shortlist[0].confidence
        shortlist = shortlist[1:]

    return ScanResponse(
        status=verdict.status,
        top1_confident=verdict.top1_confident,
        reason=verdict.reason,
        message=verdict.message,
        wine=wine,
        alternatives=shortlist,
        timings=_timings(result),
    )


@app.get("/v1/wines/{slug}", response_model=WineCard)
def wine_card(slug: str) -> WineCard:
    """Карточка с советами сомелье — когда пользователь выбрал вариант сам."""
    payload = _sommelier().get(slug)
    if payload is None:
        raise HTTPException(status_code=404, detail="Вино не найдено в каталоге")
    return _wine_card(payload)


@app.get("/v1/catalog/search", response_model=list[WineSummary])
def catalog_search(
    q: str = Query(..., min_length=1, max_length=100, description="название, производитель, сорт, регион"),
    limit: int = Query(default=12, ge=1, le=50),
) -> list[WineSummary]:
    """Поиск вина по тексту — лупа в шапке сканера."""
    return [_wine_summary(w) for w in _sommelier().search(q, limit=limit)]


@app.get("/v1/pairing/dishes", response_model=list[DishListItem])
def pairing_dishes() -> list[DishListItem]:
    """Все блюда каталога — для экрана «подобрать вино к блюду»."""
    return [
        DishListItem(dish=dish, icon=dish_icon(dish), image=_sommelier().media.dish(dish), wines=count)
        for dish, count in _sommelier().dish_names()
    ]


@app.get("/v1/pairing", response_model=DishPairingResponse)
def pairing(
    dish: str = Query(..., description="блюдо, как на сайте: «Сыры»"),
    style: str | None = Query(default=None, description="red, white, rose, orange, sparkling, sweet"),
    exclude: str | None = Query(default=None, description="slug, который не показывать"),
    limit: int = Query(default=8, ge=1, le=30),
) -> DishPairingResponse:
    """Вина к блюду: сначала те, у кого это блюдо в карточке, затем то же семейство."""
    wines = _sommelier().for_dish(dish, style=style, exclude=exclude, limit=limit)
    return DishPairingResponse(dish=dish, wines=[_wine_summary(w) for w in wines])


@app.post("/v1/feedback", response_model=FeedbackResponse)
def feedback(body: FeedbackRequest) -> FeedbackResponse:
    """«Да, это оно» / «выбрал другое» / «моего вина нет».

    Каждый отклик — готовая строка разметки: какие вина путаются между собой
    (hard negatives) и каких вин не хватает в каталоге. Фото не сохраняются.
    """
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **body.model_dump()}
    api_settings.feedback_dir.mkdir(parents=True, exist_ok=True)
    with _feedback_lock, (api_settings.feedback_dir / "feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return FeedbackResponse(ok=True)


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


def _timings(result: SearchResult) -> Timings:
    return Timings(
        decode_ms=round(result.decode_ms, 2),
        encode_ms=round(result.encode_ms, 2),
        search_ms=round(result.search_ms, 2),
        ocr_ms=round(result.ocr_ms, 2),
        vlm_ms=round(result.vlm_ms, 2),
        total_ms=round(result.total_ms, 2),
    )


def _image_url(payload: dict) -> str | None:
    """Фото бутылки: локальный PNG каталога, иначе — ресайз-API сайта.

    Локальная копия нужна на проверке: она не зависит от доступности сайта.
    """
    image_path = payload.get("image_path")
    if image_path and _CATALOG_IMAGES.is_dir() and (api_settings.catalog_dir / image_path).exists():
        return "/catalog-images/" + image_path.removeprefix("images/")
    sommelier = _state.get("sommelier")
    media = sommelier.media if sommelier else SiteMedia(image_api=api_settings.site_image_api)
    return media.image_url(payload.get("image_url"), 1160)


def _wine_summary(payload: dict, **extra) -> WineSummary:
    fields = {k: v for k, v in payload.items() if k in WineSummary.model_fields}
    return WineSummary(**{**fields, "image_url": _image_url(payload), **extra})


def _wine_card(payload: dict) -> WineCard:
    sommelier = _sommelier()
    # В выдаче поиска payload может быть неполным (старый индекс) — берём
    # запись сомелье, если она есть: там те же поля из того же индекса
    payload = sommelier.get(payload["slug"]) or payload
    fields = {k: v for k, v in payload.items() if k in WineCard.model_fields}
    similar = [
        _wine_summary(w, reasons=w["reasons"]) for w in sommelier.similar(payload, limit=8)
    ]
    return WineCard(
        **{
            **fields,
            "image_url": _image_url(payload),
            **sommelier.card_media(payload),
            "sommelier": sommelier.advise(payload),
            "similar": similar,
        }
    )
