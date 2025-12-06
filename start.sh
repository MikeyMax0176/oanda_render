#!/usr/bin/env bash
set -euo pipefail

# Activate Render's venv if present
[ -d "${VENV_ROOT:-./.venv}" ] && source "${VENV_ROOT:-./.venv}/bin/activate" || true

mkdir -p runtime

echo "[boot] starting news-sentiment worker in background..."
python -u src/news_sentiment.py >> runtime/news.log 2>&1 & echo $! > runtime/news.pid
echo "[boot] tail logs: tail -f runtime/news.log"

echo "[boot] starting streamlit dashboard..."
exec streamlit run src/dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
