#!/usr/bin/env bash
set -euo pipefail

# --- Activate Render's virtualenv if present ---
if [ -d "${VENV_ROOT:-./.venv}" ]; then
  # shellcheck disable=SC1091
  source "${VENV_ROOT:-./.venv}/bin/activate"
fi

# --- Prep runtime dir for logs & PID files ---
mkdir -p runtime

# --- Start news/sentiment worker (background) ---
echo "[boot] starting news-sentiment worker in background..."
python -u news_sentiment.py >> runtime/news.log 2>&1 & echo $! > runtime/news.pid

# --- Start heartbeat sidecar (background) ---
# If you added news_heartbeat.py as discussed
if [ -f "news_heartbeat.py" ]; then
  echo "[boot] starting heartbeat sidecar..."
  python -u news_heartbeat.py >> runtime/heartbeat.log 2>&1 & echo $! > runtime/heartbeat.pid
fi

# --- Helpful hints for debugging in Shell ---
echo "[boot] tail logs:"
echo "  tail -f runtime/news.log"
[ -f runtime/heartbeat.log ] && echo "  tail -f runtime/heartbeat.log"

# --- Start Streamlit dashboard (foreground keeps service alive) ---
echo "[boot] starting streamlit dashboard..."
exec streamlit run dashboard.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT:-10000}" \
  --server.headless true
