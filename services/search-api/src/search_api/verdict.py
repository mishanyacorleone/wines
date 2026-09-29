"""Решение «вино найдено» или «такого вина в каталоге нет».

Сервис всегда находит ближайшее вино каталога, даже если вина с фото в
каталоге нет (в публичной выборке таких фото треть). Поэтому поверх выдачи
нужно явное решение: показать одну карточку или честно сказать «не нашли» и
предложить аналоги из топ-10.

Главный сигнал — попарная проверка VLM (`VlmReranker.verify`): «это одно и то
же вино?» по этикетке, тексту на бутылке, форме бутылки и цвету вина. Пороги
подобраны на 99 размеченных реальных фото (`data/labels/real-photos.json`,
VLM выбирает из топ-5, прогон `data/reports/service-final`):

| правило | «нет в каталоге» у вин вне каталога | верный ответ скрыт | отсеяно неверных ответов |
|---|---|---|---|
| ответ VLM «0» + балл картинки (топ-10) | 19 из 33 | 3 из 59 | 1 из 7 |
| **проверка `verify` < 0,3 + «0» > 0,5** | **30 из 33** | **5 из 62** | 1 из 4 |
| + вероятность выбора < 0,8 → «уточните» | — | +2 (верное вино первым в списке) | 0 |

Из пяти скрытых верных ответов два — трудные случаи, где карточка на сайте
расходится с бутылкой на полке (каталог и разметка верны): у Uva Vallis
«Рислинг» категория на сайте «Красное сухое», хотя сорт и цвет — белого вина;
у Golubitskoe «Red Blend» на сайте прежний дизайн этикетки, на полке — новый
(«Winery Series»). Это подгонка на той же выборке, а не
независимый замер: пороги вынесены в `.env`, чтобы их можно было сдвинуть без
правки кода.

Без VLM (WINE_VLM_ENABLED=0) решение по-старому держится на балле картинки
и отрыве топ-1 от топ-2 — заметно слабее.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["match", "not_found"]


@dataclass(frozen=True)
class Thresholds:
    # с VLM: вероятность «Да» попарной проверки топ-1 (у верных ответов почти
    # всегда > 0,9, у вин вне каталога почти всегда < 0,1)
    min_verify_prob: float = 0.3
    # с VLM: вероятность ответа «ни одна карточка не подходит»
    max_vlm_none_prob: float = 0.5
    # с VLM: вероятность топ-1 при выборе; ниже — VLM колеблется между
    # похожими винами, пусть пользователь выберет сам
    min_vlm_prob: float = 0.8
    # без VLM: косинус картинки топ-1 и отрыв топ-1 от топ-2
    min_image_score: float = 0.76
    min_margin: float = 0.02


@dataclass(frozen=True)
class Verdict:
    status: Status
    top1_confident: bool
    reason: str
    message: str

    @property
    def absent(self) -> bool:
        """Вина с фото нет в каталоге — топ-1 наружу не отдаётся (пустой ответ).

        «ambiguous» сюда не входит: там VLM колеблется между винами одной
        линейки, и топ-1 обычно верный — пользователь просто выбирает сам.
        """
        return self.reason in _ABSENT_REASONS


_ABSENT_REASONS = {"not_verified", "vlm_none", "low_similarity", "empty"}
_NOT_IN_CATALOG = "Данное вино отсутствует в каталоге, но вот какие аналоги вы можете найти"
_MESSAGES = {
    "confident": "Нашли ваше вино",
    "empty": "Не удалось распознать вино на фото",
    "not_verified": _NOT_IN_CATALOG,
    "vlm_none": _NOT_IN_CATALOG,
    "low_similarity": _NOT_IN_CATALOG,
    "ambiguous": "Не удалось точно определить вино — выберите своё из похожих",
}


def decide(
    candidates: list[dict[str, Any]],
    *,
    vlm_none_prob: float | None,
    verify_prob: float | None,
    thresholds: Thresholds,
) -> Verdict:
    """Причина отказа важна интерфейсу: «вина нет в каталоге» и «не смогли
    выбрать между похожими» — разные тексты для пользователя."""
    if not candidates:
        return _verdict("not_found", "empty")

    top = candidates[0]
    if vlm_none_prob is not None:
        if vlm_none_prob > thresholds.max_vlm_none_prob:
            return _verdict("not_found", "vlm_none")
        if verify_prob is not None and verify_prob < thresholds.min_verify_prob:
            return _verdict("not_found", "not_verified")
        if (top.get("vlm_prob") or 0.0) < thresholds.min_vlm_prob:
            return _verdict("not_found", "ambiguous")
        return _verdict("match", "confident")

    if top["image_score"] < thresholds.min_image_score:
        return _verdict("not_found", "low_similarity")
    second = candidates[1]["score"] if len(candidates) > 1 else float("-inf")
    if top["score"] - second < thresholds.min_margin:
        return _verdict("not_found", "ambiguous")
    return _verdict("match", "confident")


def _verdict(status: Status, reason: str) -> Verdict:
    return Verdict(status, status == "match", reason, _MESSAGES[reason])
