import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import dotenv
import redis
import requests
from telegram.ext import Application, CommandHandler

from logger import setup_logger
from regman import ConfigManager  # type: ignore

if getattr(sys, "frozen", False):
    base_path = Path(sys.executable).parent
else:
    base_path = Path(__file__).parent

dotenv.load_dotenv(base_path / ".env")

REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REG_PATH = r"Software\tBotAgent\v1"
_last_send = 0.0
_send_lock = threading.Lock()

configmgr = ConfigManager(reg_path=REG_PATH, ttl=2.0)

log = setup_logger(
    "telegram-agent",
    os.path.join(configmgr.get().output_dir, configmgr.get().log_tbot_path, "telegram-agent.log"),
)

if not TG_TOKEN or not TG_CHAT_ID:
    log.error("Telegram bot token or chat ID not set in environment variables.")
    sys.exit(1)

API = f"https://api.telegram.org/bot{TG_TOKEN}"


class DedupCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}
        self._last_purge = 0.0

    def seen(self, key: str, ttl_sec: int) -> bool:
        if not key:
            return False
        now = time.time()
        with self._lock:
            ts = self._items.get(key)
            if ts and now - ts <= ttl_sec:
                return True
            self._items[key] = now

            if now - self._last_purge > max(5.0, ttl_sec / 2):
                cutoff = now - ttl_sec
                self._items = {k: v for k, v in self._items.items() if v >= cutoff}
                self._last_purge = now
        return False


def send_photo(path: str, config_mgr: ConfigManager) -> bool:
    global _last_send
    for _ in range(3):
        with _send_lock:
            now = time.time()
            interval = config_mgr.get().send_interval_sec
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
                    timeout=15,
                )
            if r.status_code == 429:
                retry = r.json().get("parameters", {}).get("retry_after", 1)
                log.warning("Telegram rate limit, retry in %ss", retry)
                time.sleep(retry)
                continue
            r.raise_for_status()
            return True
        except Exception as e:
            log.error("Failed to send photo %s: %s", path, e)
    return False


async def cleanup_old_photos(update, context, config_mgr: ConfigManager) -> None:
    cfg = config_mgr.get()
    minutes = cfg.delete_minutes
    max_minutes = cfg.max_cleanup_minutes

    if context.args:
        try:
            minutes = int(context.args[0])
            minutes = max(1, min(minutes, max_minutes))
        except ValueError:
            await update.message.reply_text("Invalid value. Usage: /cleanup <minutes>")
            return

    cutoff_ts = time.time() - (minutes * 60)

    output_dir = os.path.join(os.getenv("APPDATA", ""), "Agent", "captures")
    if not os.path.isdir(output_dir):
        await update.message.reply_text("Capture directory does not exist.")
        return

    deleted = 0
    failed = 0
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

    await update.message.reply_text(
        f"Cleanup done\nOlder than: {minutes} minutes\nDeleted: {deleted}\nFailed: {failed}"
    )


async def set_config(update, context, config_mgr: ConfigManager) -> None:
    if len(context.args) != 2:
        await update.message.reply_text("Invalid usage. Usage: /set_config <key> <value>")
        return

    key = context.args[0]
    value_str = context.args[1]
    if value_str.lower() in ("true", "false"):
        value = value_str.lower() == "true"
    else:
        try:
            value = float(value_str) if "." in value_str else int(value_str)
        except ValueError:
            value = value_str

    config_mgr.write(key, value)
    await update.message.reply_text(f"Set configuration: {key} = {value}")


async def ping_agent(update, context):
    try:
        r = requests.get("http://localhost:18080/ping", timeout=3).json()
        await update.message.reply_text(
            f"Agent alive\nPID: {r['pid']}\nUptime: {r['uptime_sec']}s\nRedis: {r['redis_connected']}"
        )
    except Exception:
        await update.message.reply_text("Agent offline")


