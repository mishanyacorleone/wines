"""Ручная разметка реальных фото: импорт из Label Studio и загрузка.

Размечен верный ответ (slug), а не выдача конкретного прогона, поэтому одна
разметка годится для сравнения любых конфигураций.

Импорт экспорта Label Studio (CSV):
    PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.labels \\
        data/project-32-at-...csv

Соглашения разметчика:
  - `wine`: один или несколько slug через запятую; после `__` — комментарий;
  - `status`: пусто — уверенно, «Не уверен» — ближайшее вино каталога с
    расхождением (год, версия этикетки, сладость), «Нет в каталоге».
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LABELS = REPO_ROOT / "data" / "labels" / "real-photos.json"

# Label Studio при загрузке файла дописывает к имени каталог проекта и
# 8 hex-символов: /data/upload/32/539ed086-1.73_06-09-2026_14-54-06.webp
_UPLOAD_PREFIX = re.compile(r"^/data/upload/\d+/[0-9a-f]{8}-")

SURE, UNSURE, ABSENT, SKIP = "sure", "unsure", "absent", "skip"
_STATUSES = {"": SURE, "Не уверен": UNSURE, "Нет в каталоге": ABSENT}


@dataclass(frozen=True)
class Label:
    slugs: tuple[str, ...]
    status: str
    note: str = ""


def import_label_studio(csv_path: Path, catalog_slugs: set[str]) -> tuple[dict, list[str]]:
    labels: dict[str, dict] = {}
    problems: list[str] = []

    with csv_path.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            filename = _UPLOAD_PREFIX.sub("", row["image"])
            wine, _, note = row["wine"].partition("__")
            slugs = [s.strip() for s in re.split(r"[,;\s]+", wine) if s.strip()]
            status = _STATUSES.get(row["status"].strip(), SURE)

            unknown = [s for s in slugs if s not in catalog_slugs]
            if status == ABSENT:
                slugs = []
            elif unknown or not slugs:
                # без валидного slug фото нельзя ни засчитать, ни отнести к
                # «нет в каталоге» — исключаем из подсчёта, но не теряем
                problems.append(f"{filename}: {wine!r} — {note.strip() or 'нет slug'}")
                status, slugs = SKIP, []

            labels[filename] = {
                "slugs": slugs,
                "status": status,
                "note": note.strip().strip("()"),
            }
    return labels, problems


def load_labels(path: Path = DEFAULT_LABELS) -> dict[str, Label]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: Label(tuple(v["slugs"]), v["status"], v.get("note", ""))
        for name, v in raw.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт разметки из Label Studio (CSV)")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--catalog", type=Path, default=REPO_ROOT / "data" / "catalog" / "catalog.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_LABELS)
    args = parser.parse_args(argv)

    with args.catalog.open(encoding="utf-8") as file:
        catalog_slugs = {json.loads(line)["slug"] for line in file if line.strip()}

    labels, problems = import_label_studio(args.csv, catalog_slugs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(labels, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    counts: dict[str, int] = {}
    for value in labels.values():
        counts[value["status"]] = counts.get(value["status"], 0) + 1
    print(f"Фото: {len(labels)} · {counts} → {args.output}")
    for problem in problems:
        print(f"  исключено: {problem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
