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

Komut shell olmadan çalışır; çıktı diske akıtılarak `.harness/evidence/T-1/E<n>.txt`'ye
yazılır (son 1 MiB tutulur), görev dosyasına çıkış kodu, komut başlamadan önceki
commit, çıktı hash'i ve `recorded_by: harness` eklenir. Komut başarısızsa da kaydedilir
ve 1 ile çıkılır; varsayılan zaman aşımı 3600 sn'dir (`--timeout`), aşılırsa çıkış kodu
124 kaydedilir. Görev dosyası komut bittikten sonra kilit altında yeniden okunup
birleştirilir, böylece arada yapılan düzenlemeler korunur; dosya yeniden yazıldığı
için YAML yorumları korunmaz. Git yoksa commit boş kalır ve uyarı verilir.

Bilinen token biçimleri (GitHub, OpenAI, Slack, AWS, Google), `...TOKEN=`/`PASSWORD:`
gibi atamalar ve `Authorization:` başlıkları komutta ve çıktıda `[REDACTED]` ile
maskelenir. Bu sezgiseldir; her gizli bilgiyi yakalamaz. Kanıt dosyaları commit
edildiği için gizli bilgiyi argümanla vermeyin ve çıktıya yazdırmayın. `check` hash'i doğrulayarak sonradan değiştirilmiş çıktıyı
yakalar; kanıtın yerelde uydurulmasını engellemez, güvenilir koşu CI'dır.

`check` hata varsa 1 ile çıkar. Denetledikleri: şema ve dosya adı = id; DONE görevde
her kabul kriterinin başarılı kanıta bağlı olması; review'un sahibinden farklı biri
tarafından onaylanması ya da gerekçeli `unavailable`; bilinmeyen/döngüsel
bağımlılıklar; PENDING karara bağlı ilerleyen iş; proje dışı veya symlink yollar.
Komut çalıştırmaz: kanıtların tutarlılığını denetler, doğruluğunu kanıtlamaz.
Reviewer adı (`review.by`) beyandır; kimlik doğrulaması yapılmaz.
CI'da çalıştırılması önerilir. Plan: [docs/plans/team-system.md](docs/plans/team-system.md).

## Yaşam döngüsü fazları

`.harness/config.yaml` içindeki opsiyonel `workflow`, işin hangi fazlardan geçeceğini
tanımlar. `init` bunu yazmaz; elle eklenir. Yoksa hiçbir şey değişmez.

```yaml
workflow:
  require_phase: true
  phases:
    - name: spec
    - name: design
      requires: [spec]
    - name: implement
      requires: [spec, design]
    - name: fix
```

Görev `phase: implement` taşır. READY, IN_PROGRESS, IN_REVIEW ya da DONE durumundaki
fazlı görev, fazının `requires` listesindeki her faz için `depends_on` zincirinde o
fazda bir göreve ulaşmalıdır; yoksa `check` hata verir. İlerleyen görevin bağımlılıkları
zaten DONE olmak zorunda olduğundan, gereken fazın görevi tamamlanmadan iş ilerleyemez.
`requires` yalnız daha önce tanımlı fazlara bakabilir. `require_phase: true` fazsız
görevi reddeder (CANCELLED hariç); küçük değişiklikler için gereksinimi olmayan bir faz
(yukarıdaki `fix`) tanımlanır.

Sınırlar: kapı, faz görevinin var ve tamamlanmış olduğunu denetler; içeriğinin iyi
olduğunu ya da bağlanan görevin gerçekten o spec'i uyguladığını değil. Bu review'un
işidir. Kapı config'te durur: workflow'u gevşeten bir değişikliği `check` yakalamaz;
config silinirse yalnız `phase` taşıyan görevler hata verir. Config'i rolün yazma
kapsamı dışında tutun. Mevcut bir deftere
`require_phase: true` eklemek fazsız eski görevleri de hataya düşürür; onlara
gereksinimsiz bir faz verin. Plan: [docs/plans/lifecycle.md](docs/plans/lifecycle.md).

## Sıradaki iş

```bash
harness next /path/to/project --json
```

