import threading

_metrics = {
    "total_captures": 0,
    "total_publish_fail": 0,
    "total_redis_reconnect": 0,
}
_metrics_lock = threading.Lock()


def inc(key: str) -> None:
    with _metrics_lock:
        _metrics[key] += 1


def snapshot() -> dict:
    with _metrics_lock:
        return dict(_metrics)
