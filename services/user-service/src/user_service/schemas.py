"""Схемы запросов и ответов сервиса пользователей."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AvatarPresetOut(BaseModel):
    id: str
    emoji: str
    background: str
    label: str


class AvatarOut(BaseModel):
    # Заполнено одно из двух: готовый аватар (id из /v1/avatars) или ссылка на файл
    preset: str | None = None
    url: str | None = None


class UserOut(BaseModel):
    id: int
    name: str
    login: str
    role: Literal["user", "admin"]
    avatar: AvatarOut
    created_at: datetime


class Stats(BaseModel):
    scans: int = Field(description="всех сканов, вместе с «нет в каталоге»")
    wines: int = Field(description="разных вин")
    regions: int = Field(description="разных регионов")
    regions_total: int = Field(default=0, description="регионов в каталоге (0 — каталог недоступен)")
    ratings: int = Field(description="оценённых вин")


class AchievementOut(BaseModel):
    id: int
    title: str
    description: str
    image_url: str | None
    rule_type: str
    rule_label: str
    threshold: int
    rule_value: str | None
    is_active: bool
    sort_order: int
    earned: bool = False
    earned_at: datetime | None = None
    progress: int = Field(default=0, description="сколько набрано из threshold")


class MeOut(BaseModel):
    user: UserOut
    stats: Stats
    achievements: list[AchievementOut]
    new_achievements: list[AchievementOut] = Field(
        default_factory=list, description="выданные этим запросом — показать поздравление"
    )


class RegisterRequest(BaseModel):
    login: str = Field(min_length=3, max_length=254, description="почта или телефон")
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=40)


class LoginRequest(BaseModel):
    login: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    avatar_preset: str | None = Field(default=None, max_length=32)


class WineSnapshot(BaseModel):
    slug: str
    title: str
    manufacturer: str | None = None
    region: str | None = None
    category: str | None = None
    grapes: list[str] = Field(default_factory=list)
    image_url: str | None = None
    url: str | None = None


class ScanOut(BaseModel):
    id: int
    slug: str | None
    status: Literal["match", "not_found"]
    wine: WineSnapshot | None
    photo_url: str | None
    scanned_at: datetime
    rating: int = Field(default=0, description="1 — нравится, -1 — нет, 0 — без оценки")


class ScanList(BaseModel):
    items: list[ScanOut]
    total: int


class ScanResult(BaseModel):
    scan: ScanOut
    new_achievements: list[AchievementOut] = Field(default_factory=list)
    new_region: str | None = Field(default=None, description="регион, впервые открытый этим сканом, — для винной карты")


class OpenedRegion(BaseModel):
    name: str = Field(description="винный регион карты; не сопоставленный админом — как в каталоге")
    on_map: bool = Field(default=True, description="false — регион ещё не сопоставлен с картой")
    wines: list[WineSnapshot] = Field(description="попробованные вина региона, последние — первыми")
    opened_at: datetime = Field(description="первый скан вина из региона")


class RegionsOut(BaseModel):
    regions: list[OpenedRegion]


class ScanUpdate(BaseModel):
    slug: str = Field(min_length=1, max_length=255, description="вино, которое пользователь выбрал сам")


class RatingRequest(BaseModel):
    value: Literal[-1, 0, 1]


class RatingResult(BaseModel):
    slug: str
    value: int
    new_achievements: list[AchievementOut] = Field(default_factory=list)


class ZoneOut(BaseModel):
    id: int
    name: str
    subjects: list[str] = Field(description="субъекты РФ, коды ISO 3166-2 (RU-KDA)")


class AliasOut(BaseModel):
    catalog_region: str = Field(description="region вина в каталоге")
    zone_id: int
    zone: str


class MapZonesOut(BaseModel):
    zones: list[ZoneOut]
    aliases: list[AliasOut]


class ZoneRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    subjects: list[str] = Field(min_length=1, max_length=30)


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    subjects: list[str] | None = Field(default=None, min_length=1, max_length=30)


class AliasRequest(BaseModel):
    catalog_region: str = Field(min_length=1, max_length=255)
    zone_id: int


class ShareSettings(BaseModel):
    wine_template: str = Field(
        min_length=1, max_length=600,
        description="текст «Поделиться вином»; маски {название} {сорт} {сахар} {цвет} {бренд} {регион} {ссылка}",
    )
    default_wine_template: str | None = Field(default=None, description="шаблон по умолчанию — для «Сбросить»")


class RuleOut(BaseModel):
    type: str
    label: str
    progress: str
    needs_value: bool
    value_hint: str


class RulesOut(BaseModel):
    rules: list[RuleOut]
    regions: list[str] = Field(description="регионы каталога — значения для правила region")


class HealthResponse(BaseModel):
    status: str
    users: int
    achievements: int
