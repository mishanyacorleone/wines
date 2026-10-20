"""Местные даты: «сегодня» для вопроса дня и границы дней челленджей и итогов года."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .config import settings


def local_tz() -> timezone:
    return timezone(timedelta(hours=settings.utc_offset_hours))


def today() -> date:
    return datetime.now(local_tz()).date()


def day_start(day: date) -> datetime:
    """Начало местного дня — в UTC, как время в базе."""
    return datetime.combine(day, time.min, tzinfo=local_tz()).astimezone(timezone.utc)


def local_day(moment: datetime) -> date:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(local_tz()).date()
