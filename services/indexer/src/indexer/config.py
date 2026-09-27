"""Настройки индексатора и общие параметры коллекции Qdrant."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class IndexSettings:
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    collection: str = _env("COLLECTION", "wines")

    catalog_dir: Path = field(
        default_factory=lambda: Path(_env("CATALOG_DIR", str(REPO_ROOT / "data" / "catalog")))
    )
    site_base_url: str = _env("SITE_BASE_URL", "https://vino-svoe.ru")

    @property
    def catalog_path(self) -> Path:
        return self.catalog_dir / "catalog.jsonl"


index_settings = IndexSettings()
