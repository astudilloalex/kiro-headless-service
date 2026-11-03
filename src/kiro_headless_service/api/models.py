from typing import Any, Literal

from pydantic import BaseModel

from kiro_headless_service.domain.model import State


class JobView(BaseModel):
    id: str
    state: State
    session_id: str | None
    correlation_id: str
    metadata: dict[str, str]
    created_at: str
    updated_at: str
    attempts: int
    cancel_requested: bool
    error: str | None


class SessionView(BaseModel):
    id: str
    closed: bool
    active_job: str | None
    metadata: dict[str, str]
    created_at: str
    updated_at: str
    context_strategy: Literal["service_history"]


class ResultView(BaseModel):
    format: Literal["text", "json", "json_schema"]
    text: str | None = None
    data: Any = None
    usage: dict[str, Any]


class EventView(BaseModel):
    id: int
    version: int
    type: str
    data: dict[str, Any]
    created_at: str


class EventPage(BaseModel):
    items: list[EventView]
    next_cursor: int


class MessageView(BaseModel):
    seq: int
    role: Literal["user", "assistant"]
    content: Any
    job_id: str | None
    status: State


class MessagePage(BaseModel):
    items: list[MessageView]
    next_cursor: int


class ArtifactView(BaseModel):
    id: str
    name: str
    media_type: str
    size: int
    sha256: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail
