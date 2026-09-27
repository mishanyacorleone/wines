#!/usr/bin/env bash
# Загрузка весов EasyOCR в models/easyocr через curl с резюмированием.
#
# Встроенный загрузчик EasyOCR тянет zip с GitHub одним запросом и на обрыве
# (прокси рвёт длинные соединения) начинает заново. Качаем сами, проверяем md5
# и распаковываем туда, где EasyOCR их найдёт (model_storage_directory).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/models/easyocr"
MAX_ATTEMPTS=100

# имя .pth | url архива | md5 распакованного .pth (из easyocr/config.py)
MODELS=(
  "craft_mlt_25k.pth|https://github.com/JaidedAI/EasyOCR/releases/download/pre-v1.1.6/craft_mlt_25k.zip|2f8227d2def4037cdb3b34389dcf9ec1"
  "cyrillic_g2.pth|https://github.com/JaidedAI/EasyOCR/releases/download/v1.6.1/cyrillic_g2.zip|19f85f43d9128a89ac21b8d6a06973fe"
)

mkdir -p "$DEST"

for spec in "${MODELS[@]}"; do
  IFS='|' read -r name url md5 <<< "$spec"
  target="$DEST/$name"

  if [[ -f "$target" ]] && [[ "$(md5sum "$target" | cut -d' ' -f1)" == "$md5" ]]; then
    echo "Уже есть: $name"
    continue
  fi

  archive="$DEST/${name%.pth}.zip"
  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    if curl -fL --retry 5 --retry-delay 2 -C - \
            --speed-limit 10240 --speed-time 30 \
            -o "$archive" "$url"; then
      break
    fi
    echo "  обрыв на $name, попытка $attempt — продолжаю с места остановки"
    sleep 2
  done

  unzip -o -q "$archive" -d "$DEST" && rm -f "$archive"

  if [[ "$(md5sum "$target" | cut -d' ' -f1)" != "$md5" ]]; then
    echo "md5 не сошёлся: $name" >&2
    exit 1
  fi
  echo "OK: $name"
done

echo "Готово: $DEST"
ls -lh "$DEST"
