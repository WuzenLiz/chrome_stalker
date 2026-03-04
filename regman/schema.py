from dataclasses import dataclass
import os

@dataclass
class RegistryCfg:
    enabled: bool = True
    interval_sec: int = 5
    title_regex: str = r"(facebook|messenger|zalo)"
    fg_poll_interval: float = 0.5
    output_dir: str = os.getenv("APPDATA") or "C:\\Users\\Public\\AppData"

    # agent-only
    max_files_rotation: int = 500
    log_agent_path: str = "logs/agent"

    # tbot-only
    send_interval_sec: float = 1.2
    delete_minutes: int = 5
    max_cleanup_minutes: int = 60
    log_tbot_path: str = "logs/tbot"

    # redis reliability
    redis_stream_name: str = "image_ready_stream"
    redis_consumer_group: str = "tbot_group"
    redis_consumer_name_prefix: str = "tbot"
    redis_stream_maxlen: int = 10000
    redis_block_ms: int = 5000
    redis_claim_idle_ms: int = 60000
    redis_connect_timeout_sec: int = 5
    redis_socket_timeout_sec: int = 10
    redis_healthcheck_sec: int = 30
    redis_retry_min_sec: float = 1.0
    redis_retry_max_sec: float = 60.0
    redis_dedup_ttl_sec: int = 300
