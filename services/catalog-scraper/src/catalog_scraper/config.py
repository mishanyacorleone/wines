"""Настройки сервиса. Значения берутся из переменных окружения с префиксом SCRAPER_."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень репозитория: .../wines/services/catalog-scraper/src/catalog_scraper/config.py
REPO_ROOT = Path(__file__).resolve().parents[4]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"SCRAPER_{name}", default)


@dataclass(frozen=True)
class Settings:
    # --- источник ---
    base_url: str = _env("BASE_URL", "https://vino-svoe.ru")
    sitemap_url: str = _env("SITEMAP_URL", "https://vino-svoe.ru/wines-sitemap.xml")
    image_api_url: str = _env("IMAGE_API_URL", "https://api.vino-svoe.ru")

    # Запрашиваем заведомо больший размер, чем оригинал: API не апскейлит,
    # поэтому так мы получаем нативное разрешение. Потолок API — 3840.
    image_request_size: int = int(_env("IMAGE_REQUEST_SIZE", "3840"))

    # --- вежливость к серверу ---
    concurrency: int = int(_env("CONCURRENCY", "4"))
    delay_sec: float = float(_env("DELAY_SEC", "0.2"))
    timeout_sec: float = float(_env("TIMEOUT_SEC", "30"))
    max_retries: int = int(_env("MAX_RETRIES", "3"))
    user_agent: str = _env(
        "USER_AGENT",
        "Mozilla/5.0 (compatible; svoe-vino-scanner-hackathon/0.1; catalog indexing)",
    )

    # --- куда складываем ---
    output_dir: Path = field(
        default_factory=lambda: Path(_env("OUTPUT_DIR", str(REPO_ROOT / "data" / "catalog")))
    )

    @property
    def images_dir(self) -> Path:
        return self.output_dir / "images"

    @property
    def catalog_path(self) -> Path:
        return self.output_dir / "catalog.jsonl"

    @property
    def failures_path(self) -> Path:
        return self.output_dir / "failures.jsonl"

    @property
    def sitemap_cache_path(self) -> Path:
        return self.output_dir / "wines-sitemap.xml"


settings = Settings()
