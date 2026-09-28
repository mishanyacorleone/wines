"""Решение «показать одно вино или предложить варианты».

Сервис всегда находит ближайшее вино каталога, даже если вина с фото в
каталоге нет (в публичной выборке таких фото треть). Поэтому поверх выдачи
нужно явное решение: уверены ли мы в топ-1 настолько, чтобы показать одну
карточку, или честно сказать «не нашли» и предложить топ-5.

Пороги подобраны на 99 размеченных реальных фото (`data/labels/real-photos.json`,
прогоны `data/reports/ocr` и `data/reports/service-vlm`):

| конфигурация | показали одну карточку | из них верно | «не найдено» у вин вне каталога |
|---|---|---|---|
| OCR, без VLM  | 60 | 48 (80%) | 22 из 33 |
| VLM, без OCR  | 65 | 55 (85%) | 25 из 33 |

Это подгонка на той же выборке, а не независимый замер: пороги вынесены в
`.env`, чтобы их можно было сдвинуть без правки кода.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["match", "not_found"]


@dataclass(frozen=True)
class Thresholds:
    # косинус картинки топ-1: у верных ответов медиана 0,87, у вин вне каталога 0,76
    min_image_score: float = 0.76
    # без VLM: отрыв топ-1 от топ-2 по итоговому баллу
    min_margin: float = 0.02
    # с VLM: его вероятность для топ-1 (она не откалибрована — почти всегда
    # 0,9–1,0, поэтому одна она не решает, только вместе с баллом картинки)
    min_vlm_prob: float = 0.8
    # с VLM: вероятность ответа «ни одна из пяти не подходит»
    max_vlm_none_prob: float = 0.5


@dataclass(frozen=True)
class Verdict:
    status: Status
    top1_confident: bool
    reason: str
    message: str


_MESSAGES = {
    "confident": "Нашли ваше вино",
    "empty": "Не удалось распознать вино на фото",
    "vlm_none": "Такое вино не найдено в каталоге — возможно, это одно из похожих",
    "low_similarity": "Такое вино не найдено в каталоге — возможно, это одно из похожих",
    "ambiguous": "Не удалось точно определить вино — выберите своё из похожих",
}


def decide(
    candidates: list[dict[str, Any]],
    *,
    vlm_none_prob: float | None,
    thresholds: Thresholds,
) -> Verdict:
    """Причина отказа важна интерфейсу: «вина нет в каталоге» и «не смогли
    выбрать между похожими» — разные тексты для пользователя."""
    if not candidates:
        return _verdict("not_found", "empty")

    top = candidates[0]
    vlm_used = vlm_none_prob is not None

    if vlm_used and vlm_none_prob > thresholds.max_vlm_none_prob:
        return _verdict("not_found", "vlm_none")
    if top["image_score"] < thresholds.min_image_score:
        return _verdict("not_found", "low_similarity")

    if vlm_used:
        confident = (top.get("vlm_prob") or 0.0) >= thresholds.min_vlm_prob
    else:
        second = candidates[1]["score"] if len(candidates) > 1 else float("-inf")
        confident = top["score"] - second >= thresholds.min_margin
    if not confident:
        return _verdict("not_found", "ambiguous")
    return _verdict("match", "confident")


def _verdict(status: Status, reason: str) -> Verdict:
    return Verdict(status, status == "match", reason, _MESSAGES[reason])
