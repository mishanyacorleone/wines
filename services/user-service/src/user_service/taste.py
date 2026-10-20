"""«Сомелье, который вас помнит»: понравится ли человеку это вино — и почему.

Два источника, оба объяснимые:
- «Мой вкус» — что человек сам отметил (цвет, сладость, сорта, винодельни), как жанры в Spotify;
- его оценки: звёзды дневника и 👍/👎 в истории сканов. Похожее на то, что понравилось, — плюс,
  на то, что не понравилось, — минус.
Сравнение — по признакам карточки (цвет, сладость, сорт, винодельня, регион), не по векторам SigLIP:
те про вид этикетки, а не про вкус. Ответ всегда с причиной. Пока данных мало (профиль пуст и меньше
трёх оценок) — процента нет, страница предлагает рассказать о вкусе.
"""

# Без `from __future__ import annotations`: модели запросов объявлены внутри mount
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import CatalogUnavailable
from .dates import local_day
from .db import Rating, Scan, TasteProfile, TastingNote, utcnow

logger = logging.getLogger("taste")

COLORS = {"red": "красное", "white": "белое", "rose": "розовое", "orange": "оранжевое", "sparkling": "игристое"}
SUGARS = {"dry": "сухое", "semidry": "полусухое", "semisweet": "полусладкое", "sweet": "сладкое"}
_COLOR_WORDS = {"красное": "red", "белое": "white", "розовое": "rose", "оранжевое": "orange"}
# брют и экстра брют — сухие игристые
_SUGAR_WORDS = {"сухое": "dry", "полусухое": "semidry", "полусладкое": "semisweet", "сладкое": "sweet",
                "брют": "dry", "экстра брют": "dry"}
_SUGAR_ORDER = ["dry", "semidry", "semisweet", "sweet"]

# Сколько весит оценка: звёзды дневника и 👍/👎 истории
STAR_WEIGHT = {5: 1.0, 4: 0.6, 3: 0.0, 2: -0.6, 1: -1.0}
LIKE_WEIGHT = 0.7
# Оценка уже выпитого вина → процент
STAR_PERCENT = {5: 95, 4: 82, 3: 55, 2: 28, 1: 10}
SIMILAR = 0.45  # оценённое вино считается похожим от этой доли совпадения признаков
MIN_RATED = 3  # без профиля — со скольких оценок начинаем угадывать
EXPERIENCE_WEIGHT = 1.5  # свои оценки весомее отмеченного в профиле


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


@dataclass(frozen=True)
class Features:
    color: str | None
    sparkling: bool
    sugar: str | None
    grapes: frozenset[str]
    region: str
    maker: str

    @property
    def style(self) -> str:
        """«сухое красное», «розовое игристое» — как сказать о вине словами."""
        color = COLORS.get(self.color or "", "")
        if self.sparkling:
            return f"{color} игристое".strip()
        return " ".join(x for x in (SUGARS.get(self.sugar or ""), color) if x)


def features(wine: dict) -> Features:
    words = _norm(wine.get("category")).split(" ", 1)
    sugar_word = words[1] if len(words) > 1 else ""
    return Features(
        color=_COLOR_WORDS.get(words[0]),
        sparkling=sugar_word in ("брют", "экстра брют"),
        sugar=_SUGAR_WORDS.get(sugar_word),
        grapes=frozenset(_norm(g) for g in wine.get("grapes") or [] if g),
        region=_norm(wine.get("region")),
        maker=_norm(wine.get("manufacturer")),
    )


def _sugar_gap(a: str | None, b: str | None) -> int | None:
    if a not in _SUGAR_ORDER or b not in _SUGAR_ORDER:
        return None
    return abs(_SUGAR_ORDER.index(a) - _SUGAR_ORDER.index(b))


