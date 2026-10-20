"""«Вопрос дня»: один вопрос в день для всех, серия дней подряд — только с аккаунтом.

Откуда вопрос на сегодня:
1. вопрос админа, назначенный на эту дату (дата обязательна);
2. иначе — «Угадайте вино»: вопрос по бутылке из каталога, фото вина и «из какого
   региона / сорта, какого цвета, какое по сладости». Выбор детерминирован датой —
   у всех один и тот же вопрос, а каталог (~50 новых вин в день) не кончится.

Очереди вопросов без даты нет (была до 20.10): вопрос, добавленный без даты, сам
занимал ближайший день, и владелец вместо «Угадайте вино» увидел тестовый вопрос.

Без аккаунта отвечать можно: ответ проверяется, но серия не копится — это
и есть повод зарегистрироваться.
"""

# Без `from __future__ import annotations`: модели запросов объявлены внутри mount
import logging
import random
from datetime import date

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import rewards
from .dates import today
from .db import QuizAnswer, QuizQuestion

logger = logging.getLogger("quiz")

COLORS = ("красное", "белое", "розовое", "оранжевое")
SUGARS = ("экстра брют", "брют", "сухое", "полусухое", "полусладкое", "сладкое")
AUTO_TEXT = {
    "region": "Из какого региона это вино?",
    "grape": "Из какого сорта винограда это вино?",
    "color": "Какого цвета это вино?",
    "sugar": "Какое это вино по сладости?",
}


def _attr(wine: dict, kind: str) -> str | None:
    category = (wine.get("category") or "").lower().replace("ё", "е")
    if kind == "region":
        return wine.get("region")
    if kind == "grape":
        grapes = wine.get("grapes") or []
        # купаж — неоднозначный вопрос; «Белые сорта винограда» — заглушка каталога, а не сорт
        return grapes[0] if len(grapes) == 1 and "сорта" not in grapes[0].lower() else None
    if kind == "color":
        return next((c.capitalize() for c in COLORS if c in category), None)
    if kind == "sugar":
        # «полусухое» содержит «сухое» — сначала длинные варианты
        return next((s.capitalize() for s in sorted(SUGARS, key=len, reverse=True) if s in category), None)
    return None


def auto_question(day: date, wines: list[dict]) -> dict | None:
    """Вопрос по бутылке из каталога: одинаковый у всех в этот день."""
    rng = random.Random(day.toordinal())
    pool = [w for w in wines if w.get("image_url")]
    if not pool:
        return None
    kinds = list(AUTO_TEXT)
    start = day.toordinal() % len(kinds)
    for kind in kinds[start:] + kinds[:start]:
        # вино с фото — загадка, варианты-обманки — из всего каталога
        values = sorted({v for w in wines if (v := _attr(w, kind))})
        candidates = [w for w in pool if _attr(w, kind)]
        if len(values) < 2 or not candidates:
            continue
        wine = rng.choice(candidates)
        answer = _attr(wine, kind)
        others = rng.sample([v for v in values if v != answer], k=min(3, len(values) - 1))
        options = [answer, *others]
        rng.shuffle(options)
        facts = ", ".join(x for x in [wine.get("region"), ", ".join(wine.get("grapes") or []), wine.get("category")] if x)
        return {
            "key": f"auto:{wine['slug']}:{kind}",
            "text": AUTO_TEXT[kind],
            "options": options,
            "correct": options.index(answer),
            "explanation": f"Это «{wine['title']}»{', ' + wine['manufacturer'] if wine.get('manufacturer') else ''}"
                           f" — {facts}.",
            "image_url": wine.get("image_url"),
            "wine_slug": wine["slug"],
        }
    return None


def answered_on(session: Session, day: date) -> bool:
    return session.scalar(select(QuizAnswer.id).where(QuizAnswer.day == day).limit(1)) is not None


def question_for(session: Session, catalog, day: date) -> dict | None:
    row = session.scalar(select(QuizQuestion).where(QuizQuestion.day == day, QuizQuestion.is_active.is_(True)))
    if row is not None:
        return {"key": f"q:{row.id}", "text": row.text, "options": list(row.options), "correct": row.correct,
                "explanation": row.explanation, "image_url": None, "wine_slug": None}
    return auto_question(day, catalog.wines())


