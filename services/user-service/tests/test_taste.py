"""«Сомелье, который вас помнит»: профиль вкуса, совпадение, «вы уже пили»."""

from __future__ import annotations

import pytest
from test_api import WINES, _register, _scan, client  # noqa: F401 — фикстура client

from user_service.taste import features, similarity, verdict

MORE = {
    "fanagoria-cab": {"slug": "fanagoria-cab", "title": "Каберне Резерв", "manufacturer": "Фанагория",
                      "region": "Кубань", "category": "Красное сухое", "grapes": ["Каберне Совиньон", "Мерло"]},
    "sweet-white": {"slug": "sweet-white", "title": "Мускат", "manufacturer": "Массандра", "region": "Крым",
                    "category": "Белое полусладкое", "grapes": ["Мускат Белый"]},
    "sweet-white-2": {"slug": "sweet-white-2", "title": "Мускат 2", "manufacturer": "Солнечная долина",
                      "region": "Крым", "category": "Белое полусладкое", "grapes": ["Мускат Белый"]},
    "brut": {"slug": "brut", "title": "Брют", "manufacturer": "Абрау-Дюрсо", "region": "Кубань",
             "category": "Белое брют", "grapes": ["Шардоне"]},
}


@pytest.fixture(autouse=True)
def more_wines(monkeypatch):
    for slug, wine in MORE.items():
        monkeypatch.setitem(WINES, slug, wine)


def _diary(client, slug, rating):
    r = client.post("/v1/me/diary", json={"slug": slug, "rating": rating, "tags": ["Ягоды"], "note": "вишня"})
    assert r.status_code == 201, r.text


def _match(client, slug):
    r = client.get(f"/v1/me/taste/match?slug={slug}")
    assert r.status_code == 200, r.text
    return r.json()


def test_features_and_similarity():
    red = features(MORE["fanagoria-cab"])
    assert (red.color, red.sugar, red.sparkling, red.style) == ("red", "dry", False, "сухое красное")
    brut = features(MORE["brut"])
    assert (brut.color, brut.sugar, brut.sparkling, brut.style) == ("white", "dry", True, "белое игристое")
    sim, common = similarity(features(WINES["kuban-red"]), red)
    assert sim >= 0.9 and common[0] == "тот же сорт" and "та же винодельня" in common
    assert similarity(red, features(MORE["sweet-white"]))[0] < 0.2
    assert verdict(80) == ("Вам понравится", "good") and verdict(30)[1] == "bad"


def test_need_data_then_profile(client):
    assert client.get("/v1/me/taste/match?slug=kuban-red").status_code == 401
    _register(client)
    out = _match(client, "kuban-red")
    assert out["state"] == "need_data" and out["percent"] is None and not out["has_profile"]

    r = client.put("/v1/me/taste", json={"colors": ["red"], "sugars": ["dry"], "grapes": ["Каберне Совиньон"]})
    assert r.status_code == 200 and r.json()["ready"]
    good = _match(client, "kuban-red")
    assert good["state"] == "ready" and good["percent"] >= 90 and good["verdict"] == "Вам понравится"
    assert any("вы любите" in x.lower() or "ваш сорт" in x.lower() for x in good["reasons"])
    bad = _match(client, "sweet-white")
    assert bad["percent"] < 30 and bad["tone"] == "bad"
    assert "полусладкое" in " ".join(bad["reasons"])
    assert client.put("/v1/me/taste", json={"colors": ["blue"]}).status_code == 422


def test_ratings_drive_match_and_explain(client):
    _register(client)
    _diary(client, "kuban-red", 5)
    _diary(client, "sweet-white", 1)
    _scan(client, "crimea-white")
    assert client.put("/v1/me/ratings/crimea-white", json={"value": 1}).status_code == 200
    assert client.get("/v1/me/taste").json()["rated"] == 3

    cab = _match(client, "fanagoria-cab")  # похоже на «Каберне» с 5★: сорт, сухое красное, винодельня
    assert cab["state"] == "ready" and cab["percent"] >= 85
    assert cab["reasons"][0].startswith("Похоже на «Каберне», которому вы поставили 5★")
    muscat = _match(client, "sweet-white-2")  # похоже на «Мускат» с 1★
    assert muscat["percent"] <= 20 and "вы поставили 1★" in muscat["reasons"][0]


def test_already_tried(client):
    _register(client)
    _diary(client, "kuban-red", 4)
    out = _match(client, "kuban-red")
    assert out["percent"] == 82 and out["reasons"] == ["Вы поставили этому вину 4★"]
    assert out["tried"]["kind"] == "tasted" and out["tried"]["rating"] == 4 and out["tried"]["note"] == "вишня"
    _scan(client, "crimea-white")  # скан только что — это не «сканировали раньше»
    assert _match(client, "crimea-white")["tried"] is None


def test_profile_removed_with_account(client):
    _register(client)
    client.put("/v1/me/taste", json={"colors": ["white"]})
    assert client.request("DELETE", "/v1/me").status_code == 204
