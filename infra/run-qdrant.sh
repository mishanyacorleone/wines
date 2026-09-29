#!/usr/bin/env bash
# Запасной запуск Qdrant без Docker — нативный бинарник той же версии (1.19.1),
# что образ в docker-compose.yml; хранилище то же (data/qdrant/storage), так что
# индекс переносится между способами без переиндексации. Основной способ —
# docker compose up -d.
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
