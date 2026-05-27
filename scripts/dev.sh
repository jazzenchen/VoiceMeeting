#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${VOICE_MEETING_BACKEND_PORT:-8788}"
FRONTEND_PORT="${VOICE_MEETING_FRONTEND_PORT:-5199}"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT

.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT" &

if command -v bun >/dev/null 2>&1; then
  (cd frontend && VITE_API_BASE="http://127.0.0.1:${BACKEND_PORT}" bun run dev --host 127.0.0.1 --port "$FRONTEND_PORT") &
else
  (cd frontend && VITE_API_BASE="http://127.0.0.1:${BACKEND_PORT}" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT") &
fi

wait
