#!/usr/bin/env bash
set -e
source "${VENV_ROOT:-/opt/render/project/src/.venv}/bin/activate"

echo "[boot] starting oanda bot in background..."
python bot.py &

echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py --server.address 0.0.0.0 --server.port "${PORT:-10000}"
