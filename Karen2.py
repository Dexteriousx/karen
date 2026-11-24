import os, re, sys, json, array, time, datetime, signal, atexit, asyncio, tempfile, threading, queue, subprocess, audioop
import socket
import concurrent.futures as cf
import edge_tts
import speech_recognition as sr
from google import genai
from google.genai import types

# ---------- Кэш DNS ----------
# На хотспоте телефона поиск адреса сайта иногда висит 5 с. Запоминаем адреса: после первого раза
# ответ мгновенный, а устаревший адрес обновляется в фоне, не задерживая Карен.
_getaddrinfo = socket.getaddrinfo
_dns = {}
DNS_TTL = 300

def _dns_refresh(key, args, kw):
    try:
        _dns[key] = (time.time(), _getaddrinfo(*args, **kw))
    except OSError:
        pass   # остаётся старый адрес

def _cached_getaddrinfo(*args, **kw):
    key = (args, tuple(sorted(kw.items())))
    hit = _dns.get(key)
    if hit:
        if time.time() - hit[0] > DNS_TTL:
            _dns[key] = (time.time(), hit[1])   # чтобы не запускать обновление много раз
            threading.Thread(target=_dns_refresh, args=(key, args, kw), daemon=True).start()
        return hit[1]
    res = _getaddrinfo(*args, **kw)
    _dns[key] = (time.time(), res)
    return res

socket.getaddrinfo = _cached_getaddrinfo

# ---------- Настройки ----------
MODEL = "gemini-3.5-flash-lite"   # быстрее, чем gemini-3.8-flash (~0.7 с против ~2 с); вернуть старую — поменяй здесь
VOICE = "ru-RU-SvetlanaNeural"
VOICE_KK = "kk-KZ-AigulNeural"   # казахский голос (мужской: kk-KZ-DauletNeural)
VOICE_EN = "en-US-JennyNeural"   # английский голос
# Язык, на котором Карен слушает: "ru" или "kk". Переключается голосом: «қазақша» / «орысша».
# Язык последней фразы: "kk", "ru" или "en". Карен сама узнаёт язык речи и отвечает на нём же.
LANG = os.environ.get("KAREN_LANG", "kk")   # язык приветствия и запасного распознавания
LANG_NAMES = {"kk": "казахский", "ru": "русский", "en": "английский"}
GOOGLE_CODES = {"kk": "kk-KZ", "ru": "ru-RU", "en": "en-US"}
KK_LETTERS = re.compile(r"[әғқңөұүһіӘҒҚҢӨҰҮҺІ]")   # буквы, которых нет в русском
LATIN = re.compile(r"[A-Za-z]")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")

def voice_for(text):
    """Голос по тексту: казахские буквы -> казахский, латиница -> английский, иначе по языку разговора."""
    if KK_LETTERS.search(text):
        return VOICE_KK
    if len(LATIN.findall(text)) > len(CYRILLIC.findall(text)):
        return VOICE_EN
    return VOICE_KK if LANG == "kk" else VOICE
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
    "Ты говоришь по-казахски, по-русски и по-английски. Отвечай на том языке, который указан в начале сообщения "
    "(«Язык ответа»), если собеседник сам не просит другой язык; 1-3 коротких предложения. "
    "По-казахски пиши грамотно, казахскими буквами (ә, ғ, қ, ң, ө, ұ, ү, һ, і). "
    "Твой ответ озвучивается голосом, поэтому: без смайликов, списков, звёздочек, заголовков, ссылок и "
    "названий сайтов; числа пиши так, чтобы их легко было прочитать вслух (например «плюс пять градусов», "
    "а не «+5 °C»; без диапазонов через многоточие)."
)

config = types.GenerateContentConfig(
    system_instruction=system_instruction,
    max_output_tokens=500,
    thinking_config=types.ThinkingConfig(thinking_level="minimal"),   # было "low": первая фраза на 0.1-0.25 с быстрее
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
            voice = voice_for(text)
            async for ch in edge_tts.Communicate(text, voice, rate=RATE, pitch=PITCH).stream():
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
        # Играем сразу, как пришёл первый кусок звука (~0.5 с быстрее, чем ждать всю фразу).
        # Громкость для волны на лице досчитываем по ходу и обновляем.
        parts, p, start, last_lv = [], None, 0, 0.0
        try:
            data = chunks.get()
            while data is not None:
                parts.append(data)
                if p is None:
                    p = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"]
                                         + (["-af", VOICE_FX] if VOICE_FX else []),
                                         stdin=subprocess.PIPE)
                    start = int(time.time() * 1000) + VOICE_LAG_MS
                try:
                    p.stdin.write(data)
                    p.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                if time.time() - last_lv > 0.3:
                    set_state("обычное", True, voice={"старт": start, "шаг": 20, "уровни": voice_levels(b"".join(parts))})
                    last_lv = time.time()
                data = chunks.get()
            if p:
                set_state("обычное", True, voice={"старт": start, "шаг": 20, "уровни": voice_levels(b"".join(parts))})
                try:
                    p.stdin.close()
                except OSError:
                    pass
                p.wait()
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
        for chunk in chat.send_message_stream(f"[Сейчас: {now}] [Язык ответа: {LANG_NAMES[LANG]}] {user_text}"):
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
recognizer.pause_threshold = 0.4         # было 0.5: столько тишины ждём, чтобы понять, что ты договорил
recognizer.non_speaking_duration = 0.3   # не больше pause_threshold
SEND_RATE = 16000   # микрофон пишет 48 кГц; для распознавания хватает 16 кГц - файл в 3 раза меньше, ответ ~1 с быстрее
recognizer.dynamic_energy_threshold = False
recognizer.energy_threshold = 60   # тишина ~16-45, речь ~85-200 (до усиления)

