# Agent Harness Starter Kit

Bir projeye Claude Code ajan rolleri, skills, kurallar ve kalıcı görev hafızası kuran
Python CLI. İlk sürüm: `init`, `analyze`, `validate`, `doctor`.

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
