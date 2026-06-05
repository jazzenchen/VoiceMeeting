from __future__ import annotations

import asyncio
import json
import mimetypes
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from .api.schemas import (
    CreateEditableVersionRequest,
    CreateMeetingRequest,
    CreateVersionRequest,
    FinalizeRequest,
    LLMConfigRequest,
    MeetingAskRequest,
    ModelDownloadRequest,
    PromptConfigRequest,
    RecordingConfigRequest,
    RenameSpeakerRequest,
    ReprocessRequest,
    UpdateMeetingRequest,
    UpdateSegmentRequest,
)
from .asr import ASRUnavailable
from .asr_runtime import ASRRuntime
from .config import (
    ASR_MODEL,
    ASR_MODEL_DIR,
    PROJECT_DIR,
    ensure_runtime_dirs,
)
from .diarization import (
    DiarizationUnavailable,
    PyannoteDiarizer,
    assign_speakers,
    pyannote_audio_status,
    split_segments_by_turns,
)
from .llm import LLMManager
from .meeting_audio import (
    cleanup_chunk_audio_files,
    ensure_meeting_audio,
    meeting_audio_path,
    meeting_audio_status,
    meeting_audio_waveform,
)
from .model_registry import (
    ASR_MODEL_CATALOG,
    FUNASR_MODEL_CATALOG,
    FUNASR_MODEL_DIR,
    MAC_MLX_ENABLED,
    MLX_ASR_MODEL_CATALOG,
    MLX_ASR_MODEL_DIR,
    MODELS_DIR,
    PYANNOTE_COMMUNITY_MODEL_ID,
    PYANNOTE_COMMUNITY_REPO_ID,
    PYANNOTE_MODEL_DIR,
    SUPPORTED_ASR_MODELS,
    asr_model_cache_path,
    asr_model_repos,
    directory_size_bytes,
    discovered_asr_models,
    local_pyannote_model_path,
)
from .model_downloads import ModelDownloadManager
from .prompt_settings import PromptConfigStore
from .recording_config import RecordingConfigStore
from .speaker_tracker import SpeakerTracker, SpeakerTrackingUnavailable
from .storage import MeetingStore
from .summarizer import (
    DEFAULT_PROMPT_META,
    DEFAULT_PROMPTS,
    MeetingSummarizer,
    build_local_markdown,
    build_transcript_markdown,
    fallback_incremental_summary,
    notes_only_markdown,
)
from .transcript import build_utterances, is_unrecognized_text
from .transcript_source import (
    final_notes_current,
    summary_is_current,
    transcript_source,
    transcript_source_without_segments,
)
from .transcription_helpers import (
    asr_context_prompt,
    audio_duration_ms,
    clear_segment_speakers,
    existing_audio_path,
    segments_or_unrecognized,
)
from .transcription_pipeline import ChunkTranscriptionPipeline
from .vibearound import VibeAroundClient


ensure_runtime_dirs()

