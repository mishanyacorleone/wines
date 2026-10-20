"""«Хочу съездить»: винодельни, куда человек собирается (A11, «От бутылки — на винодельню»).

Справочник виноделен и туров — у api (/v1/wineries); здесь только список
человека. Как и дневник, копится только с аккаунтом: гость нажимает «Хочу
съездить» → вход → страница сохраняет сама.
"""

# Без `from __future__ import annotations`: модели запросов объявлены внутри mount
import logging
from datetime import datetime

from fastapi import FastAPI, Path
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import TripWish

logger = logging.getLogger("trips")
SLUG = r"^[a-z0-9][a-z0-9-]{0,254}$"


def mount(app: FastAPI, d) -> None:
    SessionDep, UserDep = d.SessionDep, d.UserDep

    class TripIn(BaseModel):
        name: str = Field(min_length=1, max_length=255)
        region: str | None = Field(default=None, max_length=120)
        image: str | None = Field(default=None, max_length=500)

    class TripOut(TripIn):
        winery: str
        created_at: datetime

    def out(t: TripWish) -> TripOut:
        return TripOut(winery=t.winery, name=t.name, region=t.region, image=t.image, created_at=t.created_at)

    @app.get("/v1/me/trips", response_model=list[TripOut])
    def list_trips(user: UserDep, session: SessionDep) -> list[TripOut]:
        rows = session.scalars(select(TripWish).where(TripWish.user_id == user.id)
                               .order_by(TripWish.created_at.desc())).all()
        return [out(t) for t in rows]

    @app.put("/v1/me/trips/{winery}", response_model=TripOut)
    def add_trip(body: TripIn, user: UserDep, session: SessionDep,
                 winery: str = Path(pattern=SLUG)) -> TripOut:
        trip = session.get(TripWish, (user.id, winery))
        if trip is None:
            trip = TripWish(user_id=user.id, winery=winery, **body.model_dump())
            session.add(trip)
            logger.info("Хочу съездить: пользователь %s — «%s»", d.who(user), body.name)
        else:
            trip.name, trip.region, trip.image = body.name, body.region, body.image
        session.commit()
        return out(trip)

    @app.delete("/v1/me/trips/{winery}", status_code=204)
    def remove_trip(user: UserDep, session: SessionDep, winery: str = Path(pattern=SLUG)) -> None:
        trip = session.get(TripWish, (user.id, winery))
        if trip is not None:
            session.delete(trip)
            session.commit()
