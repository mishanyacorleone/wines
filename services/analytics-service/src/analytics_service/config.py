"""Настройки сервиса аналитики. Переменные окружения с префиксом WINE_."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class AnalyticsSettings:
    # Та же база PostgreSQL, что у сервиса пользователей, своя таблица events
    db_url: str = _env("ANALYTICS_DB_URL", "postgresql+psycopg://wines:wines@localhost:5433/wines")
    # Кто админ — решает сервис пользователей: ему пересылается cookie сессии
    users_url: str = _env("ANALYTICS_USERS_URL", "http://localhost:8000")
    # Вина для демо-данных — из каталога сервиса распознавания
    api_url: str = _env("ANALYTICS_API_URL", "http://localhost:8080")
    # Защита от засорения: событий в минуту с одного адреса
    max_events_per_minute: int = int(_env("ANALYTICS_MAX_EVENTS_PER_MINUTE", "120"))
    host: str = _env("ANALYTICS_HOST", "127.0.0.1")
    port: int = int(_env("ANALYTICS_PORT", "8001"))


settings = AnalyticsSettings()
