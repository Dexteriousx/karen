"""Агент робота Карен: держит связь Raspberry Pi <-> сайт (Render).

Робот сам подключается к сайту (WebSocket), поэтому работает за любым Wi-Fi/хотспотом.
Получает команды: ехать, стоп, лицо, сказать, включить/выключить голосовую Карен.
Отправляет: датчики (температура, CPU, Wi-Fi…) каждые 2 с и реплики голосовой Карен.

Запуск: ROBOT_TOKEN=... KAREN_CLOUD=wss://<сайт>/ws/robot python robot_agent.py
"""
import os, json, time, asyncio, urllib.request

import websockets
import robot_hw as hw

URL = os.environ.get("KAREN_CLOUD", "wss://karen-hzjj.onrender.com/ws/robot")
TOKEN = os.environ["ROBOT_TOKEN"]
HTTP_URL = URL.replace("wss://", "https://").replace("ws://", "http://").rsplit("/ws/", 1)[0]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


async def handle(ws, msg):
    t = msg.get("type")
    try:
        if t == "drive":
            hw.drive(float(msg.get("left", 0)), float(msg.get("right", 0)), float(msg.get("seconds", hw.DEADMAN)))
        elif t == "stop":
            hw.stop()
        elif t == "face":
            hw.set_face(str(msg.get("face", "обычное")))
        elif t == "say":
            hw.say(str(msg.get("text", ""))[:2000], str(msg.get("face", "радость")))
        elif t == "karen":
            if msg.get("on"):
                hw.karen_start()
            else:
                hw.karen_stop()
            log("голосовая Карен:", "вкл" if msg.get("on") else "выкл")
    except Exception as e:
        await ws.send(json.dumps({"type": "error", "msg": str(e)}, ensure_ascii=False))


async def send_telemetry(ws):
    while True:
        tel = await asyncio.to_thread(hw.telemetry)
        await ws.send(json.dumps({"type": "telemetry", "data": tel}, ensure_ascii=False))
        await asyncio.sleep(2)


async def send_karen_lines(ws):
    lines = hw.follow_karen_log()
    while True:
        item = next(lines)
        if item is None:
            await asyncio.sleep(0.5)
            continue
        who, text = item
        await ws.send(json.dumps({"type": "heard" if who == "user" else "said", "text": text}, ensure_ascii=False))


async def session():
    async with websockets.connect(URL, additional_headers={"X-Robot-Token": TOKEN},
                                  ping_interval=20, ping_timeout=20, open_timeout=90) as ws:
        log("подключён к", URL)
        await ws.send(json.dumps({"type": "hello", "name": "Карен", "hostname": os.uname().nodename}, ensure_ascii=False))
        tasks = [asyncio.create_task(send_telemetry(ws)), asyncio.create_task(send_karen_lines(ws))]
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                await handle(ws, msg)
        finally:
            for t in tasks:
                t.cancel()
            hw.stop()


async def keep_awake():
    # бесплатный Render засыпает без входящих HTTP-запросов — будим его сами раз в 10 минут
    while True:
        try:
            await asyncio.to_thread(urllib.request.urlopen, HTTP_URL + "/healthz", None, 30)
        except Exception:
            pass
        await asyncio.sleep(600)


async def main():
    asyncio.create_task(keep_awake())
    delay = 2
    while True:
        try:
            await session()
            delay = 2
        except Exception as e:
            hw.stop()
            log("нет связи с сайтом:", type(e).__name__, e)
        log(f"переподключение через {delay} с")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


if __name__ == "__main__":
    asyncio.run(main())
