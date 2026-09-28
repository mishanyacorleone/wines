# Сканер российских вин «Своё Вино»

Поиск карточки вина по фотографии этикетки, снятой в реальных условиях —
у полки магазина, под углом, с бликами. Решение кейса РСХБ.Цифра, ТЗ —
[`data/10. РСХБ.Цифра.pdf`](data/10.%20РСХБ.Цифра.pdf).

Задача решается как retrieval, а не классификация: новое вино в каталоге —
один новый вектор в индексе, без переобучения.

```
фото → SigLIP 2 → вектор → Qdrant топ-20 → OCR-переранжирование → [VLM выбирает из топ-5] → slug
```

| Конфигурация | top-1 (n=53) | top-5 | p95 ответа | VRAM |
|---|---|---|---|---|
| только картинка | 77,4% | 100% | 0,4 с | ~1,2 ГБ |
| картинка + OCR (**по умолчанию**) | 88,7% | 100% | 0,8 с | ~3,2 ГБ |
| картинка + VLM, без OCR (`WINE_VLM_ENABLED=1`, `WINE_OCR_ENABLED=0`) | **96,2%** | 100% | 1,7 с | ~10 ГБ |

Точность — по ручной разметке 100 реальных фото (53 размечены уверенно, 33 —
вина нет в каталоге). SLA организатора — 3 с. Все прогоны —
[`data/reports/accuracy.xlsx`](data/reports/accuracy.xlsx).

Архитектура и границы слоёв → [`ARCHITECTURE.md`](ARCHITECTURE.md).
Контекст задачи, данные и набитые шишки → [`CLAUDE.md`](CLAUDE.md).
Замеры и разбор ошибок → [`docs/baseline-results.md`](docs/baseline-results.md).

## Структура

```
libs/wine-embeddings/       общий энкодер (SigLIP 2): модель, нормализация, лимит VRAM
services/catalog-scraper/   сбор каталога с vino-svoe.ru → data/catalog/
services/indexer/           каталог → векторы → Qdrant
services/search-api/        FastAPI: /v1/search (топ-k), /v1/eval/predict (контракт организатора)
tools/eval-runner/          прогон фото через API → HTML-отчёт, точность, Excel
infra/                      Qdrant, скрипты загрузки весов
docs/                       данные, матчинг, парсер, результаты, план, журнал изменений
data/                       ТЗ, датасет организатора, разметка, каталог (метаданные), отчёты
```

## Что не лежит в репозитории

Тяжёлые файлы исключены (`.gitignore`) и восстанавливаются так:

| Что | Размер | Как получить |
|---|---|---|
| `models/siglip2-so400m-patch16-512` | 4,3 ГБ | `./infra/fetch-model.sh google/siglip2-so400m-patch16-512` |
| `models/easyocr` | 94 МБ | `./infra/fetch-ocr.sh` |
| `models/Qwen3-VL-4B-Instruct` | 8,9 ГБ | `./infra/fetch-vlm.sh` — только если включаете VLM |
| `infra/bin/qdrant` | 86 МБ | бинарник Qdrant 1.19.1, см. ниже |
| `data/catalog/images/` | ~1 ГБ | парсер каталога (шаг 2 запуска), `catalog.jsonl` уже в репозитории |
| `data/Датасет (1)/Реальные фото.zip`, `data/real-photos/` | 94 МБ | архив организатора; распаковать в `data/real-photos/` |
| `data/qdrant/` | — | строится индексатором (шаг 3) |

## Сетап

Python 3.12, CUDA-GPU (проверено на RTX 3090; процессу отводится не более 14 ГБ VRAM).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r services/search-api/requirements.txt \
                      -r services/indexer/requirements.txt \
                      -r services/catalog-scraper/requirements.txt \
                      -r tools/eval-runner/requirements.txt

./infra/fetch-model.sh google/siglip2-so400m-patch16-512
./infra/fetch-ocr.sh
./infra/fetch-vlm.sh              # опционально, для WINE_VLM_ENABLED=1

