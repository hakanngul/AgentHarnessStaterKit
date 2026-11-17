# Current task

Status: T-7..T-16 DONE (T-16: macOS'ta symlink'li geçici dizin testleri, CI'da macOS). Aktif görev yok; sıradaki görev henüz açılmadı (görev dosyası `.harness/tasks/` altında açılacak).

## Önerilen sıradaki odak

Gerçek bir Claude oturumuyla sunucu ve arayüz üzerinden uçtan uca deneme: şimdiye kadarki her sınır sahte motorla ya da
yalnız commit işiyle sınandı. Sonra masaüstü kabuğu (pywebview denemesi, yedek Electron), `init` varsayılan workflow ve
faz rolleri, orkestratör döngüsünde faz kapısı.

## Product decisions and blockers

Yok. Dal düzeni A (docs/plans/lifecycle.md). Açık karar (sahibi): Sourcery'nin güvenlik etiketli `.harness`
symlink değiş-tokuşu bulgusu T-13'te reddedildi (saldırganın projeye yazabilmesi gerekir); sertleştirmek istenirse ayrı görev.
