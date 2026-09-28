# Парсер каталога vino-svoe.ru → data/catalog (catalog.jsonl + images/*.png).
# Без torch и GPU: только HTTP-клиент и Pillow.
# Сборка из корня репозитория: docker compose build scraper
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Структура путей как в репозитории: настройки ищут корень проекта
# на четыре уровня выше модуля (/app), а каталог — в /app/data/catalog
WORKDIR /app
COPY services/catalog-scraper/requirements.txt services/catalog-scraper/requirements.txt
RUN pip install -r services/catalog-scraper/requirements.txt

COPY services/catalog-scraper/src services/catalog-scraper/src
ENV PYTHONPATH=/app/services/catalog-scraper/src

ENTRYPOINT ["python", "-m", "catalog_scraper"]
