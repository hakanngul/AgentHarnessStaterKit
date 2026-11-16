# Current task

Status: Adım 1-4 DONE (motor T-7, dalga T-8, reviewer T-9), review approved. Sıradaki:
kapsamı kodla denetleyen görev başına commit (layout A), sonra FastAPI.

## Goal and scope

T-9: `harness review` ve `wave --review`; review koşusu salt okunur, karar kodla işlenir.
Kapsam dışı: görev başına commit, kapsam denetimi, faz kapısını döngüde işletme, sunucu, arayüz.

## Acceptance criteria

AC1-AC5 in `.harness/tasks/T-9.yaml`.

## Required checks

Unit tests only (owner decision): pytest, ruff check, ruff format --check, harness check.

## Product decisions and blockers

Yok. Dal düzeni A (docs/plans/lifecycle.md). PR #5 merge'i kullanıcıda.
