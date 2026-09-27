"""CLI сервиса сбора каталога."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import sys

from .config import settings
from .pipeline import scrape_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catalog-scraper",
        description="Собирает каталог вин с vino-svoe.ru: slug + PNG-фото + метаданные.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="обработать только первые N вин (для пробного прогона)",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="не пропускать уже собранные вина, переписать каталог с нуля",
    )
    parser.add_argument(
        "--refresh-sitemap", action="store_true",
        help="перекачать sitemap, игнорируя локальный кэш",
    )
    parser.add_argument(
        "--concurrency", type=int, default=None,
        help=f"параллельных запросов (по умолчанию {settings.concurrency})",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="подробные логи")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    active = settings
    if args.concurrency is not None:
        active = dataclasses.replace(settings, concurrency=args.concurrency)

    stats = asyncio.run(
        scrape_catalog(
            active,
            limit=args.limit,
            resume=not args.no_resume,
            refresh_sitemap=args.refresh_sitemap,
        )
    )

    print(
        f"\nВсего в sitemap: {stats.total}\n"
        f"Собрано за этот прогон: {stats.scraped}\n"
        f"Пропущено (уже было): {stats.skipped}\n"
        f"Ошибок: {stats.failed}\n"
        f"Каталог: {active.catalog_path}\n"
        f"Фото: {active.images_dir}"
    )
    if stats.failed:
        print(f"Список ошибок: {active.failures_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
