"""Выбор вина внутри топ-5 с помощью VLM (Qwen3-VL).

Картинка и OCR надёжно находят линейку — верное вино почти всегда в пятёрке, —
но внутри линейки путаются: стилизованные названия OCR не читает, а цвет вина
и сладость эмбеддинг почти не различает. VLM видит этикетку целиком и сверяет
её с карточками кандидатов.

Генерации нет: один прямой проход, из логитов следующего токена берутся
вероятности ответов «1»…«N» и «0» (ни одна карточка не подходит). Так получается
распределение уверенности, а не одна строка, и время не зависит от того,
насколько модель разговорчива.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

PROMPT_HEAD = (
    "На фото — бутылка вина, снятая покупателем. Ниже карточки вин из каталога. "
    "Определи, какая карточка соответствует бутылке на фото. Сверяй надписи "
    "на этикетке: название, производителя, сорт винограда, цвет и сладость вина. "
    "Вина одной линейки выглядят почти одинаково и различаются только этими надписями."
)
PROMPT_TAIL = (
    "0. Ни одна карточка не подходит — такого вина нет в списке.\n\n"
    "Ответь одной цифрой — номером карточки."
)


@dataclass(frozen=True)
class VlmVerdict:
    # вероятность каждого кандидата, в порядке, в котором они переданы
    probs: list[float]
    # вероятность ответа «ни одна не подходит»
    none_prob: float


def _fit(image: Image.Image, max_pixels: int) -> Image.Image:
    """Уменьшает картинку до max_pixels по площади, сохраняя пропорции.

    Число визуальных токенов Qwen3-VL растёт с площадью (один токен на 32×32 px),
    а вместе с ним время прохода.
    """
    image = ImageOps.exif_transpose(image).convert("RGB")
    area = image.width * image.height
    if area > max_pixels:
        scale = (max_pixels / area) ** 0.5
        image = image.resize(
            (max(32, int(image.width * scale)), max(32, int(image.height * scale))),
            Image.Resampling.BICUBIC,
        )
    return image


def describe(candidate: dict[str, Any]) -> str:
    parts = [candidate.get("title"), candidate.get("manufacturer"), candidate.get("category")]
    text = " — ".join(p.strip() for p in parts if p and p.strip())
    grapes = candidate.get("grapes") or []
    if grapes:
        text += f"; сорта: {', '.join(grapes)}"
    return text


class VlmReranker:
    """Обёртка над Qwen3-VL. Веса грузятся один раз, только с диска."""

    def __init__(
        self,
        model_dir: Path,
        *,
        device: str,
        query_max_pixels: int,
        candidate_max_pixels: int,
        with_candidate_images: bool,
        catalog_dir: Path | None = None,
    ) -> None:
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        self._torch = torch
        self._device = device
        self._processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        self._model = Qwen3VLForConditionalGeneration.from_pretrained(
            model_dir, dtype=dtype, attn_implementation="sdpa", local_files_only=True
        ).to(device).eval()

        self._query_max_pixels = query_max_pixels
        self._candidate_max_pixels = candidate_max_pixels
        self._with_candidate_images = with_candidate_images and catalog_dir is not None
        self._catalog_dir = catalog_dir

        tokenizer = self._processor.tokenizer
        self._digit_ids = []
        for digit in range(10):
            ids = tokenizer.encode(str(digit), add_special_tokens=False)
            if len(ids) != 1:
                raise RuntimeError(f"цифра {digit} не один токен: {ids}")
            self._digit_ids.append(ids[0])
        self._lock = threading.Lock()
        logger.info("VLM готов: %s (картинки кандидатов: %s)", model_dir,
                    "да" if self._with_candidate_images else "нет")

    def _messages(self, image: Image.Image, candidates: list[dict[str, Any]]) -> list[dict]:
        content: list[dict[str, Any]] = [
            {"type": "image", "image": _fit(image, self._query_max_pixels)},
            {"type": "text", "text": PROMPT_HEAD + "\n\n"},
        ]
        for number, candidate in enumerate(candidates, start=1):
            content.append({"type": "text", "text": f"{number}. {describe(candidate)}\n"})
            reference = self._reference(candidate)
            if reference is not None:
                content.append({"type": "image", "image": reference})
        content.append({"type": "text", "text": PROMPT_TAIL})
        return [{"role": "user", "content": content}]

    def _reference(self, candidate: dict[str, Any]) -> Image.Image | None:
        if not self._with_candidate_images or not candidate.get("image_path"):
            return None
        path = self._catalog_dir / candidate["image_path"]
        if not path.exists():
            return None
        with Image.open(path) as image:
            return _fit(image, self._candidate_max_pixels)

    def judge(self, image: Image.Image, candidates: list[dict[str, Any]]) -> VlmVerdict:
        if not candidates:
            return VlmVerdict([], 1.0)
        if len(candidates) > 9:
            raise ValueError("VLM выбирает максимум из 9 кандидатов: ответ — одна цифра")

        inputs = self._processor.apply_chat_template(
            self._messages(image, candidates),
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self._device)

        torch = self._torch
        with self._lock, torch.inference_mode():
            logits = self._model(**inputs, logits_to_keep=1).logits[0, -1]

        # Softmax только по допустимым ответам: 0 и номера кандидатов
        allowed = [self._digit_ids[d] for d in range(len(candidates) + 1)]
        probs = torch.softmax(logits[allowed].float(), dim=-1).tolist()
        return VlmVerdict(probs=probs[1:], none_prob=probs[0])
