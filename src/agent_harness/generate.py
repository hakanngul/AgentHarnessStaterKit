"""Render packaged templates and install without overwriting project files."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

from agent_harness.analyze import analyze
from agent_harness.filesystem import HarnessError, managed_path, project_root
from agent_harness.models import HarnessConfig, Manifest, RoleConfig

TEMPLATES = {
    "AGENTS.md": "agents.md.j2",
    "CLAUDE.md": "claude.md.j2",
    ".claude/agents/lead.md": "lead.md.j2",
    ".claude/agents/developer.md": "developer.md.j2",
    ".claude/agents/reviewer.md": "reviewer.md.j2",
    ".claude/skills/development/SKILL.md": "skill.md.j2",
    ".claude/rules/verification.md": "verification.md.j2",
    ".harness/state/TASK.md": "task.md.j2",
    ".harness/state/PROGRESS.md": "progress.md.j2",
    ".harness/state/EVIDENCE.md": "evidence.md.j2",
    ".harness/state/HANDOFF.md": "handoff.md.j2",
    "docs/architecture/README.md": "architecture.md.j2",
    "docs/decisions/README.md": "decisions.md.j2",
    "docs/plans/README.md": "plans.md.j2",
}
CONFIG_PATH = ".harness/config.yaml"
MANIFEST_PATH = ".harness/manifest.json"
EXPECTED_FILES = frozenset([CONFIG_PATH, *TEMPLATES])
# Starting point only; projects edit these. Paths are project-relative globs.
DEFAULT_ROLES = {
    "lead": RoleConfig(
        description="Plans tasks and records decisions; does not edit source.",
        write=[".harness/tasks/**", ".harness/decisions/**", ".harness/state/**", "docs/**"],
    ),
    "developer": RoleConfig(
        description="Implements tasks inside the project.",
        write=["**"],
        deny=[".harness/config.yaml", ".harness/manifest.json", ".claude/**", ".github/**"],
    ),
    "reviewer": RoleConfig(
        description="Records review results; does not edit source.",
        write=[".harness/tasks/**", ".harness/state/**"],
    ),
}


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def render(config: HarnessConfig) -> dict[str, str]:
    environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    context = config.model_dump()
    # Encode descriptive data as JSON so it remains distinguishable from instructions.
    context["project_json"] = json.dumps(
        {"name": config.project_name, "description": config.description}, ensure_ascii=False
    )
    bundle = files("agent_harness").joinpath("templates")
    rendered = {
        target: environment.from_string(bundle.joinpath(source).read_text(encoding="utf-8")).render(
            **context
        )
        for target, source in TEMPLATES.items()
    }
    rendered[CONFIG_PATH] = yaml.safe_dump(config.model_dump(), sort_keys=False, allow_unicode=True)
    manifest = Manifest(files={name: sha256(text.encode()) for name, text in rendered.items()})
    rendered[MANIFEST_PATH] = manifest.model_dump_json(indent=2) + "\n"
    return rendered


def initialize(
    path: Path, *, name: str | None = None, description: str = "", dry_run: bool = False
) -> list[str]:
    root = project_root(path, required=False)
    technologies = analyze(root).technologies if root.exists() else []
    config = HarnessConfig(
        project_name=name if name is not None else root.name,
        description=description,
        technologies=technologies,
        roles=DEFAULT_ROLES,
    )
    outputs = render(config)
    # Complete preflight before creating any directories or files.
    for relative in outputs:
        target = managed_path(root, relative)
        if target.exists():
            raise HarnessError(f"Refusing to overwrite existing path: {relative}")
    if dry_run:
        return sorted(outputs)
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for relative, text in outputs.items():
            target = managed_path(root, relative)
            missing = [p for p in (target.parent, *target.parent.parents) if not p.exists()]
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                created_files.append(target)
                stream.write(text)
    except (OSError, HarnessError):
        for target in reversed(created_files):
            target.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            directory.rmdir()
        raise
    return sorted(outputs)
