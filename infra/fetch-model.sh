#!/usr/bin/env bash
# Загрузка весов модели через curl с резюмированием.
#
# Штатный загрузчик huggingface_hub в этой сети не работает: исходящий трафик
# идёт через прокси, который рвёт длинные соединения, а hf_hub при обрыве начинает
# файл заново, теряя весь прогресс. curl -C - дотягивает с места обрыва.
set -uo pipefail

#
# Использование: fetch-model.sh [repo] [файл ...]
# Без списка файлов тянутся три файла энкодера SigLIP.
MODEL="${1:-google/siglip2-so400m-patch16-512}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO_ROOT/models/$(basename "$MODEL")"
if [ $# -gt 1 ]; then
  FILES=("${@:2}")
else
  FILES=(config.json preprocessor_config.json model.safetensors)
fi
MAX_ATTEMPTS=100

mkdir -p "$DEST"

for file in "${FILES[@]}"; do
  url="https://huggingface.co/$MODEL/resolve/main/$file"
  target="$DEST/$file"

  # Уже скачанный целиком файл пропускаем: повторный запуск init.sh не должен
  # ничего тянуть заново. Размер — из заголовков (последний Content-Length
  # после редиректов на CDN)
  if [ -s "$target" ]; then
    remote=$(curl -sIL --retry 3 "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:"{n=$2} END{print n}')
    if [ -n "$remote" ] && [ "$(stat -c %s "$target")" = "$remote" ]; then
      echo "Уже есть: $file"
      continue
    fi
  fi

  for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    # --speed-time/--speed-limit рвут зависшее соединение сами,
    # иначе curl будет вечно ждать данные от мёртвого канала
    if curl -fL --retry 5 --retry-delay 2 -C - \
            --speed-limit 10240 --speed-time 30 \
            -o "$target" "$url"; then
      echo "OK: $file"
      break
    fi
    echo "  обрыв на $file, попытка $attempt — продолжаю с места остановки"
    sleep 2
  done
done

echo "Готово: $DEST"
ls -lh "$DEST"
