#!/bin/bash
# Агент робота: связь Raspberry Pi <-> сайт Карен (команды, датчики). Повторный запуск не плодит копии.
# Настройки: ~/.config/karen-robot.env  (KAREN_CLOUD=wss://<сайт>/ws/robot, ROBOT_TOKEN=<как на Render>)
LOG="$HOME/.cache/karen-logs"; mkdir -p "$LOG"
if pgrep -f 'karen-web-env/bin/python -u robot_agent.py' >/dev/null; then
  echo "Агент уже работает. Лог: $LOG/robot.log"; tail -3 "$LOG/robot.log"; exit 0
fi
set -a; . "$HOME/.config/karen-robot.env"; set +a
cd "$HOME/karen-github/web" && setsid nohup "$HOME/karen-web-env/bin/python" -u robot_agent.py >>"$LOG/robot.log" 2>&1 < /dev/null &
sleep 5
echo "Агент робота запущен. Лог: $LOG/robot.log"; tail -3 "$LOG/robot.log"
