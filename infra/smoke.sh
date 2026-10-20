#!/usr/bin/env bash
# Смоук-тесты поднятого стека: контракт организатора, сканер, веб, аккаунт,
# синхронизация каталога, логи, скорость.
#
#   ./infra/smoke.sh                           # сервис на http://127.0.0.1:8080
#   ./infra/smoke.sh --queries ./eval/queries  # + каждое фото папки через /v1/eval/predict
#   ./infra/smoke.sh --url http://host:8080 --samples 5
#   ./infra/smoke.sh --wait 0                  # не ждать загрузки моделей
#
# Главная проверка — фото из каталога (data/catalog, его собирает парсер) должно
# вернуть свой же slug. Нужно: bash, curl, jq. Код выхода 0 — всё прошло.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

URL="http://127.0.0.1:8080"
QDRANT="http://127.0.0.1:6333"
SAMPLES=3
QUERIES=""
SLA_MS=3000
WAIT_S=900  # embedder и vLLM грузят модели минуты — сразу после `up -d` они ещё «запускаются»

while [ "$#" -gt 0 ]; do
  case "$1" in
    --url) URL="${2%/}"; shift 2 ;;
    --qdrant) QDRANT="${2%/}"; shift 2 ;;
    --samples) SAMPLES="$2"; shift 2 ;;
    --queries) QUERIES="$2"; shift 2 ;;
    --wait) WAIT_S="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный аргумент: $1 (см. --help)" >&2; exit 2 ;;
  esac
done

for c in curl jq; do
  command -v "$c" >/dev/null || { echo "Не найден $c" >&2; exit 2; }
done

