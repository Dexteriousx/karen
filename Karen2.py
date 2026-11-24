import os, re, sys, json, array, time, datetime, signal, atexit, asyncio, tempfile, threading, queue, subprocess, audioop
import edge_tts
import speech_recognition as sr
from google import genai
from google.genai import types

# ---------- Настройки ----------
MODEL = "gemini-3.5-flash-lite"   # быстрее, чем gemini-3.8-flash (~0.7 с против ~2 с); вернуть старую — поменяй здесь
VOICE = "ru-RU-SvetlanaNeural"
PITCH = "+65Hz"     # подогнано под голос Карен из cbkitchen01dbb.wav (~260 Гц); было "+10Hz"
# «Компьютерный» фильтр голоса Карен (ffmpeg). Выключить: VOICE_FX = ""
VOICE_FX = ("highpass=f=170,lowpass=f=5200,aecho=0.85:0.6:11:0.22,"
            "flanger=delay=1.5:depth=1.2:regen=-20:width=35:speed=0.25,"
            "equalizer=f=1800:t=q:w=1.2:g=2.5,volume=2.2,alimiter=limit=0.9")
RATE = "+5%"       # быстрее = "+15%", медленнее = "-10%"

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"],
    http_options=types.HttpOptions(timeout=20000),
)

CITY = "Алматы"   # город по умолчанию для погоды и местных вопросов

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
    "а не «+5 °C»; без диапазонов через многоточие)."
)

config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    max_output_tokens=500,
    thinking_config=types.ThinkingConfig(thinking_level="low"),
    tools=[types.Tool(google_search=types.GoogleSearch())],   # поиск Google: погода, новости, курсы...
)
chat = client.chats.create(model=MODEL, config=config)

# ---------- Состояние лица (читает «Лицо Карен.html») ----------
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "состояние.json")
_state_lock = threading.Lock()
thinking = threading.Event()   # True, пока ждём ответ Gemini

def set_state(face, talking=False, exit=False, voice=None):
    state = {"лицо": face, "говорит": talking, "выход": exit}
    if voice:
        state["голос"] = voice   # громкость по времени: волна повторяет голос
    with _state_lock:
        tmp = STATE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)   # атомарно: страница не прочтёт полфайла
        except OSError as e:
            print("Ошибка записи состояния:", e)

set_state("обычное")
shutting_down = threading.Event()   # после «выключись» лицо больше не трогаем
atexit.register(lambda: shutting_down.is_set() or set_state("обычное"))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

# ---------- Голос ----------
# Звук не ждёт целого mp3-файла: куски от edge-tts сразу идут в ffplay и играют, пока качаются.
# Пока звучит одно предложение, следующее уже скачивается.
text_q = queue.Queue()    # предложения, которые надо озвучить
audio_q = queue.Queue()   # по одной очереди кусков звука на предложение (None в конце)

def synth_worker():
    while True:
        text = text_q.get()
        chunks = queue.Queue()
        audio_q.put(chunks)            # проигрыватель может начинать, не дожидаясь конца
        async def go():
            async for ch in edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).stream():
                if ch["type"] == "audio":
                    chunks.put(ch["data"])
        try:
            asyncio.run(go())
        except Exception as e:
            print("Ошибка озвучки:", e)
        chunks.put(None)
        text_q.task_done()


VOICE_LAG_MS = 150   # примерная задержка запуска ffplay; если волна опережает голос - увеличить

def voice_levels(mp3, step_ms=20):
    """Громкость голоса каждые step_ms (0..1) - по ней лицо рисует волну во время речи."""
    try:
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "8000", "pipe:1"],
                             input=mp3, capture_output=True, timeout=20).stdout
    except Exception as e:
        print("Ошибка разбора громкости:", e)
        return []
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) // 2 * 2])
    n = 8 * step_ms
    lv = [(sum(x * x for x in a[i:i + n]) / n) ** 0.5 for i in range(0, len(a), n)]
    top = max(lv, default=0)
    if top < 1:
        return []
    return [round(min(1.0, v / top) ** 0.7, 2) for v in lv]

def play_worker():
    while True:
        chunks = audio_q.get()
        # фразу дослушиваем целиком (следующая тем временем уже качается), чтобы знать её громкость
        parts = []
        data = chunks.get()
        while data is not None:
            parts.append(data)
            data = chunks.get()
        audio = b"".join(parts)
        if audio:
            try:
                levels = voice_levels(audio)
                p = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"]
                                     + (["-af", VOICE_FX] if VOICE_FX else []),
                                     stdin=subprocess.PIPE)
                set_state("обычное", True, voice={"старт": int(time.time() * 1000) + VOICE_LAG_MS,
                                                  "шаг": 20, "уровни": levels})
                p.communicate(audio)
            except Exception as e:
                print("Ошибка воспроизведения:", e)
        if audio_q.empty() and not shutting_down.is_set():
            set_state("думает" if thinking.is_set() else "обычное")
        audio_q.task_done()

