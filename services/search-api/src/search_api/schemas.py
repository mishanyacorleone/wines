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
        description="итоговый балл: image_score + TEXT_WEIGHT * text_score; по нему выдача отсортирована"
    )
    image_score: float = Field(description="косинусное сходство картинок, [-1, 1]")
    text_score: float = Field(
        default=0.0,
        description="текстовая улика, [-1, 1]: вес найденных на этикетке слов карточки "
        "минус штраф за противоречие по цвету/сладости",
    )
    matched_terms: list[str] = Field(
        default_factory=list, description="какие слова карточки нашлись на этикетке"
    )
    conflict_terms: list[str] = Field(
        default_factory=list,
        description="цвет/сладость, прочитанные на этикетке и противоречащие карточке",
    )
    vlm_prob: float | None = Field(
        default=None,
        description="вероятность VLM, что это вино на фото; null — VLM выключен "
        "или кандидат не попал в его пятёрку",
    )
    confidence: float = Field(
        description="доля уверенности внутри выдачи: без VLM — softmax по score (сумма "
        "по топ-k равна 1), с VLM — vlm_prob (сумма с vlm_none_prob равна 1)"
    )


class Timings(BaseModel):
    """Разбивка времени ответа — нужна и для SLA, и для поиска узкого места."""

    decode_ms: float = Field(description="декодирование загруженного файла")
    encode_ms: float = Field(description="инференс энкодера")
    search_ms: float = Field(description="поиск в Qdrant")
    ocr_ms: float = Field(default=0.0, description="OCR этикетки и переранжирование")
    vlm_ms: float = Field(default=0.0, description="выбор вина внутри пятёрки VLM")
    total_ms: float


class SearchResponse(BaseModel):
    status: Literal["match", "not_found"] = Field(
        description="match — топ-1 можно показывать одной карточкой; not_found — "
        "уверенности нет, пользователю предлагаются варианты из топ-5"
    )
    top1_confident: bool = Field(description="уверена ли система в топ-1 (то же, что status == match)")
    reason: str = Field(
        description="почему принято решение: confident, low_similarity (фото мало похоже "
        "на всё в каталоге), vlm_none (VLM: ни одна из пяти не подходит), ambiguous "
        "(кандидаты слишком близки), empty"
    )
    results: list[Candidate]
    top1_confidence: float = Field(description="confidence первого кандидата")
    top5_confidence: float = Field(description="суммарный confidence первых пяти")
    margin: float = Field(
        description="отрыв первого кандидата от второго по тому, чем отсортирована "
        "выдача (score, с VLM — confidence): чем больше, тем надёжнее можно "
        "показывать одну карточку без экрана вариантов"
    )
    ocr_text: list[str] | None = Field(
        default=None, description="строки, прочитанные с этикетки; null — OCR выключен"
    )
    vlm_none_prob: float | None = Field(
        default=None,
        description="вероятность VLM, что на фото нет ни одного вина из пятёрки; "
        "null — VLM выключен. Как сигнал отказа пока слабый",
    )
    timings: Timings


class PredictResponse(BaseModel):
    """Плоский ответ под скрипт оценки кейсодержателя."""

    slug: str


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
    reason: str = Field(description="confident, low_similarity, vlm_none, ambiguous, empty")
    message: str = Field(description="готовый текст для пользователя")
    wine: WineCard | None = Field(default=None, description="найденное вино; null при not_found")
    alternatives: list[WineSummary] = Field(
        default_factory=list,
        description="при not_found — топ-5 вариантов; при match — остальные из топ-5 "
        "для кнопки «Не то вино?»",
    )
    timings: Timings


class DishPairingResponse(BaseModel):
    dish: str
    wines: list[WineSummary]


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


class HealthResponse(BaseModel):
    status: str
    model_id: str
    device: str
    collection: str
    indexed_points: int
    vector_size: int
    ocr_enabled: bool
    vlm_enabled: bool
    sommelier_wines: int = Field(default=0, description="вин в памяти сомелье")
    pairing_data: bool = Field(
        default=False,
        description="есть ли в индексе блюда и подача; false — нужен "
        "`python -m indexer --payload-only`",
    )