# Qdrant без Docker
mkdir -p infra/bin && curl -L https://github.com/qdrant/qdrant/releases/download/v1.19.1/qdrant-x86_64-unknown-linux-gnu.tar.gz \
  | tar -xz -C infra/bin

cp .env.example .env              # и поправить под себя
```

Веса скачиваются в папку проекта, а не в `~/.cache`, через curl с докачкой:
штатный загрузчик HuggingFace на обрывах начинает файл заново.

`requirements.txt` сервиса поиска закрепляет `torchvision==0.23.0`: без этого
`pip install easyocr` тянет свежий torchvision, а тот поднимает torch до версии
с CUDA 13 и ломает стек.

## Запуск

```bash
# 1. Векторная база
./infra/run-qdrant.sh                      # либо: docker compose -f infra/docker-compose.yml up -d

# 2. Сбор каталога (~2100 вин, 35–40 минут)
PYTHONPATH=services/catalog-scraper/src .venv/bin/python -m catalog_scraper

# 3. Индексация (~3,5 минуты на 2097 фото)
PYTHONPATH=libs/wine-embeddings/src:services/indexer/src .venv/bin/python -m indexer --recreate

# 3a. Только метаданные карточек (блюда, подача, описание) — без GPU, секунды.
#     Нужен один раз для индекса, построенного до появления сомелье
PYTHONPATH=libs/wine-embeddings/src:services/indexer/src .venv/bin/python -m indexer --payload-only

# 4. Сервис (читает .env)
PYTHONPATH=libs/wine-embeddings/src:services/search-api/src .venv/bin/python -m search_api
```

Мобильный сканер — `http://<хост>:8080/app/` (корень `/` перенаправляет туда).
Кнопка «Сфотографировать этикетку»:

- **телефон** — системная камера через `<input capture>` (автофокус, вспышка);
  работает и по обычному HTTP в локальной сети;
- **ноутбук** — `<input capture>` браузеры игнорируют и открывают выбор файла,
  поэтому камера открывается прямо в странице (`getUserMedia`). Браузер даёт
  камеру только на `https://` или `localhost`; на `http://<ip>` и при отказе в
  доступе страница скажет об этом и откроет выбор файла.

Чтобы открыть с телефона, запустите сервис с `WINE_API_HOST=0.0.0.0`.
Лупа в шапке ищет вино по каталогу (`GET /v1/catalog/search`) и даёт ссылку
на поиск по всему сайту (`vino-svoe.ru/search-result`).

Вёрстку можно смотреть без GPU и сервиса — на заготовленных ответах:

```bash
python tools/web-demo/build_demo.py          # пересобрать demo/ из catalog.jsonl
python -m http.server 8765 --directory services/search-api/src/search_api/web
# http://localhost:8765/?demo=match&autorun=1  и  ?demo=not_found&autorun=1
```

Проверка:

```bash
curl --noproxy '*' http://127.0.0.1:8080/health
curl --noproxy '*' -X POST 'http://127.0.0.1:8080/v1/search?top_k=5' -F image=@photo.jpg
curl --noproxy '*' -X POST 'http://127.0.0.1:8080/v1/eval/predict' -F image=@photo.jpg   # {"slug": "..."}
curl --noproxy '*' -X POST 'http://127.0.0.1:8080/v1/scan' -F image=@photo.jpg          # карточка + сомелье
curl --noproxy '*' 'http://127.0.0.1:8080/v1/wines/vermentino-viognier-2022'
curl --noproxy '*' 'http://127.0.0.1:8080/v1/pairing?dish=Сыры&style=red'
```

## Сканер для пользователя

