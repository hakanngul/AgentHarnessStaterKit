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
