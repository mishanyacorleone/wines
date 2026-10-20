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
| **картинка + VLM (топ-5) + проверка (по умолчанию)** | **96,2%** | **30 из 33**, верный скрыт у 5 из 62 | 1,6 с (max 1,6) | ~13,5 ГБ: vLLM ~12 + SigLIP ~1,5 |

Точность — по ручной разметке 100 реальных фото (53 размечены уверенно, 33 —
вина нет в каталоге). SLA организатора — 3 с. VLM (Qwen3-VL-4B-FP8) работает в
vLLM: та же точность, что у Qwen3-VL-4B в transformers. Все прогоны —
[`data/reports/accuracy.xlsx`](data/reports/accuracy.xlsx).

Архитектура и границы слоёв → [`ARCHITECTURE.md`](ARCHITECTURE.md).
Контекст задачи, данные и набитые шишки → [`CLAUDE.md`](CLAUDE.md).
Замеры и разбор ошибок → [`docs/baseline-results.md`](docs/baseline-results.md).

## Запуск для проверки (Docker)

Всё работает в контейнерах, каждый сервис отвечает за одно: шлюз (nginx, порт
8080), страница сканера (web), API распознавания и каталога (api, без GPU), модели
— SigLIP 2 (embedder) и Qwen3-VL (vllm), Qdrant, синхронизация каталога с сайтом
(catalog), личный кабинет (users) и аналитика с PostgreSQL, сборщик логов. Упавший
сервис отключает только свою функцию: без моделей сайт открывается, а сканер
пишет «распознавание временно недоступно». Модели и каталог скачиваются на вашей
машине, индекс тоже строится у вас. Одна команда делает всё с нуля.

### Что нужно на машине

| | |
|---|---|
| ОС | Linux x86_64 (проверено на Ubuntu) |
| GPU | NVIDIA, **24 ГБ видеопамяти** (vLLM берёт 0,5 карты ≈ 12 ГБ, SigLIP ~1,5 ГБ); проверено на RTX 3090. Меньше — `WINE_VLM_ENABLED=0` (только картинка, top-1 77%) |
| ПО | Docker с Compose v2 (`docker compose version`), [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), `bash`, `curl`, `jq`, `git` |
| Диск | ~45 ГБ: веса моделей 10 ГБ, образ vLLM ~23 ГБ, наши образы ~8 ГБ, фото каталога 1 ГБ |
| Сеть | huggingface.co (веса), vino-svoe.ru (каталог и его синхронизация), Docker Hub и PyPI (образы) |
| Порты | 8080 (шлюз: сервис и контракт оценки); на localhost — 6333 (Qdrant), 8090 (API напрямую), 8100 (vLLM), 5433 (PostgreSQL) |

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
| 1. Модели | `google/siglip2-so400m-patch16-512` (4,3 ГБ) и `Qwen/Qwen3-VL-4B-Instruct-FP8` (5,7 ГБ) → `models/`, с докачкой | зависит от канала |
| 2. Образы | `docker compose --profile init build` (api, embedder, catalog, users, analytics, logs, indexer, scraper) и `docker compose pull vllm` (~23 ГБ) | 10–30 минут |
| 3. Каталог | парсер обходит vino-svoe.ru и сохраняет фото и карточки ~2100 вин в `data/catalog/`; пауза между запросами, чтобы не нагружать сайт | 35–40 минут |
| 4. Индекс | эмбеддинги SigLIP 2 для всех фото каталога → Qdrant (коллекция `wines`, dim 1152, косинус) | ~4 минуты |
| 5. Сервисы | все контейнеры; vLLM грузит модель и компилирует ядра (кэш — `data/vllm-cache`) | до 6 минут в первый раз, ~1–2 потом |
| 6. Смоук-тесты | `infra/smoke.sh`, см. ниже | ~20 секунд |

В конце скрипт печатает адреса:

- сканер для телефона: `http://<IP машины>:8080/app/`;
- контракт оценки: `POST http://127.0.0.1:8080/v1/eval/predict`;
- документация API: `http://127.0.0.1:8080/docs`.

Дальше сервис поднимается одной командой:

