"""Настройки сервиса пользователей. Переменные окружения с префиксом WINE_."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class UserSettings:
    # PostgreSQL — отдельный контейнер в compose. Тесты подставляют SQLite:
    # схема переносимая, без особенностей конкретной СУБД
    db_url: str = _env("USERS_DB_URL", "postgresql+psycopg://wines:wines@localhost:5433/wines")

    # Каталог вин — тот же Qdrant, что у сервиса поиска: единственный источник
    # истины о винах. Отсюда берутся регион, сорта и категория отсканированного
    # вина — по ним выдаются достижения
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    collection: str = _env("COLLECTION", "wines")

    # Загруженные файлы: аватары, превью сканов, картинки достижений
    media_dir: Path = field(
        default_factory=lambda: Path(_env("USERS_MEDIA_DIR", str(REPO_ROOT / "data" / "users")))
    )
    max_upload_bytes: int = int(_env("USERS_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

    # Сессия — непрозрачный токен в HttpOnly-cookie: скрипт страницы его не
    # видит, выход из аккаунта удаляет сессию на сервере
    session_cookie: str = _env("USERS_SESSION_COOKIE", "wine_session")
    session_days: int = int(_env("USERS_SESSION_DAYS", "30"))
    # true — cookie только по https. На демо сервис открывают по http
    cookie_secure: bool = _env("USERS_COOKIE_SECURE", "0") == "1"

    # Администратор создаётся при старте. Пароль не задан — генерируется и
    # пишется в лог сервиса (docker compose logs users)
    admin_login: str = _env("ADMIN_LOGIN", "admin@vino-svoe.local")
    admin_password: str = _env("ADMIN_PASSWORD", "")

    # День «вопроса дня», челленджей и итогов года — по местному времени (Москва без перехода на летнее)
    utc_offset_hours: int = int(_env("USERS_UTC_OFFSET_HOURS", "3"))

    host: str = _env("USERS_HOST", "127.0.0.1")
    port: int = int(_env("USERS_PORT", "8000"))


settings = UserSettings()
