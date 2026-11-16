# Current task

Status: T-7..T-12 DONE (motor, dalga, reviewer, commit, sunucu, sunucuda işler ve canlı olaylar). Sıradaki: proje çalışma kilidi (T-13), sonra arayüz.

## Goal and scope

T-9: `harness review` ve `wave --review`; review koşusu salt okunur, karar kodla işlenir.
Kapsam dışı: görev başına commit, kapsam denetimi, faz kapısını döngüde işletme, sunucu, arayüz.

## Acceptance criteria

AC1-AC5 in `.harness/tasks/T-9.yaml`.

## Required checks

Unit tests only (owner decision): pytest, ruff check, ruff format --check, harness check.

## Product decisions and blockers

Yok. Dal düzeni A (docs/plans/lifecycle.md). PR #5 merge'i kullanıcıda.
