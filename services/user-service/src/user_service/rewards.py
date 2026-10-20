"""Призы (промокоды) и челленджи.

Приз — набор промокодов: список (каждому свой) или один общий код. Админ решает,
за что приз выдаётся сам по себе — серия «Вопроса дня» из N дней или достижение, —
либо привязывает его к челленджу. Сами скидки даёт партнёр (ВинЛаб, винодельня):
мы только раздаём коды и следим, чтобы за одно и то же код выдавался один раз.

Челлендж — задание с датами: «попробуйте 3 вина из Долины Дона» (сканы вин),
«5 записей в дневнике» или «ответьте на вопрос дня 7 раз». Прогресс считается
по уже записанным сканам, записям и ответам — отдельного учёта нет.

Призы выдаются лениво: после скана, записи в дневник, ответа на вопрос и при
открытии профиля (`check`) — так подхватываются и челленджи, добавленные позже.
"""

# Без `from __future__ import annotations`: модели запросов объявлены внутри mount
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import zones
from .dates import day_start, today
from .db import (
    Challenge,
    PromoCode,
    PromoIssue,
    PromoPool,
    QuizAnswer,
    Scan,
    TastingNote,
    UserAchievement,
    utcnow,
)

logger = logging.getLogger("rewards")

# Условие челленджа: формы «1 / 2 / 5» — «1 вино», «2 вина», «5 вин»
CHALLENGE_KINDS = {
    "wines": ("вино", "вина", "вин"),
    "diary": ("запись в дневнике", "записи в дневнике", "записей в дневнике"),
    "quiz": ("ответ на вопрос дня", "ответа на вопрос дня", "ответов на вопрос дня"),
}
FILTER_FIELDS = {"region": "регион", "grape": "сорт", "category": "категория", "manufacturer": "винодельня"}
MAX_CODES = 10_000


# ── Серия «Вопроса дня» ──────────────────────────────────────────────────
def streaks(session: Session, user_id: int, on: date | None = None) -> tuple[int, int]:
    """(текущая, лучшая) серия: дни с ответом подряд. Текущая жива, если ответ был сегодня или вчера."""
    days = sorted(set(session.scalars(select(QuizAnswer.day).where(QuizAnswer.user_id == user_id))))
    if not days:
        return 0, 0
    best = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        best = max(best, run)
    current = 0
    day = on or today()
    if days[-1] >= day - timedelta(days=1):
        current = 1
        for prev, cur in zip(reversed(days[:-1]), reversed(days[1:])):
            if cur - prev != timedelta(days=1):
                break
            current += 1
    return current, best


# ── Челленджи ────────────────────────────────────────────────────────────
def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _matches(challenge: Challenge, wine: dict, resolve) -> bool:
    field, value = challenge.filter_field, _norm(challenge.filter_value)
    if not field or not value:
        return True
    if field == "region":
        raw = wine.get("region")
        return value in (_norm(raw), _norm(resolve(raw)))
    if field == "grape":
        return any(value in _norm(g) for g in wine.get("grapes") or [])
    if field == "category":
        return value in _norm(wine.get("category"))
    if field == "manufacturer":
        return value in _norm(wine.get("manufacturer"))
    return False


def counted_from(challenge: Challenge) -> datetime:
    """С какого момента считается прогресс: с начала челленджа или, если он опубликован позже, — с публикации."""
    start = day_start(challenge.starts_on)
    if challenge.count_before:
        return start
    published = challenge.published_at or challenge.created_at
    if published is None:
        return start
    published = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
    return max(start, published)


def challenge_progress(session: Session, user_id: int, challenge: Challenge) -> int:
    start, end = counted_from(challenge), day_start(challenge.ends_on + timedelta(days=1))
    if challenge.kind == "wines":
        rows = session.execute(
            select(Scan.slug, Scan.wine).where(Scan.user_id == user_id, Scan.slug.is_not(None),
                                               Scan.scanned_at >= start, Scan.scanned_at < end)
        ).all()
        resolve = zones.resolver(session)
        return len({slug for slug, wine in rows if _matches(challenge, wine or {}, resolve)})
    if challenge.kind == "diary":
        return session.scalar(select(func.count()).select_from(TastingNote).where(
            TastingNote.user_id == user_id, TastingNote.kind == "tasted",
            TastingNote.created_at >= start, TastingNote.created_at < end)) or 0
    if challenge.kind == "quiz":
        return session.scalar(select(func.count()).select_from(QuizAnswer).where(
            QuizAnswer.user_id == user_id, QuizAnswer.answered_at >= start,
            QuizAnswer.day <= challenge.ends_on)) or 0
    return 0