def get_redis_status(config_mgr: ConfigManager) -> dict:
    cfg = config_mgr.get()
    r = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_keepalive=True,
        health_check_interval=cfg.redis_healthcheck_sec,
        socket_connect_timeout=cfg.redis_connect_timeout_sec,
        socket_timeout=cfg.redis_socket_timeout_sec,
    )
    try:
        r.ping()
        groups = r.xinfo_groups(cfg.redis_stream_name)
        has_group = any(g.get("name") == cfg.redis_consumer_group for g in groups)
        return {"ok": True, "stream": cfg.redis_stream_name, "group_exists": has_group}
    except redis.ResponseError as e:
        if "no such key" in str(e).lower():
            return {"ok": True, "stream": cfg.redis_stream_name, "group_exists": False}
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        r.close()


async def redis_status(update, context):
    status = get_redis_status(configmgr)
    if status.get("ok"):
        await update.message.reply_text(
            f"Redis OK\nStream: {status.get('stream')}\nGroup exists: {status.get('group_exists')}"
        )
        return
    await update.message.reply_text(f"Redis error: {status.get('error')}")


async def reload_agent(update, context):
    try:
        requests.post("http://localhost:18080/reload", timeout=3)
        await update.message.reply_text("Agent reload requested")
    except Exception:
        await update.message.reply_text("Agent not reachable")


