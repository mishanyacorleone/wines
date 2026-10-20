"""Синхронизация каталога с сайтом: новые вина → фото и карточка → вектор → Qdrant.

По ТЗ в каталог добавляется до ~50 вин в день. Переобучать ничего не нужно:
новое вино — это одна новая точка в Qdrant. Шаги:

1. парсер (services/catalog-scraper) берёт свежий sitemap vino-svoe.ru и
   скачивает только вина, которых ещё нет в data/catalog (фото + карточка);
2. вина каталога, которых нет в Qdrant, получают вектор от embedder (тот же
   SigLIP 2, что для фото покупателей) и записываются в индекс — с тем же
   payload и id точки, что у индексатора (indexer.catalog, indexer.store);
3. api перечитывает каталог в память — вино сразу видно в поиске, карточках,
   подборе к блюду и на карте.

Ни один сервис не перезапускается. Вина, снятые с сайта, не удаляются —
отчёт только считает их: удалить вино из выдачи — решение человека.
Запуск — кнопкой в админке или по расписанию: админ задаёт «N раз в минуту / час /
день / неделю / месяц».
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np
from qdrant_client import QdrantClient

from indexer.catalog import CatalogEntry, load_catalog
from indexer.store import ensure_collection, point_id, upsert_batch

logger = logging.getLogger("sync")

MAX_RUNS = 20


# Период расписания → секунды и подпись («2 раза в день»)
PERIODS = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400}
PERIOD_NAMES = {"minute": "минуту", "hour": "час", "day": "день", "week": "неделю", "month": "месяц"}
# Чаще не нужно и невежливо к сайту: каждая сверка — запрос sitemap vino-svoe.ru
MIN_INTERVAL_S = 60


@dataclass
class ScheduleSettings:
    auto: bool = True
    times: int = 1
    per: str = "day"

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=PERIODS[self.per] / self.times)

    def describe(self) -> str:
        n = self.times
        word = "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"
        return f"{n} {word} в {PERIOD_NAMES[self.per]}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleSettings":
        if "interval_hours" in data:  # sync-state.json до вечера 19.10: «раз в N часов»
            hours = int(data["interval_hours"])
            times, per = (24 // hours, "day") if 24 % hours == 0 else (1, "week") if hours == 168 else (1, "day")
            return cls(auto=bool(data.get("auto", True)), times=times, per=per)
        return cls(**data)


@dataclass
class SyncRun:
    started_at: str
    trigger: str  # «вручную (admin@…)» или «по расписанию»
    status: str = "running"  # running | ok | error
    finished_at: str | None = None
    seconds: float = 0.0
    on_site: int = 0
    new_on_site: int = 0
    downloaded: int = 0
    indexed: int = 0
    failed: int = 0
    removed_on_site: int = 0
    errors: list[str] = field(default_factory=list)
    added: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


class SyncState:
    """Расписание и история запусков в data/catalog/sync-state.json.

    Файл, а не база: сервис один, запусков — единицы в день, а состояние
    должно пережить перезапуск контейнера.
    """

    def __init__(self, path: Path, defaults: ScheduleSettings) -> None:
        self.path = path
        self.schedule = defaults
        self.runs: list[SyncRun] = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.schedule = ScheduleSettings.from_dict(data.get("schedule", {}))
                self.runs = [SyncRun(**r) for r in data.get("runs", [])]
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("Состояние синхронизации не прочитано (%s) — начинаю заново", exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_name(self.path.name + ".tmp")
        partial.write_text(json.dumps(
            {"schedule": asdict(self.schedule), "runs": [asdict(r) for r in self.runs[-MAX_RUNS:]]},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        _same_owner(partial, self.path.parent)
        partial.replace(self.path)

    def last_finished(self) -> datetime | None:
        for run in reversed(self.runs):
            if run.finished_at:
                return datetime.fromisoformat(run.finished_at)
        return None

    def next_run(self, started: datetime) -> datetime | None:
        if not self.schedule.auto:
            return None
        base = self.last_finished() or started
        return base + self.schedule.interval


def _same_owner(path: Path, reference: Path) -> None:
    """В контейнере сервис работает от root — файлы в data/ отдаём владельцу
    репозитория, иначе на хосте их не удалить без sudo."""
    try:
        stat = reference.stat()
        os.chown(path, stat.st_uid, stat.st_gid)
    except (OSError, AttributeError):
        pass


@dataclass
class ScrapeResult:
    """Что вернул парсер сайта: все slug sitemap и что не удалось собрать."""

    slugs: list[str]
    downloaded: int
    failures: list[str]


class Syncer:
    def __init__(
        self,
        *,
        state: SyncState,
        qdrant: QdrantClient,
        collection: str,
        catalog_path: Path,
        site_base_url: str,
        scrape: Callable[[], Awaitable[ScrapeResult]],
        embed: Callable[[bytes, str], Awaitable[list[float]]],
        notify_api: Callable[[], Awaitable[None]],
    ) -> None:
        self.state = state
        self._qdrant = qdrant
        self._collection = collection
        self._catalog_path = catalog_path
        self._site_base_url = site_base_url
        self._scrape = scrape
        self._embed = embed
        self._notify_api = notify_api
        self._lock = asyncio.Lock()
        self.stage = ""
        self.started = datetime.now()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def run(self, trigger: str) -> SyncRun:
        if self._lock.locked():
            raise RuntimeError("синхронизация уже идёт")
        async with self._lock:
            run = SyncRun(started_at=datetime.now().isoformat(timespec="seconds"), trigger=trigger)
            self.state.runs.append(run)
            started = time.perf_counter()
            logger.info("Синхронизация каталога с сайтом: старт (%s)", trigger)
            try:
                await self._run(run)
                run.status = "ok"
            except Exception as exc:  # noqa: BLE001 — отчёт должен сохраниться при любой ошибке
                run.status = "error"
                run.message = f"{type(exc).__name__}: {exc}"[:300]
                logger.exception("Синхронизация каталога прервана: %s", run.message)
            finally:
                run.seconds = round(time.perf_counter() - started, 1)
                run.finished_at = datetime.now().isoformat(timespec="seconds")
                self.stage = ""
                self.state.save()
            if run.status == "ok":
                run.message = self._summary(run)
                self.state.save()
                log = logger.warning if run.failed else logger.info
                log("Синхронизация каталога за %.0f с: %s", run.seconds, run.message)
            return run

    async def _run(self, run: SyncRun) -> None:
        self.stage = "Сверяю каталог с сайтом"
        scraped = await self._scrape()
        run.on_site = len(scraped.slugs)
        run.downloaded = scraped.downloaded
        run.errors.extend(scraped.failures[:10])
        run.failed += len(scraped.failures)

        self.stage = "Сверяю каталог с индексом"
        entries = await asyncio.to_thread(load_catalog, self._catalog_path, self._site_base_url)
        indexed_ids = await asyncio.to_thread(self._indexed_ids)
        missing = [e for e in entries if point_id(e.slug) not in indexed_ids]
        site = set(scraped.slugs)
        run.new_on_site = len(missing)
        run.removed_on_site = sum(1 for e in entries if site and e.slug not in site)

        for number, entry in enumerate(missing, start=1):
            self.stage = f"Добавляю в поиск: {number} из {len(missing)}"
            try:
                await self._index(entry)
            except Exception as exc:  # noqa: BLE001 — одно вино не должно ронять остальные
                run.failed += 1
                reason = f"{entry.slug}: в индекс не добавлено ({type(exc).__name__}: {str(exc)[:120]})"
                run.errors.append(reason)
                logger.warning("Синхронизация: %s", reason)
                continue
            run.indexed += 1
            run.added.append({"slug": entry.slug, "title": entry.payload.get("title"),
                              "manufacturer": entry.payload.get("manufacturer"),
                              "region": entry.payload.get("region")})
            logger.info("Новое вино в поиске: «%s» (%s, %s)", entry.payload.get("title") or entry.slug,
                        entry.payload.get("manufacturer") or "—", entry.slug)

        if run.indexed:
            self.stage = "Обновляю каталог в сервисе распознавания"
            try:
                await self._notify_api()
            except Exception as exc:  # noqa: BLE001 — api сам сверится с Qdrant в течение минуты
                logger.warning("api не перечитал каталог сразу (%s) — подхватит при плановой сверке", exc)

    def _indexed_ids(self) -> set[str]:
        if not self._qdrant.collection_exists(self._collection):
            return set()
        ids, offset = set(), None
        while True:
            points, offset = self._qdrant.scroll(
                self._collection, limit=1000, offset=offset, with_payload=False, with_vectors=False
            )
            ids.update(str(p.id) for p in points)
            if offset is None:
                return ids

    async def _index(self, entry: CatalogEntry) -> None:
        _same_owner(entry.image_path, self._catalog_path.parent)  # фото скачано парсером от root
        raw = await asyncio.to_thread(entry.image_path.read_bytes)
        vector = np.asarray([await self._embed(raw, entry.image_path.name)], dtype=np.float32)

        def write() -> None:
            ensure_collection(self._qdrant, self._collection, vector.shape[1])
            upsert_batch(self._qdrant, self._collection, [entry.slug], vector, [entry.payload])

        await asyncio.to_thread(write)

    @staticmethod
    def _summary(run: SyncRun) -> str:
        parts = [f"на сайте {run.on_site} вин", f"новых в поиске {run.indexed}"]
        if run.failed:
            parts.append(f"ошибок {run.failed}")
        if run.removed_on_site:
            parts.append(f"сняты с сайта {run.removed_on_site} (из поиска не удалены)")
        return ", ".join(parts)

    async def schedule_loop(self, check_every_s: float = 10.0) -> None:
        """Запуск по расписанию: интервал отсчитывается от конца прошлого запуска."""
        while True:
            await asyncio.sleep(check_every_s)
            due = self.state.next_run(self.started)
            if due is not None and datetime.now() >= due and not self.running:
                try:
                    await self.run("по расписанию")
                except RuntimeError:
                    pass
