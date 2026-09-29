"""Картинки справочников сайта: блюда, регионы, сорта винограда.

    python tools/site-media/fetch_site_media.py

На карточке вина vino-svoe.ru у каждого блюда, региона и сорта есть своё фото
(круглые картинки блюд, миниатюры региона и сорта, фон с виноградом). Парсер
каталога их не сохраняет — он собирает только сами вина. Этот скрипт
открывает минимальный набор страниц вин, покрывающий все блюда, регионы и
сорта из catalog.jsonl, и складывает пути картинок в
services/search-api/src/search_api/site_media.json.

Только стандартная библиотека. Между запросами пауза — robots.txt разрешает
обход /wines/*, но сайт не наш.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "data" / "catalog" / "catalog.jsonl"
OUT = ROOT / "services" / "search-api" / "src" / "search_api" / "site_media.json"
SITE = "https://vino-svoe.ru"
DELAY_S = 0.7
NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _cover(rows: list[dict]) -> tuple[list[str], set[str]]:
    """Жадное покрытие: как можно меньше страниц на все значения справочников."""
    def values(row: dict) -> set[str]:
        return set(row.get("dishes") or []) | set(row.get("grapes") or []) | {row.get("region") or ""}

    need = set().union(*(values(r) for r in rows)) - {""}
    chosen = []
    while need:
        best = max(rows, key=lambda r: len(values(r) & need))
        got = values(best) & need
        if not got:
            break
        chosen.append(best["slug"])
        need -= got
    return chosen, need


def _refs(html: str) -> dict[str, dict]:
    """Объекты {name, image[, backgroundImage]} из __NUXT_DATA__.

    Nuxt сериализует состояние плоским массивом со ссылками по индексам —
    разворачиваем только то, что нужно.
    """
    match = NUXT_RE.search(html)
    if not match:
        return {}
    arr = json.loads(match.group(1))

    def resolve(value, depth=0):
        if not isinstance(value, int) or isinstance(value, bool) or depth > 6:
            return value
        item = arr[value]
        if isinstance(item, dict):
            return {k: resolve(v, depth + 1) for k, v in item.items()}
        if isinstance(item, list):
            return [resolve(v, depth + 1) for v in item]
        return item

    found = {}
    for idx, item in enumerate(arr):
        if isinstance(item, dict) and "name" in item and ("image" in item or "backgroundImage" in item):
            obj = resolve(idx)
            if isinstance(obj.get("name"), str):
                image = obj.get("image") if isinstance(obj.get("image"), dict) else {}
                bg = obj.get("backgroundImage") if isinstance(obj.get("backgroundImage"), dict) else {}
                found[obj["name"]] = {"image": image.get("url"), "background": bg.get("url")}
    return found


def main() -> int:
    rows = [json.loads(l) for l in CATALOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    slugs, _ = _cover(rows)
    print(f"Страниц для обхода: {len(slugs)}")

    media: dict[str, dict] = {}
    for n, slug in enumerate(slugs, 1):
        request = urllib.request.Request(f"{SITE}/wines/{slug}", headers={"User-Agent": "vino-scanner-media/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                media.update(_refs(response.read().decode("utf-8")))
        except Exception as exc:  # одна страница не должна ронять сбор
            print(f"  {slug}: {exc}", file=sys.stderr)
        print(f"\r  {n}/{len(slugs)}", end="", flush=True)
        time.sleep(DELAY_S)
    print()

    dishes = {d for r in rows for d in r.get("dishes") or []}
    regions = {r["region"] for r in rows if r.get("region")}
    grapes = {g for r in rows for g in r.get("grapes") or []}
    result = {
        "_source": f"{SITE}, __NUXT_DATA__ карточек вин; пути относительно ресайз-API сайта",
        "dishes": {k: media[k]["image"] for k in sorted(dishes) if media.get(k, {}).get("image")},
        "regions": {k: media[k]["image"] for k in sorted(regions) if media.get(k, {}).get("image")},
        "grapes": {
            k: {"image": media[k]["image"], "background": media[k]["background"]}
            for k in sorted(grapes) if media.get(k, {}).get("image")
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"Блюда {len(result['dishes'])}/{len(dishes)}, регионы {len(result['regions'])}/{len(regions)}, "
        f"сорта {len(result['grapes'])}/{len(grapes)} → {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
