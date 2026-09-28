# Сканер российских вин «Своё Вино»

Поиск карточки вина по фотографии этикетки, снятой в реальных условиях —
у полки магазина, под углом, с бликами. Решение кейса РСХБ.Цифра, ТЗ —
[`data/10. РСХБ.Цифра.pdf`](data/10.%20РСХБ.Цифра.pdf).

Задача решается как retrieval, а не классификация: новое вино в каталоге —
один новый вектор в индексе, без переобучения.

```
фото → SigLIP 2 → вектор → Qdrant топ-10 → Qwen3-VL выбирает вино из первых 5 → Qwen3-VL проверяет выбранное
     → /v1/eval/predict: slug топ-1   |   /v1/scan: «нашли» или «нет в каталоге — вот аналоги» + топ-10
```

| Конфигурация | top-1 (n=53) | «нет в каталоге» у вин вне каталога | p95 ответа | VRAM |
|---|---|---|---|---|
| только картинка (`WINE_VLM_ENABLED=0`) | 77,4% | порог по баллу: лучший разделяет 61 из 74 | 0,4 с | ~1,2 ГБ |
| **картинка + VLM (топ-5) + проверка (по умолчанию)** | **96,2%** | **30 из 33**, верный скрыт у 5 из 62 | 1,6 с (max 1,6) | ~10,3 ГБ |

Точность — по ручной разметке 100 реальных фото (53 размечены уверенно, 33 —
вина нет в каталоге). SLA организатора — 3 с. Все прогоны —
[`data/reports/accuracy.xlsx`](data/reports/accuracy.xlsx).

Архитектура и границы слоёв → [`ARCHITECTURE.md`](ARCHITECTURE.md).
Контекст задачи, данные и набитые шишки → [`CLAUDE.md`](CLAUDE.md).
Замеры и разбор ошибок → [`docs/baseline-results.md`](docs/baseline-results.md).

## Запуск для проверки (Docker)

Всё работает в контейнерах: Qdrant, API с веб-сканером, парсер каталога,
индексатор. Модели и каталог скачиваются на вашей машине, индекс тоже строится
у вас. Одна команда делает всё с нуля.

### Что нужно на машине

| | |
|---|---|
| ОС | Linux x86_64 (проверено на Ubuntu) |
| GPU | NVIDIA, **от 12 ГБ видеопамяти** (сервис занимает ~10,3 ГБ, лимит в коде — 14 ГБ); проверено на RTX 3090 |
| ПО | Docker с Compose v2 (`docker compose version`), [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), `bash`, `curl`, `jq`, `git` |
| Диск | ~25 ГБ: веса моделей 13 ГБ, образы ~8 ГБ, фото каталога 1 ГБ |
| Сеть | huggingface.co (веса), vino-svoe.ru (каталог), Docker Hub и PyPI (сборка образов) |
| Порты | 8080 (сервис, контракт оценки), 6333 на localhost (Qdrant) |

Проверить, что Docker видит GPU:

```bash
docker run --rm --gpus all ubuntu nvidia-smi
```

### Первый запуск

```bash
git clone <репозиторий> wines && cd wines
./infra/init.sh
```

`init.sh` выполняет шесть шагов. Если скрипт прервался, запустите его снова:
то, что уже скачано и построено, он пропустит.

| Шаг | Что происходит | Время |
|---|---|---|
| 1. Модели | `google/siglip2-so400m-patch16-512` (4,3 ГБ) и `Qwen/Qwen3-VL-4B-Instruct` (8,9 ГБ) → `models/`, с докачкой | зависит от канала |
| 2. Образы | `docker compose --profile init build`: api, indexer, scraper | 5–15 минут |
| 3. Каталог | парсер обходит vino-svoe.ru и сохраняет фото и карточки ~2100 вин в `data/catalog/`; пауза между запросами, чтобы не нагружать сайт | 35–40 минут |
| 4. Индекс | эмбеддинги SigLIP 2 для всех фото каталога → Qdrant (коллекция `wines`, dim 1152, косинус) | ~4 минуты |
| 5. Сервис | загрузка моделей в видеопамять, прогрев, кэш уменьшенных эталонов для VLM | 1–2 минуты |
| 6. Смоук-тесты | `infra/smoke.sh`, см. ниже | ~20 секунд |

В конце скрипт печатает адреса:

