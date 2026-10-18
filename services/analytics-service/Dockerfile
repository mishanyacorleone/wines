# Сервис аналитики: события веб-сканера → дашборд для РСХБ. Лёгкий, без GPU.
# Сборка из корня репозитория: docker compose build analytics
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY services/analytics-service/requirements.txt services/analytics-service/requirements.txt
RUN pip install -r services/analytics-service/requirements.txt

COPY services/analytics-service/src services/analytics-service/src
ENV PYTHONPATH=/app/services/analytics-service/src \
    WINE_ANALYTICS_HOST=0.0.0.0 \
    WINE_ANALYTICS_PORT=8001

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=4s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"

CMD ["python", "-m", "analytics_service"]
