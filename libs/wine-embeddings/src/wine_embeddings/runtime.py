"""Подготовка окружения до импорта torch/transformers.

Важно: переменные кэша HuggingFace читаются при импорте библиотеки, поэтому
setup_environment() должен вызываться раньше, чем transformers попадёт в
sys.modules.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def setup_model_cache(models_dir: Path) -> None:
    """Складывает веса моделей внутрь проекта, а не в ~/.cache."""
    models_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(models_dir))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(models_dir / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(models_dir / "hub"))
    os.environ.setdefault("TORCH_HOME", str(models_dir / "torch"))


def limit_vram(limit_gb: float, device: str) -> None:
    """Жёстко ограничивает долю видеопамяти, доступную процессу.

    На карте работают посторонние сервисы. Лимит выставляется как доля от
    полного объёма устройства: при превышении процесс получит OOM и упадёт
    сам, вместо того чтобы вытеснить чужую память.
    """
    import torch

    if not device.startswith("cuda") or not torch.cuda.is_available():
        return

    index = torch.cuda.current_device()
    total_gb = torch.cuda.get_device_properties(index).total_memory / 1024**3
    fraction = min(limit_gb / total_gb, 1.0)
    torch.cuda.set_per_process_memory_fraction(fraction, index)
    logger.info(
        "Лимит VRAM: %.1f ГБ из %.1f ГБ (%.0f%%) на %s",
        limit_gb, total_gb, fraction * 100, torch.cuda.get_device_name(index),
    )


def vram_used_gb() -> float:
    import torch

    if not torch.cuda.is_available():
        return 0.0
    return torch.cuda.max_memory_allocated() / 1024**3


def bypass_proxy_for_localhost() -> None:
    """Исключает локальные адреса из системного прокси.

    В окружении задан HTTP(S)_PROXY, и без этого клиент Qdrant пытается
    достучаться до localhost:6333 через внешний прокси, который отвечает
    503 Forwarding failure. Вызывать до создания HTTP-клиентов.
    """
    local = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    for name in ("NO_PROXY", "no_proxy"):
        current = {h.strip() for h in os.environ.get(name, "").split(",") if h.strip()}
        os.environ[name] = ",".join(sorted(current | local))
