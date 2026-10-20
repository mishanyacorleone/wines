"""Запуск сервиса: python -m catalog_service"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

import wine_logging

if __name__ == "__main__":
    # .env читается до импорта приложения: настройки фиксируются при импорте модулей
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    wine_logging.setup("catalog")
    uvicorn.run(
        "catalog_service.main:app",
        host=os.environ.get("WINE_CATALOG_HOST", "127.0.0.1"),
        port=int(os.environ.get("WINE_CATALOG_PORT", "8002")),
        **wine_logging.uvicorn_options(),
    )
