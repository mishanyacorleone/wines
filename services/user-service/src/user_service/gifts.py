"""Подарочная открытка и «Мой винный год».

Открытка: человек дарит вино и делает ссылку — карточка вина, пожелание, от кого.
Получатель открывает её без аккаунта; «Сохранить себе» кладёт вино в его дневник
(«хочу попробовать») и требует входа — так подарок приводит нового пользователя.

«Мой винный год» — итоги года по сканам, оценкам, дневнику и вопросу дня. Включает
админ (настройка year.enabled): это сезонная механика под Новый год. Админ видит
свои итоги всегда — чтобы проверить экран до запуска.
"""

# Без `from __future__ import annotations`: модели запросов объявлены внутри mount
import logging
import secrets
import time
from collections import Counter
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import diary, rewards, zones
from .dates import day_start, today
from .db import Achievement, Gift, QuizAnswer, Rating, Scan, Setting, TastingNote, UserAchievement, WineZone

logger = logging.getLogger("gifts")

YEAR_ENABLED_KEY = "year.enabled"
YEAR_VALUE_KEY = "year.year"
DEFAULT_MESSAGE = "Попробуй это вино — мне кажется, тебе понравится!"
# Открыток с одного адреса за час — защита от накрутки ссылками
_MAX_GIFTS_PER_HOUR = 30


