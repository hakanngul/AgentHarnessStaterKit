# Yol haritası

## 0.1.0 — uygulanan

- Python CLI: init, analyze, validate, doctor; JSON çıktı ve dry-run.
- Claude Code rolleri, skill, kurallar ve kalıcı görev belgeleri.
- Sürüm şeması, SHA-256 manifest, mevcut dosyaları koruyan kurulum.
- Otomatik davranış testleri ve paket dağıtımı.

## Yayımlanmamış — uygulanan (Faz 1)

- Şemalı görev, kanıt ve karar defteri; `harness check` tutarlılık denetimi.
  Ayrıntı: [plans/team-system.md](plans/team-system.md).

## Yayımlanmamış — uygulanan (Faz 2)

- `harness evidence run`, config'te roller ve `harness scope`, PreToolUse hook'u ve
  `harness hook install`.

## Yayımlanmamış — uygulanan (yaşam döngüsü L1)

- Config'te `workflow` fazları, görevde `phase`, `harness check` faz kapısı.
  Ayrıntı: [plans/lifecycle.md](plans/lifecycle.md).

## Yayımlanmamış — uygulanan (yaşam döngüsü L3)

- `harness next`: defterden sıradaki işleri, bekleyenleri ve paralel başlatılabilecek
  görevleri hesaplar.

## Yayımlanmamış — uygulanan (uygulama adım 2)

- `harness run`: bir görevi rolün modeli ve izin politikasıyla Claude'a yaptırır, kullanım
  limitinde bekleyip aynı oturumu sürdürür. Yalnız birim testlerle sınandı; gerçek Claude
  oturumu denenmedi.

## Sonraki aşamalar — uygulanmadı

- Yaşam döngüsü L2 ve L4: `init` için varsayılan akış ve faz rolleri; orchestrator döngüsü,
  sunucu, arayüz ve masaüstü uygulaması.
- Stack pack: teknolojiye göre komut, kural ve klasör düzeni (Faz 3).
- upgrade --dry-run / sync: kullanıcı değişikliklerini koruyan üç yönlü karşılaştırma.
- Gerçek proje ihtiyaçlarına göre uzman rolü/skill seçimi ve üretimi.
- Başka ajan runtime adaptörleri ve isteğe bağlı hook/MCP entegrasyonları.
- Başarı, token ve süre metriklerini gerçek karşılaştırmalarla ölçme.

Bu maddeler mevcut CLI komutu veya çalışır garanti olarak sunulmaz.
