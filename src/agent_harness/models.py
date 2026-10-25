"""Versioned configuration and installation receipt contracts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