```bash
docker compose up -d          # все сервисы; после перезагрузки машины стартуют сами
docker compose ps             # healthy у gateway, web, api, embedder, vllm, qdrant, users, analytics, catalog
tail -f logs/$(date +%F)/all.log   # что происходит: сканы, входы, ошибки, перезапуски
docker compose down           # остановить
```

**Новые вина без перезапуска.** Каталог на сайте пополняется (до ~50 вин в день).
Сервис `catalog` сверяет его с сайтом раз в сутки и по кнопке в админке:
`/app/` → Профиль → Управление → «Каталог» → «Обновить каталог с сайта». Новые вина
скачиваются, получают вектор и сразу появляются в поиске — ничего не
перезапускается. Там же — расписание (вкл./выкл., «N раз в минуту / час / день / неделю / месяц») и история
запусков. Если выход в интернет только через прокси на 127.0.0.1 — см. «Если что-то
не так». Полная пересборка индекса по-прежнему — `docker compose run --rm indexer --recreate`.

**Логи** — в `logs/<дата>/`: `all.log` — важное по всем сервисам (сканы и их итог,
входы, действия админа, синхронизация каталога, ошибки, медленные запросы, падения
и перезапуски контейнеров), `services/<сервис>.log` — всё по одному сервису,
`services/gateway.log` — каждый запрос к API. Номер в скобках (`[33319c71]`) — номер
запроса: по нему видно, как один скан прошёл через шлюз, api, embedder и vLLM.

### Смоук-тесты

```bash
./infra/smoke.sh                               # сервис на 127.0.0.1:8080
./infra/smoke.sh --queries ./eval/queries      # плюс каждое фото из папки
./infra/smoke.sh --url http://<хост>:8080 --samples 5
```

Что проверяется:

- шлюз отвечает; `/health` api: Qdrant, каталог в памяти, embedder и vLLM — каждый
  отдельной строкой; число вин в индексе совпадает с `data/catalog/catalog.jsonl`;
- **контракт `/v1/eval/predict`**: ответ ровно `{"slug": "<строка>"}` или
  `{"slug": null}`, время ответа меньше 3 секунд. Фото из каталога должно вернуть
  свой же slug. В каталоге есть вина с одинаковым фото, для них подходит любой
  slug из группы;
- не картинка → 4xx, сервис при этом не падает;
- `/v1/search` (топ-1 + топ-10), `/v1/scan` (карточка + советы сомелье),
  `/v1/wines/{slug}`, `/v1/pairing/dishes`, `/v1/catalog/search`;
- веб-сканер `/app/`, «Поделиться», регионы карты, аналитика, личный кабинет
  (регистрация → скан → лайк → карта → удаление тестового аккаунта);
- синхронизация каталога закрыта без входа админа, `/internal/` снаружи недоступен;
- логи за сегодня пишутся.

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

Скрипт из `eval.zip` работает с сервисом без изменений. Всё для прогона лежит
в папке `eval/` в корне репозитория (она в `.gitignore`):

```
eval/
├── participant_test.sh   # скрипт организатора, не менять
├── queries.tsv           # список фото: query_id<TAB>image_path
├── queries/              # сами фото
└── predictions.jsonl     # ответы сервиса — появляется после прогона, его отдаём организатору
```

**Режим сервиса.** По условиям проверки все фото из `queries` есть в каталоге,
поэтому `/v1/eval/predict` по умолчанию всегда отдаёт вино, выбранное VLM
из топ-5, и никогда не отвечает `{"slug": null}` — «вина нет в каталоге» здесь
может только отнять верные ответы. Настраивать ничего не нужно.

Веб-сканер `/app/` при этом честно пишет «Данное вино отсутствует в каталоге»
и предлагает аналоги: экран строится по причине отказа, а не по ответу для
метрики. Точность и скорость: top-1 96,2%, p95 1,6 с
(прогон `data/reports/service-eval-mode/`). Отдавать `null` для вин вне
каталога — `WINE_PREDICT_EMPTY_IF_ABSENT=1` в `.env`, затем
`docker compose up -d --force-recreate api`.

