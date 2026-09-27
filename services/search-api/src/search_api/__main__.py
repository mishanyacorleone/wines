"""Запуск сервиса: python -m search_api"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

if __name__ == "__main__":
    # .env читается до импорта приложения: настройки фиксируются при импорте
    # модулей. Уже заданные переменные окружения .env не перекрывает
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
    uvicorn.run(
        "search_api.main:app",
        host=os.environ.get("WINE_API_HOST", "127.0.0.1"),
        # 8080 — порт по умолчанию в скрипте оценки кейсодержателя
        port=int(os.environ.get("WINE_API_PORT", "8080")),
        workers=1,  # одна модель на процесс: вторая копия не влезет в лимит VRAM
    )
