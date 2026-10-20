"""Ретранслятор TCP: адрес, видимый из контейнеров (docker0), → прокси хоста на 127.0.0.1.

Нужен, только если выход в интернет на машине — через прокси, который слушает
лишь 127.0.0.1 (как на машине разработки). Контейнер catalog ходит на сайт через
host.docker.internal:<порт>, а egress (сеть хоста) передаёт байты прокси как есть:
сам он ничего не разбирает, HTTPS остаётся сквозным (CONNECT делает прокси).
Без зависимостей — запускается в чистом python:3.12-slim.

    WINE_EGRESS_LISTEN=172.17.0.1:7115 WINE_EGRESS_TARGET=127.0.0.1:7114 python relay.py
"""

import asyncio
import os
from datetime import datetime


def _address(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host, int(port)


LISTEN = _address(os.environ.get("WINE_EGRESS_LISTEN", "172.17.0.1:7115"))
TARGET = _address(os.environ.get("WINE_EGRESS_TARGET", "127.0.0.1:3128"))


def log(level: str, text: str) -> None:
    """Формат wine_logging («ЧЧ:ММ:СС.мс УРОВЕНЬ тема текст») — сборщик логов разбирает его как свой."""
    print(f"{datetime.now():%H:%M:%S.%f}"[:12] + f" {level:<5} egress     {text}", flush=True)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(*TARGET)
    except OSError as exc:
        log("WARN", f"прокси {TARGET[0]}:{TARGET[1]} недоступен: {exc}")
        client_writer.close()
        return
    await asyncio.gather(pipe(client_reader, target_writer), pipe(target_reader, client_writer))


async def main() -> None:
    server = await asyncio.start_server(handle, *LISTEN)
    log("INFO", f"{LISTEN[0]}:{LISTEN[1]} → прокси {TARGET[0]}:{TARGET[1]}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
