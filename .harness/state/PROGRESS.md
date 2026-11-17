# Progress

## Completed

- 0.1.0: CLI init/analyze/validate/doctor, templates, manifest, 86 tests.
- Faz 1 (unreleased): task/decision ledger models, JSON schemas, `harness check`,
  templates and docs, CI step, this repository's own ledger (T-1). 187 tests.
- Faz 2 (unreleased, done): `harness evidence run`, roles + `harness scope`,
  PreToolUse hook + `harness hook install`.
- Yaşam döngüsü L1 (unreleased, PR #3): `workflow` phases in the config, `phase` on
  tasks, phase gate in `harness check` (T-5, DONE, review approved). 380 tests.
- Yaşam döngüsü L3 (unreleased, PR #4): `harness next` (T-6, DONE, review approved).
  441 tests.

## Current work

T-13 (proje çalışma kilidi, PR #7) birleştirildi: `flock` ile CLI ve sunucu işleri tek çalıştırma, `run_wave` yalnız
kendi claim'lerini bırakır, git zaman aşımında süreç grubu öldürülür. 949 test. T-14 (güvenlik başlıkları, `--ui` ile statik arayüz, token devri tasarımı, PR #9) birleştirildi. 989 test. PR #2'den üç konu bakımcıda açık: subprocess audit bulgusu, credential redaction kapsamı, settings.json eşzamanlılığı.

## Next step

Gerçek bir Claude oturumuyla sunucu/arayüz üzerinden uçtan uca deneme (changes_requested rework, kullanım limiti bekleme), masaüstü kabuğu (pywebview denemesi, Electron yedek). Sonra: `init` varsayılan workflow ve faz rolleri, faz kapısını orkestra döngüsünde işletmek.

## 2026-11-16 T-7 engine adapter

DONE. 656 unit tests, ruff clean, 3 independent review rounds (all fixed). Never run
against a real Claude session. Next: orchestrator loop.

## 2026-11-16 T-8 orchestrator wave

DONE. 684 unit tests, ruff clean, three review rounds (last APPROVED). Not run against a
real Claude session. Next: real-session trial (needs permission), then FastAPI server.

## 2026-11-16 T-9 independent reviewer

DONE. 734 unit tests, ruff clean, two review rounds (last APPROVED). Real session trial:
wave then review took two tasks to DONE (before the read-only fix). Next: scope check and
commit per task, then FastAPI.

## 2026-11-16 T-10 commit per task

DONE. 795 unit tests, ruff clean, four review rounds (last APPROVED, low findings accepted).
Real trial: four tasks committed one per task on a group branch. Next: FastAPI server.

## 2026-11-16 T-11 read-only local server

DONE. 859 unit tests, ruff clean, three review rounds (last APPROVED). `harness serve`:
loopback only, bearer token, strict Host check, read-only JSON (plan, check, tasks).
Verified against a real uvicorn process. Next: actions (wave, review, commit) and live events.

## 2026-11-16 T-12 server jobs

DONE. 923 unit tests, ruff clean, three review rounds (last APPROVED) plus bot triage on PR #6.
Wave, review and commit run as background jobs with SSE events, cancel and shutdown (a real
uvicorn test covers shutdown with an open stream). Never run against a real Claude session
through the server. Next: project run lock (CLI and server must not work on one project at
once; fixes the wave-claim race and the shared git index), then the UI.

## 2026-11-17 T-14 ve T-15

T-14 DONE (tarayıcı için sunucu hazırlığı). T-15 DONE: `ui/` Vite + React + TypeScript ilk dilim (görev listesi,
iş paneli, canlı olaylar, iptal). 989 pytest, 67 vitest, typecheck, build, CI'da ayrı `ui` işi; üç review turu
(sonuncusu APPROVED) ve bot triage (Codex 2 P2 alındı, Sonar güvenlik derecesi düzeltildi). Gerçek Chromium ile
elle denendi (otomatik değil, depoda yok). Gerçek Claude oturumuyla sunucu/arayüz üzerinden deneme yapılmadı.