**Прогон.** Фото и `queries.tsv` от организатора кладём в `eval/`
(старые фото из `queries/` удалить), затем:

```bash
# если eval/ ещё нет — распаковать архив организатора
mkdir -p eval && unzip -o "data/Датасет (1)/eval.zip" -x '__MACOSX/*' -d eval

cd eval && rm -f predictions.jsonl     # скрипт не перезаписывает готовый файл
bash participant_test.sh --images-dir ./queries --manifest ./queries.tsv \
  --endpoint 'http://127.0.0.1:8080/v1/eval/predict' --output ./predictions.jsonl
wc -l predictions.jsonl                                     # строк = фото в queries.tsv
jq -r 'select(.predicted_slug == null) | .image_path' predictions.jsonl   # пусто
```

На трёх фото из архива ответ приходит за 1,3–1,5 с. Что важно знать о скрипте:

- ждёт ответ не дольше 10 с, повторов нет: сорвал — в файле `predicted_slug: null`;
- временную папку создаёт через `mktemp -d` и требует, чтобы она была в `/tmp`:
  если в окружении задан `TMPDIR` с другим путём, скрипт падает — `unset TMPDIR`;
- нужны `curl`, `jq`, `awk`; при системном прокси — `no_proxy=127.0.0.1,localhost`;
- расширение файла может не совпадать с форматом (в архиве все три фото — WebP,
  два из них с `.jpg`): сервис определяет формат по содержимому.

### Если что-то не так

