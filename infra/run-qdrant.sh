#!/usr/bin/env bash
# Локальный запуск Qdrant без Docker.
# Docker Hub из этой сети недоступен, поэтому используем нативный бинарник
# с того же GitHub-релиза. Поведение и REST API идентичны образу из
# docker-compose.yml, который остаётся основным способом развёртывания.
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$INFRA_DIR")"

export QDRANT__STORAGE__STORAGE_PATH="${QDRANT_STORAGE:-$REPO_ROOT/data/qdrant/storage}"
export QDRANT__STORAGE__SNAPSHOTS_PATH="$REPO_ROOT/data/qdrant/snapshots"
export QDRANT__SERVICE__HTTP_PORT=6333
export QDRANT__SERVICE__GRPC_PORT=6334
export QDRANT__TELEMETRY_DISABLED=true

mkdir -p "$QDRANT__STORAGE__STORAGE_PATH" "$QDRANT__STORAGE__SNAPSHOTS_PATH"
exec "$INFRA_DIR/bin/qdrant" "$@"
