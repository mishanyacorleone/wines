# Индексатор: фото каталога → эмбеддинги SigLIP 2 → Qdrant.
# GPU через NVIDIA Container Toolkit; CUDA-библиотеки приходят вместе с
# колёсами torch из PyPI, поэтому база — обычный python-slim, не cuda-образ.
# Сборка из корня репозитория: docker compose build indexer
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Структура путей как в репозитории: настройки ищут корень проекта (/app),
# а в нём models/ и data/catalog/ — их подключают томами
WORKDIR /app
# Сначала общий слой с torch и transformers — он одинаковый у API и индексатора,
# Docker хранит и собирает его один раз
COPY libs/wine-embeddings/requirements.txt libs/wine-embeddings/requirements.txt
RUN pip install -r libs/wine-embeddings/requirements.txt
COPY services/indexer/requirements.txt services/indexer/requirements.txt
RUN pip install -r services/indexer/requirements.txt

COPY libs/wine-embeddings/src libs/wine-embeddings/src
COPY services/indexer/src services/indexer/src
ENV PYTHONPATH=/app/libs/wine-embeddings/src:/app/services/indexer/src

ENTRYPOINT ["python", "-m", "indexer"]
