#!/usr/bin/env bash
set -euo pipefail

# Activate the virtualenv Render creates
[ -d "${VENV_ROOT:-./.venv}" ] && source "${VENV_ROOT:-./.venv}/bin/activate" || true

# Make a place for logs/heartbeats
mkdir -p runtime

# Start the news/sentiment bot in the background
echo "[boot] starting news-sentiment worker in background..."
# Unbuffered stdout so logs stream immediately
python -u news_sentiment.py >> runtime/news.log 2>&1 & echo $! > runtime/news.pid

# Helpful hint
echo "[boot] tail logs: tail -f runtime/news.log"

# Start the Streamlit dashboard in the foreground (keeps container alive)
echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
