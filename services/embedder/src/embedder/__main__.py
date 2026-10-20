"""Запуск сервиса: python -m embedder"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

import wine_logging

if __name__ == "__main__":
    # .env читается до импорта приложения: настройки фиксируются при импорте модулей
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    wine_logging.setup("embedder")
    uvicorn.run(
        "embedder.main:app",
        host=os.environ.get("WINE_EMBEDDER_HOST", "127.0.0.1"),
        port=int(os.environ.get("WINE_EMBEDDER_PORT", "8091")),
        workers=1,  # одна модель на процесс: вторая копия — лишняя VRAM
        **wine_logging.uvicorn_options(),
    )
