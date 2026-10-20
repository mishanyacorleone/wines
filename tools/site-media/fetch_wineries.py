"""Винодельни и винные туры с сайта: для «От бутылки — на винодельню».

    python tools/site-media/fetch_wineries.py
    python tools/site-media/download_site_media.py   # их фото — в data/site-media

У вина в каталоге есть `manufacturer_slug` — это и есть адрес страницы винодельни
на vino-svoe.ru (/wineries/<slug>). Скрипт обходит все винодельни из
wineries-sitemap.xml и туры из их страниц, берёт из __NUXT_DATA__ описание,
место (координаты, адрес), терруар, галерею, людей и туры и складывает в
services/search-api/src/search_api/wineries.json. Точка винодельни сразу
переводится в координаты нашей винной карты (tools/region-map, поле `frame`
в russia-map.json) — страница ставит метку без своей проекции.

Только стандартная библиотека. Между запросами пауза — сайт не наш.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "region-map"))
from build_map import project_point  # noqa: E402

OUT = ROOT / "services" / "search-api" / "src" / "search_api" / "wineries.json"
MAP = ROOT / "services" / "web" / "app" / "russia-map.json"
SITE = "https://vino-svoe.ru"
DELAY_S = 0.7
NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
WRAPPERS = ("Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef")


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "vino-scanner-media/1.0"})
    for attempt in range(3):  # сайт иногда отвечает 502 — пробуем ещё раз
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    return ""


def nuxt(page: str) -> dict:
    """Состояние страницы Nuxt: плоский массив со ссылками по индексам → обычные объекты."""
    arr = json.loads(NUXT_RE.search(page).group(1))

    def resolve(value, depth=0):
        if isinstance(value, bool) or not isinstance(value, int) or depth > 12:
            return value
        item = arr[value]
        if isinstance(item, dict):
            return {k: resolve(v, depth + 1) for k, v in item.items()}
        if isinstance(item, list):
            if len(item) == 2 and item[0] in WRAPPERS:
                return resolve(item[1], depth + 1)
            return [resolve(v, depth + 1) for v in item]
        return item

    return resolve(0)


def text(value, limit: int = 0) -> str | None:
    """HTML редактора сайта → текст абзацами; limit — обрезать по предложению."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = re.sub(r"</p>|<br\s*/?>", "\n", value)
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    value = "\n".join(line.strip() for line in value.splitlines() if line.strip())
    if limit and len(value) > limit:
        cut = value[:limit]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        value = cut[: end + 1] if end > limit // 2 else cut.rstrip() + "…"
    return value or None


def url(image) -> str | None:
    return image.get("url") if isinstance(image, dict) else None


def page_data(page: str, prefix: str) -> dict:
    data = nuxt(page).get("data", {})
    return next((v for k, v in data.items() if k.startswith(prefix)), {}) or {}


def tour(slug: str) -> dict | None:
    t = page_data(fetch(f"{SITE}/tours/{slug}"), "tour-").get("tour")
    if not t:
        return None
    loc = t.get("location") or {}
    city = loc.get("city") or {}
    return {
        "slug": slug, "title": (t.get("title") or "").strip(),
        "short": text(t.get("shortDescription")), "about": text(t.get("description"), 700),
        "kind": ", ".join(c["name"] for c in t.get("categories") or [] if c.get("name")) or None,
        "duration": t.get("duration"), "price": t.get("price"),
        "group": [t.get("minGroupSize"), t.get("maxGroupSize")], "individual": bool(t.get("isIndividual")),
        "schedule": [{"day": s.get("weekDay"), "from": (s.get("startTime") or "")[:5]}
                     for s in t.get("schedule") or [] if s.get("weekDay")],
        "address": ", ".join(x for x in (city.get("name"), loc.get("address")) if x) or None,
        "image": next((url(g) for g in t.get("gallery") or [] if url(g)), None),
        "link": t.get("link"),
    }


def winery(slug: str, frame: dict) -> dict | None:
    d = page_data(fetch(f"{SITE}/wineries/{slug}"), "winery-")
    w = d.get("winery")
    if not w:
        return None
    loc = w.get("location") or {}
    point = None
    if isinstance(loc.get("latitude"), (int, float)) and isinstance(loc.get("longitude"), (int, float)):
        x, y = project_point(loc["longitude"], loc["latitude"], frame)
        point = [round(x, 2), round(y, 2)]
    faces = d.get("faces") or {}
    return {
        "slug": slug, "name": (w.get("name") or "").strip(),
        "region": (loc.get("region") or {}).get("name"),
        "address": loc.get("address"), "lat": loc.get("latitude"), "lon": loc.get("longitude"), "point": point,
        "description": text(w.get("description"), 300), "about": text(w.get("about"), 900),
        "history": text(w.get("history"), 900), "terroir": text(w.get("terroir"), 700),
        "terrain": w.get("terrain"), "climate": w.get("climate"), "square": w.get("square"),
        "site": w.get("site"), "logo": url(w.get("logo")),
        "gallery": [url(g) for g in w.get("gallery") or [] if url(g)][:4],
        "people": [{"name": f.get("name"), "role": f.get("description"), "image": url(f.get("image"))}
                   for f in faces.get("items") or [] if f.get("name")][:4],
        "people_about": text(faces.get("description"), 600),
        "wines_total": (d.get("wines") or {}).get("totalItems"),
        "nearby": [n["slug"] for n in (d.get("nearbyWineries") or {}).get("items") or [] if n.get("slug")][:6],
        "tours": [t["slug"] for t in (d.get("tours") or {}).get("items") or [] if t.get("slug")],
    }


def main() -> int:
    frame = json.loads(MAP.read_text(encoding="utf-8"))["frame"]
    slugs = re.findall(r"<loc>[^<]*/wineries/([^<]+)</loc>", fetch(f"{SITE}/wineries-sitemap.xml"))
    print(f"Виноделен: {len(slugs)}")
    wineries, failed = {}, []
    for n, slug in enumerate(slugs, 1):
        try:
            w = winery(slug, frame)
            if w:
                wineries[slug] = w
        except Exception as exc:  # одна страница не должна ронять сбор
            failed.append(slug)
            print(f"\n  {slug}: {exc}", file=sys.stderr)
        print(f"\r  {n}/{len(slugs)}", end="", flush=True)
        time.sleep(DELAY_S)
    print()

    tour_slugs = sorted({t for w in wineries.values() for t in w["tours"]})
    print(f"Туров: {len(tour_slugs)}")
    tours = {}
    for n, slug in enumerate(tour_slugs, 1):
        try:
            t = tour(slug)
            if t:
                tours[slug] = t
        except Exception as exc:
            failed.append(f"tour {slug}")
            print(f"\n  тур {slug}: {exc}", file=sys.stderr)
        print(f"\r  {n}/{len(tour_slugs)}", end="", flush=True)
        time.sleep(DELAY_S)
    print()
    for w in wineries.values():
        w["tours"] = [t for t in w["tours"] if t in tours]

    result = {"_source": f"{SITE}/wineries и /tours, __NUXT_DATA__; point — координаты винной карты",
              "wineries": wineries, "tours": tours}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with_tours = sum(1 for w in wineries.values() if w["tours"])
    print(f"Виноделен {len(wineries)} (с турами {with_tours}), туров {len(tours)}, ошибок {len(failed)} → {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
