#!/usr/bin/env bash
set -euo pipefail

# Activate Render venv if present
[ -d "${VENV_ROOT:-./.venv}" ] && source "${VENV_ROOT:-./.venv}/bin/activate" || true

# Logs / state dir
mkdir -p runtime

echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
