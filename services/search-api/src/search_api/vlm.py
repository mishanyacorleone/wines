"""Выбор вина среди первых кандидатов векторного поиска с помощью VLM (Qwen3-VL).

Картинка надёжно находит линейку — верное вино почти всегда в первых кандидатах, —
но внутри линейки путается: цвет вина, сладость и стилизованные названия
эмбеддинг почти не различает. VLM видит этикетку целиком и сверяет её с
карточками кандидатов.

Генерации нет: один прямой проход, из логитов следующего токена берутся
вероятности ответов «1»…«N» (карточки) и «0» (ни одна не подходит). Так
получается распределение уверенности, а не одна строка, и время не зависит от
того, насколько модель разговорчива. Цифра — один токен, поэтому кандидатов не
больше девяти; буквы A–J позволяли десять, но на топ-5 цифры точнее на одно
фото (96,2% против 94,3%), а десять кандидатов не нужны
(docs/baseline-results.md, «Итоговый пайплайн»).

Второй проход — проверка выбранного вина (`verify`): «это одно и то же вино?» —
по этикетке, тексту на бутылке, форме бутылки и цвету вина; ответ «Да»/«Нет».
Выбирая из нескольких, модель почти всегда называет самое похожее вино и редко
отвечает «0» — даже когда на фото другое вино того же производителя. Попарный вопрос она решает честнее: на размеченных фото
отказ по нему ловит 31 из 33 вин вне каталога против 7 по ответу «0»
(docs/baseline-results.md, «Отказ "нет в каталоге"»).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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

# Сверяется не только этикетка: вина одного производителя бывают с почти
# одинаковой этикеткой, но в другой бутылке или другого цвета. На размеченных
# фото вопрос «одна ли этикетка» и этот дают те же 31 из 33 отказов, но этот
# увереннее (меньше ответов в серой зоне 0,1–0,9) и не зависит от порога 0,2–0,4
VERIFY_PROMPT = (
    "Первое фото — бутылка, снятая покупателем. Второе фото — эталонное фото "
    "вина из каталога ({description}).\n"
    "Это одно и то же вино? Сравни всё, что видно на обоих фото: этикетку и "
    "надписи на ней, текст на бутылке и горлышке, форму бутылки и цвет вина. "
    "Год урожая, ракурс и условия съёмки не важны.\n"
    "Ответь одним словом: Да или Нет."
)

# Метки карточек: одна цифра — один токен, поэтому кандидатов не больше девяти
LABELS = "123456789"
NONE_LABEL = "0"
MAX_CANDIDATES = len(LABELS)


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
        # [0] — «ни одна не подходит», дальше — карточки по порядку
        self._answer_ids = []
        for label in NONE_LABEL + LABELS:
            ids = tokenizer.encode(label, add_special_tokens=False)
            if len(ids) != 1:
                raise RuntimeError(f"метка {label!r} не один токен: {ids}")
            self._answer_ids.append(ids[0])
        # «Да» и «Нет» — по два токена; первые токены у них разные, и их хватает,
        # чтобы сравнить два ответа
        self._yes_no_ids = [
            tokenizer.encode(word, add_special_tokens=False)[0] for word in ("Да", "Нет")
        ]
        if self._yes_no_ids[0] == self._yes_no_ids[1]:
            raise RuntimeError("«Да» и «Нет» начинаются с одного токена")
        self._lock = threading.Lock()
        logger.info("VLM готов: %s (картинки кандидатов: %s)", model_dir,
                    "да" if self._with_candidate_images else "нет")

    def _messages(self, image: Image.Image, candidates: list[dict[str, Any]]) -> list[dict]:
        content: list[dict[str, Any]] = [
            {"type": "image", "image": _fit(image, self._query_max_pixels)},
            {"type": "text", "text": PROMPT_HEAD + "\n\n"},
        ]
        for label, candidate in zip(LABELS, candidates):
            content.append({"type": "text", "text": f"{label}. {describe(candidate)}\n"})
            reference = self._reference(candidate)
            if reference is not None:
                content.append({"type": "image", "image": reference})
        content.append({"type": "text", "text": PROMPT_TAIL})
        return [{"role": "user", "content": content}]

    def _reference(self, candidate: dict[str, Any], *, force: bool = False) -> Image.Image | None:
        """Эталонное фото кандидата; force — даже если в выборе карточек они выключены."""
        if not (self._with_candidate_images or force) or self._catalog_dir is None:
            return None
        if not candidate.get("image_path"):
            return None
        return self._load_reference(candidate["image_path"])

    @property
    def _cache_dir(self) -> Path:
        # Лимит пикселей в имени папки: сменили лимит — кэш строится заново
        return self._catalog_dir / f"vlm-refs-{self._candidate_max_pixels}"

    def _load_reference(self, image_path: str) -> Image.Image | None:
        """Уменьшенный эталон из кэша на диске, при промахе — из оригинала.

        Оригиналы каталога — PNG по 1–2,5 МБ: декодировать десяток на запрос
        стоило до 2,5 с при SLA 3 с. Уменьшенная копия читается за миллисекунды.
        Кэш — тоже PNG, без потерь: VLM видит те же пиксели, что и без кэша.
        """
        cached = self._cache_dir / image_path
        if cached.exists():
            with Image.open(cached) as image:
                image.load()
                return image
        path = self._catalog_dir / image_path
        if not path.exists():
            return None
        with Image.open(path) as image:
            fitted = _fit(image, self._candidate_max_pixels)
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            # через временный файл: параллельный запрос не прочтёт недописанный PNG
            partial = cached.with_name(cached.name + f".{threading.get_ident()}.tmp")
            fitted.save(partial, format="PNG")
            partial.replace(cached)
        except OSError as exc:
            logger.warning("Не удалось сохранить эталон в кэш %s: %s", cached, exc)
        return fitted

    def warm_reference_cache(self, image_paths: list[str], workers: int = 8) -> None:
        """Строит недостающие уменьшенные эталоны — один раз, дальше они на диске.

        Запускается в фоне при старте сервиса: запросы, пришедшие раньше,
        просто читают оригинал сами, как без кэша.
        """
        if self._catalog_dir is None:
            return
        missing = [p for p in image_paths if not (self._cache_dir / p).exists()]
        if not missing:
            return
        started = time.perf_counter()
        # Декодирование PNG в Pillow отпускает GIL — потоков достаточно
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self._load_reference, missing))
        logger.info("Кэш эталонов VLM: %d фото за %.0f с → %s",
                    len(missing), time.perf_counter() - started, self._cache_dir)

    def _next_token_logits(self, messages: list[dict]):
        inputs = self._processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self._device)
        with self._lock, self._torch.inference_mode():
            return self._model(**inputs, logits_to_keep=1).logits[0, -1]

    def judge(self, image: Image.Image, candidates: list[dict[str, Any]]) -> VlmVerdict:
        if not candidates:
            return VlmVerdict([], 1.0)
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError(f"VLM выбирает максимум из {MAX_CANDIDATES} кандидатов")

        logits = self._next_token_logits(self._messages(image, candidates))
        # Softmax только по допустимым ответам: «0» и метки переданных кандидатов
        allowed = self._answer_ids[: len(candidates) + 1]
        probs = self._torch.softmax(logits[allowed].float(), dim=-1).tolist()
        return VlmVerdict(probs=probs[1:], none_prob=probs[0])

    def verify(self, image: Image.Image, candidate: dict[str, Any]) -> float | None:
        """Вероятность, что на фото именно это вино; None — нет эталонного фото.

        Без эталона сравнивать не с чем: вопрос про «одну и ту же этикетку»
        по одному тексту карточки модель решает заметно хуже.
        """
        reference = self._reference(candidate, force=True)
        if reference is None:
            return None
        messages = [{"role": "user", "content": [
            {"type": "image", "image": _fit(image, self._query_max_pixels)},
            {"type": "image", "image": reference},
            {"type": "text", "text": VERIFY_PROMPT.format(description=describe(candidate))},
        ]}]
        logits = self._next_token_logits(messages)
        return self._torch.softmax(logits[self._yes_no_ids].float(), dim=-1)[0].item()
