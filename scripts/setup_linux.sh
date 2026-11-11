#!/usr/bin/env bash
# CodeMind OS setup for Linux/macOS. Creates a venv and installs Python dependencies.
# It does NOT download models, run repository code, or overwrite existing files.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
"$PY" - <<'PYEOF' || { echo "Python 3.11+ is required."; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PYEOF
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
[ -f .env ] || { cp .env.example .env && echo "Created .env from .env.example"; }
python scripts/check_environment.py || true
cat <<'MSG'

Next steps (nothing large is downloaded automatically):
  1. Install Ollama from https://ollama.com, then choose to run:  ollama pull qwen2.5-coder:7b
  2. Optional offline embeddings:  python scripts/check_environment.py --download-embedding-model
  3. Start CodeMind:   source .venv/bin/activate && python main.py serve
  4. Open the web UI:  http://127.0.0.1:8000/
MSG
