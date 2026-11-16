# Current task

Status: Uygulama adım 1, motor adapter (T-7) DONE, review approved. Sıradaki adım:
orchestrator döngüsü (görev seç, paralel başlat, kanıt, review, kapı).

## Goal and scope

T-7: Claude Agent SDK üstünde, rol politikasıyla kısıtlı, kullanım limitini bekleyen
`harness run`. Kapsam dışı: orchestrator, FastAPI sunucu, React arayüz, masaüstü kabuğu.

## Acceptance criteria

AC1-AC6 in `.harness/tasks/T-7.yaml`.

## Required checks

Unit tests only (owner decision): uv run --frozen pytest -q; ruff check .; ruff format
--check .; harness validate; harness check. Run via `harness evidence run`.

## Product decisions and blockers

User decisions: docs/plans/lifecycle.md (branch layout A decided). No blocker.
