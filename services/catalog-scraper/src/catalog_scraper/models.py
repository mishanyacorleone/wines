"""Доменные модели каталога."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Wine:
    """Одна карточка вина из каталога «Своё Вино»."""

    slug: str
    source_url: str
    title: str | None = None
    category: str | None = None
    color: str | None = None
    region: str | None = None
    manufacturer: str | None = None
    manufacturer_slug: str | None = None
    grapes: list[str] = field(default_factory=list)
    dishes: list[str] = field(default_factory=list)
    alcohol: float | None = None
    temperature: str | None = None
    public_rating: float | None = None
    description: str | None = None

    # путь к исходной картинке на CDN (относительный, вида /uploads/xxx.webp)
    image_url: str | None = None
    # путь к сохранённому PNG относительно output_dir
    image_path: str | None = None
    image_width: int | None = None
    image_height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(slots=True)
class Failure:
    """Страница, которую не удалось обработать. Пишется отдельным файлом,
    чтобы прогон можно было повторить только по упавшим URL."""

    slug: str
    source_url: str
    stage: str  # fetch_page | parse | image_download | image_convert
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
