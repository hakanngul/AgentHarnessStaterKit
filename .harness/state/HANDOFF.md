# Handoff

## Context

First implementation of Agent Harness Starter Kit 0.1.0. Four CLI commands work:
init, analyze, validate, doctor. Read README.md and docs/ARCHITECTURE.md.

## Decisions

- Python >=3.11; Typer/Rich/Pydantic/Jinja2/PyYAML; pytest; hatchling packaging.
- Python is the setup engine; Claude Code is an optional separately installed runtime.
- Refuse file collisions; warn on edits; no destructive force/upgrade or shell execution.
- Keep agent entry points small; task/progress/evidence/handoff are persistent memory.
- Markdown guides behavior; it does not provide sandboxing or enforced completion.

## Remaining work

See docs/ROADMAP.md: safe upgrade/sync, structured task evidence contracts, dynamic
role selection and additional adapters. Claude CLI/authentication and independent
review were not tested. Do not claim optional roadmap features already exist.

## Resume

Use this checkout; no worktree unless explicitly requested. Set
UV_CACHE_DIR=/workspace/.cache/uv; run uv sync --frozen --group dev. Read AGENTS.md,
TASK.md, PROGRESS.md, EVIDENCE.md. Start a new task with its own acceptance criteria.
Expected modifications to these state files are reported as warnings by validate;
do not overwrite them or refresh their original hashes just to suppress warnings.
