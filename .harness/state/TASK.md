# Current task

Status: T-7..T-15 DONE (T-15: React arayüzünün ilk dilimi). Sıradaki: masaüstü kabuğu denemesi (pywebview, yedek Electron), init varsayılan workflow, faz kapısı.

## Goal and scope

Sunucuyu React arayüzüne hazırlamak: her yanıta güvenlik başlıkları, `harness serve --ui DIR` ile derlenmiş
arayüzü salt okunur sunmak, token devrini belgelemek. Token URL fragment'inde taşınır, arayüz fetch + Authorization
başlığıyla konuşur (EventSource yok, çerez yok). Kapsam dışı: React arayüzünün kendisi, masaüstü kabuğu, TLS.

## Acceptance criteria

AC1-AC5 in `.harness/tasks/T-14.yaml`.

## Required checks

Unit tests only (owner decision): pytest, ruff check, ruff format --check, harness check.

## Product decisions and blockers

Yok. Dal düzeni A (docs/plans/lifecycle.md). Açık karar (sahibi): Sourcery'nin güvenlik etiketli `.harness`
symlink değiş-tokuşu bulgusu T-13'te reddedildi (saldırganın projeye yazabilmesi gerekir); sertleştirmek istenirse ayrı görev.
