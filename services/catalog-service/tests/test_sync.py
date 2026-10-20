"""Синхронизация каталога на заглушках: Qdrant в памяти, без сайта, embedder и api.

Запуск из корня репозитория:
    PYTHONPATH=libs/wine-logging/src:services/catalog-scraper/src:services/indexer/src:services/catalog-service/src \
        .venv/bin/python -m pytest services/catalog-service/tests -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from qdrant_client import QdrantClient

from catalog_service.main import create_app
from catalog_service.sync import ScheduleSettings, ScrapeResult, SyncState, Syncer
from indexer.store import point_id


def _add_wine(catalog: Path, slug: str, **fields) -> None:
    (catalog / "images").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 16), "red").save(catalog / "images" / f"{slug}.png")
    row = {"slug": slug, "image_path": f"images/{slug}.png", "title": slug.title(), **fields}
    with (catalog / "catalog.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


class Fakes:
    def __init__(self, catalog: Path) -> None:
        self.catalog = catalog
        self.site = ["kuban-red", "crimea-white"]
        self.new_on_site: list[str] = []
        self.broken: set[str] = set()
        self.notified = 0

    async def scrape(self) -> ScrapeResult:
        for slug in self.new_on_site:
            _add_wine(self.catalog, slug, manufacturer="Новая винодельня", region="Крым")
        self.site += self.new_on_site
        downloaded, self.new_on_site = len(self.new_on_site), []
        return ScrapeResult(slugs=list(self.site), downloaded=downloaded, failures=[])

    async def embed(self, raw: bytes, filename: str) -> list[float]:
        if filename.removesuffix(".png") in self.broken:
            raise RuntimeError("embedder недоступен")
        return [1.0, float(len(raw) % 7), 0.5, 0.25]

    async def notify(self) -> None:
        self.notified += 1


@pytest.fixture()
def env(tmp_path: Path):
    catalog = tmp_path / "catalog"
    _add_wine(catalog, "kuban-red", region="Кубань")
    _add_wine(catalog, "crimea-white", region="Крым")
    fakes = Fakes(catalog)
    qdrant = QdrantClient(":memory:")
    syncer = Syncer(
        state=SyncState(catalog / "sync-state.json", ScheduleSettings(auto=True, times=1, per="day")),
        qdrant=qdrant, collection="wines", catalog_path=catalog / "catalog.jsonl",
        site_base_url="https://vino-svoe.ru", scrape=fakes.scrape, embed=fakes.embed, notify_api=fakes.notify,
    )
    return syncer, fakes, qdrant


def test_first_run_indexes_catalog_and_second_adds_nothing(env):
    syncer, fakes, qdrant = env
    run = asyncio.run(syncer.run("тест"))
    assert run.status == "ok" and run.indexed == 2 and run.failed == 0
    assert qdrant.count("wines").count == 2
    assert fakes.notified == 1

    again = asyncio.run(syncer.run("тест"))
    assert again.indexed == 0 and fakes.notified == 1  # нечего добавлять — api не дёргаем
    saved = json.loads((fakes.catalog / "sync-state.json").read_text(encoding="utf-8"))
    assert [r["indexed"] for r in saved["runs"]] == [2, 0]


def test_new_wine_from_site_gets_into_index_with_indexer_payload(env):
    syncer, fakes, qdrant = env
    asyncio.run(syncer.run("тест"))
    fakes.new_on_site = ["new-rose"]
    run = asyncio.run(syncer.run("тест"))
    assert run.indexed == 1 and run.downloaded == 1
    assert run.added[0]["slug"] == "new-rose"
    point = qdrant.retrieve("wines", [point_id("new-rose")], with_payload=True)[0]
    # тот же payload, что пишет индексатор: ссылка на сайт, путь к фото
    assert point.payload["url"] == "https://vino-svoe.ru/wines/new-rose"
    assert point.payload["image_path"] == "images/new-rose.png"
    assert point.payload["manufacturer"] == "Новая винодельня"


def test_one_broken_wine_does_not_stop_others(env):
    syncer, fakes, qdrant = env
    fakes.broken = {"kuban-red"}
    run = asyncio.run(syncer.run("тест"))
    assert run.status == "ok" and run.indexed == 1 and run.failed == 1
    assert "kuban-red" in run.errors[0]
    assert qdrant.count("wines").count == 1


def test_wines_removed_from_site_are_counted_not_deleted(env):
    syncer, fakes, qdrant = env
    asyncio.run(syncer.run("тест"))
    fakes.site = ["kuban-red"]
    run = asyncio.run(syncer.run("тест"))
    assert run.removed_on_site == 1
    assert qdrant.count("wines").count == 2


def test_api_admin_only_and_schedule(env):
    syncer, fakes, _ = env

    async def admin_check(request):
        return "admin@test" if request.headers.get("x-admin") else None

    with TestClient(create_app(syncer=syncer, admin_check=admin_check)) as client:
        assert client.get("/v1/admin/catalog/sync").status_code == 403
        headers = {"x-admin": "1"}
        status = client.get("/v1/admin/catalog/sync", headers=headers).json()
        assert status["schedule"] == {"auto": True, "times": 1, "per": "day", "text": "1 раз в день"}
        assert status["next_run_at"]

        res = client.put("/v1/admin/catalog/sync/settings", headers=headers,
                         json={"auto": True, "times": 3, "per": "hour"})
        assert res.json()["schedule"]["text"] == "3 раза в час"
        # чаще раза в минуту нельзя
        res = client.put("/v1/admin/catalog/sync/settings", headers=headers,
                         json={"auto": True, "times": 2, "per": "minute"})
        assert res.status_code == 422
        res = client.put("/v1/admin/catalog/sync/settings", headers=headers,
                         json={"auto": False, "times": 1, "per": "week"})
        assert res.json()["schedule"]["per"] == "week" and res.json()["next_run_at"] is None

        assert client.post("/v1/admin/catalog/sync", headers=headers).status_code == 202
        for _ in range(50):
            status = client.get("/v1/admin/catalog/sync", headers=headers).json()
            if not status["running"]:
                break
        assert status["runs"][0]["status"] == "ok"
        assert status["runs"][0]["trigger"] == "вручную, admin@test"
        assert status["indexed"] == 2


def test_schedule_from_old_state_file(tmp_path):
    """sync-state.json до вечера 19.10 хранил «раз в N часов» — читается без потери расписания."""
    path = tmp_path / "sync-state.json"
    path.write_text('{"schedule": {"auto": true, "interval_hours": 12}, "runs": []}', encoding="utf-8")
    schedule = SyncState(path, ScheduleSettings()).schedule
    assert (schedule.times, schedule.per, schedule.describe()) == (2, "day", "2 раза в день")
    assert schedule.interval.total_seconds() == 12 * 3600
