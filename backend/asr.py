from __future__ import annotations

import gc
import os
import re
import subprocess
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import (
    ALLOW_MODEL_DOWNLOAD,
    ASR_COMPUTE_TYPE,
    ASR_DEVICE,
    ASR_LANGUAGE,
    ASR_MODEL,
    ASR_MODEL_DIR,
    FUNASR_MODEL_DIR,
    MLX_ASR_MODEL_DIR,
)
from .media_tools import ffmpeg_path


SAMPLE_RATE = 16000
ASR_CONTEXT_MAX_CHARS = 360
ASR_CONTEXT_MIN_SPEECH_MS = 1200
PROMPT_ECHO_MIN_CHARS = 10
PROMPT_ECHO_COVERAGE = 0.88
PROMPT_ECHO_RATIO = 0.45
MIXED_LANGUAGE_PROMPT = (
    "这是会议录音转写。请使用简体中文；如果听到英文单词、产品名、"
    "人名、代码、模型名或缩写，请保留原始英文，不要翻译或音译。"
)
AUTO_LANGUAGE_PROMPT = (
    "Transcribe each utterance in the language that is actually spoken. "
    "Do not translate. Preserve code-switching, names, acronyms, and technical terms."
)
HALLUCINATION_MARKERS = (
    "请保留原始英文",
    "保留原始英文单词",
    "不要翻译或音译",
    "这是一次会议录音转写",
    "会议转写上下文",
    "请尽量保留原语言",
    "不要把英文或其他语言翻译成中文",
    "请建议一个任务",
    "欢迎订阅",
    "请不客点赞",
    "请点赞",
    "点赞、订阅、转发",
    "订阅、转发、打赏",
    "打赏支持明镜",
    "点点栏目",
    "明镜与点点",
)
PROMPT_ECHO_MARKERS = (
    "这是一次会议录音转写",
    "会议转写上下文",
    "请尽量保留原语言",
    "不要把英文或其他语言翻译成中文",
    "transcribe each utterance",
    "do not translate",
    "preserve code-switching",
)

MLX_MODEL_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


class ASRUnavailable(RuntimeError):
    pass


