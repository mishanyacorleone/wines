# Сборщик логов: читает stdout всех контейнеров проекта (через Docker API) и
# раскладывает строки по дням: logs/<дата>/all.log — важное по всем сервисам,
# logs/<дата>/services/<сервис>.log — всё по одному сервису. Без GPU.
# Сборка из корня репозитория: docker compose build logs
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY services/log-collector/requirements.txt services/log-collector/requirements.txt
RUN pip install -r services/log-collector/requirements.txt

COPY libs/wine-logging/src libs/wine-logging/src
COPY services/log-collector/src services/log-collector/src
ENV PYTHONPATH=/app/libs/wine-logging/src:/app/services/log-collector/src

CMD ["python", "-m", "log_collector"]
