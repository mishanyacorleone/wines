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

Модель работает в отдельном сервисе vLLM (compose-сервис `vllm`): здесь — только
промпты, картинки и HTTP. Упал vLLM — `VlmUnavailable`, и сервис отвечает по одной
картинке (search.py), а не ошибкой.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from wine_logging import trace_headers

from .remote import Breaker, ServiceUnavailable, describe_error

logger = logging.getLogger("vlm")

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




class VlmUnavailable(ServiceUnavailable):
    pass


class PromptBuilder:
    """Промпты и картинки для VLM — без модели и сети.

    Отдельно от клиента, чтобы стенд сравнения моделей (eval_runner.model_bench)
    задавал моделям ровно те же вопросы с теми же картинками, что и сервис.
    """

    def __init__(
        self,
        *,
        query_max_pixels: int,
        candidate_max_pixels: int,
        with_candidate_images: bool = True,
        catalog_dir: Path | None = None,
    ) -> None:
        self.query_max_pixels = query_max_pixels
        self.candidate_max_pixels = candidate_max_pixels
        self.with_candidate_images = with_candidate_images and catalog_dir is not None
        self.catalog_dir = catalog_dir

    def fit_query(self, image: Image.Image) -> Image.Image:
        return _fit(image, self.query_max_pixels)

    def messages(self, image: Image.Image, candidates: list[dict[str, Any]]) -> list[dict]:
        """Выбор среди кандидатов. image — фото покупателя (уменьшается здесь же)."""
        content: list[dict[str, Any]] = [
            {"type": "image", "image": self.fit_query(image), "query": True},
            {"type": "text", "text": PROMPT_HEAD + "\n\n"},
        ]
        for label, candidate in zip(LABELS, candidates):
            content.append({"type": "text", "text": f"{label}. {describe(candidate)}\n"})
            reference = self.reference(candidate)
            if reference is not None:
                content.append(self.reference_part(candidate, reference))
        content.append({"type": "text", "text": PROMPT_TAIL})
        return [{"role": "user", "content": content}]

    def verify_messages(self, image: Image.Image, candidate: dict[str, Any]) -> list[dict] | None:
        """Попарная проверка; None — у кандидата нет эталонного фото.

        Без эталона сравнивать не с чем: вопрос про «одну и ту же этикетку»
        по одному тексту карточки модель решает заметно хуже.
        """
        reference = self.reference(candidate, force=True)
        if reference is None:
            return None
        return [{"role": "user", "content": [
            {"type": "image", "image": self.fit_query(image), "query": True},
            self.reference_part(candidate, reference),
            {"type": "text", "text": VERIFY_PROMPT.format(description=describe(candidate))},
        ]}]

    @staticmethod
    def reference_part(candidate: dict[str, Any], reference: Image.Image) -> dict[str, Any]:
        # source — путь эталона в каталоге: vLLM может прочесть кэш сам, без base64
        return {"type": "image", "image": reference, "source": candidate["image_path"]}

    def reference(self, candidate: dict[str, Any], *, force: bool = False) -> Image.Image | None:
        """Эталонное фото кандидата; force — даже если в выборе карточек они выключены."""
        if not (self.with_candidate_images or force) or self.catalog_dir is None:
            return None
        if not candidate.get("image_path"):
            return None
        return self.load_reference(candidate["image_path"])

    @property
    def cache_dir(self) -> Path:
        # Лимит пикселей в имени папки: сменили лимит — кэш строится заново
        return self.catalog_dir / f"vlm-refs-{self.candidate_max_pixels}"

    def load_reference(self, image_path: str) -> Image.Image | None:
        """Уменьшенный эталон из кэша на диске, при промахе — из оригинала.

        Оригиналы каталога — PNG по 1–2,5 МБ: декодировать десяток на запрос
        стоило до 2,5 с при SLA 3 с. Уменьшенная копия читается за миллисекунды.
        Кэш — тоже PNG, без потерь: VLM видит те же пиксели, что и без кэша.
        """
        cached = self.cache_dir / image_path
        if cached.exists():
            with Image.open(cached) as image:
                image.load()
                return image
        path = self.catalog_dir / image_path
        if not path.exists():
            return None
        with Image.open(path) as image:
            fitted = _fit(image, self.candidate_max_pixels)
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

        Запускается в фоне при старте сервиса и после добавления вин: запросы,
        пришедшие раньше, просто читают оригинал сами, как без кэша.
        """
        if self.catalog_dir is None:
            return
        missing = [p for p in image_paths if not (self.cache_dir / p).exists()]
        if not missing:
            return
        started = time.perf_counter()
        # Декодирование PNG в Pillow отпускает GIL — потоков достаточно
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self.load_reference, missing))
        logger.info("Кэш эталонов VLM: %d фото за %.0f с → %s",
                    len(missing), time.perf_counter() - started, self.cache_dir)


class VlmClient:
    """Qwen3-VL в сервисе vLLM: выбор среди кандидатов и попарная проверка.

    Ответ — один токен (max_tokens=1) из allowed_token_ids; vLLM запущен с
    --logprobs-mode=processed_logprobs, поэтому logprobs — уже после ограничения,
    то есть softmax только по меткам. На 100 размеченных фото — 96,2%, «нет в
    каталоге» 30 из 33 (docs/baseline-results.md, «vLLM»).

    Картинки уменьшает сервис (_fit) — vLLM видит те же пиксели. Фото покупателя
    уходит PNG в base64 (кодируется один раз на запрос, `prepare`); эталон — путём
    к уменьшенной копии в кэше (`file://`), если vLLM видит каталог (`media_dir`,
    --allowed-local-media-path): кодирование шести PNG на запрос стоило ~0,5 с.
    Id меток берутся у самого vLLM (/tokenize) — токенизатор в api не нужен.
    """

    backend = "vllm"

    def __init__(
        self,
        url: str,
        prompts: PromptBuilder,
        *,
        model: str,
        media_dir: str,
        timeout_s: float,
        connect_timeout_s: float,
        cooldown_s: float,
    ) -> None:
        self.url = url.rstrip("/")
        self.prompts = prompts
        self._model = model
        self._media_dir = media_dir.rstrip("/")
        # trust_env=False: исходящий трафик машины идёт через прокси, а vLLM — локальный
        self._http = httpx.AsyncClient(
            base_url=self.url,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            trust_env=False,
            limits=httpx.Limits(max_connections=64),
        )
        self.breaker = Breaker("vLLM", cooldown_s)
        # [0] — «ни одна не подходит», дальше — карточки по порядку; «Да», «Нет»
        self._answer_ids: list[int] | None = None
        self._yes_no_ids: list[int] | None = None

    async def _token_ids(self) -> tuple[list[int], list[int]]:
        if self._answer_ids is None or self._yes_no_ids is None:
            answer = [await self._tokenize(label) for label in NONE_LABEL + LABELS]
            if any(len(ids) != 1 for ids in answer):
                raise RuntimeError(f"метки VLM не по одному токену: {answer}")
            # «Да» и «Нет» — по два токена; первые у них разные, и их хватает, чтобы сравнить ответы
            yes_no = [(await self._tokenize(word))[0] for word in ("Да", "Нет")]
            if yes_no[0] == yes_no[1]:
                raise RuntimeError("«Да» и «Нет» начинаются с одного токена")
            self._answer_ids, self._yes_no_ids = [ids[0] for ids in answer], yes_no
        return self._answer_ids, self._yes_no_ids

    async def _tokenize(self, text: str) -> list[int]:
        response = await self._http.post("/tokenize", json={
            "model": self._model, "prompt": text, "add_special_tokens": False,
        })
        response.raise_for_status()
        return response.json()["tokens"]

    async def judge(self, query: "PreparedQuery", candidates: list[dict[str, Any]]) -> VlmVerdict:
        if not candidates:
            return VlmVerdict([], 1.0)
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError(f"VLM выбирает максимум из {MAX_CANDIDATES} кандидатов")
        messages = await asyncio.to_thread(self.prompts.messages, query.image, candidates)
        # Softmax только по допустимым ответам: «0» и метки переданных кандидатов
        probs = await self._label_probs(messages, query, lambda ids: ids[0][: len(candidates) + 1])
        return VlmVerdict(probs=probs[1:], none_prob=probs[0])

    async def verify(self, query: "PreparedQuery", candidate: dict[str, Any]) -> float | None:
        """Вероятность, что на фото именно это вино; None — нет эталонного фото."""
        messages = await asyncio.to_thread(self.prompts.verify_messages, query.image, candidate)
        if messages is None:
            return None
        return (await self._label_probs(messages, query, lambda ids: ids[1]))[0]

    async def prepare(self, image: Image.Image) -> "PreparedQuery":
        """Фото покупателя для обоих проходов: уменьшенное и закодированное один раз."""
        def encode() -> PreparedQuery:
            # уменьшенное фото: в промптах _fit его уже не меняет (площадь в пределах лимита)
            fitted = self.prompts.fit_query(image)
            return PreparedQuery(image=fitted, data_url=_data_url(fitted))
        return await asyncio.to_thread(encode)

    def _image_url(self, part: dict[str, Any], query: "PreparedQuery") -> str:
        if part.get("query"):
            return query.data_url
        source = part.get("source")
        if self._media_dir and source and (self.prompts.cache_dir / source).exists():
            return f"file://{self._media_dir}/{self.prompts.cache_dir.name}/{source}"
        return _data_url(part["image"])

    async def _label_probs(self, messages: list[dict], query: "PreparedQuery", pick) -> list[float]:
        """Вероятности меток в следующей позиции, нормированные между собой."""
        self.breaker.check(VlmUnavailable)
        try:
            token_ids = pick(await self._token_ids())
            content = await asyncio.to_thread(lambda: [
                {"type": "image_url", "image_url": {"url": self._image_url(part, query)}}
                if part["type"] == "image" else {"type": "text", "text": part["text"]}
                for part in messages[0]["content"]
            ])
            response = await self._http.post("/v1/chat/completions", headers=trace_headers(), json={
                "model": self._model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 1,
                "temperature": 0,
                "logprobs": True,
                "top_logprobs": len(token_ids),
                "allowed_token_ids": token_ids,
                # токены — как «token_id:24110»: строка токена не годится, «Да» = «Д» + «а»
                "return_tokens_as_token_ids": True,
            })
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.breaker.failure(describe_error(exc))
            raise VlmUnavailable("vLLM", describe_error(exc)) from exc
        self.breaker.success()
        top = response.json()["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        by_id = {int(t["token"].rsplit(":", 1)[1]): math.exp(t["logprob"]) for t in top}
        probs = [by_id.get(i, 0.0) for i in token_ids]
        total = sum(probs) or 1.0
        return [p / total for p in probs]

    async def health(self) -> bool:
        try:
            response = await self._http.get("/health", timeout=2)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def close(self) -> None:
        await self._http.aclose()


@dataclass(frozen=True)
class PreparedQuery:
    image: Image.Image
    data_url: str


def _data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    # без потерь, но слабое сжатие: с уровнем по умолчанию (6) — в разы дольше
    image.save(buffer, format="PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
