"""Разбор блока __NUXT_DATA__ со страницы вина.

Nuxt отдаёт состояние в devalue-формате: это плоский список «ячеек», где
вложенные значения хранятся не напрямую, а как целочисленные индексы внутри
этого же списка. Поэтому любое поле нужно «разыменовывать» через resolve().

Парсим именно этот блок, а не вёрстку: на странице вина десятки <img>
(иконки региона и сорта, фото блюд, превью похожих вин), и по разметке
невозможно однозначно понять, какая картинка — фото самой бутылки.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import Wine

_NUXT_RE = re.compile(
    r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


class NuxtParseError(Exception):
    """Не удалось достать данные вина из страницы."""


class NuxtData:
    """Плоский список ячеек devalue с разыменованием по индексу."""

    def __init__(self, cells: list[Any]) -> None:
        self._cells = cells

    @classmethod
    def from_html(cls, html: str) -> "NuxtData":
        match = _NUXT_RE.search(html)
        if match is None:
            raise NuxtParseError("блок __NUXT_DATA__ не найден")
        try:
            cells = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise NuxtParseError(f"__NUXT_DATA__ не парсится как JSON: {exc}") from exc
        if not isinstance(cells, list):
            raise NuxtParseError("__NUXT_DATA__ не является списком ячеек")
        return cls(cells)

    def deref(self, value: Any) -> Any:
        """Разыменовывает одну ссылку: целое число -> значение ячейки."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and 0 <= value < len(self._cells):
            return self._cells[value]
        return value

    def resolve(self, value: Any, _depth: int = 0) -> Any:
        """Рекурсивно разыменовывает структуру целиком.

        Глубина ограничена: ячейки могут ссылаться друг на друга циклически,
        а нам нужны только неглубокие поля карточки.
        """
        if _depth > 6:
            return None
        value = self.deref(value)
        if isinstance(value, dict):
            return {k: self.resolve(v, _depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, _depth + 1) for v in value]
        return value

    def find_wine_cell(self) -> dict[str, Any]:
        """Находит ячейку самого вина.

        Индексы ячеек плавают от страницы к странице, поэтому ищем не magic
        number, а структуру: в корневом словаре есть ключ вида 'wine-<slug>'.
        """
        for cell in self._cells:
            if not isinstance(cell, dict):
                continue
            wine_key = next(
                (k for k in cell if isinstance(k, str) and k.startswith("wine-")), None
            )
            if wine_key is None:
                continue
            container = self.deref(cell[wine_key])
            if not isinstance(container, dict) or "wine" not in container:
                continue
            wine = self.deref(container["wine"])
            if isinstance(wine, dict) and "slug" in wine:
                return wine
        raise NuxtParseError("ячейка с данными вина не найдена")


def _named(data: NuxtData, value: Any) -> str | None:
    """Поля вида {'name': <ref>, ...} -> строка имени."""
    resolved = data.deref(value)
    if isinstance(resolved, dict) and "name" in resolved:
        name = data.deref(resolved["name"])
        return name if isinstance(name, str) else None
    return resolved if isinstance(resolved, str) else None


def _named_list(data: NuxtData, value: Any) -> list[str]:
    resolved = data.deref(value)
    if not isinstance(resolved, list):
        return []
    names = [_named(data, item) for item in resolved]
    return [n for n in names if n]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def parse_wine(html: str, source_url: str) -> Wine:
    """Собирает Wine из HTML страницы вина."""
    data = NuxtData.from_html(html)
    cell = data.find_wine_cell()

    slug = data.deref(cell.get("slug"))
    if not isinstance(slug, str) or not slug:
        raise NuxtParseError("у вина нет slug")

    manufacturer = data.deref(cell.get("manufacturer"))
    manufacturer_slug = None
    if isinstance(manufacturer, dict) and "slug" in manufacturer:
        value = data.deref(manufacturer["slug"])
        manufacturer_slug = value if isinstance(value, str) else None

    image_url = None
    image = data.deref(cell.get("image"))
    if isinstance(image, dict) and "url" in image:
        value = data.deref(image["url"])
        image_url = value if isinstance(value, str) else None

    def as_str(key: str) -> str | None:
        value = data.deref(cell.get(key))
        return value if isinstance(value, str) else None

    return Wine(
        slug=slug,
        source_url=source_url,
        title=as_str("title"),
        category=_named(data, cell.get("category")),
        color=as_str("color"),
        region=_named(data, cell.get("region")),
        manufacturer=_named(data, cell.get("manufacturer")),
        manufacturer_slug=manufacturer_slug,
        grapes=_named_list(data, cell.get("grapes")),
        dishes=_named_list(data, cell.get("dishes")),
        alcohol=_number(data.deref(cell.get("alcohol"))),
        temperature=as_str("temperature"),
        public_rating=_number(data.deref(cell.get("publicRating"))),
        description=as_str("description"),
        image_url=image_url,
    )
