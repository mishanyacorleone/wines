"""Сервис аналитики: события со страницы сканера → дашборд для РСХБ.

Страница шлёт события (скан, «поделиться», карта, регистрация…) на /v1/events;
адрес клиента подставляет шлюз (X-Real-IP). Дашборд и демо-данные — только для
админа: кто админ, решает сервис пользователей (ему пересылается cookie сессии).
Контракт организатора (/v1/eval/predict) в аналитику не попадает: события шлёт
только веб-сканер.
"""

# Без `from __future__ import annotations`: зависимости объявлены внутри фабрики
import logging
import time
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import wine_logging
from wine_logging import trace_headers

from . import demo, export, report
from .config import settings
from .db import Database, Event, make_database

logger = logging.getLogger("analytics")

# Какие события со страницы писать в лог (остальные — только в базу: их сотни)
_LOGGED_EVENTS = {"share": "поделился", "register": "регистрация со страницы"}

EventType = Literal[
    "page_view", "scan", "share", "map_open", "region_open",
    "nudge_shown", "nudge_click", "register", "login", "achievement",
    # вовлечение (engage.js, 19.10)
    "quiz", "diary", "diary_prompt", "gift_open", "gift_save", "prize", "year_open",
    "auth_prompt",  # предложение войти; props.source — quiz | diary | gift | challenge
    # оценки (20.10): ответ «Это ваше вино?» после скана и 👍/👎 в истории сканов
    "recognition",  # props.answer — confirmed | picked_alternative | none_match; props.status — ответ сканера
    "rate",  # props.value — 1 | -1 | 0 (снял оценку)
    "taste",  # «Мой вкус» (A9): props.action — open | save | match (+ percent)
]


class EventIn(BaseModel):
    type: EventType
    anon_id: str = Field(min_length=8, max_length=64)
    user_id: int | None = None
    props: dict[str, Any] = Field(default_factory=dict)

    @field_validator("props")
    @classmethod
    def small(cls, value: dict) -> dict:
        # только простые значения и не больше пары килобайт — это счётчики, не логи
        clean = {str(k)[:32]: v for k, v in list(value.items())[:12]
                 if v is None or isinstance(v, (str, int, float, bool))}
        return {k: v[:200] if isinstance(v, str) else v for k, v in clean.items()}


def create_app(database: Database | None = None, admin_check=None, wine_source=None) -> FastAPI:
    db = database or make_database()
    recent: dict[str, list[float]] = {}
    admin_cache: dict[str, tuple[float, bool]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for attempt in range(30):
            try:
                with db.engine.connect():
                    break
            except OperationalError:
                if attempt == 29:
                    raise
                time.sleep(2)
        db.create_schema()
        logger.info("Сервис аналитики готов")
        yield
        db.engine.dispose()

    app = FastAPI(title="Аналитика «Своё Вино»", version="0.1.0", lifespan=lifespan)
    app.add_middleware(wine_logging.RequestLogMiddleware)

    def get_session():
        yield from db.session()

    SessionDep = Annotated[Session, Depends(get_session)]

    def client_ip(request: Request) -> str | None:
        return request.headers.get("x-real-ip") or (request.client.host if request.client else None)

    async def require_admin(request: Request) -> None:
        """Админ ли — спрашиваем сервис пользователей, с той же cookie. Ответ кэшируется на 30 с."""
        if admin_check is not None:
            if not await admin_check(request):
                raise HTTPException(status_code=403, detail="Только для администратора")
            return
        cookie = request.headers.get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=401, detail="Войдите в аккаунт администратора")
        cached = admin_cache.get(cookie)
        if cached and time.monotonic() - cached[0] < 30:
            ok = cached[1]
        else:
            try:
                async with httpx.AsyncClient(base_url=settings.users_url, timeout=3, trust_env=False) as client:
                    res = await client.get("/v1/me", headers={"cookie": cookie, **trace_headers()})
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=503, detail="Сервис пользователей недоступен") from exc
            ok = res.status_code == 200 and res.json().get("user", {}).get("role") == "admin"
            admin_cache[cookie] = (time.monotonic(), ok)
        if not ok:
            raise HTTPException(status_code=403, detail="Только для администратора")

    AdminDep = Annotated[None, Depends(require_admin)]

    @app.get("/health")
    def health(session: SessionDep) -> dict:
        return {"status": "ok", "events": session.scalar(select(func.count()).select_from(Event)) or 0}

    @app.post("/v1/events", status_code=204)
    def track(body: EventIn, request: Request, session: SessionDep) -> Response:
        """Событие со страницы. Ответ пустой: страница не ждёт и не показывает ошибок."""
        ip = client_ip(request)
        now = time.monotonic()
        window = [t for t in recent.get(ip or "?", []) if now - t < 60]
        if len(window) >= settings.max_events_per_minute:
            if len(window) == settings.max_events_per_minute:
                logger.warning("Слишком много событий с адреса %s (> %d в минуту) — лишние отклоняются",
                               ip, settings.max_events_per_minute)
            raise HTTPException(status_code=429, detail="Слишком много событий")
        recent[ip or "?"] = [*window, now]
        if body.type in _LOGGED_EVENTS:
            logger.info("Событие «%s»%s", _LOGGED_EVENTS[body.type],
                        f": {', '.join(f'{k}={v}' for k, v in body.props.items())}" if body.props else "")
        session.add(Event(type=body.type, anon_id=body.anon_id, user_id=body.user_id, ip=ip, props=body.props))
        session.commit()
        return Response(status_code=204)

    @app.get("/v1/admin/analytics")
    def analytics(
        _: AdminDep, session: SessionDep,
        days: int = Query(default=30, ge=1, le=365),
        demo_data: bool = Query(default=True, alias="demo", description="учитывать демо-данные"),
    ) -> dict:
        return report.build(session, days, include_demo=demo_data)

    @app.get("/v1/admin/analytics/export.xlsx")
    def export_xlsx(
        _: AdminDep, session: SessionDep,
        days: int = Query(default=30, ge=1, le=365),
        demo_data: bool = Query(default=True, alias="demo"),
    ) -> Response:
        """Тот же отчёт в Excel + события периода — для РСХБ и своих расчётов."""
        content = export.build_xlsx(session, days, include_demo=demo_data)
        name = f"wine-analytics-{datetime.now():%Y-%m-%d}-{days}d.xlsx"
        logger.info("Выгрузка аналитики в Excel: %d дн., %s, %.0f КБ", days,
                    "с демо-данными" if demo_data else "без демо-данных", len(content) / 1024)
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/v1/admin/analytics/demo")
    def create_demo(_: AdminDep, session: SessionDep, days: int = Query(default=30, ge=1, le=90)) -> dict:
        """Демо-данные за период — вместо прежних демо-данных, настоящие не трогаются."""
        session.execute(delete(Event).where(Event.demo.is_(True)))
        session.commit()
        wines = wine_source() if wine_source else demo.catalog_wines(settings.api_url)
        created = demo.generate(session, wines, days=days)
        logger.info("Созданы демо-данные: %d событий за %d дней", created, days)
        return {"created": created}

    @app.delete("/v1/admin/analytics/demo")
    def delete_demo(_: AdminDep, session: SessionDep) -> dict:
        result = session.execute(delete(Event).where(Event.demo.is_(True)))
        session.commit()
        logger.info("Демо-данные удалены: %d событий", result.rowcount or 0)
        return {"deleted": result.rowcount or 0}

    return app


app = create_app()
