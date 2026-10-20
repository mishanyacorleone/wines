"""Запуск сервиса: python -m user_service"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

import wine_logging

if __name__ == "__main__":
    # .env читается до импорта приложения: настройки фиксируются при импорте
    # модулей. Уже заданные переменные окружения .env не перекрывает
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    wine_logging.setup("users")
    uvicorn.run(
        "user_service.main:app",
        host=os.environ.get("WINE_USERS_HOST", "127.0.0.1"),
        port=int(os.environ.get("WINE_USERS_PORT", "8000")),
        # за шлюзом: X-Forwarded-For — настоящий адрес клиента для лимита попыток входа
        proxy_headers=True,
        forwarded_allow_ips="*",
        **wine_logging.uvicorn_options(),
    )
