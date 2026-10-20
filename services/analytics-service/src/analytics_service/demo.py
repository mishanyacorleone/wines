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
                    if rnd.random() < 0.3:  # «Это ваше вино?» — отвечает меньшинство; точность ~96%, как на замере
                        add(t + timedelta(seconds=15), "recognition", visitor, status="match",
                            answer=rnd.choices(["confirmed", "picked_alternative", "none_match"], weights=[96, 3, 1])[0])
                    if rnd.random() < 0.09:
                        add(t + timedelta(seconds=40), "share", visitor,
                            kind=rnd.choices(["wine", "achievement", "map"], weights=[70, 20, 10])[0],
                            channel=rnd.choices(["native", "tg", "vk", "save"], weights=[40, 30, 15, 15])[0])
                else:
                    add(t, "scan", visitor, status="not_found", ms=rnd.randint(1300, 2900))
                    if rnd.random() < 0.15:
                        add(t + timedelta(seconds=25), "recognition", visitor, status="not_found",
                            answer=rnd.choices(["none_match", "picked_alternative"], weights=[80, 20])[0])
            # вовлечение: вопрос дня, дневник, открытки — до регистрации, чтобы был виден её источник
            if rnd.random() < 0.35:
                add(ts + timedelta(seconds=30), "quiz", visitor, correct=rnd.random() < 0.62)
            if visitor.get("user") and rnd.random() < 0.18:
                kind = rnd.choices(["tasted", "want"], weights=[75, 25])[0]
                add(ts + timedelta(minutes=3), "diary", visitor, kind=kind,
                    rating=rnd.choices([5, 4, 3, 2, 1], weights=[30, 38, 18, 9, 5])[0] if kind == "tasted" else None)
            if visitor.get("user") and rnd.random() < 0.12:
                add(ts + timedelta(minutes=7), "rate", visitor, value=rnd.choices([1, -1], weights=[78, 22])[0])
            if rnd.random() < 0.04:
                add(ts + timedelta(minutes=4), "share", visitor, kind="gift", channel="gift")
                friend = {"anon": str(uuid.uuid4()), "ip": f"{rnd.choice(_NETWORKS)}.{rnd.randint(2, 254)}"}
                opened = ts + timedelta(hours=rnd.randint(1, 20))
                if opened < now and rnd.random() < 0.8:
                    add(opened, "gift_open", friend)
                    if rnd.random() < 0.45:  # «Сохранить себе» — только с аккаунтом
                        add(opened + timedelta(seconds=30), "auth_prompt", friend, source="gift")
                        if rnd.random() < 0.7:
                            next_user_id += 1
                            friend["user"] = next_user_id
                            add(opened + timedelta(minutes=1), "register", friend)
                            add(opened + timedelta(minutes=1, seconds=10), "gift_save", friend)
                            pool.append(friend)
            # без аккаунта — призывы войти (плашка после скана, серия вопроса дня, запись в дневник),
            # часть регистрируется — источник регистрации виден в «Откуда регистрации»
            if not visitor.get("user"):
                add(ts + timedelta(minutes=1), "nudge_shown", visitor)
                source, chance = rnd.choices(
                    [("scan", 0.3), ("quiz", 0.12), ("diary", 0.08)], weights=[60, 25, 15])[0]
                if rnd.random() < chance * 3:
                    if source == "scan":
                        add(ts + timedelta(minutes=1, seconds=20), "nudge_click", visitor, place="scan")
                    else:
                        add(ts + timedelta(minutes=1, seconds=20), "auth_prompt", visitor, source=source)
                    if rnd.random() < {"scan": 0.6, "quiz": 0.45, "diary": 0.7}[source]:
                        next_user_id += 1
                        visitor["user"] = next_user_id
                        add(ts + timedelta(minutes=2), "register", visitor)
                        if source == "diary":
                            add(ts + timedelta(minutes=2, seconds=20), "diary", visitor, kind="tasted",
                                rating=rnd.choice([3, 4, 5]))
            if visitor.get("user") and rnd.random() < 0.05:
                add(ts + timedelta(minutes=8), "prize", visitor, count=1)
            if offset <= 3 and visitor.get("user") and rnd.random() < 0.15:
                add(ts + timedelta(minutes=9), "year_open", visitor)
            if rnd.random() < 0.22:
                add(ts + timedelta(minutes=5), "map_open", visitor)
                if rnd.random() < 0.7:
                    wine = rnd.choices(wines, weights=weights)[0]
                    add(ts + timedelta(minutes=6), "region_open", visitor, region=wine.get("region"))
    session.add_all(events)
    session.commit()
    return len(events)
