# Handoff

## Context

Agent Harness Starter Kit: Python CLI that installs a Claude Code development
harness. Goal (docs/plans/team-system.md): a stack-independent AI development team
where mistakes cannot be hidden. Faz 1 added the task/decision ledger and
`harness check`. Read README.md, docs/ARCHITECTURE.md, the plan, then T-1.yaml.

## Decisions

- Ledger lives in `.harness/tasks/` and `.harness/decisions/`; not created by init
  and not in the manifest, so existing 0.1.0 installs keep validating.
- Version stays 0.1.0: config pins harness_version; bump together with upgrade (Faz 4).
- `check` never runs commands; evidence is a claim checked for consistency only.
- Roles will be defined by authority and write scope, not by technology; stack
  specifics come from data-only stack packs (Faz 3).

## Remaining work

Finish T-1 review (PR #1). Then Faz 2–5 per the plan. Do not claim hooks, stack
packs, upgrade or evidence recording exist.

## Resume

Use this checkout; no worktree unless asked. `uv sync --frozen --group dev`, then
`uv run --frozen pytest` and `uv run --frozen harness check .`. Expected edits to
generated files show as `validate` warnings; do not refresh hashes to hide them.
