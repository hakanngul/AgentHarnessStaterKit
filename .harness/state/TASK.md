# Current task

Status: Yaşam döngüsü L3 (T-6, `harness next`) DONE, review approved; PR #4 awaits merge.
L1 (T-5) is merged.

## Goal and scope

L3 of docs/plans/lifecycle.md: `harness next` computes from the ledger which tasks can
start, which wait and why, and which can run in parallel. Out of scope: default
workflow and phase roles in `init` (L2), engine adapter, background agents and the
desktop app (L4), stack packs (Faz 3), upgrade (Faz 4), version bump.

## Acceptance criteria

AC1–AC6 in `.harness/tasks/T-6.yaml`.

## Required checks

Run through `harness evidence run <task> -- <command>`: uv run --frozen pytest -q;
ruff check .; ruff format --check .; python -m build --no-isolation; harness
validate . --json; harness check . --json. Wheel smoke test in a fresh venv for
template, generation or CLI changes.

## Product decisions and blockers

User decisions are recorded in docs/plans/lifecycle.md. Open: branch layout for
parallel work (one branch per parallel group, or a worktree per task). No hook is
installed in this repository.
