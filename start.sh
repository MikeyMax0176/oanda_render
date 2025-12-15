#!/usr/bin/env bash
set -euo pipefail

# Activate Render venv if present
[ -d "${VENV_ROOT:-./.venv}" ] && source "${VENV_ROOT:-./.venv}/bin/activate" || true

# Logs / state dir
mkdir -p runtime

echo "[boot] starting GDELT news-sentiment worker in background..."
# unbuffered stdout; write PID and log to runtime/
python -u news_sentiment.py >> runtime/news.log 2>&1 & echo $! > runtime/news.pid

# small sanity ping
sleep 1
ps -ef | grep -E 'news_sentiment.py' | grep -v grep || echo "[boot] WARN: worker not running"

echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