| Метод | Что отдаёт |
|---|---|
| `POST /v1/scan` | `status` (`match` / `not_found`), `top1_confident`, `reason`, готовый `message`; при `match` — `wine` (карточка + советы сомелье + похожие вина) и остальные из топ-5 в `alternatives`; при `not_found` — топ-5 в `alternatives` |
| `GET /v1/wines/{slug}` | карточка с советами — когда пользователь выбрал вариант сам |
| `GET /v1/pairing?dish=&style=` | вина к блюду (сначала точное блюдо, затем то же семейство, внутри — по народному рейтингу) |
| `GET /v1/pairing/dishes` | все блюда каталога с числом вин |
| `GET /v1/catalog/search?q=` | поиск вина по названию, производителю, сорту, региону |
| `POST /v1/feedback` | «да, это оно» / «выбрал другое» / «моего вина нет» → `data/feedback/feedback.jsonl` |

`/v1/search` тоже отдаёт `status`, `top1_confident` и `reason`; контракт
`/v1/eval/predict` не менялся.

**Решение «одна карточка или варианты»** (`search_api/verdict.py`): карточка
показывается, только если балл картинки топ-1 ≥ 0,76 и (без VLM) отрыв от
второго ≥ 0,02 или (с VLM) его вероятность ≥ 0,8 и вероятность «ни одна не
подходит» < 0,5. Иначе — «такое вино не найдено» и топ-5. На 99 размеченных
фото: без VLM показано 60 карточек, верных 48 (80%), «не найдено» получили
22 из 33 вин вне каталога; с VLM — 65 / 55 (85%) / 25 из 33. Пороги подобраны
на той же выборке — это подгонка, а не независимый замер.

Фото блюд, регионов и сортов, как на сайте, берутся из
`search_api/site_media.json`; пересобрать его после пополнения каталога —
`python tools/site-media/fetch_site_media.py` (обходит ~110 страниц вин с паузой).

**Цифровой сомелье** (`search_api/sommelier.py`) — правила по полям каталога,
без внешних данных и моделей: стиль и тело вина (по категории и крепости),
температура подачи и как её добиться, бокал, аэрация, конкретные блюда к
каждой группе из карточки с учётом стиля вина, похожие вина с объяснением
(«тот же сорт: Верментино», «тоже Крым»; не больше двух от одного производителя).

## Оценка качества

```bash
# Прогон фото через сервис → results.jsonl + report.html
no_proxy='*' PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner --output-dir data/reports/<имя>

# Точность по ручной разметке (data/labels/real-photos.json)
PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.score data/reports/<имя>/results.jsonl

# Пересобрать сводную таблицу data/reports/accuracy.xlsx
PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.excel

# Импорт новой разметки из Label Studio (CSV-экспорт)
PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.labels data/project-...csv

# VLM на готовом прогоне, без перезапуска сервиса
PYTHONPATH=tools/eval-runner/src:services/search-api/src:libs/wine-embeddings/src \
  .venv/bin/python -m eval_runner.vlm_offline data/reports/results.jsonl \
  --out data/reports/vlm/results.jsonl --candidate-images \
  --query-max-pixels 524288 --candidate-max-pixels 131072
```

## Переменные окружения

