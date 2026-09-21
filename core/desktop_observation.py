from __future__ import annotations

import ctypes
import os
import time
from collections import OrderedDict
from typing import Any

_SIGNATURES: OrderedDict[str, Any] = OrderedDict()


def remember_signature(key: str, image) -> None:
    _SIGNATURES[key] = image.resize((192, 108)).convert("RGB")
    while len(_SIGNATURES) > 8:
        _SIGNATURES.popitem(last=False)


def scene_matches(key: str, frame: dict[str, Any] | None = None) -> bool:
    from PIL import ImageGrab, ImageChops
    expected = _SIGNATURES.get(key)
    if expected is None:
        return False
    from .vision_tools import _virtual_origin
    origin = _virtual_origin()
    current = ImageGrab.grab(all_screens=True)
    if origin != _virtual_origin():
        return False
    if frame is not None and (current.size != (frame["source_width"], frame["source_height"])
                              or origin != (frame["virtual_origin_x"], frame["virtual_origin_y"])):
        return False
    current = current.resize(expected.size).convert("RGB")
    return all(high <= 12 for _, high in ImageChops.difference(expected, current).getextrema())


def foreground_identity() -> int:
    if os.name != "nt":
        return 0
    function = ctypes.windll.user32.GetForegroundWindow
    function.restype = ctypes.c_void_p
    return int(function() or 0)


class DesktopObservationGate:
    """Bind raw input to a recent observed foreground and virtual desktop bounds."""

    def __init__(self, max_age_seconds: float = 10.0):
        self.frame: dict[str, Any] | None = None
        self.observed_at = 0.0
        self.max_age_seconds = max_age_seconds

    def observe(self, frame: dict[str, Any]) -> None:
        self.frame = dict(frame)
        self.observed_at = time.monotonic()

    def invalidate(self) -> None:
        self.frame = None

    def check(self, arguments: dict[str, Any]) -> None:
        frame = self.frame
        age = time.monotonic() - self.observed_at
        if frame is None or age > (60 if frame.get("scene_bound") else self.max_age_seconds):
            raise ValueError("Fresh screen_observe required before desktop input")
        if frame.get("stable") is False:
            raise ValueError("Fresh stable screen_observe required: the UI is still changing")
        if not frame.get("foreground_hwnd") or foreground_identity() != frame["foreground_hwnd"]:
            self.invalidate()
            raise ValueError("Foreground changed since observation; inspect the current screen")
        if frame.get("scene_bound"):
            if not scene_matches(frame["sha256"], frame) or foreground_identity() != frame["foreground_hwnd"]:
                self.invalidate()
                raise ValueError("Fresh screen_observe required: visible UI changed before input")
        left, top = frame["virtual_origin_x"], frame["virtual_origin_y"]
        right, bottom = left + frame["source_width"], top + frame["source_height"]
        for x_key, y_key in (("x", "y"), ("start_x", "start_y"), ("end_x", "end_y")):
            if x_key in arguments or y_key in arguments:
                x, y = arguments.get(x_key), arguments.get(y_key)
                if type(x) is not int or type(y) is not int or not (left <= x < right and top <= y < bottom):
                    raise ValueError("Desktop coordinate is outside the observed virtual desktop")
