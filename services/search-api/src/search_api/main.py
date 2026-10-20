"""FastAPI-приложение сервиса распознавания и каталога вин.

api — без моделей и без GPU: вектор фото считает embedder, выбор и проверку
вина — vLLM, кандидатов хранит Qdrant. Здесь — порядок шагов, решение
«найдено / нет в каталоге», карточки и советы сомелье. Страницу /app/ отдаёт
сервис web, поэтому упавший api не роняет сайт, а упавшая модель не роняет api:
сканер честно говорит «распознавание временно недоступно» (или отвечает по
одной картинке, если не ответил только vLLM), всё остальное работает.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from qdrant_client import QdrantClient

import wine_logging
from wine_logging import format_ms

from .catalog import CatalogStore
from .config import api_settings, bypass_proxy_for_localhost
from .remote import EmbedderClient, InvalidImageError, ServiceUnavailable
from .schemas import (
    Candidate,
    ComponentHealth,
    DishListItem,
    DishPairingResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    PredictResponse,
    RegionInfo,
    ReloadResponse,
    ScanResponse,
    SearchResponse,
    TasteOptions,
    Timings,
    WineCard,
    WineSummary,
    WineryInfo,
    WineryPin,
)
from .search import SearchResult, WineSearcher
from .sommelier import SiteMedia, Sommelier, dish_icon
from .verdict import Thresholds, Verdict, decide
from .wineries import Wineries
from .vlm import PromptBuilder, VlmClient

logger = logging.getLogger("api")
scan_log = logging.getLogger("scan")
SLOW_SCAN_MS = 3000  # SLA из ТЗ

_state: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Старт за секунды: api не ждёт ни моделей, ни Qdrant.

    Каталог грузится в фоне (catalog.CatalogStore.keep_fresh); embedder и vLLM
    поднимаются сами по себе — пока их нет, скан отвечает 503 или по картинке.
    """
    bypass_proxy_for_localhost()
    client = QdrantClient(url=api_settings.qdrant_url, timeout=api_settings.qdrant_timeout_s)
    embedder = EmbedderClient(
        api_settings.embedder_url,
        timeout_s=api_settings.embedder_timeout_s,
        connect_timeout_s=api_settings.connect_timeout_s,
        cooldown_s=api_settings.unavailable_cooldown_s,
    )
    vlm = _build_vlm()
    store = CatalogStore(
        client, api_settings.collection,
        SiteMedia.load(image_api=api_settings.site_image_api, local_dir=api_settings.site_media_dir),
        on_loaded=_warm_references(vlm),
    )
    _state.update(client=client, embedder=embedder, vlm=vlm, catalog=store, searcher=WineSearcher(
        embedder, client, api_settings.collection, vlm=vlm, vlm_top_k=api_settings.vlm_top_k,
    ))
    refresher = asyncio.create_task(store.keep_fresh(api_settings.catalog_refresh_s))
    warmer = asyncio.create_task(_warm_pipeline())
    logger.info("api готов: embedder %s, VLM %s, Qdrant %s", api_settings.embedder_url,
                api_settings.vllm_url if vlm is not None else "выключен", api_settings.qdrant_url)
    yield
    refresher.cancel()
    warmer.cancel()
    await embedder.close()
    if vlm is not None:
        await vlm.close()
    client.close()


def _build_vlm() -> VlmClient | None:
    if not api_settings.vlm_enabled:
        return None
    prompts = PromptBuilder(
        query_max_pixels=api_settings.vlm_query_max_pixels,
        candidate_max_pixels=api_settings.vlm_candidate_max_pixels,
        with_candidate_images=api_settings.vlm_candidate_images,
        catalog_dir=api_settings.catalog_dir,
    )
    return VlmClient(
        api_settings.vllm_url,
        prompts,
        model=api_settings.vllm_model,
        media_dir=api_settings.vllm_media_dir,
        timeout_s=api_settings.vllm_timeout_s,
        connect_timeout_s=api_settings.connect_timeout_s,
        cooldown_s=api_settings.unavailable_cooldown_s,
    )