CATALOG=data/catalog/catalog.jsonl
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
BODY="$TMP/body"
passed=0
failed=0

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; passed=$((passed + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; failed=$((failed + 1)); }
skip() { printf '  --    %s\n' "$*"; }
section() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# Запрос → тело в $BODY, в stdout «код время_мс». Прокси мимо: сервис локальный
req() {
  local meta
  meta=$(curl -s --noproxy '*' --max-time 30 -o "$BODY" -w '%{http_code} %{time_total}' "$@") || meta="000 0"
  awk '{ printf "%s %.0f", $1, $2 * 1000 }' <<<"$meta"
}

# Фото → slug через контракт организатора; проверяет код, форму ответа и SLA
predict_ok() {  # $1 — файл, $2 — подпись; печатает slug (или null) в $TMP/slug
  local code ms
  read -r code ms < <(req -X POST -F "image=@$1" "$URL/v1/eval/predict")
  if [ "$code" != 200 ]; then bad "$2: HTTP $code"; return 1; fi
  if ! jq -e 'type == "object" and keys == ["slug"] and (.slug == null or (.slug | type == "string" and length > 0))' \
      "$BODY" >/dev/null 2>&1; then
    bad "$2: ответ не {\"slug\": строка|null}: $(head -c 200 "$BODY")"; return 1
  fi
  jq -r '.slug // "null"' "$BODY" > "$TMP/slug"
  if [ "$ms" -gt "$SLA_MS" ]; then bad "$2: $ms мс — дольше SLA $SLA_MS мс"; return 1; fi
  echo "$ms" >> "$TMP/latencies"
  return 0
}

# Ждём, пока api увидит все зависимости: иначе первые проверки упадут
# только потому, что модели ещё в видеопамять не легли
waited=0
while [ "$waited" -lt "$WAIT_S" ]; do
  read -r code ms < <(req "$URL/health")
  if [ "$code" = 200 ] && jq -e '.components' "$BODY" >/dev/null 2>&1; then
    down=$(jq -r '[.components | to_entries[] | select(.value.ok | not) | .key] | join(", ")' "$BODY")
    [ -z "$down" ] && break
    printf '\r  ...   жду: %s (%d с из %d)   ' "$down" "$waited" "$WAIT_S"
  else
    printf '\r  ...   жду api (%d с из %d)   ' "$waited" "$WAIT_S"
  fi
  sleep 10; waited=$((waited + 10))
done
[ "$waited" -gt 0 ] && printf '\n'

section "Сервисы $URL"
read -r code ms < <(req "$URL/gateway-health")
[ "$code" = 200 ] && ok "шлюз отвечает" || skip "/gateway-health: HTTP $code — запуск без шлюза?"
read -r code ms < <(req "$URL/health")
if [ "$code" = 200 ] && jq -e '.components' "$BODY" >/dev/null 2>&1; then
  # api жив; каждая зависимость — отдельной строкой: что именно не так, видно сразу
  while IFS=$'\t' read -r name okv detail; do
    [ "$okv" = true ] && ok "$name: $detail" || bad "$name: $detail (logs/$(date +%F)/all.log)"
  done < <(jq -r '.components | to_entries[] | [.key, (.value.ok | tostring), .value.detail] | @tsv' "$BODY")
  points=$(jq -r '.components.qdrant.detail' "$BODY" | grep -o '^[0-9]*')
  points=${points:-0}
else
  bad "/health: HTTP $code — api не поднят (docker compose ps, logs/$(date +%F)/services/api.log)"
  printf '\nДальше проверять нечего.\n'; exit 1
fi

if [ "$points" = 0 ]; then
  bad "индекс пуст — каталог не собран: см. data/catalog/failures.jsonl, затем ./infra/init.sh"
elif [ -f "$CATALOG" ]; then
  wines=$(grep -c . "$CATALOG")
  if [ "$points" = "$wines" ]; then ok "индекс полный: $points = $wines вин в $CATALOG"
  else bad "в индексе $points точек, в каталоге $wines вин — переиндексировать: docker compose run --rm indexer --recreate"; fi
else
  skip "нет $CATALOG — сверка индекса с каталогом пропущена"
fi

read -r code ms < <(req "$QDRANT/collections/wines")
if [ "$code" = 200 ]; then ok "Qdrant: $(jq -r '.result.status' "$BODY"), $(jq -r '.result.points_count' "$BODY") точек"
else skip "Qdrant на $QDRANT не отвечает напрямую (HTTP $code) — сервис видит его, этого достаточно"; fi

section "Контракт POST /v1/eval/predict"
if [ -f "$CATALOG" ] && [ "$SAMPLES" -gt 0 ]; then
  # Равномерно по каталогу, только вина с локальным фото
  jq -r 'select(.image_path) | [.slug, .image_path] | @tsv' "$CATALOG" > "$TMP/wines.tsv"
  total=$(wc -l < "$TMP/wines.tsv")
  [ "$total" -gt 0 ] || skip "в $CATALOG нет вин с фото — проверка на фото каталога пропущена"
  for i in $([ "$total" -gt 0 ] && seq 1 "$SAMPLES"); do
    line=$(( (total * i) / (SAMPLES + 1) ))
    [ "$line" -ge 1 ] || line=1
    IFS=$'\t' read -r slug path < <(sed -n "${line}p" "$TMP/wines.tsv")
    file="data/catalog/$path"
    [ -f "$file" ] || { skip "нет файла $file"; continue; }
    predict_ok "$file" "фото каталога $slug" || continue
    got=$(cat "$TMP/slug")
    if [ "$got" = "$slug" ]; then
      ok "фото каталога → свой slug ($slug, $(tail -1 "$TMP/latencies") мс)"
    else
      # 25 групп каталожных фото байт-идентичны — чужой slug с тем же фото не ошибка
      other=$(jq -r --arg s "$got" 'select(.slug == $s) | .image_path // empty' "$CATALOG" | head -1)
      if [ -n "$other" ] && cmp -s "$file" "data/catalog/$other"; then
        ok "фото каталога $slug → $got (у этих вин одно и то же фото)"
      else
        bad "фото каталога $slug → $got"
      fi
    fi
  done
else
  skip "нет $CATALOG — проверка на фото каталога пропущена"
fi

printf 'это не картинка\n' > "$TMP/not-image.jpg"
read -r code ms < <(req -X POST -F "image=@$TMP/not-image.jpg" "$URL/v1/eval/predict")
case "$code" in
  4??) ok "не картинка → HTTP $code, сервис не падает" ;;
  *)   bad "не картинка → HTTP $code (ожидался 4xx)" ;;
esac

if [ -n "$QUERIES" ]; then
  section "Фото из $QUERIES"
  n=0
  while IFS= read -r -d '' file; do
    predict_ok "$file" "$(basename "$file")" || continue
    ok "$(basename "$file") → $(cat "$TMP/slug") ($(tail -1 "$TMP/latencies") мс)"
    n=$((n + 1))
  done < <(find "$QUERIES" -maxdepth 1 -type f ! -name '._*' \
             \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) -print0 | sort -z)
  [ "$n" -gt 0 ] || bad "в $QUERIES нет фото (jpg/png/webp)"
fi