def active_challenges(session: Session, on: date | None = None) -> list[Challenge]:
    day = on or today()
    return list(session.scalars(
        select(Challenge).where(Challenge.is_active.is_(True), Challenge.starts_on <= day, Challenge.ends_on >= day)
        .order_by(Challenge.ends_on, Challenge.id)
    ))


def describe(challenge: Challenge) -> str:
    """«3 вина (регион: Долина Дона)» — условие одной строкой для админки и лога."""
    n = challenge.target
    one, few, many = CHALLENGE_KINDS.get(challenge.kind, (challenge.kind,) * 3)
    form = one if n % 10 == 1 and n % 100 != 11 else few if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else many
    what = f"{n} {form}"
    if challenge.kind == "wines" and challenge.filter_field and challenge.filter_value:
        what += f" ({FILTER_FIELDS.get(challenge.filter_field, challenge.filter_field)}: {challenge.filter_value})"
    return what


# ── Выдача призов ────────────────────────────────────────────────────────
def issue(session: Session, user_id: int, pool: PromoPool, source: str, reason: str) -> PromoIssue | None:
    """Выдаёт код из приза один раз за source. Коды кончились — None и предупреждение в логе."""
    if not pool.is_active:
        return None
    if session.scalar(select(PromoIssue.id).where(PromoIssue.user_id == user_id, PromoIssue.source == source)):
        return None
    if pool.shared_code:
        code = pool.shared_code
    else:
        row = session.scalar(select(PromoCode).where(PromoCode.pool_id == pool.id, PromoCode.issued.is_(False))
                             .order_by(PromoCode.id).limit(1).with_for_update(skip_locked=True))
        if row is None:
            logger.warning("Приз «%s»: промокоды закончились — пользователь #%d не получил приз за «%s»",
                           pool.title, user_id, reason)
            return None
        row.issued = True
        code = row.code
    prize = PromoIssue(user_id=user_id, pool_id=pool.id, code=code, source=source, reason=reason)
    session.add(prize)
    try:
        session.commit()
    except IntegrityError:  # тот же приз выдал параллельный запрос
        session.rollback()
        return None
    logger.info("Приз «%s» выдан пользователю #%d: %s", pool.title, user_id, reason)
    return prize


def check(session: Session, user_id: int) -> list[PromoIssue]:
    """Выдаёт всё, что пользователь уже заработал и ещё не получил. Возвращает новые призы."""
    new: list[PromoIssue] = []
    pools = session.scalars(select(PromoPool).where(PromoPool.is_active.is_(True))).all()
    if not pools:
        return new
    by_id = {p.id: p for p in pools}
    given = set(session.scalars(select(PromoIssue.source).where(PromoIssue.user_id == user_id)))
    best = None
    earned = None
    for pool in pools:
        source = f"pool:{pool.id}"
        if source in given or pool.trigger == "challenge" or not pool.trigger_value:
            continue
        if pool.trigger == "quiz_streak":
            best = best if best is not None else streaks(session, user_id)[1]
            if best >= pool.trigger_value:
                prize = issue(session, user_id, pool, source, f"Серия «Вопроса дня»: {pool.trigger_value} дн. подряд")
                new += [prize] if prize else []
        elif pool.trigger == "achievement":
            if earned is None:
                earned = set(session.scalars(
                    select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id)))
            if pool.trigger_value in earned:
                prize = issue(session, user_id, pool, source, "За достижение")
                new += [prize] if prize else []
    for challenge in active_challenges(session):
        source = f"challenge:{challenge.id}"
        pool = by_id.get(challenge.reward_pool_id or 0)
        if pool is None or source in given:
            continue
        if challenge_progress(session, user_id, challenge) >= challenge.target:
            prize = issue(session, user_id, pool, source, f"Челлендж «{challenge.title}»")
            new += [prize] if prize else []
    return new


# ── Ручки ────────────────────────────────────────────────────────────────
class PrizeOut(BaseModel):
    title: str
    description: str | None
    code: str
    reason: str
    issued_at: datetime


def prize_out(session: Session, prize: PromoIssue) -> PrizeOut:
    pool = session.get(PromoPool, prize.pool_id)
    return PrizeOut(title=pool.title if pool else "Приз", description=pool.description if pool else None,
                    code=prize.code, reason=prize.reason, issued_at=prize.issued_at)


