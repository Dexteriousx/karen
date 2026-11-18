"""Карен — веб-версия: лицо, чат и голос в браузере (телефон/компьютер).

Запуск локально:  GEMINI_API_KEY=... uvicorn app:app --reload
Railway:          переменные GEMINI_API_KEY и KAREN_PASSWORD, старт из Procfile.
"""
import os, re, datetime, secrets
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


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.get("/api/ping")
def ping(request: Request):
    check_auth(request)
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    check_auth(request)
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
