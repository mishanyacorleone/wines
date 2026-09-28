"""Точность прогона по ручной разметке.

    PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.score \\
        data/reports/image-top20/results.jsonl data/reports/service-vlm5-top10/results.jsonl

Метрики считаются на двух наборах:
  - «уверенные» — только фото, где разметчик уверен в ответе;
  - «+ не уверен» — плюс фото, где размечено ближайшее вино каталога
    с расхождением (год, версия этикетки).
recall@20 — есть ли верное вино в первых 20 кандидатах: потолок для любого
переранжирования, которое переставляет только уже найденное. Считается,
если прогон сделан с --top-k 20.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from .labels import ABSENT, DEFAULT_LABELS, SURE, UNSURE, Label, load_labels


@dataclass
class Metrics:
    total: int = 0
    top1: int = 0
    top5: int = 0
    top20: int = 0
    reciprocal_rank: float = 0.0
    depth: int = 0

    def row(self) -> str:
        n = self.total or 1
        recall20 = f"{self.top20 / n:6.1%}" if self.depth >= 20 else "     —"
        return (
            f"{self.total:>4} │ {self.top1 / n:6.1%} │ {self.top5 / n:6.1%} │ "
            f"{recall20} │ {self.reciprocal_rank / n:.3f}"
        )


def _rank(slugs: list[str], expected: tuple[str, ...]) -> int | None:
    for rank, slug in enumerate(slugs, start=1):
        if slug in expected:
            return rank
    return None


def score(results_path: Path, labels: dict[str, Label], statuses: set[str]) -> Metrics:
    metrics = Metrics()
    with results_path.open(encoding="utf-8") as file:
        for line in file:
            result = json.loads(line)
            label = labels.get(result["filename"])
            if label is None or label.status not in statuses:
                continue
            slugs = [c["slug"] for c in result.get("results", [])]
            metrics.depth = max(metrics.depth, len(slugs))
            metrics.total += 1
            rank = _rank(slugs, label.slugs)
            if rank is None:
                continue
            metrics.top1 += rank == 1
            metrics.top5 += rank <= 5
            metrics.top20 += rank <= 20
            metrics.reciprocal_rank += 1 / rank
    return metrics


def absent_separation(results_path: Path, labels: dict[str, Label]) -> str:
    """Насколько балл топ-1 отличает «вина нет в каталоге» от верного ответа.

    Если распределения не пересекаются, отказ решается одним порогом.
    """
    correct, absent = [], []
    with results_path.open(encoding="utf-8") as file:
        for line in file:
            result = json.loads(line)
            label = labels.get(result["filename"])
            if label is None or not result.get("results"):
                continue
            top = result["results"][0]
            if label.status == ABSENT:
                absent.append(top["score"])
            elif label.status == SURE and top["slug"] in label.slugs:
                correct.append(top["score"])
    if not correct or not absent:
        return ""
    best = max(
        ((t, sum(s >= t for s in correct) + sum(s < t for s in absent))
         for t in sorted(correct + absent)),
        key=lambda x: x[1],
    )
    return (
        f"  балл топ-1: верные {statistics.median(correct):.3f} (медиана, n={len(correct)}), "
        f"нет в каталоге {statistics.median(absent):.3f} (n={len(absent)}); "
        f"лучший порог {best[0]:.3f} разделяет {best[1]}/{len(correct) + len(absent)}"
    )


def not_found_quality(results_path: Path, labels: dict[str, Label]) -> str:
    """Как работает решение сервиса «вина нет в каталоге» (поле status).

    Три числа: сколько вин вне каталога сервис честно назвал ненайденными;
    сколько верных ответов он зря спрятал за «не найдено»; сколько неверных
    ответов отсеял. Прогоны без поля status (старые) пропускаются.
    """
    counts = {"absent": [0, 0], "correct": [0, 0], "wrong": [0, 0]}
    with results_path.open(encoding="utf-8") as file:
        for line in file:
            result = json.loads(line)
            label = labels.get(result["filename"])
            if label is None or not result.get("status") or not result.get("results"):
                continue
            if label.status == ABSENT:
                kind = "absent"
            elif label.status in (SURE, UNSURE):
                kind = "correct" if result["results"][0]["slug"] in label.slugs else "wrong"
            else:
                continue
            counts[kind][0] += result["status"] == "not_found"
            counts[kind][1] += 1
    if not counts["absent"][1]:
        return ""
    (a, na), (c, nc), (w, nw) = counts["absent"], counts["correct"], counts["wrong"]
    return (
        f"  «не найдено»: вина вне каталога {a}/{na}, ложный отказ у верных ответов {c}/{nc}, "
        f"отсеяно неверных ответов {w}/{nw}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Точность прогонов по ручной разметке")
    parser.add_argument("results", type=Path, nargs="+", help="results.jsonl прогонов")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    args = parser.parse_args(argv)

    labels = load_labels(args.labels)
    header = "   n │  top-1 │  top-5 │ rec@20 │   MRR"
    for results_path in args.results:
        print(f"\n{results_path}")
        print(f"  {'набор':<14}{header}")
        for name, statuses in (("уверенные", {SURE}), ("+ не уверен", {SURE, UNSURE})):
            print(f"  {name:<14}{score(results_path, labels, statuses).row()}")
        for extra in (absent_separation(results_path, labels), not_found_quality(results_path, labels)):
            if extra:
                print(extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
