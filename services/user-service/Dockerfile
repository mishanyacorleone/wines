# Сервис пользователей: аккаунт, история сканов, оценки, достижения.
# Без GPU и без torch — лёгкий образ, перезапускается за секунды.
# Сборка из корня репозитория: docker compose build users
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Структура путей как в репозитории: настройки ищут корень проекта (/app),
# а в нём data/users/ — его подключают томом
WORKDIR /app
COPY services/user-service/requirements.txt services/user-service/requirements.txt
RUN pip install -r services/user-service/requirements.txt

COPY services/user-service/src services/user-service/src
ENV PYTHONPATH=/app/services/user-service/src \
    WINE_USERS_HOST=0.0.0.0 \
    WINE_USERS_PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=4s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "-m", "user_service"]
