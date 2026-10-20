#!/usr/bin/env bash
# Веса VLM для сервиса vllm: Qwen3-VL-4B-Instruct-FP8 (5,7 ГБ) → models/Qwen3-VL-4B-Instruct-FP8.
# Качаются тем же curl с докачкой, что и энкодер. Точность та же, что у bf16-версии
# (96,2% на размеченных фото), памяти — меньше (docs/baseline-results.md, «vLLM»).
set -euo pipefail
exec "$(dirname "${BASH_SOURCE[0]}")/fetch-model.sh" Qwen/Qwen3-VL-4B-Instruct-FP8 \
  config.json generation_config.json chat_template.json \
  preprocessor_config.json video_preprocessor_config.json \
  tokenizer.json tokenizer_config.json vocab.json \
  model.safetensors.index.json \
  model-00001-of-00002.safetensors model-00002-of-00002.safetensors
