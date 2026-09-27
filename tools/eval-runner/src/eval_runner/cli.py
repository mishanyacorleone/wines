"""CLI прогона реальных фото."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .report import build_report
from .runner import run_queries, write_jsonl

REPO_ROOT = Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval-runner",
        description="Прогоняет папку с фото через сервис и строит HTML-отчёт.",
    )
    parser.add_argument(
        "--images-dir", type=Path, default=REPO_ROOT / "data" / "real-photos",
        help="папка с query-фотографиями",
    )
    parser.add_argument(
        "--catalog-dir", type=Path, default=REPO_ROOT / "data" / "catalog",
        help="папка каталога (для превью кандидатов)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "data" / "reports",
        help="куда положить отчёт и результаты",
    )
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:8080/v1/search",
        help="ручка поиска",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="только первые N фото")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    results, summary = run_queries(args.images_dir, args.endpoint, args.top_k)
    if args.limit:
        results = results[: args.limit]

    jsonl_path = args.output_dir / "results.jsonl"
    write_jsonl(results, jsonl_path)

    report_path = build_report(
        results, summary,
        queries_dir=args.images_dir,
        catalog_dir=args.catalog_dir,
        output_path=args.output_dir / "report.html",
    )

    print(
        f"\nФото: {summary.total}, ошибок: {summary.failed}\n"
        f"Общее время прогона: {summary.wall_seconds:.1f} с\n"
        f"Время отклика: среднее {summary.mean_ms:.0f} мс, "
        f"p50 {summary.percentile(50):.0f} мс, "
        f"p95 {summary.percentile(95):.0f} мс, "
        f"max {max(summary.latencies_ms or [0]):.0f} мс\n"
        f"Результаты: {jsonl_path}\n"
        f"Отчёт: {report_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
