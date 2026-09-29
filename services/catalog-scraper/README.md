# catalog-scraper

Сервис сбора эталонного каталога с платформы «Своё Вино» (`vino-svoe.ru`).

На выходе — то, что нужно для построения индекса: **`slug` + стоковое фото бутылки в PNG** (плюс метаданные карточки, которые пригодятся для выдачи и «цифрового сомелье»).

## Как это работает

1. `wines-sitemap.xml` — официальный список URL всех вин каталога (~2100).
2. Каждая страница — Nuxt SSR; данные карточки лежат в `<script id="__NUXT_DATA__">` в devalue-формате (плоский список ячеек, ссылки по индексу). Парсим его, а не вёрстку: на странице десятки `<img>` (иконки региона и сорта, блюда, похожие вина), и по разметке фото самой бутылки однозначно не выделить.
3. Фото забираем через ресайз-эндпоинт `api.vino-svoe.ru`, запрашивая размер больше оригинала — API не апскейлит, поэтому приходит нативное разрешение. WebP конвертируется в PNG (с сохранением альфа-канала).

Обход вежливый: ограниченный параллелизм, задержка между запросами, ретраи с backoff, честный User-Agent. `robots.txt` разрешает `/wines/*`.

## Запуск

```bash
pip install -r requirements.txt

# пробный прогон на 10 винах
PYTHONPATH=src python -m catalog_scraper --limit 10

# полный каталог (~2100 страниц, порядка 10-15 минут)
PYTHONPATH=src python -m catalog_scraper
```

Прогон **возобновляемый**: уже собранные вина (строка в каталоге + существующий PNG) пропускаются. Строки без PNG перед обходом удаляются из `catalog.jsonl` — эти вина собираются заново и дописываются в конец, без повторов (на чистом клоне `catalog.jsonl` есть в git, а фото нет). `--no-resume` переписывает всё с нуля.

## Результат

```
data/catalog/
├── images/<slug>.png      # фото бутылки, нативное разрешение
├── catalog.jsonl          # по строке на вино
├── failures.jsonl         # что не собралось и почему (для повторного прогона)
└── wines-sitemap.xml      # кэш sitemap
```

Строка `catalog.jsonl`:

```json
{"slug": "simbioz", "source_url": "https://vino-svoe.ru/wines/simbioz",
 "title": "Симбиоз", "category": "Красное сухое", "color": "Темно-рубиновый",
 "region": "Крым", "manufacturer": "WINEPARK", "manufacturer_slug": "winepark",
 "grapes": ["Каберне Совиньон"], "dishes": ["BBQ", "Запеченные овощи"],
 "alcohol": 13.5, "temperature": "15-18", "public_rating": 4.22,
 "description": "...", "image_url": "/uploads/simbioz_2021_975b321bc5.webp",
 "image_path": "images/simbioz.png", "image_width": 1500, "image_height": 1500}
```

## Настройки

Переменные окружения с префиксом `SCRAPER_` (см. `src/catalog_scraper/config.py`):

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `SCRAPER_OUTPUT_DIR` | `<repo>/data/catalog` | куда складывать результат |
| `SCRAPER_CONCURRENCY` | `4` | параллельных запросов |
| `SCRAPER_DELAY_SEC` | `0.2` | пауза после каждого запроса |
| `SCRAPER_IMAGE_REQUEST_SIZE` | `3840` | запрашиваемый размер фото (потолок API) |
| `SCRAPER_MAX_RETRIES` | `3` | попыток на запрос |
| `SCRAPER_TIMEOUT_SEC` | `30` | таймаут запроса |

## Структура

```
src/catalog_scraper/
├── config.py        # настройки из окружения
├── models.py        # Wine, Failure
├── http_client.py   # httpx-клиент, ретраи, backoff
├── sitemap.py       # sitemap -> список URL
├── nuxt.py          # разбор __NUXT_DATA__ -> Wine
├── images.py        # скачивание + конвертация в PNG
├── storage.py       # JSONL, возобновление прогона
├── pipeline.py      # оркестрация
└── cli.py           # точка входа
```
