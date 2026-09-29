#!/usr/bin/env bash
# Смоук-тесты поднятого сервиса: контракт организатора, сканер, веб, скорость.
#
#   ./infra/smoke.sh                           # сервис на http://127.0.0.1:8080
#   ./infra/smoke.sh --queries ./eval/queries  # + каждое фото папки через /v1/eval/predict
#   ./infra/smoke.sh --url http://host:8080 --samples 5
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

while [ "$#" -gt 0 ]; do
  case "$1" in
    --url) URL="${2%/}"; shift 2 ;;
    --qdrant) QDRANT="${2%/}"; shift 2 ;;
    --samples) SAMPLES="$2"; shift 2 ;;
    --queries) QUERIES="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

section "Сервис $URL"
read -r code ms < <(req "$URL/health")
if [ "$code" = 200 ] && jq -e '.status == "ok"' "$BODY" >/dev/null 2>&1; then
  ok "/health: $(jq -r '"\(.indexed_points) вин в индексе, \(.device), VLM \(if .vlm_enabled then "вкл." else "выкл." end)"' "$BODY")"
  points=$(jq -r '.indexed_points' "$BODY")
else
  bad "/health: HTTP $code — сервис не поднят (docker compose ps, docker compose logs api)"
  printf '\nДальше проверять нечего.\n'; exit 1
fi

if [ -f "$CATALOG" ]; then
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
  for i in $(seq 1 "$SAMPLES"); do
    line=$(( (total * i) / (SAMPLES + 1) ))
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

read -r code ms < <(req "$URL/app/")
if [ "$code" = 200 ] && grep -qi '<html' "$BODY"; then ok "/app/: страница сканера отдаётся"
else bad "/app/: HTTP $code"; fi

if [ -s "$TMP/latencies" ]; then
  section "Скорость /v1/eval/predict"
  sort -n "$TMP/latencies" | awk -v sla="$SLA_MS" '
    { v[NR] = $1; s += $1 }
    END { printf "  %d запросов: среднее %.0f мс, max %d мс (SLA %d мс)\n", NR, s / NR, v[NR], sla }'
fi

printf '\nИтого: %d прошло, %d не прошло\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