def _warm_references(vlm: VlmClient | None):
    """После каждой загрузки каталога — уменьшенные эталоны новых вин, в фоне."""
    def warm(payloads: list[dict]) -> None:
        if vlm is None:
            return
        paths = [p["image_path"] for p in payloads if p.get("image_path")]
        threading.Thread(
            target=vlm.prompts.warm_reference_cache, args=(paths,), name="vlm-refs", daemon=True
        ).start()
    return warm


async def _warm_pipeline(retry_s: float = 15.0) -> None:
    """Холостой скан фото из каталога, когда поднялись все зависимости.

    Первый запрос после старта стека шёл 10,8 с (19.10: вектор 6,8 с, Qdrant 1,3 с,
    VLM 2,2 с — холодные ядра и кэши) при SLA 3 с, а первым может оказаться фото
    организатора. Прогрев embedder на белой картинке этого не снимал.
    """
    store: CatalogStore = _state["catalog"]
    searcher: WineSearcher = _state["searcher"]
    raw = None
    while True:
        await asyncio.sleep(retry_s)
        if not store.ready:
            continue
        if raw is None:
            raw = await asyncio.to_thread(_sample_photo)
            if raw is None:
                logger.warning("Прогрев пропущен: в каталоге нет фото на диске")
                return
        try:
            result = await searcher.search(raw, top_k=api_settings.vlm_top_k)
        except ServiceUnavailable:
            continue  # модели ещё грузятся — повторим
        if result.degraded:
            continue
        timings = (f"вектор {format_ms(result.encode_ms)} · Qdrant {format_ms(result.search_ms)}"
                   f" · VLM {format_ms(result.vlm_ms)}")
        # Второй проход показывает, каким будет обычный запрос
        warm = await searcher.search(raw, top_k=api_settings.vlm_top_k)
        logger.info("Прогрев: первый скан %s (%s), повторный %s — сканер готов",
                    format_ms(result.total_ms), timings, format_ms(warm.total_ms))
        return


def _sample_photo() -> bytes | None:
    client: QdrantClient = _state["client"]
    points, _ = client.scroll(api_settings.collection, limit=20, with_payload=True, with_vectors=False)
    for point in points:
        path = (point.payload or {}).get("image_path")
        if path and (api_settings.catalog_dir / path).is_file():
            return (api_settings.catalog_dir / path).read_bytes()
    return None


