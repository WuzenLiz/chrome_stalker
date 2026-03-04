import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import redis
import requests
from telegram.ext import Application, CommandHandler

from regman import ConfigManager  # type: ignore
from tbot.state import reconnect_redis_evt
from tbot import sender as sender_module

log = logging.getLogger("telegram-agent")


# ============================================================
# COMMAND HANDLERS
# ============================================================

async def cleanup_old_photos(update, context, config_mgr: ConfigManager) -> None:
    minutes = config_mgr.get().delete_minutes
    MAX_MINUTES = config_mgr.get().max_delete_minutes

    if context.args:
        try:
            minutes = int(context.args[0])
            minutes = max(1, min(minutes, MAX_MINUTES))
        except ValueError:
            await update.message.reply_text("❌ Invalid value. Usage: /cleanup <minutes>")
            return

    cutoff_ts = time.time() - (minutes * 60)
    output_dir = os.path.join(config_mgr.get().output_dir, "captures")

    if not os.path.isdir(output_dir):
        await update.message.reply_text("ℹ️ Capture directory does not exist.")
        return

    deleted = 0
    failed = 0
    for filename in os.listdir(output_dir):
        file_path = os.path.join(output_dir, filename)
        if not os.path.isfile(file_path):
            continue
        # Skip files modified very recently — they may still be written by the agent
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
        f"🧹 Cleanup done\n"
        f"• Older than: {minutes} minutes\n"
        f"• Deleted: {deleted}\n"
        f"• Failed: {failed}"
    )


async def set_config(update, context, config_mgr: ConfigManager) -> None:
    if len(context.args) != 2:
        await update.message.reply_text("❌ Invalid usage. Usage: /set_config <key> <value>")
        return

    key = context.args[0]
    value_str = context.args[1]

    if value_str.lower() in ("true", "false"):
        value = value_str.lower() == "true"
    else:
        try:
            value = float(value_str) if '.' in value_str else int(value_str)
        except ValueError:
            value = value_str

    config_mgr.write(key, value)
    await update.message.reply_text(f"✅ Set configuration\n• {key} = {value}")


async def ping_agent(update, context) -> None:
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


async def redis_status(update, context, redis_url: str) -> None:
    connected = await _check_redis_status(redis_url)
    status = "🟢 Connected" if connected else "🔴 Disconnected"
    await update.message.reply_text(f"Redis status: {status}")


async def _check_redis_status(redis_url: str) -> bool:
    r = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        return r.ping()
    except redis.RedisError as e:
        log.error("Redis connection error: %s", e)
        return False
    finally:
        r.close()


async def reload_agent(update, context) -> None:
    try:
        requests.post("http://localhost:18080/reload", timeout=3)
        await update.message.reply_text("♻️ Agent reload requested")
    except Exception:
        await update.message.reply_text("❌ Agent not reachable")


async def manual_shot(update, context, config_mgr: ConfigManager) -> None:
    try:
        r = requests.post("http://localhost:18080/manual_shot", timeout=10).json()
        if r.get("status") == "ok":
            path = r["path"]
            captures_dir = os.path.realpath(
                os.path.join(config_mgr.get().output_dir, "captures")
            )
            if not os.path.realpath(path).startswith(captures_dir + os.sep):
                await update.message.reply_text("❌ Invalid capture path")
                return
            with open(path, "rb") as f:
                await update.message.reply_photo(f)
        else:
            await update.message.reply_text(f"❌ {r.get('message', 'Capture failed')}")
    except Exception as e:
        await update.message.reply_text(f"❌ Agent not reachable: {e}")


