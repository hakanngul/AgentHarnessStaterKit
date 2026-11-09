# Plan: stack'ten bağımsız yazılım geliştirme ekibi

Hedef: Bugün Flutter, yarın başka bir teknoloji. Aynı süreçle çalışan,
hatanın gizlenemediği bir ajan ekibi. ai-team-setup'taki süreç fikirleri
(görev defteri, kanıt, karar kuyruğu, QA kapısı, hook'lar) bu CLI'ya testli
olarak taşınır.

## Katmanlar

1. Süreç çekirdeği: şemalı görev, kanıt ve karar defteri, `harness check`.
2. Roller yetkiye göredir, teknolojiye göre değil: lead, developer, reviewer, qa
   çekirdek; diğerleri opsiyonel. Rol = yetki + yazma kapsamı + stack bilgisi.
3. Stack pack (yalnız veri): tespit işaretleri, test/lint/build komutları, klasör
   düzeni, kurallar. Pack yoksa generic; komutlar kullanıcı onayıyla girilir.
4. Zorlama: yerelde hook'lar, asıl kapı CI'da `harness check`. Hook'lar Bash ile
   aşılabilir; bu bir sandbox değildir.

## Fazlar

| Faz | İçerik | Durum |
| --- | --- | --- |
| 1 | Görev/kanıt/karar şemaları + `harness check` | uygulandı, review'da (T-1) |
| 2 | config'te rol + yazma kapsamı; testli hook'lar | planlandı |
| 3 | Stack pack mekanizması: generic + 2 pack | planlandı |
| 4 | `upgrade` / `sync`, kullanıcı değişikliklerini koruyarak | planlandı |
| 5 | Opsiyonel roller, sürüm sabitli dış skill'ler | ihtiyaca göre |

## Faz 1 kabul kriterleri

- AC1: `.harness/tasks/<ID>.yaml` ve `.harness/decisions/DEC-<n>.yaml` sürümlü,
  katı (bilinmeyen alan reddedilir) Pydantic modelleriyle doğrulanır; JSON şemaları
  `schemas/` altında yayımlanır ve modelle eşleştiği test edilir.
- AC2: `harness check [PATH] [--json]` hata varsa çıkış 1, yoksa 0 döner; hiçbir komut
  çalıştırmaz ve bunu raporunda belirtir.
- AC3: DONE için: her kabul kriteri en az bir kanıta bağlı, bağlı kanıtlar başarılı,
  review approved (sahibinden farklı kişi) ya da unavailable + gerekçe.
- AC4: Bağımlılıklar: bilinmeyen referans, kendine bağımlılık ve döngü hata; PENDING
  karara ya da bitmemiş göreve bağlı READY/IN_PROGRESS/IN_REVIEW/DONE görev hata.
- AC5: Dosya güvenliği: symlink reddi, proje dışı kanıt yolu reddi, güvenli YAML,
  dosya adı = id.
- AC6: Üretilen şablonlar defteri ve `harness check`'i anlatır; şablondaki örnek
  şemaya uyar (testli). Mevcut 0.1.0 kurulumları `validate`'te bozulmaz.
- AC7: Bu repo kendi defterini kullanır; CI `harness check .` çalıştırır.

## Faz 1 kapsam dışı

Görev oluşturma komutu, commit'in git'te var olduğunu doğrulama, testleri yeniden
koşturma, rol/kapsam zorlaması, sürüm yükseltme. `check` iddiaların tutarlılığını
denetler; doğruluğunu kanıtlamaz.

## Bilinen riskler

- Kanıt kendi beyanıdır; ajan uydurma çıkış kodu yazabilir. Azaltma: Faz 2'de kanıtı
  kaydeden bir komut (`harness evidence run`) ve CI'da gerçek test koşusu.
- Sürüm 0.1.0'da kaldı: config `harness_version` sabit değer olduğu için yükseltme
  mevcut kurulumları bozardı. Sürüm yükseltme Faz 4 (`upgrade`) ile birlikte yapılır.
