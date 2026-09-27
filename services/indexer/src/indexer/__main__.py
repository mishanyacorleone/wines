import sys
from pathlib import Path

from dotenv import load_dotenv

# .env — до импорта настроек: индексатор и API обязаны видеть одну и ту же
# модель, иначе поиск молча вернёт мусор
load_dotenv(Path(__file__).resolve().parents[4] / ".env")

from .cli import main  # noqa: E402

sys.exit(main())
