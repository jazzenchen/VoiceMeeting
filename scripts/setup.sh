#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${VOICE_MEETING_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "Python 3.10+ is required. Install Python 3.10 or newer and rerun setup." >&2
  exit 1
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  "$PYTHON" --version >&2 || true
  echo "Python 3.10+ is required." >&2
  exit 1
fi

if [ -x .venv/bin/python ]; then
  if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    current_version="$(.venv/bin/python --version 2>&1 || echo Python unknown)"
    echo "Existing .venv uses ${current_version}. Move or remove .venv, then rerun ./scripts/setup.sh." >&2
    exit 1
  fi
else
  "$PYTHON" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if command -v bun >/dev/null 2>&1; then
  (cd frontend && bun install)
  (cd tauri && bun install)
else
  (cd frontend && npm install)
  (cd tauri && npm install)
fi

.venv/bin/python scripts/predownload_asr.py
