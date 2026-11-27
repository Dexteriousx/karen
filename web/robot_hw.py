"""Железо робота Карен (только Raspberry Pi): колёса, лицо на экране, динамик, датчики.

Используют: robot_agent.py (робот подключается к сайту на Render) и app.py с KAREN_ROBOT=1 (локальный пульт).
"""
import os, re, json, time, array, glob, queue, asyncio, threading, subprocess, concurrent.futures

import edge_tts

VOICE = "ru-RU-SvetlanaNeural"
VOICE_KK = "kk-KZ-AigulNeural"   # казахский голос
VOICE_EN = "en-US-JennyNeural"   # английский голос
KK_LETTERS = re.compile(r"[әғқңөұүһіӘҒҚҢӨҰҮҺІ]")
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
VOICE_FX = ("highpass=f=170,lowpass=f=5200,aecho=0.85:0.6:11:0.22,"
            "flanger=delay=1.5:depth=1.2:regen=-20:width=35:speed=0.25,"
            "equalizer=f=1800:t=q:w=1.2:g=2.5,volume=2.2,alimiter=limit=0.9")
HOME = os.path.expanduser("~")
STATE_FILE = os.environ.get("KAREN_STATE_FILE", os.path.join(HOME, "Жүктемелер", "состояние.json"))
KAREN_START = os.path.join(HOME, "karen-start.sh")
KAREN_LOG = os.path.join(HOME, ".cache", "karen-logs", "karen.log")
FACES = {"обычное", "грусть", "злость", "удивление", "думает", "сонная", "слушает", "подмигивает"}
DEADMAN = 0.7   # сек без команды «ехать» -> стоп (отпустили кнопку, пропала связь)

# ---------- Колёса (драйвер L298N, пины как в kr.py) ----------
left = right = None
motor_error = ""
try:
    if not os.path.exists("/dev/gpiochip0"):
        raise RuntimeError("это не Raspberry Pi")
    from gpiozero import Motor
    left = Motor(forward=6, backward=5, pwm=True)                 # IN2=GPIO6 вперёд, IN1=GPIO5 назад (проверено 27.11.2026, не менять)
    right = Motor(forward=22, backward=23, enable=13, pwm=True)   # IN3=GPIO22 вперёд, IN4=GPIO23 назад, ENB=GPIO13 (проверено, не менять)
except Exception as e:   # например, колёса уже заняты kr.py
    motor_error = f"Колёса недоступны: {e}"
    print(motor_error)

_lock = threading.Lock()
_drive_until = 0.0
motion = {"left": 0.0, "right": 0.0}


def _apply(l, r):
    if not left:
        return
    with _lock:
        for m, v in ((left, l), (right, r)):
            if v > 0.02:
                m.forward(min(v, 1.0))
            elif v < -0.02:
                m.backward(min(-v, 1.0))
            else:
                m.stop()
        motion["left"], motion["right"] = round(l, 2), round(r, 2)


def drive(l: float, r: float, seconds: float = DEADMAN):
    """Ехать; если за `seconds` не придёт новая команда — стоп."""
    global _drive_until
    if not left:
        raise RuntimeError(motor_error or "Колёса не подключены")
    l, r = max(-1, min(1, l)), max(-1, min(1, r))
    _drive_until = time.time() + (seconds if (l or r) else 0)
    _apply(l, r)


def stop():
    global _drive_until
    _drive_until = 0
    _apply(0, 0)


def _watchdog():
    while True:
        time.sleep(0.1)
        if (motion["left"] or motion["right"]) and time.time() > _drive_until:
            _apply(0, 0)


if left:
    threading.Thread(target=_watchdog, daemon=True).start()

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
    """Короткая фраза «вперёд/назад/налево/направо/стоп» -> (левое, правое, секунд, ответ) или None."""
    t = text.lower().replace("ё", "е")
    if len(t.split()) > 4:   # «что было неделю назад» — это вопрос, а не команда
        return None
    for words, l, r, secs, answer in DRIVE_WORDS:
        if any(re.search(r"\b" + w, t) for w in words):
            return l * 0.7, r * 0.7, secs, answer
    return None


# ---------- Лицо на экране робота (читает «Лицо Карен.html») ----------
def set_face(face: str, talking: bool = False, voice: dict | None = None):
    if face == "говорит":   # кнопка «говорит» на сайте: только волна на весь экран, пока не выберут другое
        talking = True
    elif face not in FACES:
        return
    state = {"лицо": face, "говорит": talking, "выход": False}
    if voice:
        state["голос"] = voice   # громкость по времени: волна повторяет голос
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print("Ошибка записи состояния:", e)


