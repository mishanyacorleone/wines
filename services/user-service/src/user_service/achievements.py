"""Достижения: правила, подсчёт по истории, выдача новых.

Админ не пишет код условия, а собирает его из готовых правил: тип + порог
(+ значение, например регион). Новое правило — одна строка в RULES и одна
ветка в `_stat`.

Выданное достижение не отзывается: если пользователь поправил вино в скане
или снял оценку, значок остаётся — так честнее по отношению к нему.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import rewards, zones
from .db import Achievement, Rating, Scan, TastingNote, UserAchievement


@dataclass(frozen=True)
class Rule:
    label: str          # как правило читается в админке: «{label} ≥ {threshold}»
    progress: str       # подпись прогресса у пользователя
    needs_value: bool = False
    value_hint: str = ""


RULES: dict[str, Rule] = {
    "scans": Rule("Распознанных сканов", "сканов"),
    "wines": Rule("Разных вин", "вин"),
    "regions": Rule("Разных регионов", "регионов"),
    "region": Rule("Вин из региона", "вин из региона", True, "Регион, как в каталоге: Крым"),
    "grapes": Rule("Сортов винограда", "сортов"),
    "manufacturers": Rule("Разных виноделен", "виноделен"),
    "category": Rule("Вин категории", "вин категории", True, "Часть категории: Игристое, Розовое"),
    "ratings": Rule("Оценённых вин", "оценок"),
    "diary": Rule("Записей в дневнике дегустации", "записей"),
    "quiz_streak": Rule("Дней подряд в «Вопросе дня» (лучшая серия)", "дней подряд"),
}


@dataclass
class UserStats:
    scans: int               # распознанные сканы — для правила «scans»
    total_scans: int         # все сканы, вместе с «нет в каталоге» — для профиля
    wines: list[dict]        # уникальные вина из распознанных сканов, снимки карточек
    ratings: int
    diary: int = 0           # записи «попробовал» в дневнике
    best_streak: int = 0     # лучшая серия «Вопроса дня»
    # region вина → винный регион карты (с учётом сопоставлений админа)
    resolve: Callable[[str | None], str | None] = field(default=lambda region: None)

    def region_of(self, wine: dict) -> str | None:
        """Винный регион вина: регион карты, а не сопоставленный — как в каталоге."""
        return self.resolve(wine.get("region")) or wine.get("region")

    @property
    def regions(self) -> set[str]:
        return {r for w in self.wines if (r := self.region_of(w))}

    def summary(self) -> dict[str, int]:
        return {
            "scans": self.total_scans,
            "wines": len(self.wines),
            "regions": len(self.regions),
            "ratings": self.ratings,
        }


def user_stats(session: Session, user_id: int) -> UserStats:
    rows = session.execute(
        select(Scan.slug, Scan.wine).where(Scan.user_id == user_id, Scan.slug.is_not(None))
    ).all()
    wines: dict[str, dict] = {}
    for slug, wine in rows:
        wines.setdefault(slug, wine or {})
    total = session.scalar(select(func.count()).select_from(Scan).where(Scan.user_id == user_id)) or 0
    ratings = session.scalar(select(func.count()).select_from(Rating).where(Rating.user_id == user_id)) or 0
    diary = session.scalar(select(func.count()).select_from(TastingNote).where(
        TastingNote.user_id == user_id, TastingNote.kind == "tasted")) or 0
    return UserStats(scans=len(rows), total_scans=total, wines=list(wines.values()), ratings=ratings,
                     diary=diary, best_streak=rewards.streaks(session, user_id)[1],
                     resolve=zones.resolver(session))


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def _stat(achievement: Achievement, stats: UserStats) -> int:
    rule, value = achievement.rule_type, _norm(achievement.rule_value)
    wines = stats.wines
    if rule == "scans":
        return stats.scans
    if rule == "wines":
        return len(wines)
    if rule == "regions":
        return len(stats.regions)
    if rule == "region":
        return sum(1 for w in wines if zones.norm(stats.region_of(w)) == zones.norm(value))
    if rule == "grapes":
        return len({_norm(g) for w in wines for g in w.get("grapes") or []})
    if rule == "manufacturers":
        return len({_norm(w.get("manufacturer")) for w in wines if w.get("manufacturer")})
    if rule == "category":
        return sum(1 for w in wines if value and value in _norm(w.get("category")))
    if rule == "ratings":
        return stats.ratings
    if rule == "diary":
        return stats.diary
    if rule == "quiz_streak":
        return stats.best_streak
    return 0


def progress(achievement: Achievement, stats: UserStats) -> int:
    """Сколько набрано из порога (не больше порога)."""
    return min(_stat(achievement, stats), achievement.threshold)


def award(session: Session, user_id: int) -> list[Achievement]:
    """Выдаёт достижения, условия которых уже выполнены. Возвращает новые.

    Вызывается после скана, правки скана, оценки и при открытии профиля —
    последнее подхватывает достижения, которые админ добавил позже.
    """
    earned = set(
        session.scalars(select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id))
    )
    candidates = session.scalars(
        select(Achievement).where(Achievement.is_active.is_(True)).order_by(Achievement.sort_order, Achievement.id)
    ).all()
    pending = [a for a in candidates if a.id not in earned]
    if not pending:
        return []
    stats = user_stats(session, user_id)
    new = [a for a in pending if _stat(a, stats) >= a.threshold]
    if not new:
        return []
    for achievement in new:
        session.add(UserAchievement(user_id=user_id, achievement_id=achievement.id))
    try:
        session.commit()
    except IntegrityError:
        # Два запроса одного пользователя выдали то же достижение одновременно:
        # оно уже записано другим запросом, показывать его второй раз не нужно
        session.rollback()
        return []
    return new
