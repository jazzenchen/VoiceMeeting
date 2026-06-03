from __future__ import annotations

import importlib.util
import os
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional


class DiarizationUnavailable(RuntimeError):
    pass


PYANNOTE_REQUIRED_PYTHON = (3, 10)
PYANNOTE_REQUIRED_PACKAGE = ">=4.0.0"
PYANNOTE_PACKAGE_NAME = "pyannote.audio"


def _python_version_text() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _major_version(value: str) -> Optional[int]:
    head = value.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def pyannote_audio_status() -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "package": PYANNOTE_PACKAGE_NAME,
        "required_package": PYANNOTE_REQUIRED_PACKAGE,
        "package_version": "",
        "required_python": ">=3.10",
        "python": _python_version_text(),
        "available": False,
        "reason": "",
    }
    if sys.version_info < PYANNOTE_REQUIRED_PYTHON:
        status["reason"] = (
            f"高精度分离需要 Python 3.10+ 运行环境。当前是 Python {_python_version_text()}，"
            "请重新运行安装脚本。"
        )
        return status
    if not _module_available("pyannote") or not _module_available(PYANNOTE_PACKAGE_NAME):
        status["reason"] = "高精度分离运行库未安装，请重新运行安装脚本。"
        return status
    try:
        package_version = metadata.version(PYANNOTE_PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        status["reason"] = "高精度分离运行库未安装，请重新运行安装脚本。"
        return status

    status["package_version"] = package_version
    major = _major_version(package_version)
    if major is None or major < 4:
        status["reason"] = (
            f"高精度分离运行库版本过低（当前 {package_version}，需要 4.0+），"
            "请重新运行安装脚本。"
        )
        return status
    status["available"] = True
    return status


class PyannoteDiarizer:
    def __init__(
        self,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        device: Optional[str] = None,
    ) -> None:
        self.backend = (backend or os.environ.get("VOICE_MEETING_DIARIZATION", "off")).strip().lower()
        self.model = model or os.environ.get(
            "VOICE_MEETING_DIARIZATION_MODEL",
            "pyannote/speaker-diarization-community-1",
        )
        self.device = device or os.environ.get("VOICE_MEETING_DIARIZATION_DEVICE", "cpu")
        self.token = (
            os.environ.get("VOICE_MEETING_HF_TOKEN")
            or os.environ.get("HUGGINGFACE_TOKEN")
            or os.environ.get("HF_TOKEN")
        )
        self.allow_remote_model = os.environ.get("VOICE_MEETING_ALLOW_MODEL_DOWNLOAD", "0").strip() in {
            "1",
            "true",
            "yes",
        }
        self._pipeline = None
        self.loading = False
        self.loaded = False
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return self.backend in {"pyannote", "on", "true", "1"}

    def status(self) -> Dict[str, Any]:
        reason = ""
        dependency = pyannote_audio_status()
        if not self.enabled:
            reason = "set VOICE_MEETING_DIARIZATION=pyannote to enable"
        elif not Path(self.model).exists():
            reason = "本地高精度分离模型未安装，请先在设置里下载模型。"
        elif not dependency["available"]:
            reason = str(dependency["reason"])
        return {
            "backend": "pyannote" if self.enabled else "off",
            "model": self.model,
            "device": self.device,
            "enabled": self.enabled,
            "loaded": self.loaded,
            "loading": self.loading,
            "available": self.enabled and not reason,
            "reason": reason,
            "last_error": self.last_error,
            "dependency": dependency,
        }

    def _load_pipeline(self) -> Any:
        if not self.enabled:
            raise DiarizationUnavailable("高精度分离未启用。")
        if self._pipeline is not None:
            return self._pipeline
        if not Path(self.model).exists() and not self.allow_remote_model:
            raise DiarizationUnavailable(
                "本地高精度分离模型未安装，请先在设置里下载模型。"
            )
        dependency = pyannote_audio_status()
        if not dependency["available"]:
            raise DiarizationUnavailable(str(dependency["reason"]))

        self.loading = True
        self.last_error = ""
        try:
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(
                self.model,
                token=self.token,
                local_files_only=not self.allow_remote_model,
            )
            try:
                import torch

                pipeline.to(torch.device(self.device))
            except Exception:
                pass
            self._pipeline = pipeline
            self.loaded = True
            return pipeline
        except Exception as exc:
            self.last_error = str(exc)
            raise DiarizationUnavailable(str(exc)) from exc
        finally:
            self.loading = False

    def diarize(self, wav_path: Path) -> List[Dict[str, Any]]:
        pipeline = self._load_pipeline()
        output = pipeline(str(wav_path))
        annotation = getattr(output, "speaker_diarization", output)
        turns: List[Dict[str, Any]] = []

        if hasattr(annotation, "itertracks"):
            iterator = annotation.itertracks(yield_label=True)
            for turn, _, speaker in iterator:
                turns.append(
                    {
                        "start_ms": int(float(turn.start) * 1000),
                        "end_ms": int(float(turn.end) * 1000),
                        "speaker": str(speaker),
                    }
                )
        else:
            for item in annotation:
                try:
                    turn, speaker = item
                    turns.append(
                        {
                            "start_ms": int(float(turn.start) * 1000),
                            "end_ms": int(float(turn.end) * 1000),
                            "speaker": str(speaker),
                        }
                    )
                except Exception:
                    continue
        return turns


def _overlap_ms(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    return max(0, min(left_end, right_end) - max(left_start, right_start))


def assign_speakers(
    segments: List[Dict[str, Any]],
    turns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not turns:
        return segments

    assigned: List[Dict[str, Any]] = []
    for segment in segments:
        start_ms = int(segment.get("start_ms") or 0)
        end_ms = int(segment.get("end_ms") or start_ms)
        best_speaker = ""
        best_overlap = 0
        for turn in turns:
            overlap = _overlap_ms(
                start_ms,
                end_ms,
                int(turn.get("start_ms") or 0),
                int(turn.get("end_ms") or 0),
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = str(turn.get("speaker") or "")
        assigned.append({**segment, "speaker": best_speaker or segment.get("speaker") or ""})
    return assigned
