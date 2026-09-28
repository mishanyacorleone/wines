#!/usr/bin/env bash
# Первый запуск с нуля: модели → каталог с сайта → индекс в Qdrant → сервис → смоук-тесты.
#
#   ./infra/init.sh            # всё по порядку; повторный запуск ничего не тянет заново
#   ./infra/init.sh --reindex  # пересчитать эмбеддинги, даже если индекс уже есть
#
# Нужно на хосте: Docker с Compose v2, NVIDIA Container Toolkit, bash, curl, jq.
# Время первого запуска: веса ~13 ГБ, каталог 35–40 минут (вежливый темп
# к сайту), индекс ~4 минуты, старт сервиса ~1–2 минуты.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

REINDEX=0
for arg in "$@"; do
  case "$arg" in
    --reindex) REINDEX=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный аргумент: $arg (см. --help)" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { echo "Ошибка: $*" >&2; exit 1; }

command -v docker >/dev/null || fail "не найден docker"
command -v curl >/dev/null || fail "не найден curl"
command -v jq >/dev/null || fail "не найден jq (нужен смоук-тестам)"
docker compose version >/dev/null 2>&1 || fail "нужен Docker Compose v2 (docker compose)"
if ! docker info 2>/dev/null | grep -qi 'runtimes:.*nvidia'; then
  echo "Предупреждение: в Docker не найден NVIDIA runtime — без него модели не увидят GPU."
  echo "Установите NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/"
fi

# Папки данных создаём от имени пользователя, иначе Docker создаст их от root
mkdir -p data/catalog data/qdrant/storage data/qdrant/snapshots data/feedback
# .env необязателен (compose читает его, если он есть) — заводим из шаблона для тонкой настройки
[ -f .env ] || cp .env.example .env

step "1/6 Веса моделей → models/ (SigLIP 2 — 4,3 ГБ, Qwen3-VL — 8,9 ГБ)"
./infra/fetch-model.sh google/siglip2-so400m-patch16-512
./infra/fetch-vlm.sh

step "2/6 Сборка образов"
docker compose --profile init build

step "3/6 Каталог с vino-svoe.ru → data/catalog (докачивает только недостающее)"
docker compose run --rm scraper

step "4/6 Эмбеддинги SigLIP 2 → Qdrant"
docker compose up -d --wait qdrant
points=$(curl -s --noproxy '*' http://127.0.0.1:6333/collections/wines \
  | grep -o '"points_count":[0-9]*' | cut -d: -f2 || true)
if [ "$REINDEX" = 1 ] || [ -z "$points" ] || [ "$points" = 0 ]; then
  docker compose run --rm indexer --recreate
else
  echo "Индекс уже есть: $points вин. Пересчитать — ./infra/init.sh --reindex"
fi

step "5/6 Сервис (загрузка моделей и прогрев — до пары минут)"
docker compose up -d --wait --wait-timeout 600 api
port="${WINE_API_PORT:-8080}"

step "6/6 Смоук-тесты"
./infra/smoke.sh --url "http://127.0.0.1:$port" || fail "смоук-тесты не прошли — см. выше и docker compose logs api"
cat <<EOF

Готово.
  Сканер:            http://localhost:$port/app/   (с телефона — http://<IP машины>:$port/app/)
  Контракт оценки:   POST http://localhost:$port/v1/eval/predict  (поле image)
  Документация API:  http://localhost:$port/docs
Остановить: docker compose down    Запустить снова: docker compose up -d
EOF
