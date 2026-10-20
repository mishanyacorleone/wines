"""Запись в файлы по дням: logs/<дата>/all.log и logs/<дата>/services/<сервис>.log.

Одна строка — одно событие:

    2026-10-19 14:38:48.123 INFO  api        scan       [7f3a91c2] Скан: найдено «…» …

дата и время (местные) · уровень · сервис · тема · номер запроса · текст.
Запись — из одного процесса (сборщика), поэтому строки разных сервисов в
all.log не перемешиваются посередине.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TextIO

from .parsers import Record

logger = logging.getLogger("logs")


class DailyWriter:
    def __init__(self, root: Path, keep_days: int) -> None:
        self.root = root
        self.keep_days = keep_days
        self._lock = threading.Lock()
        self._day: date | None = None
        self._files: dict[str, TextIO] = {}
        root.mkdir(parents=True, exist_ok=True)

    def write(self, when: datetime, service: str, record: Record) -> None:
        stamp = when.strftime("%Y-%m-%d %H:%M:%S.") + f"{when.microsecond // 1000:03d}"
        line = f"{stamp} {record.level:<5} {service:<10} {record.topic:<10} {record.text}\n"
        with self._lock:
            if when.date() != self._day:
                self._switch_day(when.date())
            self._file(f"services/{service}.log").write(line)
            if record.to_all:
                self._file("all.log").write(line)

    def flush(self) -> None:
        with self._lock:
            for file in self._files.values():
                file.flush()

    def _file(self, name: str) -> TextIO:
        file = self._files.get(name)
        if file is None:
            path = self.root / self._day.isoformat() / name  # type: ignore[union-attr]
            path.parent.mkdir(parents=True, exist_ok=True)
            self._own(path.parent)
            file = path.open("a", encoding="utf-8")
            self._own(path)
            self._files[name] = file
        return file

    def _switch_day(self, day: date) -> None:
        for file in self._files.values():
            file.close()
        self._files.clear()
        self._day = day
        self._cleanup(day)

    def _own(self, path: Path) -> None:
        """В контейнере сборщик работает от root — файлы отдаём владельцу папки logs/,
        иначе на хосте их не удалить без sudo."""
        try:
            stat = self.root.stat()
            os.chown(path, stat.st_uid, stat.st_gid)
        except OSError:
            pass

    def _cleanup(self, today: date) -> None:
        """Папки старше keep_days дней удаляются при смене дня."""
        oldest = today - timedelta(days=self.keep_days)
        for folder in self.root.iterdir():
            try:
                day = date.fromisoformat(folder.name)
            except ValueError:
                continue
            if folder.is_dir() and day < oldest:
                shutil.rmtree(folder, ignore_errors=True)
                logger.info("Удалены логи за %s (храним %d дней)", folder.name, self.keep_days)
