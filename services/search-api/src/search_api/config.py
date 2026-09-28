"""Настройки API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from wine_embeddings.config import REPO_ROOT, embedding_settings


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class ApiSettings:
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    collection: str = _env("COLLECTION", "wines")

    default_top_k: int = int(_env("TOP_K", "5"))
    max_top_k: int = int(_env("MAX_TOP_K", "50"))

    # Ограничение на размер загружаемого файла: фото с телефона ~1.5 МБ,
    # 25 МБ с запасом покрывает любой разумный случай
    max_upload_bytes: int = int(_env("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

    # OCR-переранжирование: картинка отдаёт rerank_k кандидатов, текст
    # с этикетки переставляет их внутри. Выключается для сравнения с чистой
    # картинкой: WINE_OCR_ENABLED=0
    ocr_enabled: bool = _env("OCR_ENABLED", "1") == "1"
    ocr_models_dir: Path = field(
        default_factory=lambda: Path(
            _env("OCR_MODELS_DIR", str(embedding_settings.models_dir / "easyocr"))
        )
    )
    ocr_max_side: int = int(_env("OCR_MAX_SIDE", "1280"))
    ocr_min_confidence: float = float(_env("OCR_MIN_CONFIDENCE", "0.3"))
    rerank_k: int = int(_env("RERANK_K", "20"))
    text_weight: float = float(_env("TEXT_WEIGHT", "0.15"))

    # VLM-реранкер: Qwen3-VL выбирает вино внутри первых vlm_top_k кандидатов.
    # По умолчанию выключен — +9 ГБ VRAM и +0,7–1,2 с на запрос. Настройки по
    # умолчанию — лучший офлайн-вариант: текст и эталонные фото кандидатов,
    # картинки уменьшены (docs/baseline-results.md, «VLM-реранкер»)
    vlm_enabled: bool = _env("VLM_ENABLED", "0") == "1"
    vlm_model_dir: Path = field(
        default_factory=lambda: Path(
            _env("VLM_MODEL_DIR", str(embedding_settings.models_dir / "Qwen3-VL-4B-Instruct"))
        )
    )
    vlm_top_k: int = int(_env("VLM_TOP_K", "5"))
    vlm_candidate_images: bool = _env("VLM_CANDIDATE_IMAGES", "1") == "1"
    vlm_query_max_pixels: int = int(_env("VLM_QUERY_MAX_PIXELS", str(512 * 1024)))
    vlm_candidate_max_pixels: int = int(_env("VLM_CANDIDATE_MAX_PIXELS", str(128 * 1024)))
    # Эталонные фото кандидатов VLM берёт из каталога: путь image_path в
    # payload Qdrant — относительно этой папки
    catalog_dir: Path = field(
        default_factory=lambda: Path(_env("CATALOG_DIR", str(REPO_ROOT / "data" / "catalog")))
    )

    # Решение «одна карточка или варианты» (search_api.verdict): пороги
    # подобраны на размеченных фото, см. docstring модуля
    accept_min_image_score: float = float(_env("ACCEPT_MIN_IMAGE_SCORE", "0.76"))
    accept_min_margin: float = float(_env("ACCEPT_MIN_MARGIN", "0.02"))
    accept_min_vlm_prob: float = float(_env("ACCEPT_MIN_VLM_PROB", "0.8"))
    accept_max_vlm_none_prob: float = float(_env("ACCEPT_MAX_VLM_NONE_PROB", "0.5"))

    # Веб-интерфейс сканера и отзывы пользователей («не то вино»)
    web_enabled: bool = _env("WEB_ENABLED", "1") == "1"
    feedback_dir: Path = field(
        default_factory=lambda: Path(_env("FEEDBACK_DIR", str(REPO_ROOT / "data" / "feedback")))
    )
    # Ресайз-API картинок сайта: фото бутылок (если нет локального PNG),
    # блюд, регионов и сортов (search_api/site_media.json)
    site_image_api: str = _env("SITE_IMAGE_API", "https://api.vino-svoe.ru/v1/img/str-api")


api_settings = ApiSettings()
