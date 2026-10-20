"""«Хочу съездить»: список виноделен человека."""

from __future__ import annotations

from test_api import _register, client  # noqa: F401 — фикстура client


def test_trips_need_account(client):
    assert client.get("/v1/me/trips").status_code == 401
    assert client.put("/v1/me/trips/esse", json={"name": "ESSE"}).status_code == 401


def test_add_list_remove(client):
    _register(client)
    r = client.put("/v1/me/trips/esse", json={"name": "ESSE", "region": "Крым", "image": "/site-media/240/x.webp"})
    assert r.status_code == 200, r.text
    client.put("/v1/me/trips/fanagoriya", json={"name": "Фанагория", "region": "Кубань"})
    client.put("/v1/me/trips/esse", json={"name": "ESSE", "region": "Крым"})  # повтор — не дубль
    trips = client.get("/v1/me/trips").json()
    assert [t["winery"] for t in trips] == ["fanagoriya", "esse"]
    assert client.put("/v1/me/trips/Bad Slug", json={"name": "x"}).status_code == 422
    assert client.delete("/v1/me/trips/esse").status_code == 204
    assert [t["winery"] for t in client.get("/v1/me/trips").json()] == ["fanagoriya"]
    # удаление аккаунта уносит и поездки
    assert client.delete("/v1/me").status_code == 204
