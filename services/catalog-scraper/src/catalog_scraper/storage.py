"""Запись результатов на диск.

Каталог пишется в JSONL построчно, по мере обхода: прогон на ~2100 страниц
длится минуты, и падение в середине не должно стоить всей работы.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

from .models import Failure, Wine

logger = logging.getLogger(__name__)


class JsonlWriter:
    """Пишет по одному JSON-объекту в строку, с флашем после каждой записи."""

    def __init__(self, path: Path, *, append: bool) -> None:
        self._path = path
        self._mode = "a" if append else "w"
        self._file = None

    def __enter__(self) -> "JsonlWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open(self._mode, encoding="utf-8")
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._file is not None:
            self._file.close()

    def write(self, payload: dict[str, Any]) -> None:
        assert self._file is not None, "writer используется вне контекста"
        self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._file.flush()


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as file:
        for line_no, line in enumerate(file, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # последняя строка могла остаться обрезанной после убийства процесса
                logger.warning("Пропускаю битую строку %s:%d", path, line_no)


def compact_catalog(catalog_path: Path, images_dir: Path) -> set[str]:
    """Оставляет в каталоге только собранные вина и возвращает их слаги.

    Собранное вино — строка в каталоге И PNG на диске. Остальные строки
    выбрасываются: парсер соберёт эти вина заново и допишет в конец. Без этого
    на чистом клоне (catalog.jsonl в git, фото — нет) каждое вино попадало в
    файл дважды. Повтор слага — последняя строка, как при upsert в индекс.
    """
    rows: dict[str, dict[str, Any]] = {}
    total = 0
    for row in read_jsonl(catalog_path):
        total += 1
        slug = row.get("slug")
        image_path = row.get("image_path")
        if not slug or not image_path:
            continue
        if (images_dir.parent / image_path).exists():
            rows.pop(slug, None)  # повтор встаёт на место последней строки
            rows[slug] = row
    if len(rows) != total:
        logger.info("Каталог: из %d строк собраны %d вин, остальные соберу заново",
                    total, len(rows))
        # через временный файл: прерванная запись не должна стереть каталог
        partial = catalog_path.with_name(catalog_path.name + ".tmp")
        with JsonlWriter(partial, append=False) as writer:
            for row in rows.values():
                writer.write(row)
        partial.replace(catalog_path)
    return set(rows)


def write_wine(writer: JsonlWriter, wine: Wine) -> None:
    writer.write(wine.to_dict())


def write_failure(writer: JsonlWriter, failure: Failure) -> None:
    writer.write(failure.to_dict())