def mount(app: FastAPI, d) -> None:
    """d — зависимости сервиса пользователей (main.create_app): SessionDep, UserDep, …"""
    SessionDep, UserDep, OptionalUserDep, AdminDep = d.SessionDep, d.UserDep, d.OptionalUserDep, d.AdminDep

    class ChallengeIn(BaseModel):
        title: str = Field(min_length=1, max_length=80)
        description: str | None = Field(default=None, max_length=500)
        starts_on: date
        ends_on: date
        kind: Literal["wines", "diary", "quiz"] = "wines"
        filter_field: Literal["region", "grape", "category", "manufacturer"] | None = None
        filter_value: str | None = Field(default=None, max_length=120)
        target: int = Field(ge=1, le=1000)
        reward_pool_id: int | None = None
        is_active: bool = True
        count_before: bool = Field(default=False, description="засчитать сделанное до публикации")

    class ChallengeOut(ChallengeIn):
        id: int
        condition: str
        progress: int = 0
        done: bool = False
        reward: dict | None = None  # {title, description}
        prize_code: str | None = None

    class PoolIn(BaseModel):
        title: str = Field(min_length=1, max_length=80)
        description: str | None = Field(default=None, max_length=500)
        shared_code: str | None = Field(default=None, max_length=64)
        codes: str = Field(default="", max_length=400_000, description="новые коды, по одному в строке")
        trigger: Literal["quiz_streak", "achievement", "challenge"] = "challenge"
        trigger_value: int | None = Field(default=None, ge=1)
        is_active: bool = True

    class PoolOut(BaseModel):
        id: int
        title: str
        description: str | None
        shared_code: str | None
        trigger: str
        trigger_value: int | None
        is_active: bool
        codes_total: int
        codes_left: int
        issued: int

    def challenge_out(session: Session, c: Challenge, user=None) -> ChallengeOut:
        pool = session.get(PromoPool, c.reward_pool_id) if c.reward_pool_id else None
        out = ChallengeOut(
            id=c.id, title=c.title, description=c.description, starts_on=c.starts_on, ends_on=c.ends_on,
            kind=c.kind, filter_field=c.filter_field, filter_value=c.filter_value, target=c.target,
            reward_pool_id=c.reward_pool_id, is_active=c.is_active, count_before=bool(c.count_before),
            condition=describe(c),
            reward={"title": pool.title, "description": pool.description} if pool else None,
        )
        if user is not None:
            out.progress = min(challenge_progress(session, user.id, c), c.target)
            out.done = out.progress >= c.target
            out.prize_code = session.scalar(select(PromoIssue.code).where(
                PromoIssue.user_id == user.id, PromoIssue.source == f"challenge:{c.id}"))
        return out

    def pool_out(session: Session, p: PromoPool) -> PoolOut:
        total = session.scalar(select(func.count()).select_from(PromoCode).where(PromoCode.pool_id == p.id)) or 0
        left = session.scalar(select(func.count()).select_from(PromoCode).where(
            PromoCode.pool_id == p.id, PromoCode.issued.is_(False))) or 0
        issued = session.scalar(select(func.count()).select_from(PromoIssue).where(PromoIssue.pool_id == p.id)) or 0
        return PoolOut(id=p.id, title=p.title, description=p.description, shared_code=p.shared_code,
                       trigger=p.trigger, trigger_value=p.trigger_value, is_active=p.is_active,
                       codes_total=total, codes_left=left, issued=issued)

    def check_challenge(session: Session, body: ChallengeIn) -> None:
        if body.ends_on < body.starts_on:
            raise HTTPException(status_code=422, detail="Дата окончания раньше даты начала")
        if body.reward_pool_id is not None and session.get(PromoPool, body.reward_pool_id) is None:
            raise HTTPException(status_code=422, detail="Нет такого приза")

    def apply_pool(session: Session, pool: PromoPool, body: PoolIn) -> int:
        """Поля приза + новые коды из текста; возвращает, сколько кодов добавлено."""
        shared = (body.shared_code or "").strip() or None
        codes = list(dict.fromkeys(c.strip() for c in body.codes.splitlines() if c.strip()))
        if any(len(c) > 64 for c in codes):
            raise HTTPException(status_code=422, detail="Промокод длиннее 64 символов")
        if len(codes) > MAX_CODES:
            raise HTTPException(status_code=422, detail=f"За раз — не больше {MAX_CODES} кодов")
        if body.trigger != "challenge" and not body.trigger_value:
            raise HTTPException(status_code=422, detail="Укажите, сколько дней серии или какое достижение")
        pool.title, pool.description = body.title.strip(), (body.description or "").strip() or None
        pool.shared_code, pool.trigger, pool.is_active = shared, body.trigger, body.is_active
        pool.trigger_value = body.trigger_value if body.trigger != "challenge" else None
        if pool.id is None:
            session.add(pool)
            session.flush()
        existing = set(session.scalars(select(PromoCode.code).where(PromoCode.pool_id == pool.id)))
        fresh = [c for c in codes if c not in existing]
        session.add_all(PromoCode(pool_id=pool.id, code=c) for c in fresh)
        if not shared and not existing and not fresh:
            raise HTTPException(status_code=422, detail="Добавьте промокоды списком или один общий код")
        session.commit()
        return len(fresh)

    # ── пользователь ──
    @app.get("/v1/challenges", response_model=list[ChallengeOut])
    def challenges(session: SessionDep, user: OptionalUserDep) -> list[ChallengeOut]:
        """Идущие сейчас челленджи; у вошедшего — прогресс и выданный приз."""
        if user is not None:
            check(session, user.id)
        return [challenge_out(session, c, user) for c in active_challenges(session)]

    @app.get("/v1/me/prizes", response_model=list[PrizeOut])
    def my_prizes(user: UserDep, session: SessionDep) -> list[PrizeOut]:
        check(session, user.id)
        rows = session.scalars(select(PromoIssue).where(PromoIssue.user_id == user.id)
                               .order_by(PromoIssue.issued_at.desc())).all()
        return [prize_out(session, p) for p in rows]

    # ── админ: челленджи ──
    @app.get("/v1/admin/challenges", response_model=list[ChallengeOut])
    def admin_challenges(_: AdminDep, session: SessionDep) -> list[ChallengeOut]:
        rows = session.scalars(select(Challenge).order_by(Challenge.starts_on.desc(), Challenge.id.desc())).all()
        return [challenge_out(session, c) for c in rows]

    @app.post("/v1/admin/challenges", response_model=ChallengeOut, status_code=201)
    def create_challenge(body: ChallengeIn, admin: AdminDep, session: SessionDep) -> ChallengeOut:
        check_challenge(session, body)
        c = Challenge(**body.model_dump(), published_at=utcnow() if body.is_active else None)
        session.add(c)
        session.commit()
        d.admin_log.info("Админ %s добавил челлендж «%s»: %s, %s — %s", d.who(admin), c.title, describe(c),
                         c.starts_on, c.ends_on)
        return challenge_out(session, c)

    @app.put("/v1/admin/challenges/{challenge_id}", response_model=ChallengeOut)
    def update_challenge(challenge_id: int, body: ChallengeIn, admin: AdminDep, session: SessionDep) -> ChallengeOut:
        c = session.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status_code=404, detail="Челлендж не найден")
        check_challenge(session, body)
        for key, value in body.model_dump().items():
            setattr(c, key, value)
        if c.is_active and c.published_at is None:  # создан выключенным — публикация сейчас
            c.published_at = utcnow()
        session.commit()
        d.admin_log.info("Админ %s изменил челлендж «%s»", d.who(admin), c.title)
        return challenge_out(session, c)

    @app.delete("/v1/admin/challenges/{challenge_id}", status_code=204)
    def delete_challenge(challenge_id: int, admin: AdminDep, session: SessionDep) -> Response:
        c = session.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status_code=404, detail="Челлендж не найден")
        session.delete(c)
        session.commit()
        d.admin_log.info("Админ %s удалил челлендж «%s»", d.who(admin), c.title)
        return Response(status_code=204)

    # ── админ: призы ──
    @app.get("/v1/admin/prizes", response_model=list[PoolOut])
    def admin_pools(_: AdminDep, session: SessionDep) -> list[PoolOut]:
        return [pool_out(session, p) for p in session.scalars(select(PromoPool).order_by(PromoPool.id.desc()))]

    @app.post("/v1/admin/prizes", response_model=PoolOut, status_code=201)
    def create_pool(body: PoolIn, admin: AdminDep, session: SessionDep) -> PoolOut:
        pool = PromoPool()
        added = apply_pool(session, pool, body)
        d.admin_log.info("Админ %s добавил приз «%s» (%s, кодов: %s)", d.who(admin), pool.title, pool.trigger,
                         "общий код" if pool.shared_code else added)
        return pool_out(session, pool)

    @app.put("/v1/admin/prizes/{pool_id}", response_model=PoolOut)
    def update_pool(pool_id: int, body: PoolIn, admin: AdminDep, session: SessionDep) -> PoolOut:
        pool = session.get(PromoPool, pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="Приз не найден")
        added = apply_pool(session, pool, body)
        d.admin_log.info("Админ %s изменил приз «%s»%s", d.who(admin), pool.title,
                         f", добавил кодов: {added}" if added else "")
        return pool_out(session, pool)

    @app.delete("/v1/admin/prizes/{pool_id}", status_code=204)
    def delete_pool(pool_id: int, admin: AdminDep, session: SessionDep) -> Response:
        """Удаляет приз и его коды; уже выданные коды у пользователей пропадают — мягче выключить."""
        pool = session.get(PromoPool, pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail="Приз не найден")
        for model in (PromoIssue, PromoCode):
            session.execute(model.__table__.delete().where(model.pool_id == pool_id))
        session.execute(Challenge.__table__.update().where(Challenge.reward_pool_id == pool_id)
                        .values(reward_pool_id=None))
        session.delete(pool)
        session.commit()
        d.admin_log.info("Админ %s удалил приз «%s»", d.who(admin), pool.title)
        return Response(status_code=204)

