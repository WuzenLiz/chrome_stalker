import threading
from queue import Queue

# Shared queues/events used by redis_worker, sender, and commands
send_queue: Queue = Queue(maxsize=200)
ack_queue: Queue = Queue()
reconnect_redis_evt: threading.Event = threading.Event()
