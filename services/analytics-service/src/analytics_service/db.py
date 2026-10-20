"""Таблица событий. Схема создаётся при старте (`create_all`)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Event(Base):
    """Одно действие на странице: скан, «поделиться», открыл карту, регистрация…"""

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    # Посетитель — случайный id в браузере: считает уникальных и вернувшихся без входа
    anon_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(45))
    props: Mapped[dict] = mapped_column(_Json, default=dict)
    # Демо-данные для показа дашборда: помечены и удаляются одной кнопкой
    demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            from sqlalchemy.pool import StaticPool

            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session


def make_database() -> Database:
    return Database(settings.db_url)