def similarity(a: Features, b: Features) -> tuple[float, list[str]]:
    """Доля общих признаков 0…1 и что именно общего — для причины."""
    score, common = 0.0, []
    if a.color and a.color == b.color and a.sparkling == b.sparkling:
        score += 0.3
        gap = _sugar_gap(a.sugar, b.sugar)
        if gap == 0:
            score += 0.2
            common.append(f"тоже {a.style}")
        elif gap == 1:
            score += 0.1
    shared = a.grapes & b.grapes
    if shared and a.grapes and b.grapes:
        score += 0.3 * len(shared) / min(len(a.grapes), len(b.grapes))
        common.insert(0, "тот же сорт")
    if a.maker and a.maker == b.maker:
        score += 0.1
        common.append("та же винодельня")
    if a.region and a.region == b.region:
        score += 0.1
        if not common:
            common.append("тот же регион")
    return min(score, 1.0), common


@dataclass
class Rated:
    slug: str
    title: str
    weight: float  # −1…1
    stars: int | None  # звёзды дневника; None — 👍/👎
    features: Features


def rated_wines(session: Session, user_id: int, catalog) -> list[Rated]:
    """Оценённые вина человека: звёзды дневника важнее 👍/👎 того же вина."""
    out: dict[str, Rated] = {}
    notes = session.scalars(select(TastingNote).where(
        TastingNote.user_id == user_id, TastingNote.kind == "tasted", TastingNote.rating.is_not(None))
        .order_by(TastingNote.created_at)).all()
    for n in notes:
        out[n.slug] = Rated(n.slug, (n.wine or {}).get("title") or n.slug, STAR_WEIGHT.get(n.rating, 0.0), n.rating,
                            features(n.wine or {}))
    likes = session.scalars(select(Rating).where(Rating.user_id == user_id, Rating.value != 0)).all()
    for r in likes:
        if r.slug in out:
            continue
        wine = session.scalar(select(Scan.wine).where(Scan.user_id == user_id, Scan.slug == r.slug)
                              .order_by(Scan.scanned_at.desc()).limit(1))
        if not wine:
            try:
                wine = catalog.get(r.slug) if catalog is not None else None
            except CatalogUnavailable:
                wine = None
        if wine:
            out[r.slug] = Rated(r.slug, wine.get("title") or r.slug, LIKE_WEIGHT * (1 if r.value > 0 else -1), None,
                                features(wine))
    return list(out.values())


def _profile_dims(profile: TasteProfile | None, f: Features) -> list[tuple[float, str]]:
    """Совпадение с «Моим вкусом» по каждому признаку: (−0,7…1, причина)."""
    if profile is None:
        return []
    dims: list[tuple[float, str]] = []
    colors, sugars = set(profile.colors or []), set(profile.sugars or [])
    color = sugar = None
    if colors and f.color:
        if f.sparkling:
            value = 1.0 if "sparkling" in colors else 0.2 if f.color in colors else -0.7
        else:
            value = 1.0 if f.color in colors else -0.7
        chosen = ", ".join(COLORS[c] for c in COLORS if c in colors)
        color = (value, f"вы любите {COLORS['sparkling'] if f.sparkling else COLORS[f.color]}" if value > 0.5
                 else f"вы отметили {chosen}, а это {f.style}")
    if sugars and f.sugar:
        gap = min(_sugar_gap(f.sugar, s) for s in sugars)
        value = 1.0 if gap == 0 else 0.0 if gap == 1 else -0.7
        chosen = ", ".join(SUGARS[s] for s in _SUGAR_ORDER if s in sugars)
        sugar = (value, f"вы любите {SUGARS[f.sugar]}" if value > 0.5
                 else f"это {SUGARS[f.sugar]}, а вы отмечали {chosen}")
    if color and sugar and color[0] > 0.5 and sugar[0] > 0.5:
        # цвет и сладость совпали — одна причина «вы любите сухое красное», но вес обоих признаков
        dims += [(1.0, f"вы любите {f.style}"), (1.0, "")]
    else:
        dims += [d for d in (color, sugar) if d]
    grapes = {_norm(g): g for g in profile.grapes or []}
    shared = [grapes[g] for g in f.grapes if g in grapes]
    if shared:
        dims.append((1.0, f"ваш сорт — {shared[0]}"))
    makers = {_norm(m) for m in profile.manufacturers or []}
    if f.maker and f.maker in makers:
        dims.append((1.0, "винодельня из ваших любимых"))
    return dims


def _has_profile(profile: TasteProfile | None) -> bool:
    return bool(profile and (profile.colors or profile.sugars or profile.grapes or profile.manufacturers))


def verdict(percent: int) -> tuple[str, str]:
    if percent >= 75:
        return "Вам понравится", "good"
    if percent >= 58:
        return "Скорее понравится", "good"
    if percent > 42:
        return "Может понравиться", "maybe"
    return "Скорее не ваше", "bad"


def _tried(session: Session, user_id: int, slug: str) -> dict | None:
    """Это вино человек уже пил (дневник), хотел попробовать или сканировал."""
    note = session.scalar(select(TastingNote).where(TastingNote.user_id == user_id, TastingNote.slug == slug)
                          .order_by(TastingNote.created_at.desc()).limit(1))
    if note is not None and note.kind == "tasted":
        day = note.tasted_on or local_day(note.created_at)
        return {"kind": "tasted", "date": day.isoformat(), "rating": note.rating, "tags": (note.tags or [])[:3],
                "note": (note.note or "")[:120] or None}
    scans = session.scalars(select(Scan.scanned_at).where(Scan.user_id == user_id, Scan.slug == slug)
                            .order_by(Scan.scanned_at)).all()
    like = session.get(Rating, (user_id, slug))
    if note is not None:  # «хочу попробовать»
        return {"kind": "want", "date": local_day(note.created_at).isoformat(), "scans": len(scans)}
    # текущий скан уже в истории: «сканировали раньше» — только если был скан до сегодняшнего
    earlier = [s for s in scans if (utcnow() - _aware(s)).total_seconds() > 600]
    if earlier:
        return {"kind": "scanned", "date": local_day(earlier[0]).isoformat(), "scans": len(scans),
                "like": like.value if like and like.value else None}
    return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def match(session: Session, user_id: int, wine: dict, catalog=None) -> dict:
    """Понравится ли вино: процент, вердикт, причины (до двух) и «вы уже пили»."""
    slug = wine.get("slug")
    profile = session.get(TasteProfile, user_id)
    rated = rated_wines(session, user_id, catalog)
    tried = _tried(session, user_id, slug) if slug else None
    out = {"state": "need_data", "percent": None, "verdict": None, "tone": None, "reasons": [], "tried": tried,
           "rated": len(rated), "has_profile": _has_profile(profile)}

    own = next((r for r in rated if r.slug == slug), None)
    if own is not None and own.stars:  # уже пил и оценил — ответ его же оценкой
        percent = STAR_PERCENT[own.stars]
        out.update(state="ready", percent=percent, verdict=verdict(percent)[0], tone=verdict(percent)[1],
                   reasons=[f"Вы поставили этому вину {own.stars}★"])
        return out
    if not out["has_profile"] and len(rated) < MIN_RATED:
        return out

    f = features(wine)
    dims = _profile_dims(profile, f)
    evidence = []  # (вес оценки × похожесть, похожесть, причина)
    for r in rated:
        if r.slug == slug:
            continue
        sim, common = similarity(f, r.features)
        if sim >= SIMILAR and r.weight:
            evidence.append((r, sim, common))
    sims = sum(sim for _, sim, _ in evidence)
    if not dims and not evidence:
        out["state"] = "unknown"
        return out

    profile_part = sum(v for v, _ in dims) / len(dims) if dims else 0.0
    experience = sum(r.weight * sim for r, sim, _ in evidence) / sims if sims else 0.0
    confidence = min(1.0, sims / 1.5) * EXPERIENCE_WEIGHT
    weight_p = 1.0 if dims else 0.0
    score = (weight_p * profile_part + confidence * experience) / (weight_p + confidence)
    percent = max(5, min(97, round(50 + 45 * score)))

    reasons: list[tuple[float, str]] = []  # (сила со знаком, текст)
    for r, sim, common in evidence:
        how = (f"которому вы поставили {r.stars}★" if r.stars and r.weight > 0
               else f"— вы поставили {r.stars}★" if r.stars
               else "которое вам понравилось" if r.weight > 0 else "— не понравилось")
        detail = f": {', '.join(common[:2])}" if common else ""
        reasons.append((r.weight * sim * 1.2, f"похоже на «{r.title}», {how}{detail}" if r.weight > 0
                        else f"похоже на «{r.title}» {how}{detail}"))
    reasons += [(v, text) for v, text in dims if v != 0 and text]
    lead = 1 if score >= 0 else -1
    reasons.sort(key=lambda x: (-(x[0] * lead > 0), -abs(x[0])))
    texts = list(dict.fromkeys(t for _, t in reasons))[:2]
    out.update(state="ready", percent=percent, verdict=verdict(percent)[0], tone=verdict(percent)[1],
               reasons=[t[0].upper() + t[1:] for t in texts])
    return out


