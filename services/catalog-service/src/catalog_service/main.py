"""Сервис каталога: синхронизация с vino-svoe.ru по кнопке и по расписанию.

Ручки — только для админа (кто админ, решает сервис пользователей по cookie):
статус и история, запуск, расписание. Работа — в sync.py.
"""

# Без `from __future__ import annotations`: зависимости объявлены внутри фабрики
import asyncio
import dataclasses
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

import wine_logging
from wine_logging import trace_headers

from .config import settings
from .sync import MIN_INTERVAL_S, ScheduleSettings, ScrapeResult, SyncState, Syncer

logger = logging.getLogger("catalog")


class ScheduleIn(BaseModel):
    auto: bool
    times: int = Field(ge=1, le=1000, description="сколько раз…")
    per: Literal["minute", "hour", "day", "week", "month"] = Field(description="…за какой период")


def _scrape_from_site():
    """Парсер сайта: только новые вина (resume), sitemap — свежий."""
    from catalog_scraper.config import Settings as ScraperSettings
    from catalog_scraper.http_client import FetchError
    from catalog_scraper.pipeline import scrape_catalog

    async def scrape() -> ScrapeResult:
        scraper = ScraperSettings(output_dir=settings.catalog_dir)
        try:
            stats = await scrape_catalog(scraper, resume=True, refresh_sitemap=True, progress=False)
        except FetchError as exc:
            raise RuntimeError(f"сайт vino-svoe.ru недоступен: {exc}") from exc
        return ScrapeResult(
            slugs=stats.slugs,
            downloaded=stats.scraped,
            failures=[f"{f.slug}: {f.stage} — {f.reason}"[:200] for f in stats.failures],
        )

    return scrape


def _embedder_client():
    async def embed(raw: bytes, filename: str) -> list[float]:
        async with httpx.AsyncClient(base_url=settings.embedder_url, timeout=60, trust_env=False) as client:
            response = await client.post("/v1/embed", files={"image": (filename, raw)}, headers=trace_headers())
        if response.status_code >= 500:
            raise RuntimeError(f"embedder недоступен (HTTP {response.status_code})")
        response.raise_for_status()
        return response.json()["vector"]

    return embed


def _notify_api():
    async def notify() -> None:
        async with httpx.AsyncClient(base_url=settings.api_url, timeout=60, trust_env=False) as client:
            response = await client.post("/internal/catalog/reload", headers=trace_headers())
        response.raise_for_status()

    return notify


def create_app(syncer: Syncer | None = None, admin_check=None) -> FastAPI:
    admin_cache: dict[str, tuple[float, str | None]] = {}
    tasks: set[asyncio.Task] = set()

    def build_syncer() -> Syncer:
        state = SyncState(settings.state_path,
                          ScheduleSettings(settings.default_auto, settings.default_times, settings.default_per))
        return Syncer(
            state=state,
            qdrant=QdrantClient(url=settings.qdrant_url, timeout=60),
            collection=settings.collection,
            catalog_path=settings.catalog_path,
            site_base_url=settings.site_base_url,
            scrape=_scrape_from_site(),
            embed=_embedder_client(),
            notify_api=_notify_api(),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.syncer = syncer or build_syncer()
        scheduler = asyncio.create_task(app.state.syncer.schedule_loop())
        schedule = app.state.syncer.state.schedule
        logger.info("Сервис каталога готов: автообновление %s",
                    schedule.describe() if schedule.auto else "выключено")
        yield
        scheduler.cancel()

    app = FastAPI(title="Каталог «Своё Вино»", version="1.0.0", lifespan=lifespan)
    app.add_middleware(wine_logging.RequestLogMiddleware)

    async def require_admin(request: Request) -> str:
        """Логин админа — для отчёта «кто запустил». Ответ users кэшируется на 30 с."""
        if admin_check is not None:
            login = await admin_check(request)
            if not login:
                raise HTTPException(status_code=403, detail="Только для администратора")
            return login
        cookie = request.headers.get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=401, detail="Войдите в аккаунт администратора")
        cached = admin_cache.get(cookie)
        if cached and time.monotonic() - cached[0] < 30:
            login = cached[1]
        else:
            try:
                async with httpx.AsyncClient(base_url=settings.users_url, timeout=3, trust_env=False) as client:
                    res = await client.get("/v1/me", headers={"cookie": cookie, **trace_headers()})
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=503, detail="Сервис пользователей недоступен") from exc
            user = res.json().get("user", {}) if res.status_code == 200 else {}
            login = (user.get("login") or "admin") if user.get("role") == "admin" else None
            admin_cache[cookie] = (time.monotonic(), login)
        if not login:
            raise HTTPException(status_code=403, detail="Только для администратора")
        return login

    AdminDep = Annotated[str, Depends(require_admin)]

    def status(request: Request) -> dict[str, Any]:
        s: Syncer = request.app.state.syncer
        due = s.state.next_run(s.started)
        try:
            indexed = s._qdrant.count(settings.collection, exact=True).count  # noqa: SLF001
        except Exception:  # noqa: BLE001 — статус показываем и без Qdrant
            indexed = None
        return {
            "running": s.running,
            "stage": s.stage,
            "schedule": {**dataclasses.asdict(s.state.schedule), "text": s.state.schedule.describe()},
            "next_run_at": due.isoformat(timespec="minutes") if due else None,
            "indexed": indexed,
            "runs": [dataclasses.asdict(r) for r in reversed(s.state.runs[-10:])],
        }

    @app.get("/health")
    def health(request: Request) -> dict:
        return {"status": "ok", "running": request.app.state.syncer.running}

    @app.get("/v1/admin/catalog/sync")
    def sync_status(request: Request, _: AdminDep) -> dict[str, Any]:
        return status(request)

    @app.post("/v1/admin/catalog/sync", status_code=202)
    async def sync_start(request: Request, admin: AdminDep) -> dict[str, Any]:
        s: Syncer = request.app.state.syncer
        if s.running:
            raise HTTPException(status_code=409, detail="Синхронизация уже идёт")
        task = asyncio.create_task(s.run(f"вручную, {admin}"))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        await asyncio.sleep(0)  # дать задаче занять блокировку: статус сразу «идёт»
        return status(request)

    @app.put("/v1/admin/catalog/sync/settings")
    def sync_settings(body: ScheduleIn, request: Request, admin: AdminDep) -> dict[str, Any]:
        s: Syncer = request.app.state.syncer
        schedule = ScheduleSettings(auto=body.auto, times=body.times, per=body.per)
        if schedule.interval.total_seconds() < MIN_INTERVAL_S:
            raise HTTPException(status_code=422, detail="Не чаще раза в минуту: каждая сверка — запрос к сайту")
        s.state.schedule = schedule
        s.state.save()
        logger.info("Расписание синхронизации изменено (%s): %s", admin,
                    schedule.describe() if body.auto else "автообновление выключено")
        return status(request)

    return app


app = create_app()
