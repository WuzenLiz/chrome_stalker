import os
import json
import logging
import redis
import requests
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("telegram-agent")

REDIS_URL = os.getenv("REDIS_CONNECTION", "redis://localhost:6379/0")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API = f"https://api.telegram.org/bot{TG_TOKEN}"


def send_photo(path: str):
    with open(path, "rb") as f:
        r = requests.post(
            f"{API}/sendPhoto",
            data={"chat_id": TG_CHAT_ID},
            files={"photo": f},
            timeout=15
        )
    r.raise_for_status()


def main():
    log.info("Telegram Bot Agent started")

    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    pubsub.subscribe("IMAGE_READY")

    for msg in pubsub.listen():
        if msg["type"] != "message":
            continue

        try:
            data = json.loads(msg["data"])
            send_photo(data["path"])
            log.info("Sent: %s", data["path"])
        except Exception as e:
            log.error("Failed to send image: %s", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
