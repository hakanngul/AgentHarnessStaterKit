# Evidence

Structured evidence for Faz 1 lives in `.harness/tasks/T-1.yaml`; raw outputs are
in `.harness/evidence/T-1/`. Environment: managed cloud container, Python 3.12,
repository root as working directory, 2026-11-09.

| Check | Result |
| --- | --- |
| uv run --frozen ruff check . | exit 0 |
| uv run --frozen ruff format --check . | exit 0 |
| uv run --frozen pytest -q | exit 0; 187 passed, none skipped |
| uv run --frozen python -m build --no-isolation | exit 0; sdist and wheel |
| uv run --frozen harness validate . --json | exit 0; edited generated files are warnings |
| uv run --frozen harness check . --json | exit 0; 1 task checked |
| Wheel in fresh venv outside checkout | init/validate/check 0; bad ledger made check exit 1 |

Mutation check: four rules deliberately broken (review status, dependency state,
failing evidence, cycle reporting); tests caught all four. Regression tests for the
review findings fail on the code before each fix (11 on a3ce0a9, 2 on f477ce6,
11 on c5a45a0, 5 on 31cbb17)
and pass after the fixes.

## Independent review

A read-only reviewer agent (no command execution) reported 1 high, 4 medium and
4 low findings: missing self-ledger and evidence (H1); uncaught OSError for long
paths and unlistable directories, RecursionError for deep YAML and long dependency
chains (M1–M4); case-sensitive self-approval, template example contradicting its
own rule, numeric dates accepted, duplicate dependencies (L1–L4). All were fixed
with tests. Re-review found one more crash (invalid YAML date, oversized integer);
fixed with tests. Sourcery's PR review then found 6 more real issues (blank names,
evidence cwd, duplicate YAML keys, NUL paths, entry and byte caps); fixed with tests,
and one regression it caused (YAML merge keys) was caught and fixed. Final verdict
on 3c92c6f: APPROVED, no blocking items. The reviewer read code
only and did not rerun the checks above.

## Not tested

Python 3.11 and 3.13 run only in CI. Real Claude Code sessions are not tested.
`check` does not verify that commits exist or that recorded commands really ran.
Reviewer identity in `review.by` is self-declared text.

Earlier 0.1.0 evidence (2026-10-25, 86 tests) is in Git history.
