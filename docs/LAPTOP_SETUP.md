# Работа с ноутбука

Актуальная рабочая ветка: `work/laptop-handoff-2026-10-28`.
Это исходники для разработки, не готовая production-сборка. Полный PA/HTF не восстановлен.

```powershell
git clone --branch work/laptop-handoff-2026-10-28 https://github.com/vitaliyekzarkho/Market-Radar.git
cd Market-Radar
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B verify_portable.py
```

Не запускайте run_radar.py, run_cycle.py, save_snapshot.py, native helpers или autostart-скрипты для проверки установки. Они могут подключаться к терминалу. Присутствие исходников MT5 не означает разрешения на live-запуск. Некоторые operational пути пока привязаны к исходному Windows ПК; ноутбук используется для разработки и офлайн-проверок.

В репозитории нет .env, паролей, Telegram получателей, account state, реальных capture/receipt, runtime-журналов, приватных ZIP или .venv. Вход в GitHub/ChatGPT не переносит эти данные и не синхронизирует память чатов. Начинайте новый чат с просьбы прочитать docs/HANDOFF.md.

Перед работой на другой машине:

```powershell
git status
git pull --ff-only
```

После законченного изменения: выполнить подходящие офлайн-тесты, обновить HANDOFF, затем `git add` конкретных файлов, `git commit` и `git push`. Не редактируйте одновременно одни файлы на двух компьютерах без отдельных веток. Если pull сообщает о расхождении, не используйте force/reset: сохраните изменения и согласуйте merge.

Для входа в push используйте Git Credential Manager/браузер GitHub. Пароли и токены не вставлять в чат, файлы или remote URL.

verify_portable.py — ограниченный публичный synthetic-набор, не замена полному локальному регрессу. Общая discovery включает тесты приватных fixtures и без них не обязана проходить. Реальные verification_inputs остаются на исходном ПК. Не публикуйте их ради устранения FileNotFoundError.
