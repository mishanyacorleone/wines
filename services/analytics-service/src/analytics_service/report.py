"""Отчёт для дашборда РСХБ: сканы, посетители, воронка регистрации, топы, шары.

Считается в Python по событиям периода — одинаково на PostgreSQL и SQLite,
а объёмы стенда (тысячи событий) — мгновенно. На пилоте с миллионами событий
агрегаты переезжают в SQL или ClickHouse; формат отчёта при этом не меняется.
"""

from __future__ import annotations

import ipaddress
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Event


def mask_ip(ip: str | None) -> str:
    """Адрес без последнего октета (IPv4) или до /48 (IPv6): видно сеть, а не человека."""
    if not ip:
        return "неизвестно"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "неизвестно"
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False).network_address).rsplit(".", 1)[0] + ".x"
    return str(ipaddress.ip_network(f"{ip}/48", strict=False).network_address) + "/48"


def is_local(ip: str | None) -> bool:
    try:
        return ipaddress.ip_address(ip or "").is_private
    except ValueError:
        return False


def _top(counter: Counter, n: int, key_name: str) -> list[dict]:
    return [{key_name: k, "count": v} for k, v in counter.most_common(n) if k]


def build(session: Session, days: int, include_demo: bool, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start_day = (now - timedelta(days=days - 1)).date()
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    query = select(Event).where(Event.ts >= start).order_by(Event.ts)
    if not include_demo:
        query = query.where(Event.demo.is_(False))
    events = session.scalars(query).all()
    has_demo = any(e.demo for e in events)

    def day(e: Event) -> date:
        ts = e.ts if e.ts.tzinfo else e.ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).date()

    scans = [e for e in events if e.type == "scan"]
    found = [e for e in scans if (e.props or {}).get("status") == "match"]
    visitors = {e.anon_id for e in events}
    account_visitors = {e.anon_id for e in events if e.user_id}
    active_days: dict[str, set[date]] = defaultdict(set)
    for e in events:
        active_days[e.anon_id].add(day(e))
    returning = sum(1 for d in active_days.values() if len(d) >= 2)

    # Конверсия: сканировал без аккаунта → зарегистрировался после этого
    first_anon_scan: dict[str, datetime] = {}
    for e in scans:
        if not e.user_id:
            first_anon_scan.setdefault(e.anon_id, e.ts)
    registered = {e.anon_id for e in events if e.type == "register"}
    converted = sum(1 for a in first_anon_scan if a in registered)

    daily = {start_day + timedelta(days=i): {"found": 0, "not_found": 0} for i in range(days)}
    for e in scans:
        bucket = daily.get(day(e))
        if bucket is not None:
            bucket["found" if (e.props or {}).get("status") == "match" else "not_found"] += 1

    shown = {e.anon_id for e in events if e.type == "nudge_shown"}
    clicked = {e.anon_id for e in events if e.type == "nudge_click"}
    shares = [e for e in events if e.type == "share"]

    ip_scans: Counter = Counter()
    ip_visitors: dict[str, set[str]] = defaultdict(set)
    for e in scans:
        key = mask_ip(e.ip)
        ip_scans[key] += 1
        ip_visitors[key].add(e.anon_id)

    wine_titles: dict[str, str] = {}
    for e in found:
        p = e.props or {}
        # название — из любого скана, где оно есть: в части событий его может не быть
        if p.get("slug") and (p.get("title") or p["slug"] not in wine_titles):
            wine_titles[p["slug"]] = p.get("title") or p["slug"]
    top_wines = Counter((e.props or {}).get("slug") for e in found)

    latencies = sorted((e.props or {}).get("ms") for e in scans if isinstance((e.props or {}).get("ms"), (int, float)))
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else None

    return {
        "period": {"days": days, "from": start_day.isoformat(), "to": now.date().isoformat()},
        "has_demo": has_demo,
        "kpi": {
            "scans": len(scans),
            "found": len(found),
            "not_found": len(scans) - len(found),
            "not_found_share": round((len(scans) - len(found)) / len(scans), 3) if scans else 0,
            "visitors": len(visitors),
            "visitors_with_account": len(account_visitors),
            "registrations": sum(1 for e in events if e.type == "register"),
            "conversion": round(converted / len(first_anon_scan), 3) if first_anon_scan else 0,
            "returning": returning,
            "returning_share": round(returning / len(visitors), 3) if visitors else 0,
            "shares": len(shares),
            "scan_p95_ms": round(p95) if p95 is not None else None,
        },
        "daily": [{"date": d.isoformat(), **v} for d, v in daily.items()],
        "funnel": [
            {"step": "Сканировали без аккаунта", "value": len(first_anon_scan)},
            {"step": "Увидели предложение войти", "value": len(shown)},
            {"step": "Нажали «Войти» или «Зарегистрироваться»", "value": len(clicked)},
            {"step": "Зарегистрировались", "value": len(registered)},
        ],
        "top_wines": [{"slug": s, "title": wine_titles.get(s, s), "count": c} for s, c in top_wines.most_common(8) if s],
        "top_regions": _top(Counter((e.props or {}).get("region") for e in found), 9, "name"),
        "top_manufacturers": _top(Counter((e.props or {}).get("manufacturer") for e in found), 8, "name"),
        "share_channels": _top(Counter((e.props or {}).get("channel") for e in shares), 5, "name"),
        "share_kinds": _top(Counter((e.props or {}).get("kind") for e in shares), 5, "name"),
        "map": {
            "opens": sum(1 for e in events if e.type == "map_open"),
            "regions": _top(Counter((e.props or {}).get("region") for e in events if e.type == "region_open"), 9, "name"),
        },
        "ips": [
            {"network": k, "scans": v, "visitors": len(ip_visitors[k])}
            for k, v in ip_scans.most_common(10)
        ],
        "local_share": round(sum(1 for e in scans if is_local(e.ip)) / len(scans), 3) if scans else 0,
        "engagement": engagement(events),
        "feedback": feedback(events, scans),
    }


