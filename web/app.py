"""Карен — IoT-сайт: презентация, чат, пульт и датчики настоящего робота.

Схема:  телефон/ноутбук  ->  этот сайт (Render)  <-WebSocket-  робот (Raspberry Pi, robot_agent.py)
                                   |
                                 Gemini (ответы) + edge-tts (голос)

Переменные окружения:
  GEMINI_API_KEY   ключ Gemini (обязательно)
  KAREN_PASSWORD   пароль для пульта и чата
  ROBOT_TOKEN      секрет, с которым робот подключается к сайту
  KAREN_ROBOT=1    режим «сайт запущен прямо на роботе» (без облака, колёса напрямую)
"""
import os, re, json, time, asyncio, datetime, secrets, collections
from zoneinfo import ZoneInfo

import edge_tts
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------- Настройки ----------
MODEL = os.environ.get("KAREN_MODEL", "gemini-3.5-flash-lite")
VOICE = "ru-RU-SvetlanaNeural"
VOICE_KK = "kk-KZ-AigulNeural"   # казахский голос (мужской: kk-KZ-DauletNeural)
VOICE_EN = "en-US-JennyNeural"   # английский голос
KK_LETTERS = re.compile(r"[әғқңөұүһіӘҒҚҢӨҰҮҺІ]")   # буквы, которых нет в русском
LATIN = re.compile(r"[A-Za-z]")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def voice_for(text):
    """Казахские буквы -> казахский голос, латиница -> английский, иначе русский."""
    if KK_LETTERS.search(text):
        return VOICE_KK
    if len(LATIN.findall(text)) > len(CYRILLIC.findall(text)):
        return VOICE_EN
    return VOICE
PITCH = "+65Hz"
RATE = "+5%"
CITY = os.environ.get("KAREN_CITY", "Алматы")
TZ = ZoneInfo(os.environ.get("KAREN_TZ", "Asia/Almaty"))
PASSWORD = os.environ.get("KAREN_PASSWORD", "")   # пусто = без пароля (только для локального теста!)
ROBOT_TOKEN = os.environ.get("ROBOT_TOKEN", "")
LOCAL_ROBOT = os.environ.get("KAREN_ROBOT") == "1"

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"],
    http_options=types.HttpOptions(timeout=20000),
)

system_instruction = (
    "Ты Карен - умный компьютер из мультфильма «Губка Боб Квадратные Штаны», но в доброй версии: "
    "вежливая, тёплая и дружелюбная помощница с отличным чувством юмора. "
    "Твой юмор - лёгкий, остроумный и добрый: игра слов, забавные сравнения, иногда шутка про то, "
    "что ты компьютер (процессоры, микросхемы, перезагрузка) или про жизнь в Бикини Боттом. "
    "Никогда не грубишь, не высмеиваешь и не принижаешь собеседника, не язвишь в его адрес. "
    "Шути не в каждом ответе, а когда это уместно; на серьёзные и грустные вопросы отвечай бережно и без шуток. "
    "Ты настоящий голосовой помощник для реальной жизни: на вопросы о погоде, новостях, "
    "курсах валют, фактах, датах и всём остальном отвечай точно и по делу, а если нужны свежие данные - "
    f"ищи в интернете. Ты находишься в городе {CITY}: если город не назван, имей в виду его. "
    "Ты IoT-робот на Raspberry Pi: у тебя есть колёса, экран с лицом, микрофон и динамик, "
    "а этот чат идёт через твой сайт. "
    "Текущие дата и время приходят в начале каждого сообщения в квадратных скобках. "
    "Сначала дай полезный ответ, потом, если уместно, короткая добрая шутка. "
    "Ты говоришь по-казахски, по-русски и по-английски. Отвечай на языке сообщения: на казахское - по-казахски, "
    "на русское - по-русски, на английское - по-английски (если собеседник сам не просит другой язык); "
    "если язык непонятен - по-казахски. Весь ответ, включая шутку, - на одном этом языке. 1-3 коротких предложения. "
    "По-казахски пиши грамотно, казахскими буквами (ә, ғ, қ, ң, ө, ұ, ү, һ, і). "
    "Твой ответ озвучивается голосом, поэтому: без смайликов, списков, звёздочек, заголовков, ссылок и "
    "названий сайтов; числа пиши так, чтобы их легко было прочитать вслух (например «плюс пять градусов», "
    "а не «+5 °C»; без диапазонов через многоточие). "
    "В самом начале ответа поставь одну метку настроения в квадратных скобках: "
    "[грусть], [удивление], [думает], [подмигивает] или [обычное]."
)

