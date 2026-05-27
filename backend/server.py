from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _is_writable(stream: object) -> bool:
    if stream is None:
        return False
    try:
        stream.write("")
        return True
    except Exception:
        return False


if not _is_writable(sys.stdout):
    sys.stdout = open(os.devnull, "w")
if not _is_writable(sys.stderr):
    sys.stderr = open(os.devnull, "w")

multiprocessing.freeze_support()

if "--version" in sys.argv:
    from backend import __version__

    print(f"voice-meeting-server {__version__}")
    sys.exit(0)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("voice-meeting-server")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _start_parent_watchdog(parent_pid: int) -> None:
    def watch() -> None:
        while True:
            if not _pid_alive(parent_pid):
                logger.info("Parent process %s exited, stopping server.", parent_pid)
                if sys.platform == "win32":
                    os._exit(0)
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(2)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()


def _default_bundle_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VoiceMeeting"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "VoiceMeeting"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "VoiceMeeting"


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceMeeting backend server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--models-dir", default=None)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else _default_bundle_data_dir() / "data"
    models_dir = Path(args.models_dir) if args.models_dir else _default_bundle_data_dir() / "models"
    data_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    os.environ["VOICE_MEETING_DATA_DIR"] = str(data_dir)
    os.environ["VOICE_MEETING_MODELS_DIR"] = str(models_dir)
    os.environ["VOICE_MEETING_ASR_MODEL_DIR"] = str(models_dir / "faster-whisper")
    os.environ["VOICE_MEETING_MLX_ASR_MODEL_DIR"] = str(models_dir / "mlx-whisper")
    if args.allow_model_download:
        os.environ["VOICE_MEETING_ALLOW_MODEL_DOWNLOAD"] = "1"
    os.environ.setdefault("VIBEAROUND_WORKSPACE", str(data_dir.parent))

    if args.parent_pid is not None:
        if args.parent_pid <= 0:
            parser.error("--parent-pid must be a positive integer")
        _start_parent_watchdog(args.parent_pid)

    logger.info("Starting VoiceMeeting server on %s:%s", args.host, args.port)
    logger.info("Data directory: %s", data_dir)
    logger.info("Models directory: %s", models_dir)

    try:
        import uvicorn
        from backend.main import app
    except Exception:
        logger.exception("Failed to import backend application.")
        return 1

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
