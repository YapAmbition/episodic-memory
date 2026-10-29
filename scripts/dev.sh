#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
if [[ "${1:-start}" == "setup" ]]; then
  "${PYTHON:-python3}" -m venv .venv
  .venv/bin/python -m pip install -r backend/requirements.txt
  npm ci --prefix frontend
  exit 0
fi
if [[ ! -x .venv/bin/python || ! -f frontend/node_modules/vite/bin/vite.js ]]; then
  echo '请先运行：./scripts/dev.sh setup' >&2
  exit 1
fi
exec .venv/bin/python scripts/dev.py "${1:-start}"
