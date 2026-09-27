FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    SCRAPER_OUTPUT_DIR=/data/catalog

VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "catalog_scraper"]
