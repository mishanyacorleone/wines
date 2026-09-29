"""Демо-ответы API для вёрстки сканера без GPU и поднятого сервиса.

    python tools/web-demo/build_demo.py

Пишет в services/search-api/src/search_api/web/demo/ ответы в формате
/v1/scan, /v1/wines/{slug}, /v1/pairing — собранные тем же сомелье и тем же
правилом решения, что и в сервисе, по catalog.jsonl и реальному прогону
(data/reports/service-vlm5-top10/results.jsonl). Страница: /app/?demo=match или ?demo=not_found.

Зависимостей, кроме стандартной библиотеки, нет: модули сомелье и решения
не тянут FastAPI и torch.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "services" / "search-api" / "src" / "search_api"
OUT = SRC / "web" / "demo"

# Вино для экрана «нашли» и фото вне каталога для экрана «не найдено»
MATCH_SLUG = "gevyurcztraminer-oranzh"
NOT_FOUND_PHOTO = "88.2_05-09-2026_16-29-06.webp"

SUMMARY_FIELDS = (
    "slug", "url", "title", "manufacturer", "category", "region", "grapes",
    "alcohol", "public_rating",
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_media = None  # SiteMedia, задаётся в main()


def _summary(row: dict, **extra) -> dict:
    data = {k: row.get(k) for k in SUMMARY_FIELDS}
    data["url"] = data["url"] or f"https://vino-svoe.ru/wines/{row['slug']}"
    data["grapes"] = data["grapes"] or []
    image = _media.image_url(row.get("image_url"), 1160)
    return {**data, "image_url": image, "rank": None, "confidence": None, "reasons": [], **extra}


def _card(sommelier, row: dict) -> dict:
    similar = [_summary(w, reasons=w["reasons"]) for w in sommelier.similar(row, limit=8)]
    return {
        **_summary(row),
        "color": row.get("color"),
        "temperature": row.get("temperature"),
        "dishes": row.get("dishes") or [],
        "description": row.get("description"),
        **sommelier.card_media(row),
        "sommelier": sommelier.advise(row),
        "similar": similar,
    }


def main() -> int:
    sommelier_mod = _load("sommelier")
    verdict_mod = _load("verdict")

    catalog = ROOT / "data" / "catalog" / "catalog.jsonl"
    rows = [json.loads(l) for l in catalog.read_text(encoding="utf-8").splitlines() if l.strip()]
    for row in rows:
        row["url"] = f"https://vino-svoe.ru/wines/{row['slug']}"
    global _media
    _media = sommelier_mod.SiteMedia.load()
    sommelier = sommelier_mod.Sommelier(rows, _media)
    OUT.mkdir(parents=True, exist_ok=True)
    timings = {"decode_ms": 200.0, "encode_ms": 280.0, "search_ms": 6.0,
               "vlm_ms": 990.0, "total_ms": 1476.0}

    def write(name: str, data) -> None:
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # «Нашли»: карточка + остальные вина той же выдачи как «Не то вино?»
    match = sommelier.get(MATCH_SLUG)
    card = {**_card(sommelier, match), "rank": 1, "confidence": 0.93}
    alternatives = [_summary(w, rank=i + 2) for i, w in enumerate(sommelier.similar(match, limit=4))]
    write("scan-match.json", {
        "status": "match", "top1_confident": True, "reason": "confident",
        "message": "Нашли ваше вино", "top1": MATCH_SLUG, "wine": card,
        "alternatives": alternatives, "timings": timings,
    })
    write("wine.json", card)

    # «Не найдено»: реальная выдача сервиса по фото вина, которого нет в каталоге
    results = ROOT / "data" / "reports" / "service-vlm5-top10" / "results.jsonl"
    result = next(
        json.loads(l) for l in results.read_text(encoding="utf-8").splitlines()
        if json.loads(l)["filename"] == NOT_FOUND_PHOTO
    )
    verdict = verdict_mod.decide(
        result["results"], vlm_none_prob=result.get("vlm_none_prob"),
        verify_prob=result.get("verify_prob"), thresholds=verdict_mod.Thresholds(),
    )
    shortlist = [
        _summary(sommelier.get(c["slug"]) or c, rank=c["rank"], confidence=round(c["confidence"], 4))
        for c in result["results"]
    ]
    write("scan-not_found.json", {
        "status": verdict.status, "top1_confident": verdict.top1_confident, "reason": verdict.reason,
        "message": verdict.message, "top1": None if verdict.absent else result["results"][0]["slug"],
        "wine": None, "alternatives": shortlist, "timings": timings,
    })
    for item in shortlist + alternatives + card["similar"]:
        write(f"wine-{item['slug']}.json", _card(sommelier, sommelier.get(item["slug"])))

    write("dishes.json", [
        {"dish": d, "icon": sommelier_mod.dish_icon(d), "image": _media.dish(d), "wines": n}
        for d, n in sommelier.dish_names()
    ])
    # Для поиска в шапке: в демо фильтруется в браузере, в сервисе — /v1/catalog/search
    catalog_rows = [
        {k: v for k, v in _summary(r).items() if v not in (None, [], "")} for r in rows
    ]
    (OUT / "catalog.json").write_text(
        json.dumps(catalog_rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    write("pairing.json", {
        "dish": "Сыры", "wines": [_summary(w) for w in sommelier.for_dish("Сыры", limit=10)],
    })
    print(f"Демо-ответы: {OUT} (решение по фото вне каталога: {verdict.status}/{verdict.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
