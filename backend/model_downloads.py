from __future__ import annotations

import asyncio
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .model_registry import (
    FUNASR_MODEL_DIR,
    FUNASR_MODEL_MARKER_DIR,
    PYANNOTE_COMMUNITY_MODEL_ID,
    PYANNOTE_COMMUNITY_REPO_ID,
    PYANNOTE_MODEL_DIR,
    SUPPORTED_ASR_MODELS,
    asr_model_backend,
    asr_model_cache_dir,
    asr_model_cache_paths,
    asr_model_repos,
    funasr_model_marker_path,
    mark_funasr_model_installed,
)


ASREngineResolver = Callable[[str], Any]


class ModelDownloadCancelled(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _friendly_model_error(exc: Exception, kind: str = "") -> str:
    text = str(exc)
    lowered = text.lower()
    if kind == "asr" and (
        "repository not found" in lowered
        or "401" in text
        or "403" in text
        or "gated" in lowered
        or "token" in lowered
    ):
        return "ASR 模型下载失败，可能是下载源不可用或网络访问被拦截。请刷新模型列表后重试。"
    if "401" in text or "403" in text or "gated" in lowered or "token" in lowered:
        return "模型需要授权。请先配置 VOICE_MEETING_HF_TOKEN/HF_TOKEN，并确认已接受模型条款。"
    if "connection" in lowered or "timeout" in lowered or "network" in lowered:
        return "联网下载失败，请检查网络后重试。"
    return text or type(exc).__name__


class ModelDownloadManager:
    def __init__(self, asr_engine_for_model: ASREngineResolver) -> None:
        self.asr_engine_for_model = asr_engine_for_model
        self.states: Dict[str, Dict[str, Any]] = {}

    def hf_token_available(self) -> bool:
        return bool(self._read_hf_token())

    def create_job(self, job_id: str, kind: str, model: str) -> Dict[str, Any]:
        return self.set_state(
            job_id,
            id=job_id,
            key=self.job_key(kind, model),
            kind=kind,
            model=model,
            status="queued",
            stage="queued",
            progress=0.0,
            error="",
            created_at=now_iso(),
        )

    def job_key(self, kind: str, model: str) -> str:
        return f"{kind}:{model}"

    def set_state(self, job_id: str, **fields: Any) -> Dict[str, Any]:
        current = dict(self.states.get(job_id) or {})
        current.update(fields)
        current["updated_at"] = now_iso()
        self.states[job_id] = current
        return current

    def active_job(self, kind: str, model: str) -> Optional[Dict[str, Any]]:
        key = self.job_key(kind, model)
        jobs = [
            state
            for state in self.states.values()
            if state.get("key") == key and state.get("status") in {"queued", "running"}
        ]
        if not jobs:
            return None
        return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]

    def latest_job(self, kind: str, model: str) -> Optional[Dict[str, Any]]:
        key = self.job_key(kind, model)
        jobs = [state for state in self.states.values() if state.get("key") == key]
        if not jobs:
            return None
        return sorted(jobs, key=lambda item: str(item.get("updated_at") or ""))[-1]

    def recent_states(self, limit: int = 24) -> list[Dict[str, Any]]:
        return sorted(
            self.states.values(),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )[:limit]

    def request_cancel(self, job_id: str) -> Dict[str, Any]:
        return self.set_state(
            job_id,
            status="cancelling",
            stage="正在取消",
            cancel_requested=True,
            error="",
        )

    def cleanup_model_files(self, kind: str, model: str) -> None:
        paths: list[Path] = []
        lock_paths: list[Path] = []
        if kind == "asr":
            if asr_model_backend(model) == "funasr":
                marker = funasr_model_marker_path(model)
                try:
                    marker.unlink()
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
                remaining = [
                    path
                    for path in FUNASR_MODEL_MARKER_DIR.glob("*.json")
                    if path.is_file()
                ] if FUNASR_MODEL_MARKER_DIR.exists() else []
                if not remaining:
                    paths = [FUNASR_MODEL_DIR]
            else:
                paths = asr_model_cache_paths(model)
                cache_dir = asr_model_cache_dir(model)
                lock_paths = [cache_dir / ".locks" / path.name for path in paths]
        elif kind == "diarization" and model == PYANNOTE_COMMUNITY_MODEL_ID:
            paths = [PYANNOTE_MODEL_DIR]

        for path in paths + lock_paths:
            try:
                if path.exists():
                    shutil.rmtree(path)
            except Exception:
                continue

    async def download_job(self, job_id: str, kind: str, model: str) -> None:
        self.set_state(job_id, status="running", stage="准备下载", progress=0.0)
        try:
            if kind == "asr":
                if model not in SUPPORTED_ASR_MODELS:
                    raise ValueError("当前识别模型不可用。")
                if asr_model_backend(model) == "funasr":
                    self.set_state(job_id, stage="加载 FunASR 模型", progress=0.2)
                    engine = self.asr_engine_for_model(model)
                    await asyncio.to_thread(engine.load)
                    mark_funasr_model_installed(model)
                    self.set_state(job_id, stage="整理模型文件", progress=0.95)
                    await asyncio.to_thread(engine.unload)
                    self._raise_if_cancelled(job_id)
                    self.set_state(
                        job_id,
                        status="done",
                        stage="已安装",
                        progress=1.0,
                        error="",
                        finished_at=now_iso(),
                    )
                    return
                cache_dir = asr_model_cache_dir(model)
                cache_dir.mkdir(parents=True, exist_ok=True)
                repo_id = asr_model_repos(model)[0]
                self.set_state(job_id, repo_id=repo_id, stage="连接下载源")
                await asyncio.to_thread(
                    self._download_hf_repo_files,
                    job_id,
                    repo_id,
                    cache_dir=cache_dir,
                )
            elif kind == "diarization" and model == PYANNOTE_COMMUNITY_MODEL_ID:
                await asyncio.to_thread(
                    self._download_hf_repo_files,
                    job_id,
                    PYANNOTE_COMMUNITY_REPO_ID,
                    local_dir=PYANNOTE_MODEL_DIR,
                    token=self._read_hf_token(),
                )
            else:
                raise ValueError("当前模型不可用。")
            self.set_state(
                job_id,
                status="done",
                stage="已安装",
                progress=1.0,
                error="",
                finished_at=now_iso(),
            )
        except ModelDownloadCancelled:
            self.cleanup_model_files(kind, model)
            self.set_state(
                job_id,
                status="cancelled",
                stage="已取消",
                progress=0.0,
                error="",
                cancel_requested=True,
                finished_at=now_iso(),
            )
        except Exception as exc:
            self.set_state(
                job_id,
                status="error",
                stage="下载失败",
                progress=0.0,
                error=_friendly_model_error(exc, kind),
                finished_at=now_iso(),
            )

    def _read_hf_token(self) -> Optional[str]:
        return (
            os.environ.get("VOICE_MEETING_HF_TOKEN")
            or os.environ.get("HUGGINGFACE_TOKEN")
            or os.environ.get("HF_TOKEN")
        )

    def _cancel_requested(self, job_id: str) -> bool:
        return bool(self.states.get(job_id, {}).get("cancel_requested"))

    def _raise_if_cancelled(self, job_id: str) -> None:
        if self._cancel_requested(job_id):
            raise ModelDownloadCancelled("model download cancelled")

    def _download_hf_repo_files(
        self,
        job_id: str,
        repo_id: str,
        *,
        cache_dir: Optional[Path] = None,
        local_dir: Optional[Path] = None,
        token: Optional[str] = None,
    ) -> None:
        from huggingface_hub import HfApi, hf_hub_download, snapshot_download
        from tqdm.auto import tqdm

        manager = self

        class CancelAwareTqdm(tqdm):
            current_file = ""
            file_index = 0
            total_files = 1
            base_downloaded_bytes = 0
            total_repo_bytes = 0
            last_emit_at = 0.0

            @classmethod
            def configure(
                cls,
                *,
                filename: str = "",
                file_index: int = 0,
                total_files: int = 1,
                base_downloaded_bytes: int = 0,
                total_repo_bytes: int = 0,
            ) -> None:
                cls.current_file = filename
                cls.file_index = file_index
                cls.total_files = max(1, total_files)
                cls.base_downloaded_bytes = max(0, base_downloaded_bytes)
                cls.total_repo_bytes = max(0, total_repo_bytes)
                cls.last_emit_at = 0.0

            def update(self, n: int = 1):
                manager._raise_if_cancelled(job_id)
                result = super().update(n)
                total_repo_bytes = type(self).total_repo_bytes
                if total_repo_bytes <= 0:
                    return result

                now = time.monotonic()
                current_file_bytes = max(0, int(getattr(self, "n", 0) or 0))
                downloaded = min(
                    total_repo_bytes,
                    type(self).base_downloaded_bytes + current_file_bytes,
                )
                if now - type(self).last_emit_at >= 0.25 or downloaded >= total_repo_bytes:
                    type(self).last_emit_at = now
                    manager.set_state(
                        job_id,
                        stage=f"下载 {type(self).file_index}/{type(self).total_files}",
                        progress=min(0.98, downloaded / total_repo_bytes),
                        file=type(self).current_file,
                        downloaded_bytes=downloaded,
                        total_bytes=total_repo_bytes,
                    )
                return result

        def current_incomplete_bytes() -> int:
            if cache_dir is None:
                return 0
            blobs_dir = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}" / "blobs"
            if not blobs_dir.exists():
                return 0
            sizes: list[int] = []
            for path in blobs_dir.glob("*.incomplete"):
                try:
                    sizes.append(path.stat().st_size)
                except OSError:
                    continue
            return max(sizes, default=0)

        def start_cache_progress_poll(
            *,
            filename: str,
            file_index: int,
            total_files: int,
            base_downloaded_bytes: int,
            total_repo_bytes: int,
        ) -> tuple[threading.Event, Optional[threading.Thread]]:
            stop_event = threading.Event()
            if cache_dir is None or total_repo_bytes <= 0:
                return stop_event, None

            def poll() -> None:
                last_downloaded = -1
                while not stop_event.wait(0.35):
                    if manager._cancel_requested(job_id):
                        return
                    current_file_bytes = current_incomplete_bytes()
                    downloaded = min(
                        total_repo_bytes,
                        max(0, base_downloaded_bytes) + max(0, current_file_bytes),
                    )
                    if downloaded <= last_downloaded:
                        continue
                    last_downloaded = downloaded
                    manager.set_state(
                        job_id,
                        stage=f"下载 {file_index}/{total_files}",
                        progress=min(0.98, downloaded / total_repo_bytes),
                        file=filename,
                        downloaded_bytes=downloaded,
                        total_bytes=total_repo_bytes,
                    )

            thread = threading.Thread(target=poll, daemon=True)
            thread.start()
            return stop_event, thread

        self._raise_if_cancelled(job_id)
        try:
            api = HfApi()
            info = api.model_info(repo_id, files_metadata=True, token=token)
            siblings = [
                sibling
                for sibling in (getattr(info, "siblings", None) or [])
                if getattr(sibling, "rfilename", None)
            ]
        except Exception:
            siblings = []

        if not siblings:
            self.set_state(job_id, stage="下载模型文件", progress=0.1)
            self._raise_if_cancelled(job_id)
            kwargs: Dict[str, Any] = {"repo_id": repo_id, "token": token}
            if cache_dir is not None:
                kwargs["cache_dir"] = str(cache_dir)
            if local_dir is not None:
                local_dir.mkdir(parents=True, exist_ok=True)
                kwargs["local_dir"] = str(local_dir)
            CancelAwareTqdm.configure()
            kwargs["tqdm_class"] = CancelAwareTqdm
            snapshot_download(**kwargs)
            self._raise_if_cancelled(job_id)
            self.set_state(job_id, stage="整理模型文件", progress=0.95)
            return

        files = [
            sibling
            for sibling in siblings
            if not str(getattr(sibling, "rfilename", "")).endswith("/")
        ]
        total_files = max(1, len(files))
        total_bytes = sum(int(getattr(item, "size", 0) or 0) for item in files)
        downloaded_bytes = 0
        for index, item in enumerate(files, start=1):
            self._raise_if_cancelled(job_id)
            filename = str(getattr(item, "rfilename"))
            size = int(getattr(item, "size", 0) or 0)
            progress = (
                min(0.98, downloaded_bytes / total_bytes)
                if total_bytes > 0
                else min(0.98, (index - 1) / total_files)
            )
            self.set_state(
                job_id,
                stage=f"下载 {index}/{total_files}",
                progress=progress,
                file=filename,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
            )
            CancelAwareTqdm.configure(
                filename=filename,
                file_index=index,
                total_files=total_files,
                base_downloaded_bytes=downloaded_bytes,
                total_repo_bytes=total_bytes,
            )
            kwargs = {
                "repo_id": repo_id,
                "filename": filename,
                "token": token,
            }
            if cache_dir is not None:
                kwargs["cache_dir"] = str(cache_dir)
            if local_dir is not None:
                local_dir.mkdir(parents=True, exist_ok=True)
                kwargs["local_dir"] = str(local_dir)
            kwargs["tqdm_class"] = CancelAwareTqdm
            stop_event, poll_thread = start_cache_progress_poll(
                filename=filename,
                file_index=index,
                total_files=total_files,
                base_downloaded_bytes=downloaded_bytes,
                total_repo_bytes=total_bytes,
            )
            try:
                hf_hub_download(**kwargs)
            finally:
                stop_event.set()
                if poll_thread is not None:
                    poll_thread.join(timeout=1.0)
            self._raise_if_cancelled(job_id)
            downloaded_bytes += size
            self.set_state(
                job_id,
                stage=f"下载 {index}/{total_files}",
                progress=(
                    min(0.98, downloaded_bytes / total_bytes)
                    if total_bytes > 0
                    else min(0.98, index / total_files)
                ),
                file=filename,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
            )
