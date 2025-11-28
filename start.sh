#!/usr/bin/env bash
set -euo pipefail
# activate venv created by Render
source "${VENV_ROOT:-/opt/render/project/src/.venv}/bin/activate"
# start streamlit on the port Render sets
exec streamlit run dashboard.py --server.address 0.0.0.0 --server.port "$PORT"
