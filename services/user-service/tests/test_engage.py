"""Дневник, вопрос дня, челленджи, призы, открытки, итоги года — на SQLite и каталоге-заглушке.

    PYTHONPATH=libs/wine-logging/src:services/user-service/src .venv/bin/python -m pytest services/user-service/tests -q
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_api import FakeCatalog, _register, _scan, _settings, client  # noqa: F401 — фикстура client

from user_service import dates
from user_service.db import Database, QuizAnswer
from user_service.main import create_app
from user_service.quiz import auto_question


@pytest.fixture()
def client_db(tmp_path, monkeypatch):
    """Клиент + база — для записей «из прошлого» (ответы прошлых дней)."""
    monkeypatch.setattr("user_service.main.settings", _settings(admin_password="admin-pass-123"))
    db = Database("sqlite://")
    with TestClient(create_app(database=db, catalog=FakeCatalog(), media_dir=tmp_path)) as c:
        yield c, db


def _admin(c):
    r = c.post("/v1/auth/login", json={"login": "admin@vino-svoe.local", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text


def _logout(c):
    c.post("/v1/auth/logout")


# ── Дневник ──────────────────────────────────────────────────────────────
def test_diary_requires_account_and_flows(client):
    assert client.post("/v1/me/diary", json={"slug": "kuban-red"}).status_code == 401
    _register(client)
    # «хочу попробовать» → потом «попробовал» превращает ту же запись
    r = client.post("/v1/me/diary", json={"slug": "kuban-red", "kind": "want"})
    assert r.status_code == 201 and r.json()["entry"]["kind"] == "want"
    want_id = r.json()["entry"]["id"]
    r = client.post("/v1/me/diary", json={"slug": "kuban-red", "rating": 5, "tags": ["ягоды", " дуб ", "ягоды"],
                                          "note": "Отлично к стейку", "occasion": "Дома, с друзьями"})
    assert r.status_code == 201, r.text
    entry = r.json()["entry"]
    assert entry["id"] == want_id and entry["kind"] == "tasted" and entry["tags"] == ["ягоды", "дуб"]
    assert entry["wine"]["title"] == "Каберне" and entry["tasted_on"] == dates.today().isoformat()

    client.post("/v1/me/diary", json={"slug": "crimea-white", "kind": "want"})
    listing = client.get("/v1/me/diary").json()
    assert (listing["total"], listing["tasted"], listing["want"]) == (2, 1, 1)
    assert client.get("/v1/me/diary?kind=want").json()["items"][0]["slug"] == "crimea-white"

    assert client.post("/v1/me/diary", json={"slug": "kuban-red", "rating": 9}).status_code == 422
    assert client.post("/v1/me/diary", json={"slug": "nope"}).status_code == 422
    r = client.put(f"/v1/me/diary/{want_id}", json={"kind": "tasted", "rating": 4, "tags": []})
    assert r.json()["entry"]["rating"] == 4
    assert client.delete(f"/v1/me/diary/{want_id}").status_code == 204
    assert client.get("/v1/me/diary").json()["total"] == 1

    # чужую запись не тронуть
    other = client.get("/v1/me/diary").json()["items"][0]["id"]
    _logout(client)
    _register(client, login="bob@example.com")
    assert client.delete(f"/v1/me/diary/{other}").status_code == 404


# ── Вопрос дня ───────────────────────────────────────────────────────────
def test_auto_question_is_stable_and_valid():
    from test_api import WINES
    from user_service.catalog import wine_snapshot

    wines = [wine_snapshot(w) for w in WINES.values()]
    day = dates.today()
    q1, q2 = auto_question(day, wines), auto_question(day, wines)
    assert q1 == q2, "у всех в этот день один и тот же вопрос"
    assert 2 <= len(q1["options"]) <= 4 and len(set(q1["options"])) == len(q1["options"])
    assert 0 <= q1["correct"] < len(q1["options"])
    # вопросы меняются день ото дня
    texts = {auto_question(day + timedelta(days=i), wines)["key"] for i in range(8)}
    assert len(texts) > 1


def test_quiz_anonymous_then_streak(client):
    q = client.get("/v1/quiz/today").json()
    assert q["signed_in"] is False and q["options"] and "correct" not in q
    r = client.post("/v1/quiz/today/answer", json={"choice": 0}).json()
    assert r["saved"] is False and r["streak"] == 0 and isinstance(r["correct"], bool)

    _register(client)
    first = client.post("/v1/quiz/today/answer", json={"choice": 1}).json()
    assert first["saved"] is True and first["streak"] == 1
    # второй ответ за день не меняет первый
    again = client.post("/v1/quiz/today/answer", json={"choice": 0}).json()
    assert again["choice"] == first["choice"] and again["streak"] == 1
    assert client.get("/v1/quiz/today").json()["answered"]["choice"] == first["choice"]


def test_admin_questions_dated(client):
    _admin(client)
    today = dates.today()
    r = client.post("/v1/admin/quiz", json={
        "text": "Кто вывел сорт Цимлянский чёрный?", "options": ["Народная селекция", "Голицын", "Мюллер"],
        "correct": 0, "explanation": "Старинный донской сорт", "day": today.isoformat()})
    assert r.status_code == 201, r.text
    assert client.post("/v1/admin/quiz", json={"text": "Дубль даты?", "options": ["а", "б"], "correct": 0,
                                               "day": today.isoformat()}).status_code == 409
    assert client.post("/v1/admin/quiz", json={"text": "Плохой ответ", "options": ["а", "б"],
                                               "correct": 3, "day": today.isoformat()}).status_code == 422
    # без даты вопрос не принимается: иначе непонятно, когда он выйдет
    assert client.post("/v1/admin/quiz", json={"text": "Без даты", "options": ["а", "б"], "correct": 0}).status_code == 422
    q = client.get("/v1/quiz/today").json()
    assert q["text"].startswith("Кто вывел") and q["image_url"] is None
    res = client.post("/v1/quiz/today/answer", json={"choice": 0}).json()
    assert res["correct"] is True and res["explanation"] == "Старинный донской сорт"

    tomorrow = (today + timedelta(days=1)).isoformat()
    later = client.post("/v1/admin/quiz", json={"text": "Вопрос на завтра", "options": ["да", "нет"],
                                                "correct": 1, "day": tomorrow}).json()
    assert later["day"] == tomorrow
    listing = client.get("/v1/admin/quiz").json()
    assert listing["today"]["text"].startswith("Кто вывел") and len(listing["questions"]) == 2


# ── Призы и челленджи ────────────────────────────────────────────────────
def _pool(client, **over):
    body = {"title": "Скидка 10% в ВинЛаб", "description": "до 31.12", "codes": "AAA-1\nAAA-2\n\nAAA-1",
            "trigger": "challenge"}
    r = client.post("/v1/admin/prizes", json={**body, **over})
    assert r.status_code == 201, r.text
    return r.json()


def test_challenge_awards_prize_once(client):
    _admin(client)
    pool = _pool(client)
    assert (pool["codes_total"], pool["codes_left"]) == (2, 2)
    today = dates.today()
    r = client.post("/v1/admin/challenges", json={
        "title": "Месяц Дона", "starts_on": today.isoformat(), "ends_on": (today + timedelta(days=10)).isoformat(),
        "kind": "wines", "filter_field": "region", "filter_value": "Долина Дона", "target": 1,
        "reward_pool_id": pool["id"]})
    assert r.status_code == 201 and r.json()["condition"] == "1 вино (регион: Долина Дона)"
    assert client.post("/v1/admin/challenges", json={
        "title": "Плохие даты", "starts_on": today.isoformat(), "ends_on": (today - timedelta(days=1)).isoformat(),
        "kind": "wines", "target": 1}).status_code == 422
    _logout(client)

    _register(client)
    ch = client.get("/v1/challenges").json()[0]
    assert (ch["progress"], ch["done"], ch["reward"]["title"]) == (0, False, "Скидка 10% в ВинЛаб")
    assert _scan(client, "kuban-red").json()["new_prizes"] == []  # не тот регион
    prizes = _scan(client, "don-rose").json()["new_prizes"]
    assert [p["code"] for p in prizes] == ["AAA-1"]
    assert _scan(client, "don-rose").json()["new_prizes"] == []  # второй раз — нет
    ch = client.get("/v1/challenges").json()[0]
    assert ch["done"] and ch["prize_code"] == "AAA-1"
    assert [p["code"] for p in client.get("/v1/me/prizes").json()] == ["AAA-1"]

    # второй пользователь получает следующий код, третьему кодов не хватит
    _logout(client)
    _register(client, login="bob@example.com")
    assert [p["code"] for p in _scan(client, "don-rose").json()["new_prizes"]] == ["AAA-2"]
    _logout(client)
    _register(client, login="kate@example.com")
    assert _scan(client, "don-rose").json()["new_prizes"] == []
    _logout(client)
    _admin(client)
    assert client.get("/v1/admin/prizes").json()[0]["codes_left"] == 0
    # дозагрузка кодов к тому же призу
    r = client.put(f"/v1/admin/prizes/{pool['id']}", json={"title": "Скидка 10% в ВинЛаб", "codes": "BBB-1",
                                                            "trigger": "challenge"})
    assert r.json()["codes_left"] == 1


def test_streak_prize_and_achievement(client_db):
    client, db = client_db
    _admin(client)
    _pool(client, title="Бокал в подарок", codes="", shared_code="WINE7", trigger="quiz_streak", trigger_value=3)
    assert client.post("/v1/admin/prizes", json={"title": "Без кодов", "trigger": "challenge"}).status_code == 422
    r = client.post("/v1/admin/achievements", data={"title": "Три дня подряд", "description": "Серия 3",
                                                    "rule_type": "quiz_streak", "threshold": "3"})
    assert r.status_code == 201, r.text
    _logout(client)

    _register(client)
    me = client.get("/v1/me").json()
    user_id = me["user"]["id"]
    # два прошлых дня ответов — пишем в базу напрямую
    with db.engine.begin() as conn:
        for back in (1, 2):
            conn.execute(QuizAnswer.__table__.insert().values(
                user_id=user_id, day=dates.today() - timedelta(days=back), question="q", choice=0, correct=True))
    r = client.post("/v1/quiz/today/answer", json={"choice": 0}).json()
    assert r["streak"] == 3 and r["best"] == 3
    assert [p["code"] for p in r["new_prizes"]] == ["WINE7"]
    assert [a["title"] for a in r["new_achievements"]] == ["Три дня подряд"]


# ── Открытки и итоги года ────────────────────────────────────────────────
def test_gift_open_and_save(client):
    r = client.post("/v1/gifts", json={"slug": "crimea-white", "from_name": "Маша", "message": "С днём рождения!"})
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    gift = client.get(f"/v1/gifts/{token}").json()
    assert gift["from_name"] == "Маша" and gift["wine"]["title"] == "Алиготе"
    assert client.get("/v1/gifts/nope").status_code == 404
    assert client.post(f"/v1/gifts/{token}/save").status_code == 401  # сохранить — только с аккаунтом
    _register(client)
    assert client.post(f"/v1/gifts/{token}/save").json()["kind"] == "want"
    entry = client.get("/v1/me/diary").json()["items"][0]
    assert entry["occasion"] == "Подарок · Маша"
    # открытка от вошедшего — имя из профиля
    assert client.post("/v1/gifts", json={"slug": "kuban-red"}).json()["from_name"] == "Анна"


def test_year_switch(client):
    _register(client)
    _scan(client, "kuban-red")
    client.post("/v1/me/diary", json={"slug": "kuban-red", "rating": 5})
    assert client.get("/v1/me/year").status_code == 403
    _logout(client)
    _admin(client)
    assert client.get("/v1/me/year").status_code == 200  # админ видит до запуска
    r = client.put("/v1/admin/settings/year", json={"enabled": True, "year": dates.today().year})
    assert r.json()["enabled"] is True
    _logout(client)
    client.post("/v1/auth/login", json={"login": "anna@example.com", "password": "secret-123"})
    year = client.get("/v1/me/year").json()
    assert (year["scans"], year["wines"], year["diary"], year["top_region"]) == (1, 1, 1, "Кубань")
    assert year["best_wine"]["title"] == "Каберне" and year["avg_rating"] == 5 and year["best_rating"] == 5
    assert year["top_style"] == "Красное сухое" and year["taste_wine"]["slug"] == "kuban-red"
    assert year["top_region_wines"] == 1 and year["region_names"] == ["Кубань"]
    assert year["badges"] and year["badges"][0]["title"]


def test_delete_account_cleans_engagement(client):
    _register(client)
    client.post("/v1/me/diary", json={"slug": "kuban-red", "rating": 3})
    client.post("/v1/quiz/today/answer", json={"choice": 0})
    token = client.post("/v1/gifts", json={"slug": "kuban-red"}).json()["token"]
    assert client.delete("/v1/me").status_code == 204
    assert client.get(f"/v1/gifts/{token}").status_code == 200  # открытка у получателя продолжает работать


def test_today_question_stays_once_answered(client):
    """Нет своего вопроса — «Угадайте вино»; после первых ответов вопрос дня уже не подменить."""
    _register(client)
    auto = client.get("/v1/quiz/today").json()
    assert auto["image_url"]  # вопрос по бутылке из каталога
    client.post("/v1/quiz/today/answer", json={"choice": 0})
    _logout(client)
    _admin(client)
    assert client.get("/v1/quiz/today").json()["text"] == auto["text"]
    r = client.post("/v1/admin/quiz", json={"text": "На сегодня", "options": ["а", "б"], "correct": 0,
                                            "day": dates.today().isoformat()})
    assert r.status_code == 409


def test_challenge_counts_from_publication(client):
    # скан до публикации челленджа не засчитывается, если не стоит галочка «засчитывать сделанное раньше»
    _register(client)
    _scan(client, "don-rose")
    _logout(client)
    _admin(client)
    today = dates.today()
    body = {"title": "Месяц Дона", "starts_on": (today - timedelta(days=3)).isoformat(),
            "ends_on": (today + timedelta(days=10)).isoformat(), "kind": "wines",
            "filter_field": "region", "filter_value": "Долина Дона", "target": 1}
    fresh = client.post("/v1/admin/challenges", json=body).json()
    old = client.post("/v1/admin/challenges", json={**body, "title": "С прошлым", "count_before": True}).json()
    assert (fresh["count_before"], old["count_before"]) == (False, True)
    _logout(client)
    r = client.post("/v1/auth/login", json={"login": "anna@example.com", "password": "secret-123"})
    assert r.status_code == 200, r.text
    progress = {c["title"]: c["progress"] for c in client.get("/v1/challenges").json()}
    assert progress == {"Месяц Дона": 0, "С прошлым": 1}


def test_new_columns_added_to_old_table(tmp_path):
    # таблица челленджей из прошлой версии — без count_before и published_at
    from sqlalchemy import create_engine, inspect, text

    from user_service.db import Database

    url = f"sqlite:///{tmp_path / 'old.db'}"
    with create_engine(url).begin() as conn:
        conn.execute(text("CREATE TABLE challenges (id INTEGER PRIMARY KEY, title VARCHAR(80))"))
    db = Database(url)
    db.create_schema()
    columns = {c["name"] for c in inspect(db.engine).get_columns("challenges")}
    assert {"count_before", "published_at"} <= columns
    db.create_schema()  # повторный старт ничего не ломает
