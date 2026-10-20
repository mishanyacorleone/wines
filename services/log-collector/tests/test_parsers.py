"""Разбор строк сервисов — на настоящих строках из логов стенда.

Запуск из корня репозитория:
    PYTHONPATH=services/log-collector/src .venv/bin/python -m pytest services/log-collector/tests -q
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from log_collector.parsers import Record, parse
from log_collector.writer import DailyWriter


def test_python_service_line_and_traceback():
    first = parse("api", "14:38:48.123 ERROR http       [7f3a91c2] Необработанная ошибка: POST /v1/scan", None)
    assert first == Record("ERROR", "http", "[7f3a91c2] Необработанная ошибка: POST /v1/scan", True)
    tail = parse("api", 'Traceback (most recent call last):', first)
    assert tail.level == "ERROR" and tail.to_all and tail.text.startswith("  Traceback")
    debug = parse("embedder", "14:38:48.123 DEBUG embedder   Вектор 512×512 px", None)
    assert debug.to_all is False


def test_gateway_access_to_all_only_when_service_not_reached():
    ok = parse("gateway", "GET /v1/wines/x → 200 за 0.012s (сервис 0.010s, клиент 1.2.3.4) [ab12cd34]", None)
    assert ok.level == "INFO" and not ok.to_all
    assert ok.text == "GET /v1/wines/x → 200 за 12 мс (сервис 10 мс, клиент 1.2.3.4) [ab12cd34]"
    slow = parse("gateway", "POST /v1/scan → 200 за 3.420s (сервис 3.400s, клиент 1.2.3.4) [ab12cd34]", None)
    # медленный и ошибочный ответ сервиса описывает сам сервис — дубля в all.log нет
    assert not slow.to_all and "3,42 с" in slow.text
    failed = parse("gateway", "POST /v1/scan → 503 за 0.010s (сервис 0.009s, клиент 1.2.3.4) [ab12cd34]", None)
    assert failed.level == "ERROR" and not failed.to_all
    down = parse("gateway", "POST /v1/scan → 503 за 0.002s (сервис -s, клиент 1.2.3.4) [ab12cd34]", None)
    assert down.level == "ERROR" and down.to_all and "не дошёл до сервиса" in down.text


def test_nginx_error_is_humanized():
    line = ('2026/10/19 09:38:47 [error] 31#31: *5183 connect() failed (111: Connection refused) while connecting '
            'to upstream, client: 172.20.0.1, server: _, request: "POST /v1/scan HTTP/1.1", '
            'upstream: "http://172.20.0.5:8080/v1/scan", host: "127.0.0.1:8080"')
    record = parse("gateway", line, None)
    assert record.level == "ERROR"
    assert record.text == ("Сервис за шлюзом не отвечает: POST /v1/scan (http://172.20.0.5:8080/v1/scan) — "
                           "111: Connection refused")
    buffered = parse("gateway", '2026/10/19 09:38:47 [warn] 31#31: *5183 a client request body is buffered to a '
                                'temporary file /var/cache/nginx/client_temp/0000001035', None)
    assert not buffered.to_all
    missing = parse("gateway", '2026/10/19 09:59:54 [error] 28#28: *20 api could not be resolved (2: Server failure), '
                               'client: 172.24.0.1, server: _, request: "POST /v1/scan HTTP/1.1", host: "x"', None)
    assert missing.text == "Сервис api не найден в сети Docker — контейнер не запущен: POST /v1/scan"


def test_qdrant_access_log_is_dropped():
    assert parse("qdrant", '2026-10-19T05:12:00.674014Z  INFO actix_web::middleware::logger: 172.20.0.8 '
                           '"GET /collections/wines HTTP/1.1" 200 466 "-" "python-httpx/0.28.1" 0.599110', None) is None
    info = parse("qdrant", "2026-10-19T04:16:53.852037Z  INFO collection::shards::local_shard: "
                           "Recovered collection wines: 1/1 (100%)", None)
    assert info == Record("INFO", "qdrant", "Recovered collection wines: 1/1 (100%)", True)


def test_vllm_access_dropped_stats_only_in_service_log():
    assert parse("vllm", 'INFO:     172.20.0.8:46148 - "POST /v1/chat/completions HTTP/1.1" 200 OK', None) is None
    stats = parse("vllm", "(APIServer pid=1) INFO 10-19 04:52:07 [loggers.py:123] Engine 000: Avg prompt "
                          "throughput: 1200.0 tokens/s", None)
    assert stats.topic == "server" and not stats.to_all
    warn = parse("vllm", "(EngineCore pid=171) WARNING 10-19 04:47:42 [x.py:1] Not enough KV cache", None)
    assert warn.level == "WARN" and warn.to_all


def test_postgres_checkpoints_stay_out_of_all_log():
    checkpoint = parse("postgres", "2026-10-19 04:56:50.465 UTC [33] LOG:  checkpoint starting: time", None)
    assert checkpoint.level == "INFO" and not checkpoint.to_all
    fatal = parse("postgres", "2026-10-19 04:48:33.827 UTC [2672] FATAL:  connection to client lost", None)
    assert fatal.level == "ERROR" and fatal.to_all


def test_writer_splits_by_day_and_service(tmp_path: Path):
    writer = DailyWriter(tmp_path, keep_days=30)
    writer.write(datetime(2026, 10, 19, 23, 59, 59), "api", Record("INFO", "scan", "Скан: найдено", True))
    writer.write(datetime(2026, 10, 19, 23, 59, 59), "qdrant", Record("INFO", "qdrant", "шум", False))
    writer.write(datetime(2026, 10, 20, 0, 0, 1), "api", Record("WARN", "scan", "Скан: VLM недоступен", True))
    writer.flush()
    day1 = (tmp_path / "2026-10-19" / "all.log").read_text(encoding="utf-8")
    assert day1 == "2026-10-19 23:59:59.000 INFO  api        scan       Скан: найдено\n"
    assert (tmp_path / "2026-10-19" / "services" / "qdrant.log").read_text(encoding="utf-8").endswith("шум\n")
    assert "VLM недоступен" in (tmp_path / "2026-10-20" / "services" / "api.log").read_text(encoding="utf-8")
