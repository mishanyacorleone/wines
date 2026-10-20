"""Единый формат логов Python-сервисов «Своё Вино».

Каждый сервис пишет в stdout строки одного вида:

    14:38:48.123 INFO  scan       [7f3a91c2] Найдено: «Инкерман Шато Блан» …

время · уровень · тема (последняя часть имени логгера) · номер запроса · событие.
Сборщик логов (services/log-collector) читает stdout контейнеров и раскладывает
строки по файлам logs/<дата>/all.log и logs/<дата>/services/<сервис>.log.

Принцип — в логе события, а не HTTP-трафик: «скан: найдено такое-то вино за
1,4 с», «пользователь вошёл», «каталог: +3 вина». Каждый запрос не пишется:
строка о запросе появляется, только если он упал (5xx), отклонён (4xx) или
медленный; WINE_LOG_REQUESTS=1 — писать все. Номер запроса задаёт шлюз
(X-Request-ID) и передаётся дальше по цепочке сервисов — по нему в all.log
видно, как один скан прошёл через шлюз, api, embedder и vLLM.
"""

from __future__ import annotations

import contextvars
import logging
import os
import sys
import time
import uuid
from typing import Any

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

# Библиотеки, которые на INFO пишут каждый HTTP-запрос или служебные мелочи
_QUIET = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "uvicorn": logging.WARNING,
    "uvicorn.error": logging.WARNING,
    "uvicorn.access": logging.CRITICAL,
    "multipart": logging.WARNING,
    "python_multipart": logging.WARNING,
    "urllib3": logging.WARNING,
    "PIL": logging.INFO,
    "transformers": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
}

_LEVELS = {"WARNING": "WARN", "CRITICAL": "FATAL"}


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        clock = time.strftime("%H:%M:%S", time.localtime(record.created))
        level = _LEVELS.get(record.levelname, record.levelname)
        topic = record.name.rsplit(".", 1)[-1][:10]
        request_id = getattr(record, "request_id", None) or _request_id.get()
        prefix = f"[{request_id}] " if request_id else ""
        line = f"{clock}.{int(record.msecs):03d} {level:<5} {topic:<10} {prefix}{record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup(service: str, *, level: str | None = None) -> None:
    """Настраивает корневой логгер. Вызывать до запуска uvicorn (log_config=None)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("WINE_LOG_LEVEL", "INFO").upper())
    for name, quiet in _QUIET.items():
        logging.getLogger(name).setLevel(quiet)
    logging.getLogger("service").info("Запуск сервиса %s", service)


def request_id() -> str | None:
    return _request_id.get()


# Отметка «о запросе уже написана своя строка» (скан с итогом и временем). Изменяемый
# словарь, а не значение: обработчик работает в копии контекста, и новое значение
# ContextVar до middleware не дошло бы
_request_note: contextvars.ContextVar[dict | None] = contextvars.ContextVar("request_note", default=None)


def mark_logged() -> None:
    """Запрос описан своей строкой — middleware не дублирует её строкой «→ 503 за …»."""
    note = _request_note.get()
    if note is not None:
        note["logged"] = True


def trace_headers() -> dict[str, str]:
    """Заголовок с номером текущего запроса — для вызовов других сервисов."""
    current = _request_id.get()
    return {"X-Request-ID": current} if current else {}


def uvicorn_options() -> dict[str, Any]:
    """Параметры uvicorn.run: логи — наши, свой журнал доступа uvicorn не нужен."""
    return {"log_config": None, "access_log": False}


class RequestLogMiddleware:
    """ASGI-middleware: номер запроса и строка о запросе, если с ним что-то не так.

    Пишет: 5xx — ERROR, 4xx — WARN (401 — DEBUG: аноним на /v1/me — норма),
    дольше slow_ms — WARN, остальное — DEBUG (INFO при WINE_LOG_REQUESTS=1).
    Если обработчик сам описал запрос (mark_logged) — только DEBUG: одна проблема — одна строка.
    """

    def __init__(self, app, *, quiet_paths: tuple[str, ...] = ("/health",), slow_ms: float = 3000) -> None:
        self.app = app
        self.quiet_paths = quiet_paths
        self.slow_ms = slow_ms
        self.log_all = os.environ.get("WINE_LOG_REQUESTS", "0") == "1"
        self.logger = logging.getLogger("http")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rid = (headers.get("x-request-id") or uuid.uuid4().hex)[:8]
        token = _request_id.set(rid)
        note = {"logged": False}
        note_token = _request_note.set(note)
        status = {"code": 500}
        started = time.perf_counter()

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self.logger.exception("Необработанная ошибка: %s %s", scope["method"], scope["path"])
            raise
        finally:
            self._log(scope, headers, status["code"], (time.perf_counter() - started) * 1000, note["logged"])
            _request_note.reset(note_token)
            _request_id.reset(token)

    def _log(self, scope, headers: dict[str, str], code: int, elapsed_ms: float, logged: bool) -> None:
        path = scope["path"]
        if path in self.quiet_paths and code < 400:
            return
        if logged:
            level = logging.DEBUG
        elif code >= 500:
            level = logging.ERROR
        elif code == 401:
            level = logging.DEBUG
        elif code >= 400 or elapsed_ms > self.slow_ms:
            level = logging.WARNING
        else:
            level = logging.INFO if self.log_all else logging.DEBUG
        if not self.logger.isEnabledFor(level):
            return
        client = headers.get("x-real-ip") or (scope.get("client") or ("?",))[0]
        note = " — медленно" if elapsed_ms > self.slow_ms and code < 400 else ""
        self.logger.log(level, "%s %s → %d за %s (клиент %s)%s",
                        scope["method"], path, code, format_ms(elapsed_ms), client, note)


def format_ms(ms: float) -> str:
    """1420 → «1,42 с», 35 → «35 мс»: в логе читается сразу, без пересчёта."""
    if ms >= 1000:
        return f"{ms / 1000:.2f} с".replace(".", ",")
    return f"{ms:.0f} мс"
