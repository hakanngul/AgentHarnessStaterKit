# Evidence

Structured evidence for Faz 1 lives in `.harness/tasks/T-1.yaml`; raw outputs are
in `.harness/evidence/T-1/`. Environment: managed cloud container, Python 3.12,
repository root as working directory, 2026-11-09.

| Check | Result |
| --- | --- |
| uv run --frozen ruff check . | exit 0 |
| uv run --frozen ruff format --check . | exit 0 |
| uv run --frozen pytest -q | exit 0; 167 passed, none skipped |
| uv run --frozen python -m build --no-isolation | exit 0; sdist and wheel |
| uv run --frozen harness validate . --json | exit 0; edited generated files are warnings |
| uv run --frozen harness check . --json | exit 0; 1 task checked |
| Wheel in fresh venv outside checkout | init/validate/check 0; bad ledger made check exit 1 |

Mutation check: four rules deliberately broken (review status, dependency state,
failing evidence, cycle reporting); tests caught all four. Regression tests for the
review findings: 11 failed against commit a3ce0a9, all pass after the fixes.

## Independent review

A read-only reviewer agent (no command execution) reported 1 high, 4 medium and
4 low findings: missing self-ledger and evidence (H1); uncaught OSError for long
paths and unlistable directories, RecursionError for deep YAML and long dependency
chains (M1–M4); case-sensitive self-approval, template example contradicting its
own rule, numeric dates accepted, duplicate dependencies (L1–L4). All were fixed
with tests. Re-review outcome is recorded in T-1.yaml `review`.

## Not tested

Python 3.11 and 3.13 run only in CI. Real Claude Code sessions are not tested.
`check` does not verify that commits exist or that recorded commands really ran.
Reviewer identity in `review.by` is self-declared text.

Earlier 0.1.0 evidence (2026-10-25, 86 tests) is in Git history.
