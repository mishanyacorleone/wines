"""Общий энкодер изображений для индексации и для поисковых запросов.

Вынесен в отдельную библиотеку намеренно: индексатор и API обязаны считать
эмбеддинги абсолютно одинаково. Любое расхождение в препроцессинге (другой
ресайз, другой фон под альфа-каналом) тихо ломает поиск — вектора окажутся
в разных частях пространства, и это не видно ни по логам, ни по тестам.
"""

from .config import EmbeddingSettings, embedding_settings
from .encoder import ImageEncoder, get_encoder
from .preprocessing import normalize_image

__all__ = [
    "EmbeddingSettings",
    "embedding_settings",
    "ImageEncoder",
    "get_encoder",
    "normalize_image",
]
