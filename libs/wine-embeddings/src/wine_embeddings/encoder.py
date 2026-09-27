"""Обёртка над SigLIP 2: изображение -> нормализованный вектор."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

from .config import EmbeddingSettings, embedding_settings
from .preprocessing import normalize_image
from .runtime import limit_vram, setup_model_cache

logger = logging.getLogger(__name__)


class ImageEncoder:
    """Считает эмбеддинги картинок. Модель грузится один раз при создании."""

    def __init__(self, settings: EmbeddingSettings | None = None) -> None:
        self.settings = settings or embedding_settings

        # Порядок важен: кэш и лимит выставляем до загрузки весов
        setup_model_cache(self.settings.models_dir)

        import torch
        from transformers import SiglipImageProcessor, SiglipVisionModel

        limit_vram(self.settings.vram_limit_gb, self.settings.device)

        device = self.settings.device
        if device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning("CUDA недоступна, откатываюсь на CPU")
            device = "cpu"
        # fp16 на CPU медленнее fp32 и местами не поддержан
        dtype = self.settings.torch_dtype if device != "cpu" else torch.float32

        source = self.settings.model_source
        logger.info("Загружаю %s на %s (%s)", source, device, dtype)
        # Берём только vision-башню: текстовая часть модели нам не нужна,
        # поиск идёт картинка-к-картинке, а веса текстовой башни — лишняя VRAM.
        self.model = SiglipVisionModel.from_pretrained(
            source, dtype=dtype
        ).to(device).eval()
        self.processor = SiglipImageProcessor.from_pretrained(source)

        self.device = device
        self.dtype = dtype
        self._torch = torch
        # FastAPI обслуживает запросы в пуле потоков; инференс одной модели
        # из нескольких потоков одновременно небезопасен
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return int(self.model.config.hidden_size)

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        """Возвращает матрицу (N, dim) L2-нормализованных векторов.

        Нормализация нужна, потому что в Qdrant метрика — косинусная:
        на единичных векторах скалярное произведение и есть косинус.
        """
        if not images:
            return np.empty((0, self.dimension), dtype=np.float32)

        torch = self._torch
        normalized = [normalize_image(image) for image in images]
        inputs = self.processor(images=normalized, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(self.device, dtype=self.dtype)

        with self._lock, torch.inference_mode():
            outputs = self.model(pixel_values=pixel_values)
            # pooler_output — то же представление, что использует SigLIP
            # для image-text матчинга, а не усреднение патчей
            vectors = outputs.pooler_output.float()
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=-1)

        return vectors.cpu().numpy().astype(np.float32)

    def encode_image(self, image: Image.Image) -> np.ndarray:
        return self.encode_images([image])[0]

    def encode_paths(self, paths: Iterable[Path | str]) -> np.ndarray:
        images = []
        for path in paths:
            with Image.open(path) as image:
                image.load()
                images.append(image)
        return self.encode_images(images)


_encoder: ImageEncoder | None = None
_encoder_lock = threading.Lock()


def get_encoder(settings: EmbeddingSettings | None = None) -> ImageEncoder:
    """Ленивый синглтон: модель грузится один раз на процесс.

    Для API это обязательное условие — по контракту организатора на запрос
    даётся секунды, загрузка весов на каждый запрос в них не уложится.
    """
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                _encoder = ImageEncoder(settings)
    return _encoder
