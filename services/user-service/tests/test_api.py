"""Сквозные тесты ручек на SQLite и каталоге-заглушке (без Postgres и Qdrant).

Запуск из корня репозитория:
    PYTHONPATH=services/user-service/src .venv/bin/python -m pytest services/user-service/tests -q
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from user_service.catalog import wine_snapshot
from user_service.db import Database
from user_service.main import create_app

WINES = {
    "kuban-red": {"slug": "kuban-red", "title": "Каберне", "manufacturer": "Фанагория", "region": "Кубань",
                  "category": "Красное сухое", "grapes": ["Каберне Совиньон"], "image_path": "images/kuban-red.png"},
    "crimea-white": {"slug": "crimea-white", "title": "Алиготе", "manufacturer": "Инкерман", "region": "Крым",
                     "category": "Белое сухое", "grapes": ["Алиготе"]},
    "don-rose": {"slug": "don-rose", "title": "Цимлянский", "manufacturer": "Цимлянские вина",
                 "region": "Долина Дона", "category": "Розовое игристое", "grapes": ["Цимлянский чёрный"]},
    # region не совпадает ни с одним регионом карты — уходит админу
    "krd-white": {"slug": "krd-white", "title": "Шардоне", "manufacturer": "Шато Тамань",
                  "region": "Краснодарский край", "category": "Белое сухое", "grapes": ["Шардоне"]},
}


class FakeCatalog:
    def get(self, slug: str) -> dict | None:
        return wine_snapshot(WINES[slug]) if slug in WINES else None

    def regions(self) -> list[str]:
        return sorted({w["region"] for w in WINES.values()})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("user_service.main.settings", _settings(admin_password="admin-pass-123"))
    app = create_app(database=Database("sqlite://"), catalog=FakeCatalog(), media_dir=tmp_path)
    with TestClient(app) as c:
        yield c


def _settings(**overrides):
    from dataclasses import replace

    from user_service.config import settings

    return replace(settings, **overrides)


def _jpeg(color=(140, 60, 66), size=(900, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _register(client, login="anna@example.com", password="secret-123", name="Анна"):
    return client.post("/v1/auth/register", json={"login": login, "password": password, "name": name})


def _scan(client, slug=None, photo=True, **form):
    files = {"photo": ("p.jpg", _jpeg(), "image/jpeg")} if photo else None
    data = {"status": "match" if slug else "not_found", **form}
    if slug:
        data["slug"] = slug
    return client.post("/v1/me/scans", data=data, files=files)


def test_register_login_logout(client):
    r = _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["name"] == "Анна"
    assert body["user"]["role"] == "user"
    assert body["user"]["avatar"] == {"preset": "grapes", "url": None}
    assert len(body["achievements"]) == 5

    assert client.get("/v1/me").status_code == 200
    assert client.post("/v1/auth/logout").status_code == 204
    assert client.get("/v1/me").status_code == 401

    assert client.post("/v1/auth/login", json={"login": "ANNA@example.com", "password": "wrong-pass"}).status_code == 401
    r = client.post("/v1/auth/login", json={"login": "ANNA@example.com", "password": "secret-123"})
    assert r.status_code == 200
    assert client.get("/v1/me").json()["user"]["login"] == "anna@example.com"


def test_phone_login_normalized(client):
    assert _register(client, login="8 (912) 345-67-89").status_code == 201
    client.post("/v1/auth/logout")
    r = client.post("/v1/auth/login", json={"login": "+7 912 345 67 89", "password": "secret-123"})
    assert r.status_code == 200
    assert r.json()["user"]["login"] == "+79123456789"
    assert _register(client, login="9123456789").status_code == 409


def test_bad_login_and_short_password(client):
    assert _register(client, login="12345").status_code == 422
    assert _register(client, password="short").status_code == 422


def test_scan_history_and_first_achievement(client):
    _register(client)
    r = _scan(client, "kuban-red")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scan"]["wine"]["region"] == "Кубань"
    assert body["scan"]["wine"]["image_url"] == "/catalog-images/kuban-red.png"
    assert body["scan"]["photo_url"].startswith("/v1/media/scans/")
    assert [a["title"] for a in body["new_achievements"]] == ["Первый бокал"]

    # превью сохранено уменьшенным
    photo = client.get(body["scan"]["photo_url"])
    assert photo.status_code == 200
    assert max(Image.open(io.BytesIO(photo.content)).size) == 480

    # не найдено — тоже в истории, но без вина и без достижений
    r = _scan(client)
    assert r.status_code == 201
    assert r.json()["scan"]["status"] == "not_found"
    assert r.json()["new_achievements"] == []

    history = client.get("/v1/me/scans").json()
    assert history["total"] == 2
    assert history["items"][0]["status"] == "not_found"  # сначала новые

    me = client.get("/v1/me").json()
    assert me["stats"] == {"scans": 2, "wines": 1, "regions": 1, "regions_total": 4, "ratings": 0}
    first = next(a for a in me["achievements"] if a["title"] == "Первый бокал")
    assert first["earned"] and first["progress"] == 1


def test_scan_unknown_wine_and_anonymous(client):
    assert _scan(client, "kuban-red").status_code == 401
    _register(client)
    assert _scan(client, "no-such-wine").status_code == 422
    r = client.post("/v1/me/scans", data={"status": "match"})
    assert r.status_code == 422


def test_correct_scan_and_regions_achievements(client):
    _register(client)
    scan_id = _scan(client).json()["scan"]["id"]
    # пользователь сам выбрал вино в «Не то вино?»
    r = client.patch(f"/v1/me/scans/{scan_id}", json={"slug": "crimea-white"})
    assert r.status_code == 200
    titles = {a["title"] for a in r.json()["new_achievements"]}
    assert titles == {"Первый бокал", "Крымский берег"}

    _scan(client, "kuban-red")
    r = _scan(client, "don-rose")
    assert [a["title"] for a in r.json()["new_achievements"]] == ["Винный путешественник"]


def test_regions_map_progress(client):
    _register(client)
    assert client.get("/v1/me/regions").json() == {"regions": []}
    r = _scan(client, "kuban-red")
    assert r.json()["new_region"] == "Кубань"
    assert _scan(client, "kuban-red").json()["new_region"] is None   # регион уже открыт
    assert _scan(client).json()["new_region"] is None                # не распознано
    scan_id = _scan(client).json()["scan"]["id"]
    # «Не то вино?» → выбрал крымское — открылся Крым
    assert client.patch(f"/v1/me/scans/{scan_id}", json={"slug": "crimea-white"}).json()["new_region"] == "Крым"
    regions = client.get("/v1/me/regions").json()["regions"]
    assert [r["name"] for r in regions] == ["Кубань", "Крым"]          # по времени открытия
    assert [w["slug"] for w in regions[0]["wines"]] == ["kuban-red"]  # без повторов
    assert client.get("/v1/me").json()["stats"]["regions"] == 2


def test_ratings_only_for_scanned_wines(client):
    _register(client)
    assert client.put("/v1/me/ratings/kuban-red", json={"value": 1}).status_code == 404
    _scan(client, "kuban-red")
    assert client.put("/v1/me/ratings/kuban-red", json={"value": 1}).json()["value"] == 1
    assert client.get("/v1/me/scans").json()["items"][0]["rating"] == 1
    client.put("/v1/me/ratings/kuban-red", json={"value": -1})
    assert client.get("/v1/me/scans").json()["items"][0]["rating"] == -1
    client.put("/v1/me/ratings/kuban-red", json={"value": 0})
    assert client.get("/v1/me/scans").json()["items"][0]["rating"] == 0
    assert client.put("/v1/me/ratings/kuban-red", json={"value": 5}).status_code == 422


def test_profile_name_and_avatars(client):
    _register(client)
    assert client.patch("/v1/me", json={"name": "  Анна К.  "}).json()["name"] == "Анна К."
    assert client.patch("/v1/me", json={"name": "   "}).status_code == 422
    presets = client.get("/v1/avatars").json()
    assert len(presets) >= 6
    assert client.patch("/v1/me", json={"avatar_preset": "crimea"}).json()["avatar"]["preset"] == "crimea"
    assert client.patch("/v1/me", json={"avatar_preset": "nope"}).status_code == 422

    r = client.put("/v1/me/avatar", files={"image": ("a.jpg", _jpeg(size=(800, 400)), "image/jpeg")})
    url = r.json()["avatar"]["url"]
    assert url and r.json()["avatar"]["preset"] is None
    assert Image.open(io.BytesIO(client.get(url).content)).size == (256, 256)
    bad = client.put("/v1/me/avatar", files={"image": ("a.jpg", b"not an image", "image/jpeg")})
    assert bad.status_code == 400

    # вернулся к готовому аватару — загруженный файл удалён
    client.patch("/v1/me", json={"avatar_preset": "bottle"})
    assert client.get(url).status_code == 404


def test_media_path_traversal(client):
    assert client.get("/v1/media/scans/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/v1/media/other/abc.jpg").status_code == 404


def test_admin_manages_achievements(client):
    _register(client)
    assert client.get("/v1/admin/achievements").status_code == 403
    client.post("/v1/auth/logout")

    r = client.post("/v1/auth/login", json={"login": "admin@vino-svoe.local", "password": "admin-pass-123"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"

    rules = client.get("/v1/admin/achievements/rules").json()
    assert "region" in {x["type"] for x in rules["rules"]}
    assert rules["regions"] == ["Долина Дона", "Краснодарский край", "Крым", "Кубань"]

    png = io.BytesIO()
    Image.new("RGBA", (600, 600), (200, 50, 80, 255)).save(png, format="PNG")
    r = client.post("/v1/admin/achievements", data={
        "title": "Игристое настроение", "description": "Отсканируйте игристое",
        "rule_type": "category", "rule_value": "игристое", "threshold": "1",
    }, files={"image": ("b.png", png.getvalue(), "image/png")})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["image_url"].endswith(".png")
    assert Image.open(io.BytesIO(client.get(created["image_url"]).content)).size == (256, 256)

    # вытянутая картинка вписывается в квадрат, а не растягивает сетку значков
    tall = io.BytesIO()
    Image.new("RGBA", (100, 400), (10, 10, 10, 255)).save(tall, format="PNG")
    r = client.patch(f"/v1/admin/achievements/{created['id']}", files={"image": ("t.png", tall.getvalue(), "image/png")})
    assert Image.open(io.BytesIO(client.get(r.json()["image_url"]).content)).size == (256, 256)
    created = r.json()

    assert client.post("/v1/admin/achievements", data={
        "title": "x", "description": "y", "rule_type": "region"}).status_code == 422
    assert client.post("/v1/admin/achievements", data={
        "title": "x", "description": "y", "rule_type": "magic"}).status_code == 422

    # выключенное достижение не видно пользователям, но видно админу
    first = next(a for a in client.get("/v1/admin/achievements").json() if a["title"] == "Ценитель")
    assert client.patch(f"/v1/admin/achievements/{first['id']}", data={"is_active": "false"}).json()["is_active"] is False
    assert "Ценитель" not in {a["title"] for a in client.get("/v1/achievements").json()}
    assert "Ценитель" in {a["title"] for a in client.get("/v1/admin/achievements").json()}

    # новое достижение выдаётся тому, у кого условие уже выполнено
    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"login": "anna@example.com", "password": "secret-123"})
    _scan(client, "don-rose")
    me = client.get("/v1/me").json()
    assert any(a["title"] == "Игристое настроение" and a["earned"] for a in me["achievements"])

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"login": "admin@vino-svoe.local", "password": "admin-pass-123"})
    assert client.delete(f"/v1/admin/achievements/{created['id']}").status_code == 204
    assert client.get(created["image_url"]).status_code == 404


def test_delete_account(client):
    _register(client)
    photo_url = _scan(client, "kuban-red").json()["scan"]["photo_url"]
    assert client.delete("/v1/me").status_code == 204
    assert client.get("/v1/me").status_code == 401
    assert client.get(photo_url).status_code == 404
    # почта освободилась
    assert _register(client).status_code == 201


def test_login_rate_limit(client):
    _register(client)
    client.post("/v1/auth/logout")
    for _ in range(10):
        client.post("/v1/auth/login", json={"login": "anna@example.com", "password": "wrong-pass"})
    r = client.post("/v1/auth/login", json={"login": "anna@example.com", "password": "secret-123"})
    assert r.status_code == 429


def test_import_anonymous_scan_keeps_time(client):
    _register(client)
    r = _scan(client, "kuban-red", scanned_at="2026-10-01T12:00:00+00:00")
    assert r.json()["scan"]["scanned_at"].startswith("2026-10-01T12:00:00")
    # будущее время не принимается — обрезается до текущего
    r = _scan(client, "kuban-red", scanned_at="2099-01-01T00:00:00+00:00")
    assert not r.json()["scan"]["scanned_at"].startswith("2099")


def _admin(client):
    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"login": "admin@vino-svoe.local", "password": "admin-pass-123"}).status_code == 200


def test_map_zones_and_admin_matching(client):
    zones = client.get("/v1/map/zones").json()
    names = {z["name"]: z["subjects"] for z in zones["zones"]}
    assert len(names) == 9 and names["Крым"] == ["UA-43", "UA-40"] and zones["aliases"] == []

    # Совпало по названию — сразу регион карты; не совпало — как в каталоге, не на карте
    _register(client)
    assert _scan(client, "kuban-red").json()["new_region"] == "Кубань"
    assert _scan(client, "krd-white").json()["new_region"] == "Краснодарский край"
    regions = {r["name"]: r["on_map"] for r in client.get("/v1/me/regions").json()["regions"]}
    assert regions == {"Кубань": True, "Краснодарский край": False}
    assert client.put("/v1/admin/map/aliases", json={"catalog_region": "x", "zone_id": 1}).status_code == 403

    # Админ относит «Краснодарский край» к Кубани
    _admin(client)
    kuban = next(z["id"] for z in zones["zones"] if z["name"] == "Кубань")
    out = client.put("/v1/admin/map/aliases", json={"catalog_region": "Краснодарский край", "zone_id": kuban}).json()
    assert out["aliases"] == [{"catalog_region": "Краснодарский край", "zone_id": kuban, "zone": "Кубань"}]

    # Новый регион: название + субъекты; дубликат названия и чужой код — ошибки
    r = client.post("/v1/admin/map/zones", json={"name": "Сибирь", "subjects": ["ru-nvs", "RU-OMS", "RU-NVS"]})
    assert r.status_code == 201
    assert next(z for z in r.json()["zones"] if z["name"] == "Сибирь")["subjects"] == ["RU-NVS", "RU-OMS"]
    assert client.post("/v1/admin/map/zones", json={"name": "сибирь", "subjects": ["RU-OMS"]}).status_code == 409
    assert client.post("/v1/admin/map/zones", json={"name": "Урал", "subjects": ["XX"]}).status_code == 422

    client.post("/v1/auth/logout")
    client.post("/v1/auth/login", json={"login": "anna@example.com", "password": "secret-123"})
    regions = client.get("/v1/me/regions").json()["regions"]
    assert [(r["name"], r["on_map"], len(r["wines"])) for r in regions] == [("Кубань", True, 2)]
    assert client.get("/v1/me").json()["stats"]["regions"] == 1
    # вино из уже открытой Кубани (через сопоставление) — не новый регион
    assert _scan(client, "krd-white").json()["new_region"] is None

    # удалили регион — его вина снова не на карте
    _admin(client)
    assert client.delete(f"/v1/admin/map/zones/{kuban}").json()["aliases"] == []


def test_share_template(client):
    default = client.get("/v1/settings/share").json()
    assert default["wine_template"] == default["default_wine_template"]
    assert "{название}" in default["wine_template"]
    _register(client)
    assert client.put("/v1/admin/settings/share", json={"wine_template": "x"}).status_code == 403

    _admin(client)
    text = "Нашлось то самое {цвет} {сахар} {сорт}! Попробуй тоже: {ссылка}"
    assert client.put("/v1/admin/settings/share", json={"wine_template": text}).json()["wine_template"] == text
    assert client.get("/v1/settings/share").json()["wine_template"] == text   # видно всем, без входа
    assert client.put("/v1/admin/settings/share", json={"wine_template": "   "}).status_code == 422
    # сброс — снова по умолчанию
    client.put("/v1/admin/settings/share", json={"wine_template": default["default_wine_template"]})
    assert client.get("/v1/settings/share").json()["wine_template"] == default["default_wine_template"]
