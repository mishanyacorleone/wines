"""Запуск сервиса: python -m analytics_service"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

if __name__ == "__main__":
    # .env читается до импорта приложения: настройки фиксируются при импорте модулей
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    uvicorn.run(
        "analytics_service.main:app",
        host=os.environ.get("WINE_ANALYTICS_HOST", "127.0.0.1"),
        port=int(os.environ.get("WINE_ANALYTICS_PORT", "8001")),
    )
