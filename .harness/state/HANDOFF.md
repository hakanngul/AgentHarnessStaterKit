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
- `check` never runs commands. `evidence run` runs only explicit argv, no shell;
  its hash detects later edits, not fabrication. CI is the trusted run.
- Hook role comes from HARNESS_ROLE, else agent_type if configured; unknown role is
  not blocked. Do not install the hook into this repository's own settings.
- Roles will be defined by authority and write scope, not by technology; stack
  specifics come from data-only stack packs (Faz 3).

## Remaining work

Faz 2 is in PR #2 under review. Then Faz 3 (stack packs) and Faz 4 (upgrade) per the
plan. Do not claim stack packs, upgrade or a sandbox exist. The hook does not see Bash.

## Resume

Use this checkout; no worktree unless asked. `uv sync --frozen --group dev`, then
`uv run --frozen pytest` and `uv run --frozen harness check .`. Expected edits to
generated files show as `validate` warnings; do not refresh hashes to hide them.