Defterden hangi görevlerin şimdi başlayabileceğini hesaplar; hiçbir şey çalıştırmaz ve
defteri değiştirmez. `check` hata veriyorsa aynı hataları gösterir, plan üretmez ve 1
ile çıkar. Listeler: `running` (IN_PROGRESS, IN_REVIEW), `start` (şimdi başlatılabilecek
READY görevler), `held` (kapsamı çalışan ya da aynı turda başlayan bir görevle kesişen
READY görevler), `unblocked` (bağımlılıkları tamam, READY yapılabilecek BACKLOG görevler),
`waiting` (nedenleriyle bekleyenler), `decisions` (PENDING kararlar ve beklettikleri).

İki görev ancak `scope` alanları kesişmiyorsa birlikte başlatılır. `scope` girdileri
proje köküne göre yol ya da glob'dur (`*`, `?`, `**`; köşeli parantez ve süslü parantez
desteklenmez). Kesişim yol öneklerine bakar ve temkinlidir: `src/api/*.py` ile
`src/api/v1/` kesişir sayılır. Büyük/küçük harf ve Unicode normalleştirme farkı yok
sayılır; mutlak yollar, `~`, sürücü harfi, `$VAR`, URL ve `..` her şeyle kesişir. `scope` yazılmamış görev her şeye
dokunuyor kabul edilir ve yalnız tek başına başlar; başka işler sürdükçe bekleyebilir.
Sıra önce önek, sonra numaradır (`API-3`, `T-1`, `T-2`, `T-10`); kesişmede önce gelen
başlar.

Sınırlar: `scope` beyandır; görevin gerçekten yalnız o yollara yazdığını rolün yazma
kapsamı ve review denetler. Serbest metin (`API katmanı`) yol gibi okunur ve gerçek
yollarla kesişmez; yol yazın. Herkesin yazdığı dosyalar (`.harness/state/`, lock
dosyaları) hiçbir görevin kapsamında değildir ve `next` bunları korumaz. AGENTS.md her
göreve durum belgelerini güncellemesini söylediği için, paralel çalıştırırken bu
belgeleri yalnız başka iş çalışmıyorken güncellemek kullanıcının uyması gereken bir
kuraldır; bunu yapan bir orchestrator henüz yok. `next` öncelik, tahmin ya da rol
kapasitesi bilmez.

## Bir görevi ajana yaptırma

```bash
pip install 'agent-harness-starter[engine]'   # Claude Agent SDK, isteğe bağlı
harness run T-3 /path/to/project --json
```

Defterdeki bir görevi, görev sahibinin rolüyle Claude'a yaptırır. Yalnız READY ya da
IN_PROGRESS görevler çalışır; geçersiz defter, bilinmeyen görev ve rol motor çağrılmadan
reddedilir. Komut defteri değiştirmez, commit atmaz: durum güncelleme ve commit
orchestrator'ın işidir. Çıkış kodu 0 yalnız görev tamamlandıysa; hata ya da limit
nedeniyle bitmişse 1.

Rol başına motor ayarları `.harness/config.yaml` içinde durur:

```yaml
roles:
  developer:
    write: ["src/**", "tests/**"]
    model: claude-sonnet-5-5   # boşsa motorun varsayılanı
    effort: medium             # low, medium, high, xhigh, max
    bash: ["uv run pytest", "ruff check"]   # ajanın sormadan çalıştırabildiği komutlar
```

Ajan insansız çalıştığı için her araç çağrısını rol politikası karara bağlar. Varsayılan red; izin
verilenler şunlardır.

- Dosya yazan araçlar (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) rolün yazma
  kapsamı içinde; `harness scope` ile aynı kural.
- Bilinen okuma araçları (`Read`, `Glob`, `Grep`, `LS`, `TodoWrite`, `Task`, `Skill`...),
  yalnız proje klasörünün içindeki yollar ve desenler için (`/`, `~` ya da `..` ile başlayan
  `Glob` deseni reddedilir).
