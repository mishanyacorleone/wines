"""Схемы ответов API."""

from __future__ import annotations

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


class HealthResponse(BaseModel):
    status: str
    model_id: str
    device: str
    collection: str
    indexed_points: int
    vector_size: int
    ocr_enabled: bool
    vlm_enabled: bool
