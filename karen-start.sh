#!/bin/bash
# Запуск всей Карен: лицо (с эмоциями) + голос + сервер. Повторный запуск не плодит копии.
DIR="$HOME/Жүктемелер"
LOG="$HOME/.cache/karen-logs"; mkdir -p "$LOG"
URL='http://127.0.0.1:8765/%D0%9B%D0%B8%D1%86%D0%BE%20%D0%9A%D0%B0%D1%80%D0%B5%D0%BD.backup3.html'
export DISPLAY="${DISPLAY:-:0}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# 1. сервер для лица
if ! pgrep -f 'http.server 8765' >/dev/null; then
  (cd "$DIR" && setsid nohup python3 -m http.server 8765 --bind 127.0.0.1 >"$LOG/http.log" 2>&1 &)
  sleep 1
fi

# 2. лицо на весь экран
if ! pgrep -f 'user-data-dir=/home/ws/.config/karen-kiosk' >/dev/null; then
  rm -f "$HOME/.config/karen-kiosk/"Singleton*
  setsid nohup chromium --user-data-dir="$HOME/.config/karen-kiosk" --kiosk --noerrdialogs --disable-infobars \
    --ozone-platform=wayland --autoplay-policy=no-user-gesture-required "$URL" >"$LOG/chromium.log" 2>&1 &
  sleep 3
fi

# 3. голос и мозг
if ! pgrep -f 'karen-env/bin/python -u .*Karen2.py' >/dev/null; then
  setsid nohup "$HOME/karen-env/bin/python" -u "$DIR/Karen2.py" >"$LOG/karen.log" 2>&1 &
fi

echo "Карен запущена: лицо + голос. Лог: $LOG/karen.log"
