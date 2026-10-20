"""Схема БД и сессии SQLAlchemy.

Схема создаётся при старте (`create_all`): таблицы новые, миграций пока не
было. Когда схема начнёт меняться на живых данных — подключить Alembic.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    false,
    inspect,
    text,
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


# ── Вовлечение (19.10): дневник, вопрос дня, челленджи, призы, подарки ──────


class TastingNote(Base):
    """Запись дневника дегустации: «попробовал» (оценка, вкус, заметка) или «хочу попробовать»."""

    __tablename__ = "tasting_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(255), index=True)
    wine: Mapped[dict] = mapped_column(_Json)  # снимок карточки, как у скана
    kind: Mapped[str] = mapped_column(String(8), default="tasted")  # tasted | want
    rating: Mapped[int | None] = mapped_column(SmallInteger)  # 1–5
    tags: Mapped[list] = mapped_column(_Json, default=list)  # «ягоды», «дуб» — что почувствовал
    note: Mapped[str | None] = mapped_column(Text)
    occasion: Mapped[str | None] = mapped_column(String(200))  # где, с кем, с чем
    tasted_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class QuizQuestion(Base):
    """Вопрос дня от админа. Без даты — в ротации; на день без вопроса — вопрос по бутылке из каталога."""

    __tablename__ = "quiz_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(String(300))
    options: Mapped[list] = mapped_column(_Json)  # 2–4 варианта
    correct: Mapped[int] = mapped_column(SmallInteger)  # номер верного варианта, с 0
    explanation: Mapped[str | None] = mapped_column(Text)
    day: Mapped[date | None] = mapped_column(Date, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class QuizAnswer(Base):
    """Ответ на вопрос дня — один в день. Серия — дни с ответом подряд."""

    __tablename__ = "quiz_answers"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    question: Mapped[str] = mapped_column(String(300))  # «q:12» или «auto:<slug>:<вид>»
    choice: Mapped[int] = mapped_column(SmallInteger)
    correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(default=utcnow)


class PromoPool(Base):
    """Приз: промокоды (список или один общий) и за что их выдавать."""

    __tablename__ = "promo_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(80))  # «Скидка 10% в ВинЛаб»
    description: Mapped[str | None] = mapped_column(Text)  # условия: где и до какого числа действует
    shared_code: Mapped[str | None] = mapped_column(String(64))  # один код на всех; иначе — список PromoCode
    # За что выдавать сам по себе: серия вопроса дня из N дней | достижение | только за челленджи
    trigger: Mapped[str] = mapped_column(String(16), default="challenge")  # quiz_streak | achievement | challenge
    trigger_value: Mapped[int | None] = mapped_column(Integer)  # дней серии или id достижения
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PromoCode(Base):
    __tablename__ = "promo_codes"
    __table_args__ = (UniqueConstraint("pool_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("promo_pools.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    issued: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class PromoIssue(Base):
    """Выданный приз. source — за что («pool:3», «challenge:5»): один приз за одно и то же — один раз."""

    __tablename__ = "promo_issues"
    __table_args__ = (UniqueConstraint("user_id", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("promo_pools.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(200))  # «Серия вопроса дня: 7 дней»
    issued_at: Mapped[datetime] = mapped_column(default=utcnow)


class Challenge(Base):
    """Челлендж с датами: «Октябрь — месяц Дона: 3 вина из Долины Дона»."""

    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))  # wines | diary | quiz
    filter_field: Mapped[str | None] = mapped_column(String(16))  # region | grape | category | manufacturer
    filter_value: Mapped[str | None] = mapped_column(String(120))
    target: Mapped[int] = mapped_column(Integer, default=3)
    reward_pool_id: Mapped[int | None] = mapped_column(ForeignKey("promo_pools.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # прогресс считается с момента публикации (первого включения); галочка — засчитать и сделанное раньше
    count_before: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    published_at: Mapped[datetime | None] = mapped_column()


class TasteProfile(Base):
    """«Мой вкус»: что человек сам отметил — цвета, сладость, сорта, винодельни (как выбор жанров в Spotify)."""

    __tablename__ = "taste_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    colors: Mapped[list] = mapped_column(_Json, default=list)  # red | white | rose | orange | sparkling
    sugars: Mapped[list] = mapped_column(_Json, default=list)  # dry | semidry | semisweet | sweet
    grapes: Mapped[list] = mapped_column(_Json, default=list)
    manufacturers: Mapped[list] = mapped_column(_Json, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Gift(Base):
    """Подарочная открытка: ссылка на вино с пожеланием. Открыть может любой, у кого есть ссылка."""

    __tablename__ = "gifts"

    token: Mapped[str] = mapped_column(String(24), primary_key=True)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    from_name: Mapped[str] = mapped_column(String(40))
    slug: Mapped[str] = mapped_column(String(255))
    wine: Mapped[dict] = mapped_column(_Json)
    message: Mapped[str] = mapped_column(String(300))
    opens: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


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
        self._add_new_columns()

    def _add_new_columns(self) -> None:
        """create_all не трогает существующие таблицы: новые столбцы (с server_default или NULL) добавляем сами."""
        existing = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                have = {c["name"] for c in existing.get_columns(table.name)}
                for column in table.columns:
                    if column.name in have:
                        continue
                    kind = column.type.compile(dialect=self.engine.dialect)
                    default = column.server_default.arg if column.server_default is not None else None
                    sql = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}"
                    if default is not None:
                        sql += " DEFAULT " + str(default.compile(dialect=self.engine.dialect)
                                                 if hasattr(default, "compile") else f"'{default}'")
                    conn.execute(text(sql))

    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session


def make_database() -> Database:
    return Database(settings.db_url)
