import logging
import os
import sys
import time
import threading

log = logging.getLogger("telegram-agent")


def watchdog_thread(stop_evt: threading.Event, *threads: threading.Thread) -> None:
    while not stop_evt.is_set():
        time.sleep(10)
        for t in threads:
            if not t.is_alive():
                log.warning("Thread %s died — restarting process", t.name)
                os.execv(sys.executable, [sys.executable] + sys.argv)
