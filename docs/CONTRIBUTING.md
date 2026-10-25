# Geliştirme

Python >=3.11 ve uv kullanın. Mevcut checkout'u kullanın; kullanıcı açıkça istemedikçe
worktree oluşturmayın. Kurulum: `uv sync --frozen --group dev`.

Kaynak `src/agent_harness/`, şablonlar aynı paketin `templates/` dizinindedir.
Giriş noktası `harness = agent_harness.cli:app`. Ayrı init.py wrapper'ları yerine
komutların iş mantığı `generate.py`, `analyze.py`, `validate.py`, `doctor.py` içindedir.

Değişiklikten sonra `uv run --frozen pytest`, `uv run --frozen ruff check .`,
`uv run --frozen ruff format --check .` çalıştırın. Şablon değişikliğinde wheel
oluşturup temiz bir venv'de kaynak dizini dışından init/validate/doctor deneyin.

Bağımlılık değişikliğinde `uv lock` ile lock'u bilinçli güncelleyin; normal kurulumda
`--frozen` kullanın. Sırlar, .env, venv, cache ve dist çıktıları commit edilmez.

Testler dosya üretimini, mevcut dosyaları korumayı, symlink/path güvenliğini,
şema hatalarını, manifest bütünlüğünü ve kurulu CLI komutlarını kapsar. Gerçek Claude
Code oturumu, model davranışı veya API kimlik doğrulaması test kapsamına dahil değildir.
