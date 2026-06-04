from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CreateMeetingRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class UpdateMeetingRequest(BaseModel):
    title: str
    description: Optional[str] = None


class CreateVersionRequest(BaseModel):
    version_id: Optional[str] = None
    label: Optional[str] = None
    kind: str = "manual"
    parent_version_id: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    make_current: bool = False


class CreateEditableVersionRequest(BaseModel):
    source_version_id: Optional[str] = None


class UpdateSegmentRequest(BaseModel):
    text: Optional[str] = None
    speaker: Optional[str] = None


class RenameSpeakerRequest(BaseModel):
    old_label: str
    new_label: str


class AskMessage(BaseModel):
    role: str = "user"
    content: str


class MeetingAskRequest(BaseModel):
    prompt: str
    history: list[AskMessage] = Field(default_factory=list)


class ReprocessRequest(BaseModel):
    level: str = "asr"
    language: Optional[str] = "auto"
    asr_model: Optional[str] = None
    speaker_mode: Optional[str] = "voiceprint"
    make_current: bool = True
    force_local: bool = False
    source_version_id: Optional[str] = None
    reset_speakers: bool = False


class FinalizeRequest(BaseModel):
    force_local: bool = False


class ModelDownloadRequest(BaseModel):
    kind: str = "asr"
    model: str


class RecordingConfigRequest(BaseModel):
    language: Optional[str] = None
    asr_model: Optional[str] = None
    speaker_mode: Optional[str] = None
    max_segment_ms: Optional[int] = None
    input_gain: Optional[float] = None


class LLMLiteLLMRequest(BaseModel):
    preset: Optional[str] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class LLMConfigRequest(BaseModel):
    provider: str = "litellm"
    litellm: LLMLiteLLMRequest = Field(default_factory=LLMLiteLLMRequest)


class PromptConfigRequest(BaseModel):
    prompts: Dict[str, str] = Field(default_factory=dict)
