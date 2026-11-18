# Karen

Голосовой помощник «Карен» для Raspberry Pi: лицо с эмоциями в браузере, распознавание речи, ответы через Gemini и голос через edge-tts.

## Файлы
- `Karen2.py` — голос и «мозг» (распознавание речи, Gemini, синтез голоса)
- `kr.py` — вариант с управлением моторами (gpiozero)
- `Лицо Карен.backup3.html` — лицо Карен (открывается в браузере)
- `karen-start.sh` — запуск всего: сервер, лицо на весь экран, голос

## Ключ API
Ключ не хранится в коде. Перед запуском задайте его:
```bash
export GEMINI_API_KEY="ваш_ключ"
```

## IoT-сайт (`web/`)
Сайт на Render: https://karen-hzjj.onrender.com
- `/` — презентация проекта (возможности, схема, железо, живой статус робота)
- `/app` — пульт по паролю: чат с Карен (голос/текст), езда, эмоции на экране робота, «сказать через робота», вкл/выкл голосовой Карен, датчики, журнал событий. Вкладку можно открыть ссылкой: `/app#drive`, `/app#sensors`.

Схема: телефон → сайт (FastAPI на Render) ⇄ WebSocket ⇄ робот (`web/robot_agent.py` на Raspberry Pi). Робот подключается к сайту сам, поэтому работает за любым Wi‑Fi/хотспотом.

### Render
Build: `pip install -r requirements.txt` · Start: `python start.py` · Переменные:
- `GEMINI_API_KEY` — ключ Gemini
- `KAREN_PASSWORD` — пароль пульта
- `ROBOT_TOKEN` — секрет для подключения робота (такой же, как на Pi)

### Робот (Raspberry Pi)
`~/.config/karen-robot.env`:
```
KAREN_CLOUD=wss://karen-hzjj.onrender.com/ws/robot
ROBOT_TOKEN=<тот же, что на Render>
```
Запуск агента: `./karen-robot-start.sh` (лог: `~/.cache/karen-logs/robot.log`).
Окружение: `python3 -m venv --system-site-packages ~/karen-web-env && ~/karen-web-env/bin/pip install -r web/requirements.txt` (gpiozero берётся из системы).

Без интернета: `./karen-web-start.sh` запускает сайт прямо на роботе (`KAREN_ROBOT=1`), открыть `http://<адрес-робота>:8000`. Не запускайте одновременно с агентом и `kr.py` — они делят колёса.
