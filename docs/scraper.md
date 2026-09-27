# Парсер vino-svoe.ru

Закрывает пробелы там, где матчинг CSV↔архив не сработал (см. `catalog-matching.md`), и даёт официальный, актуальный источник `(slug, фото, метаданные)` напрямую с сайта организатора.

## Правовой/этический контекст

- Сайт — официальная платформа РСХБ + ВинЛаб, не мелкий частный проект.
- `robots.txt` разрешает обход `/wines/*` для всех User-Agent (`Yandex`, `Googlebot`, `*`). Запрещён только `/api/*` — это относится к отдельному поддомену `api.vino-svoe.ru`, к самим страницам `/wines/...` не применяется.
- Используется `wines-sitemap.xml` — официальный, предназначенный для обхода список URL, а не brute-force перебор.
- Троттлинг между запросами (0.5 сек по умолчанию) — вежливая практика, не агрессивный параллельный обход.
- Стоит перепроверить формальные правила хакатона на явный запрет парсинга источника данных, если такой пункт есть в регламенте — на момент написания этого документа предполагается, что это разрешено ("хакатон не запрещает").

## Как устроены данные на странице

Сайт — Nuxt.js SSR-приложение. На каждой странице вина есть блок:
```html
<script id="__NUXT_DATA__" type="application/json">...</script>
```

Это не готовый JSON для `json.loads()` "в лоб" — Nuxt использует devalue-формат: **плоский список "ячеек"**, где объекты ссылаются друг на друга по индексу в этом списке, а не хранят значения напрямую.

Пример из реальной страницы (`/wines/simbioz`):
```
data[5] = {'alcohol': 6, 'category': 7, 'color': 10, 'description': 11, ..., 'slug': 53, 'title': 55}
data[53] = "simbioz"   # это и есть slug — просто по индексу 53
data[41] = {'altText': 42, 'url': 43}
data[43] = "/uploads/simbioz_2021_975b321bc5.webp"   # это image.url
```

Это подтверждено сверкой: `image.url` из `__NUXT_DATA__` буквально совпадает с `og:image` на странице и с превьюшкой на странице листинга — три независимых источника указывают на один и тот же файл.

**Почему не парсить `og:image` или `<img>` теги напрямую:** на странице вина есть много посторонних картинок (иконки региона/сорта, фото винограда, блюда, "похожие вина" — превью совсем других товаров). Без `__NUXT_DATA__` пришлось бы гадать, какой из десятка `<img>` — это именно фото нужной бутылки. `__NUXT_DATA__` даёт это однозначно и структурированно.

## Полный рабочий код

Файл: `scrape_vino_svoe.py` (протестирован локально на сохранённом HTML — вся логика извлечения работает корректно; реальный сетевой прогон на все 2067 URL — на стороне исполнителя, доступ к сайту из окружения ассистента ограничен).

