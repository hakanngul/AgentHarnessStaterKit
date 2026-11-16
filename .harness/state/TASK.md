# Current task

Status: Yaşam döngüsü L1 (T-5) DONE, review approved; PR #3 awaits merge.
Faz 2 (T-2..T-4) is merged.

## Goal and scope

L1 of docs/plans/lifecycle.md: optional `workflow` phases in the config, `phase` on
tasks, and a phase gate in `harness check`. Out of scope: default workflow and phase
roles in `init` (L2), orchestrator command (L3), engine adapter and UI (L4), stack
packs (Faz 3), upgrade (Faz 4), version bump.

## Acceptance criteria

AC1–AC6 in `.harness/tasks/T-5.yaml`.

## Required checks

Run through `harness evidence run <task> -- <command>`: uv run --frozen pytest -q;
ruff check .; ruff format --check .; python -m build --no-isolation; harness
validate . --json; harness check . --json. Wheel smoke test in a fresh venv for
template or generation changes.

## Product decisions and blockers

Needed from the user before L2 and L4: the default phase list and roles `init` should
install; how the engine runs (local `claude -p` on the user's login, or an API key);
the form of the management UI. No hook is installed in this repository.