async def redeploy(update, context) -> None:
    await update.message.reply_text("🔄 Redeploying...")
    base_path = Path(__file__).parent.parent
    try:
        pull = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, cwd=base_path, timeout=60,
        )
        if pull.returncode != 0:
            await update.message.reply_text(f"❌ git pull failed:\n{pull.stderr[:500]}")
            return
        pull_out = pull.stdout.strip() or "Already up to date."

        await update.message.reply_text(
            f"✅ Code updated: {pull_out}\n♻️ Restarting services in 1s..."
        )

        def _restart():
            time.sleep(1.0)
            # Restart both NSSM-managed services using net stop/start
            # (avoids the "Unexpected status SERVICE_STOPPED" quirk of nssm restart)
            for svc in ("ChromeStalker"):
                ctl = subprocess.run(["net", "stop", svc], capture_output=True, text=True)
                if ctl.returncode != 0:
                    log.warning("net stop %s failed (may already be stopped): %s",
                                svc, (ctl.stdout + ctl.stderr).strip())
            for svc in ("ChromeStalker"):
                ctl = subprocess.run(
                    ["net", "start", svc],
                    capture_output=True, text=True,
                )
                if ctl.returncode != 0:
                    log.error("net start %s failed: %s", svc, (ctl.stdout + ctl.stderr).strip())
                else:
                    log.info("net start %s: OK", svc)

        threading.Thread(target=_restart, daemon=True).start()
    except subprocess.TimeoutExpired:
        await update.message.reply_text("❌ git pull timed out")
    except Exception as e:
        await update.message.reply_text(f"❌ Redeploy failed: {e}")


async def reconnect_redis(update, context) -> None:
    reconnect_redis_evt.set()
    agent_ok = False
    try:
        requests.post("http://localhost:18080/reconnect_redis", timeout=3)
        agent_ok = True
    except Exception as e:
        log.warning("Could not reach agent for Redis reconnect: %s", e)
    if agent_ok:
        await update.message.reply_text("🔌 Redis reconnect triggered (bot + agent)")
    else:
        await update.message.reply_text("🔌 Redis reconnect triggered (bot only — agent unreachable)")


async def self_restart(update, context) -> None:
    await update.message.reply_text("♻️ Bot restarting...")

    def _restart():
        time.sleep(0.5)
        log.info("Self-restarting bot...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_restart, daemon=True).start()


async def last_log_agent(update, context, config_mgr: ConfigManager) -> None:
    n = int(context.args[0]) if context.args else 20
    log_file = os.path.join(
        config_mgr.get().output_dir, config_mgr.get().log_agent_path, "agent.log"
    )
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        await update.message.reply_text("".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading log: {e}")


async def last_log_bot(update, context, config_mgr: ConfigManager) -> None:
    n = int(context.args[0]) if context.args else 20
    log_file = os.path.join(
        config_mgr.get().output_dir, config_mgr.get().log_tbot_path, "telegram-agent.log"
    )
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        await update.message.reply_text("".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading log: {e}")


async def queue_status(update, context) -> None:
    from tbot.state import send_queue
    fail_count = sender_module.get_fail_count()
    await update.message.reply_text(
        f"📊 Queue status\n"
        f"• Pending images: {send_queue.qsize()}\n"
        f"• Telegram send failures: {fail_count}"
    )


# ============================================================
# APP FACTORY
# ============================================================

def app_init(token: str, config_mgr: ConfigManager, redis_url: str) -> Application:
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(
        "cleanup", lambda u, c: cleanup_old_photos(u, c, config_mgr)
    ))
    app.add_handler(CommandHandler(
        "set_config", lambda u, c: set_config(u, c, config_mgr)
    ))
    app.add_handler(CommandHandler("ping_agent", ping_agent))
    app.add_handler(CommandHandler("hot_reload_agent", reload_agent))
    app.add_handler(CommandHandler(
        "last_log_agent", lambda u, c: last_log_agent(u, c, config_mgr)
    ))
    app.add_handler(CommandHandler(
        "last_log_bot", lambda u, c: last_log_bot(u, c, config_mgr)
    ))
    app.add_handler(CommandHandler(
        "redis_status", lambda u, c: redis_status(u, c, redis_url)
    ))
    app.add_handler(CommandHandler("self_restart", self_restart))
    app.add_handler(CommandHandler(
        "manual_shot", lambda u, c: manual_shot(u, c, config_mgr)
    ))
    app.add_handler(CommandHandler("queue_status", queue_status))
    app.add_handler(CommandHandler("redeploy", redeploy))
    app.add_handler(CommandHandler("reconnect_redis", reconnect_redis))

    return app
