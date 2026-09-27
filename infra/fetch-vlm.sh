#!/usr/bin/env bash
# Веса VLM-реранкера Qwen3-VL-4B-Instruct (8,9 ГБ) → models/Qwen3-VL-4B-Instruct.
# Нужны только при WINE_VLM_ENABLED=1. Качаются тем же curl с докачкой, что и энкодер.
set -euo pipefail
exec "$(dirname "${BASH_SOURCE[0]}")/fetch-model.sh" Qwen/Qwen3-VL-4B-Instruct \
  config.json generation_config.json chat_template.json \
  preprocessor_config.json video_preprocessor_config.json \
  tokenizer.json tokenizer_config.json vocab.json merges.txt \
  model.safetensors.index.json \
  model-00001-of-00002.safetensors model-00002-of-00002.safetensors
