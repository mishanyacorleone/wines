"""Офлайн-проверка VLM-реранкера на готовом прогоне, без перезапуска сервиса.

    PYTHONPATH=tools/eval-runner/src:services/search-api/src:libs/wine-embeddings/src \\
    .venv/bin/python -m eval_runner.vlm_offline data/reports/image-top20/results.jsonl \\
        --out data/reports/vlm/results.jsonl

Берёт топ-k каждого фото из results.jsonl, спрашивает VLM и пишет новый
results.jsonl: кандидаты отсортированы по вероятности VLM, у каждого поле
vlm_prob, у фото — vlm_none_prob и vlm_ms. С --verify — ещё verify_prob
(попарная проверка топ-1, как в сервисе). Точность — обычным eval_runner.score.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from PIL import Image
from tqdm import tqdm

from wine_embeddings.runtime import limit_vram, vram_used_gb

from search_api.vlm import VlmReranker

REPO = Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VLM-реранкер на готовом прогоне")
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--photos", type=Path, default=REPO / "data/real-photos")
    parser.add_argument("--catalog", type=Path, default=REPO / "data/catalog")
    parser.add_argument("--model", type=Path, default=REPO / "models/Qwen3-VL-4B-Instruct")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--query-max-pixels", type=int, default=1024 * 1024)
    parser.add_argument("--candidate-max-pixels", type=int, default=256 * 1024)
    parser.add_argument("--candidate-images", action="store_true",
                        help="показывать VLM эталонные фото кандидатов, не только текст")
    parser.add_argument("--verify", action="store_true",
                        help="проверить выбранный топ-1 попарно (VlmReranker.verify)")
    parser.add_argument("--vram-gb", type=float, default=14.0)
    parser.add_argument("--limit", type=int, default=0, help="только первые N фото")
    args = parser.parse_args(argv)

    limit_vram(args.vram_gb, "cuda")
    reranker = VlmReranker(
        args.model,
        device="cuda",
        query_max_pixels=args.query_max_pixels,
        candidate_max_pixels=args.candidate_max_pixels,
        with_candidate_images=args.candidate_images,
        catalog_dir=args.catalog,
    )

    rows = [json.loads(line) for line in args.results.open(encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]
    args.out.parent.mkdir(parents=True, exist_ok=True)

    timings = []
    with args.out.open("w", encoding="utf-8") as out:
        for row in tqdm(rows, unit="фото"):
            candidates = row.get("results", [])[: args.top_k]
            if candidates:
                with Image.open(args.photos / row["filename"]) as image:
                    image.load()
                    started = time.perf_counter()
                    verdict = reranker.judge(image, candidates)
                    for candidate, prob in zip(candidates, verdict.probs):
                        candidate["vlm_prob"] = prob
                    # sorted стабилен: при равных вероятностях остаётся прежний порядок
                    candidates = sorted(candidates, key=lambda c: c["vlm_prob"], reverse=True)
                    if args.verify:
                        row["verify_prob"] = reranker.verify(image, candidates[0])
                    elapsed = (time.perf_counter() - started) * 1000
                timings.append(elapsed)
                row["results"] = candidates
                for rank, candidate in enumerate(row["results"], start=1):
                    candidate["rank"] = rank
                row["vlm_none_prob"] = verdict.none_prob
                row["vlm_ms"] = round(elapsed, 1)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    if timings:
        ordered = sorted(timings)
        print(f"VLM: медиана {statistics.median(ordered):.0f} мс, "
              f"p95 {ordered[min(int(0.95 * len(ordered)), len(ordered) - 1)]:.0f} мс, "
              f"пик VRAM {vram_used_gb():.2f} ГБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
