# Сервис распознавания и каталога: /v1/eval/predict, /v1/scan, карточки, сомелье.
# Без моделей и GPU: вектор фото — сервис embedder, выбор вина — vllm, индекс — qdrant.
# Сборка из корня репозитория: docker compose build api
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Структура путей как в репозитории: настройки ищут корень проекта (/app),
# а в нём data/catalog/ — его подключают томом
WORKDIR /app
COPY services/search-api/requirements.txt services/search-api/requirements.txt
RUN pip install -r services/search-api/requirements.txt

COPY libs/wine-logging/src libs/wine-logging/src
COPY services/search-api/src services/search-api/src
ENV PYTHONPATH=/app/libs/wine-logging/src:/app/services/search-api/src \
    WINE_API_HOST=0.0.0.0 \
    WINE_API_PORT=8080

EXPOSE 8080
# Старт — секунды: модели api не грузит, каталог читает в фоне
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python", "-m", "search_api"]
