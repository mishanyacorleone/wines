"""Сервис пользователей: аккаунт, профиль, история сканов, оценки, достижения.

Отдельный микросервис со своей БД (PostgreSQL). Распознавание о пользователях
ничего не знает: сканер сначала получает ответ сервиса поиска, затем — если
человек вошёл — записывает скан сюда. Сканировать можно и без аккаунта.

Наружу оба сервиса смотрят через шлюз (infra/gateway): один адрес и порт,
cookie сессии работает без CORS.
"""

# Без `from __future__ import annotations`: зависимости (SessionDep, UserDep)
# объявлены внутри фабрики, и FastAPI не смог бы разрешить их из строк
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from . import achievements as ach
from . import media, zones
from .catalog import Catalog, CatalogUnavailable, QdrantCatalog
from .config import settings
from .db import (
    Achievement,
    AuthSession,
    Database,
    Rating,
    Scan,
    Setting,
    User,
    UserAchievement,
    WineZone,
    ZoneAlias,
    make_database,
    utcnow,
)
from .schemas import (
    AchievementOut,
    AliasOut,
    AliasRequest,
    AvatarOut,
    AvatarPresetOut,
    HealthResponse,
    LoginRequest,
    MapZonesOut,
    MeOut,
    ProfileUpdate,
    RatingRequest,
    OpenedRegion,
    RatingResult,
    RegionsOut,
    RegisterRequest,
    RuleOut,
    RulesOut,
    ScanList,
    ScanOut,
    ScanResult,
    ScanUpdate,
    ShareSettings,
    Stats,
    UserOut,
    WineSnapshot,
    ZoneOut,
    ZoneRequest,
    ZoneUpdate,
)
from .security import LoginError, hash_password, new_token, parse_login, token_hash, verify_password
from .seed import AVATAR_IDS, AVATAR_PRESETS, DEFAULT_AVATAR, ensure_achievements, ensure_admin

logger = logging.getLogger(__name__)

# Текст «Поделиться вином» по умолчанию; админ меняет его в /app/#admin → «Поделиться».
# Маски подставляет страница (share.js), пустые — убираются вместе с запятой
DEFAULT_WINE_TEMPLATE = "Моё вино: «{название}», {бренд} — нашлось по фото в «Своё Вино»"
WINE_TEMPLATE_KEY = "share.wine_template"

# Сколько раз за 5 минут можно ошибиться паролем с одного адреса — дальше 429.
# Счётчик в памяти процесса: сервис один, при перезапуске он обнуляется
_MAX_FAILED_LOGINS = 10
_FAILED_WINDOW_S = 300


