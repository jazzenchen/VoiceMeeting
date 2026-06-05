#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${VOICE_MEETING_BACKEND_PORT:-8788}"
APP_SUPPORT_DIR="${VOICE_MEETING_APP_SUPPORT_DIR:-$HOME/Library/Application Support/com.jazzen.voicemeeting}"
DATA_DIR="${VOICE_MEETING_DATA_DIR:-$APP_SUPPORT_DIR/data}"
MODELS_DIR="${VOICE_MEETING_MODELS_DIR:-$APP_SUPPORT_DIR/models}"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT

bun run scripts/setup-dev-sidecar.js

.venv/bin/python -m backend.server \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  --data-dir "$DATA_DIR" \
  --models-dir "$MODELS_DIR" \
  --allow-model-download &

cd tauri
bun install
bun run tauri dev --no-watch
