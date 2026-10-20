"""Нагрузочный замер: сколько сканов в секунду выдерживает сервис (RPS).

Одни и те же фото отправляются с разным числом одновременных пользователей
(1, 2, 4, 8…): каждый «пользователь» берёт следующее фото из общей очереди,
как только получил ответ на предыдущее. На каждом уровне — пропускная
способность (успешных ответов в секунду), время ответа (p50 / p95 / max),
ошибки и средние серверные тайминги. Ответы сверяются с уровнем 1: под
нагрузкой распознавание не должно меняться.

    .venv/bin/python -m eval_runner.load_test --label before
    .venv/bin/python -m eval_runner.load_test --levels 1 4 --endpoint http://127.0.0.1:8080/v1/eval/predict

Итог — data/reports/load-<label>/summary.json и таблица в консоли.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

from .runner import list_images

REPO_ROOT = Path(__file__).resolve().parents[4]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(q / 100 * len(ordered)), len(ordered) - 1)]


def _answer(payload: dict[str, Any]) -> str | None:
    """Что сервис распознал: slug топ-1 (/v1/scan, /v1/search, /v1/eval/predict)."""
    if "slug" in payload:
        return payload["slug"]
    if payload.get("top1"):
        return payload["top1"]
    results = payload.get("results") or payload.get("alternatives") or []
    wine = payload.get("wine")
    return (wine or {}).get("slug") or (results[0]["slug"] if results else None)


async def _run_level(
    client: httpx.AsyncClient, endpoint: str, photos: list[tuple[str, bytes]], users: int
) -> dict[str, Any]:
    queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
    for item in photos:
        queue.put_nowait(item)
    latencies: list[float] = []
    errors: list[str] = []
    answers: dict[str, str | None] = {}
    timings: dict[str, list[float]] = {}

    async def user() -> None:
        while True:
            try:
                name, raw = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = time.perf_counter()
            try:
                response = await client.post(endpoint, files={"image": (name, raw)})
                elapsed = (time.perf_counter() - started) * 1000
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 — ошибка под нагрузкой и есть результат
                errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            latencies.append(elapsed)
            answers[name] = _answer(payload)
            for key, value in (payload.get("timings") or {}).items():
                timings.setdefault(key, []).append(value)

    wall_started = time.perf_counter()
    await asyncio.gather(*(user() for _ in range(users)))
    wall = time.perf_counter() - wall_started
    return {
        "users": users,
        "requests": len(photos),
        "ok": len(latencies),
        "errors": len(errors),
        "error_samples": errors[:5],
        "wall_s": round(wall, 2),
        "rps": round(len(latencies) / wall, 3) if wall else 0.0,
        "p50_ms": round(_percentile(latencies, 50)),
        "p95_ms": round(_percentile(latencies, 95)),
        "max_ms": round(max(latencies, default=0.0)),
        "mean_ms": round(statistics.fmean(latencies)) if latencies else 0,
        "server_mean_ms": {k: round(statistics.fmean(v)) for k, v in sorted(timings.items())},
        "answers": answers,
    }


async def _main(args: argparse.Namespace) -> list[dict[str, Any]]:
    images = list_images(args.images_dir)[: args.limit]
    photos = [(p.name, p.read_bytes()) for p in images]
    # trust_env=False: исходящий трафик машины идёт через прокси, а сервис — локальный
    async with httpx.AsyncClient(
        timeout=args.timeout, trust_env=False, limits=httpx.Limits(max_connections=max(args.levels))
    ) as client:
        # прогрев: первый запрос после простоя медленнее и в замер не идёт
        await client.post(args.endpoint, files={"image": photos[0]})
        levels = []
        for users in args.levels:
            print(f"  {users} польз. …", flush=True)
            levels.append(await _run_level(client, args.endpoint, photos, users))
    return levels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_test", description=__doc__.split("\n\n")[0])
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/scan")
    parser.add_argument("--images-dir", type=Path, default=REPO_ROOT / "data" / "real-photos")
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 2, 4, 8],
                        help="сколько пользователей одновременно")
    parser.add_argument("--limit", type=int, default=100, help="сколько фото на уровень")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--label", default="run", help="имя прогона: data/reports/load-<label>/")
    args = parser.parse_args(argv)

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    levels = asyncio.run(_main(args))

    reference = levels[0]["answers"]
    for level in levels:
        level["differs_from_first"] = sorted(
            name for name, slug in level["answers"].items()
            if name in reference and reference[name] != slug
        )

    out_dir = REPO_ROOT / "data" / "reports" / f"load-{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"started": started, "endpoint": args.endpoint, "photos": args.limit, "levels": levels}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'польз.':>6} {'RPS':>6} {'p50, мс':>8} {'p95, мс':>8} {'max, мс':>8} {'ошибок':>7} "
          f"{'ответ иной':>10}  сервер, мс (среднее)")
    for level in levels:
        server = ", ".join(f"{k.removesuffix('_ms')} {v}" for k, v in level["server_mean_ms"].items())
        print(f"{level['users']:>6} {level['rps']:>6.2f} {level['p50_ms']:>8} {level['p95_ms']:>8} "
              f"{level['max_ms']:>8} {level['errors']:>7} {len(level['differs_from_first']):>10}  {server}")
    print(f"\n→ {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
