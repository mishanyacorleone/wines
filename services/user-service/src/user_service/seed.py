"""Стартовые данные: администратор, готовые аватары, достижения для демо."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import media
from .db import Achievement, User
from .security import hash_password, parse_login

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent / "assets" / "achievements"


@dataclass(frozen=True)
class AvatarPreset:
    id: str
    emoji: str
    background: str
    label: str


# Готовые аватары: эмодзи на однотонном фоне — вино и винные регионы России
AVATAR_PRESETS: list[AvatarPreset] = [
    AvatarPreset("red-wine", "🍷", "#8F3D42", "Красное"),
    AvatarPreset("sparkling", "🥂", "#E8D48E", "Игристое"),
    AvatarPreset("bottle", "🍾", "#2F5D50", "Праздник"),
    AvatarPreset("grapes", "🍇", "#6B4E8C", "Виноград"),
    AvatarPreset("cheese", "🧀", "#F2C14E", "К сыру"),
    AvatarPreset("crimea", "🌊", "#2F6690", "Крым"),
    AvatarPreset("kuban", "🏞️", "#5E8C4A", "Кубань"),
    AvatarPreset("caucasus", "⛰️", "#7A8A99", "Кавказ"),
    AvatarPreset("don", "🌾", "#C9A24E", "Дон"),
]
AVATAR_IDS = {p.id for p in AVATAR_PRESETS}
DEFAULT_AVATAR = "grapes"


@dataclass(frozen=True)
class SeedAchievement:
    code: str
    title: str
    description: str
    rule_type: str
    threshold: int
    rule_value: str | None = None


# Пять достижений для демо — дальше их добавляет админ
SEED_ACHIEVEMENTS: list[SeedAchievement] = [
    SeedAchievement("first-scan", "Первый бокал", "Отсканируйте первое вино", "scans", 1),
    SeedAchievement("taster-10", "Дегустатор", "Распознайте 10 вин по фото", "scans", 10),
    SeedAchievement("traveler-3", "Винный путешественник", "Попробуйте вина из трёх регионов России", "regions", 3),
    SeedAchievement("crimea", "Крымский берег", "Отсканируйте вино из Крыма", "region", 1, "Крым"),
    SeedAchievement("critic-5", "Ценитель", "Оцените пять вин из истории сканов", "ratings", 5),
]


def ensure_admin(session: Session, login: str, password: str) -> None:
    """Создаёт администратора; заданный в окружении пароль применяет при каждом старте.

    Так пароль админа можно сменить, просто перезапустив сервис с новым
    WINE_ADMIN_PASSWORD. Без него пароль генерируется один раз и пишется в лог.
    """
    kind, value = parse_login(login)
    column = User.email if kind == "email" else User.phone
    admin = session.scalar(select(User).where(column == value))
    if admin is None:
        generated = not password
        password = password or secrets.token_urlsafe(9)
        admin = User(name="Администратор", role="admin", avatar_preset="red-wine",
                     password_hash=hash_password(password), **{kind: value})
        session.add(admin)
        session.commit()
        if generated:
            logger.warning("Создан администратор %s, пароль: %s (задайте WINE_ADMIN_PASSWORD)", value, password)
        else:
            logger.info("Создан администратор %s", value)
        return
    changed = False
    if admin.role != "admin":
        admin.role, changed = "admin", True
    if password:
        admin.password_hash, changed = hash_password(password), True
    if changed:
        session.commit()


def ensure_achievements(session: Session, media_dir: Path) -> None:
    """Стартовые достижения — только на пустой таблице, при первом запуске.

    Дальше список ведёт админ: удалённое им стартовое достижение после
    перезапуска не возвращается.
    """
    if session.scalar(select(Achievement.id).limit(1)) is not None:
        return
    added = 0
    for order, seed in enumerate(SEED_ACHIEVEMENTS):
        svg = _ASSETS / f"{seed.code}.svg"
        session.add(Achievement(
            code=seed.code, title=seed.title, description=seed.description,
            rule_type=seed.rule_type, threshold=seed.threshold, rule_value=seed.rule_value,
            sort_order=(order + 1) * 10,
            image_file=media.copy_seed_svg(svg, media_dir) if svg.exists() else None,
        ))
        added += 1
    if added:
        session.commit()
        logger.info("Добавлено стартовых достижений: %d", added)
