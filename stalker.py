import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty

import dotenv
import mss
import mss.windows

from logger import setup_logger
from regman import ConfigManager, RegistryCfg  # type: ignore
from agent.capture import CaptureEngine
from agent.redis_pub import RedisPublisher
from agent.orchestrator import Orchestrator, keyboard_thread
import agent.api as api_module

# ============================================================
# BOOTSTRAP
# ============================================================
base_path = Path(__file__).resolve().parent
env_path = base_path / ".env"

if env_path.exists():
    dotenv.load_dotenv(env_path)
else:
    print(f".env not found at {env_path}")

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
config_mgr = ConfigManager(reg_path=REG_PATH, ttl=3)

# ============================================================
# LOGGING
# ============================================================
log = setup_logger(
    "agent",
    os.path.join(config_mgr.get().output_dir, config_mgr.get().log_agent_path, "agent.log"),
)

# ============================================================
# RUNTIME FLAGS
# ============================================================
reload_evt = threading.Event()
stop_evt = threading.Event()


# ============================================================
# WATCHDOG
# ============================================================

def watchdog_thread(kb_thread: threading.Thread) -> None:
    while not stop_evt.is_set():
        time.sleep(10)
        if not kb_thread.is_alive():
            log.warning("Keyboard thread died — restarting process")
            stop_evt.set()
            os.execv(sys.executable, [sys.executable] + sys.argv)


# ============================================================
# MAIN
# ============================================================

def main():
    global config_mgr

    log.info("Capture Agent started")

    engine = CaptureEngine(config_mgr)
    orchestrator = Orchestrator(engine, config_mgr)

    cfg = config_mgr.get()
    redis_pub = RedisPublisher(REDIS_URL, config_mgr)
    orchestrator.cleanup_old(cfg.max_files_rotation)

    api_module.init(redis_pub, engine, config_mgr, reload_evt)

    queue = Queue()

    kb_t = threading.Thread(
        target=keyboard_thread, args=(queue, config_mgr, stop_evt), daemon=True
    )
    kb_t.start()
    threading.Thread(target=watchdog_thread, args=(kb_t,), daemon=True).start()
    threading.Thread(target=api_module.flask_thread, daemon=False).start()

    try:
        while True:
            if reload_evt.is_set():
                reload_evt.clear()
                redis_pub.reset_connection()
                redis_pub = RedisPublisher(REDIS_URL, config_mgr)
                # Re-inject updated redis_pub into the running Flask module
                api_module.init(redis_pub, engine, config_mgr, reload_evt)
                orchestrator.cleanup_old(config_mgr.get().max_files_rotation)
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
