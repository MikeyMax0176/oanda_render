#!/usr/bin/env bash
set -euo pipefail

# --- Activate virtualenv (Render sets .venv) ---
if [ -d "${VENV_ROOT:-./.venv}" ]; then
  # shellcheck disable=SC1091
  source "${VENV_ROOT:-./.venv}/bin/activate"
fi

# --- Ensure runtime dir exists for logs/heartbeat ---
mkdir -p runtime

# --- Start the news/sentiment trading worker in the background ---
echo "[boot] starting news-sentiment worker in background..."
# Logs -> runtime/news.log, PID -> runtime/news.pid
# -u for unbuffered stdout so logs stream immediately
python -u news_sentiment.py >> runtime/news.log 2>&1 & echo $! > runtime/news.pid

# Optional: small note where to tail logs
echo "[boot] tail logs: tail -f runtime/news.log"

# --- Start the Streamlit dashboard (foreground) ---
echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
