from dataclasses import dataclass
import os

@dataclass
class RegistryCfg:
    enabled: bool = True
    interval_sec: int = 5
    title_regex: str = r"(facebook|messenger|zalo)"
    fg_poll_interval: float = 0.5
    output_dir: str = os.path.join(os.getenv("APPDATA") or "C:\\Users\\Public\\AppData", "chrome_stalker")

    # agent-only
    max_pubsub_false_countdown: int = 5
    max_files_rotation: int = 500
    log_agent_path: str = "logs/agent"

    # tbot-only
    send_interval_sec: float = 1.2
    delete_minutes: int = 5
    max_delete_minutes: int = 1440
    log_tbot_path: str = "logs/tbot"

    # Redis Streams
    stream_name: str = "IMAGE_STREAM"
    stream_consumer_group: str = "tbot_group"
    stream_maxlen: int = 1000

    # Circuit breaker (Telegram sender)
    circuit_breaker_threshold: int = 5
    circuit_breaker_sleep_sec: float = 60.0