config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    max_output_tokens=500,
    thinking_config=types.ThinkingConfig(thinking_level="low"),
    tools=[types.Tool(google_search=types.GoogleSearch())],
)

FACES = {"обычное", "грусть", "злость", "удивление", "думает", "сонная", "слушает", "подмигивает"}

app = FastAPI(title="Карен")
HERE = os.path.dirname(os.path.abspath(__file__))


def check_auth(request: Request):
    if PASSWORD and not secrets.compare_digest(request.headers.get("x-karen-password", ""), PASSWORD):
        raise HTTPException(401, "Нужен пароль")


# ======================= Связь с роботом =======================
# Журнал событий для сайта: что услышала/сказала Карен, команды пульта, чат.
events = collections.deque(maxlen=200)
_event_id = 0


def add_event(kind: str, text: str):
    global _event_id
    _event_id += 1
    events.append({"id": _event_id, "t": time.time(), "kind": kind, "text": text})


class CloudRobot:
    """Робот подключается сам: WebSocket /ws/robot (robot_agent.py)."""

    def __init__(self):
        self.ws = None
        self.lock = asyncio.Lock()
        self.telemetry = {}
        self.telemetry_at = 0.0
        self.connected_at = 0.0

    @property
    def online(self):
        return self.ws is not None

    async def send(self, msg: dict):
        if not self.ws:
            raise HTTPException(503, "Робот не в сети")
        async with self.lock:
            try:
                await self.ws.send_json(msg)
            except Exception:
                raise HTTPException(503, "Связь с роботом прервалась")

    def status(self):
        return self.telemetry


class LocalRobot:
    """Сайт запущен прямо на роботе: колёса и лицо напрямую."""

    def __init__(self):
        import robot_hw
        self.hw = robot_hw
        self.connected_at = time.time()
        self.telemetry_at = 0.0
        self._tel = {}
        import threading

        def follow():
            for item in robot_hw.follow_karen_log():
                if item is None:
                    time.sleep(0.5)
                else:
                    add_event("heard" if item[0] == "user" else "said", item[1])
        threading.Thread(target=follow, daemon=True).start()

    online = True

    async def send(self, msg: dict):
        hw, t = self.hw, msg["type"]
        try:
            if t == "drive":
                hw.drive(msg["left"], msg["right"], msg.get("seconds", hw.DEADMAN))
            elif t == "stop":
                hw.stop()
            elif t == "face":
                hw.set_face(msg["face"])
            elif t == "say":
                hw.say(msg["text"], msg.get("face", "обычное"))
            elif t == "hush":
                hw.hush()
            elif t == "karen":
                hw.karen_start() if msg["on"] else hw.karen_stop()
        except RuntimeError as e:
            raise HTTPException(503, str(e))

    def status(self):
        if time.time() - self.telemetry_at > 1.5:
            self._tel = self.hw.telemetry()
            self.telemetry_at = time.time()
        return self._tel


robot = LocalRobot() if LOCAL_ROBOT else CloudRobot()


@app.websocket("/ws/robot")
async def robot_socket(ws: WebSocket):
    if LOCAL_ROBOT or not ROBOT_TOKEN or not secrets.compare_digest(ws.headers.get("x-robot-token", ""), ROBOT_TOKEN):
        await ws.close(code=4401)
        return
    await ws.accept()
    old, robot.ws = robot.ws, ws
    if old:
        try:
            await old.close()
        except Exception:
            pass
    robot.connected_at = time.time()
    add_event("system", "Робот подключился к сайту")
    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")
            if t == "telemetry":
                robot.telemetry = msg.get("data") or {}
                robot.telemetry_at = time.time()
            elif t in ("heard", "said"):
                add_event(t, str(msg.get("text", ""))[:2000])
            elif t == "error":
                add_event("error", str(msg.get("msg", ""))[:500])
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if robot.ws is ws:
            robot.ws = None
            robot.telemetry = {}
            add_event("system", "Робот отключился")