| Симптом | Причина и решение |
|---|---|
| `failed to bind host port ... 6333` или `8080: address already in use` | Порт уже занят, например Qdrant или сервисом, запущенным без Docker. Найти процесс: `ss -ltnp \| grep -E ':6333\|:8080'`. Порт сервиса можно сменить: `WINE_API_PORT=8081 docker compose up -d` |
| `could not select device driver "nvidia"` | Не установлен NVIDIA Container Toolkit или Docker не перезапущен после установки (`sudo systemctl restart docker`) |
| `vllm` долго в статусе `starting`, сканер пишет «упрощённый режим» | Первый старт vLLM — до 6 минут (веса, профилирование, CUDA graphs). Пока его нет, сканер отвечает по одной картинке. Ход — `logs/<дата>/services/vllm.log` |
| Сканер: «Распознавание временно недоступно» | Не отвечает embedder или Qdrant: `docker compose ps`, строка `… недоступен` в `logs/<дата>/all.log` |
| CUDA out of memory | На GPU работает что-то ещё. vLLM нужно ~12 ГБ, SigLIP ~1,5 ГБ. Без VLM — только SigLIP, но точность падает до 77% (`WINE_VLM_ENABLED=0` в `.env`, `docker compose up -d api` и `docker compose stop vllm`) |
| Выход в интернет только через прокси | Задайте `HTTP_PROXY`/`HTTPS_PROXY` и `no_proxy=127.0.0.1,localhost` в окружении перед `init.sh`. Эти переменные получают curl при скачивании весов и парсер (он работает в сети хоста). Для `pip install` при сборке образов прокси задаётся в `~/.docker/config.json` (раздел `proxies`, [документация Docker](https://docs.docker.com/engine/cli/proxy/)). Синхронизации каталога (сервис catalog) — `WINE_SYNC_PROXY` в `.env`; если прокси слушает только 127.0.0.1 хоста, из контейнера его не видно — включите ретранслятор: `COMPOSE_PROFILES=proxy`, `WINE_SYNC_PROXY=http://host.docker.internal:7115`, `WINE_EGRESS_TARGET=127.0.0.1:<порт прокси>`. Остальным сервисам интернет не нужен |
| Синхронизация каталога: «сайт vino-svoe.ru недоступен» | Нет выхода в интернет из контейнера catalog — см. строку выше про прокси |
| Парсер: «Ошибок: 2097», в `data/catalog/failures.jsonl` — `CERTIFICATE_VERIFY_FAILED` или `ConnectError`; `init.sh` останавливается с «каталог пуст» | Контейнер парсера пошёл в интернет напрямую, а сеть пускает наружу только через прокси (или подменяет сертификаты при прямом выходе). Задайте прокси в окружении — `HTTP_PROXY`/`HTTPS_PROXY` или строчные `http_proxy`/`https_proxy` — и запустите `./infra/init.sh` снова; проверить, что compose его видит: `docker compose --profile init config scraper \| grep PROXY` |
| Смоук-тест: «в индексе N точек, в каталоге M вин» | Каталог дополнили, а в индекс не добавили: «Обновить каталог с сайта» в админке (добавит недостающие) или `docker compose run --rm indexer --recreate` |

Все настройки задаются в `.env` (создаётся из `.env.example` при первом запуске),
полный список — в разделе «Переменные окружения». В контейнерах адреса сервисов
и прослушивание `0.0.0.0` задаёт compose, `.env` их не перекрывает.

## Структура

```
libs/wine-embeddings/       общий энкодер (SigLIP 2): модель, нормализация, лимит VRAM — embedder и indexer
libs/wine-logging/          единый формат логов Python-сервисов, номер запроса
services/web/               nginx: страница сканера (app/ — HTML, JS, CSS) и фото бутылок
services/search-api/        api (без GPU): /v1/eval/predict (контракт организатора), /v1/scan (сканер +
                            сомелье), /v1/search (топ-10 для анализа), карточки, блюда, регионы
services/embedder/          модель SigLIP 2: фото → вектор (GPU)
services/catalog-service/   синхронизация каталога с сайтом по кнопке и расписанию
services/catalog-scraper/   сбор каталога с vino-svoe.ru → data/catalog/ (первый запуск)
services/indexer/           каталог → векторы → Qdrant (первый запуск, полная переиндексация)
services/user-service/      FastAPI + PostgreSQL: аккаунт, профиль, история сканов, оценки, достижения
services/analytics-service/ FastAPI + PostgreSQL: события веб-сканера и дашборд для РСХБ
services/log-collector/     сборщик логов: stdout контейнеров → logs/<дата>/
tools/eval-runner/          прогон фото через API → HTML-отчёт, точность, Excel, нагрузка (RPS)
tools/web-demo/             демо-ответы API для вёрстки без GPU
tools/site-media/           фото блюд, регионов и сортов с сайта для карточки
tools/region-map/           контуры субъектов РФ для винной карты (Natural Earth → services/web/app/russia-map*.json)
infra/                      init.sh (первый запуск), smoke.sh (смоук-тесты), загрузка весов,
                            run-qdrant.sh (Qdrant без Docker), egress/ (ретранслятор к прокси хоста)
docker-compose.yml          все сервисы; scraper, indexer — профиль init; egress — профиль proxy
infra/gateway/nginx.conf    маршруты шлюза: какой путь в какой сервис, ответы за упавший сервис
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
| `models/Qwen3-VL-4B-Instruct-FP8` | 5,7 ГБ | `./infra/fetch-vlm.sh` |
| `data/catalog/vlm-refs-*/` | ~0,1 ГБ | уменьшенные эталоны для VLM — api строит сам при старте и после новых вин |
| `data/catalog/sync-state.json` | — | расписание и история синхронизации каталога — создаёт сервис catalog |
| `logs/` | — | логи по дням — пишет сборщик логов |
| `infra/bin/qdrant` | 86 МБ | бинарник Qdrant 1.19.1, см. ниже |
| `data/catalog/images/` | ~1 ГБ | парсер каталога (шаг 2 запуска), `catalog.jsonl` уже в репозитории |
| `data/Датасет (1)/Реальные фото.zip`, `data/real-photos/` | 94 МБ | архив организатора; распаковать в `data/real-photos/` |
| `data/qdrant/` | — | строится индексатором (шаг 3) |
| `data/users/`, том `postgres-data` | — | данные пользователей: создаются сервисом пользователей при работе |

## Разработка без Docker

Сервисы можно запустить из виртуального окружения — так удобнее отлаживать код
и гонять оценку. Контейнеры занимают порты 6333 и 8080 — либо остановите их
(`docker compose down`), либо запускайте свои копии на других портах. Хранилище
Qdrant (`data/qdrant/storage`) у обоих способов общее; VLM — контейнер `vllm`
(`docker compose up -d vllm`, порт 127.0.0.1:8100).

### Сетап

Python 3.12, CUDA-GPU (проверено на RTX 3090; процессу отводится не более 14 ГБ VRAM).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r services/search-api/requirements.txt \
                      -r services/embedder/requirements.txt \
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

torch закреплён на 2.8.0 (`libs/wine-embeddings/requirements.txt`): свежий
torchvision поднимает torch до версии с CUDA 13 и ломает стек.

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

# 4. Модели: SigLIP (embedder, :8091) и VLM (контейнер vllm, :8100)
PYTHONPATH=libs/wine-embeddings/src:libs/wine-logging/src:services/embedder/src .venv/bin/python -m embedder
docker compose up -d vllm

# 5. api (читает .env; адреса моделей — WINE_EMBEDDER_URL, WINE_VLLM_URL)
WINE_VLLM_MEDIA_DIR=/app/data/catalog PYTHONPATH=libs/wine-logging/src:services/search-api/src \
  .venv/bin/python -m search_api
```

Страницу сканера без Docker отдаёт любой статический сервер из `services/web/app`,
но API должен быть на том же адресе — проще смотреть её через Docker-шлюз.
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
python -m http.server 8765 --directory services/web/app
# http://localhost:8765/?demo=match&autorun=1  и  ?demo=not_found&autorun=1
```

Проверка:

```bash
curl --noproxy '*' http://127.0.0.1:8080/health                                          # api и его зависимости
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
`services/search-api/src/search_api/site_media.json`; пересобрать его после пополнения каталога —
`python tools/site-media/fetch_site_media.py` (обходит ~110 страниц вин с паузой).

**Цифровой сомелье** (`search_api/sommelier.py`) — правила по полям каталога,
без внешних данных и моделей: стиль и тело вина (по категории и крепости),
температура подачи и как её добиться, бокал, аэрация, конкретные блюда к
каждой группе из карточки с учётом стиля вина, похожие вина с объяснением
(«тот же сорт: Верментино», «тоже Крым»; не больше двух от одного производителя).

## Личный кабинет

Сканировать можно и без аккаунта. Вошедший пользователь получает историю сканов
(с превью своего фото), лайки и дизлайки отсканированных вин, аватар (готовый или
своё фото), смену имени и достижения. Сканы, сделанные до входа, переносятся в
историю при входе. Вход — по почте или телефону и паролю (подтверждения кодом нет:
SMS и письма требуют внешнего шлюза).

Достижения добавляет администратор: «Профиль → Управление достижениями»
(`/app/#admin`). Условие собирается из готовых правил — распознанных сканов,
разных вин, регионов, вин из региона, сортов, виноделен, вин категории, оценок —
и порога. Пять стартовых достижений создаются при первом запуске.

Вход администратора — `WINE_ADMIN_LOGIN` / `WINE_ADMIN_PASSWORD` из `.env`; если
пароль не задан, он сгенерирован при первом старте:

```bash
docker compose logs users | grep -i пароль
```

Ручки кабинета перечислены в `ARCHITECTURE.md`, «Контракты API».
`http://localhost:8080/docs` — документация API распознавания; у сервиса
пользователей она своя, внутри сети compose (`users:8000/docs`).

Тесты сервисов (SQLite и Qdrant в памяти, без Postgres, сайта и моделей):

```bash
.venv/bin/pip install -r services/user-service/requirements.txt -r services/catalog-service/requirements.txt pytest
PYTHONPATH=libs/wine-logging/src:services/user-service/src .venv/bin/python -m pytest services/user-service/tests -q
PYTHONPATH=libs/wine-logging/src:services/analytics-service/src .venv/bin/python -m pytest services/analytics-service/tests -q
PYTHONPATH=libs/wine-logging/src:services/catalog-scraper/src:services/indexer/src:services/catalog-service/src \
  .venv/bin/python -m pytest services/catalog-service/tests -q
PYTHONPATH=services/log-collector/src .venv/bin/python -m pytest services/log-collector/tests -q
```

Если сервис пользователей недоступен, кабинета нет: сканер работает как раньше,
кнопка профиля не показывается.

**Поделиться** — у карточки вина, полученного достижения (и в поздравлении) и на
винной карте: картинка 1080×1350 с QR-кодом на страницу вина на vino-svoe.ru,
рисуется в браузере. Telegram и ВКонтакте (уходит ссылка на vino-svoe.ru с
текстом), «Сохранить» — картинку можно прикрепить к сообщению или сторис.
Если браузер умеет делиться файлом (только на https или localhost), сразу открывается
системное меню «Поделиться» с картинкой и текстом; на http — кнопки выше.
Текст сообщения админ меняет в «Профиль → Управление достижениями → Поделиться»:
шаблон с масками `{название}`, `{сорт}`, `{сахар}`, `{цвет}`, `{бренд}`, `{регион}`,
`{ссылка}` и предпросмотром.

**Аналитика для РСХБ** — «Профиль → Управление достижениями → Аналитика»
(только админ): сканы и доля «нет в каталоге», посетители и вернувшиеся,
конверсия в аккаунт, воронка регистрации, топ вин, регионов и виноделен, шары,
винная карта, сети провайдеров. На стенде все запросы с одного адреса — для показа
есть «Сгенерировать демо-данные» (помечены, удаляются одной кнопкой).

**Винная карта России** (`/app/#map`, вход — с главной сканера, из меню и профиля):
интерактивная карта всех субъектов РФ (колесо или щипок — масштаб, перетаскивание —
перемещение); субъекты винных регионов выделены, открытые пользователем закрашены,
скан вина из нового региона открывает его с поздравлением. Вино встаёт на карту,
если его `region` совпал с названием региона карты; иначе регион ждёт админа:
«Профиль → Управление достижениями → Регионы карты» — отнести к существующему
региону или создать новый, отметив субъекты на карте. Контуры пересобираются так
(~7 минут):

```bash
curl -L -o /tmp/admin1.geojson \
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson
python3 tools/region-map/build_map.py /tmp/admin1.geojson
```

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

# Другая модель на месте VLM на тех же топ-5 (сравнение моделей, docs/baseline-results.md)
PYTHONPATH=tools/eval-runner/src:services/search-api/src:libs/wine-logging/src:libs/wine-embeddings/src \
  .venv/bin/python -m eval_runner.model_bench vllm models/Qwen3-VL-4B-Instruct-FP8 \
  --out data/reports/bench-vllm-qwen3-vl-4b-fp8/results.jsonl

# Сколько сканов в секунду выдерживает стек (1, 2, 4, 8 пользователей одновременно)
NO_PROXY=127.0.0.1 PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.load_test --label after
```

## Переменные окружения

docker compose подставляет `.env` из корня во все сервисы; при нативном запуске
сервисы читают его сами. Переменные, уже заданные в окружении, имеют приоритет.
Шаблон — [`.env.example`](.env.example).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `WINE_MODEL_ID` | `google/siglip2-so400m-patch16-512` | модель-энкодер; при наличии одноимённой папки в `models/` веса берутся с диска. После смены — переиндексация |
| `WINE_MODELS_DIR` | `./models` | куда складывать веса |
| `WINE_DEVICE` / `WINE_DTYPE` | `cuda` / `float16` | устройство и точность энкодера |
| `WINE_VRAM_LIMIT_GB` | `14` | жёсткий потолок видеопамяти процесса с SigLIP (индексатор); у embedder в compose — `WINE_EMBEDDER_VRAM_LIMIT_GB` (`2.5`) |
| `WINE_BATCH_SIZE` | `32` | батч при индексации |
| `WINE_QDRANT_URL` | `http://localhost:6333` | адрес Qdrant |
| `WINE_COLLECTION` | `wines` | имя коллекции |
| `WINE_QDRANT_TIMEOUT_S` | `60` | таймаут запросов API к Qdrant (api читает все карточки в память) |
| `WINE_EMBEDDER_URL` | `http://127.0.0.1:8091` | адрес embedder (в compose — `http://embedder:8091`) |
| `WINE_CONNECT_TIMEOUT_S` / `WINE_EMBEDDER_TIMEOUT_S` | `2` / `15` | таймауты к моделям: упавший сервис — быстрый отказ, а не ожидание |
| `WINE_UNAVAILABLE_COOLDOWN_S` | `10` | после сбоя модели не вызывать её столько секунд — запросы сразу получают отказ |
| `WINE_CATALOG_REFRESH_S` | `60` | как часто api сверяет каталог в памяти с Qdrant (новые вина без перезапуска) |
| `WINE_CATALOG_DIR` | `./data/catalog` | каталог: индексатор берёт оттуда фото, VLM — эталонные фото кандидатов |
| `WINE_API_HOST` / `WINE_API_PORT` | `127.0.0.1` / `8080` | адрес сервиса (8080 — порт из скрипта оценки); `0.0.0.0` — доступ с телефона |
| `WINE_TOP_K` | `10` | сколько вин отдают `/v1/search` и `/v1/scan` |
| `WINE_VLM_ENABLED` | `1` | VLM (Qwen3-VL-4B-FP8 в vLLM): выбор вина и проверка «нет в каталоге»; `0` — только картинка |
| `WINE_VLLM_URL` | `http://127.0.0.1:8100` | адрес vLLM (в compose — `http://vllm:8000`) |
| `WINE_VLLM_MEDIA_DIR` | — | каталог так, как его видит vLLM (в compose — `/app/data/catalog`): эталоны уходят путями, а не base64 |
| `WINE_VLLM_SERVED_MODEL` / `WINE_VLLM_TIMEOUT_S` | `vlm` / `30` | имя модели в vLLM, таймаут запроса |
| `WINE_VLLM_MODEL` / `WINE_VLLM_GPU_UTIL` / `WINE_VLLM_PORT` | `Qwen3-VL-4B-Instruct-FP8` / `0.5` / `8100` | compose-сервис `vllm`: папка весов в `models/`, доля памяти всей карты, порт на localhost |
| `WINE_VLM_TOP_K` | `5` | из скольких кандидатов выбирает VLM (1–9, метки — цифры); из 10 — медленнее и на 2 фото хуже |
| `WINE_VLM_CANDIDATE_IMAGES` | `1` | показывать VLM эталонные фото кандидатов, а не только текст карточек |
| `WINE_VLM_QUERY_MAX_PIXELS` | `524288` | площадь, до которой уменьшается фото пользователя для VLM |
| `WINE_VLM_CANDIDATE_MAX_PIXELS` | `131072` | площадь эталонного фото кандидата (и имя папки кэша `vlm-refs-*`) |
| `WINE_ACCEPT_MIN_VERIFY_PROB` | `0.3` | с VLM: проверка топ-1 ниже — «нет в каталоге» |
| `WINE_ACCEPT_MAX_VLM_NONE_PROB` | `0.5` | с VLM: вероятность «ни одна не подходит» выше — «нет в каталоге» |
| `WINE_ACCEPT_MIN_VLM_PROB` | `0.8` | с VLM: вероятность выбора топ-1 ниже — «уточните, какое это вино» |
| `WINE_PREDICT_EMPTY_IF_ABSENT` | `0` | `0` — `/v1/eval/predict` и `top1` всегда отдают вино, выбранное VLM (все фото проверки есть в каталоге); `1` — вина нет в каталоге → `{"slug": null}` и `top1: null`. Веб-сканер пишет «нет в каталоге» при любом значении |
| `WINE_ACCEPT_MIN_IMAGE_SCORE` | `0.76` | без VLM: балл картинки ниже — «нет в каталоге» |
| `WINE_ACCEPT_MIN_MARGIN` | `0.02` | без VLM: минимальный отрыв топ-1 от топ-2 |
| `WINE_FEEDBACK_DIR` | `./data/feedback` | куда писать отклики пользователей |
| `WINE_SITE_IMAGE_API` | ресайз-API vino-svoe.ru | фото бутылки, если нет локального PNG каталога |
| `WINE_ADMIN_LOGIN` / `WINE_ADMIN_PASSWORD` | `admin@vino-svoe.local` / — | администратор достижений; пароль из окружения применяется при каждом старте, без него генерируется один раз и пишется в лог |
| `WINE_PG_USER` / `WINE_PG_PASSWORD` / `WINE_PG_DB` | `wines` / `wines` / `wines` | PostgreSQL в compose |
| `WINE_USERS_DB_URL` | задаёт compose | строка подключения сервиса пользователей (SQLAlchemy) |
| `WINE_USERS_MEDIA_DIR` | `./data/users` | аватары, превью сканов, значки достижений |
| `WINE_USERS_SESSION_DAYS` | `30` | срок сессии |
| `WINE_USERS_COOKIE_SECURE` | `0` | `1` — cookie сессии только по https |
| `WINE_API_DIRECT_PORT` / `WINE_PG_PORT` | `8090` / `5433` | порты на localhost для отладки: API в обход шлюза, PostgreSQL |
| `WINE_CATALOG_SYNC_AUTO` / `WINE_CATALOG_SYNC_TIMES` / `WINE_CATALOG_SYNC_PER` | `1` / `1` / `day` | расписание синхронизации каталога по умолчанию — «N раз в minute / hour / day / week / month» (не чаще раза в минуту); дальше его меняет админ в «Каталог» |
| `WINE_SYNC_PROXY` | — | прокси для синхронизации каталога (сервис catalog) |
| `WINE_EGRESS_LISTEN` / `WINE_EGRESS_TARGET` | `172.17.0.1:7115` / `127.0.0.1:3128` | ретранслятор к прокси хоста (профиль `proxy`) |
| `WINE_LOG_LEVEL` | `INFO` | уровень логов Python-сервисов (`DEBUG` — подробнее) |
| `WINE_LOG_REQUESTS` | `0` | `1` — писать в лог сервиса каждый HTTP-запрос, а не только ошибки и медленные |
| `WINE_LOGS_KEEP_DAYS` | `30` | сколько дней хранить `logs/<дата>/` |
| `SCRAPER_*` | см. `services/catalog-scraper/README.md` | параметры сбора каталога (и синхронизации) |

OCR (EasyOCR) из пайплайна удалён 28.09.2026: с VLM он не давал прироста
точности, а стоил 0,35 с и 2 ГБ VRAM.

## Ограничения

- **Точность измерена на 53 уверенно размеченных фото** — выборка маленькая,
  1 фото ≈ 2 п.п., разница в 1–2 фото между настройками — шум. Ground truth
  организатор не выдаёт, разметка ручная.
- **«Вина нет в каталоге» видит пользователь, но не метрика.** Веб-сканер
  (`/v1/scan`) показывает «Данное вино отсутствует в каталоге» и аналоги для
  30 из 33 вин вне каталога; цена — 5 из 62 вин из каталога тоже получают
  отказ. `/v1/eval/predict` по умолчанию отдаёт выбор VLM всегда: по условиям
  проверки все фото есть в каталоге, и отказ только терял бы верные ответы.
  С `WINE_PREDICT_EMPTY_IF_ABSENT=1` он отвечает `{"slug": null}` (и `top1: null`)
  там же, где сканер пишет «нет в каталоге»: по разметке 100 фото, если для вин
  вне каталога верный ответ — null, это 87 из 99 верных против 62 из 99.
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
- **Без GPU** VLM не работает: vLLM требует видеокарту. На CPU — только картинка
  (`WINE_DEVICE=cpu` для embedder, `WINE_VLM_ENABLED=0`, `docker compose stop vllm`):
  top-1 77%, вектор ~1–7 с в зависимости от процессора.
- **Сборщику логов нужен сокет Docker** (`/var/run/docker.sock`, только чтение логов
  и событий) — это доступ к Docker хоста; на общих серверах его стоит заменить на
  драйвер логов Docker или Fluent Bit с тем же разбором строк.
- **Трафик идёт через прокси**, который рвёт длинные соединения: веса скачиваются
  скриптами с докачкой, а localhost исключается из проксирования в рантайме.
