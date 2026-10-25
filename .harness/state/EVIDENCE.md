# Evidence

Validated on 2026-10-25 in the managed cloud environment, Python 3.12.14.
Working directory unless stated otherwise: /workspace/AgentHarnessStaterKit.

| Check | Result |
| --- | --- |
| uv sync --frozen --group dev | exit 0; pinned project environment installed |
| .venv/bin/pytest -q | exit 0; 86 passed, no skipped/xfail tests |
| .venv/bin/ruff check . | exit 0 |
| .venv/bin/ruff format --check . | exit 0 |
| .venv/bin/python -m build --no-isolation | exit 0; sdist and wheel built |
| Isolated wheel install, temporary cwd outside checkout | version/analyze/dry-run/init/validate/doctor passed; repeated init rejected with exit 1; packaged role/skill files present |
| harness doctor on this repository | schema/files/Python/Git pass; Claude CLI absent, reported as optional warning |

The 86 tests cover CLI workflows, every generated-file collision and missing file,
user-file preservation, invalid YAML/schema versions, manifest layout/hashes,
symlink/path traversal rejection, partial-write rollback, unexecuted manifest commands,
root-only technology detection, UTF-8 metadata, and local customization detection.

Self-review caught overly permissive numeric Literal validation (true/1.0 accepted
as schema version 1) and newline-normalized hashes. Both were corrected and regression
tested. No independent reviewer agent ran in this session; that review remains unrun.
Claude Code sessions/model behavior/authentication were not tested. Python 3.11/3.13
are configured in CI but were not locally executed. Remote CI status is not implied.
