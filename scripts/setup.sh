#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m venv .venv
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