def mount(app: FastAPI, d) -> None:
    SessionDep, UserDep, OptionalUserDep, AdminDep, CatalogDep = (
        d.SessionDep, d.UserDep, d.OptionalUserDep, d.AdminDep, d.CatalogDep)
    recent: dict[str, list[float]] = {}

    class GiftIn(BaseModel):
        slug: str = Field(min_length=1, max_length=255)
        from_name: str | None = Field(default=None, max_length=40)
        message: str | None = Field(default=None, max_length=300)

    class GiftOut(BaseModel):
        token: str
        from_name: str
        message: str
        wine: dict
        created_at: datetime

    class YearSettings(BaseModel):
        enabled: bool
        year: int = Field(ge=2024, le=2100)

    def gift_out(g: Gift) -> GiftOut:
        return GiftOut(token=g.token, from_name=g.from_name, message=g.message, wine=g.wine, created_at=g.created_at)

    def find(session, token: str) -> Gift:
        g = session.get(Gift, token)
        if g is None:
            raise HTTPException(status_code=404, detail="Открытка не найдена — проверьте ссылку")
        return g

    # ── Открытка ──
    @app.post("/v1/gifts", response_model=GiftOut, status_code=201)
    def create_gift(body: GiftIn, request: Request, session: SessionDep, user: OptionalUserDep,
                    catalog: CatalogDep) -> GiftOut:
        client = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
        now = time.monotonic()
        stamps = [t for t in recent.get(client, []) if now - t < 3600]
        if len(stamps) >= _MAX_GIFTS_PER_HOUR:
            raise HTTPException(status_code=429, detail="Слишком много открыток — попробуйте позже")
        recent[client] = [*stamps, now]
        wine = d.lookup_wine(catalog, body.slug)
        name = (body.from_name or "").strip() or (user.name if user else "") or "Друг"
        g = Gift(token=secrets.token_urlsafe(9), sender_id=user.id if user else None, from_name=name,
                 slug=body.slug, wine=wine, message=(body.message or "").strip() or DEFAULT_MESSAGE)
        session.add(g)
        session.commit()
        logger.info("Открытка: «%s» от %s%s", wine["title"], d.who(user) if user else "гостя", f" ({g.token})")
        return gift_out(g)

    @app.get("/v1/gifts/{gift_id}", response_model=GiftOut)
    def open_gift(gift_id: str, session: SessionDep, user: OptionalUserDep) -> GiftOut:
        g = find(session, gift_id)
        if user is None or user.id != g.sender_id:  # сам себе — не считаем
            g.opens += 1
            session.commit()
        return gift_out(g)

    @app.post("/v1/gifts/{gift_id}/save")
    def save_gift(gift_id: str, user: UserDep, session: SessionDep) -> dict:
        """Вино из открытки → дневник получателя, «хочу попробовать»."""
        g = find(session, gift_id)
        note = diary.add_want(session, user.id, g.slug, g.wine, f"Подарок · {g.from_name}")
        g.saves += 1
        session.commit()
        logger.info("Открытка %s сохранена: пользователь %s — «%s»", g.token, d.who(user), g.wine.get("title"))
        return {"id": note.id, "kind": note.kind}

    # ── Мой винный год ──
    def year_settings(session) -> YearSettings:
        enabled = session.get(Setting, YEAR_ENABLED_KEY)
        year = session.get(Setting, YEAR_VALUE_KEY)
        return YearSettings(enabled=bool(enabled and enabled.value == "1"),
                            year=int(year.value) if year else today().year)

    def put_setting(session, key: str, value: str) -> None:
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value

    @app.get("/v1/settings/year", response_model=YearSettings)
    def get_year_settings(session: SessionDep) -> YearSettings:
        return year_settings(session)

    @app.put("/v1/admin/settings/year", response_model=YearSettings)
    def put_year_settings(body: YearSettings, admin: AdminDep, session: SessionDep) -> YearSettings:
        put_setting(session, YEAR_ENABLED_KEY, "1" if body.enabled else "0")
        put_setting(session, YEAR_VALUE_KEY, str(body.year))
        session.commit()
        d.admin_log.info("Админ %s %s «Мой винный год %d»", d.who(admin),
                         "включил" if body.enabled else "выключил", body.year)
        return year_settings(session)

    @app.get("/v1/me/year")
    def my_year(user: UserDep, session: SessionDep) -> dict:
        conf = year_settings(session)
        if not conf.enabled and user.role != "admin":
            raise HTTPException(status_code=403, detail="Итоги года ещё не открыты")
        year = conf.year
        start, end = day_start(date(year, 1, 1)), day_start(date(year + 1, 1, 1))
        scans = session.execute(
            select(Scan.slug, Scan.wine, Scan.scanned_at).where(
                Scan.user_id == user.id, Scan.slug.is_not(None), Scan.scanned_at >= start, Scan.scanned_at < end)
            .order_by(Scan.scanned_at)
        ).all()
        wines: dict[str, dict] = {}
        for slug, wine, _ in scans:
            wines.setdefault(slug, wine or {})
        resolve = zones.resolver(session)
        regions = Counter(resolve(w.get("region")) or w.get("region") for _, w, _ in scans if (w or {}).get("region"))
        grapes = Counter(g for _, w, _ in scans for g in (w or {}).get("grapes") or [])
        makers = Counter((w or {}).get("manufacturer") for _, w, _ in scans if (w or {}).get("manufacturer"))
        notes = session.scalars(select(TastingNote).where(
            TastingNote.user_id == user.id, TastingNote.kind == "tasted",
            TastingNote.created_at >= start, TastingNote.created_at < end)).all()
        rated = [n for n in notes if n.rating]
        best_note = max(rated, key=lambda n: (n.rating, n.created_at), default=None)
        likes = session.scalar(select(func.count()).select_from(Rating).where(
            Rating.user_id == user.id, Rating.value == 1, Rating.updated_at >= start, Rating.updated_at < end)) or 0
        quiz_days = session.scalar(select(func.count()).select_from(QuizAnswer).where(
            QuizAnswer.user_id == user.id, QuizAnswer.day >= date(year, 1, 1), QuizAnswer.day <= date(year, 12, 31))) or 0
        badges = session.execute(
            select(Achievement.title, Achievement.image_file).join(
                UserAchievement, UserAchievement.achievement_id == Achievement.id)
            .where(UserAchievement.user_id == user.id, UserAchievement.earned_at >= start,
                   UserAchievement.earned_at < end)
            .order_by(UserAchievement.earned_at)
        ).all()
        top = lambda c: c.most_common(1)[0][0] if c else None  # noqa: E731
        styles = Counter((w or {}).get("category") for _, w, _ in scans if (w or {}).get("category"))
        top_grape = top(grapes)
        # вино, которое «показывает» сорт года: чаще всего сканированное из этого сорта
        by_grape = Counter(slug for slug, w, _ in scans if top_grape in ((w or {}).get("grapes") or []))
        tags = Counter(t for n in notes for t in n.tags or [])
        top_region = top(regions)
        regions_total = session.scalar(select(func.count()).select_from(WineZone)) or 0
        return {
            "year": year,
            "enabled": conf.enabled,
            "scans": len(scans),
            "wines": len(wines),
            "regions": len(regions),
            "regions_total": regions_total,
            "top_region": top_region,
            "top_region_wines": regions[top_region] if top_region else 0,
            "region_names": [name for name, _ in regions.most_common()],
            "top_grape": top_grape,
            "taste_wine": wines.get(top(by_grape)) if by_grape else None,
            "top_style": top(styles),
            "top_manufacturer": top(makers),
            "top_tags": [t for t, _ in tags.most_common(3)],
            "first_wine": scans[0][1] if scans else None,
            "first_at": scans[0][2] if scans else None,
            "diary": len(notes),
            "avg_rating": round(sum(n.rating for n in rated) / len(rated), 1) if rated else None,
            "best_wine": best_note.wine if best_note else None,
            "best_rating": best_note.rating if best_note else None,
            "likes": likes,
            "quiz_days": quiz_days,
            "best_streak": rewards.streaks(session, user.id)[1],
            "achievements": len(badges),
            "badges": [{"title": t, "image_url": f"/v1/media/achievements/{f}" if f else None} for t, f in badges],
            "covers": [w.get("image_url") for w in list(wines.values())[-8:] if w.get("image_url")],
        }
