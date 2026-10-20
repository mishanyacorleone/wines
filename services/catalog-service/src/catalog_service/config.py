"""Настройки сервиса каталога. Переменные окружения с префиксом WINE_."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень репозитория: .../wines/services/catalog-service/src/catalog_service/config.py
REPO_ROOT = Path(__file__).resolve().parents[4]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class CatalogSettings:
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    collection: str = _env("COLLECTION", "wines")
    # Вектор нового вина считает тот же embedder, что и для фото покупателей
    embedder_url: str = _env("EMBEDDER_URL", "http://127.0.0.1:8091")
    # api перечитывает каталог в память после добавления вин
    api_url: str = _env("CATALOG_API_URL", "http://127.0.0.1:8090")
    # кто админ — решает сервис пользователей (ему пересылается cookie сессии)
    users_url: str = _env("CATALOG_USERS_URL", "http://127.0.0.1:8000")
    catalog_dir: Path = field(
        default_factory=lambda: Path(_env("CATALOG_DIR", str(REPO_ROOT / "data" / "catalog")))
    )
    site_base_url: str = _env("SITE_BASE_URL", "https://vino-svoe.ru")
    # Расписание по умолчанию; дальше его меняет админ на странице (хранится в sync-state.json)
    default_auto: bool = _env("CATALOG_SYNC_AUTO", "1") == "1"
    default_times: int = int(_env("CATALOG_SYNC_TIMES", "1"))  # сколько раз…
    default_per: str = _env("CATALOG_SYNC_PER", "day")  # …за minute | hour | day | week | month
    host: str = _env("CATALOG_HOST", "127.0.0.1")
    port: int = int(_env("CATALOG_PORT", "8002"))

    @property
    def catalog_path(self) -> Path:
        return self.catalog_dir / "catalog.jsonl"

    @property
    def state_path(self) -> Path:
        return self.catalog_dir / "sync-state.json"


settings = CatalogSettings()
