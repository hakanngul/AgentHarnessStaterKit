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

## Not tested / limits

- Python 3.11 and 3.13 run only in CI. Windows is not tested; process-group kill is POSIX.
- Real Claude Code sessions are not tested; whether subagent events carry agent_type is
  not confirmed by docs. The hook does not see Bash writes.
- Evidence is produced locally: the hash detects later edits, not fabrication.
  Redaction is heuristic. Reviewer identity in `review.by` is self-declared.

Faz 1 evidence (T-1, 187 tests) and 0.1.0 evidence are in Git history and T-1.yaml.
