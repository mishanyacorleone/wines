"""CLI индексатора."""

from __future__ import annotations

import argparse
import logging
import sys

from .build import build_index, refresh_payloads
from .config import index_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="indexer", description="Строит векторный индекс каталога вин в Qdrant."
    )
    parser.add_argument("--recreate", action="store_true", help="пересоздать коллекцию с нуля")
    parser.add_argument("--limit", type=int, default=None, help="только первые N вин (проба)")
    parser.add_argument(
        "--payload-only",
        action="store_true",
        help="обновить только метаданные карточек (блюда, подача, описание) без пересчёта векторов",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.payload_only:
        stats = refresh_payloads(index_settings)
        print(
            f"\nPayload обновлён: {stats.indexed}\n"
            f"Нет в коллекции (пропущено): {stats.skipped}\n"
            f"Время: {stats.seconds:.1f} с\n"
            f"Коллекция: {index_settings.collection} @ {index_settings.qdrant_url}"
        )
        return 0

    stats = build_index(index_settings, recreate=args.recreate, limit=args.limit)

    rate = stats.indexed / stats.seconds if stats.seconds else 0
    print(
        f"\nПроиндексировано: {stats.indexed}\n"
        f"Пропущено: {stats.skipped}\n"
        f"Размерность вектора: {stats.dimension}\n"
        f"Время: {stats.seconds:.1f} с ({rate:.1f} вин/с)\n"
        f"Пик VRAM: {stats.vram_peak_gb:.2f} ГБ\n"
        f"Коллекция: {index_settings.collection} @ {index_settings.qdrant_url}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
