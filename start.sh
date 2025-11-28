#!/usr/bin/env bash
set -euo pipefail

echo "[boot] starting oanda bot in background..."
python bot.py &

echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py --server.address 0.0.0.0 --server.port "$PORT"