- сканер для телефона: `http://<IP машины>:8080/app/`;
- контракт оценки: `POST http://127.0.0.1:8080/v1/eval/predict`;
- документация API: `http://127.0.0.1:8080/docs`.

Дальше сервис поднимается одной командой:

```bash
docker compose up -d          # Qdrant + API; после перезагрузки машины стартуют сами
docker compose ps             # оба сервиса должны быть healthy
docker compose logs -f api    # лог сервиса
docker compose down           # остановить
```

Каталог на сайте пополняется. Чтобы подтянуть новые вина, запустите парсер
ещё раз: он скачает только недостающее. Затем пересчитайте индекс:

```bash
docker compose run --rm scraper
docker compose run --rm indexer --recreate
docker compose restart api
```

### Смоук-тесты

```bash
./infra/smoke.sh                               # сервис на 127.0.0.1:8080
./infra/smoke.sh --queries ./eval/queries      # плюс каждое фото из папки
./infra/smoke.sh --url http://<хост>:8080 --samples 5
```

Что проверяется:

- `/health` отвечает, число вин в индексе совпадает с `data/catalog/catalog.jsonl`,
  Qdrant в статусе `green`;
- **контракт `/v1/eval/predict`**: ответ ровно `{"slug": "<строка>"}` или
  `{"slug": null}`, время ответа меньше 3 секунд. Фото из каталога должно вернуть
  свой же slug. В каталоге есть вина с одинаковым фото, для них подходит любой
  slug из группы;
- не картинка → 4xx, сервис при этом не падает;
- `/v1/search` (топ-1 + топ-10), `/v1/scan` (карточка + советы сомелье),
  `/v1/wines/{slug}`, `/v1/pairing/dishes`, `/v1/catalog/search`;
- веб-сканер `/app/` отдаётся.

Скрипт возвращает код 0, если все проверки прошли; это удобно для CI.
Пример вывода:

```
Контракт POST /v1/eval/predict
  OK    фото каталога → свой slug (gunko-winery-risling-rezerv-beloe-suhoe-135, 571 мс)
  OK    не картинка → HTTP 400, сервис не падает
...
Скорость /v1/eval/predict
  8 запросов: среднее 1047 мс, max 1491 мс (SLA 3000 мс)

Итого: 18 прошло, 0 не прошло
```

### Прогон скрипта оценки организатора

Скрипт из `eval.zip` работает с сервисом без изменений:

```bash
mkdir -p eval && unzip -o "data/Датасет (1)/eval.zip" -x '__MACOSX/*' -d eval
cd eval && rm -f predictions.jsonl
bash participant_test.sh --images-dir ./queries --manifest ./queries.tsv \
  --endpoint 'http://127.0.0.1:8080/v1/eval/predict' --output ./predictions.jsonl
```

На трёх фото из архива ответ приходит за 1,4–1,5 с. Если вина нет в каталоге,
сервис отвечает `{"slug": null}`, и скрипт записывает его как `predicted_slug: null`.
Это сделано намеренно: см. «Ограничения».

### Если что-то не так

