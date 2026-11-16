# Progress

## Completed

- 0.1.0: CLI init/analyze/validate/doctor, templates, manifest, 86 tests.
- Faz 1 (unreleased): task/decision ledger models, JSON schemas, `harness check`,
  templates and docs, CI step, this repository's own ledger (T-1). 187 tests.
- Faz 2 (unreleased, done): `harness evidence run`, roles + `harness scope`,
  PreToolUse hook + `harness hook install`.
- Yaşam döngüsü L1 (unreleased, PR #3): `workflow` phases in the config, `phase` on
  tasks, phase gate in `harness check` (T-5, DONE, review approved). 380 tests.
- Yaşam döngüsü L3 (unreleased, PR #4): `harness next` (T-6, DONE, review approved).
  441 tests.

## Current work

T-6 is DONE in PR #4, awaiting merge. Three open threads from PR #2 remain for the
maintainer (subprocess audit finding, credential redaction scope, settings.json
concurrency).

## Next step

L2: default workflow (discovery, spec, design, implement, qa, release, fix) and phase
roles in `init`, with model and effort per role. Then L4: engine adapter and background
agents. The user must first choose the branch layout for parallel work.

## 2026-11-16 T-7 engine adapter

DONE. 656 unit tests, ruff clean, 3 independent review rounds (all fixed). Never run
against a real Claude session. Next: orchestrator loop.

## 2026-11-16 T-8 orchestrator wave

DONE. 684 unit tests, ruff clean, three review rounds (last APPROVED). Not run against a
real Claude session. Next: real-session trial (needs permission), then FastAPI server.

## 2026-11-16 T-9 independent reviewer

DONE. 734 unit tests, ruff clean, two review rounds (last APPROVED). Real session trial:
wave then review took two tasks to DONE (before the read-only fix). Next: scope check and
commit per task, then FastAPI.

## 2026-11-16 T-10 commit per task

DONE. 795 unit tests, ruff clean, four review rounds (last APPROVED, low findings accepted).
Real trial: four tasks committed one per task on a group branch. Next: FastAPI server.

## 2026-11-16 T-11 read-only local server

DONE. 859 unit tests, ruff clean, three review rounds (last APPROVED). `harness serve`:
loopback only, bearer token, strict Host check, read-only JSON (plan, check, tasks).
Verified against a real uvicorn process. Next: actions (wave, review, commit) and live events.
