"""Engine-neutral run requests and events. Nothing here imports an engine SDK."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class RunRequest:
    """One agent session: a prompt run in a project with a role's engine settings."""

    cwd: Path
    role: str
    prompt: str
    model: str | None = None
    effort: str | None = None
    resume: str | None = None  # session id to continue
    max_turns: int | None = None


@dataclass(frozen=True)
class Started:
    session_id: str


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolUse:
    name: str
    input: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDenied:
    name: str
    reason: str


@dataclass(frozen=True)
class RateLimit:
    status: Literal["allowed", "allowed_warning", "rejected"]
    resets_at: int | None = None  # Unix time


@dataclass(frozen=True)
class Finished:
    ok: bool
    session_id: str | None = None
    result: str = ""
    error: str = ""
    turns: int = 0
    cost_usd: float | None = None
    http_status: int | None = None


Event = Started | Text | ToolUse | ToolDenied | RateLimit | Finished