| Симптом | Причина и решение |
|---|---|
| `failed to bind host port ... 6333` или `8080: address already in use` | Порт уже занят, например Qdrant или сервисом, запущенным без Docker. Найти процесс: `ss -ltnp \| grep -E ':6333\|:8080'`. Порт сервиса можно сменить: `WINE_API_PORT=8081 docker compose up -d` |
| `could not select device driver "nvidia"` | Не установлен NVIDIA Container Toolkit или Docker не перезапущен после установки (`sudo systemctl restart docker`) |
| API долго в статусе `starting` | При первом старте модели загружаются в видеопамять и строится кэш эталонов, это до 2 минут. Ход загрузки видно в `docker compose logs -f api` |
| CUDA out of memory | На GPU работает что-то ещё. Сервису нужно ~10,3 ГБ. Без VLM ему хватает ~1,2 ГБ, но точность падает до 77% (`WINE_VLM_ENABLED=0` в `.env`, затем `docker compose up -d api`) |
| В логе API при первом старте `Temporary failure in name resolution` | API стартовал раньше Qdrant. Он перезапустится сам (`restart: unless-stopped`) |
| Выход в интернет только через прокси | Задайте `HTTP_PROXY`/`HTTPS_PROXY` и `no_proxy=127.0.0.1,localhost` в окружении перед `init.sh`. Эти переменные получают curl при скачивании весов и парсер (он работает в сети хоста). Для `pip install` при сборке образов прокси задаётся в `~/.docker/config.json` (раздел `proxies`, [документация Docker](https://docs.docker.com/engine/cli/proxy/)). API и индексатору интернет не нужен |
| Смоук-тест: «в индексе N точек, в каталоге M вин» | Каталог дополнили, а индекс не пересчитали: `docker compose run --rm indexer --recreate`, затем `docker compose restart api` |

Все настройки задаются в `.env` (создаётся из `.env.example` при первом запуске),
полный список — в разделе «Переменные окружения». В контейнере адрес Qdrant
и прослушивание `0.0.0.0` задаёт compose, `.env` их не перекрывает.

## Структура

```
libs/wine-embeddings/       общий энкодер (SigLIP 2): модель, нормализация, лимит VRAM
services/catalog-scraper/   сбор каталога с vino-svoe.ru → data/catalog/
services/indexer/           каталог → векторы → Qdrant
services/search-api/        FastAPI: /v1/eval/predict (контракт организатора), /v1/scan и /app/
                            (сканер + сомелье), /v1/search (топ-10 для анализа)
tools/eval-runner/          прогон фото через API → HTML-отчёт, точность, Excel
tools/web-demo/             демо-ответы API для вёрстки без GPU
tools/site-media/           фото блюд, регионов и сортов с сайта для карточки
infra/                      init.sh (первый запуск), smoke.sh (смоук-тесты), загрузка весов,
                            run-qdrant.sh (Qdrant без Docker)
docker-compose.yml          qdrant + api; scraper, indexer — профиль init
services/*/Dockerfile       образ на сервис, только его зависимости
docs/                       данные, парсер, результаты, план, журнал изменений;
                            docs/history/ — исходный план и этапы, которые уже не актуальны
data/                       ТЗ, датасет организатора, разметка, каталог (метаданные), отчёты
```

## Что не лежит в репозитории

Тяжёлые файлы исключены (`.gitignore`) и восстанавливаются так:

| Что | Размер | Как получить |
|---|---|---|
| `models/siglip2-so400m-patch16-512` | 4,3 ГБ | `./infra/fetch-model.sh google/siglip2-so400m-patch16-512` |
| `models/Qwen3-VL-4B-Instruct` | 8,9 ГБ | `./infra/fetch-vlm.sh` |
| `data/catalog/vlm-refs-*/` | ~0,1 ГБ | уменьшенные эталоны для VLM — сервис строит сам при старте |
| `infra/bin/qdrant` | 86 МБ | бинарник Qdrant 1.19.1, см. ниже |
| `data/catalog/images/` | ~1 ГБ | парсер каталога (шаг 2 запуска), `catalog.jsonl` уже в репозитории |
| `data/Датасет (1)/Реальные фото.zip`, `data/real-photos/` | 94 МБ | архив организатора; распаковать в `data/real-photos/` |
| `data/qdrant/` | — | строится индексатором (шаг 3) |

## Разработка без Docker

То же самое можно запустить из виртуального окружения. Так удобнее
отлаживать код и гонять оценку. Перед этим остановите контейнеры
(`docker compose down`): они занимают те же порты 6333 и 8080. Хранилище
Qdrant (`data/qdrant/storage`) у обоих способов общее.

### Сетап

Python 3.12, CUDA-GPU (проверено на RTX 3090; процессу отводится не более 14 ГБ VRAM).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r services/search-api/requirements.txt \
                      -r services/indexer/requirements.txt \
                      -r services/catalog-scraper/requirements.txt \
                      -r tools/eval-runner/requirements.txt

./infra/fetch-model.sh google/siglip2-so400m-patch16-512
./infra/fetch-vlm.sh

# Qdrant без Docker
mkdir -p infra/bin && curl -L https://github.com/qdrant/qdrant/releases/download/v1.19.1/qdrant-x86_64-unknown-linux-gnu.tar.gz \
  | tar -xz -C infra/bin

cp .env.example .env              # и поправить под себя
```

Веса скачиваются в папку проекта, а не в `~/.cache`, через curl с докачкой:
штатный загрузчик HuggingFace на обрывах начинает файл заново.

`requirements.txt` сервиса поиска закрепляет `torchvision==0.23.0` (нужен
процессору Qwen3-VL): свежий torchvision поднимает torch до версии с CUDA 13
и ломает стек.

### Запуск

```bash
# 1. Векторная база
./infra/run-qdrant.sh                      # либо: docker compose up -d qdrant

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

С телефона — по IP машины в той же сети (`WINE_API_HOST=0.0.0.0`, так в `.env.example`).
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
curl --noproxy '*' -X POST 'http://127.0.0.1:8080/v1/search' -F image=@photo.jpg          # top1 + топ-10 + status
curl --noproxy '*' -X POST 'http://127.0.0.1:8080/v1/eval/predict' -F image=@photo.jpg   # {"slug": "..."} или {"slug": null}
curl --noproxy '*' -X POST 'http://127.0.0.1:8080/v1/scan' -F image=@photo.jpg          # карточка + сомелье
curl --noproxy '*' 'http://127.0.0.1:8080/v1/wines/vermentino-viognier-2022'
curl --noproxy '*' 'http://127.0.0.1:8080/v1/pairing?dish=Сыры&style=red'
```

## Сканер для пользователя

| Метод | Что отдаёт |
|---|---|
| `POST /v1/scan` | `status` (`match` / `not_found`), `top1_confident`, `reason`, готовый `message`; при `match` — `wine` (карточка + советы сомелье + похожие вина) и остальные из топ-10 в `alternatives`; при `not_found` — «Ваше вино в каталоге не найдено, но вот какие аналоги вы можете найти» и топ-10 в `alternatives` |
| `GET /v1/wines/{slug}` | карточка с советами — когда пользователь выбрал вариант сам |
| `GET /v1/pairing?dish=&style=` | вина к блюду (сначала точное блюдо, затем то же семейство, внутри — по народному рейтингу) |
| `GET /v1/pairing/dishes` | все блюда каталога с числом вин |
| `GET /v1/catalog/search?q=` | поиск вина по названию, производителю, сорту, региону |
| `POST /v1/feedback` | «да, это оно» / «выбрал другое» / «моего вина нет» → `data/feedback/feedback.jsonl` |

`/v1/search` отдаёт `top1` (тот же slug, что `/v1/eval/predict` — для метрики),
`results` (топ-10 — для интерфейса), `status`, `reason`, `message`,
`verify_prob`; контракт `/v1/eval/predict` не менялся.

**Решение «нашли / нет в каталоге»** (`search_api/verdict.py`): VLM выбирает
вино среди первых 5 из топ-10, затем отдельно проверяет его — «одно и то же
вино?» по этикетке, тексту на бутылке, форме бутылки и цвету вина. Вероятность
«Да» < 0,3 → «нет в каталоге» (`reason=not_verified`); вероятность выбора
< 0,8 → «уточните, какое это вино» (`ambiguous`). На 99 размеченных фото:
«нет в каталоге» у 30 из 33 вин вне каталога, верный ответ скрыт у 5 из 62
(два — карточка на сайте расходится с бутылкой: «Красное сухое» у белого рислинга,
прежний дизайн этикетки у Red Blend) и ещё у 2 показан экран «уточните» с верным
вином первым. Пороги подобраны на той же выборке
— это подгонка, а не независимый замер.

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

# VLM на готовом прогоне (только картинка, --top-k ≥ 10), без сервиса
PYTHONPATH=tools/eval-runner/src:services/search-api/src:libs/wine-embeddings/src \
  .venv/bin/python -m eval_runner.vlm_offline data/reports/image-top20/results.jsonl \
  --out data/reports/vlm/results.jsonl --top-k 10 --candidate-images --verify \
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
| `WINE_API_HOST` / `WINE_API_PORT` | `127.0.0.1` / `8080` | адрес сервиса (8080 — порт из скрипта оценки); `0.0.0.0` — доступ с телефона |
| `WINE_TOP_K` | `10` | сколько вин отдают `/v1/search` и `/v1/scan` |
| `WINE_VLM_ENABLED` | `1` | VLM (Qwen3-VL-4B): выбор вина и проверка «нет в каталоге»; `0` — только картинка |
| `WINE_VLM_MODEL_DIR` | `./models/Qwen3-VL-4B-Instruct` | веса VLM |
| `WINE_VLM_TOP_K` | `5` | из скольких кандидатов выбирает VLM (1–9, метки — цифры); из 10 — медленнее и на 2 фото хуже |
| `WINE_VLM_CANDIDATE_IMAGES` | `1` | показывать VLM эталонные фото кандидатов, а не только текст карточек |
| `WINE_VLM_QUERY_MAX_PIXELS` | `524288` | площадь, до которой уменьшается фото пользователя для VLM |
| `WINE_VLM_CANDIDATE_MAX_PIXELS` | `131072` | площадь эталонного фото кандидата (и имя папки кэша `vlm-refs-*`) |
| `WINE_ACCEPT_MIN_VERIFY_PROB` | `0.3` | с VLM: проверка топ-1 ниже — «нет в каталоге» |
| `WINE_ACCEPT_MAX_VLM_NONE_PROB` | `0.5` | с VLM: вероятность «ни одна не подходит» выше — «нет в каталоге» |
| `WINE_ACCEPT_MIN_VLM_PROB` | `0.8` | с VLM: вероятность выбора топ-1 ниже — «уточните, какое это вино» |
| `WINE_PREDICT_EMPTY_IF_ABSENT` | `1` | вина нет в каталоге → `{"slug": null}` в `/v1/eval/predict` и `top1: null`; `0` — ближайшее вино |
| `WINE_ACCEPT_MIN_IMAGE_SCORE` | `0.76` | без VLM: балл картинки ниже — «нет в каталоге» |
| `WINE_ACCEPT_MIN_MARGIN` | `0.02` | без VLM: минимальный отрыв топ-1 от топ-2 |
| `WINE_WEB_ENABLED` | `1` | мобильный сканер на `/app/` |
| `WINE_FEEDBACK_DIR` | `./data/feedback` | куда писать отклики пользователей |
| `WINE_SITE_IMAGE_API` | ресайз-API vino-svoe.ru | фото бутылки, если нет локального PNG каталога |
| `SCRAPER_*` | см. `services/catalog-scraper/README.md` | параметры сбора каталога |

OCR (EasyOCR) из пайплайна удалён 28.09.2026: с VLM он не давал прироста
точности, а стоил 0,35 с и 2 ГБ VRAM.

## Ограничения

- **Точность измерена на 53 уверенно размеченных фото** — выборка маленькая,
  1 фото ≈ 2 п.п., разница в 1–2 фото между настройками — шум. Ground truth
  организатор не выдаёт, разметка ручная.
- **Вина нет в каталоге → `/v1/eval/predict` отдаёт `{"slug": null}`**, а
  `/v1/search` и `/v1/scan` — `top1: null` (интерфейс показывает «Данное вино
  отсутствует в каталоге» и аналоги). Так для 30 из 33 вин вне каталога; цена —
  5 из 62 верных ответов тоже уходят в null. По разметке 100 фото, если для вин
  вне каталога верный ответ — null: 87 из 99 верных против 62 из 99 без этого. `WINE_PREDICT_EMPTY_IF_ABSENT=0` —
  всегда отдавать ближайшее вино.
- **Вероятность выбора VLM не откалибрована**: почти всегда 0,9–1,0, в том числе
  на ошибках. «Нет в каталоге» решает отдельная проверка, а не она.
- **VLM выбирает из топ-5, а не из топ-10**: из десяти было 92,5% и до 3,7 с —
  лишние кандидаты путают вина одной линейки. Выдача наружу — топ-10.
- **25 групп каталожных фото байт-идентичны** (50 вин): одна и та же картинка
  на разные slug — различить их по изображению невозможно.
- **71 slug из CSV-дампа отсутствует на сайте** (страницы отдают 404), фото для
  них взять неоткуда. Если закрытая таблица ответов построена по дампу, эти вина
  недостижимы.
- **Docker Hub из сети разработки нестабилен** (503, обрывы на CDN), поэтому
  для отладки есть запасной путь без Docker: нативный Qdrant
  (`infra/run-qdrant.sh`) той же версии и с тем же хранилищем. Стек в Docker
  проверен 28.09: 100 реальных фото дали те же ответы, что и без Docker.
- **Трафик идёт через прокси**, который рвёт длинные соединения: веса скачиваются
  скриптами с докачкой, а localhost исключается из проксирования в рантайме.