def create_app(
    database: Database | None = None,
    catalog: Catalog | None = None,
    media_dir: Path | None = None,
) -> FastAPI:
    """Фабрика приложения: тесты подставляют SQLite и каталог-заглушку."""
    db = database or make_database()
    media_root = media_dir or settings.media_dir
    failed_logins: dict[str, list[float]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        _wait_for_db(db)
        db.create_schema()
        with next(db.session()) as session:
            ensure_admin(session, settings.admin_login, settings.admin_password)
            ensure_achievements(session, media_root)
            zones.ensure_zones(session)
        app.state.catalog = catalog or QdrantCatalog(settings.qdrant_url, settings.collection)
        logger.info("Сервис пользователей готов")
        yield
        db.engine.dispose()

    app = FastAPI(
        title="Пользователи «Своё Вино»",
        description="Аккаунт, история сканов, оценки и достижения.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── Зависимости ──────────────────────────────────────────────────────
    def get_session():
        yield from db.session()

    SessionDep = Annotated[Session, Depends(get_session)]

    def optional_user(
        session: SessionDep,
        token: Annotated[str | None, Cookie(alias=settings.session_cookie)] = None,
    ) -> User | None:
        if not token:
            return None
        auth = session.get(AuthSession, token_hash(token))
        if auth is None or _aware(auth.expires_at) < utcnow():
            return None
        return auth.user

    def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
        if user is None:
            raise HTTPException(status_code=401, detail="Войдите в аккаунт")
        return user

    def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Только для администратора")
        return user

    UserDep = Annotated[User, Depends(current_user)]
    AdminDep = Annotated[User, Depends(admin_user)]

    def get_catalog() -> Catalog:
        return app.state.catalog

    # ── Представления ────────────────────────────────────────────────────
    def media_url(kind: str, name: str | None) -> str | None:
        return f"/v1/media/{kind}/{name}" if name else None

    def user_out(user: User) -> UserOut:
        avatar = (AvatarOut(url=media_url("avatars", user.avatar_file)) if user.avatar_file
                  else AvatarOut(preset=user.avatar_preset or DEFAULT_AVATAR))
        return UserOut(id=user.id, name=user.name, login=user.login, role=user.role,  # type: ignore[arg-type]
                       avatar=avatar, created_at=user.created_at)

    def achievement_out(a: Achievement, *, earned_at: datetime | None = None,
                        stats: ach.UserStats | None = None) -> AchievementOut:
        rule = ach.RULES.get(a.rule_type)
        return AchievementOut(
            id=a.id, title=a.title, description=a.description,
            image_url=media_url("achievements", a.image_file),
            rule_type=a.rule_type, rule_label=rule.label if rule else a.rule_type,
            threshold=a.threshold, rule_value=a.rule_value, is_active=a.is_active, sort_order=a.sort_order,
            earned=earned_at is not None, earned_at=earned_at,
            progress=a.threshold if earned_at else (ach.progress(a, stats) if stats else 0),
        )

    def scan_out(scan: Scan, rating: int = 0) -> ScanOut:
        return ScanOut(
            id=scan.id, slug=scan.slug, status=scan.status,  # type: ignore[arg-type]
            wine=WineSnapshot(**scan.wine) if scan.wine else None,
            photo_url=media_url("scans", scan.photo_file),
            scanned_at=scan.scanned_at, rating=rating,
        )

    def achievements_for(session: Session, user: User | None, *, include_inactive: bool = False) -> list[AchievementOut]:
        query = select(Achievement).order_by(Achievement.sort_order, Achievement.id)
        if not include_inactive:
            query = query.where(Achievement.is_active.is_(True))
        items = session.scalars(query).all()
        if user is None:
            return [achievement_out(a) for a in items]
        earned = dict(session.execute(
            select(UserAchievement.achievement_id, UserAchievement.earned_at).where(UserAchievement.user_id == user.id)
        ).all())
        stats = ach.user_stats(session, user.id)
        return [achievement_out(a, earned_at=earned.get(a.id), stats=stats) for a in items]

    def new_out(new: list[Achievement]) -> list[AchievementOut]:
        now = utcnow()
        return [achievement_out(a, earned_at=now) for a in new]

    def stats_of(session: Session, user_id: int) -> Stats:
        # «из скольких регионов» — для счётчика в профиле и будущей карты регионов
        return Stats(**ach.user_stats(session, user_id).summary(), regions_total=len(app.state.catalog.regions()))

    def rating_of(session: Session, user_id: int, slug: str | None) -> int:
        if not slug:
            return 0
        rating = session.get(Rating, (user_id, slug))
        return rating.value if rating else 0

    def start_session(session: Session, user: User, response: Response) -> None:
        token = new_token()
        now = utcnow()
        # Заодно убираем истёкшие сессии этого пользователя
        for old in session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
            if _aware(old.expires_at) < now:
                session.delete(old)
        session.add(AuthSession(token_hash=token_hash(token), user_id=user.id,
                                expires_at=now + timedelta(days=settings.session_days)))
        session.commit()
        response.set_cookie(
            settings.session_cookie, token, max_age=settings.session_days * 86400,
            httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
        )

    async def read_upload(file: UploadFile) -> bytes:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Пустой файл")
        if len(raw) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Файл слишком большой")
        return raw

    def lookup_wine(catalog: Catalog, slug: str) -> dict:
        try:
            wine = catalog.get(slug)
        except CatalogUnavailable as exc:
            raise HTTPException(status_code=503, detail="Каталог вин временно недоступен") from exc
        if wine is None:
            raise HTTPException(status_code=422, detail="Такого вина нет в каталоге")
        return wine

    # ── Служебное ────────────────────────────────────────────────────────
    @app.get("/health", response_model=HealthResponse)
    def health(session: SessionDep) -> HealthResponse:
        return HealthResponse(
            status="ok",
            users=session.scalar(select(func.count()).select_from(User)) or 0,
            achievements=session.scalar(select(func.count()).select_from(Achievement)) or 0,
        )

    @app.get("/v1/media/{kind}/{name}", include_in_schema=False)
    def get_media(kind: str, name: str) -> FileResponse:
        path = media.media_path(media_root, kind, name)
        if path is None:
            raise HTTPException(status_code=404, detail="Файл не найден")
        # Имена файлов — случайные и не меняются: кэшировать можно надолго
        return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/v1/avatars", response_model=list[AvatarPresetOut])
    def avatar_presets() -> list[AvatarPresetOut]:
        return [AvatarPresetOut(**p.__dict__) for p in AVATAR_PRESETS]

    # ── Вход ─────────────────────────────────────────────────────────────
    @app.post("/v1/auth/register", response_model=MeOut, status_code=201)
    def register(body: RegisterRequest, response: Response, session: SessionDep) -> MeOut:
        try:
            kind, value = parse_login(body.login)
        except LoginError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        name = (body.name or "").strip() or "Любитель вина"
        user = User(name=name, password_hash=hash_password(body.password),
                    avatar_preset=DEFAULT_AVATAR, **{kind: value})
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            what = "почтой" if kind == "email" else "номером"
            raise HTTPException(status_code=409, detail=f"Аккаунт с этим {what} уже есть — войдите") from exc
        start_session(session, user, response)
        return MeOut(user=user_out(user), stats=stats_of(session, user.id),
                     achievements=achievements_for(session, user))

    @app.post("/v1/auth/login", response_model=MeOut)
    def login(body: LoginRequest, request: Request, response: Response, session: SessionDep) -> MeOut:
        client = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
        now = time.monotonic()
        attempts = [t for t in failed_logins.get(client, []) if now - t < _FAILED_WINDOW_S]
        if len(attempts) >= _MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Слишком много попыток — подождите несколько минут")
        try:
            kind, value = parse_login(body.login)
        except LoginError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        column = User.email if kind == "email" else User.phone
        user = session.scalar(select(User).where(column == value))
        if user is None or not verify_password(body.password, user.password_hash):
            failed_logins[client] = [*attempts, now]
            raise HTTPException(status_code=401, detail="Неверный логин или пароль")
        failed_logins.pop(client, None)
        start_session(session, user, response)
        new = ach.award(session, user.id)
        return MeOut(user=user_out(user), stats=stats_of(session, user.id),
                     achievements=achievements_for(session, user), new_achievements=new_out(new))

    @app.post("/v1/auth/logout", status_code=204)
    def logout(
        response: Response, session: SessionDep,
        token: Annotated[str | None, Cookie(alias=settings.session_cookie)] = None,
    ) -> Response:
        if token and (auth := session.get(AuthSession, token_hash(token))):
            session.delete(auth)
            session.commit()
        response.delete_cookie(settings.session_cookie, path="/")
        response.status_code = 204
        return response

    # ── Профиль ──────────────────────────────────────────────────────────
    @app.get("/v1/me", response_model=MeOut)
    def me(user: UserDep, session: SessionDep) -> MeOut:
        new = ach.award(session, user.id)  # подхватывает достижения, добавленные админом позже
        return MeOut(user=user_out(user), stats=stats_of(session, user.id),
                     achievements=achievements_for(session, user), new_achievements=new_out(new))

    @app.patch("/v1/me", response_model=UserOut)
    def update_me(body: ProfileUpdate, user: UserDep, session: SessionDep) -> UserOut:
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=422, detail="Имя не может быть пустым")
            user.name = name
        if body.avatar_preset is not None:
            if body.avatar_preset not in AVATAR_IDS:
                raise HTTPException(status_code=422, detail="Нет такого аватара")
            media.delete(media_root, "avatars", user.avatar_file)
            user.avatar_preset, user.avatar_file = body.avatar_preset, None
        session.add(user)
        session.commit()
        return user_out(user)

    @app.delete("/v1/me", status_code=204)
    def delete_me(user: UserDep, session: SessionDep) -> Response:
        """Удаляет аккаунт со всей историей, оценками и файлами."""
        if user.role == "admin":
            raise HTTPException(status_code=403, detail="Администратора удалить нельзя")
        photos = session.scalars(select(Scan.photo_file).where(Scan.user_id == user.id)).all()
        for model in (Scan, Rating, UserAchievement, AuthSession):
            session.execute(model.__table__.delete().where(model.user_id == user.id))
        avatar = user.avatar_file
        session.delete(user)
        session.commit()
        for name in photos:
            media.delete(media_root, "scans", name)
        media.delete(media_root, "avatars", avatar)
        response = Response(status_code=204)
        response.delete_cookie(settings.session_cookie, path="/")
        return response

    @app.put("/v1/me/avatar", response_model=UserOut)
    async def upload_avatar(user: UserDep, session: SessionDep, image: UploadFile = File(...)) -> UserOut:
        raw = await read_upload(image)
        try:
            name = media.save_avatar(raw, media_root)
        except media.InvalidImage as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        media.delete(media_root, "avatars", user.avatar_file)
        user.avatar_file = name
        session.add(user)
        session.commit()
        return user_out(user)

    # ── История сканов ───────────────────────────────────────────────────
    @app.get("/v1/me/scans", response_model=ScanList)
    def list_scans(
        user: UserDep, session: SessionDep,
        limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0),
    ) -> ScanList:
        total = session.scalar(select(func.count()).select_from(Scan).where(Scan.user_id == user.id)) or 0
        scans = session.scalars(
            select(Scan).where(Scan.user_id == user.id)
            .order_by(Scan.scanned_at.desc(), Scan.id.desc()).limit(limit).offset(offset)
        ).all()
        slugs = {s.slug for s in scans if s.slug}
        ratings = dict(session.execute(
            select(Rating.slug, Rating.value).where(Rating.user_id == user.id, Rating.slug.in_(slugs))
        ).all()) if slugs else {}
        return ScanList(items=[scan_out(s, ratings.get(s.slug, 0)) for s in scans], total=total)

    @app.post("/v1/me/scans", response_model=ScanResult, status_code=201)
    async def add_scan(
        user: UserDep, session: SessionDep, catalog: Annotated[Catalog, Depends(get_catalog)],
        status: Annotated[str, Form(pattern="^(match|not_found)$")],
        slug: Annotated[str | None, Form(max_length=255)] = None,
        scanned_at: Annotated[datetime | None, Form(description="время скана без аккаунта — при переносе")] = None,
        photo: UploadFile | None = File(default=None),
    ) -> ScanResult:
        """Записывает скан. Вызывает сканер после ответа /v1/scan сервиса поиска."""
        slug = (slug or "").strip() or None
        if status == "match" and not slug:
            raise HTTPException(status_code=422, detail="Для распознанного вина нужен slug")
        wine = lookup_wine(catalog, slug) if slug else None
        photo_file = None
        if photo is not None and photo.filename:
            try:
                photo_file = media.save_scan_photo(await read_upload(photo), media_root)
            except media.InvalidImage as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        when = utcnow()
        if scanned_at is not None:
            when = min(_aware(scanned_at), when)
        stats = ach.user_stats(session, user.id)
        scan = Scan(user_id=user.id, slug=slug, status="match" if slug else "not_found",
                    wine=wine, photo_file=photo_file, scanned_at=when)
        session.add(scan)
        session.commit()
        new = ach.award(session, user.id)
        return ScanResult(scan=scan_out(scan, rating_of(session, user.id, slug)), new_achievements=new_out(new),
                          new_region=_new_region(wine, stats.regions, stats.resolve))

    @app.patch("/v1/me/scans/{scan_id}", response_model=ScanResult)
    def update_scan(
        scan_id: int, body: ScanUpdate, user: UserDep, session: SessionDep,
        catalog: Annotated[Catalog, Depends(get_catalog)],
    ) -> ScanResult:
        """Пользователь сам выбрал вино в «Не то вино?» — исправляем скан."""
        scan = session.get(Scan, scan_id)
        if scan is None or scan.user_id != user.id:
            raise HTTPException(status_code=404, detail="Скан не найден")
        wine = lookup_wine(catalog, body.slug)
        # регионы без этого скана: поправленный скан мог и закрыть свой старый регион
        resolve = zones.resolver(session)
        regions_before = {
            resolve(raw) or raw for (w,) in session.execute(
                select(Scan.wine).where(Scan.user_id == user.id, Scan.slug.is_not(None), Scan.id != scan.id)
            ) if (raw := (w or {}).get("region"))
        }
        scan.wine, scan.slug, scan.status = wine, body.slug, "match"
        session.commit()
        new = ach.award(session, user.id)
        return ScanResult(scan=scan_out(scan, rating_of(session, user.id, scan.slug)), new_achievements=new_out(new),
                          new_region=_new_region(wine, regions_before, resolve))

    @app.get("/v1/me/regions", response_model=RegionsOut)
    def my_regions(user: UserDep, session: SessionDep) -> RegionsOut:
        """Открытые регионы винной карты: какие вина из них попробованы и когда регион открыт."""
        rows = session.execute(
            select(Scan.slug, Scan.wine, Scan.scanned_at)
            .where(Scan.user_id == user.id, Scan.slug.is_not(None))
            .order_by(Scan.scanned_at.desc(), Scan.id.desc())
        ).all()
        resolve = zones.resolver(session)
        regions: dict[str, dict] = {}
        for slug, wine, scanned_at in rows:
            raw = (wine or {}).get("region")
            if not raw:
                continue
            zone = resolve(raw)
            info = regions.setdefault(zone or raw, {"wines": {}, "opened_at": scanned_at, "on_map": zone is not None})
            info["wines"].setdefault(slug, wine)
            info["opened_at"] = min(_aware(info["opened_at"]), _aware(scanned_at))
        return RegionsOut(regions=[
            OpenedRegion(name=name, on_map=info["on_map"], wines=[WineSnapshot(**w) for w in info["wines"].values()],
                         opened_at=info["opened_at"])
            for name, info in sorted(regions.items(), key=lambda x: x[1]["opened_at"])
        ])

    @app.delete("/v1/me/scans/{scan_id}", status_code=204)
    def delete_scan(scan_id: int, user: UserDep, session: SessionDep) -> Response:
        scan = session.get(Scan, scan_id)
        if scan is None or scan.user_id != user.id:
            raise HTTPException(status_code=404, detail="Скан не найден")
        media.delete(media_root, "scans", scan.photo_file)
        session.delete(scan)
        session.commit()
        return Response(status_code=204)

    # ── Оценки ───────────────────────────────────────────────────────────
    @app.put("/v1/me/ratings/{slug}", response_model=RatingResult)
    def rate(slug: str, body: RatingRequest, user: UserDep, session: SessionDep) -> RatingResult:
        """Нравится / не нравится — только для вин из своей истории сканов."""
        scanned = session.scalar(select(Scan.id).where(Scan.user_id == user.id, Scan.slug == slug).limit(1))
        if scanned is None:
            raise HTTPException(status_code=404, detail="Этого вина нет в вашей истории сканов")
        rating = session.get(Rating, (user.id, slug))
        if body.value == 0:
            if rating is not None:
                session.delete(rating)
        elif rating is None:
            session.add(Rating(user_id=user.id, slug=slug, value=body.value))
        else:
            rating.value = body.value
        session.commit()
        new = ach.award(session, user.id)
        return RatingResult(slug=slug, value=body.value, new_achievements=new_out(new))

    # ── Винные регионы карты ─────────────────────────────────────────────
    def zones_out(session: Session) -> MapZonesOut:
        all_zones = session.scalars(select(WineZone).order_by(WineZone.name)).all()
        names = {z.id: z.name for z in all_zones}
        aliases = session.scalars(select(ZoneAlias).order_by(ZoneAlias.catalog_region)).all()
        return MapZonesOut(
            zones=[ZoneOut(id=z.id, name=z.name, subjects=z.subjects) for z in all_zones],
            aliases=[AliasOut(catalog_region=a.catalog_region, zone_id=a.zone_id, zone=names[a.zone_id]) for a in aliases],
        )

    def check_subjects(subjects: list[str]) -> list[str]:
        clean = list(dict.fromkeys(s.strip().upper() for s in subjects))
        if not all(zones.SUBJECT_ID.match(s) for s in clean):
            raise HTTPException(status_code=422, detail="Неизвестный код субъекта")
        return clean

    def check_zone_name(session: Session, name: str, exclude_id: int | None = None) -> str:
        name = name.strip()
        for zone in session.scalars(select(WineZone)):
            if zone.id != exclude_id and zones.norm(zone.name) == zones.norm(name):
                raise HTTPException(status_code=409, detail=f"Регион «{zone.name}» уже есть на карте")
        return name

    @app.get("/v1/map/zones", response_model=MapZonesOut)
    def map_zones(session: SessionDep) -> MapZonesOut:
        """Винные регионы карты (какие субъекты занимают) и сопоставления админа."""
        return zones_out(session)

    @app.post("/v1/admin/map/zones", response_model=MapZonesOut, status_code=201)
    def create_zone(body: ZoneRequest, _: AdminDep, session: SessionDep) -> MapZonesOut:
        """Новый винный регион карты: название и отмеченные на карте субъекты."""
        session.add(WineZone(name=check_zone_name(session, body.name), subjects=check_subjects(body.subjects)))
        session.commit()
        return zones_out(session)

    @app.patch("/v1/admin/map/zones/{zone_id}", response_model=MapZonesOut)
    def update_zone(zone_id: int, body: ZoneUpdate, _: AdminDep, session: SessionDep) -> MapZonesOut:
        zone = session.get(WineZone, zone_id)
        if zone is None:
            raise HTTPException(status_code=404, detail="Регион не найден")
        if body.name is not None:
            zone.name = check_zone_name(session, body.name, exclude_id=zone.id)
        if body.subjects is not None:
            zone.subjects = check_subjects(body.subjects)
        session.commit()
        return zones_out(session)

    @app.delete("/v1/admin/map/zones/{zone_id}", response_model=MapZonesOut)
    def delete_zone(zone_id: int, _: AdminDep, session: SessionDep) -> MapZonesOut:
        """Удаляет регион с карты вместе с сопоставлениями — его вина снова уйдут админу."""
        zone = session.get(WineZone, zone_id)
        if zone is None:
            raise HTTPException(status_code=404, detail="Регион не найден")
        session.execute(ZoneAlias.__table__.delete().where(ZoneAlias.zone_id == zone_id))
        session.delete(zone)
        session.commit()
        return zones_out(session)

    @app.put("/v1/admin/map/aliases", response_model=MapZonesOut)
    def set_alias(body: AliasRequest, _: AdminDep, session: SessionDep) -> MapZonesOut:
        """Относит region из каталога к существующему региону карты."""
        if session.get(WineZone, body.zone_id) is None:
            raise HTTPException(status_code=404, detail="Регион не найден")
        alias = session.get(ZoneAlias, body.catalog_region.strip())
        if alias is None:
            session.add(ZoneAlias(catalog_region=body.catalog_region.strip(), zone_id=body.zone_id))
        else:
            alias.zone_id = body.zone_id
        session.commit()
        return zones_out(session)

    @app.delete("/v1/admin/map/aliases", response_model=MapZonesOut)
    def delete_alias(_: AdminDep, session: SessionDep,
                     catalog_region: str = Query(min_length=1, max_length=255)) -> MapZonesOut:
        alias = session.get(ZoneAlias, catalog_region)
        if alias is None:
            raise HTTPException(status_code=404, detail="Сопоставление не найдено")
        session.delete(alias)
        session.commit()
        return zones_out(session)

    # ── Тексты «Поделиться» ──────────────────────────────────────────────
    def share_settings(session: Session) -> ShareSettings:
        row = session.get(Setting, WINE_TEMPLATE_KEY)
        return ShareSettings(wine_template=row.value if row else DEFAULT_WINE_TEMPLATE,
                             default_wine_template=DEFAULT_WINE_TEMPLATE)

    @app.get("/v1/settings/share", response_model=ShareSettings)
    def get_share_settings(session: SessionDep) -> ShareSettings:
        return share_settings(session)

    @app.put("/v1/admin/settings/share", response_model=ShareSettings)
    def put_share_settings(body: ShareSettings, _: AdminDep, session: SessionDep) -> ShareSettings:
        text = body.wine_template.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Текст не может быть пустым")
        row = session.get(Setting, WINE_TEMPLATE_KEY)
        if text == DEFAULT_WINE_TEMPLATE:
            if row is not None:
                session.delete(row)  # «Сбросить» — снова шаблон по умолчанию, и он обновится с кодом
        elif row is None:
            session.add(Setting(key=WINE_TEMPLATE_KEY, value=text))
        else:
            row.value = text
        session.commit()
        return share_settings(session)

    # ── Достижения ───────────────────────────────────────────────────────
    @app.get("/v1/achievements", response_model=list[AchievementOut])
    def list_achievements(session: SessionDep, user: Annotated[User | None, Depends(optional_user)]) -> list[AchievementOut]:
        """Все активные достижения; для вошедшего — с отметкой и прогрессом."""
        return achievements_for(session, user)

    @app.get("/v1/admin/achievements", response_model=list[AchievementOut])
    def admin_achievements(_: AdminDep, session: SessionDep) -> list[AchievementOut]:
        return achievements_for(session, None, include_inactive=True)

    @app.get("/v1/admin/achievements/rules", response_model=RulesOut)
    def admin_rules(_: AdminDep, catalog: Annotated[Catalog, Depends(get_catalog)]) -> RulesOut:
        return RulesOut(
            rules=[RuleOut(type=k, label=r.label, progress=r.progress, needs_value=r.needs_value,
                           value_hint=r.value_hint) for k, r in ach.RULES.items()],
            regions=catalog.regions(),
        )

    def validate_rule(rule_type: str, rule_value: str | None) -> str | None:
        rule = ach.RULES.get(rule_type)
        if rule is None:
            raise HTTPException(status_code=422, detail="Неизвестный тип условия")
        value = (rule_value or "").strip() or None
        if rule.needs_value and not value:
            raise HTTPException(status_code=422, detail=f"Для условия «{rule.label}» нужно значение")
        return value if rule.needs_value else None

    async def save_badge(image: UploadFile | None) -> str | None:
        if image is None or not image.filename:
            return None
        try:
            return media.save_achievement_image(await read_upload(image), media_root)
        except media.InvalidImage as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/v1/admin/achievements", response_model=AchievementOut, status_code=201)
    async def create_achievement(
        _: AdminDep, session: SessionDep,
        title: Annotated[str, Form(min_length=1, max_length=80)],
        description: Annotated[str, Form(min_length=1, max_length=300)],
        rule_type: Annotated[str, Form()],
        threshold: Annotated[int, Form(ge=1, le=100000)] = 1,
        rule_value: Annotated[str | None, Form(max_length=120)] = None,
        sort_order: Annotated[int, Form(ge=0, le=100000)] = 100,
        is_active: Annotated[bool, Form()] = True,
        image: UploadFile | None = File(default=None),
    ) -> AchievementOut:
        value = validate_rule(rule_type, rule_value)
        achievement = Achievement(
            title=title.strip(), description=description.strip(), rule_type=rule_type,
            threshold=threshold, rule_value=value, sort_order=sort_order, is_active=is_active,
            image_file=await save_badge(image),
        )
        session.add(achievement)
        session.commit()
        return achievement_out(achievement)

    @app.patch("/v1/admin/achievements/{achievement_id}", response_model=AchievementOut)
    async def update_achievement(
        achievement_id: int, _: AdminDep, session: SessionDep,
        title: Annotated[str | None, Form(min_length=1, max_length=80)] = None,
        description: Annotated[str | None, Form(min_length=1, max_length=300)] = None,
        rule_type: Annotated[str | None, Form()] = None,
        threshold: Annotated[int | None, Form(ge=1, le=100000)] = None,
        rule_value: Annotated[str | None, Form(max_length=120)] = None,
        sort_order: Annotated[int | None, Form(ge=0, le=100000)] = None,
        is_active: Annotated[bool | None, Form()] = None,
        image: UploadFile | None = File(default=None),
    ) -> AchievementOut:
        achievement = session.get(Achievement, achievement_id)
        if achievement is None:
            raise HTTPException(status_code=404, detail="Достижение не найдено")
        if title is not None:
            achievement.title = title.strip()
        if description is not None:
            achievement.description = description.strip()
        if rule_type is not None or rule_value is not None:
            achievement.rule_type = rule_type or achievement.rule_type
            achievement.rule_value = validate_rule(achievement.rule_type,
                                                   rule_value if rule_value is not None else achievement.rule_value)
        if threshold is not None:
            achievement.threshold = threshold
        if sort_order is not None:
            achievement.sort_order = sort_order
        if is_active is not None:
            achievement.is_active = is_active
        if (badge := await save_badge(image)) is not None:
            media.delete(media_root, "achievements", achievement.image_file)
            achievement.image_file = badge
        session.commit()
        return achievement_out(achievement)

    @app.delete("/v1/admin/achievements/{achievement_id}", status_code=204)
    def delete_achievement(achievement_id: int, _: AdminDep, session: SessionDep) -> Response:
        """Удаляет достижение вместе с выданными значками. Мягкий вариант — is_active=false."""
        achievement = session.get(Achievement, achievement_id)
        if achievement is None:
            raise HTTPException(status_code=404, detail="Достижение не найдено")
        session.execute(UserAchievement.__table__.delete().where(UserAchievement.achievement_id == achievement_id))
        media.delete(media_root, "achievements", achievement.image_file)
        session.delete(achievement)
        session.commit()
        return Response(status_code=204)

    return app


def _new_region(wine: dict | None, regions_before: set, resolve) -> str | None:
    raw = (wine or {}).get("region")
    region = resolve(raw) or raw
    return region if region and region not in regions_before else None


def _aware(value: datetime) -> datetime:
    """SQLite возвращает время без зоны — считаем его UTC, как и записывали."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _wait_for_db(db: Database, attempts: int = 30) -> None:
    """Postgres в compose стартует параллельно — ждём, пока примет соединение."""
    for attempt in range(1, attempts + 1):
        try:
            with db.engine.connect():
                return
        except OperationalError as exc:
            if attempt == attempts:
                raise
            logger.info("База ещё не готова (%s), повтор через 2 с", exc.__class__.__name__)
            time.sleep(2)


app = create_app()
