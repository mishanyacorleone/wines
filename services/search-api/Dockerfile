# Сервис распознавания: /v1/eval/predict, /v1/scan, веб-интерфейс /app/.
# SigLIP 2 + Qwen3-VL на GPU через NVIDIA Container Toolkit; CUDA-библиотеки
# приходят вместе с колёсами torch из PyPI, поэтому база — обычный python-slim.
# Сборка из корня репозитория: docker compose build api
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
COPY services/search-api/requirements.txt services/search-api/requirements.txt
RUN pip install -r services/search-api/requirements.txt

COPY libs/wine-embeddings/src libs/wine-embeddings/src
COPY services/search-api/src services/search-api/src
ENV PYTHONPATH=/app/libs/wine-embeddings/src:/app/services/search-api/src \
    WINE_API_HOST=0.0.0.0 \
    WINE_API_PORT=8080

EXPOSE 8080
# Загрузка двух моделей и прогрев занимают до пары минут — отсюда start-period
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python", "-m", "search_api"]
