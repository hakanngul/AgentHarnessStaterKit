# Current task

Status: Uygulama adım 1 (motor, T-7) ve adım 2 (orchestrator dalgası, T-8) DONE, review
approved. Sıradaki: gerçek Claude oturumunda deneme (izin bekliyor), sonra FastAPI.

## Goal and scope

T-8: `harness wave` planlayıcının başlatılabilir dediği görevleri paralel çalıştırır ve
sonucu deftere yazar (IN_REVIEW, BLOCKED, READY). Kapsam dışı: reviewer ajanını otomatik
çalıştırma, görev başına commit, faz kapısını işletme, sunucu, arayüz.

## Acceptance criteria

AC1-AC4 in `.harness/tasks/T-8.yaml`.

## Required checks

Unit tests only (owner decision): pytest, ruff check, ruff format --check, harness check.

## Product decisions and blockers

Gerçek Claude denemesi: izin sınıflandırıcısı `harness run` komutunu reddetti; kullanıcı
karar verecek. Dal düzeni A karar verildi (docs/plans/lifecycle.md).
