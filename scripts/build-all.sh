#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOCAL_UI=0
NO_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --local-ui) LOCAL_UI=1 ;;
    --no-docker) NO_DOCKER=1 ;;
  esac
done

if [[ "$LOCAL_UI" = 1 || "$NO_DOCKER" = 1 ]]; then
  echo "Building Operating Platform web dist..."
  cd "$ROOT/Enterprise_AI_Platform/web"
  [[ -d node_modules ]] || npm ci
  npm run build
  cd "$ROOT"
fi

if [[ "$NO_DOCKER" = 0 ]]; then
  echo "Building Docker images..."
  docker compose build
  docker compose images
fi

echo "Build complete."
