"""Винодельни и винные туры: «От бутылки — на винодельню».

Данные — снимок страниц vino-svoe.ru (/wineries, /tours) в wineries.json
(tools/site-media/fetch_wineries.py): описание, место и точка на винной карте,
терруар, галерея, люди, туры. Вино связано с винодельней через
`manufacturer_slug` — это адрес её страницы на сайте. Фото — через SiteMedia:
локальная копия, если скачана, иначе ресайз-API сайта.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .sommelier import SiteMedia

WINERIES_PATH = Path(__file__).resolve().parent / "wineries.json"
SITE = "https://vino-svoe.ru"


class Wineries:
    def __init__(self, wineries: dict[str, dict], tours: dict[str, dict], media: SiteMedia) -> None:
        self._wineries = wineries
        self._tours = tours
        self.media = media

    @classmethod
    def load(cls, media: SiteMedia, path: Path = WINERIES_PATH) -> "Wineries":
        if not path.exists():
            return cls({}, {}, media)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data.get("wineries", {}), data.get("tours", {}), media)

    def __contains__(self, slug: str | None) -> bool:
        return bool(slug) and slug in self._wineries

    def _cover(self, w: dict, size: int = 800) -> str | None:
        return self.media.image_url((w.get("gallery") or [None])[0] or w.get("logo"), size)

    def _tour(self, slug: str, size: int = 800) -> dict[str, Any] | None:
        t = self._tours.get(slug)
        if not t:
            return None
        return {**t, "image": self.media.image_url(t.get("image"), size), "url": f"{SITE}/tours/{slug}"}

    def brief(self, slug: str | None) -> dict[str, Any] | None:
        """Для карточки вина: кто сделал, где, можно ли приехать."""
        w = self._wineries.get(slug or "")
        if not w:
            return None
        tours = [t for t in (self._tour(s, 240) for s in w.get("tours") or []) if t]
        prices = [t["price"] for t in tours if isinstance(t.get("price"), (int, float)) and t["price"] > 0]
        return {
            "slug": w["slug"], "name": w["name"], "region": w.get("region"),
            "place": _place(w.get("address")), "image": self._cover(w),
            "lead": w.get("description") or _first_sentence(w.get("about")),
            "terrain": w.get("terrain"), "climate": w.get("climate"),
            "tours": len(tours), "price_from": min(prices) if prices else None,
            "tour": tours[0] if tours else None, "has_point": bool(w.get("point")),
        }

    def full(self, slug: str) -> dict[str, Any] | None:
        w = self._wineries.get(slug)
        if not w:
            return None
        return {
            **{k: w.get(k) for k in ("slug", "name", "region", "address", "point", "description", "about",
                                     "history", "terroir", "terrain", "climate", "square", "site", "people_about")},
            "place": _place(w.get("address")),
            "url": f"{SITE}/wineries/{slug}",
            "logo": self.media.image_url(w.get("logo"), 240),
            "gallery": [self.media.image_url(g, 800) for g in w.get("gallery") or []],
            "people": [{**p, "image": self.media.image_url(p.get("image"), 240)} for p in w.get("people") or []],
            "tours": [t for t in (self._tour(s) for s in w.get("tours") or []) if t],
            "nearby": [self.pin(n) for n in w.get("nearby") or [] if n in self._wineries],
            # своих туров нет — куда съездить в том же регионе
            "region_tours": [self.pin(s) for s, o in self._wineries.items()
                             if s != slug and o.get("region") == w.get("region") and w.get("region")
                             and any(t in self._tours for t in o.get("tours") or [])][:8],
        }

    def pin(self, slug: str) -> dict[str, Any]:
        w = self._wineries[slug]
        return {"slug": slug, "name": w["name"], "region": w.get("region"), "point": w.get("point"),
                "tours": len([t for t in w.get("tours") or [] if t in self._tours]),
                "logo": self.media.image_url(w.get("logo"), 240), "image": self._cover(w, 240)}

    def pins(self) -> list[dict[str, Any]]:
        """Все винодельни с точкой на карте — для меток."""
        return [self.pin(s) for s, w in self._wineries.items() if w.get("point")]


def _place(address: str | None) -> str | None:
    """«РЕСПУБЛИКА КРЫМ, БАХЧИСАРАЙСКИЙ РАЙОН, С. ДОЛИННОЕ, …» → «с. Долинное, Бахчисарайский район»."""
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip() and not p.strip().isdigit()]
    parts = [p for p in parts if p.lower() not in ("россия", "российская федерация")]
    settlement = next((p for p in parts if p.lower().startswith(("г.", "с.", "п.", "пос", "ст-ца", "станица",
                                                                    "х.", "хутор", "село", "город", "пгт", "д."))), None)
    district = next((p for p in parts if "район" in p.lower() or "р-н" in p.lower()), None)
    out = [x for x in (settlement, district) if x] or parts[:2]
    return ", ".join(_title(x) for x in out)


def _title(value: str) -> str:
    """Адреса на сайте бывают ЗАГЛАВНЫМИ — приводим к обычному виду."""
    if value.isupper():
        return " ".join(w if (w.endswith(".") and len(w) <= 4) or w in _GENERIC else w.capitalize()
                        for w in value.lower().split())
    return value


_GENERIC = {"район", "р-н", "область", "край", "республика", "округ", "поселок", "посёлок", "село", "станица", "хутор"}


def _first_sentence(text: str | None, limit: int = 220) -> str | None:
    if not text:
        return None
    first = text.split("\n")[0]
    end = first.find(". ", 40)
    first = first[: end + 1] if 0 < end < limit else first
    return first if len(first) <= limit else first[:limit].rsplit(" ", 1)[0] + "…"
