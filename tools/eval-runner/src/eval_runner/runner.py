"""Последовательный прогон папки с фото через /v1/search.

Последовательно и без ретраев — специально так же, как это делает скрипт
оценки кейсодержателя: иначе замеры времени отклика не будут сопоставимы
с тем, что увидит проверяющий.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from tqdm import tqdm

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}


@dataclass
class QueryResult:
    filename: str
    results: list[dict[str, Any]] = field(default_factory=list)
    top1_confidence: float = 0.0
    top5_confidence: float = 0.0
    margin: float = 0.0
    # время, замеренное клиентом: включает сеть и сериализацию,
    # то есть то же, что измеряет скрипт организатора
    latency_ms: float = 0.0
    server_timings: dict[str, float] = field(default_factory=dict)
    ocr_text: list[str] | None = None
    vlm_none_prob: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "latency_ms": round(self.latency_ms, 1),
            "top1_confidence": self.top1_confidence,
            "top5_confidence": self.top5_confidence,
            "margin": self.margin,
            "server_timings": self.server_timings,
            "ocr_text": self.ocr_text,
            "vlm_none_prob": self.vlm_none_prob,
            "error": self.error,
            "results": self.results,
        }


@dataclass
class RunSummary:
    total: int = 0
    failed: int = 0
    wall_seconds: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)

    def percentile(self, q: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(int(q / 100 * len(ordered)), len(ordered) - 1)
        return ordered[index]

    @property
    def mean_ms(self) -> float:
        return statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0


def list_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith("._")
    )


def run_queries(
    images_dir: Path, endpoint: str, top_k: int, timeout: float = 30.0
) -> tuple[list[QueryResult], RunSummary]:
    images = list_images(images_dir)
    summary = RunSummary(total=len(images))
    results: list[QueryResult] = []

    wall_start = time.perf_counter()
    with httpx.Client(timeout=timeout) as client:
        for path in tqdm(images, desc="Прогон фото"):
            result = QueryResult(filename=path.name)
            started = time.perf_counter()
            try:
                with path.open("rb") as file:
                    response = client.post(
                        endpoint,
                        files={"image": (path.name, file, "application/octet-stream")},
                        params={"top_k": top_k},
                    )
                result.latency_ms = (time.perf_counter() - started) * 1000
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                result.latency_ms = (time.perf_counter() - started) * 1000
                result.error = f"{type(exc).__name__}: {exc}"
                summary.failed += 1
            else:
                result.results = payload.get("results", [])
                result.top1_confidence = payload.get("top1_confidence", 0.0)
                result.top5_confidence = payload.get("top5_confidence", 0.0)
                result.margin = payload.get("margin", 0.0)
                result.server_timings = payload.get("timings", {})
                result.ocr_text = payload.get("ocr_text")
                result.vlm_none_prob = payload.get("vlm_none_prob")
                summary.latencies_ms.append(result.latency_ms)

            results.append(result)

    summary.wall_seconds = time.perf_counter() - wall_start
    return results, summary


def write_jsonl(results: list[QueryResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for result in results:
            file.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