# Откуда пришла регистрация: последний призыв войти перед ней у того же посетителя
SOURCE_NAMES = {
    "scan": "Плашка после скана", "map": "Винная карта", "quiz": "Вопрос дня («Сохранить серию»)",
    "diary": "Дневник («Сохранить запись»)", "gift": "Открытка («Сохранить себе»)", "challenge": "Челлендж",
    "taste": "«Мой вкус» («Готово»)", "direct": "Сами, кнопкой «Войти»",
}


def _prompt_source(e: Event) -> str | None:
    if e.type == "auth_prompt":
        return (e.props or {}).get("source") or "other"
    if e.type == "nudge_click":
        return (e.props or {}).get("place") or "scan"
    return None


def engagement(events: list[Event]) -> dict:
    """Вовлечение: вопрос дня, дневник, открытки, призы, итоги года — и какие призывы приводят регистрации."""
    by_type: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_type[e.type].append(e)
    quiz = by_type["quiz"]
    diary = by_type["diary"]
    gifts_created = [e for e in by_type["share"] if (e.props or {}).get("channel") == "gift"]

    prompts: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    prompted: dict[str, set[str]] = defaultdict(set)
    for e in events:  # события уже по времени
        source = _prompt_source(e)
        if source:
            prompts[e.anon_id].append((e.ts, source))
            prompted[source].add(e.anon_id)
    registered: Counter = Counter()
    for e in by_type["register"]:
        before = [s for ts, s in prompts.get(e.anon_id, []) if ts <= e.ts]
        registered[before[-1] if before else "direct"] += 1
    sources = []
    for key in [*SOURCE_NAMES, *sorted(set(prompted) - set(SOURCE_NAMES))]:
        shown, regs = len(prompted.get(key, ())), registered.get(key, 0)
        if shown or regs:
            sources.append({"source": key, "name": SOURCE_NAMES.get(key, key), "prompted": shown,
                            "registrations": regs, "conversion": round(regs / shown, 3) if shown else None})
    sources.sort(key=lambda r: r["registrations"], reverse=True)

    return {
        "quiz": {
            "answers": len(quiz),
            "players": len({e.anon_id for e in quiz}),
            "correct_share": round(sum(1 for e in quiz if (e.props or {}).get("correct")) / len(quiz), 3) if quiz else 0,
        },
        "diary": {
            "tasted": sum(1 for e in diary if (e.props or {}).get("kind") != "want"),
            "want": sum(1 for e in diary if (e.props or {}).get("kind") == "want"),
            "writers": len({e.anon_id for e in diary}),
        },
        "gifts": {
            "created": len(gifts_created),
            "opened": len(by_type["gift_open"]),
            "saved": len(by_type["gift_save"]),
        },
        "prizes": sum(int((e.props or {}).get("count") or 1) for e in by_type["prize"]),
        "year_opens": len(by_type["year_open"]),
        "taste": {
            "opens": sum(1 for e in by_type["taste"] if (e.props or {}).get("action") == "open"),
            "saves": sum(1 for e in by_type["taste"] if (e.props or {}).get("action") == "save"),
            "matches": sum(1 for e in by_type["taste"] if (e.props or {}).get("action") == "match"),
        },
        "registration_sources": sources,
    }