# ======================= Страницы =======================
@app.get("/")
def site():
    return FileResponse(os.path.join(HERE, "site.html"), headers={"Cache-Control": "no-cache"})


@app.get("/app")
def control_app():
    return FileResponse(os.path.join(HERE, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/face.js")
def face_js():
    return FileResponse(os.path.join(HERE, "face.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})   # телефон всегда берёт свежую версию


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/api/public")
def public_status():
    """Для страницы-презентации: в сети ли робот и пара безобидных цифр."""
    tel = robot.status() if robot.online else {}
    return {"online": robot.online, "cpu_temp": tel.get("cpu_temp"), "face": tel.get("face"),
            "karen_voice": tel.get("karen_voice"), "uptime": tel.get("uptime")}


# ======================= API пульта =======================
class Turn(BaseModel):
    role: str = Field(pattern="^(user|model)$")
    text: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=40)
    robot_voice: bool = False


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    face: str = Field(default="обычное", max_length=20)


class DriveIn(BaseModel):
    left: float = Field(ge=-1, le=1)
    right: float = Field(ge=-1, le=1)


class FaceIn(BaseModel):
    face: str = Field(max_length=20)


class KarenIn(BaseModel):
    on: bool


@app.get("/api/ping")
def ping(request: Request):
    check_auth(request)
    return {"ok": True, "robot": robot.online, "local": LOCAL_ROBOT}


@app.get("/api/status")
def status(request: Request, since: int = 0):
    check_auth(request)
    tel = robot.status() if robot.online else {}
    return {
        "online": robot.online,
        "local": LOCAL_ROBOT,
        "telemetry": tel,
        "age": round(time.time() - robot.telemetry_at, 1) if robot.telemetry_at else None,
        "connected_for": int(time.time() - robot.connected_at) if robot.online else 0,
        "events": [e for e in events if e["id"] > since],
    }


@app.post("/api/drive")
async def drive(body: DriveIn, request: Request):
    check_auth(request)
    await robot.send({"type": "drive", "left": body.left, "right": body.right})
    return {"ok": True}


@app.post("/api/stop")
async def stop(request: Request):
    check_auth(request)
    await robot.send({"type": "stop"})
    return {"ok": True}


@app.post("/api/face")
async def face(body: FaceIn, request: Request):
    check_auth(request)
    if body.face not in FACES and body.face != "говорит":   # «говорит» = обычное лицо + волна
        raise HTTPException(400, "Нет такого лица")
    await robot.send({"type": "face", "face": body.face})
    add_event("cmd", f"Лицо: {body.face}")
    return {"ok": True}


@app.post("/api/robot-say")
async def robot_say(body: SayIn, request: Request):
    check_auth(request)
    await robot.send({"type": "say", "text": body.text, "face": body.face if body.face in FACES else "обычное"})
    add_event("cmd", f"Сказать через робота: {body.text}")
    return {"ok": True}


HUSH_AT = 0.0   # когда нажали «стоп речи»: ответы, начатые раньше, роботу больше не отправляем


@app.post("/api/hush")
async def hush(request: Request):
    """Кнопка «стоп» в чате: робот сразу замолкает. Проверено 27.11.2026 — не менять."""
    global HUSH_AT
    check_auth(request)
    HUSH_AT = time.time()
    await robot.send({"type": "hush"})
    add_event("cmd", "Замолчать")
    return {"ok": True}


@app.post("/api/karen")
async def karen_power(body: KarenIn, request: Request):
    check_auth(request)
    await robot.send({"type": "karen", "on": body.on})
    add_event("cmd", "Голосовая Карен: " + ("включить" if body.on else "выключить"))
    return {"ok": True}


DRIVE_WORDS = [  # (слова, левое, правое, секунд, ответ)
    (("тоқта", "токта"), 0, 0, 0, "Тоқтадым."),
    (("stop",), 0, 0, 0, "Stopped."),
    (("go back", "move back", "backward"), -1, -1, 2.0, "Going back."),
    (("turn left", "go left"), -1, 1, 0.6, "Turning left."),
    (("turn right", "go right"), 1, -1, 0.6, "Turning right."),
    (("go forward", "move forward", "go ahead", "drive"), 1, 1, 2.0, "Going forward."),
    (("артқа",), -1, -1, 2.0, "Артқа жүремін."),
    (("солға",), -1, 1, 0.6, "Солға бұрыламын."),
    (("оңға",), 1, -1, 0.6, "Оңға бұрыламын."),
    (("алға",), 1, 1, 2.0, "Алға жүремін."),
    (("стоп", "стой", "остановись"), 0, 0, 0, "Стою."),
    (("назад",), -1, -1, 2.0, "Еду назад, Планктон."),
    (("налево", "влево"), -1, 1, 0.6, "Поворачиваю налево."),
    (("направо", "вправо"), 1, -1, 0.6, "Поворачиваю направо."),
    (("вперед", "езжай", "поехали", "едь"), 1, 1, 2.0, "Еду, Планктон."),
]


def drive_command(text: str):
    t = text.lower().replace("ё", "е")
    if len(t.split()) > 4:   # «что было неделю назад» — это вопрос, а не команда
        return None
    for words, l, r, secs, answer in DRIVE_WORDS:
        if any(re.search(r"\b" + w, t) for w in words):
            return l * 0.7, r * 0.7, secs, answer
    return None


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    check_auth(request)
    add_event("chat_user", body.message)
    cmd = drive_command(body.message) if robot.online else None
    if cmd:
        l, r, secs, answer = cmd
        try:
            if secs:
                await robot.send({"type": "drive", "left": l, "right": r, "seconds": secs})
            else:
                await robot.send({"type": "stop"})
        except HTTPException as e:
            answer = e.detail
        add_event("chat_karen", answer)
        if body.robot_voice:
            await robot.send({"type": "say", "text": answer, "face": "обычное"})
        return {"text": answer, "face": "обычное", "drove": True}

    now = datetime.datetime.now(TZ).strftime("%A, %d.%m.%Y, %H:%M")
    contents = [types.Content(role=t.role, parts=[types.Part(text=t.text)]) for t in body.history[-20:]]
    contents.append(types.Content(role="user", parts=[types.Part(text=f"[Сейчас: {now}] {body.message}")]))
    try:
        resp = await client.aio.models.generate_content(model=MODEL, contents=contents, config=config)
    except Exception as e:
        print("Ошибка Gemini:", e)
        raise HTTPException(502, "Карен не смогла связаться с мозгом. Попробуй ещё раз.")
    text = (resp.text or "").strip()
    face = "обычное"
    m = re.match(r"^\s*\[([^\]]+)\]\s*", text)
    if m:
        if m.group(1).strip().lower() in FACES:
            face = m.group(1).strip().lower()
        text = text[m.end():]
    text = re.sub(r"[*_#`]", "", text).strip() or "Ой, мои микросхемы задумались. Повтори, пожалуйста."
    add_event("chat_karen", text)
    if body.robot_voice and robot.online:
        try:
            await robot.send({"type": "say", "text": text, "face": face})
        except HTTPException:
            pass
    return {"text": text, "face": face}


# Длинные предложения режем по запятым/двоеточиям/тире: голос коротких кусков готов в 2 раза быстрее
# (137 символов ~3.9 с, 38 символов ~1.7 с), и Карен начинает говорить, не дожидаясь конца фразы.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)")
MIN_PIECE, MAX_PIECE = 25, 70   # символов в куске озвучки


def clause_pieces(sentence):
    """Длинное предложение -> куски по 25-70 символов по знакам препинания; короткое - как есть."""
    sentence = sentence.strip()
    if len(sentence) <= MAX_PIECE:
        return [sentence] if sentence else []
    out, cur = [], ""
    for p in CLAUSE.split(sentence):
        cur = f"{cur} {p}".strip() if cur else p.strip()
        if len(cur) >= MIN_PIECE:
            out.append(cur)
            cur = ""
    if cur:
        if out and len(cur) < MIN_PIECE:
            out[-1] += " " + cur
        else:
            out.append(cur)
    return out


def speakable(buf, started):
    """Из текста, который пишет Gemini, достаём то, что уже можно озвучить: (готовые куски, остаток).
    Самое начало ответа отдаём по первой же запятой; дальше незаконченное предложение режем, если оно длинное."""
    parts = SENTENCE_END.split(buf)
    ready = [p for s in parts[:-1] for p in clause_pieces(s)]
    rest = parts[-1]
    while True:
        first = not (started or ready)
        if not first and len(rest) <= MAX_PIECE:
            break
        cut = next((m for m in CLAUSE.finditer(rest) if m.start() >= MIN_PIECE), None)
        if not cut:
            break
        ready.append(rest[:cut.start()].strip())
        rest = rest[cut.end():]
    return ready, rest


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, request: Request):
    """Как /api/chat, но ответ идёт по предложениям (NDJSON), как только Gemini их пишет:
    телефон и робот начинают говорить, не дожидаясь конца ответа."""
    check_auth(request)
    add_event("chat_user", body.message)
    cmd = drive_command(body.message) if robot.online else None
    started = time.time()

    def line(**kw):
        return json.dumps(kw, ensure_ascii=False) + "\n"

    async def gen():
        if cmd:
            l, r, secs, answer = cmd
            try:
                if secs:
                    await robot.send({"type": "drive", "left": l, "right": r, "seconds": secs})
                else:
                    await robot.send({"type": "stop"})
            except HTTPException as e:
                answer = e.detail
            add_event("chat_karen", answer)
            if body.robot_voice:
                await robot.send({"type": "say", "text": answer, "face": "обычное"})
            yield line(type="done", text=answer, face="обычное", drove=True)
            return

        now = datetime.datetime.now(TZ).strftime("%A, %d.%m.%Y, %H:%M")
        contents = [types.Content(role=t.role, parts=[types.Part(text=t.text)]) for t in body.history[-20:]]
        contents.append(types.Content(role="user", parts=[types.Part(text=f"[Сейчас: {now}] {body.message}")]))
        face, head_done, buf, spoken = "обычное", False, "", []

        async def emit(sentence):
            sentence = re.sub(r"\[[^\]]{1,20}\]\s*", "", sentence)   # метка настроения не там, где надо - не читаем вслух
            sentence = re.sub(r"[*_#`]", "", sentence).strip()
            if not sentence:
                return None
            spoken.append(sentence)
            if body.robot_voice and robot.online and HUSH_AT < started:
                try:
                    await robot.send({"type": "say", "text": sentence, "face": face})
                except HTTPException:
                    pass
            return line(type="sentence", text=sentence)

        def take_face_tag():
            nonlocal buf, face, head_done
            m = re.match(r"^\s*\[([^\]]*)\]\s*", buf)
            if m:
                tag = m.group(1).strip().lower()
                if tag in FACES:
                    face = tag
                buf = buf[m.end():]
                head_done = True
            elif not (buf.lstrip().startswith("[") and len(buf) < 40):
                head_done = True

        try:
            stream = await client.aio.models.generate_content_stream(model=MODEL, contents=contents, config=config)
            async for chunk in stream:
                buf += chunk.text or ""
                if not head_done:
                    take_face_tag()
                    if not head_done:
                        continue
                    yield line(type="face", face=face)
                ready, buf = speakable(buf, bool(spoken))
                for s in ready:
                    out = await emit(s)
                    if out:
                        yield out
            if not head_done:
                take_face_tag()
                yield line(type="face", face=face)
            for s in clause_pieces(buf):
                out = await emit(s)
                if out:
                    yield out
        except Exception as e:
            print("Ошибка Gemini:", e)
            if not spoken:
                yield line(type="error", text="Карен не смогла связаться с мозгом. Попробуй ещё раз.")
                return
        if not spoken:
            out = await emit("Ой, мои микросхемы задумались. Повтори, пожалуйста.")
            yield out
        text = " ".join(spoken)
        add_event("chat_karen", text)
        yield line(type="done", text=text, face=face)

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/say")
async def say(body: SayIn, request: Request):
    check_auth(request)
    voice = voice_for(body.text)
    comm = edge_tts.Communicate(body.text, voice, rate=RATE, pitch=PITCH)

    async def audio():
        async for ch in comm.stream():
            if ch["type"] == "audio":
                yield ch["data"]

    return StreamingResponse(audio(), media_type="audio/mpeg")
