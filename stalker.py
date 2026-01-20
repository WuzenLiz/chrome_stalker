import time
import json
import threading
import os
import re
import ctypes
from queue import Queue, Empty
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import win32gui
import mss
import mss.windows
from PIL import Image
from pynput import keyboard
import winreg
import redis
import dotenv
import sys

from flask import Flask
from logger import setup_logger

# ============================================================
# BOOTSTRAP
# ============================================================

if getattr(sys, 'frozen', False):
    base_path = Path(sys.executable).parent
else:
    base_path = Path(__file__).parent

dotenv.load_dotenv(base_path / ".env")

# ============================================================
# SYSTEM HARDENING
# ============================================================

if hasattr(ctypes.windll.user32, "SetProcessDPIAware"):
    ctypes.windll.user32.SetProcessDPIAware()

if hasattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext"):
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    ctypes.windll.user32.SetProcessDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    )

mss.windows.CAPTUREBLT = True
# ============================================================
# CONSTANTS
# ============================================================
OUTPUT_DIR = os.path.join(os.getenv("APPDATA"), "Agent")
REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
REG_PATH = r"Software\tBotAgent\v1"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# LOGGING
# ============================================================
log = setup_logger(
    "agent",
    os.path.join(OUTPUT_DIR, "logs", "agent.log")
)


# ============================================================
# RUNTIME FLAGS
# ============================================================

reload_evt = threading.Event()
stop_evt = threading.Event()

# ============================================================
# REGISTRY CONFIG
# ============================================================

@dataclass
class RegistryConfig:
    enabled: bool = True
    interval_sec: int = 5
    title_regex: str = r"(facebook|messenger|zalo)"
    fg_poll_interval: float = 0.5
    max_pubsub_false_countdown: int = 5
    max_files_rotation: int = 500


class ConfigManager:
    def __init__(self, ttl=2.0):
        self._lock = threading.Lock()
        self._last_load = 0.0
        self._ttl = ttl
        self._config = RegistryConfig()

    def get(self) -> RegistryConfig:
        with self._lock:
            if time.time() - self._last_load > self._ttl:
                self._config = self._read_registry()
                self._last_load = time.time()
            return self._config

    def _read_registry(self) -> RegistryConfig:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
                return RegistryConfig(
                    enabled=bool(winreg.QueryValueEx(key, "enabled")[0]),
                    interval_sec=int(winreg.QueryValueEx(key, "interval_sec")[0]),
                    title_regex=str(winreg.QueryValueEx(key, "title_regex")[0]),
                    fg_poll_interval=float(winreg.QueryValueEx(key, "fg_poll_interval")[0]),
                    max_pubsub_false_countdown=int(
                        winreg.QueryValueEx(key, "max_pubsub_false_countdown")[0]
                    ),
                    max_files_rotation=int(
                        winreg.QueryValueEx(key, "max_files_rotation")[0]
                    ),
                )
        except FileNotFoundError:
            return RegistryConfig()
        except Exception as e:
            log.error("Registry read failed: %s", e)
            return RegistryConfig()


config_mgr = ConfigManager()

# ============================================================
# EVENT MODEL
# ============================================================

@dataclass
class Event:
    name: str
    data: dict
    ts: float = field(default_factory=time.time)

# ============================================================
# WINDOW HELPERS
# ============================================================

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


_regex_cache = {}

def get_foreground_chrome_hwnd(title_regex: str):
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None

    try:
        if win32gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
            return None
    except Exception:
        return None

    title = win32gui.GetWindowText(hwnd).strip()
    if not title:
        return None

    pat = _regex_cache.get(title_regex)
    if not pat:
        pat = re.compile(title_regex, re.I)
        _regex_cache[title_regex] = pat

    return hwnd if pat.search(title) else None

# ============================================================
# REDIS PUBLISHER
# ============================================================

class RedisPublisher:
    def __init__(self, url: str, config: RegistryConfig):
        self._url = url
        self._config = config
        self._client = None
        self._lock = threading.Lock()
        self._last_failed = 0.0

    def _connect(self):
        self._client = redis.Redis.from_url(
            self._url,
            decode_responses=True,
            socket_timeout=5
        )

    def publish(self, channel: str, payload: dict):
        with self._lock:
            if time.time() - self._last_failed < self._config.max_pubsub_false_countdown:
                return
            try:
                if not self._client:
                    self._connect()
                    log.info("Redis connected")
                self._client.publish(channel, json.dumps(payload))
            except Exception as e:
                log.error("Redis publish failed: %s", e)
                self._client = None
                self._last_failed = time.time()

    def is_connected(self) -> bool:
        with self._lock:
            try:
                return bool(self._client and self._client.ping())
            except Exception:
                self._client = None
                return False

    def total_subscribers(self, channel: str) -> int:
        with self._lock:
            try:
                return self._client.pubsub_numsub(channel)[0][1] if self._client else 0
            except Exception:
                self._client = None
                return 0

