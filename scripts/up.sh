#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "${1:-}" == "--build" ]]; then
  docker compose up -d --build
else
  docker compose up -d
fi
cat <<'EOF'

Sitting:
  Operating Platform  http://127.0.0.1:8010
  Gateway admin       http://127.0.0.1:8000/admin
  Agent command       http://127.0.0.1:8001/dashboard
  Eval explorer       http://127.0.0.1:8002/explorer
  Twin                http://127.0.0.1:8003/
  GraphRAG search     http://127.0.0.1:8100/search

Optional GraphRAG reindex: docker compose exec graphrag python -m graphrag.cli reindex
EOF
