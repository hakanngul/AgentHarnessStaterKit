# Current task

Status: T-7..T-13 DONE (motor, dalga, reviewer, commit, sunucu, sunucuda işler, proje çalışma kilidi). Sıradaki: tarayıcı için token devri tasarımı (çerez + Origin), sonra React arayüzü ve masaüstü kabuğu.

## Goal and scope

T-9: `harness review` ve `wave --review`; review koşusu salt okunur, karar kodla işlenir.
Kapsam dışı: görev başına commit, kapsam denetimi, faz kapısını döngüde işletme, sunucu, arayüz.

## Acceptance criteria

AC1-AC5 in `.harness/tasks/T-9.yaml`.

## Required checks

Unit tests only (owner decision): pytest, ruff check, ruff format --check, harness check.

## Product decisions and blockers

Yok. Dal düzeni A (docs/plans/lifecycle.md). PR #5 merge'i kullanıcıda.
