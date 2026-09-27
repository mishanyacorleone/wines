"""Чтение собранного каталога."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(slots=True)
class CatalogEntry:
    slug: str
    image_path: Path
    payload: dict[str, Any]


def load_catalog(catalog_path: Path, site_base_url: str) -> list[CatalogEntry]:
    """Разбирает catalog.jsonl в записи, готовые к индексации.

    В payload кладём сразу всё, что понадобится в ответе API: после поиска
    не должно быть второго похода в базу за метаданными.
    """
    entries: list[CatalogEntry] = []
    base_dir = catalog_path.parent

    for row in _read_jsonl(catalog_path):
        slug = row.get("slug")
        image_path = row.get("image_path")
        if not slug or not image_path:
            continue

        absolute = base_dir / image_path
        if not absolute.exists():
            continue

        entries.append(
            CatalogEntry(
                slug=slug,
                image_path=absolute,
                payload={
                    "slug": slug,
                    # ссылку собираем на этапе индексации, чтобы клиенту
                    # не приходилось знать про устройство URL сайта
                    "url": f"{site_base_url.rstrip('/')}/wines/{slug}",
                    "title": row.get("title"),
                    "category": row.get("category"),
                    "color": row.get("color"),
                    "region": row.get("region"),
                    "manufacturer": row.get("manufacturer"),
                    "grapes": row.get("grapes") or [],
                    "alcohol": row.get("alcohol"),
                    "image_path": image_path,
                },
            )
        )
    return entries


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                yield json.loads(line)