def mount(app: FastAPI, d) -> None:
    SessionDep, UserDep, CatalogDep = d.SessionDep, d.UserDep, d.CatalogDep

    class ProfileIn(BaseModel):
        colors: list[Literal["red", "white", "rose", "orange", "sparkling"]] = Field(default_factory=list)
        sugars: list[Literal["dry", "semidry", "semisweet", "sweet"]] = Field(default_factory=list)
        grapes: list[str] = Field(default_factory=list, max_length=40)
        manufacturers: list[str] = Field(default_factory=list, max_length=40)

        @field_validator("colors", "sugars", "grapes", "manufacturers")
        @classmethod
        def unique(cls, value: list[str]) -> list[str]:
            items = list(dict.fromkeys(v.strip() for v in value if v and v.strip()))
            if any(len(v) > 120 for v in items):
                raise ValueError("Слишком длинное название")
            return items

    class ProfileOut(ProfileIn):
        rated: int = Field(description="оценённых вин — звёзды дневника и 👍/👎")
        ready: bool = Field(description="хватает ли данных, чтобы угадывать")

    class MatchOut(BaseModel):
        state: Literal["ready", "need_data", "unknown"]
        percent: int | None
        verdict: str | None
        tone: Literal["good", "maybe", "bad"] | None
        reasons: list[str]
        tried: dict | None
        rated: int
        has_profile: bool

    def profile_out(session: Session, user_id: int, catalog) -> ProfileOut:
        p = session.get(TasteProfile, user_id)
        rated = len(rated_wines(session, user_id, catalog))
        return ProfileOut(colors=p.colors if p else [], sugars=p.sugars if p else [], grapes=p.grapes if p else [],
                          manufacturers=p.manufacturers if p else [], rated=rated,
                          ready=_has_profile(p) or rated >= MIN_RATED)

    @app.get("/v1/me/taste", response_model=ProfileOut)
    def get_profile(user: UserDep, session: SessionDep, catalog: CatalogDep) -> ProfileOut:
        return profile_out(session, user.id, catalog)

    @app.put("/v1/me/taste", response_model=ProfileOut)
    def put_profile(body: ProfileIn, user: UserDep, session: SessionDep, catalog: CatalogDep) -> ProfileOut:
        p = session.get(TasteProfile, user.id)
        if p is None:
            p = TasteProfile(user_id=user.id)
            session.add(p)
        p.colors, p.sugars, p.grapes, p.manufacturers = body.colors, body.sugars, body.grapes, body.manufacturers
        session.commit()
        logger.info("«Мой вкус»: пользователь %s — %s; сортов %d, виноделен %d", d.who(user),
                    ", ".join([COLORS[c] for c in body.colors] + [SUGARS[s] for s in body.sugars]) or "без стиля",
                    len(body.grapes), len(body.manufacturers))
        return profile_out(session, user.id, catalog)

    @app.get("/v1/me/taste/match", response_model=MatchOut)
    def get_match(user: UserDep, session: SessionDep, catalog: CatalogDep,
                  slug: str = Query(min_length=1, max_length=255)) -> MatchOut:
        """Понравится ли вино этому человеку — строка над карточкой после скана."""
        return MatchOut(**match(session, user.id, d.lookup_wine(catalog, slug), catalog))
