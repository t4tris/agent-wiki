# Wiki Log

> Хронология действий по вики. Только добавление в конец.
> Формат: `## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete
> При >500 записей — ротация: переименовать в `log-YYYY.md`, начать новый.

## [2026-09-11] create | Структура вики и пайплайн Telegram
- Домен: агентская разработка
- Созданы SCHEMA.md, index.md, log.md и каталоги entities/, concepts/, comparisons/, queries/, raw/telegram/, _staging/
- `raw/` уже содержал 14 источников (манифесты и отчёты) — не тронуты, ждут разбора в Layer 2
- Собран пайплайн: экспорт Saved Messages из Telegram Desktop → `import_telegram.py` (триаж, review.csv/review.md) → одобрение пользователем → `promote_approved.py` (перенос в `raw/telegram/`)

## [2026-09-11] ingest | Одобренные сохранённые сообщения (Telegram)
- Перенесено в raw/telegram/: 2 файлов
  - raw/telegram/2025-06-02-razbor-harness-podhoda-kak-stroitsya-agent-loop-gde-zhivut-t-102.md
  - raw/telegram/2025-06-04-media-104.md
