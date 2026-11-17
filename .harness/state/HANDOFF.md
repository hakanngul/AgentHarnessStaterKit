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

## Independent reviewer (T-9)

- `harness review` / `wave --review`: reviewer runs read-only (hook denies every write and
  `harness evidence run`), ends with `VERDICT:` plus `AC<n>: E<n>` lines; code writes review,
  verified_by and status. Approval needs harness-recorded passing evidence and no failing
  latest run per command. Any change to the task during review blocks it.
- Not done: scope check by code (reviewer cannot run git), commit per task, phase gate in
  the loop. Known: tampering detected not reverted; forged `recorded_by: harness` evidence.

## Commit per task (T-10)

- `harness commit`: one commit per DONE task on a group branch (layout A); scope checked by
  code, any unowned change blocks all commits; most specific scope owns a file; CANCELLED
  scopes own nothing but keep their ledger files. Nothing is pushed.
- Known limits: rev-parse failure treated as unborn branch; `git switch -c` on an unborn
  repo untested; wildcard scopes over-own; run the team on a clean working tree.
- Next: FastAPI server, React UI, desktop shell; phase gate in the loop.

## Local server (T-11)

- `harness serve [PATH] [--port]` needs the `server` extra. Read-only: plan, check, tasks.
  Loopback bind only, Bearer token on everything but /api/health, strict Host regex.
- Known limits: no TLS; any local process holding the token has access; plan/check error
  texts may contain file paths; no way yet to hand the token to a browser UI.
- Next: T-12 actions as background jobs (wave, review, commit) with SSE events; the UI
  token hand-off must be designed before the React UI.

## Server jobs (T-12)

- `POST /api/jobs` wave | review | commit; one job at a time; SSE events; cancel; shutdown with
  a 3 s grace period. Same functions as the CLI, no new decision logic.
- Known: no lock between CLI and server (wave claim race in `run_wave`: a run that loses a claim
  releases the winner's task; shared git index in commit; git subprocesses have no timeout).
  Plan T-13: one project run lock held for the whole wave/review/commit, plus a git timeout.
- Not done: browser UI client (token hand-off is designed in T-14: URL fragment + fetch streaming, no cookie), real-session trial
  through the server, job deadline for a stalled engine.

## T-13 (proje çalışma kilidi)

`.harness/.run.lock` üzerinde `flock` (runlock.py); CLI run/wave/review/commit ve her sunucu işi alır,
`evidence run` ve salt-okunur komutlar almaz (bir wave'in içindeki agent onu çağırır). Bilinen sınırlar README'de
"Proje çalışma kilidi": `git clean -fd` kilit dosyasını silebilir, `kill -9` sonrası yetim git, iptalde çalışan
to_thread yazımı, post-commit hook zaman aşımı "blocked" raporlar, flock NFS'te güvenilmez, Windows yolu denenmedi.
Sıradaki iş: React arayüzü. Token devri T-14'te karara bağlandı: URL fragment + Authorization başlıklı fetch akışı (EventSource başlık gönderemez, çerez yok). Arayüz hash yönlendirme kullanmalı (sunucuda history fallback yok).
