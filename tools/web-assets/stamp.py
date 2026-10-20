"""Версии файлов страницы /app/ по их содержимому: `app.js?v=3f9c1a2b7e`.

Web отдаёт файлы с `?v=` с кэшем на год (`immutable`, services/web/nginx.conf): повторное
открытие страницы не переспрашивает сервер о каждом скрипте. Цена — версию обязательно
менять при каждой правке файла, иначе браузеры год будут держать старый. Поэтому версия —
хэш содержимого, и её проставляет этот скрипт, а не человек:

    python tools/web-assets/stamp.py           # после правки вёрстки
    python tools/web-assets/stamp.py --check   # смоук: версии совпадают с файлами (код выхода 1 — нет)

Обрабатываются `<путь>?v=<что угодно>` в app.css (шрифты) и index.html (стили, скрипты,
список подгружаемых по требованию файлов). Сначала CSS: его хэш зависит от версий шрифтов.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "services" / "web" / "app"
FILES = ("app.css", "index.html")  # порядок важен: index.html ссылается на app.css
REF = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.(?:js|css|json|woff2|svg|png|ico|webp))\?v=(?P<v>[A-Za-z0-9]*)")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def stamp(text: str, base: Path, problems: list[str], name: str) -> str:
    def fix(m: re.Match) -> str:
        target = base / m["path"]
        if not target.is_file():
            problems.append(f"{name}: нет файла {m['path']}")
            return m[0]
        v = digest(target)
        if v != m["v"]:
            problems.append(f"{name}: {m['path']} — версия {m['v'] or '(пусто)'}, файл {v}")
        return f"{m['path']}?v={v}"

    return REF.sub(fix, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="только проверить, ничего не менять")
    args = parser.parse_args()

    problems: list[str] = []
    for name in FILES:
        path = APP / name
        text = path.read_text(encoding="utf-8")
        new = stamp(text, path.parent, problems, name)
        if new != text and not args.check:
            path.write_text(new, encoding="utf-8")
    if args.check:
        for p in problems:
            print(p)
        return 1 if problems else 0
    print(f"Обновлено версий: {sum('версия' in p for p in problems)}")
    missing = [p for p in problems if "нет файла" in p]
    for p in missing:
        print(p)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
