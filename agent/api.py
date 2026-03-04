import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import psutil
import win32gui
from flask import Flask

from agent import metrics

log = logging.getLogger("agent")

app = Flask(__name__)

# Injected via init() before Flask starts
_redis_pub = None
_engine = None
_config_mgr = None
_reload_evt = None


def init(redis_pub, engine, config_mgr, reload_evt) -> None:
    """Inject dependencies before the Flask server starts."""
    global _redis_pub, _engine, _config_mgr, _reload_evt
    _redis_pub = redis_pub
    _engine = engine
    _config_mgr = config_mgr
    _reload_evt = reload_evt


# ============================================================
# ROUTES
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    snap = metrics.snapshot()
    return {
        "status": "ok",
        "total_captures": snap["total_captures"],
        "total_publish_fail": snap["total_publish_fail"],
        "total_redis_reconnect": snap["total_redis_reconnect"],
    }


@app.route("/version", methods=["GET"])
def version():
    base_path = Path(__file__).parent.parent
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit = "unknown"
    return {"commit": commit, "pid": os.getpid()}


@app.route("/ping", methods=["GET"])
def ping():
    proc = psutil.Process()
    return {
        "status": "alive",
        "pid": proc.pid,
        "uptime_sec": round(time.time() - proc.create_time(), 2),
        "redis_connected": _redis_pub.is_connected() if _redis_pub else False,
    }


@app.route("/reload", methods=["POST"])
def reload_agent():
    if _reload_evt:
        _reload_evt.set()
    return {"status": "reload_scheduled"}


@app.route("/self_restart", methods=["POST"])
def self_restart():
    import sys

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
            path, ts = _engine.capture(hwnd) if hwnd else _engine.capture_primary()
        except Exception:
            path, ts = _engine.capture_primary()
        cfg = _config_mgr.get()
        _redis_pub.publish(cfg.stream_name, {
            "type": "IMAGE_READY",
            "v": "1",
            "hwnd": str(hwnd or 0),
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
    if _redis_pub:
        _redis_pub.reset_connection()
    log.info("Redis connection reset")
    return {"status": "ok"}


# ============================================================
# FLASK THREAD ENTRY POINT
# ============================================================

def flask_thread() -> None:
    try:
        log.info("Starting local API server on 127.0.0.1:18080")
        app.run(
            host="127.0.0.1",
            port=18080,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except Exception as e:
        log.exception("Flask server crashed: %s", e)
        time.sleep(5)
