"""Настройки энкодера. Переменные окружения с префиксом WINE_."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WINE_{name}", default)


@dataclass(frozen=True)
class EmbeddingSettings:
    # SigLIP 2 — рекомендация кейсодержателя. Разрешение 512, а не 224:
    # вина различаются мелкими деталями этикетки, на низком разрешении
    # near-duplicates схлопываются в один вектор.
    model_id: str = _env("MODEL_ID", "google/siglip2-so400m-patch16-512")

    device: str = _env("DEVICE", "cuda")
    # fp16 вдвое экономит VRAM и ускоряет инференс; на retrieval-задаче
    # потеря точности относительно fp32 пренебрежимо мала.
    dtype: str = _env("DTYPE", "float16")

    # Жёсткий потолок VRAM: на машине крутятся чужие сервисы, занимать
    # всю карту нельзя. При превышении процесс получит OOM, а не отъест
    # чужую память.
    vram_limit_gb: float = float(_env("VRAM_LIMIT_GB", "14"))

    batch_size: int = int(_env("BATCH_SIZE", "32"))

    # Все веса складываем внутрь проекта, а не в ~/.cache
    models_dir: Path = field(
        default_factory=lambda: Path(_env("MODELS_DIR", str(REPO_ROOT / "models")))
    )

    @property
    def model_source(self) -> str:
        """Путь к весам: локальная папка, если она есть, иначе идентификатор HF.

        Сеть до HuggingFace в этом окружении нестабильна (трафик идёт через
        прокси, который рвёт длинные соединения), поэтому веса лежат в проекте
        и загружаются с диска. Идентификатор модели остаётся источником истины
        для воспроизводимости — по нему видно, что именно скачано.
        """
        local = self.models_dir / self.model_id.split("/")[-1]
        if (local / "config.json").exists():
            return str(local)
        return self.model_id

    @property
    def torch_dtype(self):
        import torch

        return {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}[
            self.dtype
        ]


embedding_settings = EmbeddingSettings()
