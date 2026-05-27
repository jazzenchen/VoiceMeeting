from pathlib import Path
import os

from faster_whisper import WhisperModel


project_dir = Path(__file__).resolve().parents[1]
model_name = os.environ.get("VOICE_MEETING_ASR_MODEL", "small")
model_dir = Path(os.environ.get("VOICE_MEETING_ASR_MODEL_DIR", project_dir / "models" / "faster-whisper"))
device = os.environ.get("VOICE_MEETING_ASR_DEVICE", "cpu")
compute_type = os.environ.get("VOICE_MEETING_ASR_COMPUTE_TYPE", "int8")

model_dir.mkdir(parents=True, exist_ok=True)
WhisperModel(model_name, device=device, compute_type=compute_type, download_root=str(model_dir))
print(f"Downloaded faster-whisper model '{model_name}' into {model_dir}")
