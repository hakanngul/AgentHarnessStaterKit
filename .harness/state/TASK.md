# Current task

Status: implementation and local verification complete

## Goal and scope

Implement the first Agent Harness Starter Kit Python CLI from the user's supplied
conversation, populate the previously empty repository, and prepare GitHub delivery.

## Acceptance criteria

- [x] init, analyze, validate, doctor are real installed CLI commands.
- [x] Packaged Claude roles, skill, rules and persistent task memory are generated.
- [x] Existing files and local customizations are preserved; unsafe paths rejected.
- [x] Config/manifest contracts, frozen dependencies, tests and documentation exist.
- [x] Build a wheel and exercise its installed commands outside the source checkout.

## Required checks

From repository root: uv sync --frozen --group dev; uv run --frozen pytest;
uv run --frozen ruff check .; uv run --frozen ruff format --check .;
uv run --frozen python -m build --no-isolation. See EVIDENCE.md for actual outcomes.

## Product decisions and blockers

Scope is 0.1.0's four commands. Upgrade/sync and intelligent role generation are
future work. User requested repository delivery; no deployment or model runtime requested.
