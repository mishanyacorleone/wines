"""Демо-данные для показа дашборда: на стенде все запросы идут с одного адреса.

События помечены demo=true — дашборд пишет, что это демо, и удаляет их одной
кнопкой. Вина — из каталога сервиса распознавания (/v1/regions), так топы выглядят
как настоящие; без него — несколько вин из встроенного списка.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from .db import Event

logger = logging.getLogger(__name__)

_FALLBACK = [
    {"slug": "demo-kuban", "title": "Пино Нуар", "manufacturer": "А. Гордиенко & М. Николаев", "region": "Кубань"},
    {"slug": "demo-crimea", "title": "Алиготе", "manufacturer": "Инкерман", "region": "Крым"},
    {"slug": "demo-don", "title": "Цимлянский чёрный", "manufacturer": "Цимлянские вина", "region": "Долина Дона"},
    {"slug": "demo-dag", "title": "Ркацители", "manufacturer": "Дербент", "region": "Дагестан"},
]

# Сети провайдеров «как у живых пользователей» — для таблицы адресов
_NETWORKS = ["95.165.12", "178.140.44", "5.18.201", "176.59.30", "37.110.8", "93.80.66", "188.170.3", "46.242.15"]


def catalog_wines(api_url: str) -> list[dict]:
    """Вина каталога с весом региона: чем больше вин в регионе, тем чаще его сканируют."""
    try:
        with httpx.Client(base_url=api_url, timeout=5, trust_env=False) as client:
            regions = client.get("/v1/regions").raise_for_status().json()
            wines = []
            for region in regions:
                for wine in client.get(f"/v1/regions/{region['name']}/wines", params={"limit": 12}).raise_for_status().json():
                    wines.append({**wine, "weight": max(1, region["wines"]) ** 0.6})
            return wines or _FALLBACK
    except Exception as exc:  # каталог недоступен — демо всё равно нужно показать
        logger.warning("Каталог для демо-данных недоступен (%s), беру встроенный список", exc)
        return _FALLBACK


def generate(session: Session, wines: list[dict], days: int = 30, seed: int | None = None) -> int:
    rnd = random.Random(seed)
    now = datetime.now(timezone.utc)
    weights = [w.get("weight", 1) for w in wines]
    pool: list[dict] = []          # постоянные посетители — они возвращаются в другие дни
    next_user_id = 900_000         # демо-аккаунты не пересекаются с настоящими
    events: list[Event] = []

    def add(ts, type_, visitor, **props):
        events.append(Event(ts=ts, type=type_, anon_id=visitor["anon"], user_id=visitor.get("user"),
                            ip=visitor["ip"], props=props, demo=True))

    for offset in range(days, 0, -1):
        day_start = (now - timedelta(days=offset - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # рост аудитории к концу периода, по выходным — больше
        base = 18 + 70 * (1 - offset / days)
        if day_start.weekday() >= 5:
            base *= 1.35
        for _ in range(int(rnd.gauss(base, base * 0.15))):
            if pool and rnd.random() < 0.28:
                visitor = rnd.choice(pool)
            else:
                visitor = {"anon": str(uuid.uuid4()), "ip": f"{rnd.choice(_NETWORKS)}.{rnd.randint(2, 254)}"}
                pool.append(visitor)
            ts = day_start + timedelta(hours=rnd.choice([12, 13, 17, 18, 19, 19, 20, 20, 21]) + rnd.random(),
                                       minutes=rnd.randint(0, 59))
            if ts > now:
                ts = now - timedelta(minutes=rnd.randint(1, 120))
            add(ts, "page_view", visitor)
            for i in range(rnd.choices([1, 2, 3, 4], weights=[55, 25, 12, 8])[0]):
                t = ts + timedelta(minutes=2 * i + rnd.random())
                if rnd.random() < 0.74:
                    wine = rnd.choices(wines, weights=weights)[0]
                    add(t, "scan", visitor, status="match", slug=wine["slug"], title=wine.get("title"),
                        region=wine.get("region"), manufacturer=wine.get("manufacturer"), ms=rnd.randint(1300, 2700))
                    if rnd.random() < 0.09:
                        add(t + timedelta(seconds=40), "share", visitor,
                            kind=rnd.choices(["wine", "achievement", "map"], weights=[70, 20, 10])[0],
                            channel=rnd.choices(["native", "tg", "vk", "save"], weights=[40, 30, 15, 15])[0])
                else:
                    add(t, "scan", visitor, status="not_found", ms=rnd.randint(1300, 2900))
            # без аккаунта — предложение войти, часть регистрируется
            if not visitor.get("user"):
                add(ts + timedelta(minutes=1), "nudge_shown", visitor)
                if rnd.random() < 0.3:
                    add(ts + timedelta(minutes=1, seconds=20), "nudge_click", visitor)
                    if rnd.random() < 0.62:
                        next_user_id += 1
                        visitor["user"] = next_user_id
                        add(ts + timedelta(minutes=2), "register", visitor)
            if rnd.random() < 0.22:
                add(ts + timedelta(minutes=5), "map_open", visitor)
                if rnd.random() < 0.7:
                    wine = rnd.choices(wines, weights=weights)[0]
                    add(ts + timedelta(minutes=6), "region_open", visitor, region=wine.get("region"))
    session.add_all(events)
    session.commit()
    return len(events)