threading.Thread(target=synth_worker, daemon=True).start()
threading.Thread(target=play_worker, daemon=True).start()

def say(text):
    text = re.sub(r"[*_#`]", "", text).strip()
    if text:
        text_q.put(text)

def wait_until_spoken():
    text_q.join()
    audio_q.join()

# ---------- Gemini: говорим по предложениям, пока она ещё пишет ----------
def ask_and_speak(user_text):
    buffer = ""
    full = ""
    thinking.set()
    set_state("думает")
    try:
        now = datetime.datetime.now().strftime("%A, %d.%m.%Y, %H:%M")
        for chunk in chat.send_message_stream(f"[Сейчас: {now}] {user_text}"):
            full += chunk.text or ""
            buffer += chunk.text or ""
            parts = re.split(r"(?<=[.!?])\s+", buffer)
            for sentence in parts[:-1]:
                say(sentence)
            buffer = parts[-1]
    finally:
        thinking.clear()
    say(buffer)
    print(f"Карен: {full.strip()}")

# ---------- Микрофон ----------
MIC_GAIN = 8   # INMP441 тихий: x8 даёт пик ~16000 без перегруза
# настоящий микрофон (INMP441 на voiceHAT), а не USB-устройство: см. pcm.karenmic в ~/.asoundrc
_names = sr.Microphone.list_microphone_names()
MIC_INDEX = _names.index("karenmic") if "karenmic" in _names else None
print("Микрофон:", _names[MIC_INDEX] if MIC_INDEX is not None else "по умолчанию")
recognizer = sr.Recognizer()
recognizer.pause_threshold = 0.5         # было 0.7: столько тишины ждём, чтобы понять, что ты договорил
recognizer.non_speaking_duration = 0.4   # не больше pause_threshold
recognizer.dynamic_energy_threshold = False
recognizer.energy_threshold = 60   # тишина ~16-45, речь ~85-200 (до усиления)

print("--- Карен запущена. Говори. Чтобы выключить, скажи «выключись». ---")
say("Карен на связи.")
wait_until_spoken()

STOP_PHRASES = {"стоп", "выход", "пока", "до свидания"}
# команды «выключись» (ищутся внутри фразы, например «Карен, выключись»)
SLEEP_WORDS = ("выключ", "отключ", "спать", "спокойной ночи", "засыпай", "усни", "уснуть")

def go_to_sleep():
    """Прощается, лицо засыпает (Zzz), гаснет и закрывается, программа выходит."""
    say("Спокойной ночи! Ухожу в спящий режим, сладких снов.")
    wait_until_spoken()
    shutting_down.set()
    set_state("спит", exit=True)   # страница: 3 с спит, затем гаснет и закрывается
    time.sleep(6)                  # даём лицу доиграть анимацию
    # если браузер не дал странице закрыться сама - закрываем окно, открытое прямо на лицо
    import subprocess
    for pat in ("Лицо", "%D0%9B%D0%B8%D1%86%D0%BE"):
        subprocess.run(["pkill", "-f", pat], stderr=subprocess.DEVNULL)

while True:
    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            print("\nСлушаю...")
            set_state("слушает")
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)

        set_state("обычное")
        print("Распознаю...")
        audio = sr.AudioData(audioop.mul(audio.get_raw_data(), audio.sample_width, MIC_GAIN),
                             audio.sample_rate, audio.sample_width)
        raw = audio.get_raw_data()
        print(f"(звук: {len(raw)/audio.sample_rate/audio.sample_width:.1f} с, пик {audioop.max(raw, audio.sample_width)}, "
              f"средний {audioop.rms(raw, audio.sample_width)})")
        with open(os.path.join(tempfile.gettempdir(), "karen_last.wav"), "wb") as f:
            f.write(audio.get_wav_data())
        user_text = recognizer.recognize_google(audio, language="ru-RU")
        print(f"Вы: {user_text}")

        low = user_text.lower().strip(" .!?,")
        if low in STOP_PHRASES or any(w in low for w in SLEEP_WORDS):
            go_to_sleep()
            break

        t = time.time()
        ask_and_speak(user_text)
        wait_until_spoken()   # пока она говорит, микрофон не слушает
        set_state("обычное")
        print(f"(ответ занял {time.time() - t:.1f} сек)")

    except sr.UnknownValueError:
        set_state("обычное")
        print("Не удалось распознать речь, повторите.")
    except sr.WaitTimeoutError:
        print("Тишина... (превышено время ожидания)")
    except Exception as e:
        set_state("обычное")
        print(f"Произошла ошибка: {e}")