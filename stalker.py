import time
import json
import logging
import threading
import os
import re
import ctypes
from queue import Queue, Empty
from dataclasses import dataclass

import psutil
import win32gui
import win32process
import mss
import mss.windows
from PIL import Image
from pynput import keyboard
import winreg
import redis

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
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("capture-agent")

# ============================================================
# CONSTANTS
# ============================================================

TARGET_PROCESS = "chrome.exe"
OUTPUT_DIR = os.path.join(os.getenv("APPDATA"), "tBotAgent", "captures")
REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
REG_PATH = r"Software\tBotAgent\v1"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# REGISTRY CONFIG
# ============================================================

@dataclass
class RegistryConfig:
    enabled: bool = True
    interval_sec: int = 5
    title_regex: str = r"(facebook|messenger|zalo)"
    fg_poll_interval: float = 0.5


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
    ts: float = time.time()

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


def get_foreground_chrome_hwnd(title_regex: str):
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = psutil.Process(pid)
        if proc.name().lower() != TARGET_PROCESS:
            return None
    except Exception:
        return None

    title = win32gui.GetWindowText(hwnd)
    if not title or not re.search(title_regex, title, re.I):
        return None

    return hwnd

# ============================================================
# REDIS PUBLISHER (SINGLETON)
# ============================================================

class RedisPublisher:
    def __init__(self, url: str):
        self._lock = threading.Lock()
        self._url = url
        self._client = None

    def _connect(self):
        self._client = redis.Redis.from_url(
            self._url,
            decode_responses=True,
            socket_timeout=5
        )

    def publish(self, channel: str, payload: dict):
        with self._lock:
            try:
                if not self._client:
                    self._connect()
                self._client.publish(channel, json.dumps(payload))
            except Exception as e:
                log.error("Redis publish failed: %s", e)
                self._client = None


redis_pub = RedisPublisher(REDIS_URL)

# ============================================================
# CAPTURE ENGINE
# ============================================================

class CaptureEngine:
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

        image = Image.frombytes(
            "RGB",
            img.size,
            img.bgra,
            "raw",
            "BGRX",
        )

        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(
            OUTPUT_DIR,
            f"capture_{ts}_hwnd{hwnd}.png"
        )

        image.save(path)

        redis_pub.publish("IMAGE_READY", {
            "hwnd": hwnd,
            "path": path,
            "timestamp": ts
        })

        return path

# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:
    def __init__(self, engine: CaptureEngine):
        self.engine = engine
        self._lock = threading.Lock()
        self._last_capture = 0.0

    def handle(self, event: Event):
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
            if now - self._last_capture < cfg.interval_sec:
                return
            self._last_capture = now

        # Allow the target window (e.g., after Enter key press) a brief moment
        # to update/render its contents before taking the screenshot.
        time.sleep(0.3)

        try:
            path = self.engine.capture(hwnd)
            log.info("Captured: %s", path)
        except Exception as e:
            log.error("Capture failed: %s", e)

# ============================================================
# THREADS
# ============================================================

def keyboard_thread(queue: Queue, stop_evt: threading.Event):
    def on_press(key):
        if key == keyboard.Key.enter:
            cfg = config_mgr.get()
            hwnd = get_foreground_chrome_hwnd(cfg.title_regex)
            if hwnd:
                queue.put(Event("KEY_ENTER", {"hwnd": hwnd}))

    with keyboard.Listener(on_press=on_press) as listener:
        while not stop_evt.is_set():
            time.sleep(0.1)
        listener.stop()

# ============================================================
# MAIN
# ============================================================

def main():
    log.info("Capture Agent started")

    engine = CaptureEngine()
    orchestrator = Orchestrator(engine)

    queue = Queue()
    stop_evt = threading.Event()

    t = threading.Thread(
        target=keyboard_thread,
        args=(queue, stop_evt),
        daemon=True
    )
    t.start()

    try:
        while True:
            try:
                event = queue.get(timeout=1)
                orchestrator.handle(event)
            except Empty:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        log.info("Capture Agent stopped")


if __name__ == "__main__":
    main()