# ============================================================
# CAPTURE ENGINE
# ============================================================

class CaptureEngine:
    os.makedirs(os.path.join(OUTPUT_DIR, "captures"), exist_ok=True)
    def capture(self, hwnd: int) -> str:
        rect = RECT()

        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
            )
            x1, y1, x2, y2 = rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            x1, y1, x2, y2 = win32gui.GetWindowRect(hwnd)

        if x2 <= x1 or y2 <= y1:
            raise RuntimeError("Invalid window rect")

        with mss.mss() as sct:
            img = sct.grab({
                "left": x1,
                "top": y1,
                "width": x2 - x1,
                "height": y2 - y1,
            })

        image = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(OUTPUT_DIR,"captures", f"capture_{ts}_hwnd{hwnd}.png")
        image.save(path)

        return path, ts

# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:
    def __init__(self, engine: CaptureEngine):
        self.engine = engine
        self._lock = threading.Lock()
        self._last_capture_ts = 0.0

    def handle(self, event: Event, redis_pub: RedisPublisher):
        if event.name != "KEY_ENTER":
            return

        cfg = config_mgr.get()
        if not cfg.enabled:
            return

        hwnd = event.data.get("hwnd")
        if not hwnd:
            return

        with self._lock:
            now = time.time()
            if now - self._last_capture_ts < cfg.interval_sec:
                return
            self._last_capture_ts = now

        time.sleep(0.3)

        path, ts = self.engine.capture(hwnd)
        redis_pub.publish("IMAGE_READY", {
            "type": "IMAGE_READY",
            "v": 1,
            "hwnd": hwnd,
            "path": path,
            "timestamp": ts
        })

        log.info("Captured: %s", path)

    def cleanup_old(self, max_files: int):
        files = sorted(Path(os.path.join(OUTPUT_DIR, "captures")).glob("*.png"), key=os.path.getmtime)
        for f in files[:-max_files]:
            f.unlink(missing_ok=True)

# ============================================================
# KEYBOARD THREAD
# ============================================================

def keyboard_thread(queue: Queue):
    cfg = config_mgr.get()
    last_cfg_check = 0.0

    def on_press(key):
        nonlocal cfg, last_cfg_check
        if key != keyboard.Key.enter:
            return

        now = time.time()
        if now - last_cfg_check > 1.0:
            cfg = config_mgr.get()
            last_cfg_check = now

        hwnd = get_foreground_chrome_hwnd(cfg.title_regex)
        if hwnd:
            queue.put(Event("KEY_ENTER", {"hwnd": hwnd}))

    with keyboard.Listener(on_press=on_press) as listener:
        while not stop_evt.is_set():
            time.sleep(0.1)
        listener.stop()

# ============================================================
# API SERVER
# ============================================================

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}

@app.route("/ping", methods=["GET"])
def ping():
    proc = psutil.Process()
    return {
        "status": "alive",
        "pid": proc.pid,
        "uptime_sec": round(time.time() - proc.create_time(), 2),
        "redis_connected": redis_pub.is_connected(),
    }

@app.route("/reload", methods=["POST"])
def reload_agent():
    reload_evt.set()
    return {"status": "reload_scheduled"}

def flask_thread():
    app.run(port=5000, debug=False, use_reloader=False)

# ============================================================
# MAIN
# ============================================================

def main():
    global redis_pub

    log.info("Capture Agent started")

    engine = CaptureEngine()
    orchestrator = Orchestrator(engine)

    cfg = config_mgr.get()
    redis_pub = RedisPublisher(REDIS_URL, cfg)
    orchestrator.cleanup_old(cfg.max_files_rotation)

    queue = Queue()

    threading.Thread(target=keyboard_thread, args=(queue,), daemon=True).start()
    threading.Thread(target=flask_thread, daemon=True).start()

    try:
        while True:
            if reload_evt.is_set():
                reload_evt.clear()
                cfg = config_mgr.get()
                redis_pub = RedisPublisher(REDIS_URL, cfg)
                orchestrator.cleanup_old(cfg.max_files_rotation)
                log.info("Runtime reloaded")
            try:
                event = queue.get(timeout=1)
                orchestrator.handle(event, redis_pub)
            except Empty:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        log.info("Capture Agent stopped")


if __name__ == "__main__":
    main()
