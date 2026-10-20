"""Локальные копии картинок сайта: блюда, регионы, сорта, винодельни и туры.

    python tools/site-media/download_site_media.py

Пути картинок лежат в services/search-api/src/search_api/site_media.json
(fetch_site_media.py) и wineries.json (fetch_wineries.py). Без копий страница берёт их с ресайз-API сайта — и
показывает заглушки, когда сайт недоступен или медленный. Скрипт скачивает
картинки в data/site-media/<размер>/<файл> (справочники — в двух размерах); api отдаёт
ссылку на локальную копию, если она есть (`SiteMedia.image_url`), сервис web
раздаёт папку как /site-media/. Уже скачанные файлы пропускаются — повторный
запуск докачивает только новые.

Только стандартная библиотека; прокси — из HTTPS_PROXY, как у urllib.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEDIA_JSON = ROOT / "services" / "search-api" / "src" / "search_api" / "site_media.json"
WINERIES_JSON = MEDIA_JSON.with_name("wineries.json")
OUT = ROOT / "data" / "site-media"
IMAGE_API = "https://api.vino-svoe.ru/v1/img/str-api"
# 240 — миниатюры (блюда 72 px, сорта и регионы 44 px, «Мой вкус» — с запасом для retina),
# 800 — фон под характеристиками, фото региона, слайды «Мой винный год»
SIZES = (240, 800)
DELAY_S = 0.2


def _name(path: str) -> str:
    return path.lstrip("/").removeprefix("uploads/")


def files(media: dict, wineries: dict) -> set[tuple[int, str]]:
    """(размер, файл): справочники — в обоих размерах; у виноделен — только нужные."""
    paths = set(media.get("dishes", {}).values()) | set(media.get("regions", {}).values())
    for grape in media.get("grapes", {}).values():
        paths |= {p for p in grape.values() if p}
    out = {(size, _name(p)) for p in paths for size in SIZES}
    for w in wineries.get("wineries", {}).values():
        gallery = w.get("gallery") or []
        out |= {(800, _name(g)) for g in gallery}                       # галерея на странице винодельни
        out |= {(240, _name(p)) for p in [*gallery[:1], w.get("logo")] if p}  # метка на карте
        out |= {(240, _name(p["image"])) for p in w.get("people") or [] if p.get("image")}
    for t in wineries.get("tours", {}).values():
        if t.get("image"):
            out |= {(size, _name(t["image"])) for size in SIZES}
    return out


def main() -> int:
    wineries = json.loads(WINERIES_JSON.read_text(encoding="utf-8")) if WINERIES_JSON.exists() else {}
    wanted = files(json.loads(MEDIA_JSON.read_text(encoding="utf-8")), wineries)
    todo = sorted((size, name) for size, name in wanted if not (OUT / str(size) / name).exists())
    print(f"Картинок {len(wanted)}, скачать {len(todo)}")
    failed = 0
    for n, (size, name) in enumerate(todo, 1):
        target = OUT / str(size) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"{IMAGE_API}/{size}/{size}/resize/uploads/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "vino-scanner-media/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(target)  # оборванная загрузка не оставляет битый файл
        except Exception as exc:  # одна картинка не должна ронять остальные
            failed += 1
            print(f"\n  {size}/{name}: {exc}", file=sys.stderr)
        print(f"\r  {n}/{len(todo)}", end="", flush=True)
        time.sleep(DELAY_S)
    if todo:
        print()
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) if OUT.exists() else 0
    print(f"Готово: не скачалось {failed}, в {OUT} — {total / 1e6:.1f} МБ")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
