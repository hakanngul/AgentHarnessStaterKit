# Current task

Status: Faz 2 DONE (T-2, T-3, T-4); review approved; PR #2 awaits merge.
Structured status lives in `.harness/tasks/T-2.yaml` .. `T-4.yaml`.

## Goal and scope

Faz 2 of docs/plans/team-system.md: `harness evidence run`, role write scopes in
config with `harness scope`, and a Claude Code PreToolUse hook with `harness hook
install`. Out of scope: stack packs (Faz 3), upgrade (Faz 4), version bump.

## Acceptance criteria

Faz 2 AC1–AC6 in docs/plans/team-system.md, mirrored in the T-2..T-4 task files.

## Required checks

Run through `harness evidence run <task> -- <command>`: uv run --frozen pytest -q;
ruff check .; ruff format --check .; python -m build --no-isolation; harness
validate . --json. Then `harness check .`.

## Product decisions and blockers

No hook is installed in this repository: the session must not edit its own Claude
settings. Open for the maintainer: three PR threads listed in PROGRESS.md.
