# Progress

## Completed

- 0.1.0: CLI init/analyze/validate/doctor, templates, manifest, 86 tests.
- Faz 1 (unreleased): task/decision ledger models, JSON schemas, `harness check`,
  templates and docs, CI step, this repository's own ledger (T-1). 187 tests.
- Faz 2 (unreleased, done): `harness evidence run`, roles + `harness scope`,
  PreToolUse hook + `harness hook install`.
- Yaşam döngüsü L1 (unreleased, PR #3): `workflow` phases in the config, `phase` on
  tasks, phase gate in `harness check` (T-5). 379 tests.

## Current work

T-5 in PR #3. Three open threads from PR #2 remain for the maintainer (subprocess audit
finding, credential redaction scope, settings.json concurrency).

## Next step

User decisions for L2/L4 (see TASK.md), then L2: default workflow and phase roles in
`init`. Faz 3 (stack packs) per docs/plans/team-system.md is still planned.
