#!/bin/zsh
set -euo pipefail
BASE_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENV_PY="${VENV_PY:-/Users/armankhan/Documents/malomatia-competition-package/.venv/bin/python}"
if [ ! -x "$VENV_PY" ]; then
  echo "Python interpreter not found at $VENV_PY"
  exit 1
fi
cd "$BASE_DIR"
exec "$VENV_PY" -m streamlit run kb_app.py --server.headless true --server.port 8520
