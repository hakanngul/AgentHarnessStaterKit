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
| L1 | config'te `workflow` fazları, görevde `phase`, `check` faz kapısı | T-5 |
| L2 | `init` için varsayılan akış ve faz başına rol şablonları (analyst, product, architect, qa) | planlandı, karar gerekir |
| L3 | Orchestrator: sıradaki işi defterden hesaplayan komut (`harness next`) | planlandı |
| L4 | Motor adapter'ı ve yönetim arayüzü | planlandı, karar gerekir |

L2 ve L4 ürün kararı ister: varsayılan faz listesi, hangi rollerin kurulacağı, motorun
nasıl çalıştırılacağı (abonelik/API key) ve arayüzün biçimi. Bunlar uygulanmadı.

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
- Kapı config'tedir. Config silinirse ya da workflow gevşetilirse (gereksinimsiz yeni
  faz, her işe `fix`) `check` bunu fark etmez; config dosyası yoksa uyarı da vermez.
  Koruma: rolün yazma kapsamında config'in reddi ve config değişikliklerinin review'u.
- Mevcut deftere `require_phase: true` eklemek, fazsız bütün eski görevleri (DONE dahil)
  hataya düşürür. Geçiş için eski görevlere gereksinimsiz bir faz verilir; otomatik
  muafiyet yoktur.
- `workflow` alanı sürüm numarası değişmeden eklendi. Yayımlanmış 0.1.0 paketi bu alanı
  tanımaz ve config'i reddeder; sürüm yükseltme upgrade (Faz 4) ile birlikte yapılır.
