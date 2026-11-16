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

- Lifecycle (docs/plans/lifecycle.md): phases live in the config's optional `workflow`;
  the gate reuses the depends_on graph, no separate grouping. `init` writes no workflow
  yet and must not emit `workflow: null`. Direction agreed with the user: the core stays
  Python; an orchestrator app drives the phases later, engine behind an adapter.

- `harness next` (schedule.py) is the orchestrator core: stateless, derived from the
  validated ledger. Parallel start depends on task `scope`; comparison errs toward
  holding a task back. User decisions and open questions are in lifecycle.md.
- The user asked agents to state their own opinion and flag wrong or risky requests
  (AGENTS.md). The user prefers diagrams to long text.

## Remaining work

L3 is in PR #4 (T-6). Next: L2 (default workflow and phase roles in `init`), then L4
(engine adapter, background agents, desktop app: Python backend, React UI, shell
decided last). Open decision: branch layout for parallel work. Faz 3 (stack packs) and
Faz 4 (upgrade) remain per the team-system plan. Do not claim a default workflow, an
orchestrator process, a UI, stack packs, upgrade or a sandbox exist. The hook does not
see Bash. Shared files (.harness/state/, lockfiles) are not protected from parallel
tasks.

## Resume

Use this checkout; no worktree unless asked. `uv sync --frozen --group dev`, then
`uv run --frozen pytest` and `uv run --frozen harness check .`. Expected edits to
generated files show as `validate` warnings; do not refresh hashes to hide them.

## Engine adapter (T-7)

- `harness run TASK` runs one READY/IN_PROGRESS task through the Claude Agent SDK
  (optional extra `engine`). Role policy is default-deny, enforced as a PreToolUse hook
  and `can_use_tool`; shell quoting/expansion, chaining, MCP and web tools are refused.
- It is a guardrail, not a sandbox (README limits). Real-session behaviour is
  unverified: hook precedence over allow rules, subagent hook coverage, cost after
  resume, 429 as exception. Try it on a throwaway project first.
- Next: orchestrator loop that uses `harness next` and `run_task`, records evidence,
  requests review, enforces the phase gate, commits per task (layout A).

## Orchestrator wave (T-8)

- `harness wave` = plan().start -> claim (READY->IN_PROGRESS, compare-and-set) -> parallel
  run_task -> settle: IN_REVIEW only with acceptance and NEW passing evidence, BLOCKED on
  failure, READY on usage limit. Interrupts release claimed tasks to READY.
- Not done: reviewer agent run, commit per task (layout A), phase gate in the loop.
- Trial project for a real-session run: scratchpad `trial/` (throwaway, not in the repo).
