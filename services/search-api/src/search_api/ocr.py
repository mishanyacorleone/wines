"""Чтение текста с этикетки (EasyOCR).

Картинка находит линейку производителя, но внутри линейки вина различаются
почти только надписями: сорт, цвет, сладость. Их и читаем.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float


class LabelReader:
    """Обёртка над EasyOCR. Веса грузятся один раз, только с диска."""

    def __init__(
        self, models_dir: Path, *, gpu: bool, max_side: int, min_confidence: float
    ) -> None:
        import easyocr

        # download_enabled=False: веса кладёт infra/fetch-ocr.sh, встроенный
        # загрузчик на обрывах прокси начинает файл заново
        self._reader = easyocr.Reader(
            ["ru", "en"],
            gpu=gpu,
            model_storage_directory=str(models_dir),
            download_enabled=False,
            verbose=False,
        )
        self._max_side = max_side
        self._min_confidence = min_confidence
        # как и энкодер, модель не рассчитана на вызовы из нескольких потоков
        self._lock = threading.Lock()
        logger.info("OCR готов: %s", models_dir)

    def read(self, image: Image.Image) -> list[OcrLine]:
        image = ImageOps.exif_transpose(image).convert("RGB")
        # Полные 3024x4032 читаются в разы дольше, а крупный текст этикетки
        # (сорт, название) разборчив и на уменьшенной копии
        image.thumbnail((self._max_side, self._max_side))

        with self._lock:
            detections = self._reader.readtext(np.asarray(image))

        return [
            OcrLine(text=text, confidence=float(confidence))
            for _, text, confidence in detections
            if confidence >= self._min_confidence and text.strip()
        ]
