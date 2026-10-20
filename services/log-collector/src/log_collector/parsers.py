"""Разбор строк разных сервисов в одну форму: уровень, тема, текст, нужна ли строка в all.log.

Каждый сервис пишет по-своему: наши Python-сервисы — «12:03:04.123 INFO  scan  …»,
nginx — свой журнал доступа и ошибок, Qdrant — формат Rust tracing, vLLM —
«(APIServer pid=1) INFO 10-19 …», Postgres — «… UTC [33] LOG: …». Здесь всё
приводится к одному виду, а шум (журнал доступа Qdrant и vLLM, плановые
контрольные точки Postgres, полосы загрузки весов) не попадает в all.log —
а то, что ничего не говорит человеку, не пишется вовсе.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")


@dataclass(frozen=True)
class Record:
    level: str
    topic: str
    text: str
    # в общий all.log — только то, что стоит прочитать; в services/<сервис>.log — всё
    to_all: bool = True


# Python-сервисы проекта (wine_logging): время · уровень · тема · текст
_OURS = re.compile(r"^\d\d:\d\d:\d\d\.\d{3} (DEBUG|INFO|WARN|ERROR|FATAL)\s+(\S+)\s+(.*)$")

# nginx: журнал доступа шлюза (log_format wines в infra/gateway/nginx.conf)
_NGINX_ACCESS = re.compile(
    r"^(?P<method>[A-Z]+) (?P<path>\S+) → (?P<status>\d{3}) за (?P<total>[\d.]+)s "
    r"\(сервис (?P<upstream>[^,]*?)s?, клиент (?P<client>[^)]*)\) \[(?P<rid>[^\]]*)\]$"
)
_NGINX_ERROR = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d \[(\w+)\] \d+#\d+: (?:\*\d+ )?(.*)$")
_NGINX_LEVELS = {"debug": "DEBUG", "info": "INFO", "notice": "INFO", "warn": "WARN",
                 "error": "ERROR", "crit": "FATAL", "alert": "FATAL", "emerg": "FATAL"}

# Qdrant: 2026-10-19T04:16:53.852037Z  INFO collection::shards::local_shard: Recovered …
_QDRANT = re.compile(r"^\S+Z\s+(TRACE|DEBUG|INFO|WARN|ERROR)\s+([\w:]+): (.*)$")
_QDRANT_ACCESS = re.compile(r'"(\w+) (\S+) HTTP/[\d.]+" (\d{3})')

# vLLM: (APIServer pid=1) INFO 10-19 04:47:07 [api_utils.py:347] текст
_VLLM = re.compile(r"^(?:\((\w+) pid=\d+\) )?(DEBUG|INFO|WARNING|ERROR|CRITICAL) \d\d-\d\d [\d:]+ \[[^\]]+\] ?(.*)$")
_UVICORN_ACCESS = re.compile(r'^INFO:\s+\S+ - "(\w+) (\S+) HTTP/[\d.]+" (\d{3})')

# Postgres: 2026-10-19 04:56:50.465 UTC [33] LOG:  текст
_POSTGRES = re.compile(r"^\S+ \S+ \S+ \[\d+\] (LOG|ERROR|FATAL|WARNING|PANIC|DETAIL|HINT|STATEMENT):\s+(.*)$")
_POSTGRES_LEVELS = {"LOG": "INFO", "WARNING": "WARN", "ERROR": "ERROR", "FATAL": "ERROR", "PANIC": "FATAL",
                    "DETAIL": "INFO", "HINT": "INFO", "STATEMENT": "INFO"}

PYTHON_SERVICES = {"api", "users", "analytics", "catalog", "embedder", "logs", "scraper", "indexer", "egress"}


def _seconds(raw: str) -> str:
    """«1.420» → «1,42 с», «0.035» → «35 мс»; «-» (до сервиса не дошло) — как есть."""
    try:
        value = float(raw.split(",")[-1].strip())
    except ValueError:
        return raw
    return f"{value:.2f} с".replace(".", ",") if value >= 1 else f"{value * 1000:.0f} мс"


def parse(service: str, line: str, previous: Record | None) -> Record | None:
    """Строка контейнера → запись; None — строку не писать вовсе."""
    line = line.rstrip()
    if not line.strip():
        return None
    if service in PYTHON_SERVICES:
        return _python(line, previous)
    if service in ("gateway", "web"):
        return _nginx(line)
    if service == "qdrant":
        return _qdrant(line)
    if service == "vllm":
        return _vllm(line)
    if service == "postgres":
        return _postgres(line)
    return Record("INFO", "-", line)


def _python(line: str, previous: Record | None) -> Record:
    match = _OURS.match(line)
    if match:
        level, topic, text = match.groups()
        return Record(level, topic, text, to_all=level != "DEBUG")
    # Продолжение многострочной записи (traceback) — с тем же уровнем, что и начало
    if previous is not None:
        return Record(previous.level, previous.topic, "  " + line, previous.to_all)
    return Record("INFO", "-", line)


def _nginx(line: str) -> Record | None:
    match = _NGINX_ACCESS.match(line)
    if match:
        status = int(match["status"])
        level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
        upstream = match["upstream"].strip()
        service_time = "не дошёл до сервиса" if upstream in ("-", "") else f"сервис {_seconds(upstream)}"
        text = (f"{match['method']} {match['path']} → {status} за {_seconds(match['total'])} "
                f"({service_time}, клиент {match['client']}) [{match['rid']}]")
        # Дошедший до сервиса запрос сервис описывает сам (скан с итогом, ошибка, «медленно»),
        # поэтому в общий лог — только то, что сервиса не достигло: /internal/, неизвестный путь
        reached = upstream not in ("-", "")
        return Record(level, "http", text, to_all=not reached and status >= 400)
    match = _NGINX_ERROR.match(line)
    if match:
        level, text = _NGINX_LEVELS.get(match[1], "INFO"), match[2]
        if "buffered to a temporary file" in text:
            return Record("DEBUG", "nginx", text, to_all=False)
        return Record(level, "nginx", _nginx_error_text(text), to_all=level in ("WARN", "ERROR", "FATAL"))
    # docker-entrypoint.sh при старте контейнера: «Configuration complete; ready for start up»
    if "ready for start up" in line:
        return Record("INFO", "nginx", "nginx запущен и принимает запросы")
    return Record("DEBUG", "nginx", line, to_all=False)


def _nginx_error_text(text: str) -> str:
    """«connect() failed (111: Connection refused) while connecting to upstream, … request: "POST /v1/scan …",
    upstream: "http://172.20.0.5:8080/v1/scan"» → «сервис не отвечает: POST /v1/scan → …»."""
    request = re.search(r'request: "(\w+ \S+)', text)
    upstream = re.search(r'upstream: "([^"]+)"', text)
    reason = re.search(r"\((\d+: [^)]+)\)", text)
    if "upstream" in text and request:
        why = reason[1] if reason else text.split(",")[0]
        target = f" ({upstream[1]})" if upstream else ""
        return f"Сервис за шлюзом не отвечает: {request[1]}{target} — {why}"
    if "could not be resolved" in text:
        host = re.search(r"^(\S+) could not be resolved", text)
        where = f": {request[1]}" if request else ""
        return f"Сервис {host[1] if host else '?'} не найден в сети Docker — контейнер не запущен{where}"
    return text


def _qdrant(line: str) -> Record | None:
    match = _QDRANT.match(line)
    if not match:
        return Record("INFO", "qdrant", line, to_all=False)
    level, module, text = match.groups()
    level = {"TRACE": "DEBUG"}.get(level, level)
    if module.startswith("actix_web::middleware::logger"):
        access = _QDRANT_ACCESS.search(text)
        # Журнал доступа: каждый поиск — строка. Пишем только отказы
        if access and int(access[3]) < 400:
            return None
        return Record("WARN", "qdrant", f"Запрос к Qdrant отклонён: {access[1]} {access[2]} → {access[3]}"
                      if access else text)
    return Record(level, "qdrant", text, to_all=level != "DEBUG")


def _vllm(line: str) -> Record | None:
    access = _UVICORN_ACCESS.match(line)
    if access:
        if int(access[3]) < 400:
            return None
        return Record("WARN", "vllm", f"Запрос к vLLM отклонён: {access[1]} {access[2]} → {access[3]}")
    match = _VLLM.match(line)
    if not match:
        # полосы загрузки весов, логотип, предупреждения библиотек при старте
        return Record("INFO", "vllm", line, to_all=False)
    process, level, text = match.groups()
    level = {"WARNING": "WARN", "CRITICAL": "FATAL"}.get(level, level)
    topic = {"APIServer": "server", "EngineCore": "engine"}.get(process or "", "vllm")
    if not text.strip():
        return None
    # Пропускная способность раз в 10 с и подробности старта — только в services/vllm.log
    routine = (level == "INFO" and not any(key in text for key in _VLLM_IMPORTANT)) or any(
        key in text for key in _VLLM_KNOWN)
    return Record(level, topic, text, to_all=not routine)


# Строки старта vLLM, которые стоит видеть в общем логе
_VLLM_IMPORTANT = ("Starting vLLM", "Application startup complete", "Shutting down", "Available KV cache",
                   "Initializing a V1 LLM engine", "Model loading took", "Loading weights took")
# Известные предупреждения на каждом старте — ожидаемы, только в services/vllm.log:
# у RTX 3090 нет FP8-ядер (веса через Marlin — так и задумано); параметры выборки
# из generation_config нам не важны — мы не генерируем текст, а читаем logprobs
_VLLM_KNOWN = ("does not have native support for FP8", "Default vLLM sampling parameters have been overridden")


def _postgres(line: str) -> Record:
    match = _POSTGRES.match(line)
    if not match:
        return Record("INFO", "postgres", line, to_all=False)
    kind, text = match.groups()
    level = _POSTGRES_LEVELS[kind]
    # Плановые контрольные точки каждые 5 минут — в общий лог не нужны
    routine = kind == "LOG" and (text.startswith("checkpoint ") or "automatic" in text)
    return Record(level, "postgres", text, to_all=not routine)
