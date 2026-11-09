# Agent Harness Starter Kit

Bir projeye Claude Code ajan rolleri, skills, kurallar ve kalıcı görev hafızası kuran
Python CLI. Komutlar: `init`, `analyze`, `validate`, `check`, `doctor`.

Python kurulum ve denetim motorudur. Ajanları çalıştıran sistem Claude Code'dur;
bu CLI model çağırmaz, API anahtarı istemez ve kendi agent runtime'ını içermez.

## Kurulum

Python 3.11+ ve Git gerekir. Geliştirme için [uv](https://docs.astral.sh/uv/) kullanılır:

```bash
uv sync --frozen --group dev
uv run --frozen harness --help
uv run --frozen pytest
```

Normal kullanıcı kurulumu (uv gerektirmez):

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
harness --help
```

## Yeni veya mevcut projeye kurulum

```bash
harness analyze /path/to/project --json
harness init /path/to/project --name MyProject --description "Projenin hedefi" --dry-run
harness init /path/to/project --name MyProject --description "Projenin hedefi"
harness validate /path/to/project
harness doctor /path/to/project --json
```

PATH verilmezse mevcut dizin kullanılır. `init` eksik hedef dizini oluşturabilir.
Mevcut AGENTS.md, CLAUDE.md veya diğer yönetilen dosyalar varsa kurulum hiçbirini
üzerine yazmaz; çakışmaları çözerek tekrar çalıştırın. `--force` yoktur.
`--dry-run` dosya veya dizin oluşturmaz. Tekrar `init` çalıştırmak mevcut dosyaları
reddeder; sağlık denetimi için `validate` kullanın.

`analyze` yalnızca kökteki bilinen manifestleri inceler; monorepo alt projelerini
gezmez. Python, Node.js, Java, Go, Rust ve .NET işaretlerini tanır. Manifestteki
komutları veri olarak gösterir; hiçbirini çalıştırmaz ve test komutu tahmin etmez.

## Üretilen yapı

```text
MyProject/
├── AGENTS.md
├── CLAUDE.md
├── .claude/
│   ├── agents/{lead,developer,reviewer}.md
│   ├── skills/development/SKILL.md
│   └── rules/verification.md
├── .harness/
│   ├── config.yaml
│   ├── manifest.json
│   └── state/{TASK,PROGRESS,EVIDENCE,HANDOFF}.md
└── docs/{architecture,decisions,plans}/README.md
```

Config, Pydantic ile doğrulanır. Manifest üretilen dosyaların SHA-256 hash'lerini
kaydeder. Yerel düzenlemeler uyarı olarak raporlanır ve korunur; eksik dosya veya
geçersiz şema hata sayılır. Manifest bir güvenlik imzası değildir. Dosya yollarında
symlink kabul edilmez. YAML güvenli yükleyiciyle okunur; dosya boyutu sınırlıdır.

`doctor`, config/dosyalar yanında Python ve Git'i denetler. Claude CLI kurulum için
opsiyoneldir; yokluğu uyarıdır. Ajanları çalıştırmak için Claude Code kurulumu ve
kendi kimlik doğrulaması gerekir; CLI bunların çalıştığını iddia etmez.

Başarılı denetim çıkış kodu 0, yapılandırma/kurulum hatası 1, CLI kullanım hatası 2.
Tüm komutlar `--json` destekler; `harness --version` sürümü gösterir.

## Görev ve karar defteri

Her görev `.harness/tasks/<ID>.yaml` (ör. `T-1`), kullanıcının vermesi gereken her
ürün kararı `.harness/decisions/DEC-<n>.yaml` dosyasıdır. Şemalar:
`schemas/task.schema.json`, `schemas/decision.schema.json`. Şemalar alan yapısını
tanımlar; alanlar arası kurallar (kanıt türüne göre zorunlu alanlar, `DEC-` önekinin
görevlerde yasak olması, karar durumu) yalnız `harness check` ile denetlenir.

```bash
harness check /path/to/project --json
```

Kanıtı elle yazmak yerine komutu harness'a çalıştırtın:

```bash
harness evidence run T-1 -- uv run pytest -q
harness evidence run T-1 --cwd app --timeout 600 -- npm test
```

Komut shell olmadan çalışır; çıktı `.harness/evidence/T-1/E<n>.txt`'ye yazılır, görev
dosyasına çıkış kodu, commit, çıktı hash'i ve `recorded_by: harness` eklenir. Komut
başarısızsa da kaydedilir ve 1 ile çıkılır. Görev dosyası yeniden yazılır (YAML
yorumları korunmaz). `check` hash'i doğrulayarak sonradan değiştirilmiş çıktıyı
yakalar; kanıtın yerelde uydurulmasını engellemez, güvenilir koşu CI'dır.

`check` hata varsa 1 ile çıkar. Denetledikleri: şema ve dosya adı = id; DONE görevde
her kabul kriterinin başarılı kanıta bağlı olması; review'un sahibinden farklı biri
tarafından onaylanması ya da gerekçeli `unavailable`; bilinmeyen/döngüsel
bağımlılıklar; PENDING karara bağlı ilerleyen iş; proje dışı veya symlink yollar.
Komut çalıştırmaz: kanıtların tutarlılığını denetler, doğruluğunu kanıtlamaz.
Reviewer adı (`review.by`) beyandır; kimlik doğrulaması yapılmaz.
CI'da çalıştırılması önerilir. Plan: [docs/plans/team-system.md](docs/plans/team-system.md).

## Roller ve yazma kapsamı

`.harness/config.yaml` içindeki `roles`, her rolün yazabileceği yolları tanımlar.
`init` lead, developer ve reviewer için başlangıç değerleri yazar; projeye göre
düzenleyin. Roller teknolojiye göre değil yetkiye göredir.

```yaml
roles:
  developer:
    write: ["**"]
    deny: [".harness/config.yaml", ".claude/**", ".github/**"]
  reviewer:
    write: [".harness/tasks/**"]
```

Glob: `*` tek segment, `?` tek karakter, `**` sıfır veya daha fazla segment, sonda `/`
dizinin tamamı. `deny` önce uygulanır; hiçbirine uymayan yol reddedilir. Proje dışı,
`..` ile kaçan ve symlink içeren yollar her zaman reddedilir.

```bash
harness scope developer src/app.py .claude/agents/lead.md --json
```

Roller tanımlıysa `check`, görev sahibinin (`owner`) tanımlı bir rol olmasını ister.
`roles` olmayan eski config'ler geçerlidir; bu durumda sahip denetlenmez.

## Geliştirme döngüsü

Hedef ve kabul kriterleri → plan → kod → bağımsız inceleme → gerçek testler →
kanıtları kaydet → oturum devri. CLAUDE.md küçük tutulur; ayrıntılı bağlam ihtiyaç
oldukça yüklenir. İnceleme veya test yapılamadıysa bu açıkça kaydedilir.

Bu Markdown dosyaları davranış yönergeleridir; zorunlu sandbox, otomatik completion
gate veya token tasarrufu garantisi sağlamaz. Hook, MCP sunucusu, dağıtım, Git push
ve ajan çalıştırma otomatik yapılandırılmaz. `.claude/settings.json` veya `.mcp.json`
mevcut ayarları ezmemek ve gereksiz izinler eklememek için üretilmez.

## Geliştirici doğrulaması

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
uv run --frozen python -m build --no-isolation
```

[Mimari](docs/ARCHITECTURE.md), [geliştirme](docs/CONTRIBUTING.md) ve
[yol haritası](docs/ROADMAP.md). Güvenli `upgrade`/`sync` ve projeye özel uzman üretimi
bu sürümde uygulanmadı; manifest gelecekteki çakışma denetimi için temel oluşturur.
