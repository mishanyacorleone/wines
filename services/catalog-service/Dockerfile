# Сервис каталога: синхронизация с vino-svoe.ru (новые вина → фото → вектор → Qdrant)
# по кнопке в админке и по расписанию. Без torch и GPU: вектор считает embedder.
# Код парсера и записи в индекс — общий с catalog-scraper и indexer (те же пути, что в репозитории).
# Сборка из корня репозитория: docker compose build catalog
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY services/catalog-service/requirements.txt services/catalog-service/requirements.txt
RUN pip install -r services/catalog-service/requirements.txt

COPY libs/wine-logging/src libs/wine-logging/src
COPY services/catalog-scraper/src services/catalog-scraper/src
COPY services/indexer/src services/indexer/src
COPY services/catalog-service/src services/catalog-service/src
ENV PYTHONPATH=/app/libs/wine-logging/src:/app/services/catalog-scraper/src:/app/services/indexer/src:/app/services/catalog-service/src \
    WINE_CATALOG_HOST=0.0.0.0 \
    WINE_CATALOG_PORT=8002

EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=4s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3)"

CMD ["python", "-m", "catalog_service"]