app = FastAPI(
    title="Сканер вин «Своё Вино»",
    description="Поиск карточки вина по фотографии этикетки.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(wine_logging.RequestLogMiddleware)

# Справочники меняются только с синхронизацией каталога (раз в день): браузер 5 минут берёт их
# из кэша и не переспрашивает при каждом открытии страницы
_CACHED_GET = ("/v1/pairing/dishes", "/v1/regions", "/v1/wineries", "/v1/taste/options")


@app.middleware("http")
async def _cache_reference(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200 and request.url.path.startswith(_CACHED_GET):
        response.headers.setdefault("Cache-Control", "public, max-age=300")
    return response

_THRESHOLDS = Thresholds(
    min_verify_prob=api_settings.accept_min_verify_prob,
    max_vlm_none_prob=api_settings.accept_max_vlm_none_prob,
    min_vlm_prob=api_settings.accept_min_vlm_prob,
    min_image_score=api_settings.accept_min_image_score,
    min_margin=api_settings.accept_min_margin,
)
_feedback_lock = threading.Lock()


def _store() -> CatalogStore:
    return _state["catalog"]  # type: ignore[return-value]


def _sommelier() -> Sommelier:
    sommelier = _store().sommelier
    if sommelier is None:
        raise HTTPException(status_code=503, detail="Каталог вин ещё загружается — попробуйте через минуту")
    return sommelier


async def _read_upload(image: UploadFile) -> bytes:
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > api_settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    return raw


async def _run_search(raw: bytes, top_k: int) -> SearchResult:
    searcher: WineSearcher = _state["searcher"]  # type: ignore[assignment]
    try:
        return await searcher.search(raw, top_k)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail="Файл не похож на фото — пришлите снимок в JPEG, PNG или WebP") from exc
    except ServiceUnavailable as exc:
        scan_log.error("Скан не выполнен: %s недоступен (%s) → 503", exc.service, exc.reason)
        wine_logging.mark_logged()
        raise HTTPException(
            status_code=503,
            detail="Распознавание временно недоступно — попробуйте через минуту. "
                   "Остальные разделы сайта работают",
        ) from exc


def _decide(result: SearchResult) -> Verdict:
    return decide(
        result.candidates,
        vlm_none_prob=result.vlm_none_prob,
        verify_prob=result.verify_prob,
        thresholds=_THRESHOLDS,
        degraded=result.degraded,
    )


def _log_scan(kind: str, result: SearchResult, verdict: Verdict) -> None:
    """Одна строка на распознавание: что нашли, насколько уверены, сколько заняло."""
    wine_logging.mark_logged()
    top = result.candidates[0] if result.candidates else None
    name = f"«{top.get('title') or top['slug']}» ({top['slug']})" if top else "—"
    timing = (f"{format_ms(result.total_ms)} (вектор {format_ms(result.encode_ms)} · "
              f"Qdrant {format_ms(result.search_ms)} · VLM {format_ms(result.vlm_ms)})")
    # Дольше SLA — WARN той же строкой: строки «медленно» от http больше нет
    slow = result.total_ms > SLOW_SCAN_MS
    if slow:
        timing += " — медленно"
    log = scan_log.warning if slow else scan_log.info
    if verdict.status == "match":
        log("%s: найдено %s — выбор VLM %.2f, проверка %s · %s", kind, name,
                      top.get("vlm_prob") or top["confidence"], _prob(result.verify_prob), timing)
    elif verdict.reason == "vlm_unavailable":
        scan_log.warning("%s: VLM недоступен — ответ по картинке, ближе всего %s (сходство %.3f) · %s",
                         kind, name, top["image_score"], timing)
    elif verdict.absent:
        log("%s: вина нет в каталоге (%s: проверка %s, «ни одна» %s), ближе всего %s · %s",
                      kind, verdict.reason, _prob(result.verify_prob), _prob(result.vlm_none_prob), name, timing)
    else:
        log("%s: не уверены (%s), варианты, первым %s · %s", kind, verdict.reason, name, timing)


def _prob(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Жив ли api и что с зависимостями. 200 всегда, пока процесс отвечает:
    упавшая модель — не повод перезапускать api (status = degraded)."""
    store = _store()
    embedder: EmbedderClient = _state["embedder"]  # type: ignore[assignment]
    vlm: VlmClient | None = _state["vlm"]  # type: ignore[assignment]

    async def qdrant_points() -> int | None:
        try:
            return await asyncio.to_thread(store.points_count)
        except Exception:  # noqa: BLE001
            return None

    points, embedder_info, vlm_ok = await asyncio.gather(
        qdrant_points(), embedder.health(), vlm.health() if vlm is not None else asyncio.sleep(0, None),
    )
    components = {
        "qdrant": ComponentHealth(ok=points is not None, detail=f"{points} вин" if points is not None else "недоступен"),
        "catalog": ComponentHealth(ok=store.ready, detail=f"{store.points} вин в памяти" if store.ready else "загружается"),
        "embedder": ComponentHealth(
            ok=embedder_info is not None,
            detail=f"{embedder_info['model_id']} на {embedder_info['device']}" if embedder_info else "недоступен",
        ),
    }
    if vlm is not None:
        components["vlm"] = ComponentHealth(ok=bool(vlm_ok), detail=api_settings.vllm_url if vlm_ok else "недоступен")
    return HealthResponse(
        status="ok" if all(c.ok for c in components.values()) else "degraded",
        collection=api_settings.collection,
        vlm_enabled=vlm is not None,
        components=components,
        pairing_data=store.sommelier.has_pairing_data if store.sommelier else False,
    )


@app.post("/internal/catalog/reload", response_model=ReloadResponse, include_in_schema=False)
async def reload_catalog() -> ReloadResponse:
    """Перечитать каталог из Qdrant — зовёт синхронизация после добавления вин.
    Шлюз сюда не пускает: /internal доступен только внутри сети сервисов."""
    added = await asyncio.to_thread(_store().load, "запрос синхронизации")
    wine_logging.mark_logged()  # «Каталог перечитан…» уже в логе; 4 с на 2200 вин — норма, не «медленно»
    return ReloadResponse(wines=_store().points, added=added)


@app.post("/v1/search", response_model=SearchResponse)
async def search(
    image: UploadFile = File(..., description="фотография этикетки"),
    top_k: int = Query(default=api_settings.default_top_k, ge=1),
) -> SearchResponse:
    """Топ-k кандидатов с confidence и ссылкой на карточку.

    Основная ручка для анализа: отдаёт не один ответ, а всю выдачу, чтобы
    было видно, что именно вытаскивает модель и насколько она уверена.
    `top1` — тот же ответ, что вернёт /v1/eval/predict (для метрики; null —
    вина нет в каталоге); `results` — весь топ-k (для интерфейса и рекомендаций).
    """
    top_k = min(top_k, api_settings.max_top_k)
    raw = await _read_upload(image)
    result = await _run_search(raw, top_k)

    candidates = [Candidate(**_candidate_fields(c)) for c in result.candidates]
    confidences = [c.confidence for c in candidates]
    # margin — по тому же сигналу, что задал порядок: с VLM score не монотонен
    ranking = confidences if result.vlm_none_prob is not None else [c.score for c in candidates]
    verdict = _decide(result)
    _log_scan("Поиск", result, verdict)

    return SearchResponse(
        status=verdict.status,
        top1_confident=verdict.top1_confident,
        reason=verdict.reason,
        message=verdict.message,
        top1=_top1(result, verdict),
        results=candidates,
        top1_confidence=confidences[0] if confidences else 0.0,
        top5_confidence=sum(confidences[:5]),
        margin=(ranking[0] - ranking[1]) if len(ranking) > 1 else 0.0,
        vlm_none_prob=result.vlm_none_prob,
        verify_prob=result.verify_prob,
        degraded=result.degraded,
        timings=_timings(result),
    )


@app.post("/v1/scan", response_model=ScanResponse)
async def scan(image: UploadFile = File(..., description="фотография этикетки")) -> ScanResponse:
    """Ручка мобильного сканера: решение + готовая карточка с советами сомелье.

    Уверены в топ-1 — отдаём его карточку, остальные из топ-10 идут в
    «Не то вино?». Вина нет в каталоге — честно говорим об этом и предлагаем
    топ-10 как аналоги: самые похожие вина каталога.
    """
    raw = await _read_upload(image)
    result = await _run_search(raw, api_settings.default_top_k)
    verdict = _decide(result)
    _log_scan("Скан", result, verdict)

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
        top1=_top1(result, verdict),
        wine=wine,
        alternatives=shortlist,
        degraded=result.degraded,
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
    found = _sommelier().search(q, limit=limit)
    logging.getLogger("catalog").info("Поиск по каталогу «%s»: %d вин", q, len(found))
    return [_wine_summary(w) for w in found]


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


@app.get("/v1/regions", response_model=list[RegionInfo])
def regions() -> list[RegionInfo]:
    """Винные регионы каталога для винной карты: число вин, виноделен, главные сорта, фото."""
    return [RegionInfo(**r) for r in _sommelier().region_summary()]


@app.get("/v1/regions/{region}/wines", response_model=list[WineSummary])
def region_wines(region: str, limit: int = Query(default=8, ge=1, le=30)) -> list[WineSummary]:
    """Что попробовать из региона: по народному рейтингу, не больше двух вин от винодельни."""
    return [_wine_summary(w) for w in _sommelier().for_region(region, limit=limit)]


@app.get("/v1/taste/options", response_model=TasteOptions)
def taste_options() -> TasteOptions:
    """Сорта (с фото) и винодельни каталога — для выбора в «Моём вкусе»; профиль хранит сервис пользователей."""
    return TasteOptions(**_sommelier().taste_options())


@app.get("/v1/wineries", response_model=list[WineryPin])
def wineries() -> list[WineryPin]:
    """Винодельни с точкой на винной карте (туры, число вин каталога) — для меток."""
    counts = _sommelier().maker_counts()
    return [WineryPin(**pin, wines=counts.get(pin["slug"], 0)) for pin in _wineries().pins()]


@app.get("/v1/wineries/{slug}", response_model=WineryInfo)
def winery(slug: str, limit: int = Query(default=12, ge=1, le=50)) -> WineryInfo:
    """Страница винодельни: о ней, терруар, люди, туры, вина из каталога, соседи."""
    info = _wineries().full(slug)
    if info is None:
        raise HTTPException(status_code=404, detail="Винодельня не найдена")
    wines = _sommelier().for_maker(slug)
    counts = _sommelier().maker_counts()
    pins = {key: [{**n, "wines": counts.get(n["slug"], 0)} for n in info.pop(key)] for key in ("nearby", "region_tours")}
    return WineryInfo(**info, **pins, wines=[_wine_summary(w) for w in wines[:limit]], wines_total=len(wines))


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
    if body.action == "confirmed":
        scan_log.info("Отзыв: пользователь подтвердил %s", body.predicted_slug)
    elif body.action == "picked_alternative":
        scan_log.info("Отзыв: пользователь выбрал %s вместо %s", body.chosen_slug, body.predicted_slug)
    else:
        scan_log.info("Отзыв: «моего вина здесь нет» (показали %s)", body.predicted_slug)
    return FeedbackResponse(ok=True)


@app.post("/v1/eval/predict", response_model=PredictResponse)
async def predict(image: UploadFile = File(...)) -> PredictResponse:
    """Контракт скрипта оценки кейсодержателя: плоский {"slug": "..."}.

    По умолчанию всегда отдаёт вино, выбранное VLM: по условиям проверки все
    фото есть в каталоге (29.09.2026). WINE_PREDICT_EMPTY_IF_ABSENT=1 — {"slug":
    null}, если вина нет в каталоге (скрипт организатора пишет predicted_slug = null).
    """
    raw = await _read_upload(image)
    result = await _run_search(raw, 1)
    verdict = _decide(result)
    _log_scan("Оценка (eval/predict)", result, verdict)
    return PredictResponse(slug=_top1(result, verdict))


def _top1(result: SearchResult, verdict: Verdict) -> str | None:
    """Ответ для метрики: slug топ-1 или None, если вина нет в каталоге."""
    if not result.candidates:
        return None
    if verdict.absent and api_settings.predict_empty_if_absent:
        return None
    return result.candidates[0]["slug"]


def _candidate_fields(candidate: dict) -> dict:
    """Оставляет только поля схемы: в payload лежит и служебное (image_path)."""
    allowed = set(Candidate.model_fields)
    return {k: v for k, v in candidate.items() if k in allowed}


def _timings(result: SearchResult) -> Timings:
    return Timings(
        decode_ms=round(result.decode_ms, 2),
        encode_ms=round(result.encode_ms, 2),
        search_ms=round(result.search_ms, 2),
        vlm_ms=round(result.vlm_ms, 2),
        total_ms=round(result.total_ms, 2),
    )


def _image_url(payload: dict) -> str | None:
    """Фото бутылки: локальный PNG каталога, иначе — ресайз-API сайта.

    Локальная копия нужна на проверке: она не зависит от доступности сайта.
    """
    image_path = payload.get("image_path")
    if image_path and (api_settings.catalog_dir / image_path).exists():
        # фото отдаёт сервис web (/catalog-images/ — папка data/catalog/images)
        return "/catalog-images/" + image_path.removeprefix("images/")
    sommelier = _store().sommelier
    media = sommelier.media if sommelier else SiteMedia(
        image_api=api_settings.site_image_api, local_dir=api_settings.site_media_dir)
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
            "winery": _wineries().brief(payload.get("manufacturer_slug")),
        }
    )


def _wineries() -> Wineries:
    if "wineries" not in _state:
        _state["wineries"] = Wineries.load(
            SiteMedia(image_api=api_settings.site_image_api, local_dir=api_settings.site_media_dir))
    return _state["wineries"]
