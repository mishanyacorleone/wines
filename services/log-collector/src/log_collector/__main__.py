"""Сборщик логов: python -m log_collector

Читает stdout всех контейнеров проекта через Docker API (/var/run/docker.sock)
и пишет его в logs/<дата>/: all.log — важное по всем сервисам, services/<сервис>.log —
всё по одному сервису. Сами события Docker тоже попадают в all.log:
«контейнер api остановился (код 137)», «vllm: проверка здоровья не прошла».

Сервисам ничего не нужно знать про файлы: они пишут в stdout, как принято в
контейнерах, а `docker compose logs` продолжает работать как раньше.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import docker
from docker.errors import DockerException, NotFound

import wine_logging

from .parsers import Record, parse
from .writer import DailyWriter

logger = logging.getLogger("logs")

PROJECT = os.environ.get("WINE_LOGS_PROJECT", "wines")
ROOT = Path(os.environ.get("WINE_LOGS_DIR", "/logs"))
KEEP_DAYS = int(os.environ.get("WINE_LOGS_KEEP_DAYS", "30"))
STATE = ROOT / ".collector-state.json"
BACKFILL = timedelta(minutes=10)  # контейнер запущен раньше сборщика не больше чем на столько — читаем с его старта

_EXIT_CODES = {0: "штатно", 1: "с ошибкой", 137: "убит (SIGKILL или нехватка памяти)", 143: "по SIGTERM"}


class Collector:
    def __init__(self) -> None:
        self.client = docker.from_env()
        self.writer = DailyWriter(ROOT, KEEP_DAYS)
        self.started = datetime.now(timezone.utc)
        self.following: dict[str, threading.Thread] = {}
        self.last_seen: dict[str, str] = self._load_state()
        self.lock = threading.Lock()
        self.stop = threading.Event()

    # ── состояние: на чём остановились в каждом контейнере ─────────────────
    def _load_state(self) -> dict[str, str]:
        try:
            return json.loads(STATE.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        with self.lock:
            data = dict(self.last_seen)
        partial = STATE.with_name(STATE.name + ".tmp")
        partial.write_text(json.dumps(data))
        partial.replace(STATE)

    # ── чтение контейнеров ─────────────────────────────────────────────────
    def follow(self, container) -> None:
        cid = container.id
        with self.lock:
            thread = self.following.get(cid)
            if thread is not None and thread.is_alive():
                return
            thread = threading.Thread(target=self._read, args=(container,), name=container.name, daemon=True)
            self.following[cid] = thread
        thread.start()

    def _since(self, container) -> datetime:
        """С какого момента читать: где остановились в прошлый раз, иначе — с запуска контейнера,
        если он поднялся вместе со сборщиком (строки старта «api готов» иначе терялись бы).

        Контейнер, работающий давно (Qdrant с утра), читается с запуска сборщика: его
        старые строки легли бы в all.log задним числом, вперемешку с текущими (19.10)."""
        seen = self.last_seen.get(container.id)
        if seen:
            return _parse_ts(seen)
        started = _parse_ts(container.attrs["State"]["StartedAt"])
        return started if self.started - started < BACKFILL else self.started

    def _read(self, container) -> None:
        service = container.labels.get("com.docker.compose.service", container.name)
        previous: Record | None = None
        buffer = b""
        try:
            since = self._since(container)
            stream = container.logs(stream=True, follow=True, timestamps=True, since=int(since.timestamp()))
            for chunk in stream:
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    stamp, _, text = raw.decode("utf-8", "replace").partition(" ")
                    when = _parse_ts(stamp)
                    if when <= since:
                        continue  # уже записано (Docker отдаёт с точностью до секунды)
                    record = parse(service, text, previous)
                    if record is None:
                        continue
                    previous = record
                    self.writer.write(when.astimezone(), service, record)
                    with self.lock:
                        self.last_seen[container.id] = stamp
        except NotFound:
            pass
        except Exception:  # noqa: BLE001 — сборщик не должен падать из-за одного контейнера
            logger.exception("Чтение логов %s прервано", service)

    # ── события Docker: старт, остановка, здоровье ─────────────────────────
    def watch_events(self) -> None:
        filters = {"type": "container", "label": f"com.docker.compose.project={PROJECT}"}
        for event in self.client.events(decode=True, filters=filters, since=int(time.time())):
            if self.stop.is_set():
                return
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 — одно странное событие не должно останавливать сборщик
                logger.exception("Событие Docker не разобрано: %s", str(event)[:300])

    def _on_event(self, event: dict) -> None:
        action = event.get("Action", "")
        attrs = event.get("Actor", {}).get("Attributes", {})
        service = attrs.get("com.docker.compose.service", attrs.get("name", "?"))
        when = datetime.fromtimestamp(event.get("timeNano", time.time_ns()) / 1e9).astimezone()
        record = None
        if action == "start":
            record = Record("INFO", "docker", f"Контейнер {service} запущен")
            try:
                # Docker 29 убрал из событий поле id — оно осталось только в Actor.ID
                cid = event.get("id") or event.get("Actor", {}).get("ID")
                self.follow(self.client.containers.get(cid))
            except NotFound:
                pass
        elif action == "die":
            code = int(attrs.get("exitCode", "0") or 0)
            how = _EXIT_CODES.get(code, f"код {code}")
            record = Record("WARN" if code in (0, 143) else "ERROR", "docker",
                            f"Контейнер {service} остановился: {how} (код {code})")
        elif action == "oom":
            record = Record("ERROR", "docker", f"Контейнеру {service} не хватило памяти (OOM)")
        elif action.startswith("health_status"):
            status = action.split(":", 1)[-1].strip()
            if status == "unhealthy":
                record = Record("ERROR", "docker", f"Контейнер {service}: проверка здоровья не проходит")
            elif status == "healthy":
                record = Record("INFO", "docker", f"Контейнер {service} готов (проверка здоровья пройдена)")
        if record is not None:
            self.writer.write(when, "docker", record)

    # ── основной цикл ───────────────────────────────────────────────────────
    def run(self) -> None:
        containers = self.client.containers.list(filters={"label": f"com.docker.compose.project={PROJECT}"})
        for container in containers:
            self.follow(container)
        logger.info("Сбор логов: %d контейнеров проекта %s → %s (храним %d дней)",
                    len(containers), PROJECT, ROOT, KEEP_DAYS)
        threading.Thread(target=self._housekeeping, name="housekeeping", daemon=True).start()
        while not self.stop.is_set():
            try:
                self.watch_events()
            except DockerException as exc:
                logger.warning("Поток событий Docker прерван (%s) — переподключаюсь", exc)
                time.sleep(5)

    def _housekeeping(self) -> None:
        while not self.stop.wait(5):
            self.writer.flush()
            try:
                self._save_state()
            except OSError as exc:
                logger.warning("Не удалось сохранить состояние сборщика: %s", exc)


def _parse_ts(stamp: str) -> datetime:
    """RFC 3339 с наносекундами от Docker → datetime (микросекунды)."""
    match = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", stamp.strip())
    if not match:
        return datetime.now(timezone.utc)
    head, fraction, tz = match.groups()
    tz = "+00:00" if tz in (None, "Z") else tz
    return datetime.fromisoformat(f"{head}.{(fraction or '0')[:6].ljust(6, '0')}{tz}")


def main() -> None:
    wine_logging.setup("logs")
    collector = Collector()
    signal.signal(signal.SIGTERM, lambda *_: (collector.writer.flush(), collector._save_state(), os._exit(0)))
    collector.run()


if __name__ == "__main__":
    main()
