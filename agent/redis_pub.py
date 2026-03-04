import logging
import threading
import time

import redis

from regman import ConfigManager  # type: ignore
from agent import metrics

log = logging.getLogger("agent")


class RedisPublisher:
    """Publishes capture events to a Redis Stream (xadd)."""

    def __init__(self, url: str, config_mgr: ConfigManager):
        self._url = url
        self._config_mgr = config_mgr
        self._client = None
        self._lock = threading.Lock()
        self._last_failed = 0.0

    def _connect(self):
        self._client = redis.Redis.from_url(
            self._url,
            decode_responses=True,
            socket_timeout=5,
        )

    def publish(self, stream: str, payload: dict) -> None:
        cfg = self._config_mgr.get()
        with self._lock:
            if time.time() - self._last_failed < cfg.max_pubsub_false_countdown:
                log.warning("Skipping publish due to backoff window")
                return
            try:
                if not self._client:
                    self._connect()
                    log.info("Redis connected")
                    metrics.inc("total_redis_reconnect")
                self._client.xadd(
                    stream,
                    payload,
                    maxlen=cfg.stream_maxlen,
                    approximate=True,
                )
            except Exception as e:
                log.error("Redis publish failed: %s", e)
                self._client = None
                self._last_failed = time.time()
                metrics.inc("total_publish_fail")

    def reset_connection(self) -> None:
        with self._lock:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._last_failed = 0.0

    def is_connected(self) -> bool:
        with self._lock:
            try:
                return bool(self._client and self._client.ping())
            except Exception:
                self._client = None
                return False
