# Plan: yaşam döngüsü fazları ve orchestrator

Hedef: Yeni ya da mevcut bir projede iş, bilinen bir akışla ilerler: keşif, spec,
tasarım, plan, uygulama, QA, yayın. Fazı atlamayı ajan talimatı değil kod engeller.
Uzun vadede bu akışı bir orchestrator uygulaması yürütür; motor (Claude Code ya da
başka bir runtime) bir adapter arkasında kalır. Çekirdek Python'da kalır.

Bu plan [team-system.md](team-system.md) planını değiştirmez; onun süreç çekirdeğinin
üstüne oturur. Stack pack (Faz 3) ve upgrade (Faz 4) ayrıca geçerlidir.

## Dilimler

| Dilim | İçerik | Durum |
| --- | --- | --- |
| L1 | config'te `workflow` fazları, görevde `phase`, `check` faz kapısı | uygulandı (T-5) |
| L2 | `init` için varsayılan akış ve faz başına rol şablonları | planlandı; ilk hedef stack kararı bekliyor |
| L3 | Sıradaki işi defterden hesaplayan komut (`harness next`) | T-6 |
| L4 | Motor adapter'ı, arka plan ajanları, masaüstü uygulaması | planlandı |

## Kullanıcı kararları (2026-11-16)

- Fazlar: discovery → spec → design → implement → qa → release; küçük işler için
  gereksinimsiz `fix` fazı.
- Otonomi: kullanıcı onayı yalnız spec ve release bitince istenir; diğer fazlar
  otomatik ilerler.
- Git: commit'i orchestrator atar, merge'i kullanıcı yapar. Dal düzeni açık (aşağıda).
- Ajanlar arka planda, ayrı oturumlar olarak çalışır; orchestrator model değil koddur.
  Kapsamı kesişmeyen görevler paralel başlatılır (worktree yok); kanıt sayılan test
  koşusu faz kapısında yapılır.
- Modeller: lead ve architect Opus 5.5; developer, reviewer, product (spec) ve qa
  Sonnet 5.5; analyst ve devops Haiku 5.5. Effort varsayılanı medium. Rol başına
  config'te tutulacak.
- Motor: kullanıcının Claude üyeliğiyle, kendi makinesinde (Python Agent SDK ya da
  `claude -p`). Kullanım limiti dolunca orchestrator bekleyip sürdürmelidir. Kimlik
  yöntemi değiştirilebilir kalır; dağıtım için API key gerekir.
- Uygulama: masaüstü; Python arka uç (FastAPI), React arayüz, pywebview penceresi.
  Rust ve Electron yok. İlk hedef işletim sistemi macOS (varsayım, teyit edilmedi).
- Stack: karar gerektirmez. `analyze` stack'i tanır; test/lint/build komutları proje
  açılırken bir kez sorulur ve config'e yazılır.
- Açık: dal düzeni. "Görev başına branch" ile "worktree'siz paralel" birlikte olamaz;
  bir checkout'ta aynı anda tek branch açıktır. Seçenekler: paralel grup için tek dal
  (görev başına commit, grup başına PR) ya da görev başına worktree.
- Ortak dosyalar: paralel görevler `.harness/state/` ve lock dosyalarına yazmaz; bunları
  orchestrator, başka iş çalışmıyorken günceller.

Bilinen riskler: üyelikle otomasyon dokümanlarda açıkça izinli değil (kişisel kullanım
gri alan); paralel ajanlar limiti hızlı doldurur; pywebview paketlemesi denenmedi;
insansız çalışmada izin modeli (hook Bash yazmalarını görmez) çözülmedi.

## L1 tasarımı

```yaml
# .harness/config.yaml
workflow:
  require_phase: true
  phases:
    - name: spec
    - name: design
      requires: [spec]
    - name: implement
      requires: [spec, design]
    - name: fix            # küçük değişiklik için açık kısa yol
```

```yaml
# .harness/tasks/T-7.yaml
phase: implement
depends_on: [T-6]          # T-6 design fazında, o da bir spec görevine bağlı
```

- Gruplama için yeni kavram yok: kapı mevcut `depends_on` grafiğini kullanır. Fazlı ve
  ilerleyen (READY/IN_PROGRESS/IN_REVIEW/DONE) bir görev, fazının `requires` listesindeki
  her faz için bağımlılık zincirinde o fazda bir göreve ulaşmalıdır.
- Mevcut kural ilerleyen görevin bağımlılıklarının DONE olmasını zaten ister; DONE
  görevin bağımlılıkları da DONE olduğundan zincirdeki faz görevleri tamamlanmış olur.
- `requires` yalnız daha önce tanımlı fazlara bakabilir; böylece faz grafiği döngüsüzdür.
- `require_phase: true` fazsız görevi reddeder (CANCELLED hariç); aksi halde ajan `phase`
  alanını yazmayarak kapıyı atlayabilirdi. Kısa yol, gereksinimi olmayan bir faz
  tanımlayarak açıkça verilir.
- `workflow` opsiyoneldir; yoksa hiçbir şey değişmez. `init` henüz workflow yazmaz (L2).

## L1 sınırları

- `check` durumsuzdur: geçiş sırasını değil, defterin o anki tutarlılığını denetler.
- Kapı, faz görevinin içeriğinin iyi olduğunu kanıtlamaz; yalnız var ve DONE olduğunu
  ister. DONE, onaylı review ya da gerekçeli `unavailable` demektir; yani review'u
  yapılamamış bir spec de kapıyı geçirir. Kalite kanıt ve review kurallarına dayanır.
- Bir spec görevine bağlanan implement görevinin gerçekten o spec'i uyguladığı
  denetlenmez; bu review'un işidir.
- Hangi görevin hangi faza ait olduğu beyandır. `require_phase` kapalıysa fazsız görev
  kapıdan muaftır.
- Kapı config'tedir. Workflow gevşetilirse (gereksinimsiz yeni faz, her işe `fix`)
  `check` bunu fark etmez. Config ya da `workflow` silinirse yalnız `phase` taşıyan
  görevler hata verir; fazsız görevler için uyarı da çıkmaz.
  Koruma: rolün yazma kapsamında config'in reddi ve config değişikliklerinin review'u.
- Mevcut deftere `require_phase: true` eklemek, CANCELLED dışındaki fazsız bütün eski
  görevleri (DONE dahil) hataya düşürür. Geçiş için eski görevlere gereksinimsiz bir faz verilir; otomatik
  muafiyet yoktur.
- `workflow` alanı sürüm numarası değişmeden eklendi. Yayımlanmış 0.1.0 paketi bu alanı
  tanımaz ve config'i reddeder; sürüm yükseltme upgrade (Faz 4) ile birlikte yapılır.