- `Bash` yalnız `bash` listesindeki önekle başlayan tek komut. `;`, `&`, `|`, `<`, `>`,
  `` ` ``, `$(` ve satır sonu içeren komutlar reddedilir. `harness evidence run T-1 -- KOMUT`
  yalnız şu biçimde ve KOMUT listedeyse geçer: görev kimliği, bilinen seçenekler (`--cwd`,
  `--id`, `--timeout`, `--summary`, `--json`; `--cwd` proje içinde), sonra hemen `--`. Komut
  satırı ayrıştırıcısı `--` öncesindeki sözcükleri de komuta kattığı için (`T-1 rm build --
  uv run pytest` gerçekte `rm build uv run pytest` çalıştırır), başka hiçbir sözcüğe ve
  `--path` seçeneğine izin verilmez.
- Reddedilenler: MCP araçları (proje ayarları dosya yazan sunucu ekleyebilir), ağ araçları
  (`WebFetch`, `WebSearch`) ve politikanın tanımadığı her araç.

Politika iki yerden bağlanır: `PreToolUse` hook'u ve izin geri çağrımı. Hook, projenin kendi
`.claude/settings.json` dosyasındaki bir izin kuralının (ör. `Bash(*)`) geri çağrımı
atlamasını engeller. Hook hata verirse ya da girdisi bozuksa çağrı reddedilir (açık
bırakılmaz). Hook'un reddini proje ayarlarının geri aldığı SDK dokümanında yazan davranışa
dayanır; gerçek CLI ile denenmedi.

Kullanım limiti dolunca koşu hatayla biter; çalıştırıcı "bekleniyor" olayını yazdırır,
motorun bildirdiği sıfırlanma zamanına (yoksa 5 dakikaya) kadar bekler ve aynı oturumu
sürdürür. `--max-resumes` (varsayılan 3) aşılırsa ya da bekleme 6 saati geçecekse
beklemeden `rate_limited` döner. Motor ya da SDK hata fırlatırsa koşu `failed` döner;
oturum kimliği ve olaylar korunur.

Sınırlar:

- Bu bir sandbox değildir. İzinli bir komut dosya yazabilir ve bayraklar kapsam dışını
  değiştirebilir (`ruff check --fix`, `pytest --junitxml=/yol`). `bash` listesine yalnız
  zararsız komutlar yazın, bayraklı biçimleri de düşünün.
- Kabuk özel karakterleri (tırnak, `\`, `$`, `*`, `?`, `{}`, `~`, `#`) içeren komutlar reddedilir:
  politika komutu boşluktan böler, kabuk ise başka okur. Bu yüzden `-k "a or b"` gibi
  tırnaklı argümanlar da yazılamaz.
- Okuma kuralı yalnız yola bakar; proje içindeki bir symlink dışarıyı gösterebilir.
- Ajan proje ayarlarını (`.claude/settings.json`, CLAUDE.md, hook'lar, yetenekler) yükler.
  `.mcp.json`'daki MCP sunucuları başlatılır (yalnız araçları reddedilir), projenin kendi
  hook'ları ve `statusLine` komutları araç hattının dışında kabuk komutu çalıştırabilir.
  Yalnız güvendiğiniz projelerde çalıştırın.
- Kullanım limiti SDK'dan `ResultMessage` yerine istisna olarak gelirse beklenmez, koşu
  `failed` olur.
- Devam eden oturumda `cost_usd` ve `turns` toplamının tekrar saymayabileceği doğrulanmadı.
- Bekleme sırasında Ctrl-C temiz bir `failed` sonucu vermez.
- Gerçek Claude oturumu henüz denenmedi: motor yalnız sahte SDK ve sahte motorla birim
  testlerden geçti.

## Paralel dalga ve bağımsız review

`harness wave [PATH] [--max-parallel N] [--review]`: `harness next`in "başlayabilir" dediği
görevleri (en çok `--max-parallel`, varsayılan 3) aynı anda çalıştırır ve her koşunun
sonucunu deftere yazar. Durumu yalnız bu komut değiştirir; ajanlar değiştirmez.

- Başlamadan önce görev READY'den IN_PROGRESS'e alınır (başkası araya girdiyse alınmaz).
- Biten görev, koşu sırasında kaydedilmiş en az bir yeni ve geçen kanıtı varsa
  IN_REVIEW'a gider. Kanıtsızsa ya da hata verdiyse BLOCKED olur ve gerekçe yazılır.
  Kullanım limitine takılan görev READY'ye döner. Yarıda kesilen dalga görevleri
  READY'ye bırakır.
- Çıkış kodu 0: hepsi IN_REVIEW'a ulaştı ve defter yazıldı.

`harness review [PATH] [--task T-n] [--role reviewer]` (ya da `wave --review`): IN_REVIEW
görevleri reviewer rolüyle inceletir. Reviewer yalnız okur; kararını kendi yazmaz. Cevabını
`VERDICT: APPROVED|CHANGES_REQUESTED` satırıyla ve her kabul kriteri için kanıt satırıyla
(`AC1: E1`) bitirir. Karar kodla işlenir:

- Onay yalnız her kriter kaydedilmiş, geçen bir kanıta bağlanırsa kabul edilir. Görev DONE
  olur ve `verified_by` doldurulur. Eksik onay "değişiklik istendi" sayılır.
- Değişiklik istenirse görev READY'ye döner, reviewer'ın notları bir sonraki geliştirici
  istemine veri olarak eklenir.
- Görev sahibi kendi görevini inceleyemez. Reviewer kanıtı ya da kriterleri değiştirirse
  görev BLOCKED olur. Karar satırı yoksa ya da koşu başarısızsa görev IN_REVIEW'da kalır.

Sınırlar: reviewer `bash` kullanamaz, bu yüzden "kapsam dışı dosya değişti mi" sorusunu
`git` ile doğrulayamaz; onay bu noktada modelin okumasına dayanır. Kodla kapsam denetimi
görev başına commit adımıyla gelecek. IN_REVIEW'daki görev, DONE olana kadar kapsamını
meşgul tutar ve çakışan görevleri bekletir.

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
dizinin tamamı. `deny` önce uygulanır; hiçbirine uymayan yol reddedilir. Eşleştirme
büyük/küçük harf ve Unicode normalleştirme farkını yok sayar (macOS ve Windows dosya
sistemleri de öyle yapar). Bunun bedeli: Linux'ta `docs/**` izni `DOCS/x` gibi ayrı bir
dizine yazmaya da izin verir; `deny` kuralları da aynı şekilde katlandığı için korunan
yollar etkilenmez. Proje dışı yollar, `..` içeren hedefler ve proje içinde
symlink'ten geçen yollar reddedilir; proje kökünün kendisi symlink altındaysa gerçek
yolu kullanılır.

```bash
harness scope developer src/app.py .claude/agents/lead.md --json
```

Roller tanımlıysa `check`, görev sahibinin (`owner`) tanımlı bir rol olmasını ister.
`roles` olmayan eski config'ler geçerlidir; bu durumda sahip denetlenmez.

### Claude Code hook'u (opsiyonel)

```bash
harness hook install . --dry-run                     # sonucu göster
harness hook install . --command "uv run harness"    # .claude/settings.json'a ekle
```

`install`, mevcut `.claude/settings.json` içeriğini korur, aynı matcher ve harness
komutuyla kayıt varsa çoğaltmaz, geçersiz JSON ya da beklenmeyen yapıda hiçbir şey
yazmaz (JSON biçimlendirmesi yeniden yazılır). Aynı anda başka bir yazarla
çalıştırılmamalıdır; dosya kilitlenmez.
Hook, Write/Edit/MultiEdit/NotebookEdit çağrılarında rolün kapsamı dışındaki yazmayı
Claude Code'a `permissionDecision: deny` ile bildirir. Rol, hook girdisindeki
`agent_type` tanımlı bir rolse ondan (subagent veya `claude --agent`), yoksa
`HARNESS_ROLE` ortam değişkeninden alınır. Hiç rol belirlenemiyorsa, config ya da
config'te rol yoksa, girdide dosya yolu yoksa ya da girdi okunamıyorsa engellemez ve
nedenini stderr'e yazar. `HARNESS_ROLE` config'te tanımlı olmayan bir ada
ayarlanmışsa (yazım hatası) yazma reddedilir. Denetlenemeyecek kadar büyük girdi
(64 MiB üstü) roller tanımlıysa reddedilir. `CLAUDE_PROJECT_DIR` yoksa proje kökü
olay `cwd`'sinden yukarı doğru `.harness/config.yaml` aranarak bulunur.

Bu bir guardrail'dir, sandbox değil: Bash ile yazma görülmez; asıl kapı CI'daki
`harness check`'tir. Hook zaman aşımında Claude Code varsayılan olarak çağrıyı
engellemez.

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