```python
"""
Парсер каталога vino-svoe.ru: sitemap -> список вин -> для каждого забираем
slug, метаданные и фото бутылки через встроенный __NUXT_DATA__ (структурированные
данные Nuxt.js SSR, а не хрупкий парсинг вёрстки).

robots.txt разрешает обход /wines/* всем ботам (запрещён только /api/*, который
относится к другому поддомену api.vino-svoe.ru и не про эти страницы).
Троттлинг между запросами — чтобы не долбить сервер параллельно.
"""

import json
import os
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# ---- Настройки ----
SITEMAP_PATH = "wines-sitemap.xml"       # положи файл рядом или укажи полный путь
OUTPUT_DIR = "vino_svoe_catalog"          # сюда лягут картинки
OUTPUT_CSV = "vino_svoe_catalog.csv"      # итоговая таблица slug -> метаданные -> путь к фото
REQUEST_DELAY_SEC = 0.5                   # пауза между запросами страниц — вежливый троттлинг
IMG_SIZE = 1160                           # какое разрешение фото просить у API (1160x1160 — то же, что на сайте в карточке)
TIMEOUT_SEC = 10

HEADERS = {
    # честный, узнаваемый User-Agent — не маскируемся под браузер без необходимости
    "User-Agent": "Mozilla/5.0 (compatible; wine-hackathon-dataset-builder/1.0)"
}

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_wine_urls(sitemap_path: str) -> list[str]:
    tree = ET.parse(sitemap_path)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [loc.text for loc in tree.getroot().findall(".//sm:loc", ns)]


def extract_wine_data(html: str) -> dict | None:
    """Достаёт структурированные данные вина из __NUXT_DATA__.

    Формат — devalue-подобный плоский список: объекты ссылаются друг на друга
    по индексу в этом списке, а не хранят значения напрямую.
    """
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NUXT_DATA__")
    if script is None or not script.string:
        return None

    data = json.loads(script.string)

    root = None
    for item in data:
        if isinstance(item, dict) and any(
            isinstance(k, str) and k.startswith("wine-") for k in item.keys()
        ):
            root = item
            break
    if root is None:
        return None

    wine_key = next(k for k in root.keys() if k.startswith("wine-"))
    wine_container = data[root[wine_key]]
    if "wine" not in wine_container:
        return None
    wine = data[wine_container["wine"]]

    def resolve(idx):
        return data[idx]

    def resolve_named(idx):
        """Для полей вида {'name': N, ...} или просто строки напрямую."""
        val = resolve(idx)
        if isinstance(val, dict) and "name" in val:
            return resolve(val["name"])
        return val

    try:
        image_obj = resolve(wine["image"])
        image_url = resolve(image_obj["url"])
    except (KeyError, TypeError):
        image_url = None

    return {
        "slug": resolve(wine["slug"]),
        "title": resolve(wine["title"]),
        "color": resolve(wine["color"]) if isinstance(wine.get("color"), int) else wine.get("color"),
        "description": resolve(wine["description"]) if isinstance(wine.get("description"), int) else wine.get("description"),
        "region": resolve_named(wine["region"]) if "region" in wine else None,
        "manufacturer": resolve_named(wine["manufacturer"]) if "manufacturer" in wine else None,
        "category": resolve_named(wine["category"]) if "category" in wine else None,
        "image_url": image_url,
    }


def build_image_url(relative_url: str, size: int = IMG_SIZE) -> str:
    """relative_url выглядит как '/uploads/simbioz_2021_975b321bc5.webp'."""
    filename = relative_url.lstrip("/").removeprefix("uploads/")
    return f"https://api.vino-svoe.ru/v1/img/str-api/{size}/{size}/resize/uploads/{filename}"


def download_image(url: str, dest_path: str) -> bool:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SEC)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(resp.content)
        return True
    except requests.RequestException as e:
        print(f"  Не удалось скачать {url}: {e}")
        return False


def scrape_all_wines(sitemap_path: str = SITEMAP_PATH, limit: int | None = None) -> pd.DataFrame:
    urls = load_wine_urls(sitemap_path)
    if limit:
        urls = urls[:limit]

    rows = []
    failed = []

    for url in tqdm(urls, desc="Парсим каталог vino-svoe.ru"):
        slug_from_url = url.rstrip("/").split("/")[-1]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SEC)
            resp.raise_for_status()
        except requests.RequestException as e:
            failed.append((slug_from_url, f"HTTP ошибка: {e}"))
            time.sleep(REQUEST_DELAY_SEC)
            continue

        wine_data = extract_wine_data(resp.text)
        if wine_data is None or not wine_data.get("image_url"):
            failed.append((slug_from_url, "не нашли __NUXT_DATA__ или image_url"))
            time.sleep(REQUEST_DELAY_SEC)
            continue

        slug = wine_data["slug"]
        img_url = build_image_url(wine_data["image_url"])
        img_filename = f"{slug}.webp"
        img_path = os.path.join(OUTPUT_DIR, img_filename)

        ok = download_image(img_url, img_path)
        wine_data["image_path"] = img_path if ok else None
        wine_data["source_url"] = url
        rows.append(wine_data)

        time.sleep(REQUEST_DELAY_SEC)

    df = pd.DataFrame(rows)

    if failed:
        print(f"\nНе удалось обработать {len(failed)} страниц из {len(urls)}:")
        for slug, reason in failed[:20]:
            print(f"  {slug}: {reason}")
        if len(failed) > 20:
            print(f"  ... и ещё {len(failed) - 20}")

    return df


if __name__ == "__main__":
    # Сначала прогони на небольшом limit (например 10), чтобы убедиться, что всё
    # работает на твоей сети/IP, прежде чем запускать на весь каталог (2067 страниц,
    # с задержкой 0.5с это займёт ~17-20 минут).
    df = scrape_all_wines(limit=10)
    print(f"\nСобрано вин: {len(df)}")
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Сохранено в {OUTPUT_CSV}")
    df.head(10)
```

## Как запускать

1. Скачать `wines-sitemap.xml` (либо через `https://vino-svoe.ru/wines-sitemap.xml`, либо уже есть сохранённая копия — 2067 URL).
2. Установить зависимости: `pip install pandas requests beautifulsoup4 tqdm`.
3. Прогнать сначала с `limit=10`, чтобы глазами проверить результат и убедиться, что не забанили по IP/User-Agent.
4. Убрать `limit`, прогнать на весь каталог — при задержке 0.5 сек это займёт ~17-20 минут на 2067 страниц.

## Известные ограничения

- Сетевой доступ к `vino-svoe.ru` не тестировался напрямую из среды ассистента (egress ограничен списком доменов) — весь прогон на реальной сети выполняется человеком, не автоматически.
- `wine_container_idx` в `__NUXT_DATA__` ищется по паттерну ключа `wine-<slug>` в корневом словаре, а не по фиксированному индексу — индексы плавают между страницами, поэтому код ищет структуру, а не magic number. Это должно быть устойчиво к большинству страниц, но если у части URL структура вдруг иная (например, для "похожих" типов товаров) — увидишь это в списке `failed` при прогоне, разбирать по мере появления.
- Расхождение sitemap (2067) vs CSV (2103 уникальных slug) — 34 URL есть только в sitemap, 70 slug есть только в CSV. Ожидаемо для живого каталога, но если после прогона парсера итоговое покрытие окажется заметно ниже ожидаемого — сверить с этим расхождением.