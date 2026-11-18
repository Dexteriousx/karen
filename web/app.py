"""Карен — веб-версия: лицо, чат и голос в браузере (телефон/компьютер).

Запуск локально:  GEMINI_API_KEY=... uvicorn app:app --reload
Railway:          переменные GEMINI_API_KEY и KAREN_PASSWORD, старт из Procfile.
"""
import os, re, json, time, datetime, secrets, threading, subprocess, asyncio
from zoneinfo import ZoneInfo

import edge_tts
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------- Настройки ----------
MODEL = os.environ.get("KAREN_MODEL", "gemini-3.5-flash-lite")
VOICE = "ru-RU-SvetlanaNeural"
PITCH = "+65Hz"
RATE = "+5%"
CITY = os.environ.get("KAREN_CITY", "Алматы")
TZ = ZoneInfo(os.environ.get("KAREN_TZ", "Asia/Almaty"))
PASSWORD = os.environ.get("KAREN_PASSWORD", "")   # пусто = без пароля (только для локального теста!)

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
    "Текущие дата и время приходят в начале каждого сообщения в квадратных скобках. "
    "Сначала дай полезный ответ, потом, если уместно, короткая добрая шутка. "
    "Отвечай по-русски, 1-3 коротких предложения. "
    "Твой ответ озвучивается голосом, поэтому: без смайликов, списков, звёздочек, заголовков, ссылок и "
    "названий сайтов; числа пиши так, чтобы их легко было прочитать вслух (например «плюс пять градусов», "
    "а не «+5 °C»; без диапазонов через многоточие). "
    "В самом начале ответа поставь одну метку настроения в квадратных скобках: "
    "[радость], [грусть], [удивление], [думает], [подмигивает] или [обычное]."
)

config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    max_output_tokens=500,
    thinking_config=types.ThinkingConfig(thinking_level="low"),
    tools=[types.Tool(google_search=types.GoogleSearch())],
)

FACES = {"радость", "грусть", "удивление", "думает", "подмигивает", "обычное", "злость"}

app = FastAPI(title="Карен")
HERE = os.path.dirname(os.path.abspath(__file__))


def check_auth(request: Request):
    if PASSWORD and not secrets.compare_digest(request.headers.get("x-karen-password", ""), PASSWORD):
        raise HTTPException(401, "Нужен пароль")


class Turn(BaseModel):
    role: str = Field(pattern="^(user|model)$")
    text: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=40)


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    face: str = Field(default="радость", max_length=20)


# ================== Робот (только на Raspberry Pi: KAREN_ROBOT=1) ==================
# Колёса: драйвер L298N, пины как в kr.py. Лицо на экране робота: файл состояние.json.
ROBOT = os.environ.get("KAREN_ROBOT") == "1"
STATE_FILE = os.environ.get("KAREN_STATE_FILE", os.path.expanduser("~/Жүктемелер/состояние.json"))
VOICE_FX = ("highpass=f=170,lowpass=f=5200,aecho=0.85:0.6:11:0.22,"
            "flanger=delay=1.5:depth=1.2:regen=-20:width=35:speed=0.25,"
            "equalizer=f=1800:t=q:w=1.2:g=2.5,volume=2.2,alimiter=limit=0.9")
left = right = None
motor_error = ""
if ROBOT:
    try:
        from gpiozero import Motor
        left = Motor(forward=5, backward=6, pwm=True)                 # IN1=GPIO5, IN2=GPIO6
        right = Motor(forward=22, backward=23, enable=13, pwm=True)   # IN3=GPIO22, IN4=GPIO23, ENB=GPIO13
    except Exception as e:   # например, колёса уже заняты kr.py
        motor_error = f"Колёса недоступны: {e}"
        print(motor_error)

_motor_lock = threading.Lock()
_last_drive = 0.0
_drive_until = 0.0   # для голосовых команд: ехать до этого времени


def set_motors(l: float, r: float):
    if not left:
        return
    with _motor_lock:
        for m, v in ((left, l), (right, r)):
            if v > 0.02:
                m.forward(min(v, 1.0))
            elif v < -0.02:
                m.backward(min(-v, 1.0))
            else:
                m.stop()


def _watchdog():
    # страховка: если телефон перестал слать команды (отпустили кнопку, пропала связь) — стоп
    moving = False
    while True:
        time.sleep(0.1)
        now = time.time()
        active = now - _last_drive < 0.5 or now < _drive_until
        if moving and not active:
            set_motors(0, 0)
        moving = active


if left:
    threading.Thread(target=_watchdog, daemon=True).start()