def _week(ts: datetime) -> date:
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    d = ts.astimezone(timezone.utc).date()
    return d - timedelta(days=d.weekday())


def feedback(events: list[Event], scans: list[Event]) -> dict:
    """Оценки пользователей: точность распознавания по ответам «Это ваше вино?» (по неделям) и вкусовые оценки.

    Сканер сказал «нашли» → «Да, это оно» — верно; «Нет, другое» + выбрал из похожих или «моего вина нет» — ошибка.
    Сканер сказал «нет в каталоге» → выбрал из аналогов — вино было в каталоге, мы не нашли; «моего вина нет» — верно.
    """
    weeks: dict[date, Counter] = defaultdict(Counter)
    for e in scans:
        weeks[_week(e.ts)]["scans_" + ("match" if (e.props or {}).get("status") == "match" else "not_found")] += 1
    for e in events:
        if e.type != "recognition":
            continue
        p = e.props or {}
        status = "match" if p.get("status") == "match" else "not_found"
        weeks[_week(e.ts)][f"{status}_{p.get('answer')}"] += 1

    def row(week: date, c: Counter) -> dict:
        return {
            "week": week.isoformat(),
            "scans": c["scans_match"] + c["scans_not_found"],
            "match": {"scans": c["scans_match"], "confirmed": c["match_confirmed"],
                      "picked": c["match_picked_alternative"], "missing": c["match_none_match"]},
            "not_found": {"scans": c["scans_not_found"], "picked": c["not_found_picked_alternative"],
                          "missing": c["not_found_none_match"]},
        }

    total: Counter = sum(weeks.values(), Counter())
    m = row(date.min, total)["match"]
    answered = m["confirmed"] + m["picked"] + m["missing"]

    stars: Counter = Counter()
    for e in events:
        rating = (e.props or {}).get("rating")
        if e.type == "diary" and isinstance(rating, int) and 1 <= rating <= 5:
            stars[rating] += 1
    rates = Counter((e.props or {}).get("value") for e in events if e.type == "rate")
    return {
        "weeks": [row(w, weeks[w]) for w in sorted(weeks)],
        "accuracy": round(m["confirmed"] / answered, 3) if answered else None,
        "answered": answered,
        "stars": [{"stars": n, "count": stars.get(n, 0)} for n in range(5, 0, -1)],
        "likes": rates.get(1, 0),
        "dislikes": rates.get(-1, 0),
    }