print(f"--- Карен запущена (язык: {LANG}). Говори. Чтобы выключить, скажи «выключись». ---")
say({"kk": "Карен байланыста.", "ru": "Карен на связи.", "en": "Karen online."}[LANG])
wait_until_spoken()

STOP_PHRASES = {"стоп", "выход", "пока", "до свидания", "тоқта", "сау бол", "сау болыңыз", "stop", "bye", "goodbye"}
# команды «выключись» (ищутся внутри фразы, например «Карен, выключись»)
SLEEP_WORDS = ("выключ", "отключ", "спать", "спокойной ночи", "засыпай", "усни", "уснуть",
               "өшір", "ұйықта", "қайырлы түн", "shut down", "turn off", "go to sleep", "good night")

def go_to_sleep():
    """Прощается, лицо засыпает (Zzz), гаснет и закрывается, программа выходит."""
    say({"kk": "Қайырлы түн! Ұйқы режиміне кеттім.", "ru": "Спокойной ночи! Ухожу в спящий режим, сладких снов.",
         "en": "Good night! Going to sleep mode, sweet dreams."}[LANG])
    wait_until_spoken()
    shutting_down.set()
    set_state("спит", exit=True)   # страница: 3 с спит, затем гаснет и закрывается
    time.sleep(6)                  # даём лицу доиграть анимацию
    # если браузер не дал странице закрыться сама - закрываем окно, открытое прямо на лицо
    import subprocess
    for pat in ("Лицо", "%D0%9B%D0%B8%D1%86%D0%BE"):
        subprocess.run(["pkill", "-f", pat], stderr=subprocess.DEVNULL)

TRANSCRIBE_PROMPT = ("Transcribe this speech exactly, in its original language and script. "
                     "The speaker uses Kazakh, Russian or English. Reply with one line: the language code "
                     "(kk, ru or en), a | sign, then the transcript. Never guess or invent words: if the audio is silent, "
                     "only noise, or the speech is unclear, reply exactly: none|")
MIN_SPEECH_RMS = 150   # средняя громкость после усиления; тише - это не речь, Gemini не отправляем

HEDGE_AFTER = 3.0   # если Gemini не ответил за столько секунд - параллельно спрашиваем Google
_pool = cf.ThreadPoolExecutor(4)

def _gemini_transcribe(wav):
    r = client.models.generate_content(
        model=MODEL, contents=[TRANSCRIBE_PROMPT, types.Part.from_bytes(data=wav, mime_type="audio/wav")],
        config=types.GenerateContentConfig(temperature=0, thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                                           http_options=types.HttpOptions(timeout=10000)))   # меньше 10 с Gemini не принимает
    lang, _, text = (r.text or "").strip().partition("|")
    lang, text = lang.strip().lower(), text.strip()
    if lang == "none" or not text:
        raise sr.UnknownValueError()
    if lang not in LANG_NAMES:
        raise ValueError(f"непонятный ответ: {r.text!r}")
    return lang, text

def _google_transcribe(audio, lang):
    return lang, recognizer.recognize_google(audio, language=GOOGLE_CODES[lang])

def transcribe(audio):
    """Речь -> (язык, текст). Gemini сам узнаёт язык (kk/ru/en). Если он медлит или сломался -
    берём Google на текущем языке (кто первый ответит)."""
    if audioop.rms(audio.get_raw_data(), audio.sample_width) < MIN_SPEECH_RMS:
        raise sr.UnknownValueError()   # тишина: иначе Gemini может «услышать» выдуманные слова
    gem = _pool.submit(_gemini_transcribe, audio.get_wav_data())
    try:
        return gem.result(timeout=HEDGE_AFTER)
    except cf.TimeoutError:
        print("Gemini медлит, параллельно спрашиваю Google")
    except sr.UnknownValueError:
        raise
    except Exception as e:
        print("Gemini не распознал, пробую Google:", e)
        return _google_transcribe(audio, LANG)
    goo = _pool.submit(_google_transcribe, audio, LANG)
    for f in cf.as_completed([gem, goo]):
        try:
            return f.result()
        except sr.UnknownValueError:
            if f is gem:
                raise   # Gemini уверен, что речи нет
        except Exception as e:
            print("Ошибка распознавания:", e)
    raise sr.UnknownValueError()

while True:
    try:
        with sr.Microphone(device_index=MIC_INDEX) as source:
            print("\nСлушаю...")
            set_state("слушает")
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)

        set_state("обычное")
        print("Распознаю...")
        t_heard = time.time()
        audio = sr.AudioData(audioop.mul(audio.get_raw_data(convert_rate=SEND_RATE), audio.sample_width, MIC_GAIN),
                             SEND_RATE, audio.sample_width)
        raw = audio.get_raw_data()
        print(f"(звук: {len(raw)/audio.sample_rate/audio.sample_width:.1f} с, пик {audioop.max(raw, audio.sample_width)}, "
              f"средний {audioop.rms(raw, audio.sample_width)})")
        with open(os.path.join(tempfile.gettempdir(), "karen_last.wav"), "wb") as f:
            f.write(audio.get_wav_data())
        LANG, user_text = transcribe(audio)
        print(f"(распознано за {time.time() - t_heard:.1f} с)")
        print(f"Вы ({LANG}): {user_text}")

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