"""Сервис эмбеддингов: фото → нормализованный вектор SigLIP 2.

Единственная задача — модель. Поиск, каталог и решение «найдено / нет» живут
в api; этот сервис можно перезапустить, заменить или вынести на другую машину,
не трогая остальных. Энкодер — тот же `wine_embeddings`, что у индексатора:
векторы запросов и каталога считаются одинаково.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

import wine_logging
from wine_embeddings import get_encoder

logger = logging.getLogger("embedder")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_state: dict[str, object] = {}
_stats_lock = threading.Lock()
_stats = {"requests": 0, "encode_ms": 0.0}


class EmbedResponse(BaseModel):
    vector: list[float]
    dimension: int
    model_id: str
    decode_ms: float
    encode_ms: float


class HealthResponse(BaseModel):
    status: str
    model_id: str
    device: str
    dimension: int
    requests: int
    mean_encode_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    started = time.perf_counter()
    encoder = get_encoder()
    # Холостой проход: первый вызов CUDA-ядер в разы медленнее следующих
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), "white").save(buffer, format="PNG")
    encoder.encode_image(Image.open(buffer))
    _state["encoder"] = encoder
    logger.info("Модель готова: %s на %s, вектор %d, старт за %.0f с",
                encoder.settings.model_id, encoder.device, encoder.dimension,
                time.perf_counter() - started)
    yield


app = FastAPI(title="Эмбеддинги «Своё Вино»", version="1.0.0", lifespan=lifespan)
app.add_middleware(wine_logging.RequestLogMiddleware)


def _encoder():
    encoder = _state.get("encoder")
    if encoder is None:
        raise HTTPException(status_code=503, detail="Модель ещё загружается")
    return encoder


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    encoder = _encoder()
    with _stats_lock:
        requests, total = _stats["requests"], _stats["encode_ms"]
    return HealthResponse(
        status="ok",
        model_id=encoder.settings.model_id,  # type: ignore[attr-defined]
        device=encoder.device,  # type: ignore[attr-defined]
        dimension=encoder.dimension,  # type: ignore[attr-defined]
        requests=int(requests),
        mean_encode_ms=round(total / requests, 1) if requests else 0.0,
    )


@app.post("/v1/embed", response_model=EmbedResponse)
def embed(image: UploadFile = File(..., description="фото бутылки")) -> EmbedResponse:
    """Вектор фото. Обычная (не async) функция: FastAPI выполнит её в пуле потоков,
    и модель не блокирует приём следующих запросов."""
    encoder = _encoder()
    raw = image.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    started = time.perf_counter()
    try:
        picture = Image.open(io.BytesIO(raw))
        picture.load()
    except Exception as exc:  # Pillow бросает разнородные исключения
        raise HTTPException(status_code=400, detail=f"Не изображение: {exc}") from exc
    decode_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    vector = encoder.encode_image(picture)  # type: ignore[attr-defined]
    encode_ms = (time.perf_counter() - started) * 1000
    with _stats_lock:
        _stats["requests"] += 1
        _stats["encode_ms"] += encode_ms
    logger.debug("Вектор %d×%d px: декодирование %s, модель %s", picture.width, picture.height,
                 wine_logging.format_ms(decode_ms), wine_logging.format_ms(encode_ms))
    return EmbedResponse(
        vector=vector.tolist(),
        dimension=len(vector),
        model_id=encoder.settings.model_id,  # type: ignore[attr-defined]
        decode_ms=round(decode_ms, 2),
        encode_ms=round(encode_ms, 2),
    )
