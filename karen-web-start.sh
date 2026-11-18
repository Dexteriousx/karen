#!/bin/bash
# Веб-пульт Карен на роботе: http://<адрес-робота>:8000  (повторный запуск не плодит копии)
if pgrep -f 'karen-web-env/bin/python .*start.py' >/dev/null; then echo "Пульт уже запущен"; exit 0; fi
set -a; . "$HOME/.config/karen-web.env"; set +a
LOG="$HOME/.cache/karen-logs"; mkdir -p "$LOG"
cd "$HOME/karen-github" && PORT=8000 setsid nohup "$HOME/karen-web-env/bin/python" start.py >"$LOG/web.log" 2>&1 &
sleep 3
echo "Пульт Карен: http://$(hostname -I | awk '{print $1}'):8000   Лог: $LOG/web.log"
