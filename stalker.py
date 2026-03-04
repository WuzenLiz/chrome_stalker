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

from regman import ConfigManager, RegistryCfg #type: ignore
# ============================================================

# BOOTSTRAP
# ============================================================
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
REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
REG_PATH = r"Software\tBotAgent\v1"

# ============================================================
# REGISTRY CONFIG
# ============================================================
config_mgr = ConfigManager(
    reg_path=REG_PATH,
    ttl=3,
)

# ============================================================
# LOGGING
# ============================================================
log = setup_logger(
    "agent",
    os.path.join(config_mgr.get().output_dir, config_mgr.get().log_agent_path, "agent.log")
)

# ============================================================
# RUNTIME FLAGS
# ============================================================
reload_evt = threading.Event()
stop_evt = threading.Event()

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
    def __init__(self, url: str, config: RegistryCfg):
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
        cfg = config_mgr.get()
        with self._lock:
            if time.time() - self._last_failed < cfg.max_pubsub_false_countdown:
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

    def reset_connection(self):
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._last_failed = 0.0

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
    def __init__(self):
        os.makedirs(os.path.join(config_mgr.get().output_dir, "captures"), exist_ok=True)

    def capture_primary(self) -> tuple:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
        image = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(config_mgr.get().output_dir, "captures", f"capture_{ts}_screen.png")
        image.save(path)
        return path, ts

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
        path = os.path.join(config_mgr.get().output_dir, "captures", f"capture_{ts}_hwnd{hwnd}.png")
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
        files = sorted(Path(os.path.join(config_mgr.get().output_dir, "captures")).glob("*.png"), key=os.path.getmtime)
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

@app.route("/self_restart", methods=["POST"])
def self_restart():
    def _restart():
        time.sleep(0.5)
        log.info("Self-restarting agent...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_restart, daemon=True).start()
    return {"status": "restarting"}

@app.route("/manual_shot", methods=["POST"])
def manual_shot():
    try:
        hwnd = win32gui.GetForegroundWindow()
        try:
            path, ts = engine.capture(hwnd) if hwnd else engine.capture_primary()
        except Exception:
            path, ts = engine.capture_primary()
        redis_pub.publish("IMAGE_READY", {
            "type": "IMAGE_READY",
            "v": 1,
            "hwnd": hwnd or 0,
            "path": path,
            "timestamp": ts,
        })
        log.info("Manual shot: %s", path)
        return {"status": "ok", "path": path}
    except Exception as e:
        log.error("Manual shot failed: %s", e)
        return {"status": "error", "message": str(e)}, 500

@app.route("/reconnect_redis", methods=["POST"])
def reconnect_redis():
    redis_pub.reset_connection()
    log.info("Redis connection reset")
    return {"status": "ok"}

def flask_thread():
    try:
        log.info("Starting local API server on 127.0.0.1:18080")
        app.run(
            host="127.0.0.1",
            port=18080,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        log.exception("Flask server crashed: %s", e)
        time.sleep(5)
# ============================================================
# MAIN
# ============================================================

def main():
    global redis_pub, engine

    log.info("Capture Agent started")

    engine = CaptureEngine()
    orchestrator = Orchestrator(engine)

    cfg = config_mgr.get()
    redis_pub = RedisPublisher(REDIS_URL, cfg)
    orchestrator.cleanup_old(cfg.max_files_rotation)

    queue = Queue()

    threading.Thread(target=keyboard_thread, args=(queue,), daemon=True).start()
    threading.Thread(target=flask_thread, daemon=False).start()

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
