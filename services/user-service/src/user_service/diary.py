"""Дневник дегустации: «попробовал» и «хочу попробовать».

Запись «попробовал» — оценка 1–5, что почувствовал (подсказки-чипы: ягоды, дуб,
кислотность…), заметка, «где, с кем, с чем». Чипы — первые сведения о вкусе
пользователя: по ним потом можно сравнивать вина и советовать.
«Хочу попробовать» — вино на потом, например из подарочной открытки; когда
человек его попробует и запишет, «хочу» превращается в «попробовал».

Без аккаунта дневника нет — страница предлагает войти и после входа сама
открывает запись того же вина.
"""

# Без `from __future__ import annotations`: модели запросов объявлены внутри mount
import logging
from datetime import date, datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import rewards
from .dates import today
from .db import TastingNote, utcnow

logger = logging.getLogger("diary")


def add_want(session: Session, user_id: int, slug: str, wine: dict, occasion: str | None) -> TastingNote:
    """«Хочу попробовать» — если вина ещё нет в дневнике; иначе существующая запись."""
    existing = session.scalar(select(TastingNote).where(TastingNote.user_id == user_id, TastingNote.slug == slug)
                              .order_by(TastingNote.created_at.desc()).limit(1))
    if existing is not None:
        return existing
    note = TastingNote(user_id=user_id, slug=slug, wine=wine, kind="want", tags=[], occasion=occasion)
    session.add(note)
    session.commit()
    return note


def mount(app: FastAPI, d) -> None:
    SessionDep, UserDep, CatalogDep = d.SessionDep, d.UserDep, d.CatalogDep

    class NoteIn(BaseModel):
        kind: Literal["tasted", "want"] = "tasted"
        rating: int | None = Field(default=None, ge=1, le=5)
        tags: list[str] = Field(default_factory=list, max_length=12)
        note: str | None = Field(default=None, max_length=2000)
        occasion: str | None = Field(default=None, max_length=200)
        tasted_on: date | None = None

        @field_validator("tags")
        @classmethod
        def clean_tags(cls, value: list[str]) -> list[str]:
            items = list(dict.fromkeys(v.strip() for v in value if v.strip()))
            if any(len(v) > 30 for v in items):
                raise ValueError("Подсказка вкуса — до 30 символов")
            return items

    class NoteCreate(NoteIn):
        slug: str = Field(min_length=1, max_length=255)

    class NoteOut(BaseModel):
        id: int
        slug: str
        wine: dict
        kind: str
        rating: int | None
        tags: list[str]
        note: str | None
        occasion: str | None
        tasted_on: date | None
        created_at: datetime

    class NoteResult(BaseModel):
        entry: NoteOut
        new_achievements: list = []
        new_prizes: list = []

    class NoteList(BaseModel):
        items: list[NoteOut]
        total: int
        tasted: int
        want: int

    def out(n: TastingNote) -> NoteOut:
        return NoteOut(id=n.id, slug=n.slug, wine=n.wine or {}, kind=n.kind, rating=n.rating, tags=n.tags or [],
                       note=n.note, occasion=n.occasion, tasted_on=n.tasted_on, created_at=n.created_at)

    def fill(n: TastingNote, body: NoteIn) -> None:
        n.kind = body.kind
        tasted = body.kind == "tasted"
        n.rating = body.rating if tasted else None
        n.tags = body.tags if tasted else []
        n.note = (body.note or "").strip() or None
        n.occasion = (body.occasion or "").strip() or None
        n.tasted_on = (body.tasted_on or today()) if tasted else None

    def own(session: Session, user, note_id: int) -> TastingNote:
        n = session.get(TastingNote, note_id)
        if n is None or n.user_id != user.id:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        return n

    def rewards_after(session: Session, user) -> tuple[list, list]:
        return d.award(session, user), [rewards.prize_out(session, p) for p in rewards.check(session, user.id)]

    @app.get("/v1/me/diary", response_model=NoteList)
    def list_notes(
        user: UserDep, session: SessionDep,
        kind: Literal["tasted", "want"] | None = None,
        limit: int = Query(default=10, ge=1, le=50), offset: int = Query(default=0, ge=0),
    ) -> NoteList:
        base = select(TastingNote).where(TastingNote.user_id == user.id)
        counts = dict(session.execute(
            select(TastingNote.kind, func.count()).where(TastingNote.user_id == user.id).group_by(TastingNote.kind)
        ).all())
        query = base.where(TastingNote.kind == kind) if kind else base
        rows = session.scalars(query.order_by(TastingNote.created_at.desc(), TastingNote.id.desc())
                               .limit(limit).offset(offset)).all()
        total = counts.get(kind, 0) if kind else sum(counts.values())
        return NoteList(items=[out(n) for n in rows], total=total, tasted=counts.get("tasted", 0),
                        want=counts.get("want", 0))

    @app.post("/v1/me/diary", response_model=NoteResult, status_code=201)
    def create_note(body: NoteCreate, user: UserDep, session: SessionDep, catalog: CatalogDep) -> NoteResult:
        wine = d.lookup_wine(catalog, body.slug)
        if body.kind == "want":
            n = add_want(session, user.id, body.slug, wine, body.occasion)
            return NoteResult(entry=out(n))
        # Попробовал вино из «хочу» — та же запись становится дегустацией
        n = session.scalar(select(TastingNote).where(TastingNote.user_id == user.id, TastingNote.slug == body.slug,
                                                     TastingNote.kind == "want").limit(1))
        if n is None:
            n = TastingNote(user_id=user.id, slug=body.slug, wine=wine)
            session.add(n)
        fill(n, body)
        n.created_at = utcnow()  # «хочу» стало дегустацией сегодня: наверх списка и в челлендж этого месяца
        session.commit()
        logger.info("Дневник: пользователь %s — «%s», оценка %s", d.who(user), wine["title"], n.rating or "—")
        achievements, prizes = rewards_after(session, user)
        return NoteResult(entry=out(n), new_achievements=achievements, new_prizes=prizes)

    @app.put("/v1/me/diary/{note_id}", response_model=NoteResult)
    def update_note(note_id: int, body: NoteIn, user: UserDep, session: SessionDep) -> NoteResult:
        n = own(session, user, note_id)
        fill(n, body)
        session.commit()
        achievements, prizes = rewards_after(session, user)
        return NoteResult(entry=out(n), new_achievements=achievements, new_prizes=prizes)

    @app.delete("/v1/me/diary/{note_id}", status_code=204)
    def delete_note(note_id: int, user: UserDep, session: SessionDep) -> Response:
        session.delete(own(session, user, note_id))
        session.commit()
        return Response(status_code=204)
