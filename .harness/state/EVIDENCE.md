# Evidence

Structured evidence lives in `.harness/tasks/T-*.yaml`; raw outputs are in
`.harness/evidence/<task>/`. Since Faz 2, command evidence is recorded by
`harness evidence run` itself (`recorded_by: harness`, output hash, commit).

## Faz 2 (T-2, T-3, T-4) — final tree dd1bf1c, 2026-11-10

| Check | Result |
| --- | --- |
| uv run --frozen pytest -q (T-2 E6, T-3 E6, T-4 E7) | exit 0; 322 passed |
| uv run --frozen ruff check . (T-4 E8) | exit 0 |
| uv run --frozen ruff format --check . (T-4 E9) | exit 0 |
| uv run --frozen python -m build --no-isolation (T-4 E10) | exit 0 |
| uv run --frozen harness validate . --json (T-4 E11) | exit 0; edited generated files are warnings |
| Wheel in fresh venv outside checkout (T-4 E12, manual) | hook denies .claude and .CLAUDE, allows src; evidence redacts; check/validate 0 |
| uv run --frozen harness check . | exit 0 |

Mutation checks broke hash verification, output cleanup, deny ordering, symlink
rejection, agent_type gating, the deny decision and install idempotency; tests caught
each. Every review finding has a regression test that fails on the code before its fix.

## Independent review (Faz 2)

Read-only reviewer subagent, code reading only. Round 1: 3 medium (oversized input
bypass, case-variant bypass, evidence data loss) and 9 low findings. Round 2: approved
with follow-ups. Round 3 (post-approval delta): 1 blocking — leftover child processes
could write into recorded evidence after hashing; fixed by streaming into a private
unlinked temp file and killing the process group on timeout. Final verdict on dd1bf1c:
APPROVED. PR bots: Sourcery (24 findings on an older commit; real ones fixed, 4 threads
left for the maintainer), SonarCloud (S3516, S2612; fixed, gate green).
Maintainer decisions (2026-11-10): the subprocess audit finding is by design and is
suppressed inline with its justification; redaction stays heuristic; settings.json
concurrency is not addressed.

## Yaşam döngüsü L1 (T-5, PR #3)

Final tree 6614863, recorded with `harness evidence run` (E8–E11): pytest 380 passed;
ruff check and format clean; `harness validate . --json` ok. E4 build and E6
`harness check . --json` are from d8e9e7e; later commits changed tests and docs only.
E7 (manual): wheel from d8e9e7e in a fresh venv outside the checkout; implement without
a spec task rejected, rejected while the spec is IN_PROGRESS, accepted once it is DONE;
task without phase rejected under `require_phase`. That run first found `init` writing
`workflow: null` (fixed in 1ca1628 with a test that fails on the previous code).
Mutation check (not in the ledger): removing the gate call fails 18 tests; dropping
upward propagation in `_reachable_phases` fails 1.
Review: two read-only reviewer subagents, both APPROVED; details in T-5.yaml. The last
commit of test and doc fixes was not re-reviewed.

## Yaşam döngüsü L3 (T-6, PR #4)

Final tree 438b301, recorded with `harness evidence run`: pytest 441 passed; ruff check
and format clean; build ok; `harness validate . --json` ok. Manual E19: wheel from
438b301 in a fresh venv outside the checkout; two tasks with separate scopes start
together, a task with an absolute-path scope is held, a task waiting on a decision is
listed with it, and a READY task that skips its spec makes `next` exit 1.
Mutation check (not in the ledger, run on dcaafa1): six mutations of schedule.py each
fail at least one test.
Review: round 1 CHANGES REQUESTED (scope comparison false negatives), round 2 APPROVED;
details in T-6.yaml. The last commit of low-severity fixes was not re-reviewed.

## Not tested / limits

- Python 3.11 and 3.13 run only in CI. Windows is not tested; process-group kill is POSIX.
- Real Claude Code sessions are not tested; whether subagent events carry agent_type is
  not confirmed by docs. The hook does not see Bash writes.
- `next` trusts declared scopes and does not protect shared files (.harness/state/,
  lockfiles) from parallel tasks; no orchestrator process exists yet.
- The phase gate is only as strong as the config: loosening the workflow is not
  detected, and a workflow has not been tried on a real project yet.
- Evidence is produced locally: the hash detects later edits, not fabrication.
  Redaction is heuristic. Reviewer identity in `review.by` is self-declared.

Faz 1 evidence (T-1, 187 tests) and 0.1.0 evidence are in Git history and T-1.yaml.
