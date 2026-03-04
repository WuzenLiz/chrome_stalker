import logging
import time
import threading
from queue import Empty

import requests

from regman import ConfigManager  # type: ignore
from tbot.state import send_queue, ack_queue

log = logging.getLogger("telegram-agent")

_last_send = 0.0
_send_lock = threading.Lock()

_telegram_fail_count = 0
_telegram_fail_lock = threading.Lock()

# ── Circuit breaker ──────────────────────────────────────────────────────────
_consecutive_failures = 0
_circuit_open_until = 0.0
_circuit_lock = threading.Lock()


def _circuit_is_open() -> bool:
    with _circuit_lock:
        return time.time() < _circuit_open_until


def _record_success() -> None:
    global _consecutive_failures
    with _circuit_lock:
        _consecutive_failures = 0


def _record_failure(config_mgr: ConfigManager) -> None:
    global _consecutive_failures, _circuit_open_until, _telegram_fail_count
    cfg = config_mgr.get()
    with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= cfg.circuit_breaker_threshold:
            _circuit_open_until = time.time() + cfg.circuit_breaker_sleep_sec
            log.warning(
                "Circuit breaker OPEN: %d consecutive failures, sleeping %.0fs",
                _consecutive_failures,
                cfg.circuit_breaker_sleep_sec,
            )
    with _telegram_fail_lock:
        _telegram_fail_count += 1


def get_fail_count() -> int:
    with _telegram_fail_lock:
        return _telegram_fail_count


def send_text(text: str, api: str, chat_id: str) -> bool:
    """Send a plain text message to Telegram. Returns True on success."""
    try:
        r = requests.post(
            f"{api}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("Failed to send text message: %s", e)
        return False


def send_photo(path: str, config_mgr: ConfigManager, api: str, chat_id: str) -> bool:
    """Send a photo to Telegram. Returns True on success."""
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
                    f"{api}/sendPhoto",
                    data={"chat_id": chat_id},
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


def sender_thread(
    stop_evt: threading.Event,
    config_mgr: ConfigManager,
    api: str,
    chat_id: str,
) -> None:
    log.info("Starting sender thread")
    while not stop_evt.is_set():
        # Hold while circuit breaker is open — items remain in send_queue
        if _circuit_is_open():
            time.sleep(1.0)
            continue
        try:
            item = send_queue.get(timeout=1.0)
            if not isinstance(item, tuple) or len(item) != 2:
                log.error("Unexpected item in send_queue: %r", item)
                continue
            path, msg_id = item
            ok = send_photo(path, config_mgr, api, chat_id)
            if ok:
                _record_success()
                ack_queue.put(msg_id)
                log.info("Sent image: %s", path)
            else:
                _record_failure(config_mgr)
                # Do not ack — message stays in stream PEL and will be
                # re-claimed by the next xautoclaim recovery cycle.
        except Empty:
            pass
        except Exception as e:
            log.error("Sender thread error: %s", e)
    log.info("Sender thread stopped")
