"""Схема БД и сессии SQLAlchemy.

Схема создаётся при старте (`create_all`): таблицы новые, миграций пока не
было. Когда схема начнёт меняться на живых данных — подключить Alembic.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSONB в Postgres, обычный JSON в SQLite (тесты)
_Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Вход по почте или телефону: заполнено одно из двух
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    phone: Mapped[str | None] = mapped_column(String(16), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    # Аватар: готовый (id из AVATAR_PRESETS) или загруженный файл
    avatar_preset: Mapped[str | None] = mapped_column(String(32))
    avatar_file: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @property
    def login(self) -> str:
        return self.email or self.phone or ""


class AuthSession(Base):
    __tablename__ = "sessions"

    # В базе только хэш токена: утечка таблицы не даёт войти чужим аккаунтом
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]

    user: Mapped[User] = relationship(lazy="joined")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # slug пуст, если вина нет в каталоге (status = not_found)
    slug: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16))  # match | not_found
    # Снимок карточки на момент скана: история и достижения не ходят в каталог
    # на каждый просмотр, а снятое с сайта вино не пропадает из истории
    wine: Mapped[dict | None] = mapped_column(_Json)
    photo_file: Mapped[str | None] = mapped_column(String(64))
    scanned_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Rating(Base):
    __tablename__ = "ratings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger)  # 1 — нравится, -1 — нет
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Код — у стартовых достижений: по нему сид не создаёт их повторно
    code: Mapped[str | None] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    image_file: Mapped[str | None] = mapped_column(String(64))
    # Условие — тип правила из achievements.RULES + порог (+ значение для
    # правил вида «вина из региона X»)
    rule_type: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[int] = mapped_column(Integer, default=1)
    rule_value: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class UserAchievement(Base):
    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "achievement_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievements.id", ondelete="CASCADE"))
    earned_at: Mapped[datetime] = mapped_column(default=utcnow)


class WineZone(Base):
    """Винный регион карты: название и субъекты РФ (коды ISO 3166-2), которые он занимает."""

    __tablename__ = "wine_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    subjects: Mapped[list] = mapped_column(_Json)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ZoneAlias(Base):
    """Сопоставление админа: region из каталога, не совпавший ни с одним регионом карты."""

    __tablename__ = "zone_aliases"

    catalog_region: Mapped[str] = mapped_column(String(255), primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("wine_zones.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    """Настройки, которые меняет админ без выкладки кода (шаблон текста «Поделиться»)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # TestClient ходит в приложение из другого потока
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
                from sqlalchemy.pool import StaticPool

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
