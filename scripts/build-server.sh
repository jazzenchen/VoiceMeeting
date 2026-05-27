#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PLATFORM="$(rustc --print host-tuple 2>/dev/null || echo unknown)"
PYTHON="${VOICE_MEETING_BUILD_PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
  "$PYTHON" -m pip install pyinstaller
fi

"$PYTHON" backend/build_binary.py

mkdir -p tauri/src-tauri/resources
rm -rf tauri/src-tauri/resources/voice-meeting-server

if [ -d backend/dist/voice-meeting-server ]; then
  cp -R backend/dist/voice-meeting-server tauri/src-tauri/resources/voice-meeting-server
  find tauri/src-tauri/resources/voice-meeting-server -type f -name '._*' -delete
  xattr -cr tauri/src-tauri/resources/voice-meeting-server 2>/dev/null || true
  if [ -f tauri/src-tauri/resources/voice-meeting-server/voice-meeting-server ]; then
    chmod +x tauri/src-tauri/resources/voice-meeting-server/voice-meeting-server
  fi
  echo "Built tauri/src-tauri/resources/voice-meeting-server for ${PLATFORM}"
elif [ -f backend/dist/voice-meeting-server ]; then
  mkdir -p tauri/src-tauri/resources/voice-meeting-server
  cp backend/dist/voice-meeting-server tauri/src-tauri/resources/voice-meeting-server/voice-meeting-server
  chmod +x tauri/src-tauri/resources/voice-meeting-server/voice-meeting-server
  echo "Built tauri/src-tauri/resources/voice-meeting-server for ${PLATFORM}"
elif [ -f backend/dist/voice-meeting-server.exe ]; then
  mkdir -p tauri/src-tauri/resources/voice-meeting-server
  cp backend/dist/voice-meeting-server.exe tauri/src-tauri/resources/voice-meeting-server/voice-meeting-server.exe
  echo "Built tauri/src-tauri/resources/voice-meeting-server for ${PLATFORM}"
else
  echo "voice-meeting-server binary was not produced." >&2
  exit 1
fi