def set_face(face: str, talking: bool = False):
    if not ROBOT:
        return
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"лицо": face, "говорит": talking, "выход": False}, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print("Ошибка записи состояния:", e)


_speak_lock = threading.Lock()


def robot_say(text: str, face: str = "радость"):
    """Озвучить через динамик робота (в фоне)."""
    def run():
        with _speak_lock:
            async def mp3():
                data = b""
                async for ch in edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).stream():
                    if ch["type"] == "audio":
                        data += ch["data"]
                return data
            try:
                audio = asyncio.run(mp3())
                set_face(face, True)
                subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0", "-af", VOICE_FX],
                               input=audio, timeout=120)
            except Exception as e:
                print("Ошибка озвучки на роботе:", e)
            finally:
                set_face("обычное")
    threading.Thread(target=run, daemon=True).start()


DRIVE_WORDS = [  # (слова, левое, правое, ответ)
    (("стоп", "стой", "остановись"), 0, 0, "Стою."),
    (("назад",), -1, -1, "Еду назад, Планктон."),
    (("налево", "влево"), -1, 1, "Поворачиваю налево."),
    (("направо", "вправо"), 1, -1, "Поворачиваю направо."),
    (("вперед", "езжай", "поехали", "едь"), 1, 1, "Еду, Планктон."),
]


def voice_drive(text: str):
    global _drive_until
    if not left:
        return None
    t = text.lower().replace("ё", "е")
    if len(t.split()) > 4:   # длинная фраза («что было неделю назад») — это вопрос, а не команда
        return None
    for words, l, r, answer in DRIVE_WORDS:
        if any(w in t for w in words):
            secs = 0 if l == r == 0 else (0.6 if l != r else 2.0)
            _drive_until = time.time() + secs
            set_motors(l * 0.7, r * 0.7)
            return answer
    return None


class DriveIn(BaseModel):
    left: float = Field(ge=-1, le=1)
    right: float = Field(ge=-1, le=1)


class FaceIn(BaseModel):
    face: str = Field(max_length=20)


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/api/ping")
def ping(request: Request):
    check_auth(request)
    return {"ok": True, "robot": ROBOT, "motors": left is not None, "motor_error": motor_error}


@app.post("/api/drive")
def drive(body: DriveIn, request: Request):
    check_auth(request)
    global _last_drive, _drive_until
    if not left:
        raise HTTPException(503, motor_error or "Колёса есть только на роботе")
    _drive_until = 0
    _last_drive = time.time()
    set_motors(body.left, body.right)
    return {"ok": True}


@app.post("/api/face")
def face(body: FaceIn, request: Request):
    check_auth(request)
    if not ROBOT:
        raise HTTPException(503, "Лицо робота доступно только на роботе")
    set_face(body.face)
    return {"ok": True}


@app.post("/api/robot-say")
def robot_say_api(body: SayIn, request: Request):
    check_auth(request)
    if not ROBOT:
        raise HTTPException(503, "Динамик робота доступен только на роботе")
    robot_say(body.text, body.face if body.face in FACES else "радость")
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    check_auth(request)
    answer = voice_drive(body.message)
    if answer:
        return {"text": answer, "face": "радость", "drove": True}
    now = datetime.datetime.now(TZ).strftime("%A, %d.%m.%Y, %H:%M")
    contents = [types.Content(role=t.role, parts=[types.Part(text=t.text)]) for t in body.history[-20:]]
    contents.append(types.Content(role="user", parts=[types.Part(text=f"[Сейчас: {now}] {body.message}")]))
    try:
        resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
    except Exception as e:
        print("Ошибка Gemini:", e)
        raise HTTPException(502, "Карен не смогла связаться с мозгом. Попробуй ещё раз.")
    text = (resp.text or "").strip()
    face = "радость"
    m = re.match(r"^\s*\[([^\]]+)\]\s*", text)
    if m:
        if m.group(1).strip().lower() in FACES:
            face = m.group(1).strip().lower()
        text = text[m.end():]
    text = re.sub(r"[*_#`]", "", text).strip() or "Ой, мои микросхемы задумались. Повтори, пожалуйста."
    return {"text": text, "face": face}


@app.post("/api/say")
async def say(body: SayIn, request: Request):
    check_auth(request)
    comm = edge_tts.Communicate(body.text, VOICE, rate=RATE, pitch=PITCH)

    async def audio():
        async for ch in comm.stream():
            if ch["type"] == "audio":
                yield ch["data"]

    return StreamingResponse(audio(), media_type="audio/mpeg")
