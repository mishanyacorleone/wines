"""Винные регионы карты: какие субъекты РФ в них входят и как к ним относятся вина.

У вина в каталоге поле region — название винного региона («Кубань»). Если оно
совпадает с названием винного региона карты — вино на карте сразу. Если нет
(«Краснодарский край», «Сибирь»), админ сопоставляет его: относит к существующему
региону (алиас) или создаёт новый регион, отметив его субъекты на карте.
Сравнение — без учёта регистра, «ё», пробелов и вида тире.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import WineZone, ZoneAlias

# Стартовые винные регионы — те, что в каталоге «Своё Вино» на 18.10.2026.
# Дальше список ведёт админ (/app/#admin → «Регионы карты»)
SEED_ZONES: dict[str, list[str]] = {
    "Кубань": ["RU-KDA"],
    "Крым": ["UA-43", "UA-40"],                 # Республика Крым и Севастополь
    "Дагестан": ["RU-DA"],
    "Долина Дона": ["RU-ROS"],
    "Ставрополье": ["RU-STA"],
    "Нижняя Волга": ["RU-VGG", "RU-AST"],
    "Самара": ["RU-SAM"],
    "Северная Осетия — Алания": ["RU-SE"],
    "Дальневосточная зона": ["RU-KHA", "RU-PRI"],  # на сайте у зоны фото Хабаровска
}

SUBJECT_ID = re.compile(r"^(RU|UA)-[A-Z0-9]{2,3}$")


def norm(value: str | None) -> str:
    value = (value or "").strip().lower().replace("ё", "е")
    value = re.sub(r"\s*[—–-]\s*", "-", value)
    return re.sub(r"\s+", " ", value)


def resolver(session: Session) -> Callable[[str | None], str | None]:
    """region из каталога → название винного региона карты (None — не сопоставлено)."""
    zones = {norm(z.name): z.name for z in session.scalars(select(WineZone))}
    aliases = {
        norm(alias): name
        for alias, name in session.execute(select(ZoneAlias.catalog_region, WineZone.name).join(WineZone))
    }

    def resolve(region: str | None) -> str | None:
        key = norm(region)
        return zones.get(key) or aliases.get(key) if key else None

    return resolve


def ensure_zones(session: Session) -> None:
    """Стартовые регионы — только на пустой таблице: дальше их ведёт админ."""
    if session.scalar(select(WineZone.id).limit(1)) is not None:
        return
    for name, subjects in SEED_ZONES.items():
        session.add(WineZone(name=name, subjects=subjects))
    session.commit()
