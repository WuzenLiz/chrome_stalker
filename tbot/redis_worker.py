import logging
import os
import time
import threading
from queue import Empty, Full

import redis

from regman import ConfigManager  # type: ignore
from tbot.state import send_queue, ack_queue, reconnect_redis_evt

log = logging.getLogger("telegram-agent")

CONSUMER_NAME = "tbot_consumer"


def _recover_pending(r, stream: str, group: str) -> None:
    """Re-enqueue messages left pending by a previous crashed consumer."""
    try:
        result = r.xautoclaim(
            stream, group, CONSUMER_NAME,
            min_idle_time=60_000,  # 60 s
            start_id="0-0",
            count=100,
        )
        pending = result[1]
        if not pending:
            return
        log.info("Recovering %d pending messages from stream", len(pending))
        for msg_id, data in pending:
            path = data.get("path")
            if path and os.path.isfile(path):
                try:
                    send_queue.put_nowait((path, msg_id))
                except Full:
                    log.warning("Send queue full during recovery, dropping: %s", path)
                    r.xack(stream, group, msg_id)
            else:
                r.xack(stream, group, msg_id)
    except Exception as e:
        log.warning("Could not recover pending messages: %s", e)


def redis_worker(
    stop_evt: threading.Event,
    config_mgr: ConfigManager,
    redis_url: str,
) -> None:
    log.info("Starting Redis worker thread")

    retry_delay = 1.0
    max_retry_delay = 60.0
    health_check_interval = 30.0
    last_health_check = 0.0

    r = None

    while not stop_evt.is_set():
        # Manual reconnect requested from /reconnect_redis command or Telegram
        if reconnect_redis_evt.is_set():
            reconnect_redis_evt.clear()
            log.info("Redis reconnect requested")
            if r:
                try:
                    r.close()
                except Exception:
                    pass
                r = None
            retry_delay = 1.0

        try:
            if r is None:
                cfg = config_mgr.get()
                log.info("Connecting to Redis...")
                r = redis.Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_keepalive=True,
                    socket_keepalive_options={1: 10, 2: 10, 3: 3},
                    health_check_interval=30,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                r.ping()
                log.info("Redis connection established")

                stream = cfg.stream_name
                group = cfg.stream_consumer_group
                try:
                    r.xgroup_create(stream, group, id="0", mkstream=True)
                    log.info("Created consumer group %s on stream %s", group, stream)
                except redis.exceptions.ResponseError as e:
                    if "BUSYGROUP" not in str(e):
                        raise
                    log.debug("Consumer group %s already exists", group)

                _recover_pending(r, stream, group)
                retry_delay = 1.0
                last_health_check = time.time()

            cfg = config_mgr.get()
            stream = cfg.stream_name
            group = cfg.stream_consumer_group

            # Periodic health check
            now = time.time()
            if now - last_health_check > health_check_interval:
                try:
                    r.ping()
                    last_health_check = now
                    log.debug("Redis health check: OK")
                except Exception as e:
                    log.warning("Redis health check failed: %s. Reconnecting...", e)
                    raise redis.ConnectionError("Health check failed")

            # Drain ack queue — acknowledge successfully sent messages
            while True:
                try:
                    msg_id = ack_queue.get_nowait()
                    try:
                        r.xack(stream, group, msg_id)
                    except Exception as e:
                        log.warning("xack failed for %s: %s", msg_id, e)
                except Empty:
                    break

            # Read new messages (block up to 1 second)
            results = r.xreadgroup(
                group, CONSUMER_NAME,
                {stream: ">"},
                count=10,
                block=1000,
            )
            if not results:
                continue

            for _, entries in results:
                for msg_id, data in entries:
                    path = data.get("path")
                    if not path or not os.path.isfile(path):
                        r.xack(stream, group, msg_id)
                        continue
                    try:
                        send_queue.put_nowait((path, msg_id))
                    except Full:
                        log.warning("Send queue full, dropping: %s", path)
                        r.xack(stream, group, msg_id)

        except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
            log.error("Redis connection error: %s. Retrying in %.1fs...", e, retry_delay)
            if r:
                try:
                    r.close()
                except Exception:
                    pass
                r = None
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)

        except Exception as e:
            log.error("Unexpected error in Redis worker: %s", e, exc_info=True)
            time.sleep(5)

    log.warning("Redis worker stopping...")
    if r:
        try:
            r.close()
        except Exception:
            pass
    log.info("Redis worker stopped")
