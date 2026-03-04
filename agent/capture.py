import ctypes
import os
import re
import time

import mss
import win32gui
from PIL import Image

from regman import ConfigManager  # type: ignore

# ============================================================
# WINDOW HELPERS
# ============================================================

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


_regex_cache: dict = {}


def is_chrome_hwnd(hwnd: int, title_regex: str) -> bool:
    """Return True if *hwnd* is a Chrome window whose title matches title_regex.

    Unlike get_foreground_chrome_hwnd, this validates an already-known hwnd
    without calling GetForegroundWindow, so it can be safely called from the
    main thread after the low-level keyboard hook has queued the event.
    """
    try:
        if win32gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
            return False
    except Exception:
        return False

    title = win32gui.GetWindowText(hwnd).strip()
    if not title:
        return False

    pat = _regex_cache.get(title_regex)
    if not pat:
        pat = re.compile(title_regex, re.I)
        _regex_cache[title_regex] = pat

    return bool(pat.search(title))


def get_foreground_chrome_hwnd(title_regex: str):
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None

    try:
        if win32gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
            return None
    except Exception:
        return None

    title = win32gui.GetWindowText(hwnd).strip()
    if not title:
        return None

    pat = _regex_cache.get(title_regex)
    if not pat:
        pat = re.compile(title_regex, re.I)
        _regex_cache[title_regex] = pat

    return hwnd if pat.search(title) else None


# ============================================================
# CAPTURE ENGINE
# ============================================================

class CaptureEngine:
    def __init__(self, config_mgr: ConfigManager):
        self._config_mgr = config_mgr
        os.makedirs(os.path.join(config_mgr.get().output_dir, "captures"), exist_ok=True)

    def capture_primary(self) -> tuple:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
        image = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(
            self._config_mgr.get().output_dir, "captures", f"capture_{ts}_screen.png"
        )
        image.save(path)
        return path, ts

    def capture(self, hwnd: int) -> tuple:
        rect = RECT()
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
            )
            x1, y1, x2, y2 = rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            x1, y1, x2, y2 = win32gui.GetWindowRect(hwnd)

        if x2 <= x1 or y2 <= y1:
            raise RuntimeError("Invalid window rect")

        with mss.mss() as sct:
            img = sct.grab({
                "left": x1,
                "top": y1,
                "width": x2 - x1,
                "height": y2 - y1,
            })

        image = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(
            self._config_mgr.get().output_dir, "captures", f"capture_{ts}_hwnd{hwnd}.png"
        )
        image.save(path)
        return path, ts