def get_face():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        return s.get("лицо", "обычное"), bool(s.get("говорит"))
    except Exception:
        return "обычное", False


# ---------- Голос из динамика робота ----------
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


_synth_pool = concurrent.futures.ThreadPoolExecutor(3)   # несколько фраз качаются одновременно
_play_q = queue.Queue()   # звук фраз (future) строго по порядку


def _synth(text):
    async def mp3():
        data = b""
        async for ch in edge_tts.Communicate(text, voice_for(text), rate=RATE, pitch=PITCH).stream():
            if ch["type"] == "audio":
                data += ch["data"]
        return data
    try:
        return asyncio.run(mp3())
    except Exception as e:
        print("Ошибка озвучки:", e)
        return b""


def _play_worker():
    while True:
        audio = _play_q.get().result()
        if audio:
            try:
                levels = voice_levels(audio)
                set_face("обычное", True,   # во время речи лицо всегда обычное, волна голоса под ним
                         voice={"старт": int(time.time() * 1000) + VOICE_LAG_MS, "шаг": 20, "уровни": levels})
                subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0", "-af", VOICE_FX],
                               input=audio, timeout=120)
            except Exception as e:
                print("Ошибка озвучки:", e)
        if _play_q.empty():
            set_face("обычное")


threading.Thread(target=_play_worker, daemon=True).start()


def say(text: str, face: str = "обычное"):
    """Озвучить фразу через динамик робота (в фоне, по очереди)."""
    text = re.sub(r"[*_#`]", "", text).strip()
    if text:
        _play_q.put(_synth_pool.submit(_synth, text))   # качаем сразу, играем по очереди


# ---------- Голосовая Карен (Karen2.py: микрофон + лицо на весь экран) ----------
def karen_running() -> bool:
    return subprocess.run(["pgrep", "-f", r"Karen2\.py"], capture_output=True).returncode == 0


def karen_start():
    subprocess.Popen(["bash", KAREN_START], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def karen_stop():
    subprocess.run(["pkill", "-f", r"karen-env/bin/python -u .*Karen2\.py"])
    set_face("обычное")


# ---------- Датчики ----------
def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


_prev_cpu = None


def _cpu_percent():
    global _prev_cpu
    parts = _read("/proc/stat").split("\n", 1)[0].split()[1:]
    if not parts:
        return None
    vals = list(map(int, parts))
    idle, total = vals[3] + vals[4], sum(vals)
    pct = None
    if _prev_cpu:
        di, dt = idle - _prev_cpu[0], total - _prev_cpu[1]
        pct = round(100 * (1 - di / dt), 1) if dt else 0.0
    _prev_cpu = (idle, total)
    return pct


def telemetry() -> dict:
    temp = _read("/sys/class/thermal/thermal_zone0/temp").strip()
    mem = dict(re.findall(r"^(\w+):\s+(\d+)", _read("/proc/meminfo"), re.M))
    wifi = None
    for line in _read("/proc/net/wireless").splitlines()[2:]:
        p = line.split()
        if len(p) > 3:
            wifi = int(float(p[3]))   # dBm
    throttled = None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        throttled = int(out.split("=")[1], 16) if "=" in out else None
    except Exception:
        pass
    face, talking = get_face()
    total, avail = int(mem.get("MemTotal", 0)), int(mem.get("MemAvailable", 0))
    return {
        "cpu_temp": round(int(temp) / 1000, 1) if temp.isdigit() else None,
        "cpu": _cpu_percent(),
        "ram": round(100 * (1 - avail / total), 1) if total else None,
        "uptime": int(float(_read("/proc/uptime", "0").split()[0])),
        "wifi_dbm": wifi,
        "undervoltage": bool(throttled & 0x1) if throttled is not None else None,
        "face": face,
        "talking": talking,
        "motors": left is not None,
        "motor_error": motor_error,
        "motion": dict(motion),
        "karen_voice": karen_running(),
    }


def follow_karen_log():
    """Генератор новых реплик голосовой Карен из её лога: ('user'|'karen', текст)."""
    pos = os.path.getsize(KAREN_LOG) if os.path.exists(KAREN_LOG) else 0
    while True:
        try:
            size = os.path.getsize(KAREN_LOG)
            if size < pos:   # лог начат заново (перезапуск Карен)
                pos = 0
            if size > pos:
                with open(KAREN_LOG, encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                for line in chunk.splitlines():
                    if line.startswith("Вы: "):
                        yield "user", line[4:].strip()
                    elif line.startswith("Карен: "):
                        yield "karen", line[7:].strip()
        except OSError:
            pass
        yield None