class FasterWhisperASR:
    def __init__(
        self,
        model_name: str = ASR_MODEL,
        model_dir: Path = ASR_MODEL_DIR,
        device: str = ASR_DEVICE,
        compute_type: str = ASR_COMPUTE_TYPE,
        language: Optional[str] = ASR_LANGUAGE,
    ) -> None:
        self.model_name = model_name
        self.model_dir = model_dir
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None
        self.loading = False
        self.loaded = False
        self.last_error = ""
        self._opencc = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        self.loading = True
        self.last_error = ""
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            self.loading = False
            self.last_error = str(exc)
            raise ASRUnavailable(
                "faster-whisper is not installed. Run scripts/setup.sh first."
            ) from exc

        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.model_dir),
                local_files_only=not ALLOW_MODEL_DOWNLOAD,
            )
            self.loaded = True
            return self._model
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.loading = False

    def load(self) -> Any:
        return self._load_model()

    def unload(self) -> None:
        self._model = None
        self.loaded = False
        self.loading = False
        gc.collect()

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "faster-whisper",
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "loaded": self.loaded,
            "loading": self.loading,
            "language": self.language or "auto",
            "last_error": self.last_error,
        }

    def require_loaded(self) -> Any:
        if self._model is None or not self.loaded:
            raise ASRUnavailable("识别模型尚未加载，请先在设置中加载模型。")
        return self._model

    def convert_to_wav(self, source: Path, destination: Path) -> None:
        ffmpeg = ffmpeg_path()
        if ffmpeg is None:
            raise ASRUnavailable("ffmpeg is required for audio chunk conversion.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(destination),
        ]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            lines = [
                line.strip()
                for line in result.stderr.decode("utf-8", errors="ignore").splitlines()
                if line.strip()
            ]
            message = lines[-1] if lines else "unknown ffmpeg error"
            for line in reversed(lines):
                if "Invalid data" in line or "EBML" in line or "Error" in line:
                    message = line
                    break
            raise ASRUnavailable(f"ffmpeg failed to convert audio: {message}")

    def transcribe(
        self,
        wav_path: Path,
        language: Optional[str] = None,
        context_prompt: str = "",
    ) -> Dict[str, Any]:
        model = self.require_loaded()
        requested_language = self.language if language is None else language
        mixed_language = requested_language in {"mixed", "zh-en", "bilingual"}
        auto_language = requested_language in {None, "auto", "multilingual"}
        if mixed_language:
            # For real-time Chinese meetings, letting Whisper auto-detect every short
            # chunk often produces unstable languages and boilerplate hallucinations.
            requested_language = "zh"
        elif requested_language in {"auto", "multilingual"}:
            requested_language = None

        vad_segments = self.detect_speech(wav_path)
        if not vad_segments:
            return self.empty_result(language, requested_language, auto_language or mixed_language)

        speech_ms = sum(max(0, int(item["end_ms"]) - int(item["start_ms"])) for item in vad_segments)
        initial_prompt = self.clean_context_prompt(context_prompt) if speech_ms >= ASR_CONTEXT_MIN_SPEECH_MS else None

        segments_iter, info = model.transcribe(
            str(wav_path),
            language=requested_language,
            vad_filter=True,
            beam_size=5,
            temperature=0.0,
            no_speech_threshold=0.6,
            log_prob_threshold=-0.8,
            condition_on_previous_text=False,
            task="transcribe",
            initial_prompt=initial_prompt,
            multilingual=requested_language is None,
            language_detection_segments=2,
            hotwords=None,
        )

        segments: List[Dict[str, Any]] = []
        for item in segments_iter:
            text = self.to_simplified((item.text or "").strip())
            if not text:
                continue
            no_speech_prob = getattr(item, "no_speech_prob", None)
            avg_logprob = getattr(item, "avg_logprob", None)
            compression_ratio = getattr(item, "compression_ratio", None)
            duration_ms = int(float(item.end or 0.0) * 1000) - int(float(item.start or 0.0) * 1000)
            if self.is_prompt_echo(text, initial_prompt):
                continue
            if self.is_likely_hallucination(
                text,
                no_speech_prob,
                avg_logprob,
                duration_ms,
                compression_ratio,
            ):
                continue
            if no_speech_prob is not None and no_speech_prob > 0.85 and len(text) < 24:
                continue
            if avg_logprob is not None and avg_logprob < -1.2 and len(text) < 24:
                continue
            segments.append(
                {
                    "start_ms": int(float(item.start or 0.0) * 1000),
                    "end_ms": int(float(item.end or 0.0) * 1000),
                    "text": text,
                    "confidence": None,
                }
            )

        return {
            "requested_language": language or self.language or "auto",
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "top_languages": [
                {"language": item[0], "probability": item[1]}
                for item in (getattr(info, "all_language_probs", None) or [])[:5]
            ],
            "multilingual": requested_language is None,
            "vad_segments": vad_segments,
            "segments": segments,
            "text": "\n".join(segment["text"] for segment in segments),
        }

    def empty_result(
        self,
        language: Optional[str],
        requested_language: Optional[str],
        multilingual: bool,
    ) -> Dict[str, Any]:
        return {
            "requested_language": language or self.language or "auto",
            "language": requested_language,
            "language_probability": None,
            "top_languages": [],
            "multilingual": multilingual,
            "vad_segments": [],
            "segments": [],
            "text": "",
        }

    def is_likely_hallucination(
        self,
        text: str,
        no_speech_prob: Optional[float],
        avg_logprob: Optional[float],
        duration_ms: int,
        compression_ratio: Optional[float] = None,
    ) -> bool:
        compact = "".join(str(text or "").split()).lower()
        if not compact:
            return True
        if self.has_excessive_repetition(compact):
            return True
        if compression_ratio is not None and compression_ratio > 2.4:
            return True
        if any(marker.replace(" ", "").lower() in compact for marker in HALLUCINATION_MARKERS):
            return True
        if "订阅" in compact and ("点赞" in compact or "打赏" in compact or "转发" in compact):
            return True
        if no_speech_prob is not None and no_speech_prob > 0.72 and len(compact) < 80:
            return True
        if (
            no_speech_prob is not None
            and avg_logprob is not None
            and no_speech_prob > 0.5
            and avg_logprob < -0.8
        ):
            return True
        if (
            no_speech_prob is not None
            and avg_logprob is not None
            and no_speech_prob > 0.55
            and avg_logprob < -0.55
            and duration_ms < 4500
        ):
            return True
        return False

    def has_excessive_repetition(self, compact_text: str) -> bool:
        if not compact_text:
            return False
        if re.search(r"([a-z0-9])\1{7,}", compact_text):
            return True
        if re.search(r"([\u4e00-\u9fff])\1{5,}", compact_text):
            return True

        text_length = len(compact_text)
        max_unit = min(12, max(2, text_length // 2))
        for unit_size in range(2, max_unit + 1):
            index = 0
            while index + unit_size * 3 <= text_length:
                unit = compact_text[index : index + unit_size]
                repeats = 1
                cursor = index + unit_size
                while compact_text[cursor : cursor + unit_size] == unit:
                    repeats += 1
                    cursor += unit_size
                repeated_chars = repeats * unit_size
                if repeats >= 4 and repeated_chars >= 12:
                    return True
                if repeats >= 3 and repeated_chars >= max(24, int(text_length * 0.45)):
                    return True
                index += 1
        return False

    def is_prompt_echo(self, text: str, context_prompt: Optional[str]) -> bool:
        compact_text = self.compact_prompt_echo_text(text)
        compact_prompt = self.compact_prompt_echo_text(context_prompt or "")
        if (
            len(compact_text) < PROMPT_ECHO_MIN_CHARS
            or len(compact_prompt) < PROMPT_ECHO_MIN_CHARS
        ):
            return False
        if compact_text in compact_prompt or compact_prompt in compact_text:
            return True

        markers = tuple(
            item
            for item in (self.compact_prompt_echo_text(marker) for marker in PROMPT_ECHO_MARKERS)
            if item
        )
        if not any(marker in compact_text for marker in markers):
            return False

        matcher = SequenceMatcher(None, compact_text, compact_prompt)
        matched_chars = sum(block.size for block in matcher.get_matching_blocks())
        coverage = matched_chars / max(1, len(compact_text))
        return coverage >= PROMPT_ECHO_COVERAGE and matcher.ratio() >= PROMPT_ECHO_RATIO

    def compact_prompt_echo_text(self, value: Any) -> str:
        simplified = self.to_simplified(str(value or "")).lower()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", simplified)

    def clean_context_prompt(self, context_prompt: str) -> Optional[str]:
        text = re.sub(r"\s+", " ", str(context_prompt or "")).strip()
        if not text:
            return None
        # Keep this descriptive, not instructional. Long prompts are more likely
        # to be copied into uncertain short chunks.
        return text[:ASR_CONTEXT_MAX_CHARS]

    def to_simplified(self, text: str) -> str:
        if not text:
            return ""
        try:
            if self._opencc is None:
                from opencc import OpenCC

                self._opencc = OpenCC("t2s")
            return self._opencc.convert(text)
        except Exception:
            return text

    def detect_speech(self, wav_path: Path) -> List[Dict[str, int]]:
        try:
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import VadOptions, get_speech_timestamps
        except Exception as exc:
            self.last_error = str(exc)
            return []

        audio = decode_audio(str(wav_path), sampling_rate=SAMPLE_RATE)
        timestamps = get_speech_timestamps(
            audio,
            vad_options=VadOptions(
                threshold=0.5,
                min_speech_duration_ms=250,
                min_silence_duration_ms=650,
                speech_pad_ms=250,
            ),
            sampling_rate=SAMPLE_RATE,
        )
        return [
            {
                "start_ms": int(item["start"] * 1000 / SAMPLE_RATE),
                "end_ms": int(item["end"] * 1000 / SAMPLE_RATE),
            }
            for item in timestamps
        ]


class MlxWhisperASR(FasterWhisperASR):
    def __init__(
        self,
        model_name: str,
        model_dir: Path = MLX_ASR_MODEL_DIR,
        language: Optional[str] = ASR_LANGUAGE,
        repo_id: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> None:
        base_model_name = model_name.removeprefix("mlx-")
        super().__init__(
            model_name=display_name or f"mlx-{base_model_name}",
            model_dir=model_dir,
            device="mlx",
            compute_type="float16",
            language=language,
        )
        self.base_model_name = base_model_name
        self.repo_id = repo_id or MLX_MODEL_REPOS.get(base_model_name, base_model_name)
        self._runtime_path = ""

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "mlx-whisper",
            "model": self.model_name,
            "repo_id": self.repo_id,
            "device": self.device,
            "compute_type": self.compute_type,
            "loaded": self.loaded,
            "loading": self.loading,
            "language": self.language or "auto",
            "last_error": self.last_error,
        }

    def _local_snapshot(self) -> Optional[Path]:
        cache_path = self.model_dir / f"models--{self.repo_id.replace('/', '--')}" / "snapshots"
        if not cache_path.exists():
            return None
        candidates = sorted(
            (item for item in cache_path.iterdir() if item.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for snapshot in candidates:
            if (snapshot / "config.json").is_file() and (
                (snapshot / "weights.safetensors").is_file()
                or (snapshot / "weights.npz").is_file()
            ):
                return snapshot
        return None

    def _runtime_model_path(self) -> str:
        if self._runtime_path:
            return self._runtime_path
        snapshot = self._local_snapshot()
        if snapshot is not None:
            self._runtime_path = str(snapshot)
            return self._runtime_path
        if not ALLOW_MODEL_DOWNLOAD:
            raise ASRUnavailable("ASR model is not available locally.")
        try:
            from huggingface_hub import snapshot_download

            self.model_dir.mkdir(parents=True, exist_ok=True)
            self._runtime_path = snapshot_download(
                repo_id=self.repo_id,
                cache_dir=str(self.model_dir),
            )
            return self._runtime_path
        except Exception as exc:
            self.last_error = str(exc)
            raise

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        self.loading = True
        self.last_error = ""
        try:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
        except Exception as exc:
            self.loading = False
            self.last_error = str(exc)
            raise ASRUnavailable(
                "mlx-whisper is not installed. Install the macOS MLX backend first."
            ) from exc

        try:
            runtime_path = self._runtime_model_path()
            self._model = ModelHolder.get_model(runtime_path, mx.float16)
            self.loaded = True
            return self._model
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.loading = False

    def unload(self) -> None:
        runtime_path = self._runtime_path
        super().unload()
        try:
            from mlx_whisper.transcribe import ModelHolder

            if not runtime_path or ModelHolder.model_path == runtime_path:
                ModelHolder.model = None
                ModelHolder.model_path = None
        except Exception:
            pass

    def transcribe(
        self,
        wav_path: Path,
        language: Optional[str] = None,
        context_prompt: str = "",
    ) -> Dict[str, Any]:
        self.require_loaded()
        requested_language = self.language if language is None else language
        mixed_language = requested_language in {"mixed", "zh-en", "bilingual"}
        auto_language = requested_language in {None, "auto", "multilingual"}
        if mixed_language:
            requested_language = "zh"
        elif requested_language in {"auto", "multilingual"}:
            requested_language = None

        vad_segments = self.detect_speech(wav_path)
        if not vad_segments:
            return self.empty_result(language, requested_language, auto_language or mixed_language)

        speech_ms = sum(max(0, int(item["end_ms"]) - int(item["start_ms"])) for item in vad_segments)
        initial_prompt = self.clean_context_prompt(context_prompt) if speech_ms >= ASR_CONTEXT_MIN_SPEECH_MS else None

        try:
            import mlx_whisper
        except Exception as exc:
            self.last_error = str(exc)
            raise ASRUnavailable(
                "mlx-whisper is not installed. Install the macOS MLX backend first."
            ) from exc

        result = mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=self._runtime_model_path(),
            verbose=None,
            temperature=0.0,
            compression_ratio_threshold=2.4,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt,
            language=requested_language,
            task="transcribe",
            fp16=True,
        )

        segments: List[Dict[str, Any]] = []
        for item in result.get("segments") or []:
            text = self.to_simplified(str(item.get("text") or "").strip())
            if not text:
                continue
            no_speech_prob = item.get("no_speech_prob")
            avg_logprob = item.get("avg_logprob")
            compression_ratio = item.get("compression_ratio")
            duration_ms = int(float(item.get("end") or 0.0) * 1000) - int(float(item.get("start") or 0.0) * 1000)
            if self.is_prompt_echo(text, initial_prompt):
                continue
            if self.is_likely_hallucination(
                text,
                no_speech_prob,
                avg_logprob,
                duration_ms,
                compression_ratio,
            ):
                continue
            if no_speech_prob is not None and no_speech_prob > 0.85 and len(text) < 24:
                continue
            if avg_logprob is not None and avg_logprob < -1.8 and len(text) < 24:
                continue
            segments.append(
                {
                    "start_ms": int(float(item.get("start") or 0.0) * 1000),
                    "end_ms": int(float(item.get("end") or 0.0) * 1000),
                    "text": text,
                    "confidence": None,
                }
            )

        return {
            "requested_language": language or self.language or "auto",
            "backend": "mlx-whisper",
            "model": self.model_name,
            "language": result.get("language"),
            "language_probability": None,
            "top_languages": [],
            "multilingual": requested_language is None,
            "vad_segments": vad_segments,
            "segments": segments,
            "text": "\n".join(segment["text"] for segment in segments),
        }


class FunASRASR(FasterWhisperASR):
    def __init__(
        self,
        model_name: str,
        model_id: str,
        model_dir: Path = FUNASR_MODEL_DIR,
        language: Optional[str] = ASR_LANGUAGE,
        display_name: Optional[str] = None,
        vad_model: str = "fsmn-vad",
        punc_model: str = "ct-punc-c",
        trust_remote_code: bool = False,
    ) -> None:
        super().__init__(
            model_name=display_name or model_name,
            model_dir=model_dir,
            device=os.environ.get("VOICE_MEETING_FUNASR_DEVICE", "cpu"),
            compute_type="torch",
            language=language,
        )
        self.model_id = model_id
        self.vad_model = vad_model
        self.punc_model = punc_model
        self.trust_remote_code = trust_remote_code

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "funasr",
            "model": self.model_name,
            "model_id": self.model_id,
            "device": self.device,
            "compute_type": self.compute_type,
            "loaded": self.loaded,
            "loading": self.loading,
            "language": self.language or "auto",
            "last_error": self.last_error,
        }

    def _configure_cache(self) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MODELSCOPE_CACHE", str(self.model_dir / "modelscope"))
        os.environ.setdefault("HF_HOME", str(self.model_dir / "huggingface"))

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        self.loading = True
        self.last_error = ""
        try:
            from funasr import AutoModel
        except Exception as exc:
            self.loading = False
            self.last_error = str(exc)
            raise ASRUnavailable(
                "funasr is not installed. Run scripts/setup.sh first."
            ) from exc

        try:
            self._configure_cache()
            kwargs: Dict[str, Any] = {
                "model": self.model_id,
                "device": self.device,
                "disable_update": True,
            }
            if self.vad_model:
                kwargs["vad_model"] = self.vad_model
                kwargs["vad_kwargs"] = {"max_single_segment_time": 30000}
            if self.punc_model:
                kwargs["punc_model"] = self.punc_model
            if self.trust_remote_code:
                kwargs["trust_remote_code"] = True
            self._model = AutoModel(**kwargs)
            self.loaded = True
            return self._model
        except Exception as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.loading = False

    def unload(self) -> None:
        super().unload()
        try:
            import torch

            if getattr(torch, "cuda", None) and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def transcribe(
        self,
        wav_path: Path,
        language: Optional[str] = None,
        context_prompt: str = "",
    ) -> Dict[str, Any]:
        model = self.require_loaded()
        vad_segments = self.detect_speech(wav_path)
        if not vad_segments:
            return self.empty_result(language, language or self.language, True)

        kwargs: Dict[str, Any] = {
            "input": str(wav_path),
            "batch_size_s": 300,
            "merge_vad": True,
        }
        hotword = self.clean_context_prompt(context_prompt)
        if hotword:
            kwargs["hotword"] = hotword

        result = model.generate(**kwargs)
        segments = self._segments_from_result(result, wav_path)
        if hotword:
            segments = [
                segment
                for segment in segments
                if not self.is_prompt_echo(segment.get("text", ""), hotword)
            ]
        return {
            "requested_language": language or self.language or "auto",
            "backend": "funasr",
            "model": self.model_name,
            "language": self._language_from_request(language),
            "language_probability": None,
            "top_languages": [],
            "multilingual": True,
            "vad_segments": vad_segments,
            "segments": segments,
            "text": "\n".join(segment["text"] for segment in segments),
        }

    def _language_from_request(self, language: Optional[str]) -> Optional[str]:
        requested = language or self.language
        if requested in {None, "auto", "multilingual", "mixed", "zh-en", "bilingual"}:
            return None
        return requested

    def _segments_from_result(self, result: Any, wav_path: Path) -> List[Dict[str, Any]]:
        records = result if isinstance(result, list) else [result]
        segments: List[Dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            sentence_info = record.get("sentence_info") or record.get("sentences") or []
            for sentence in sentence_info:
                if not isinstance(sentence, dict):
                    continue
                text = self.clean_funasr_text(sentence.get("text") or sentence.get("raw_text") or "")
                if not text:
                    continue
                start_ms, end_ms = self._sentence_range(sentence)
                segments.append(
                    {
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "text": text,
                        "confidence": None,
                    }
                )
            if segments:
                continue
            text = self.clean_funasr_text(record.get("text") or "")
            if text:
                duration_ms = self._wav_duration_ms(wav_path)
                segments.append(
                    {
                        "start_ms": 0,
                        "end_ms": max(duration_ms, 1000),
                        "text": text,
                        "confidence": None,
                    }
                )
        return segments

    def _sentence_range(self, sentence: Dict[str, Any]) -> tuple[int, int]:
        start = sentence.get("start") or sentence.get("start_ms")
        end = sentence.get("end") or sentence.get("end_ms")
        timestamp = sentence.get("timestamp")
        if (start is None or end is None) and isinstance(timestamp, list) and timestamp:
            first = timestamp[0]
            last = timestamp[-1]
            if isinstance(first, (list, tuple)) and len(first) >= 2:
                start = first[0]
            if isinstance(last, (list, tuple)) and len(last) >= 2:
                end = last[1]
        start_ms = self._normalize_time_ms(start)
        end_ms = self._normalize_time_ms(end)
        if end_ms <= start_ms:
            end_ms = start_ms + 1000
        return start_ms, end_ms

    def _normalize_time_ms(self, value: Any) -> int:
        try:
            numeric = float(value)
        except Exception:
            return 0
        if numeric < 100:
            numeric *= 1000
        return max(0, int(round(numeric)))

    def _wav_duration_ms(self, wav_path: Path) -> int:
        try:
            with wave.open(str(wav_path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate() or SAMPLE_RATE
            return int(frames * 1000 / rate)
        except Exception:
            return 0

    def clean_funasr_text(self, text: Any) -> str:
        value = re.sub(r"<\|[^>]+?\|>", "", str(text or ""))
        value = re.sub(r"\s+", " ", value).strip()
        return self.to_simplified(value)
