#!/usr/bin/env bash
set -euo pipefail

# Activate the virtualenv if Render created one
[ -d "${VENV_ROOT:-./.venv}" ] && source "${VENV_ROOT:-./.venv}/bin/activate" || true

# (Optional) a place for local logs/cache used by the web app only
mkdir -p runtime

echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
