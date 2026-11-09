# Current task

Status: Faz 1 implemented; see `.harness/tasks/T-1.yaml` for structured status.

## Goal and scope

Faz 1 of docs/plans/team-system.md: structured task, evidence and decision ledger
plus `harness check`. Out of scope: roles/hooks (Faz 2), stack packs (Faz 3),
upgrade (Faz 4), version bump.

## Acceptance criteria

AC1–AC7 in docs/plans/team-system.md, mirrored in `.harness/tasks/T-1.yaml`.

## Required checks

From repository root: uv run --frozen pytest; uv run --frozen ruff check .;
uv run --frozen ruff format --check .; uv run --frozen python -m build --no-isolation;
uv run --frozen harness check .; installed wheel smoke outside the checkout.

## Product decisions and blockers

Version stays 0.1.0 until upgrade exists (config pins harness_version). No blockers.
