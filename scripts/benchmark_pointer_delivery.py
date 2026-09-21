"""Measure Python click overhead with every OS input endpoint replaced by a fixture.

This exercises installed PyAutoGUI timing/decorators and JARVIS guards, never the
mouse, keyboard, screen capture or a real window. It is not hardware latency.
"""
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def measure():
    import pyautogui
    from core.desktop_control_tools import desktop_click_button
    from core.tools import _desktop_click

    platform = SimpleNamespace(_click=Mock(), _multiClick=Mock(), _mouse_is_swapped=lambda: False)
    results = {}
    with patch.object(pyautogui, "platformModule", platform), \
         patch.object(pyautogui, "_mouseMoveDrag"), \
         patch.object(pyautogui, "_logScreenshot"), \
         patch.object(pyautogui, "position", return_value=pyautogui.Point(10, 20)), \
         patch.object(pyautogui, "PAUSE", 0.1), \
         patch("core.desktop_observation.foreground_identity", return_value=7):
        cases = {
            "legacy_desktop_click_before": lambda: pyautogui.click(x=10, y=20),
            "extended_click_before": lambda: pyautogui.click(x=10, y=20, clicks=1,
                                                           interval=0.06, button="left", _pause=False),
            "guarded_click_after": lambda: desktop_click_button(10, 20),
            "legacy_route_after": lambda: _desktop_click(10, 20),
        }
        for name, action in cases.items():
            times = []
            initial = platform._click.call_count
            for _ in range(5):
                started = time.perf_counter()
                action()
                times.append((time.perf_counter() - started) * 1000)
            assert platform._click.call_count - initial == 5
            results[name] = round(statistics.median(times), 3)
    return {"median_ms_five_runs": results, "native_input_calls": 0,
            "scope": "Installed library + JARVIS overhead; OS delivery fully stubbed"}


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