Сервис и индексатор при запуске читают `.env` из корня; переменные, уже
заданные в окружении, имеют приоритет. Шаблон — [`.env.example`](.env.example).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `WINE_MODEL_ID` | `google/siglip2-so400m-patch16-512` | модель-энкодер; при наличии одноимённой папки в `models/` веса берутся с диска. После смены — переиндексация |
| `WINE_MODELS_DIR` | `./models` | куда складывать веса |
| `WINE_DEVICE` / `WINE_DTYPE` | `cuda` / `float16` | устройство и точность энкодера |
| `WINE_VRAM_LIMIT_GB` | `14` | жёсткий потолок видеопамяти процесса (общий для всех моделей API) |
| `WINE_BATCH_SIZE` | `32` | батч при индексации |
| `WINE_QDRANT_URL` | `http://localhost:6333` | адрес Qdrant |
| `WINE_COLLECTION` | `wines` | имя коллекции |
| `WINE_CATALOG_DIR` | `./data/catalog` | каталог: индексатор берёт оттуда фото, VLM — эталонные фото кандидатов |
| `WINE_API_HOST` / `WINE_API_PORT` | `127.0.0.1` / `8080` | адрес сервиса (8080 — порт из скрипта оценки) |
| `WINE_OCR_ENABLED` | `1` | OCR-переранжирование; `0` — выключить |
| `WINE_OCR_MODELS_DIR` | `./models/easyocr` | веса EasyOCR |
| `WINE_OCR_MAX_SIDE` | `1280` | до какой стороны уменьшать фото перед OCR |
| `WINE_OCR_MIN_CONFIDENCE` | `0.3` | порог уверенности строки OCR |
| `WINE_RERANK_K` | `20` | сколько кандидатов картинки переранжирует текст |
| `WINE_TEXT_WEIGHT` | `0.15` | вес текста: `score = cos + w · text_score` |
| `WINE_VLM_ENABLED` | `0` | VLM-реранкер (Qwen3-VL-4B) выбирает вино внутри топ-5; `1` — включить |
| `WINE_VLM_MODEL_DIR` | `./models/Qwen3-VL-4B-Instruct` | веса VLM |
| `WINE_VLM_TOP_K` | `5` | из скольких кандидатов выбирает VLM (1–9) |
| `WINE_VLM_CANDIDATE_IMAGES` | `1` | показывать VLM эталонные фото кандидатов, а не только текст карточек |
| `WINE_VLM_QUERY_MAX_PIXELS` | `524288` | площадь, до которой уменьшается фото пользователя для VLM |
| `WINE_VLM_CANDIDATE_MAX_PIXELS` | `131072` | площадь эталонного фото кандидата |
| `WINE_ACCEPT_MIN_IMAGE_SCORE` | `0.76` | ниже — «вино не найдено» |
| `WINE_ACCEPT_MIN_MARGIN` | `0.02` | без VLM: минимальный отрыв топ-1 от топ-2 |
| `WINE_ACCEPT_MIN_VLM_PROB` | `0.8` | с VLM: минимальная вероятность топ-1 |
| `WINE_ACCEPT_MAX_VLM_NONE_PROB` | `0.5` | с VLM: выше — «вино не найдено» |
| `WINE_WEB_ENABLED` | `1` | мобильный сканер на `/app/` |
| `WINE_FEEDBACK_DIR` | `./data/feedback` | куда писать отклики пользователей |
| `WINE_SITE_IMAGE_API` | ресайз-API vino-svoe.ru | фото бутылки, если нет локального PNG каталога |
| `SCRAPER_*` | см. `services/catalog-scraper/README.md` | параметры сбора каталога |

С VLM OCR перестаёт давать прирост точности (96,2% с ним и без), поэтому
рекомендуемая связка — `WINE_VLM_ENABLED=1` + `WINE_OCR_ENABLED=0`.

## Ограничения

- **Точность измерена на 53 уверенно размеченных фото** — выборка маленькая,
  1 фото ≈ 2 п.п., разница в 1–2 фото между настройками — шум. Ground truth
  организатор не выдаёт, разметка ручная.
- **Отказ «нет в каталоге» — только в пользовательских ручках.** `/v1/scan` и
  поле `status` в `/v1/search` говорят «не найдено» для 22–25 из 33 вин вне
  каталога; `/v1/eval/predict` по-прежнему всегда возвращает ближайшее вино —
  какой ответ ждёт организатор в этом случае, неизвестно.
- **Вероятность VLM не откалибрована**: почти всегда 0,9–1,0, в том числе на ошибках.
- **25 групп каталожных фото байт-идентичны** (50 вин): одна и та же картинка
  на разные slug — различить их по изображению невозможно.
- **71 slug из CSV-дампа отсутствует на сайте** (страницы отдают 404), фото для
  них взять неоткуда. Если закрытая таблица ответов построена по дампу, эти вина
  недостижимы.
- **Docker Hub недоступен из сети разработки** — Qdrant запускается нативным
  бинарником (`infra/run-qdrant.sh`), `docker-compose.yml` оставлен для среды,
  где registry доступен.
- **Трафик идёт через прокси**, который рвёт длинные соединения: веса скачиваются
  скриптами с докачкой, а localhost исключается из проксирования в рантайме.
