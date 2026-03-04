import os
import sys
import threading
from pathlib import Path

import dotenv

from logger import setup_logger
from regman import ConfigManager  # type: ignore
from tbot.commands import app_init
from tbot.redis_worker import redis_worker
from tbot.sender import sender_thread
from tbot.watchdog import watchdog_thread

# ============================================================
# BOOTSTRAP
# ============================================================
base_path = Path(__file__).resolve().parent
env_path = base_path / ".env"

if env_path.exists():
    dotenv.load_dotenv(env_path)
else:
    print(f".env not found at {env_path}")

REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REG_PATH = r"Software\tBotAgent\v1"

config_mgr = ConfigManager(reg_path=REG_PATH, ttl=2.0)

log = setup_logger(
    "telegram-agent",
    os.path.join(
        config_mgr.get().output_dir,
        config_mgr.get().log_tbot_path,
        "telegram-agent.log",
    ),
)

if not TG_TOKEN or not TG_CHAT_ID:
    log.error("Telegram bot token or chat ID not set in environment variables.")
    sys.exit(1)

API = f"https://api.telegram.org/bot{TG_TOKEN}"

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    app = app_init(TG_TOKEN, config_mgr, REDIS_URL)
    stop_evt = threading.Event()
    log.info("Telegram agent started, pid=%s", os.getpid())

    t = threading.Thread(
        target=redis_worker,
        args=(stop_evt, config_mgr, REDIS_URL),
        daemon=True,
        name="redis_worker",
    )
    t.start()

    ts = threading.Thread(
        target=sender_thread,
        args=(stop_evt, config_mgr, API, TG_CHAT_ID),
        daemon=True,
        name="sender",
    )
    ts.start()

    threading.Thread(
        target=watchdog_thread,
        args=(stop_evt, t, ts),
        daemon=True,
    ).start()

    try:
        app.run_polling()
    finally:
        stop_evt.set()
        log.info("Telegram agent stopped")
