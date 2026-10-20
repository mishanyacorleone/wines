# Сервис эмбеддингов: фото → вектор SigLIP 2 на GPU. Только модель, без поиска и каталога.
# CUDA-библиотеки приходят вместе с колёсами torch из PyPI, поэтому база — обычный python-slim.
# Сборка из корня репозитория: docker compose build embedder
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Структура путей как в репозитории: настройки ищут корень проекта (/app), а в нём models/
WORKDIR /app
# Сначала общий слой с torch и transformers — он одинаковый у embedder и индексатора,
# Docker хранит и собирает его один раз
COPY libs/wine-embeddings/requirements.txt libs/wine-embeddings/requirements.txt
RUN pip install -r libs/wine-embeddings/requirements.txt
COPY services/embedder/requirements.txt services/embedder/requirements.txt
RUN pip install -r services/embedder/requirements.txt

COPY libs/wine-embeddings/src libs/wine-embeddings/src
COPY libs/wine-logging/src libs/wine-logging/src
COPY services/embedder/src services/embedder/src
ENV PYTHONPATH=/app/libs/wine-embeddings/src:/app/libs/wine-logging/src:/app/services/embedder/src \
    WINE_EMBEDDER_HOST=0.0.0.0 \
    WINE_EMBEDDER_PORT=8091

EXPOSE 8091
HEALTHCHECK --interval=10s --timeout=5s --start-period=300s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=4)"

CMD ["python", "-m", "embedder"]
