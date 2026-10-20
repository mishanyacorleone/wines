"""Схемы ответов API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Candidate(BaseModel):
    rank: int = Field(description="место в выдаче, начиная с 1")
    slug: str
    url: str = Field(description="ссылка на карточку вина на vino-svoe.ru")
    title: str | None = None
    manufacturer: str | None = None
    category: str | None = None
    region: str | None = None
    grapes: list[str] = Field(default_factory=list)

    image_path: str | None = Field(
        default=None,
        description="путь к эталонному фото внутри каталога — нужен инструментам "
        "разметки, чтобы показать картинку кандидата рядом с запросом",
    )

    score: float = Field(
        description="балл векторного поиска (косинус картинок); с VLM порядок выдачи "
        "задаёт vlm_prob, а не он"
    )
    image_score: float = Field(description="косинусное сходство картинок, [-1, 1]")
    vlm_prob: float | None = Field(
        default=None,
        description="вероятность VLM, что это вино на фото; null — VLM выключен "
        "или кандидат не попал в его выборку",
    )
    confidence: float = Field(
        description="доля уверенности внутри выдачи: без VLM — softmax по score (сумма "
        "по топ-k равна 1), с VLM — vlm_prob (сумма с vlm_none_prob равна 1)"
    )


class Timings(BaseModel):
    """Разбивка времени ответа — нужна и для SLA, и для поиска узкого места."""

    decode_ms: float = Field(description="декодирование загруженного файла")
    encode_ms: float = Field(description="инференс энкодера (сервис embedder)")
    search_ms: float = Field(description="поиск в Qdrant")
    vlm_ms: float = Field(default=0.0, description="выбор вина VLM и проверка выбранного")
    total_ms: float = Field(description="весь ответ api, включая сеть до моделей")


class SearchResponse(BaseModel):
    status: Literal["match", "not_found"] = Field(
        description="match — топ-1 можно показывать одной карточкой; not_found — "
        "вина нет в каталоге или уверенности нет, пользователю предлагаются аналоги"
    )
    top1_confident: bool = Field(description="уверена ли система в топ-1 (то же, что status == match)")
    reason: str = Field(
        description="почему принято решение: confident (нашли), not_verified (проверка VLM: "
        "на фото другое вино), vlm_none (VLM: ни одна карточка не подходит), ambiguous "
        "(VLM колеблется между похожими винами), low_similarity (без VLM: фото мало похоже "
        "на всё в каталоге), vlm_unavailable (vLLM не ответил — выдача по картинке), empty"
    )
    message: str = Field(default="", description="готовый текст для пользователя")
    top1: str | None = Field(
        default=None,
        description="slug топ-1 — то же, что вернёт /v1/eval/predict; для метрики. "
        "null — вина нет в каталоге",
    )
    results: list[Candidate] = Field(
        description="топ-k (по умолчанию 10) — для интерфейса и рекомендаций"
    )
    top1_confidence: float = Field(description="confidence первого кандидата")
    top5_confidence: float = Field(description="суммарный confidence первых пяти")
    margin: float = Field(
        description="отрыв первого кандидата от второго по тому, чем отсортирована "
        "выдача (score, с VLM — confidence): чем больше, тем надёжнее можно "
        "показывать одну карточку без экрана вариантов"
    )
    vlm_none_prob: float | None = Field(
        default=None,
        description="вероятность VLM, что на фото нет ни одного вина из его выборки; "
        "null — VLM выключен. Как сигнал отказа слабый — основной verify_prob",
    )
    verify_prob: float | None = Field(
        default=None,
        description="вероятность попарной проверки VLM, что топ-1 — вино с фото; "
        "null — VLM выключен или у топ-1 нет эталонного фото",
    )
    degraded: bool = Field(default=False, description="VLM недоступен — ответ по одной картинке")
    timings: Timings


class PredictResponse(BaseModel):
    """Плоский ответ под скрипт оценки кейсодержателя."""

    slug: str | None = Field(description="slug вина; null — вина с фото нет в каталоге")


# ── Сканер для пользователя: карточка вина и «цифровой сомелье» ─────────────


class WineSummary(BaseModel):
    """Короткая карточка: вариант в «не то вино?», похожее вино, вино к блюду."""

    slug: str
    url: str = Field(description="карточка на vino-svoe.ru")
    title: str | None = None
    manufacturer: str | None = None
    category: str | None = None
    region: str | None = None
    grapes: list[str] = Field(default_factory=list)
    alcohol: float | None = None
    public_rating: float | None = Field(default=None, description="народный рейтинг сайта, 1–5")
    image_url: str | None = Field(default=None, description="фото бутылки")
    rank: int | None = Field(default=None, description="место в выдаче распознавания")
    confidence: float | None = Field(default=None, description="уверенность распознавания")
    reasons: list[str] = Field(
        default_factory=list, description="чем похоже на исходное вино (для похожих вин)"
    )


class Pairing(BaseModel):
    dish: str = Field(description="блюдо из карточки сайта")
    icon: str
    image: str | None = Field(default=None, description="фото блюда с сайта")
    tip: str | None = Field(default=None, description="что именно подать, с учётом стиля вина")


class Serving(BaseModel):
    temperature: str | None = Field(default=None, description="«10–12 °C»")
    temperature_min: int | None = None
    temperature_max: int | None = None
    tip: str | None = Field(default=None, description="как добиться нужной температуры")
    glass: str
    aeration: str | None = Field(default=None, description="нужно ли дать вину подышать")


class WineStyleInfo(BaseModel):
    key: str = Field(description="red, white, rose, orange, sparkling, sweet")
    color: str | None = None
    sweetness: str | None = None
    sparkling: bool
    body: str | None = Field(default=None, description="лёгкое / среднее / полнотелое — по крепости")
    body_level: int | None = Field(default=None, description="1–3")


class AlcoholInfo(BaseModel):
    value: float | None = None
    note: str | None = None


class SommelierAdvice(BaseModel):
    summary: str = Field(description="одна фраза: что за вино и как его пить")
    style: WineStyleInfo
    serving: Serving
    alcohol: AlcoholInfo
    pairings: list[Pairing]


class WineCard(WineSummary):
    """Полная карточка найденного вина с рекомендациями."""

    color: str | None = Field(default=None, description="цвет вина: «Янтарный»")
    temperature: str | None = Field(default=None, description="как на сайте: «10-12»")
    dishes: list[str] = Field(default_factory=list)
    description: str | None = None
    region_image: str | None = Field(default=None, description="миниатюра региона с сайта")
    grape_image: str | None = Field(default=None, description="миниатюра сорта с сайта")
    background_image: str | None = Field(default=None, description="фото виноградника для карточки")
    sommelier: SommelierAdvice
    similar: list[WineSummary] = Field(default_factory=list, description="похожие вина")


class ScanResponse(BaseModel):
    """Ответ сканера: либо одна карточка, либо «не найдено» и варианты."""

    status: Literal["match", "not_found"] = Field(
        description="match — показываем wine; not_found — показываем message и alternatives"
    )
    top1_confident: bool
    reason: str = Field(
        description="confident, not_verified, vlm_none, ambiguous, low_similarity, "
        "vlm_unavailable, empty — см. SearchResponse.reason"
    )
    message: str = Field(description="готовый текст для пользователя")
    top1: str | None = Field(
        default=None,
        description="slug топ-1, как в /v1/eval/predict; null — вина нет в каталоге",
    )
    wine: WineCard | None = Field(default=None, description="найденное вино; null при not_found")
    alternatives: list[WineSummary] = Field(
        default_factory=list,
        description="при not_found — топ-10 аналогов; при match — остальные из топ-10 "
        "для кнопки «Не то вино?»",
    )
    degraded: bool = Field(default=False, description="VLM недоступен — варианты по одной картинке")
    timings: Timings


class DishPairingResponse(BaseModel):
    dish: str
    wines: list[WineSummary]


class RegionInfo(BaseModel):
    """Винный регион каталога — для винной карты России."""

    name: str = Field(description="как в поле region карточки: «Кубань»")
    wines: int = Field(description="вин региона в каталоге")
    manufacturers: int = Field(description="виноделен")
    grapes: list[str] = Field(default_factory=list, description="главные сорта, по числу вин")
    image: str | None = Field(default=None, description="фото региона с сайта")


class TasteGrape(BaseModel):
    name: str
    wines: int = Field(description="вин этого сорта в каталоге")
    image: str | None = Field(default=None, description="фото сорта с сайта")


class TasteMaker(BaseModel):
    name: str
    wines: int


class TasteOptions(BaseModel):
    """Варианты для экрана «Мой вкус»: сорта и винодельни каталога, самые частые — первыми."""

    grapes: list[TasteGrape]
    manufacturers: list[TasteMaker]


class DishListItem(BaseModel):
    dish: str
    icon: str
    image: str | None = None
    wines: int


class FeedbackRequest(BaseModel):
    """Отклик пользователя на распознавание — будущая разметка без ручного труда."""

    action: Literal["confirmed", "picked_alternative", "none_match"]
    predicted_slug: str | None = Field(default=None, description="что показала система первым")
    chosen_slug: str | None = Field(default=None, description="что выбрал пользователь")
    status: Literal["match", "not_found"] | None = None
    reason: str | None = None


class FeedbackResponse(BaseModel):
    ok: bool


class ComponentHealth(BaseModel):
    ok: bool
    detail: str = ""


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="ok — всё работает; degraded — api жив, но недоступна зависимость "
        "(модель, Qdrant): сканер отвечает 503 или по картинке, остальное работает"
    )
    collection: str
    vlm_enabled: bool
    components: dict[str, ComponentHealth] = Field(
        description="qdrant, catalog (каталог в памяти), embedder, vlm"
    )
    pairing_data: bool = Field(
        default=False,
        description="есть ли в индексе блюда и подача; false — нужен "
        "`python -m indexer --payload-only`",
    )


class ReloadResponse(BaseModel):
    wines: int
    added: int