app = FastAPI(title="VoiceMeeting", version="0.0.2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = MeetingStore()
store.mark_interrupted_recordings()
store.mark_interrupted_chunks(error="服务重启时识别被中断，请重新识别这段音频。")
asr_runtime = ASRRuntime()
model_downloads = ModelDownloadManager(asr_runtime.engine_for_model)
diarizer = PyannoteDiarizer()
local_pyannote_diarizer: Optional[PyannoteDiarizer] = None
speaker_tracker = SpeakerTracker()
vibearound = VibeAroundClient()
llm = LLMManager(vibearound)
prompt_settings = PromptConfigStore(DEFAULT_PROMPTS, DEFAULT_PROMPT_META)
recording_config = RecordingConfigStore()
summarizer = MeetingSummarizer(llm, prompt_getter=prompt_settings.get)
summary_lock = asyncio.Lock()
summary_states: Dict[str, Dict[str, Any]] = {}
reprocess_states: Dict[str, Dict[str, Any]] = {}
REALTIME_SUMMARY_ENABLED = False
recording_model_load_error = ""
llm_status_cache: Dict[str, Any] = {"updated_at": 0.0, "value": {}}
llm_status_lock = asyncio.Lock()
LLM_STATUS_TTL_SECONDS = 10.0
SUMMARY_TASK_TIMEOUT_SECONDS = 300.0
SUMMARY_STALE_SECONDS = 300.0

SUPPORTED_ASR_LANGUAGES = {
    "mixed",
    "auto",
    "multilingual",
    "zh",
    "en",
    "ja",
    "ko",
    "fr",
    "de",
    "es",
    "ru",
    "pt",
}
SUPPORTED_SPEAKER_MODES = {"voiceprint", "diarization", "off", "auto"}


def diarizer_for_mode(mode: str) -> Optional[PyannoteDiarizer]:
    global local_pyannote_diarizer
    if mode == "diarization":
        if local_pyannote_diarizer is None:
            local_pyannote_diarizer = PyannoteDiarizer(
                backend="pyannote",
                model=str(PYANNOTE_MODEL_DIR),
            )
        return local_pyannote_diarizer
    if mode == "auto" and diarizer.enabled:
        return diarizer
    return None


def normalize_asr_model(value: Optional[str]) -> str:
    model_name = (value or ASR_MODEL).strip()
    if model_name not in SUPPORTED_ASR_MODELS:
        raise HTTPException(status_code=400, detail="当前识别方式不可用，请换一个选项。")
    return model_name


def resolve_asr_model(value: Optional[str]) -> str:
    model_name = normalize_asr_model(value)
    if model_name not in discovered_asr_models():
        raise HTTPException(status_code=400, detail="本地还没有这套识别资源，请选择已有的识别方式。")
    return model_name


def asr_catalog_groups() -> list[tuple[str, str, Dict[str, Dict[str, Any]]]]:
    groups: list[tuple[str, str, Dict[str, Dict[str, Any]]]] = []
    if MAC_MLX_ENABLED:
        groups.append(("mlx", "Apple MLX", MLX_ASR_MODEL_CATALOG))
    groups.extend(
        [
            ("faster-whisper", "通用", ASR_MODEL_CATALOG),
            ("funasr", "FunASR", FUNASR_MODEL_CATALOG),
        ]
    )
    return groups


def model_catalog() -> Dict[str, Any]:
    installed_asr = discovered_asr_models()
    asr_models = []
    for backend, backend_label, catalog in asr_catalog_groups():
        for name, meta in catalog.items():
            path = asr_model_cache_path(name)
            installed = name in installed_asr
            runtime_flags = asr_runtime.model_runtime_flags(name)
            asr_models.append(
                {
                    "kind": "asr",
                    "id": name,
                    "name": name,
                    "label": meta["label"],
                    "params": meta["params"],
                    "disk": meta["disk"],
                    "file_breakdown": meta.get("file_breakdown") or "",
                    "components": meta.get("components") or "",
                    "profile": meta["profile"],
                    "installed": installed,
                    "repo_id": asr_model_repos(name)[0],
                    "path": str(path),
                    "size_bytes": directory_size_bytes(path) if installed else 0,
                    "current": name == ASR_MODEL,
                    "loaded": runtime_flags["loaded"],
                    "loading": runtime_flags["loading"],
                    "job": model_downloads.latest_job("asr", name),
                    "backend": backend,
                    "backend_label": backend_label,
                    "base_model": meta.get("base_model") or name,
                }
        )

    pyannote_options = diarization_model_options(include_details=True)
    pyannote_local_runtime = pyannote_runtime_status(pyannote_options[0])
    return {
        "asr": {
            "model_dir": str(ASR_MODEL_DIR),
            "mlx_model_dir": str(MLX_ASR_MODEL_DIR) if MAC_MLX_ENABLED else "",
            "funasr_model_dir": str(FUNASR_MODEL_DIR),
            "models": asr_models,
        },
        "diarization": {
            "model_dir": str(MODELS_DIR / "pyannote"),
            "models": pyannote_options,
            "runtime": diarizer.status(),
            "local_runtime": pyannote_local_runtime,
        },
        "downloads": model_downloads.recent_states(),
    }


def diarization_model_options(include_details: bool = False) -> list[Dict[str, Any]]:
    pyannote_path = local_pyannote_model_path()
    pyannote_installed = pyannote_path is not None
    pyannote_dependency = pyannote_audio_status()
    pyannote_available = pyannote_installed and bool(pyannote_dependency.get("available"))
    pyannote_unavailable_reason = ""
    if pyannote_installed and not pyannote_available:
        pyannote_unavailable_reason = str(pyannote_dependency.get("reason") or "高精度分离运行环境未就绪。")
    local_loaded = bool(local_pyannote_diarizer and local_pyannote_diarizer.loaded)
    local_loading = bool(local_pyannote_diarizer and local_pyannote_diarizer.loading)
    option: Dict[str, Any] = {
        "kind": "diarization",
        "id": PYANNOTE_COMMUNITY_MODEL_ID,
        "name": PYANNOTE_COMMUNITY_MODEL_ID,
        "repo_id": PYANNOTE_COMMUNITY_REPO_ID,
        "label": "Pyannote Community-1",
        "installed": pyannote_installed,
        "requires_token": True,
        "token_available": model_downloads.hf_token_available(),
        "enabled": pyannote_available,
        "available": pyannote_available,
        "loaded": local_loaded or bool(diarizer.loaded),
        "loading": local_loading or bool(diarizer.loading),
        "unavailable_reason": pyannote_unavailable_reason,
        "dependency": pyannote_dependency,
        "job": model_downloads.latest_job("diarization", PYANNOTE_COMMUNITY_MODEL_ID),
    }
    if include_details:
        option.update(
            {
                "params": "pipeline（多模型组合）",
                "disk": "约 32MB",
                "file_breakdown": "embedding约 25MB + segmentation约 6MB + PLDA",
                "components": "segmentation + embedding + PLDA",
                "profile": "高精度说话人分离",
                "path": str(PYANNOTE_MODEL_DIR),
                "size_bytes": directory_size_bytes(PYANNOTE_MODEL_DIR) if pyannote_installed else 0,
            }
        )
    return [option]


def pyannote_runtime_status(model_option: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "backend": "pyannote",
        "model": str(PYANNOTE_MODEL_DIR),
        "device": diarizer.device,
        "enabled": bool(model_option.get("enabled")),
        "loaded": bool(local_pyannote_diarizer and local_pyannote_diarizer.loaded),
        "loading": bool(local_pyannote_diarizer and local_pyannote_diarizer.loading),
        "available": bool(model_option.get("available")),
        "reason": str(model_option.get("unavailable_reason") or ""),
        "last_error": str(local_pyannote_diarizer.last_error) if local_pyannote_diarizer else "",
        "dependency": model_option.get("dependency") or {},
    }


def asr_model_options() -> list[Dict[str, Any]]:
    options: list[Dict[str, Any]] = []
    installed_models = set(discovered_asr_models())
    for backend, backend_label, catalog in asr_catalog_groups():
        for name, meta in catalog.items():
            options.append(
                {
                    "name": name,
                    "label": meta["label"],
                    "backend": backend,
                    "backend_label": backend_label,
                    "base_model": meta.get("base_model") or name,
                    "installed": name in installed_models,
                }
            )
    return options


def parse_optional_ms(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0, int(round(float(text))))
    except ValueError:
        raise HTTPException(status_code=400, detail="音频时间信息无效，请重新录制或导入。")


def clean_identifier(value: str, fallback: str = "job") -> str:
    cleaned = "".join(ch for ch in (value or fallback).strip().lower() if ch.isalnum() or ch in "-_")
    return cleaned or fallback


def resolve_speaker_mode(value: Optional[str]) -> str:
    mode = clean_identifier(value or "voiceprint", "voiceprint")
    if mode not in SUPPORTED_SPEAKER_MODES:
        raise HTTPException(status_code=400, detail="当前说话人配置不可用。")
    return mode


def chunk_sort_key(chunk: Dict[str, Any]) -> tuple[bool, int, int]:
    started_at = chunk.get("started_at_ms")
    seq = int(chunk.get("seq") or 0)
    try:
        started = int(started_at) if started_at is not None else None
    except (TypeError, ValueError):
        started = None
    return started is None, started if started is not None else seq, seq


def audio_payload(meeting_id: str, audio: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not audio:
        return None
    duration_ms = int(audio.get("duration_ms") or 0)
    if duration_ms <= 0:
        return None
    chunk_count = int(audio.get("chunk_count") or 0)
    audio_version = f"{chunk_count}-{duration_ms}"
    return {
        "audio_url": f"/api/meetings/{meeting_id}/audio?v={audio_version}",
        "duration_ms": duration_ms,
        "chunk_count": chunk_count,
        "single_file": True,
    }


def attach_audio_payload(meeting: Dict[str, Any], audio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = audio_payload(str(meeting.get("id") or ""), audio)
    if payload is None:
        path = meeting_audio_path(str(meeting.get("id") or ""))
        if path.is_file():
            duration_ms = audio_duration_ms(path)
            if duration_ms and duration_ms > 0:
                payload = audio_payload(
                    str(meeting.get("id") or ""),
                    {
                        "duration_ms": duration_ms,
                        "chunk_count": len(meeting.get("chunks") or []),
                    },
                )
    if payload is None:
        return meeting
    return {**meeting, "audio": payload}


def primary_timeline_chunk(meeting: Dict[str, Any]) -> Dict[str, Any]:
    chunks = sorted(meeting.get("chunks") or [], key=chunk_sort_key)
    if not chunks:
        raise RuntimeError("这场会议没有可挂载时间线的音频记录。")
    return {
        **chunks[0],
        "started_at_ms": 0,
    }


def segment_on_full_audio(segment: Dict[str, Any]) -> Dict[str, Any]:
    start = segment.get("absolute_start_ms")
    end = segment.get("absolute_end_ms")
    if start is None:
        start = segment.get("start_ms") or 0
    if end is None:
        end = segment.get("end_ms") or start or 0
    try:
        start_ms = max(0, int(start))
    except (TypeError, ValueError):
        start_ms = 0
    try:
        end_ms = max(start_ms, int(end))
    except (TypeError, ValueError):
        end_ms = start_ms
    return {
        **segment,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "absolute_start_ms": start_ms,
        "absolute_end_ms": end_ms,
    }


async def ensure_single_meeting_audio(
    meeting_id: str,
    *,
    cleanup_chunks: bool = False,
) -> Optional[Dict[str, Any]]:
    meeting = store.get_meeting(meeting_id)
    chunks = list(meeting.get("chunks") or [])
    for chunk in chunks:
        if existing_audio_path(chunk.get("wav_path")) is not None:
            continue
        if existing_audio_path(chunk.get("audio_path")) is None:
            continue
        try:
            await asyncio.to_thread(prepare_chunk_wav, chunk)
        except Exception:
            continue

    meeting = store.get_meeting(meeting_id)
    chunks = list(meeting.get("chunks") or [])
    output_path = meeting_audio_path(meeting_id)
    audio = await asyncio.to_thread(ensure_meeting_audio, chunks, output_path)
    if audio and cleanup_chunks:
        removed = await asyncio.to_thread(cleanup_chunk_audio_files, chunks, output_path)
        audio = {**audio, "removed_chunk_files": removed}
    return audio


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sse_event(event: str, data: Dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def set_summary_state(meeting_id: str, status: str, error: str = "") -> None:
    summary_states[meeting_id] = {
        "status": status,
        "error": error,
        "updated_at": now_iso(),
    }


def set_reprocess_state(job_id: str, **fields: Any) -> Dict[str, Any]:
    current = dict(reprocess_states.get(job_id) or {})
    current.update(fields)
    current["updated_at"] = now_iso()
    reprocess_states[job_id] = current
    return current


def latest_reprocess_state(meeting_id: str) -> Optional[Dict[str, Any]]:
    jobs = [
        state
        for state in reprocess_states.values()
        if state.get("meeting_id") == meeting_id
    ]
    if not jobs:
        return None
    return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]


def get_summary_state(meeting_id: str) -> Dict[str, Any]:
    state = summary_states.get(meeting_id) or {"status": "idle", "error": "", "updated_at": ""}
    if state.get("status") not in {"queued", "updating"}:
        return state

    updated_at = state.get("updated_at") or ""
    try:
        stamp = datetime.fromisoformat(updated_at)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
    except Exception:
        age = SUMMARY_STALE_SECONDS + 1

    if age > SUMMARY_STALE_SECONDS:
        set_summary_state(
            meeting_id,
            "error",
            f"Summary task exceeded {int(SUMMARY_STALE_SECONDS)} seconds.",
        )
        return summary_states[meeting_id]
    return state


def meeting_runtime(meeting_id: str) -> Dict[str, Any]:
    meeting = store.get_meeting(meeting_id)
    counts: Dict[str, int] = {}
    active_chunks = []
    for chunk in meeting.get("chunks") or []:
        status = chunk.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        if status in {"saved", "converting", "transcribing", "diarizing", "identifying_speakers"}:
            active_chunks.append(
                {
                    "id": chunk.get("id"),
                    "seq": chunk.get("seq"),
                    "status": status,
                    "duration_ms": chunk.get("duration_ms"),
                    "started_at_ms": chunk.get("started_at_ms"),
                    "ended_at_ms": chunk.get("ended_at_ms"),
                    "cut_reason": chunk.get("cut_reason"),
                }
            )
    if REALTIME_SUMMARY_ENABLED:
        summary_state = get_summary_state(meeting_id)
        current_source = transcript_source(meeting)
        if summary_state.get("status") not in {"queued", "updating"} and not summary_is_current(meeting, current_source):
            summary_state = {
                **summary_state,
                "status": "stale",
                "error": "",
                "source": current_source,
            }
    else:
        summary_state = {"status": "idle", "error": "", "updated_at": ""}
    return {
        "meeting_id": meeting_id,
        "chunk_counts": counts,
        "active_chunks": active_chunks,
        "has_active_chunks": bool(active_chunks),
        "summary": summary_state,
        "reprocess": latest_reprocess_state(meeting_id),
        "asr": asr_runtime.active_status(),
        "diarization": diarizer.status(),
        "speaker_tracking": speaker_tracker.status(),
        "llm": llm.describe(),
    }


def queue_summary_rebuild(
    meeting_id: str,
    background_tasks: Optional[BackgroundTasks] = None,
    reason: str = "",
) -> None:
    if not REALTIME_SUMMARY_ENABLED:
        summary_states.pop(meeting_id, None)
        return
    set_summary_state(meeting_id, "queued", reason)
    if background_tasks is not None:
        background_tasks.add_task(rebuild_summary_for_meeting, meeting_id, reason)
    else:
        asyncio.create_task(rebuild_summary_for_meeting(meeting_id, reason))


async def rebuild_summary_for_meeting(meeting_id: str, reason: str = "", force: bool = False) -> None:
    if not REALTIME_SUMMARY_ENABLED:
        summary_states.pop(meeting_id, None)
        return
    async with summary_lock:
        set_summary_state(meeting_id, "updating", reason)
        try:
            meeting = store.get_meeting(meeting_id)
            source = transcript_source(meeting)
            if not force and summary_is_current(meeting, source):
                set_summary_state(meeting_id, "done")
                return
            if source["segment_count"] == 0:
                store.set_summary(meeting_id, {}, source)
                set_summary_state(meeting_id, "done")
                return

            rebuilt = await asyncio.wait_for(
                summarizer.rebuild(meeting),
                timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
            )
            latest_meeting = store.get_meeting(meeting_id)
            latest_source = transcript_source(latest_meeting)
            if latest_source["hash"] != source["hash"]:
                rebuilt = await asyncio.wait_for(
                    summarizer.rebuild(latest_meeting),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            store.set_summary(meeting_id, rebuilt, latest_source)
            set_summary_state(meeting_id, "done")
        except asyncio.TimeoutError:
            meeting = store.get_meeting(meeting_id)
            fallback = fallback_incremental_summary(
                {},
                meeting.get("utterances") or meeting.get("segments") or [],
                f"summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds",
            )
            store.set_summary(meeting_id, fallback, transcript_source(meeting))
            set_summary_state(
                meeting_id,
                "error",
                f"Summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds.",
            )
        except Exception as exc:
            set_summary_state(meeting_id, "error", str(exc))


async def ensure_summary_current(meeting_id: str) -> Dict[str, Any]:
    meeting = store.get_meeting(meeting_id)
    if not REALTIME_SUMMARY_ENABLED:
        return meeting
    if summary_is_current(meeting):
        return meeting
    await rebuild_summary_for_meeting(meeting_id, "当前稿件已更新", force=True)
    return store.get_meeting(meeting_id)


async def send_changed_json(websocket: WebSocket, payload: Dict[str, Any], previous: str) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if encoded != previous:
        await websocket.send_json(payload)
    return encoded


@app.get("/api/models")
async def list_models() -> Dict[str, Any]:
    return model_catalog()


@app.websocket("/api/models/ws")
async def models_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    previous = ""
    try:
        while True:
            payload = await asyncio.to_thread(model_catalog)
            previous = await send_changed_json(websocket, payload, previous)
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        return


@app.post("/api/models/download")
async def start_model_download(payload: ModelDownloadRequest) -> Dict[str, Any]:
    kind = clean_identifier(payload.kind or "asr", "asr")
    model = (payload.model or "").strip()
    if kind == "asr" and model not in SUPPORTED_ASR_MODELS:
        raise HTTPException(status_code=400, detail="当前识别模型不可用。")
    if kind == "diarization" and model != PYANNOTE_COMMUNITY_MODEL_ID:
        raise HTTPException(status_code=400, detail="当前说话人分离模型不可用。")
    running = model_downloads.active_job(kind, model)
    if running:
        return {"job": running, "catalog": model_catalog()}

    job_id = uuid.uuid4().hex
    state = model_downloads.create_job(job_id, kind, model)
    asyncio.create_task(model_downloads.download_job(job_id, kind, model))
    return {"job": state, "catalog": model_catalog()}


@app.delete("/api/models/{kind}/{model}")
async def delete_model(kind: str, model: str, force: bool = False) -> Dict[str, Any]:
    clean_kind = clean_identifier(kind, "asr")
    clean_model = model.strip()
    running = model_downloads.active_job(clean_kind, clean_model)
    if running:
        model_downloads.request_cancel(str(running["id"]))
        model_downloads.cleanup_model_files(clean_kind, clean_model)
        return model_catalog()

    if clean_kind == "asr":
        if clean_model not in SUPPORTED_ASR_MODELS:
            raise HTTPException(status_code=400, detail="当前识别模型不可用。")
        runtime_flags = asr_runtime.model_runtime_flags(clean_model)
        if runtime_flags["loading"]:
            raise HTTPException(status_code=409, detail="识别模型正在加载，请稍后再删除。")
        if runtime_flags["loaded"]:
            if not force:
                raise HTTPException(status_code=409, detail="识别模型正在使用，确认后才能删除。")
            asr_runtime.unload_model(clean_model)
    elif clean_kind == "diarization" and clean_model == PYANNOTE_COMMUNITY_MODEL_ID:
        diarization_loading = diarizer.loading or bool(local_pyannote_diarizer and local_pyannote_diarizer.loading)
        diarization_loaded = diarizer.loaded or bool(local_pyannote_diarizer and local_pyannote_diarizer.loaded)
        if diarization_loading:
            raise HTTPException(status_code=409, detail="说话人分离模型正在加载，请稍后再删除。")
        if diarization_loaded:
            if not force:
                raise HTTPException(status_code=409, detail="说话人分离模型正在使用，确认后才能删除。")
            if diarizer.loaded:
                diarizer.unload()
            if local_pyannote_diarizer and local_pyannote_diarizer.loaded:
                local_pyannote_diarizer.unload()
    else:
        raise HTTPException(status_code=400, detail="当前模型不可用。")

    model_downloads.cleanup_model_files(clean_kind, clean_model)
    return model_catalog()


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "app": "VoiceMeeting",
        "version": "0.0.2",
        "api_revision": 2,
        "features": ["models.load", "native-save", "i18n"],
        "project_dir": str(PROJECT_DIR),
        "asr_model": ASR_MODEL,
        "asr": asr_runtime.active_status(),
        "diarization": diarizer.status(),
        "speaker_tracking": speaker_tracker.status(),
    }


@app.get("/api/vibearound/status")
async def vibearound_status() -> Dict[str, Any]:
    return await vibearound.status()


@app.get("/api/llm/status")
async def llm_status() -> Dict[str, Any]:
    return await llm.status()


@app.get("/api/llm/config")
async def get_llm_config() -> Dict[str, Any]:
    return llm.public_config()


@app.put("/api/llm/config")
async def update_llm_config(payload: LLMConfigRequest) -> Dict[str, Any]:
    try:
        return llm.save_config(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/prompts/config")
async def get_prompt_config() -> Dict[str, Any]:
    return prompt_settings.public_config()


@app.put("/api/prompts/config")
async def update_prompt_config(payload: PromptConfigRequest) -> Dict[str, Any]:
    return prompt_settings.save(payload.model_dump())


@app.get("/api/meetings")
async def list_meetings() -> Dict[str, Any]:
    return {"meetings": store.list_meetings()}


@app.post("/api/meetings")
async def create_meeting(payload: CreateMeetingRequest) -> Dict[str, Any]:
    return store.create_meeting(payload.title, payload.description)


@app.patch("/api/meetings/{meeting_id}")
async def update_meeting(meeting_id: str, payload: UpdateMeetingRequest) -> Dict[str, Any]:
    try:
        return attach_audio_payload(store.update_meeting_title(meeting_id, payload.title, payload.description))
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}")
async def get_meeting(meeting_id: str) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    audio = None
    if meeting.get("status") != "recording":
        audio = await ensure_single_meeting_audio(meeting_id, cleanup_chunks=False)
        meeting = store.get_meeting(meeting_id)
    return attach_audio_payload(meeting, audio)


@app.get("/api/meetings/{meeting_id}/versions")
async def list_meeting_versions(meeting_id: str) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
        return {
            "meeting_id": meeting_id,
            "active_version_id": meeting.get("active_version_id") or "auto",
            "versions": store.list_transcript_versions(meeting_id),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.post("/api/meetings/{meeting_id}/versions")
async def create_meeting_version(
    meeting_id: str,
    payload: CreateVersionRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    kind = "".join(ch for ch in (payload.kind or "manual").strip().lower() if ch.isalnum() or ch in "-_")
    kind = kind or "manual"
    version_id = payload.version_id or f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    label = payload.label or version_id
    try:
        meeting = store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=label,
            kind=kind,
            settings=payload.settings,
            parent_version_id=payload.parent_version_id,
            make_current=payload.make_current,
        )
        if payload.make_current:
            queue_summary_rebuild(meeting_id, background_tasks, "当前稿件已切换")
        return attach_audio_payload(meeting)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/meetings/{meeting_id}/versions/{version_id}/activate")
async def activate_meeting_version(
    meeting_id: str,
    version_id: str,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting = store.set_active_transcript_version(meeting_id, version_id)
        queue_summary_rebuild(meeting_id, background_tasks, "当前稿件已切换")
        return attach_audio_payload(meeting)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这份稿件，请刷新后再试。")


@app.delete("/api/meetings/{meeting_id}/versions/{version_id}")
async def delete_meeting_version(
    meeting_id: str,
    version_id: str,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting_before = store.get_meeting(meeting_id)
        meeting = store.delete_transcript_version(meeting_id, version_id)
        if (meeting_before.get("active_version_id") or "auto") == version_id:
            queue_summary_rebuild(meeting_id, background_tasks, "稿件已删除")
        return attach_audio_payload(meeting)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这份稿件，请刷新后再试。")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/meetings/{meeting_id}/versions/editable")
async def create_editable_meeting_version(
    meeting_id: str,
    payload: CreateEditableVersionRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting = store.create_editable_version(meeting_id, payload.source_version_id)
        queue_summary_rebuild(meeting_id, background_tasks, "已创建可编辑稿")
        return attach_audio_payload(meeting)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到会议或稿件，请刷新后再试。")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def editable_active_version_id(meeting_id: str) -> str:
    try:
        version = store.get_active_transcript_version(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    if version.get("kind") != "manual-edit":
        raise HTTPException(
            status_code=409,
            detail="请先创建可编辑副本，再修改文字或说话人。",
        )
    return str(version.get("id") or "auto")


@app.patch("/api/meetings/{meeting_id}/segments/{segment_id}")
async def update_meeting_segment(
    meeting_id: str,
    segment_id: str,
    payload: UpdateSegmentRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    if payload.text is None and payload.speaker is None:
        raise HTTPException(status_code=400, detail="没有可保存的修改。")
    version_id = editable_active_version_id(meeting_id)
    try:
        meeting = store.update_segment(
            meeting_id=meeting_id,
            version_id=version_id,
            segment_id=segment_id,
            text=payload.text,
            speaker=payload.speaker,
        )
        queue_summary_rebuild(meeting_id, background_tasks, "文字已修改")
        return attach_audio_payload(meeting)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这段文字，请刷新后再试。")


@app.post("/api/meetings/{meeting_id}/speakers/rename")
async def rename_meeting_speaker(
    meeting_id: str,
    payload: RenameSpeakerRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    version_id = editable_active_version_id(meeting_id)
    try:
        meeting = store.rename_speaker_in_version(
            meeting_id=meeting_id,
            version_id=version_id,
            old_label=payload.old_label,
            new_label=payload.new_label,
        )
        queue_summary_rebuild(meeting_id, background_tasks, "说话人已修改")
        return attach_audio_payload(meeting)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="当前稿件里没有找到这个说话人。")


@app.get("/api/meetings/{meeting_id}/reprocess")
async def list_reprocess_jobs(meeting_id: str) -> Dict[str, Any]:
    jobs = [
        state
        for state in reprocess_states.values()
        if state.get("meeting_id") == meeting_id
    ]
    jobs = sorted(jobs, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
    return {"meeting_id": meeting_id, "jobs": jobs}


@app.get("/api/meetings/{meeting_id}/reprocess/{job_id}")
async def get_reprocess_job(meeting_id: str, job_id: str) -> Dict[str, Any]:
    state = reprocess_states.get(job_id)
    if not state or state.get("meeting_id") != meeting_id:
        raise HTTPException(status_code=404, detail="找不到这个处理任务，请刷新后再试。")
    return state


@app.post("/api/meetings/{meeting_id}/reprocess")
async def start_reprocess_job(
    meeting_id: str,
    payload: ReprocessRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    level = clean_identifier(payload.level or "asr", "asr")
    requested_speaker_mode = resolve_speaker_mode(payload.speaker_mode)
    job_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    if level in {"asr", "rerun-asr", "full-asr"}:
        active_recording_config = recording_config.read()
        requested_language = str(active_recording_config.get("language") or payload.language or "auto").strip().lower()
        if requested_language not in SUPPORTED_ASR_LANGUAGES:
            raise HTTPException(status_code=400, detail="当前语言暂不支持，请换一种语言设置。")
        requested_model = normalize_asr_model(active_recording_config.get("asr_model"))
        try:
            asr_runtime.require_loaded_engine(requested_model)
        except ASRUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        version_id = f"asr-{requested_model}-{stamp}-{uuid.uuid4().hex[:6]}"
        store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=f"重新整理 {stamp}",
            kind="asr",
            settings={
                "model": requested_model,
                "language": requested_language,
                "speaker_mode": requested_speaker_mode,
                "source_version_id": source_version_id,
            },
            parent_version_id=source_version_id,
            make_current=False,
        )
        store.update_transcript_version_status(meeting_id, version_id, "queued")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="asr",
            status="queued",
            stage="queued",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=0,
            total=len(meeting.get("chunks") or []),
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_asr_version,
            meeting_id,
            job_id,
            version_id,
            source_version_id,
            requested_language,
            requested_model,
            requested_speaker_mode,
            payload.make_current,
        )
        return {"job": state}

    if level in {"speaker", "voiceprint", "diarization", "speakers"}:
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        version_id = f"speaker-{stamp}-{uuid.uuid4().hex[:6]}"
        store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=f"说话人分离 {stamp}",
            kind="speaker",
            settings={
                "source_version_id": source_version_id,
                "reset_speakers": payload.reset_speakers,
                "speaker_mode": requested_speaker_mode,
            },
            parent_version_id=source_version_id,
            make_current=False,
        )
        store.update_transcript_version_status(meeting_id, version_id, "queued")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="speaker",
            status="queued",
            stage="queued",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=0,
            total=len({segment.get("chunk_id") for segment in source_segments if segment.get("chunk_id")}),
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_speaker_version,
            meeting_id,
            job_id,
            version_id,
            source_version_id,
            requested_speaker_mode,
            payload.reset_speakers,
            payload.make_current,
        )
        return {"job": state}

    if level in {"repair", "llm-repair", "llm_repair", "polish"}:
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        version_id = f"repair-{stamp}-{uuid.uuid4().hex[:6]}"
        store.create_transcript_version(
            meeting_id=meeting_id,
            version_id=version_id,
            label=f"LLM 精修 {stamp}",
            kind="llm-repair",
            settings={"source_version_id": source_version_id},
            parent_version_id=source_version_id,
            make_current=False,
        )
        store.update_transcript_version_status(meeting_id, version_id, "queued")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="repair",
            status="queued",
            stage="queued",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=0,
            total=max(1, (len(source_segments) + 23) // 24),
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_llm_repair_version,
            meeting_id,
            job_id,
            version_id,
            source_version_id,
            payload.make_current,
        )
        return {"job": state}

    if level in {"merge", "remerge", "utterances"}:
        source_version_id = payload.source_version_id or meeting.get("active_version_id") or "auto"
        version_id = f"merge-{stamp}-{uuid.uuid4().hex[:6]}"
        try:
            store.get_transcript_version(meeting_id, source_version_id)
            source_segments = store.get_segments_for_version(meeting_id, source_version_id)
            meeting_snapshot = store.get_meeting(meeting_id)
            chunks = meeting_snapshot.get("chunks") or []
            chunk_map = {
                str(chunk.get("id") or ""): chunk
                for chunk in chunks
                if chunk.get("id")
            }
            utterances = build_utterances(source_segments, chunks)
            store.create_transcript_version(
                meeting_id=meeting_id,
                version_id=version_id,
                label=f"人声整理 {stamp}",
                kind="merge",
                settings={"source_version_id": source_version_id},
                parent_version_id=source_version_id,
                make_current=False,
            )
            inserted_total = 0
            for utterance in utterances:
                parts = utterance.get("parts") or []
                first_part = next(
                    (part for part in parts if part.get("chunk_id")),
                    None,
                )
                chunk_id = str(
                    (first_part or {}).get("chunk_id")
                    or (source_segments[0].get("chunk_id") if source_segments else "")
                    or ""
                )
                if not chunk_id:
                    continue
                base_ms = int((chunk_map.get(chunk_id) or {}).get("started_at_ms") or 0)
                start_ms = max(0, int(utterance.get("start_ms") or 0) - base_ms)
                end_ms = max(start_ms + 1, int(utterance.get("end_ms") or start_ms) - base_ms)
                inserted = store.add_segments(
                    meeting_id,
                    chunk_id,
                    [
                        {
                            "speaker": "" if is_unrecognized_text(utterance.get("text")) else utterance.get("speaker") or "Speaker",
                            "text": utterance.get("text") or "",
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "confidence": None,
                        }
                    ],
                    version_id=version_id,
                )
                inserted_total += len(inserted)
            if inserted_total == 0:
                inserted_total = store.copy_segments_to_version(meeting_id, source_version_id, version_id)
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "ready",
                {
                    "source_version_id": source_version_id,
                    "inserted_segments": inserted_total,
                    "source_segments": len(source_segments),
                },
            )
            if payload.make_current:
                store.set_active_transcript_version(meeting_id, version_id)
                queue_summary_rebuild(meeting_id, background_tasks, "段落已整理")
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到源稿件，请刷新后再试。")
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="merge",
            status="done",
            stage="done",
            version_id=version_id,
            source_version_id=source_version_id,
            progress=1,
            total=1,
            inserted_segments=inserted_total,
            error="",
            created_at=now_iso(),
        )
        return {"job": state}

    if level in {"notes", "summary", "final-notes", "final_notes"}:
        state = set_reprocess_state(
            job_id,
            id=job_id,
            meeting_id=meeting_id,
            level="notes",
            status="queued",
            stage="queued",
            version_id=meeting.get("active_version_id") or "auto",
            progress=0,
            total=1,
            error="",
            created_at=now_iso(),
        )
        background_tasks.add_task(
            reprocess_final_notes,
            meeting_id,
            job_id,
            payload.force_local,
        )
        return {"job": state}

    raise HTTPException(status_code=400, detail="当前处理方式不可用，请刷新后再试。")


@app.get("/api/meetings/{meeting_id}/runtime")
async def get_meeting_runtime(meeting_id: str) -> Dict[str, Any]:
    try:
        return meeting_runtime(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.websocket("/api/meetings/{meeting_id}/runtime/ws")
async def meeting_runtime_websocket(websocket: WebSocket, meeting_id: str) -> None:
    await websocket.accept()
    previous = ""
    try:
        while True:
            try:
                payload = await asyncio.to_thread(meeting_runtime, meeting_id)
            except KeyError:
                await websocket.send_json({"meeting_id": meeting_id, "error": "not_found"})
                return
            previous = await send_changed_json(websocket, payload, previous)
            await asyncio.sleep(0.8)
    except WebSocketDisconnect:
        return


@app.post("/api/meetings/{meeting_id}/ask")
async def ask_meeting(meeting_id: str, payload: MeetingAskRequest) -> Dict[str, Any]:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入要生成的内容。")
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    summary = meeting.get("summary") or {}
    has_summary = bool(summary.get("summary")) or any(
        bool(summary.get(key))
        for key in ("topics", "decisions", "action_items", "open_questions", "risks")
    )
    if not (meeting.get("utterances") or meeting.get("segments") or meeting.get("final_markdown") or has_summary):
        raise HTTPException(status_code=400, detail="这场会议还没有可用内容，录音或导入音频后再试。")

    try:
        answer = await asyncio.wait_for(
            summarizer.ask(
                meeting,
                prompt,
                [item.model_dump() for item in payload.history],
            ),
            timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="生成时间太久，已停止等待。请稍后重试。",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "answer": answer,
        "llm": llm.describe(),
    }


@app.delete("/api/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str) -> Dict[str, Any]:
    try:
        store.delete_meeting(meeting_id)
        summary_states.pop(meeting_id, None)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}/playback")
async def playback_manifest(meeting_id: str) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    audio = None
    if meeting.get("status") != "recording":
        audio = await ensure_single_meeting_audio(meeting_id, cleanup_chunks=False)
    payload = audio_payload(meeting_id, audio)
    if payload:
        duration_ms = int(payload.get("duration_ms") or 0)
        audio_url = str(payload["audio_url"])
        return {
            "meeting_id": meeting_id,
            "audio": payload,
            "chunks": [
                {
                    "id": f"meeting-audio-{duration_ms}",
                    "seq": 0,
                    "client_chunk_id": "",
                    "started_at_ms": 0,
                    "ended_at_ms": duration_ms,
                    "duration_ms": duration_ms,
                    "trim_start_ms": 0,
                    "playable_duration_ms": duration_ms,
                    "cut_reason": "完整会议音频",
                    "audio_url": audio_url,
                }
            ],
        }

    chunks = []
    last_end_ms: Optional[int] = None
    sorted_chunks = sorted(
        meeting.get("chunks") or [],
        key=lambda item: (
            item.get("started_at_ms") is None,
            item.get("started_at_ms") or item.get("seq") or 0,
        ),
    )
    for chunk in sorted_chunks:
        audio_path = existing_audio_path(chunk.get("wav_path"), chunk.get("audio_path"))
        if audio_path is None:
            continue

        duration_ms = chunk.get("duration_ms")
        if not duration_ms or int(duration_ms) <= 0:
            duration_ms = audio_duration_ms(audio_path)
        started_at_ms = chunk.get("started_at_ms")
        ended_at_ms = chunk.get("ended_at_ms")
        if started_at_ms is None:
            started_at_ms = last_end_ms or 0
        if (ended_at_ms is None or int(ended_at_ms or 0) <= int(started_at_ms or 0)) and duration_ms is not None:
            ended_at_ms = int(started_at_ms) + int(duration_ms)

        trim_start_ms = 0
        if last_end_ms is not None and started_at_ms is not None:
            trim_start_ms = max(0, int(last_end_ms) - int(started_at_ms))

        playable_duration_ms = None
        if ended_at_ms is not None and started_at_ms is not None:
            playable_duration_ms = max(0, int(ended_at_ms) - int(started_at_ms) - trim_start_ms)
            last_end_ms = max(last_end_ms or int(ended_at_ms), int(ended_at_ms))
        elif duration_ms is not None:
            playable_duration_ms = max(0, int(duration_ms) - trim_start_ms)
            last_end_ms = max(
                last_end_ms or int(started_at_ms) + int(duration_ms),
                int(started_at_ms) + int(duration_ms),
            )

        chunks.append(
            {
                "id": chunk["id"],
                "seq": chunk.get("seq"),
                "client_chunk_id": chunk.get("client_chunk_id"),
                "started_at_ms": started_at_ms,
                "ended_at_ms": ended_at_ms,
                "duration_ms": duration_ms,
                "trim_start_ms": trim_start_ms,
                "playable_duration_ms": playable_duration_ms,
                "cut_reason": chunk.get("cut_reason"),
                "audio_url": f"/api/meetings/{meeting_id}/chunks/{chunk['id']}/audio",
            }
        )

    return {"meeting_id": meeting_id, "chunks": chunks}


@app.get("/api/meetings/{meeting_id}/audio")
async def meeting_audio(meeting_id: str) -> FileResponse:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    path = meeting_audio_path(meeting_id)
    if not path.is_file() and meeting.get("status") != "recording":
        await ensure_single_meeting_audio(meeting_id, cleanup_chunks=False)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="完整会议音频还没有生成。")
    return FileResponse(str(path), media_type="audio/wav", filename=path.name)


@app.get("/api/meetings/{meeting_id}/audio/status")
async def meeting_audio_merge_status(meeting_id: str) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    path = meeting_audio_path(meeting_id)
    return {
        "meeting_id": meeting_id,
        **meeting_audio_status(meeting.get("chunks") or [], path),
    }


@app.get("/api/meetings/{meeting_id}/waveform")
async def meeting_waveform(meeting_id: str, bars: int = 256) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    path = meeting_audio_path(meeting_id)
    if not path.is_file() and meeting.get("status") != "recording":
        await ensure_single_meeting_audio(meeting_id, cleanup_chunks=False)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="完整会议音频还没有生成。")
    try:
        waveform = await asyncio.to_thread(meeting_audio_waveform, path, bars)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"音频波形生成失败：{exc}")
    return {
        "meeting_id": meeting_id,
        **waveform,
    }


@app.get("/api/meetings/{meeting_id}/chunks/{chunk_id}/audio")
async def chunk_audio(meeting_id: str, chunk_id: str) -> FileResponse:
    try:
        chunk = store.get_chunk(chunk_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这段音频，请刷新后再试。")
    if chunk.get("meeting_id") != meeting_id:
        raise HTTPException(status_code=404, detail="找不到这段音频，请刷新后再试。")

    path = existing_audio_path(chunk.get("wav_path"), chunk.get("audio_path"))
    if path is None:
        raise HTTPException(status_code=404, detail="找不到本地音频文件，可能已经被移动或删除。")
    media_type = mimetypes.guess_type(path.name)[0] or chunk.get("mime_type") or "application/octet-stream"
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@app.post("/api/meetings/{meeting_id}/chunks")
async def upload_chunk(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    duration_ms: Optional[str] = Form(None),
    language: Optional[str] = Form("mixed"),
    asr_model: Optional[str] = Form(None),
    speaker_mode: str = Form("voiceprint"),
    client_chunk_id: str = Form(""),
    started_at_ms: Optional[str] = Form(None),
    ended_at_ms: Optional[str] = Form(None),
    cut_reason: str = Form(""),
) -> Dict[str, Any]:
    try:
        store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    active_recording_config = recording_config.read()
    requested_language = str(active_recording_config.get("language") or language or "zh").strip().lower()
    if requested_language not in SUPPORTED_ASR_LANGUAGES:
        raise HTTPException(status_code=400, detail="当前语言暂不支持，请换一种语言设置。")
    requested_model = normalize_asr_model(active_recording_config.get("asr_model") or asr_model)
    requested_speaker_mode = resolve_speaker_mode(str(active_recording_config.get("speaker_mode") or speaker_mode))
    try:
        asr_engine = asr_runtime.require_loaded_engine(requested_model)
    except ASRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="这段音频为空，请重新录制或导入。")

    parsed_duration_ms = parse_optional_ms(duration_ms)
    parsed_started_at_ms = parse_optional_ms(started_at_ms)
    parsed_ended_at_ms = parse_optional_ms(ended_at_ms)

    chunk = store.create_chunk(
        meeting_id=meeting_id,
        audio_bytes=audio_bytes,
        filename=audio.filename or "chunk.webm",
        mime_type=audio.content_type or "",
        duration_ms=parsed_duration_ms,
        client_chunk_id=client_chunk_id,
        started_at_ms=parsed_started_at_ms,
        ended_at_ms=parsed_ended_at_ms,
        cut_reason=cut_reason,
    )
    try:
        transcription = await ChunkTranscriptionPipeline(
            store=store,
            audio_converter=asr_runtime.audio_converter,
            prompt_getter=prompt_settings.get,
            diarizer_for_mode=diarizer_for_mode,
            speaker_tracker=speaker_tracker,
        ).process(
            meeting_id=meeting_id,
            chunk=chunk,
            asr_engine=asr_engine,
            requested_language=requested_language,
            requested_model=requested_model,
            requested_speaker_mode=requested_speaker_mode,
        )
    except asyncio.CancelledError:
        store.update_chunk(chunk["id"], status="error", error="请求已取消。")
        raise
    except ASRUnavailable as exc:
        store.update_chunk(chunk["id"], status="error", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        store.update_chunk(chunk["id"], status="error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    if transcription.inserted:
        store.clear_final_markdown(meeting_id)
    meeting = store.get_meeting(meeting_id)
    inserted_ids = {str(segment.get("id") or "") for segment in transcription.inserted}
    inserted_segments = [
        segment
        for segment in meeting.get("segments") or []
        if str(segment.get("id") or "") in inserted_ids
    ]

    return {
        "chunk": store.get_chunk(chunk["id"]),
        "segments": inserted_segments,
        "utterances": meeting.get("utterances") or [],
        "speakers": meeting.get("speakers") or [],
        "summary": meeting["summary"],
        "runtime": meeting_runtime(meeting_id),
        "asr": {
            "requested_language": transcription.asr.get("requested_language"),
            "model": requested_model,
            "language": transcription.asr.get("language"),
            "language_probability": transcription.asr.get("language_probability"),
            "top_languages": transcription.asr.get("top_languages") or [],
            "multilingual": transcription.asr.get("multilingual"),
            "vad_segments": transcription.asr.get("vad_segments") or [],
        },
        "diarization": transcription.diarization,
        "speaker_tracking": transcription.speaker_tracking,
        "summary_status": "idle",
    }


def prepare_chunk_wav(chunk: Dict[str, Any]) -> Path:
    existing_wav = existing_audio_path(chunk.get("wav_path"))
    if existing_wav is not None:
        return existing_wav

    audio_path = existing_audio_path(chunk.get("audio_path"))
    if audio_path is None:
        raise RuntimeError("Audio file not found")

    if audio_path.suffix.lower() == ".wav":
        wav_path = audio_path.with_name(f"{audio_path.stem}_16k.wav")
    else:
        wav_path = audio_path.with_suffix(".wav")
    asr_runtime.audio_converter.convert_to_wav(audio_path, wav_path)
    store.update_chunk(chunk["id"], wav_path=str(wav_path))
    return wav_path


@app.post("/api/models/load")
async def load_model(payload: ModelDownloadRequest) -> Dict[str, Any]:
    kind = clean_identifier(payload.kind or "asr", "asr")
    if kind != "asr":
        raise HTTPException(status_code=400, detail="当前只支持加载语音识别模型。")
    try:
        requested_model = normalize_asr_model(payload.model)
        result = await asyncio.to_thread(asr_runtime.load_model_sync, requested_model)
        return {**result, "catalog": model_catalog()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型加载失败：{exc}")


def configured_asr_loaded(model_name: str) -> bool:
    return asr_runtime.is_loaded(model_name)


def recording_config_response() -> Dict[str, Any]:
    config = recording_config.read()
    return {
        "ok": True,
        "api_revision": 3,
        "config": config,
        "asr": asr_runtime.active_status(),
        "configured_model_loaded": configured_asr_loaded(config["asr_model"]),
        "load_error": recording_model_load_error,
        "asr_options": asr_model_options(),
        "diarization_options": diarization_model_options(),
    }


async def cached_llm_status() -> Dict[str, Any]:
    now = time.monotonic()
    cached_at = float(llm_status_cache.get("updated_at") or 0.0)
    cached_value = llm_status_cache.get("value")
    if isinstance(cached_value, dict) and cached_value and now - cached_at < LLM_STATUS_TTL_SECONDS:
        return cached_value
    async with llm_status_lock:
        now = time.monotonic()
        cached_at = float(llm_status_cache.get("updated_at") or 0.0)
        cached_value = llm_status_cache.get("value")
        if isinstance(cached_value, dict) and cached_value and now - cached_at < LLM_STATUS_TTL_SECONDS:
            return cached_value
        try:
            value = await llm.status()
        except Exception as exc:
            value = {
                "ok": False,
                **llm.describe(),
                "config": llm.public_config(),
                "error": str(exc),
            }
        llm_status_cache["updated_at"] = now
        llm_status_cache["value"] = value
        return value


async def app_status_response() -> Dict[str, Any]:
    return {
        **recording_config_response(),
        "llm": await cached_llm_status(),
    }


async def load_recording_model_from_config() -> None:
    global recording_model_load_error
    config = recording_config.read()
    try:
        await asyncio.to_thread(asr_runtime.load_model_sync, config["asr_model"])
        recording_model_load_error = ""
    except Exception as exc:
        recording_model_load_error = str(exc)


@app.on_event("startup")
async def load_recording_model_on_startup() -> None:
    asyncio.create_task(load_recording_model_from_config())


@app.get("/api/recording-config")
async def get_recording_config() -> Dict[str, Any]:
    return recording_config_response()


@app.put("/api/recording-config")
async def update_recording_config(payload: RecordingConfigRequest) -> Dict[str, Any]:
    global recording_model_load_error
    current = recording_config.read()
    updates = payload.model_dump(exclude_none=True)
    next_config = {**current, **updates}
    requested_model = normalize_asr_model(next_config.get("asr_model"))
    next_config["asr_model"] = requested_model
    model_changed = requested_model != current.get("asr_model")
    needs_load = model_changed or not configured_asr_loaded(requested_model)
    recording_config.save(next_config)
    try:
        if needs_load:
            await asyncio.to_thread(asr_runtime.load_model_sync, requested_model)
        recording_model_load_error = ""
    except Exception as exc:
        recording_model_load_error = str(exc)
        raise HTTPException(status_code=500, detail=f"模型加载失败：{exc}")
    return recording_config_response()


@app.websocket("/api/status/ws")
async def status_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    previous = ""
    try:
        while True:
            previous = await send_changed_json(websocket, await app_status_response(), previous)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


async def reprocess_asr_version(
    meeting_id: str,
    job_id: str,
    version_id: str,
    source_version_id: str,
    language: str,
    model_name: str,
    speaker_mode: str,
    make_current: bool,
) -> None:
    inserted_total = 0
    unrecognized_total = 0
    assigned_total = 0
    created_total = 0
    errors: list[str] = []
    try:
        asr_engine = asr_runtime.require_loaded_engine(model_name)
        meeting = store.get_meeting(meeting_id)
        audio = await ensure_single_meeting_audio(meeting_id, cleanup_chunks=False)
        if not audio:
            raise RuntimeError("完整会议音频还没有生成，请先停止录制或重新导入音频。")
        meeting = store.get_meeting(meeting_id)
        anchor_chunk = primary_timeline_chunk(meeting)
        audio_path = Path(str(audio["path"]))
        full_audio_chunk = {
            **anchor_chunk,
            "duration_ms": int(audio.get("duration_ms") or 0),
            "started_at_ms": 0,
            "ended_at_ms": int(audio.get("duration_ms") or 0),
            "cut_reason": "完整会议音频",
        }
        store.delete_segments_for_version(meeting_id, version_id)
        store.update_transcript_version_status(meeting_id, version_id, "running")
        set_reprocess_state(
            job_id,
            status="running",
            stage="整理文字",
            progress=0,
            total=1,
        )

        meeting_for_prompt = store.get_meeting(meeting_id)
        set_reprocess_state(
            job_id,
            stage="整理文字 1/1",
            progress=0,
            total=1,
            chunk_id=anchor_chunk.get("id"),
        )
        result = await asyncio.to_thread(
            asr_engine.transcribe,
            audio_path,
            language,
            asr_context_prompt(
                meeting_for_prompt,
                "",
                prompt_settings.get("asr_context", ""),
            ),
        )

        active_diarizer = diarizer_for_mode(speaker_mode)
        if active_diarizer is not None:
            try:
                turns = await asyncio.to_thread(active_diarizer.diarize, audio_path)
                if speaker_mode == "diarization":
                    result["segments"] = split_segments_by_turns(result["segments"], turns)
                    assigned_total += len(result["segments"])
                elif not speaker_tracker.enabled:
                    result["segments"] = assign_speakers(result["segments"], turns)
                    assigned_total += len(result["segments"])
            except DiarizationUnavailable as exc:
                errors.append(str(exc))

        if speaker_mode == "off":
            result["segments"] = clear_segment_speakers(result.get("segments") or [])

        use_speaker_tracking = speaker_tracker.enabled and speaker_mode in {
            "voiceprint",
            "diarization",
            "auto",
        }
        if use_speaker_tracking:
            try:
                result["segments"], speaker_result = await asyncio.to_thread(
                    speaker_tracker.assign_segments,
                    store,
                    meeting_id,
                    audio_path,
                    result["segments"],
                )
                assigned_total += int(speaker_result.get("assigned") or 0)
                created_total += int(speaker_result.get("created") or 0)
            except SpeakerTrackingUnavailable as exc:
                errors.append(str(exc))

        raw_segments = [segment_on_full_audio(segment) for segment in (result.get("segments") or [])]
        if not [segment for segment in raw_segments if str(segment.get("text") or "").strip()]:
            unrecognized_total += 1
        segments_to_store = [
            segment_on_full_audio(segment)
            for segment in segments_or_unrecognized(full_audio_chunk, raw_segments)
        ]
        inserted = store.add_segments(
            meeting_id,
            anchor_chunk["id"],
            segments_to_store,
            version_id=version_id,
        )
        inserted_total += len(inserted)
        set_reprocess_state(
            job_id,
            progress=1,
            inserted_segments=inserted_total,
            unrecognized_segments=unrecognized_total,
        )

        settings = {
            "model": model_name,
            "language": language,
            "speaker_mode": speaker_mode,
            "source_version_id": source_version_id,
            "inserted_segments": inserted_total,
            "unrecognized_segments": unrecognized_total,
            "assigned_segments": assigned_total,
            "created_speakers": created_total,
            "errors": errors[:8],
        }
        store.update_transcript_version_status(meeting_id, version_id, "ready", settings)
        if make_current:
            store.set_active_transcript_version(meeting_id, version_id)
        set_reprocess_state(
            job_id,
            status="done",
            stage="done",
            progress=1,
            inserted_segments=inserted_total,
            unrecognized_segments=unrecognized_total,
            assigned_segments=assigned_total,
            created_speakers=created_total,
        )
    except Exception as exc:
        try:
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "error",
                {"error": str(exc), "inserted_segments": inserted_total},
            )
        except Exception:
            pass
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


async def reprocess_speaker_version(
    meeting_id: str,
    job_id: str,
    version_id: str,
    source_version_id: str,
    speaker_mode: str,
    reset_speakers: bool,
    make_current: bool,
) -> None:
    inserted_total = 0
    created_total = 0
    assigned_total = 0
    errors: list[str] = []
    try:
        store.delete_segments_for_version(meeting_id, version_id)
        store.update_transcript_version_status(meeting_id, version_id, "running")
        if reset_speakers:
            store.delete_speakers(meeting_id)

        meeting = store.get_meeting(meeting_id)
        audio = await ensure_single_meeting_audio(meeting_id, cleanup_chunks=False)
        if not audio:
            raise RuntimeError("完整会议音频还没有生成，请先停止录制或重新导入音频。")
        meeting = store.get_meeting(meeting_id)
        anchor_chunk = primary_timeline_chunk(meeting)
        audio_path = Path(str(audio["path"]))
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        source_segments = [segment_on_full_audio(segment) for segment in source_segments]
        set_reprocess_state(
            job_id,
            status="running",
            stage="说话人分离",
            progress=0,
            total=1,
        )

        set_reprocess_state(
            job_id,
            stage="说话人分离 1/1",
            progress=0,
            total=1,
            chunk_id=anchor_chunk.get("id"),
        )
        assigned_segments = source_segments
        try:
            active_diarizer = diarizer_for_mode(speaker_mode)
            if active_diarizer is not None:
                turns = await asyncio.to_thread(active_diarizer.diarize, audio_path)
                if speaker_mode == "diarization":
                    assigned_segments = split_segments_by_turns(source_segments, turns)
                    assigned_total += len(assigned_segments)
                elif not speaker_tracker.enabled:
                    assigned_segments = assign_speakers(source_segments, turns)
                    assigned_total += len(assigned_segments)

            if speaker_mode == "off":
                assigned_segments = clear_segment_speakers(source_segments)

            use_speaker_tracking = speaker_tracker.enabled and speaker_mode in {
                "voiceprint",
                "diarization",
                "auto",
            }
            if use_speaker_tracking:
                assigned_segments, speaker_result = await asyncio.to_thread(
                    speaker_tracker.assign_segments,
                    store,
                    meeting_id,
                    audio_path,
                    assigned_segments,
                )
                assigned_total += int(speaker_result.get("assigned") or 0)
                created_total += int(speaker_result.get("created") or 0)
        except Exception as exc:
            errors.append(str(exc))

        assigned_segments = [segment_on_full_audio(segment) for segment in assigned_segments]
        inserted = store.add_segments(
            meeting_id,
            anchor_chunk["id"],
            assigned_segments,
            version_id=version_id,
        )
        inserted_total += len(inserted)
        set_reprocess_state(
            job_id,
            progress=1,
            inserted_segments=inserted_total,
            assigned_segments=assigned_total,
            created_speakers=created_total,
        )

        settings = {
            "source_version_id": source_version_id,
            "speaker_mode": speaker_mode,
            "reset_speakers": reset_speakers,
            "inserted_segments": inserted_total,
            "assigned_segments": assigned_total,
            "created_speakers": created_total,
            "errors": errors[:8],
        }
        store.update_transcript_version_status(meeting_id, version_id, "ready", settings)
        if make_current:
            store.set_active_transcript_version(meeting_id, version_id)
        set_reprocess_state(
            job_id,
            status="done",
            stage="done",
            progress=1,
            inserted_segments=inserted_total,
            assigned_segments=assigned_total,
            created_speakers=created_total,
            error="; ".join(errors[:2]) if errors else "",
        )
    except Exception as exc:
        try:
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "error",
                {"error": str(exc), "inserted_segments": inserted_total},
            )
        except Exception:
            pass
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


def repair_batches(segments: list[Dict[str, Any]]) -> list[list[Dict[str, Any]]]:
    batches: list[list[Dict[str, Any]]] = []
    current: list[Dict[str, Any]] = []
    current_chars = 0
    for segment in segments:
        text = str(segment.get("text") or "")
        if not text.strip() or is_unrecognized_text(text):
            continue
        if current and (len(current) >= 24 or current_chars + len(text) > 2600):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += len(text)
    if current:
        batches.append(current)
    return batches


async def reprocess_llm_repair_version(
    meeting_id: str,
    job_id: str,
    version_id: str,
    source_version_id: str,
    make_current: bool,
) -> None:
    inserted_total = 0
    changed_total = 0
    repair_errors: list[str] = []
    try:
        store.delete_segments_for_version(meeting_id, version_id)
        store.update_transcript_version_status(meeting_id, version_id, "running")
        source_segments = store.get_segments_for_version(meeting_id, source_version_id)
        batches = repair_batches(source_segments)
        set_reprocess_state(
            job_id,
            status="running",
            stage="LLM 精修",
            progress=0,
            total=len(batches),
        )

        for index, batch in enumerate(batches, start=1):
            set_reprocess_state(
                job_id,
                stage=f"精修 {index}/{len(batches)}",
                progress=index - 1,
                total=len(batches),
            )
            repair_map: Dict[str, str] = {}
            try:
                meeting_for_prompt = store.get_meeting(meeting_id)
                repair_map = await asyncio.wait_for(
                    summarizer.repair_segments(batch, meeting_for_prompt),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                repair_errors.append(str(exc))

            repaired_segments = []
            for segment in batch:
                repaired_text = repair_map.get(str(segment.get("id") or ""))
                if repaired_text and repaired_text.strip() != str(segment.get("text") or "").strip():
                    changed_total += 1
                    repaired_segments.append({**segment, "text": repaired_text.strip()})
                else:
                    repaired_segments.append(segment)

            by_chunk: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
            for segment in repaired_segments:
                if segment.get("chunk_id"):
                    by_chunk[str(segment["chunk_id"])].append(segment)
            for chunk_id, chunk_segments in by_chunk.items():
                inserted = store.add_segments(
                    meeting_id,
                    chunk_id,
                    chunk_segments,
                    version_id=version_id,
                )
                inserted_total += len(inserted)
            set_reprocess_state(
                job_id,
                progress=index,
                inserted_segments=inserted_total,
                changed_segments=changed_total,
            )

        settings = {
            "source_version_id": source_version_id,
            "inserted_segments": inserted_total,
            "changed_segments": changed_total,
            "repair_errors": repair_errors[:8],
        }
        store.update_transcript_version_status(meeting_id, version_id, "ready", settings)
        if make_current:
            store.set_active_transcript_version(meeting_id, version_id)
        set_reprocess_state(
            job_id,
            status="done",
            stage="done",
            progress=len(batches),
            inserted_segments=inserted_total,
            changed_segments=changed_total,
            error="; ".join(repair_errors[:2]) if repair_errors else "",
        )
    except Exception as exc:
        try:
            store.update_transcript_version_status(
                meeting_id,
                version_id,
                "error",
                {"error": str(exc), "inserted_segments": inserted_total},
            )
        except Exception:
            pass
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


async def reprocess_final_notes(meeting_id: str, job_id: str, force_local: bool) -> None:
    set_reprocess_state(job_id, status="running", stage="生成纪要", progress=0, total=1)
    try:
        meeting = store.get_meeting(meeting_id)
        source = transcript_source(meeting)
        if force_local:
            markdown = build_local_markdown(meeting)
        else:
            markdown = await asyncio.wait_for(
                summarizer.finalize(meeting),
                timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
            )
        store.set_final_markdown(meeting_id, markdown, source)
        set_reprocess_state(job_id, status="done", stage="done", progress=1)
    except asyncio.TimeoutError:
        set_reprocess_state(
            job_id,
            status="error",
            stage="timeout",
            progress=1,
            error=f"Final notes timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds.",
        )
    except Exception as exc:
        set_reprocess_state(job_id, status="error", stage="error", error=str(exc))


async def update_summary_for_segments(meeting_id: str, inserted: list[Dict[str, Any]]) -> None:
    if not REALTIME_SUMMARY_ENABLED:
        summary_states.pop(meeting_id, None)
        return
    async with summary_lock:
        set_summary_state(meeting_id, "updating")
        meeting: Optional[Dict[str, Any]] = None
        try:
            meeting = store.get_meeting(meeting_id)
            inserted_ids = {str(segment.get("id") or "") for segment in inserted}
            previous_source = transcript_source_without_segments(meeting, inserted_ids)
            current_source = transcript_source(meeting)
            existing_hash = str(meeting.get("summary_source_hash") or "")
            can_update_incrementally = (
                existing_hash == previous_source["hash"]
                or (
                    not existing_hash
                    and str(meeting.get("active_version_id") or "auto") == "auto"
                    and bool((meeting.get("summary") or {}).get("summary"))
                )
            )
            if existing_hash == current_source["hash"]:
                updated_summary = meeting["summary"]
            elif can_update_incrementally:
                updated_summary = await asyncio.wait_for(
                    summarizer.update(meeting["summary"], inserted, meeting),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            else:
                updated_summary = await asyncio.wait_for(
                    summarizer.rebuild(meeting),
                    timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
                )
            store.set_summary(meeting_id, updated_summary, current_source)
            set_summary_state(meeting_id, "done")
        except asyncio.TimeoutError:
            if meeting is None:
                meeting = store.get_meeting(meeting_id)
            fallback = fallback_incremental_summary(
                meeting["summary"],
                inserted,
                f"summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds",
            )
            store.set_summary(meeting_id, fallback, transcript_source(meeting))
            set_summary_state(
                meeting_id,
                "error",
                f"Summary task timed out after {int(SUMMARY_TASK_TIMEOUT_SECONDS)} seconds.",
            )
        except Exception as exc:
            set_summary_state(meeting_id, "error", str(exc))


@app.post("/api/meetings/{meeting_id}/finalize/stream")
async def finalize_meeting_stream(meeting_id: str, payload: FinalizeRequest) -> StreamingResponse:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    source = transcript_source(meeting)

    async def event_stream() -> Any:
        markdown = ""
        try:
            if payload.force_local:
                markdown = build_local_markdown(meeting)
                yield sse_event("replace", {"markdown": markdown})
            else:
                async for item in summarizer.finalize_stream(meeting):
                    kind = item.get("type")
                    if kind == "chunk":
                        text = str(item.get("text") or "")
                        if text:
                            yield sse_event("chunk", {"text": text})
                    elif kind == "replace":
                        markdown = str(item.get("markdown") or "")
                        yield sse_event("replace", {"markdown": markdown})
                    elif kind == "done":
                        if item.get("markdown"):
                            markdown = str(item["markdown"])

            if not markdown.strip() and payload.force_local:
                markdown = build_local_markdown(meeting)
                yield sse_event("replace", {"markdown": markdown})
            if not markdown.strip():
                raise RuntimeError("纪要生成失败：模型接口没有返回内容。")
            store.set_final_markdown(meeting_id, markdown, source)
            yield sse_event("done", {"meeting": attach_audio_payload(store.get_meeting(meeting_id))})
        except Exception as exc:
            yield sse_event("error", {"error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/meetings/{meeting_id}/finalize")
async def finalize_meeting(meeting_id: str, payload: FinalizeRequest) -> Dict[str, Any]:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")

    source = transcript_source(meeting)
    if payload.force_local:
        markdown = build_local_markdown(meeting)
    else:
        try:
            markdown = await asyncio.wait_for(
                summarizer.finalize(meeting),
                timeout=SUMMARY_TASK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="纪要生成超时，请稍后重试。")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
    store.set_final_markdown(meeting_id, markdown, source)
    return attach_audio_payload(store.get_meeting(meeting_id))


@app.post("/api/meetings/{meeting_id}/stop")
async def stop_meeting(meeting_id: str) -> Dict[str, Any]:
    try:
        store.update_meeting_status(meeting_id, "stopped")
        store.mark_interrupted_chunks(meeting_id, error="录音已停止，未完成的识别已取消。")
        audio = await ensure_single_meeting_audio(meeting_id, cleanup_chunks=True)
        return attach_audio_payload(store.get_meeting(meeting_id), audio)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")


@app.get("/api/meetings/{meeting_id}/export.md", response_class=PlainTextResponse)
async def export_markdown(meeting_id: str) -> str:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    if final_notes_current(meeting):
        return notes_only_markdown(meeting["final_markdown"]) + "\n"
    return build_local_markdown(meeting)


@app.get("/api/meetings/{meeting_id}/transcript.md", response_class=PlainTextResponse)
async def export_transcript_markdown(meeting_id: str) -> str:
    try:
        meeting = store.get_meeting(meeting_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这场会议，可能已经被删除。")
    return build_transcript_markdown(meeting)
