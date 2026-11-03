from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class State(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({State.SUCCEEDED, State.FAILED, State.TIMED_OUT, State.CANCELLED})


def transition(current: str, target: State) -> None:
    allowed = {
        State.QUEUED: {State.RUNNING, State.CANCELLED},
        State.RUNNING: TERMINAL | {State.QUEUED},
    }
    if target not in allowed.get(State(current), set()):
        raise ValueError(f"Invalid transition {current} -> {target}")


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class RuntimeFailure(Exception):
    def __init__(self, code: str, *, safe_to_retry: bool = False):
        super().__init__(code)
        self.code, self.safe_to_retry = code, safe_to_retry


@dataclass(frozen=True)
class Artifact:
    name: str
    data: bytes
    media_type: str = "application/octet-stream"


@dataclass
class RuntimeResult:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass(frozen=True)
class Consumer:
    id: str
    profiles: frozenset[str]
    agents: frozenset[str]
    mcp: frozenset[str]
    max_running: int = 1
    max_queued: int = 100