section "Сканер и веб"
sample=$(jq -r 'select(.image_path) | .image_path' "$CATALOG" 2>/dev/null | head -1)
if [ -n "$sample" ] && [ -f "data/catalog/$sample" ]; then
  read -r code ms < <(req -X POST -F "image=@data/catalog/$sample" "$URL/v1/search")
  if [ "$code" = 200 ] && jq -e '(.results | length) > 0 and (.status == "match" or .status == "not_found")' "$BODY" >/dev/null 2>&1; then
    ok "/v1/search: $(jq -r '"\(.status), топ-1 \(.top1 // "null"), вариантов \(.results | length)"' "$BODY")"
  else
    bad "/v1/search: HTTP $code $(head -c 200 "$BODY")"
  fi

  read -r code ms < <(req -X POST -F "image=@data/catalog/$sample" "$URL/v1/scan")
  if [ "$code" = 200 ] && jq -e '(.status == "match" and .wine.slug != null and (.wine.sommelier != null))
                                 or (.status == "not_found" and (.alternatives | length) > 0)' "$BODY" >/dev/null 2>&1; then
    ok "/v1/scan: $(jq -r '"\(.status) — \(.message)"' "$BODY"), советы сомелье есть"
    scan_slug=$(jq -r '.wine.slug // .alternatives[0].slug' "$BODY")
  else
    bad "/v1/scan: HTTP $code $(head -c 200 "$BODY")"
  fi
else
  skip "нет фото каталога — /v1/search и /v1/scan пропущены"
fi

if [ -n "${scan_slug:-}" ]; then
  read -r code ms < <(req "$URL/v1/wines/$scan_slug")
  [ "$code" = 200 ] && ok "/v1/wines/$scan_slug: карточка" || bad "/v1/wines/$scan_slug: HTTP $code"
fi

read -r code ms < <(req "$URL/v1/pairing/dishes")
if [ "$code" = 200 ] && jq -e 'length > 0' "$BODY" >/dev/null 2>&1; then
  ok "/v1/pairing/dishes: $(jq length "$BODY") блюд"
else
  bad "/v1/pairing/dishes: HTTP $code"
fi

read -r code ms < <(req -G --data-urlencode 'q=каберне' "$URL/v1/catalog/search")
if [ "$code" = 200 ] && jq -e 'type == "array"' "$BODY" >/dev/null 2>&1; then
  ok "/v1/catalog/search?q=каберне: $(jq length "$BODY") вин"
else
  bad "/v1/catalog/search: HTTP $code"
fi

read -r code ms < <(req "$URL/v1/regions")
if [ "$code" = 200 ] && jq -e 'length > 0 and all(.[]; .wines > 0)' "$BODY" >/dev/null 2>&1; then
  ok "/v1/regions: $(jq -r 'map("\(.name) \(.wines)") | join(", ")' "$BODY")"
  region=$(jq -r '.[0].name' "$BODY")
  read -r code ms < <(req -G --data-urlencode "limit=3" "$URL/v1/regions/$(jq -rn --arg r "$region" '$r|@uri')/wines")
  [ "$code" = 200 ] && jq -e 'length > 0' "$BODY" >/dev/null 2>&1 \
    && ok "/v1/regions/…/wines: что попробовать из региона «$region»" || bad "/v1/regions/…/wines: HTTP $code"
else
  bad "/v1/regions: HTTP $code — винная карта без каталога регионов"
fi
read -r code ms < <(req "$URL/app/russia-map.json")
[ "$code" = 200 ] && jq -e '(.subjects | length) >= 85' "$BODY" >/dev/null 2>&1 \
  && ok "/app/russia-map.json: контуры $(jq '.subjects | length' "$BODY") субъектов" || bad "/app/russia-map.json: HTTP $code"
read -r code ms < <(req "$URL/v1/map/zones")
if [ "$code" = 200 ] && jq -e '(.zones | length) > 0' "$BODY" >/dev/null 2>&1; then
  ok "/v1/map/zones: $(jq '.zones | length' "$BODY") винных регионов на карте, сопоставлений админа: $(jq '.aliases | length' "$BODY")"
else
  skip "/v1/map/zones: HTTP $code — регионы карты ведёт сервис пользователей (запуск без шлюза?)"
fi

read -r code ms < <(req "$URL/app/share.js")
code_share=$code
read -r code ms < <(req "$URL/app/vendor/qrcode.js")
[ "$code_share" = 200 ] && [ "$code" = 200 ] && ok "«Поделиться»: share.js и QR-библиотека отдаются" \
  || bad "«Поделиться»: share.js HTTP $code_share, vendor/qrcode.js HTTP $code"
read -r code ms < <(req "$URL/v1/settings/share")
if [ "$code" = 200 ] && jq -e '.wine_template | length > 0' "$BODY" >/dev/null 2>&1; then
  ok "текст «Поделиться вином»: $(jq -r '.wine_template' "$BODY" | head -c 80)"
else
  skip "/v1/settings/share: HTTP $code — текст по умолчанию из share.js (запуск без шлюза?)"
fi

section "Аналитика"
read -r code ms < <(req -H 'Content-Type: application/json' \
  -d '{"type":"page_view","anon_id":"smoke-test-0001","props":{"source":"smoke"}}' "$URL/v1/events")
case "$code" in
  204) ok "/v1/events: событие принято" ;;
  404|502) skip "/v1/events: HTTP $code — сервис аналитики не подключён (запуск без шлюза?)" ;;
  *) bad "/v1/events: HTTP $code $(head -c 200 "$BODY")" ;;
esac
read -r code ms < <(req "$URL/v1/admin/analytics")
case "$code" in
  401|403) ok "/v1/admin/analytics без входа администратора → HTTP $code" ;;
  404|502) skip "/v1/admin/analytics: HTTP $code — сервис аналитики не подключён" ;;
  *) bad "/v1/admin/analytics без входа → HTTP $code (ожидался 401/403)" ;;
esac

read -r code ms < <(req "$URL/app/")
if [ "$code" = 200 ] && grep -qi '<html' "$BODY"; then ok "/app/: страница сканера отдаётся"
else bad "/app/: HTTP $code"; fi

section "Каталог (синхронизация с сайтом)"
read -r code ms < <(req "$URL/v1/admin/catalog/sync")
case "$code" in
  401|403) ok "/v1/admin/catalog/sync без входа администратора → HTTP $code" ;;
  404|502|503) skip "/v1/admin/catalog/sync: HTTP $code — сервис каталога не подключён" ;;
  *) bad "/v1/admin/catalog/sync без входа → HTTP $code (ожидался 401/403)" ;;
esac
read -r code ms < <(req "$URL/internal/catalog/reload")
[ "$code" = 404 ] && ok "служебные /internal/ снаружи закрыты" || bad "/internal/ через шлюз → HTTP $code (ожидался 404)"

section "Аккаунт (сервис пользователей)"
JAR="$TMP/cookies"
read -r code ms < <(req "$URL/v1/avatars")
if [ "$code" != 200 ]; then
  # Без шлюза (API запущен напрямую, без Docker) аккаунта нет — сканер работает и так
  skip "/v1/avatars: HTTP $code — сервис пользователей не подключён (запуск без шлюза?)"
