"""Versioned configuration and installation receipt contracts."""

import datetime as dt
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agent_harness import __version__

SchemaVersion = Annotated[int, Field(strict=True, ge=1, le=1)]


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: SchemaVersion = 1
    harness_version: Literal[__version__] = __version__
    project_name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=4000)] = ""
    adapter: Literal["claude-code"] = "claude-code"
    technologies: list[str] = Field(default_factory=list)

    @field_validator("project_name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("project_name must be nonblank and contain no control characters")
        return value


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: SchemaVersion = 1
    harness_version: Literal[__version__] = __version__
    files: dict[str, Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]]


LedgerVersion = Annotated[int, Field(strict=True, ge=1, le=1)]
# pattern \S: at least one non-whitespace character (search semantics in Pydantic and JSON Schema).
Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
Name = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]
TaskId = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$", max_length=40)]
DecisionId = Annotated[str, Field(pattern=r"^DEC-[1-9][0-9]*$", max_length=40)]
Reference = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$", max_length=40)]
CriterionId = Annotated[str, Field(pattern=r"^AC[1-9][0-9]*$")]
EvidenceId = Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]


def _no_numeric_date(value: object) -> object:
    # Lax date parsing would turn 0 into 1970-01-01; require a real date or ISO string.
    if isinstance(value, int | float):
        raise ValueError("date must be YYYY-MM-DD, not a number")
    return value


LooseDate = Annotated[dt.date, Field(strict=False), BeforeValidator(_no_numeric_date)]


def _nonblank(value: str | None) -> bool:
    return bool(value and value.strip())


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: CriterionId
    text: Text
    verified_by: list[EvidenceId] = Field(default_factory=list)


class Evidence(BaseModel):
    """A recorded check. The ledger stores claims; `check` validates their consistency."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: EvidenceId
    kind: Literal["command", "manual"] = "command"
    command: Annotated[str, Field(max_length=2000)] | None = None
    cwd: Annotated[str, Field(min_length=1, max_length=500)] = "."
    exit_code: int | None = None
    result: Literal["pass", "fail"] | None = None
    summary: Annotated[str, Field(max_length=4000)] = ""
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")] | None = None
    output: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    recorded_at: LooseDate | None = None
    # "harness" means `harness evidence run` executed the command and wrote the output.
    recorded_by: Literal["agent", "harness"] = "agent"

    @model_validator(mode="after")
    def check_kind(self) -> "Evidence":
        if self.kind == "command":
            if not _nonblank(self.command) or self.exit_code is None:
                raise ValueError("command evidence requires command and exit_code")
            if self.result is not None:
                raise ValueError("command evidence uses exit_code, not result")
        else:
            if self.result is None or not _nonblank(self.summary):
                raise ValueError("manual evidence requires result and summary")
            if self.exit_code is not None:
                raise ValueError("manual evidence uses result, not exit_code")
        if self.recorded_by == "harness" and (
            self.kind != "command" or self.output is None or self.output_sha256 is None
        ):
            raise ValueError("harness-recorded evidence needs kind command, output and hash")
        return self

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 if self.kind == "command" else self.result == "pass"


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["pending", "approved", "changes_requested", "unavailable"] = "pending"
    by: Annotated[str, Field(max_length=120)] = ""
    notes: Annotated[str, Field(max_length=4000)] = ""


TaskStatus = Literal["BACKLOG", "READY", "IN_PROGRESS", "BLOCKED", "IN_REVIEW", "DONE", "CANCELLED"]


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: LedgerVersion = 1
    id: TaskId
    title: Name
    owner: Name
    status: TaskStatus
    goal: Annotated[str, Field(max_length=4000)] = ""
    scope: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list)
    depends_on: list[Reference] = Field(default_factory=list)
    reason: Annotated[str, Field(max_length=4000)] = ""
    acceptance: list[AcceptanceCriterion] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    review: Review = Field(default_factory=Review)
    log: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if value.startswith("DEC-"):
            raise ValueError("the DEC- prefix is reserved for decisions")
        return value


class DecisionOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: Annotated[str, Field(pattern=r"^[A-Z]$")]
    text: Text


class Decision(BaseModel):
    """A product decision that only the user may make."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: LedgerVersion = 1
    id: DecisionId
    question: Text
    options: Annotated[list[DecisionOption], Field(min_length=2, max_length=10)]
    recommendation: Annotated[str, Field(max_length=4000)] = ""
    status: Literal["PENDING", "DECIDED", "WITHDRAWN"] = "PENDING"
    answer: Annotated[str, Field(max_length=4000)] = ""
    decided_by: Annotated[str, Field(max_length=120)] = ""
    decided_at: LooseDate | None = None

    @model_validator(mode="after")
    def check_state(self) -> "Decision":
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("option ids must be unique")
        decided = (_nonblank(self.answer), _nonblank(self.decided_by), self.decided_at)
        if self.status == "DECIDED" and not all(decided):
            raise ValueError("DECIDED requires answer, decided_by and decided_at")
        if self.status == "PENDING" and any(decided):
            raise ValueError("PENDING decisions must not record an answer")
        return self


class Analysis(BaseModel):
    root: str
    technologies: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Report(BaseModel):
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class LedgerReport(Report):
    summary: dict[str, int] = Field(default_factory=dict)
