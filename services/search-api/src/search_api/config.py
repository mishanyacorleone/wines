"""Настройки API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень репозитория: .../wines/services/search-api/src/search_api/config.py
REPO_ROOT = Path(__file__).resolve().parents[4]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class ApiSettings:
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    collection: str = _env("COLLECTION", "wines")
    # Таймаут запросов к Qdrant: по умолчанию в клиенте 5 с — на старте (чтение
    # всех payload) под чужой нагрузкой на машину не хватало
    qdrant_timeout_s: int = int(_env("QDRANT_TIMEOUT_S", "60"))

    # Топ-10: один ответ идёт в метрику (/v1/eval/predict), весь список — в
    # веб-интерфейс как варианты и аналоги
    default_top_k: int = int(_env("TOP_K", "10"))
    max_top_k: int = int(_env("MAX_TOP_K", "50"))

    # Ограничение на размер загружаемого файла: фото с телефона ~1.5 МБ,
    # 25 МБ с запасом покрывает любой разумный случай
    max_upload_bytes: int = int(_env("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))

    # Сервисы с моделями. embedder — SigLIP 2 (фото → вектор); vLLM — Qwen3-VL
    # выбирает вино среди первых vlm_top_k кандидатов и проверяет выбранное.
    # Таймаут соединения короткий: упавший сервис должен давать быстрый отказ,
    # а не 30 с ожидания; после сбоя сервис не дёргается cooldown секунд (remote.Breaker)
    embedder_url: str = _env("EMBEDDER_URL", "http://127.0.0.1:8091")
    embedder_timeout_s: float = float(_env("EMBEDDER_TIMEOUT_S", "15"))
    connect_timeout_s: float = float(_env("CONNECT_TIMEOUT_S", "2"))
    unavailable_cooldown_s: float = float(_env("UNAVAILABLE_COOLDOWN_S", "10"))

    # VLM — часть основного пайплайна; выключается только для сравнения с чистой
    # картинкой или на машине без GPU для vLLM: WINE_VLM_ENABLED=0. Картинки
    # уменьшены — почти без потери точности (docs/baseline-results.md, «VLM-реранкер»)
    vlm_enabled: bool = _env("VLM_ENABLED", "1") == "1"
    # VLM выбирает из топ-5, наружу уходит топ-10 (TOP_K): верное вино на
    # размеченных фото всегда в топ-5, а лишние кандидаты путают VLM вина одной
    # линейки — из топ-10 top-1 был 92,5% против 96,2% и на ~0,4 с дольше
    vlm_top_k: int = int(_env("VLM_TOP_K", "5"))
    vllm_url: str = _env("VLLM_URL", "http://127.0.0.1:8100")
    vllm_model: str = _env("VLLM_SERVED_MODEL", "vlm")
    # Папка каталога так, как её видит vLLM (--allowed-local-media-path): эталоны
    # уходят путями к кэшу vlm-refs-*, а не base64. Пусто — base64
    vllm_media_dir: str = _env("VLLM_MEDIA_DIR", "")
    vllm_timeout_s: float = float(_env("VLLM_TIMEOUT_S", "30"))
    vlm_candidate_images: bool = _env("VLM_CANDIDATE_IMAGES", "1") == "1"
    vlm_query_max_pixels: int = int(_env("VLM_QUERY_MAX_PIXELS", str(512 * 1024)))
    vlm_candidate_max_pixels: int = int(_env("VLM_CANDIDATE_MAX_PIXELS", str(128 * 1024)))
    # Эталонные фото кандидатов VLM берёт из каталога: путь image_path в
    # payload Qdrant — относительно этой папки
    catalog_dir: Path = field(
        default_factory=lambda: Path(_env("CATALOG_DIR", str(REPO_ROOT / "data" / "catalog")))
    )

    # Решение «вино найдено или нет в каталоге» (search_api.verdict): пороги
    # подобраны на размеченных фото, см. docstring модуля
    accept_min_verify_prob: float = float(_env("ACCEPT_MIN_VERIFY_PROB", "0.3"))
    accept_max_vlm_none_prob: float = float(_env("ACCEPT_MAX_VLM_NONE_PROB", "0.5"))
    accept_min_vlm_prob: float = float(_env("ACCEPT_MIN_VLM_PROB", "0.8"))
    # 0 (по умолчанию): /v1/eval/predict и top1 всегда отдают вино, выбранное
    # VLM, — по условиям проверки все фото есть в каталоге (29.09.2026).
    # 1: вина нет в каталоге — {"slug": null} и top1: null. Веб-сканер пишет
    # «нет в каталоге» при любом значении: экран строится по reason, не по top1
    predict_empty_if_absent: bool = _env("PREDICT_EMPTY_IF_ABSENT", "0") == "1"
    # только без VLM
    accept_min_image_score: float = float(_env("ACCEPT_MIN_IMAGE_SCORE", "0.76"))
    accept_min_margin: float = float(_env("ACCEPT_MIN_MARGIN", "0.02"))

    # Каталог в памяти (сомелье, поиск по тексту) сверяется с Qdrant раз в
    # catalog_refresh_s: вино, добавленное синхронизацией или индексатором,
    # появляется без перезапуска (синхронизация ещё и просит перечитать сразу)
    catalog_refresh_s: float = float(_env("CATALOG_REFRESH_S", "60"))

    # Отзывы пользователей («не то вино»)
    feedback_dir: Path = field(
        default_factory=lambda: Path(_env("FEEDBACK_DIR", str(REPO_ROOT / "data" / "feedback")))
    )
    # Ресайз-API картинок сайта: фото бутылок (если нет локального PNG),
    # блюд, регионов и сортов (search_api/site_media.json)
    site_image_api: str = _env("SITE_IMAGE_API", "https://api.vino-svoe.ru/v1/img/str-api")


api_settings = ApiSettings()


def bypass_proxy_for_localhost() -> None:
    """Исключает локальные адреса из системного прокси.

    На машине разработки задан HTTP(S)_PROXY, и без этого клиент Qdrant идёт
    на localhost:6333 через внешний прокси, который отвечает 503 Forwarding
    failure. Вызывать до создания HTTP-клиентов.
    """
    local = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    for name in ("NO_PROXY", "no_proxy"):
        current = {h.strip() for h in os.environ.get(name, "").split(",") if h.strip()}
        os.environ[name] = ",".join(sorted(current | local))