else
  ok "/v1/avatars: $(jq length "$BODY") готовых аватаров"
  login="smoke-$(date +%s)-$$@example.com"
  read -r code ms < <(req -c "$JAR" -H 'Content-Type: application/json' \
    -d "{\"login\":\"$login\",\"password\":\"smoke-pass-123\",\"name\":\"Смоук\"}" "$URL/v1/auth/register")
  if [ "$code" = 201 ] && jq -e '.user.role == "user" and (.achievements | length) > 0' "$BODY" >/dev/null 2>&1; then
    ok "регистрация: $(jq -r '"\(.user.login), достижений в каталоге: \(.achievements | length)"' "$BODY")"
    if [ -n "${scan_slug:-}" ] && [ -n "${sample:-}" ]; then
      read -r code ms < <(req -b "$JAR" -F status=match -F "slug=$scan_slug" -F "photo=@data/catalog/$sample" "$URL/v1/me/scans")
      if [ "$code" = 201 ] && jq -e '.scan.wine.slug != null and .scan.photo_url != null' "$BODY" >/dev/null 2>&1; then
        ok "скан в истории: $(jq -r '"\(.scan.wine.title) (\(.scan.wine.region // "регион ?")), новых достижений: \(.new_achievements | length)"' "$BODY")"
      else
        bad "запись скана: HTTP $code $(head -c 200 "$BODY")"
      fi
      read -r code ms < <(req -b "$JAR" -X PUT -H 'Content-Type: application/json' -d '{"value":1}' "$URL/v1/me/ratings/$scan_slug")
      [ "$code" = 200 ] && ok "лайк вина из истории" || bad "лайк: HTTP $code $(head -c 200 "$BODY")"
      read -r code ms < <(req -b "$JAR" "$URL/v1/me/regions")
      if [ "$code" = 200 ] && jq -e '(.regions | length) == 1' "$BODY" >/dev/null 2>&1; then
        ok "винная карта: открыт регион $(jq -r '.regions[0].name' "$BODY")"
      else
        bad "/v1/me/regions: HTTP $code $(head -c 200 "$BODY")"
      fi
    fi
    # Вовлечение: вопрос дня, дневник, челленджи, открытки (ответ и запись удалятся вместе с аккаунтом)
    read -r code ms < <(req -b "$JAR" "$URL/v1/quiz/today")
    if [ "$code" = 200 ] && jq -e '(.options | length) >= 2' "$BODY" >/dev/null 2>&1; then
      ok "вопрос дня: $(jq -r '.text' "$BODY")"
      read -r code ms < <(req -b "$JAR" -H 'Content-Type: application/json' -d '{"choice":0}' "$URL/v1/quiz/today/answer")
      [ "$code" = 200 ] && jq -e '.saved and .streak >= 1' "$BODY" >/dev/null 2>&1 \
        && ok "ответ на вопрос дня: серия $(jq -r '.streak' "$BODY")" || bad "ответ на вопрос дня: HTTP $code $(head -c 200 "$BODY")"
    else
      bad "/v1/quiz/today: HTTP $code $(head -c 200 "$BODY")"
    fi
    if [ -n "${scan_slug:-}" ]; then
      read -r code ms < <(req -b "$JAR" -H 'Content-Type: application/json' \
        -d "{\"slug\":\"$scan_slug\",\"rating\":5,\"tags\":[\"Ягоды\"]}" "$URL/v1/me/diary")
      [ "$code" = 201 ] && ok "дневник дегустации: запись «$(jq -r '.entry.wine.title' "$BODY")»" \
                        || bad "дневник: HTTP $code $(head -c 200 "$BODY")"
    fi
    # «Мой вкус» (A9): варианты — api, профиль и «понравится ли» — users
    read -r code ms < <(req "$URL/v1/taste/options")
    [ "$code" = 200 ] && jq -e '(.grapes | length) > 0' "$BODY" >/dev/null 2>&1 \
      && ok "«Мой вкус»: сортов на выбор $(jq '.grapes | length' "$BODY")" || bad "/v1/taste/options: HTTP $code"
    read -r code ms < <(req -b "$JAR" -X PUT -H 'Content-Type: application/json' \
      -d '{"colors":["red"],"sugars":["dry"]}' "$URL/v1/me/taste")
    [ "$code" = 200 ] && jq -e '.ready' "$BODY" >/dev/null 2>&1 && ok "«Мой вкус»: профиль сохранён" \
      || bad "/v1/me/taste: HTTP $code $(head -c 200 "$BODY")"
    if [ -n "${scan_slug:-}" ]; then
      read -r code ms < <(req -b "$JAR" "$URL/v1/me/taste/match?slug=$scan_slug")
      [ "$code" = 200 ] && ok "«понравится ли»: $(jq -r '.state' "$BODY")" \
        || bad "/v1/me/taste/match: HTTP $code $(head -c 200 "$BODY")"
    fi
    read -r code ms < <(req -b "$JAR" "$URL/v1/challenges")
    [ "$code" = 200 ] && ok "челленджи: идёт $(jq length "$BODY")" || bad "/v1/challenges: HTTP $code"
    read -r code ms < <(req "$URL/v1/gifts/smoke-nope")
    [ "$code" = 404 ] && ok "открытки: неизвестная ссылка → 404" || bad "/v1/gifts: HTTP $code (маршрут шлюза?)"
    read -r code ms < <(req -b "$JAR" "$URL/v1/me")
    [ "$code" = 200 ] && ok "профиль: $(jq -r '"сканов \(.stats.scans), регионов \(.stats.regions), оценок \(.stats.ratings)"' "$BODY")" \
                      || bad "/v1/me: HTTP $code"
    # Тестовый аккаунт удаляем: смоук не должен оставлять следов в данных пользователей
    read -r code ms < <(req -b "$JAR" -X DELETE "$URL/v1/me")
    [ "$code" = 204 ] && ok "тестовый аккаунт удалён" || bad "удаление аккаунта: HTTP $code"
  else
    bad "регистрация: HTTP $code $(head -c 200 "$BODY") (docker compose logs users)"
  fi
fi

section "Логи"
today="logs/$(date +%F)"
if [ -f "$today/all.log" ]; then
  ok "$today: all.log ($(wc -l < "$today/all.log") строк) и по сервисам: $(ls "$today/services" 2>/dev/null | sed 's/\.log$//' | tr '\n' ' ')"
else
  skip "нет $today/all.log — сборщик логов не запущен (docker compose up -d logs) или стек на другой машине"
fi

if [ -s "$TMP/latencies" ]; then
  section "Скорость /v1/eval/predict"
  sort -n "$TMP/latencies" | awk -v sla="$SLA_MS" '
    { v[NR] = $1; s += $1 }
    END { printf "  %d запросов: среднее %.0f мс, max %d мс (SLA %d мс)\n", NR, s / NR, v[NR], sla }'
fi

printf '\nИтого: %d прошло, %d не прошло\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