async def last_log_agent(update, context):
    n = int(context.args[0]) if context.args else 20
    log_file = os.path.join(configmgr.get().output_dir, configmgr.get().log_agent_path, "agent.log")
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        await update.message.reply_text("".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error reading log: {e}")


async def last_log_bot(update, context):
    n = int(context.args[0]) if context.args else 20
    log_file = os.path.join(configmgr.get().output_dir, configmgr.get().log_tbot_path, "telegram-agent.log")
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        await update.message.reply_text("".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Error reading log: {e}")


def app_init(token: str, config_mgr: ConfigManager):
    app = Application.builder().token(token).build()

    async def cleanup_handler(update, context):
        await cleanup_old_photos(update, context, config_mgr)

    async def set_config_handler(update, context):
        await set_config(update, context, config_mgr)

    app.add_handler(CommandHandler("cleanup", cleanup_handler))
    app.add_handler(CommandHandler("set_config", set_config_handler))
    app.add_handler(CommandHandler("ping_agent", ping_agent))
    app.add_handler(CommandHandler("hot_reload_agent", reload_agent))
    app.add_handler(CommandHandler("last_log_agent", last_log_agent))
    app.add_handler(CommandHandler("last_log_bot", last_log_bot))
    app.add_handler(CommandHandler("redis_status", redis_status))
    return app


def _safe_close(redis_client):
    if not redis_client:
        return
    try:
        redis_client.close()
    except Exception:
        pass


def _ensure_group(redis_client, cfg):
    try:
        redis_client.xgroup_create(
            name=cfg.redis_stream_name,
            groupname=cfg.redis_consumer_group,
            id="0",
            mkstream=True,
        )
        log.info(
            "Created stream/group: %s / %s",
            cfg.redis_stream_name,
            cfg.redis_consumer_group,
        )
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def _ack(redis_client, cfg, message_id: str):
    redis_client.xack(cfg.redis_stream_name, cfg.redis_consumer_group, message_id)


def _process_stream_message(redis_client, cfg, message_id: str, fields: dict, dedup: DedupCache) -> bool:
    payload_raw = fields.get("payload")
    if not payload_raw:
        log.warning("Dropping malformed stream entry %s: missing payload", message_id)
        _ack(redis_client, cfg, message_id)
        return True

    try:
        data = json.loads(payload_raw)
    except Exception:
        log.warning("Dropping malformed stream entry %s: invalid JSON", message_id)
        _ack(redis_client, cfg, message_id)
        return True

    path = data.get("path")
    timestamp = data.get("timestamp")
    event_key = fields.get("event_key") or f"{path}|{timestamp}"
    if dedup.seen(event_key, cfg.redis_dedup_ttl_sec):
        log.info("Skipping duplicate event_key=%s", event_key)
        _ack(redis_client, cfg, message_id)
        return True

    if not path or not os.path.isfile(path):
        log.warning("Dropping stream entry %s: missing file %s", message_id, path)
        _ack(redis_client, cfg, message_id)
        return True

    if send_photo(path, config_mgr=configmgr):
        _ack(redis_client, cfg, message_id)
        log.info("Sent and acked image: %s (id=%s)", path, message_id)
        return True

    log.error("Telegram send failed, leaving pending: id=%s path=%s", message_id, path)
    return False


def _handle_read_result(redis_client, cfg, messages, dedup: DedupCache):
    for _stream, entries in messages:
        for message_id, fields in entries:
            _process_stream_message(redis_client, cfg, message_id, fields, dedup)


def _claim_stale(redis_client, cfg, consumer_name: str, dedup: DedupCache):
    claimed = redis_client.xautoclaim(
        cfg.redis_stream_name,
        cfg.redis_consumer_group,
        consumer_name,
        min_idle_time=cfg.redis_claim_idle_ms,
        start_id="0-0",
        count=10,
    )
    if not claimed:
        return
    claimed_messages = claimed[1] if isinstance(claimed, (list, tuple)) and len(claimed) > 1 else []
    for message_id, fields in claimed_messages:
        log.info("Claimed stale pending message: %s", message_id)
        _process_stream_message(redis_client, cfg, message_id, fields, dedup)


def redis_worker(stop_evt: threading.Event, config_mgr: ConfigManager):
    log.info("Starting Redis Streams worker thread")

    retry_delay = 0.0
    redis_client = None
    dedup = DedupCache()
    consumer_name = f"{config_mgr.get().redis_consumer_name_prefix}-{socket.gethostname()}-{os.getpid()}"
    last_health_check = 0.0
    last_claim = 0.0

    while not stop_evt.is_set():
        cfg = config_mgr.get()
        try:
            if redis_client is None:
                redis_client = redis.Redis.from_url(
                    REDIS_URL,
                    decode_responses=True,
                    socket_keepalive=True,
                    health_check_interval=cfg.redis_healthcheck_sec,
                    socket_connect_timeout=cfg.redis_connect_timeout_sec,
                    socket_timeout=cfg.redis_socket_timeout_sec,
                )
                redis_client.ping()
                _ensure_group(redis_client, cfg)
                retry_delay = 0.0
                last_health_check = time.time()
                last_claim = time.time()
                log.info(
                    "Redis Streams connected: stream=%s group=%s consumer=%s",
                    cfg.redis_stream_name,
                    cfg.redis_consumer_group,
                    consumer_name,
                )

            now = time.time()
            if now - last_health_check >= cfg.redis_healthcheck_sec:
                redis_client.ping()
                last_health_check = now

            messages = redis_client.xreadgroup(
                groupname=cfg.redis_consumer_group,
                consumername=consumer_name,
                streams={cfg.redis_stream_name: ">"},
                count=1,
                block=cfg.redis_block_ms,
            )
            if messages:
                _handle_read_result(redis_client, cfg, messages, dedup)

            now = time.time()
            if now - last_claim >= max(1.0, cfg.redis_claim_idle_ms / 1000.0):
                _claim_stale(redis_client, cfg, consumer_name, dedup)
                last_claim = now

        except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
            log.warning("Redis worker connection issue: %s", e)
            _safe_close(redis_client)
            redis_client = None
            if retry_delay <= 0:
                retry_delay = cfg.redis_retry_min_sec
            else:
                retry_delay = min(retry_delay * 2, cfg.redis_retry_max_sec)
            log.info("Redis worker retrying in %.1fs", retry_delay)
            stop_evt.wait(retry_delay)
        except Exception as e:
            log.error("Unexpected error in Redis worker: %s", e, exc_info=True)
            _safe_close(redis_client)
            redis_client = None
            if retry_delay <= 0:
                retry_delay = cfg.redis_retry_min_sec
            else:
                retry_delay = min(retry_delay * 2, cfg.redis_retry_max_sec)
            stop_evt.wait(retry_delay)

    _safe_close(redis_client)
    log.info("Redis worker stopped")


if __name__ == "__main__":
    config = ConfigManager(reg_path=REG_PATH, ttl=2.0)
    app = app_init(TG_TOKEN, config)
    stop_evt = threading.Event()
    log.info("Telegram agent started, pid=%s", os.getpid())

    t = threading.Thread(target=redis_worker, args=(stop_evt, config), daemon=False, name="redis-stream-worker")
    t.start()

    try:
        app.run_polling()
    finally:
        stop_evt.set()
        t.join(timeout=10)
        log.info("Telegram agent stopped")
