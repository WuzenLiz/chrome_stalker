import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue

from pynput import keyboard

from regman import ConfigManager  # type: ignore
from agent.capture import CaptureEngine, get_foreground_chrome_hwnd
from agent.redis_pub import RedisPublisher
from agent import metrics

log = logging.getLogger("agent")


# ============================================================
# EVENT MODEL
# ============================================================

@dataclass
class Event:
    name: str
    data: dict
    ts: float = field(default_factory=time.time)


# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:
    def __init__(self, engine: CaptureEngine, config_mgr: ConfigManager):
        self.engine = engine
        self._config_mgr = config_mgr
        self._lock = threading.Lock()
        self._last_capture_ts = 0.0

    def handle(self, event: Event, redis_pub: RedisPublisher) -> None:
        if event.name != "KEY_ENTER":
            return

        cfg = self._config_mgr.get()
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
        metrics.inc("total_captures")
        redis_pub.publish(cfg.stream_name, {
            "type": "IMAGE_READY",
            "v": "1",
            "hwnd": str(hwnd),
            "path": path,
            "timestamp": ts,
        })
        log.info("Captured: %s", path)

    def cleanup_old(self, max_files: int) -> None:
        cfg = self._config_mgr.get()
        files = sorted(
            Path(os.path.join(cfg.output_dir, "captures")).glob("*.png"),
            key=os.path.getmtime,
        )
        for f in files[:-max_files]:
            f.unlink(missing_ok=True)


# ============================================================
# KEYBOARD THREAD
# ============================================================

def keyboard_thread(
    queue: Queue,
    config_mgr: ConfigManager,
    stop_evt: threading.Event,
) -> None:
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
