"""Сравнение моделей на месте VLM: выбор среди топ-5 + проверка «нет в каталоге».

    PYTHONPATH=tools/eval-runner/src:services/search-api/src:libs/wine-logging/src:libs/wine-embeddings/src \\
    .venv/bin/python -m eval_runner.model_bench qwen-vl models/Qwen3-VL-2B-Instruct \\
        --out data/reports/bench-qwen3-vl-2b/results.jsonl

Кандидаты — топ-5 векторного поиска из готового прогона «только картинка»
(data/reports/image-top20/results.jsonl), то есть у всех моделей одни и те же
пять вин на фото. Каждая модель делает то же, что VLM в сервисе:
  1) выбирает вино среди пяти — вероятность каждого (vlm_prob);
  2) проверяет выбранное попарно с эталонным фото — вероятность «то же вино»
     (verify_prob); ниже порога → status = not_found.

Бэкенды:
  qwen-vl   — чат-модели Qwen (Qwen3-VL, Qwen3.5): промпты и метки из search_api.vlm,
              вероятности — из логитов следующего токена, без генерации;
  chat      — то же для любой чат-VLM transformers (InternVL-HF, Gemma, LFM2-VL, MiniCPM-V-4.7);
  minicpm   — openbmb/MiniCPM-V-4 (remote code), промпт как в его chat();
  qwen-reranker, jina-reranker — кросс-энкодеры (Qwen3-VL-Reranker-2B, jina-reranker-m0):
              балл пары «фото — эталон» для каждого кандидата, выбор — максимум,
              проверка — сигмоида балла выбранного;
  d3        — vllm-sr/d3-nano и LiquidAI/d1-3B (тот же system_one): вопросы типа choice и noul, ответ — вероятности;
  vllm      — то же, что qwen-vl, но модель в vLLM (compose-сервис vllm);
              model — локальная папка весов (нужен только токенизатор для id меток).

Итог в results.jsonl совместим с eval_runner.score; сводка по прогонам —
`--summary` (top-1, «не найдено» при пороге по умолчанию и лучшем пороге, время, VRAM).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image

REPO = Path(__file__).resolve().parents[4]
DEFAULT_RUN = REPO / "data/reports/image-top20/results.jsonl"
TOP_K = 5


# ── Бэкенды ──────────────────────────────────────────────────────────────────

class QwenChat:
    """Qwen3-VL / Qwen3.5 как в сервисе: логиты меток «0»…«5» и «Да»/«Нет»."""

    verify_threshold = 0.3  # verdict.py: min_verify_prob

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        from search_api import vlm

        self.vlm = vlm
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
        # InternVL режет картинку на плитки 448 px (до 12 + миниатюра): шесть картинок не влезают в 14 ГБ
        self.processor_kwargs = json.loads(args.processor_kwargs)
        kwargs: dict[str, Any] = {"dtype": torch.bfloat16, "local_files_only": True}
        if args.dequantize:
            from transformers import FineGrainedFP8Config
            kwargs["quantization_config"] = FineGrainedFP8Config(dequantize=True)
        # Строка или JSON по подмоделям: у Gemma 4 аудио-башня без sdpa —
        # '{"text_config": "sdpa", "vision_config": "sdpa", "audio_config": "eager"}'
        attn = json.loads(args.attn) if args.attn.startswith("{") else args.attn
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_dir, attn_implementation=attn, **kwargs).to("cuda").eval()
        tok = self.processor.tokenizer
        self.answer_ids = [self._one_token(tok, label) for label in vlm.NONE_LABEL + vlm.LABELS]
        self.yes_no_ids = [tok.encode(w, add_special_tokens=False)[0] for w in ("Да", "Нет")]
        # Промпты и кэш эталонов — те же, что у сервиса (search_api.vlm.PromptBuilder)
        self.helper = vlm.PromptBuilder(query_max_pixels=args.query_max_pixels,
                                         candidate_max_pixels=args.candidate_max_pixels, catalog_dir=args.catalog)

    @staticmethod
    def _one_token(tok, label: str) -> int:
        ids = tok.encode(label, add_special_tokens=False)
        if len(ids) != 1:
            raise RuntimeError(f"метка {label!r} не один токен: {ids}")
        return ids[0]

    def _logits(self, messages: list[dict]):
        # служебные поля сервисных сообщений (query, source) процессору не нужны
        messages = [{**m, "content": [{k: v for k, v in p.items() if k in ("type", "image", "text")}
                                      for p in m["content"]]} for m in messages]
        inputs = self.processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True,
            return_tensors="pt",
            # Qwen3.5 по умолчанию «думает» (<think>): тогда следующий токен — не ответ
            enable_thinking=False,
            **self.processor_kwargs,
        ).to("cuda")
        with self.torch.inference_mode():
            return self.model(**inputs, logits_to_keep=1).logits[0, -1]

    def choose(self, image: Image.Image, candidates: list[dict]) -> tuple[list[float], float]:
        logits = self._logits(self.helper.messages(image, candidates))
        allowed = self.answer_ids[: len(candidates) + 1]
        probs = self.torch.softmax(logits[allowed].float(), dim=-1).tolist()
        return probs[1:], probs[0]

    def verify(self, image: Image.Image, candidate: dict) -> float | None:
        reference = self.helper.reference(candidate, force=True)
        if reference is None:
            return None
        messages = [{"role": "user", "content": [
            {"type": "image", "image": self.vlm._fit(image, self.helper.query_max_pixels)},
            {"type": "image", "image": reference},
            {"type": "text", "text": self.vlm.VERIFY_PROMPT.format(description=self.vlm.describe(candidate))},
        ]}]
        logits = self._logits(messages)
        return self.torch.softmax(logits[self.yes_no_ids].float(), dim=-1)[0].item()


class MiniCPMV(QwenChat):
    """openbmb/MiniCPM-V-4 (remote code): те же промпты и метки, вход — как в его model.chat().

    chat() только генерирует, поэтому повторяем его сборку промпта (картинка → «(<image>./</image>)»,
    части сообщения через «\\n») и берём логиты последней позиции языковой модели.
    """

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        import torch
        from transformers import AutoModel, AutoProcessor

        from search_api import vlm

        self.vlm = vlm
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_dir, trust_remote_code=True, local_files_only=True)
        self.model = AutoModel.from_pretrained(
            model_dir, trust_remote_code=True, attn_implementation="sdpa", dtype=torch.bfloat16,
            local_files_only=True).to("cuda").eval()
        tok = self.processor.tokenizer
        self.answer_ids = [self._one_token(tok, label) for label in vlm.NONE_LABEL + vlm.LABELS]
        self.yes_no_ids = [self._pieces(tok, w)[0] for w in ("Да", "Нет")]
        self.helper = vlm.PromptBuilder(query_max_pixels=args.query_max_pixels,
                                         candidate_max_pixels=args.candidate_max_pixels, catalog_dir=args.catalog)

    @staticmethod
    def _pieces(tok, text: str) -> list[int]:
        # SentencePiece (Llama) ставит перед словом отдельный «▁»: «0» → [«▁», «0»]
        ids = tok.encode(text, add_special_tokens=False)
        return ids[1:] if len(ids) > 1 and tok.convert_ids_to_tokens(ids[0]) == "▁" else ids

    @classmethod
    def _one_token(cls, tok, label: str) -> int:
        ids = cls._pieces(tok, label)
        if len(ids) != 1:
            raise RuntimeError(f"метка {label!r} не один токен: {ids}")
        return ids[0]

    def _logits(self, messages: list[dict]):
        images, parts = [], []
        for part in messages[0]["content"]:
            if part["type"] == "image":
                images.append(part["image"])
                parts.append("(<image>./</image>)")
            else:
                parts.append(part["text"])
        prompt = self.processor.tokenizer.apply_chat_template(
            [{"role": "user", "content": "\n".join(parts)}], tokenize=False, add_generation_prompt=True)
        inputs = self.processor([prompt], [images], return_tensors="pt", max_length=32768).to("cuda")
        inputs.pop("image_sizes")
        with self.torch.inference_mode():
            embeds, _ = self.model.get_vllm_embedding(inputs)
            return self.model.llm(inputs_embeds=embeds).logits[0, -1]


class _RerankerBase:
    """Кросс-энкодер «запрос — документ»: запрос — фото покупателя, документ — эталон кандидата.

    Выбор — максимум балла среди пяти (vlm_prob — softmax логитов, только для порядка),
    проверка — вероятность «подходит» у выбранного: второй проход не нужен.
    """

    verify_threshold = 0.5  # своей калибровки «нет в каталоге» у реранкера нет

    INSTRUCTION = ("Find the catalog entry for exactly the same wine as on the shopper's photo: "
                   "same label, label text, producer, grape, wine color and sweetness.")

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        from search_api import vlm

        self.vlm = vlm
        self.helper = vlm.PromptBuilder(query_max_pixels=args.query_max_pixels,
                                         candidate_max_pixels=args.candidate_max_pixels, catalog_dir=args.catalog)
        self._prob: dict[str, float] = {}

    def logits(self, query: Image.Image, candidates: list[dict]) -> list[float]:
        raise NotImplementedError

    def choose(self, image: Image.Image, candidates: list[dict]) -> tuple[list[float], float]:
        logits = self.logits(self.vlm._fit(image, self.helper.query_max_pixels), candidates)
        self._prob = {c["slug"]: 1 / (1 + math.exp(-x)) for c, x in zip(candidates, logits)}
        top = max(logits)
        exp = [math.exp(x - top) for x in logits]
        return [e / sum(exp) for e in exp], 0.0

    def verify(self, image: Image.Image, candidate: dict) -> float | None:
        return self._prob.get(candidate["slug"])


class QwenReranker(_RerankerBase):
    """Qwen/Qwen3-VL-Reranker-2B: логит = (w_yes − w_no) · скрытое состояние последней позиции.

    Формат запроса — как в scripts/qwen3_vl_reranker.py из карточки модели; документ —
    эталонное фото + текст карточки. Пары считаются одним батчем (паддинг слева).
    """

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        super().__init__(model_dir, args)
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True, padding_side="left")
        lm = Qwen3VLForConditionalGeneration.from_pretrained(
            model_dir, dtype=torch.bfloat16, attn_implementation="sdpa", local_files_only=True).to("cuda").eval()
        vocab = self.processor.tokenizer.get_vocab()
        self.direction = (lm.lm_head.weight[vocab["yes"]] - lm.lm_head.weight[vocab["no"]]).detach()
        self.model = lm.model

    def _pair(self, query: Image.Image, candidate: dict) -> list[dict]:
        doc: list[dict] = [{"type": "text", "text": "\n<Document>:"}]
        ref = self.helper.reference(candidate, force=True)
        if ref is not None:
            doc.append({"type": "image", "image": ref})
        doc.append({"type": "text", "text": self.vlm.describe(candidate)})
        return [
            {"role": "system", "content": [{"type": "text", "text": (
                'Judge whether the Document meets the requirements based on the Query and the Instruct provided. '
                'Note that the answer can only be "yes" or "no".')}]},
            {"role": "user", "content": [
                {"type": "text", "text": "<Instruct>: " + self.INSTRUCTION},
                {"type": "text", "text": "<Query>:"}, {"type": "image", "image": query}, *doc]},
        ]

    def logits(self, query: Image.Image, candidates: list[dict]) -> list[float]:
        inputs = self.processor.apply_chat_template(
            [self._pair(query, c) for c in candidates], tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt", padding=True).to("cuda")
        with self.torch.inference_mode():
            hidden = self.model(**inputs).last_hidden_state[:, -1]
        return (hidden @ self.direction).float().tolist()


class JinaReranker(_RerankerBase):
    """jinaai/jina-reranker-m0 (Qwen2-VL-2B): режим «фото-запрос → фото-документ» из карточки.

    Текст карточки в документ не идёт: документ у модели — либо картинка, либо текст.
    compute_score отдаёт сигмоиду — переводим обратно в логит для общего softmax.
    """

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        super().__init__(model_dir, args)
        import torch
        from transformers import AutoModel

        self.model = AutoModel.from_pretrained(
            str(model_dir), dtype=torch.bfloat16, trust_remote_code=True, local_files_only=True).to("cuda").eval()

    def logits(self, query: Image.Image, candidates: list[dict]) -> list[float]:
        refs = [self.helper.reference(c, force=True) for c in candidates]
        blank = Image.new("RGB", (64, 64), "white")
        pairs = [(query, ref if ref is not None else blank) for ref in refs]
        scores = self.model.compute_score(pairs, batch_size=len(pairs), max_length=2048,
                                          query_type="image", doc_type="image")
        eps = 1e-6
        return [math.log(max(s, eps) / max(1 - s, eps)) for s in scores]


class D3:
    """vllm-sr/d3-nano: один вызов — выбор среди пяти (choice) и «то же вино?» для каждого (noul).

    Картинки модель ставит перед текстом, поэтому в тексте сказано, какое фото чьё.
    Все вопросы отвечаются за один проход, проверка выбранного — готовый ответ его noul.
    """

    verify_threshold = 0.3

    STATE = ("Photo 1 is a wine bottle photographed by a shopper in a store. "
             "Photos 2–{last} are reference catalog photos of candidate wines:\n{cards}")

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        from transformers import AutoModel

        from search_api import vlm

        self.vlm = vlm
        self.model = AutoModel.from_pretrained(str(model_dir), trust_remote_code=True, local_files_only=True).cuda()
        self.helper = vlm.PromptBuilder(query_max_pixels=args.query_max_pixels,
                                         candidate_max_pixels=args.candidate_max_pixels, catalog_dir=args.catalog)
        self._same: dict[str, float] = {}

    def choose(self, image: Image.Image, candidates: list[dict]) -> tuple[list[float], float]:
        images = [self.vlm._fit(image, self.helper.query_max_pixels)]
        photo_of: dict[int, int] = {}
        for i, c in enumerate(candidates):
            ref = self.helper.reference(c, force=True)
            if ref is not None:
                images.append(ref)
                photo_of[i] = len(images)
        cards = "\n".join(
            f"Candidate {i + 1}" + (f" (photo {photo_of[i]})" if i in photo_of else " (no photo)")
            + f": {self.vlm.describe(c)}" for i, c in enumerate(candidates))
        questions: dict[str, Any] = {"wine": {
            "type": "choice",
            "instructions": ("Which candidate is exactly the wine on photo 1? Compare the label text: name, "
                             "producer, grape, wine color and sweetness. Wines of one line look alike and "
                             "differ only in these words."),
            "criteria": {**{str(i + 1): self.vlm.describe(c) for i, c in enumerate(candidates)},
                         "0": "None of the candidates — this wine is not in the list"},
        }}
        for i in photo_of:
            questions[f"same{i}"] = {
                "type": "noul",
                "instructions": (f"Are photo 1 and photo {photo_of[i]} the same wine? Compare label and its text, "
                                 "text on the bottle and neck, bottle shape and wine color. Vintage, angle and "
                                 "lighting do not matter."),
            }
        answers = self.model.system_one(
            state=self.STATE.format(last=len(images), cards=cards), questions=questions, images=images)["answers"]
        probs = answers["wine"]["probabilities"]
        self._same = {candidates[i]["slug"]: answers[f"same{i}"]["noul"] for i in photo_of}
        return [probs[str(i + 1)] for i in range(len(candidates))], probs["0"]

    def verify(self, image: Image.Image, candidate: dict) -> float | None:
        return self._same.get(candidate["slug"])


class VllmChat(QwenChat):
    """Те же промпты и метки, что QwenChat, но модель — в vLLM (OpenAI API, сервис compose `vllm`).

    Один токен ответа (max_tokens=1) с allowed_token_ids = метки; vLLM запущен с
    --logprobs-mode=processed_logprobs, поэтому logprobs — уже после ограничения,
    то есть softmax только по меткам, как в QwenChat. Картинки уменьшаются у нас (_fit)
    и уходят PNG без потерь — vLLM видит те же пиксели.
    """

    def __init__(self, model_dir: Path, args: argparse.Namespace) -> None:
        import httpx
        from transformers import AutoTokenizer

        from search_api import vlm

        self.vlm = vlm
        self.url = args.vllm_url.rstrip("/") + "/v1/chat/completions"
        self.http = httpx.Client(timeout=60, trust_env=False)
        tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        self.answer_ids = [self._one_token(tok, label) for label in vlm.NONE_LABEL + vlm.LABELS]
        self.yes_no_ids = [tok.encode(w, add_special_tokens=False)[0] for w in ("Да", "Нет")]
        self.helper = vlm.PromptBuilder(query_max_pixels=args.query_max_pixels,
                                         candidate_max_pixels=args.candidate_max_pixels, catalog_dir=args.catalog)

    @staticmethod
    def _data_url(image: Image.Image) -> str:
        import base64
        import io

        buf = io.BytesIO()
        # без потерь, но слабое сжатие: по умолчанию (6) кодирование шести картинок — ~0,5 с на запрос
        image.save(buf, format="PNG", compress_level=1)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def _ask(self, messages: list[dict], token_ids: list[int]) -> list[float]:
        content = []
        for part in messages[0]["content"]:
            if part["type"] == "image":
                content.append({"type": "image_url", "image_url": {"url": self._data_url(part["image"])}})
            else:
                content.append({"type": "text", "text": part["text"]})
        body = {
            "model": "vlm", "messages": [{"role": "user", "content": content}],
            "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": len(token_ids),
            "allowed_token_ids": token_ids,
            "chat_template_kwargs": {"enable_thinking": False},
            # токены — как «token_id:24110»: строка токена не годится, «Да» = «Д» + «а»
            "return_tokens_as_token_ids": True,
        }
        res = self.http.post(self.url, json=body)
        res.raise_for_status()
        top = res.json()["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        by_id = {int(t["token"].rsplit(":", 1)[1]): math.exp(t["logprob"]) for t in top}
        probs = [by_id.get(i, 0.0) for i in token_ids]
        total = sum(probs) or 1.0
        return [p / total for p in probs]

    def choose(self, image: Image.Image, candidates: list[dict]) -> tuple[list[float], float]:
        ids = self.answer_ids[: len(candidates) + 1]
        probs = self._ask(self.helper.messages(image, candidates), ids)
        return probs[1:], probs[0]

    def verify(self, image: Image.Image, candidate: dict) -> float | None:
        reference = self.helper.reference(candidate, force=True)
        if reference is None:
            return None
        messages = [{"role": "user", "content": [
            {"type": "image", "image": self.vlm._fit(image, self.helper.query_max_pixels)},
            {"type": "image", "image": reference},
            {"type": "text", "text": self.vlm.VERIFY_PROMPT.format(description=self.vlm.describe(candidate))},
        ]}]
        return self._ask(messages, self.yes_no_ids)[0]


BACKENDS = {"qwen-vl": QwenChat, "chat": QwenChat, "minicpm": MiniCPMV, "vllm": VllmChat, "qwen-reranker": QwenReranker, "jina-reranker": JinaReranker, "d3": D3}


# ── Прогон ───────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    # vLLM держит модель в своём контейнере: наш процесс GPU не трогает, а память
    # vLLM задана долей карты (--gpu-memory-utilization), а не замеряется
    local = args.backend != "vllm"
    if local:
        import torch

        from wine_embeddings.runtime import limit_vram

        limit_vram(args.vram_gb, "cuda")
    sync = torch.cuda.synchronize if local else (lambda: None)
    started = time.perf_counter()
    backend = BACKENDS[args.backend](args.model, args)
    load_s = time.perf_counter() - started
    weights_gb = torch.cuda.memory_allocated() / 1024**3 if local else None

    rows = [json.loads(line) for line in args.results.open(encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    timings = []
    # Прогрев: первый проход — холодные ядра CUDA (до 20 с), в замер не идёт
    first = next(r for r in rows if r.get("results"))
    with Image.open(args.photos / first["filename"]) as image:
        image.load()
        warm = [dict(c) for c in first["results"][:TOP_K]]
        backend.choose(image, warm)
        backend.verify(image, warm[0])
    if local:
        torch.cuda.reset_peak_memory_stats()
    with args.out.open("w", encoding="utf-8") as out:
        for i, row in enumerate(rows, 1):
            candidates = row.get("results", [])[:TOP_K]
            if candidates:
                with Image.open(args.photos / row["filename"]) as image:
                    image.load()
                    sync()
                    t0 = time.perf_counter()
                    probs, none_prob = backend.choose(image, candidates)
                    for c, p in zip(candidates, probs):
                        c["vlm_prob"] = p
                    candidates = sorted(candidates, key=lambda c: c["vlm_prob"], reverse=True)
                    verify = backend.verify(image, candidates[0])
                    sync()
                    elapsed = (time.perf_counter() - t0) * 1000
                timings.append(elapsed)
                for rank, c in enumerate(candidates, 1):
                    c["rank"] = rank
                row["results"] = candidates
                row["vlm_none_prob"] = none_prob
                row["verify_prob"] = verify
                row["status"] = ("not_found" if verify is not None and verify < backend.verify_threshold
                                 else "match")
                row["vlm_ms"] = round(elapsed, 1)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            if i % 10 == 0:
                print(f"  {i}/{len(rows)} · медиана {statistics.median(timings):.0f} мс", flush=True)

    ordered = sorted(timings)
    meta = {
        "model": args.model.name, "backend": args.backend, "photos": len(rows),
        "load_s": round(load_s, 1), "weights_gb": round(weights_gb, 2) if local else None,
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 2) if local else None,
        "median_ms": round(statistics.median(ordered)), "p95_ms": round(ordered[min(int(0.95 * len(ordered)), len(ordered) - 1)]),
        "max_ms": round(ordered[-1]), "verify_threshold": backend.verify_threshold,
        "dequantize": bool(args.dequantize),
    }
    (args.out.parent / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


# ── Сводка ───────────────────────────────────────────────────────────────────

def summarize(paths: list[Path]) -> int:
    from .labels import ABSENT, SURE, UNSURE, load_labels, DEFAULT_LABELS

    labels = load_labels(DEFAULT_LABELS)
    header = ("| Модель | top-1 (уверенные) | top-1 (+не уверен) | «нет в каталоге» при пороге | ложные отказы | "
              "лучший порог: вне каталога / ложные | медиана, мс | p95, мс | VRAM пик, ГБ |")
    print(header)
    print("|---" * (header.count("|") - 1) + "|")
    for path in paths:
        rows = [json.loads(line) for line in path.open(encoding="utf-8")]
        meta_path = path.parent / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        sure = [r for r in rows if (lab := labels.get(r["filename"])) and lab.status == SURE]
        loose = [r for r in rows if (lab := labels.get(r["filename"])) and lab.status in (SURE, UNSURE)]
        hit = lambda r: bool(r.get("results")) and r["results"][0]["slug"] in labels[r["filename"]].slugs  # noqa: E731
        absent = [r for r in rows if (lab := labels.get(r["filename"])) and lab.status == ABSENT]
        correct = [r for r in loose if hit(r)]
        caught = sum(r.get("status") == "not_found" for r in absent)
        false_rej = sum(r.get("status") == "not_found" for r in correct)
        # Лучший порог по тем же фото — оптимистичная оценка потолка проверки
        va = [r.get("verify_prob") for r in absent if r.get("verify_prob") is not None]
        vc = [r.get("verify_prob") for r in correct if r.get("verify_prob") is not None]
        best = max(((t, sum(v < t for v in va) - sum(v < t for v in vc)) for t in sorted(set(va + vc)) + [1.01]),
                   key=lambda x: x[1], default=(0, 0))
        b_caught, b_false = sum(v < best[0] for v in va), sum(v < best[0] for v in vc)
        print(f"| {meta.get('model', path.parent.name)} | {sum(map(hit, sure))}/{len(sure)} = {sum(map(hit, sure)) / len(sure):.1%} "
              f"| {sum(map(hit, loose)) / len(loose):.1%} | {caught}/{len(absent)} (порог {meta.get('verify_threshold')}) "
              f"| {false_rej}/{len(correct)} | {b_caught}/{len(va)} / {b_false}/{len(vc)} (порог {best[0]:.2f}) "
              f"| {meta.get('median_ms', '—')} | {meta.get('p95_ms', '—')} | {meta.get('peak_vram_gb', '—')} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сравнение моделей на месте VLM")
    parser.add_argument("backend", nargs="?", choices=sorted(BACKENDS))
    parser.add_argument("model", nargs="?", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--results", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--photos", type=Path, default=REPO / "data/real-photos")
    parser.add_argument("--catalog", type=Path, default=REPO / "data/catalog")
    # как в сервисе (WINE_VLM_QUERY_MAX_PIXELS / CANDIDATE_MAX_PIXELS)
    parser.add_argument("--query-max-pixels", type=int, default=512 * 1024)
    parser.add_argument("--candidate-max-pixels", type=int, default=128 * 1024)
    parser.add_argument("--vram-gb", type=float, default=14.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dequantize", action="store_true", help="FP8-веса → bf16 при загрузке (для GPU без FP8)")
    parser.add_argument("--attn", default="sdpa", help="attn_implementation (строка или JSON по подмоделям)")
    parser.add_argument("--processor-kwargs", default="{}", help='JSON для процессора, напр. \'{"max_patches": 6}\'')
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8100", help="vLLM (бэкенд vllm)")
    parser.add_argument("--summary", type=Path, nargs="+", help="results.jsonl прогонов → таблица")
    args = parser.parse_args(argv)
    if args.summary:
        return summarize(args.summary)
    if not (args.backend and args.model and args.out):
        parser.error("нужны backend, model и --out")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