def mount(app: FastAPI, d) -> None:
    SessionDep, OptionalUserDep, AdminDep, CatalogDep = d.SessionDep, d.OptionalUserDep, d.AdminDep, d.CatalogDep

    class Result(BaseModel):
        choice: int
        correct: bool
        correct_index: int
        explanation: str | None
        wine_slug: str | None

    class QuizOut(BaseModel):
        day: date
        text: str
        options: list[str]
        image_url: str | None
        signed_in: bool
        streak: int = 0
        best: int = 0
        answered: Result | None = None

    class AnswerIn(BaseModel):
        choice: int = Field(ge=0, le=3)

    class AnswerOut(Result):
        saved: bool
        streak: int
        best: int
        new_achievements: list = []
        new_prizes: list = []

    class QuestionIn(BaseModel):
        text: str = Field(min_length=3, max_length=300)
        options: list[str] = Field(min_length=2, max_length=4)
        correct: int = Field(ge=0, le=3)
        explanation: str | None = Field(default=None, max_length=1000)
        day: date  # в какой день показать; в дни без своего вопроса — «Угадайте вино»
        is_active: bool = True

        @field_validator("options")
        @classmethod
        def clean(cls, value: list[str]) -> list[str]:
            items = [v.strip() for v in value if v.strip()]
            if len(items) < 2 or any(len(v) > 120 for v in items):
                raise ValueError("2–4 варианта, каждый до 120 символов")
            return items

    class QuestionOut(QuestionIn):
        id: int
        shown: bool  # день прошёл или сегодня

    def today_question(session: Session, catalog) -> dict:
        q = question_for(session, catalog, today())
        if q is None:
            raise HTTPException(status_code=503, detail="Вопрос дня временно недоступен")
        return q

    def result(q: dict, choice: int) -> Result:
        return Result(choice=choice, correct=choice == q["correct"], correct_index=q["correct"],
                      explanation=q["explanation"], wine_slug=q["wine_slug"])

    def question_out(row: QuizQuestion) -> QuestionOut:
        return QuestionOut(id=row.id, text=row.text, options=list(row.options), correct=row.correct,
                           explanation=row.explanation, day=row.day, is_active=row.is_active,
                           shown=row.day is not None and row.day <= today())

    @app.get("/v1/quiz/today", response_model=QuizOut)
    def quiz_today(session: SessionDep, user: OptionalUserDep, catalog: CatalogDep) -> QuizOut:
        q = today_question(session, catalog)
        out = QuizOut(day=today(), text=q["text"], options=q["options"], image_url=q["image_url"],
                      signed_in=user is not None)
        if user is not None:
            out.streak, out.best = rewards.streaks(session, user.id)
            answer = session.scalar(select(QuizAnswer).where(QuizAnswer.user_id == user.id,
                                                             QuizAnswer.day == today()))
            if answer is not None and answer.question == q["key"]:
                out.answered = result(q, answer.choice)
        return out

    @app.post("/v1/quiz/today/answer", response_model=AnswerOut)
    def quiz_answer(body: AnswerIn, session: SessionDep, user: OptionalUserDep, catalog: CatalogDep) -> AnswerOut:
        q = today_question(session, catalog)
        if body.choice >= len(q["options"]):
            raise HTTPException(status_code=422, detail="Нет такого варианта")
        if user is None:
            return AnswerOut(**result(q, body.choice).model_dump(), saved=False, streak=0, best=0)
        existing = session.scalar(select(QuizAnswer).where(QuizAnswer.user_id == user.id, QuizAnswer.day == today()))
        choice = existing.choice if existing else body.choice  # второй ответ за день не меняет первый
        res = result(q, choice)
        new_achievements, new_prizes = [], []
        if existing is None:
            session.add(QuizAnswer(user_id=user.id, day=today(), question=q["key"], choice=choice,
                                   correct=res.correct))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            new_achievements = d.award(session, user)
            new_prizes = [rewards.prize_out(session, p) for p in rewards.check(session, user.id)]
        streak, best = rewards.streaks(session, user.id)
        if existing is None:
            logger.info("Вопрос дня: пользователь %s — %s, серия %d", d.who(user),
                        "верно" if res.correct else "неверно", streak)
        return AnswerOut(**res.model_dump(), saved=True, streak=streak, best=best,
                         new_achievements=new_achievements, new_prizes=new_prizes)

    # ── админ ──
    @app.get("/v1/admin/quiz")
    def admin_quiz(_: AdminDep, session: SessionDep, catalog: CatalogDep) -> dict:
        rows = session.scalars(select(QuizQuestion).order_by(QuizQuestion.day.is_(None), QuizQuestion.day.desc(),
                                                             QuizQuestion.id)).all()
        q = question_for(session, catalog, today())
        return {"questions": [question_out(r) for r in rows], "today": q}

    def check_day(session: Session, day: date | None, exclude_id: int | None = None) -> None:
        if day is None:
            return
        taken = session.scalar(select(QuizQuestion.id).where(QuizQuestion.day == day))
        if taken is not None and taken != exclude_id:
            raise HTTPException(status_code=409, detail=f"На {day:%d.%m.%Y} уже есть вопрос")
        if day <= today() and answered_on(session, day):
            raise HTTPException(status_code=409, detail="На этот день уже отвечают — поставьте вопрос на завтра или позже")

    @app.post("/v1/admin/quiz", response_model=QuestionOut, status_code=201)
    def create_question(body: QuestionIn, admin: AdminDep, session: SessionDep) -> QuestionOut:
        if body.correct >= len(body.options):
            raise HTTPException(status_code=422, detail="Отметьте верный вариант среди заполненных")
        check_day(session, body.day)
        row = QuizQuestion(**body.model_dump())
        session.add(row)
        session.commit()
        d.admin_log.info("Админ %s добавил вопрос дня «%s»%s", d.who(admin), row.text[:60],
                         f" на {row.day:%d.%m}" if row.day else "")
        return question_out(row)

    @app.put("/v1/admin/quiz/{question_id}", response_model=QuestionOut)
    def update_question(question_id: int, body: QuestionIn, admin: AdminDep, session: SessionDep) -> QuestionOut:
        row = session.get(QuizQuestion, question_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Вопрос не найден")
        if body.correct >= len(body.options):
            raise HTTPException(status_code=422, detail="Отметьте верный вариант среди заполненных")
        check_day(session, body.day, exclude_id=row.id)
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        session.commit()
        d.admin_log.info("Админ %s изменил вопрос дня «%s»", d.who(admin), row.text[:60])
        return question_out(row)

    @app.delete("/v1/admin/quiz/{question_id}", status_code=204)
    def delete_question(question_id: int, admin: AdminDep, session: SessionDep) -> Response:
        row = session.get(QuizQuestion, question_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Вопрос не найден")
        session.delete(row)
        session.commit()
        d.admin_log.info("Админ %s удалил вопрос дня «%s»", d.who(admin), row.text[:60])
        return Response(status_code=204)
