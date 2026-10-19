"""One process-wide input lane with cooperative deadlines and cancellation."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable


class ActionCancelled(RuntimeError):
    pass


class ActionTimedOut(TimeoutError):
    pass


@dataclass(frozen=True)
class ExecutionContext:
    deadline: float
    cancelled: Callable[[], bool]


_CONTEXT: ContextVar[ExecutionContext | None] = ContextVar("control_deadline", default=None)
_INPUT_LOCK = threading.RLock()


def has_action_context() -> bool:
    return _CONTEXT.get() is not None


def check_context() -> None:
    context = _CONTEXT.get()
    if context is not None:
        if context.cancelled():
            raise ActionCancelled("Control action cancelled")
        if time.monotonic() >= context.deadline:
            raise ActionTimedOut("Control action deadline exceeded; inspect before retrying")


@contextmanager
def action_context(deadline: float, cancelled: Callable[[], bool]):
    parent = _CONTEXT.get()
    context = ExecutionContext(min(deadline, parent.deadline) if parent else deadline,
                               (lambda: cancelled() or parent.cancelled()) if parent else cancelled)
    token = _CONTEXT.set(context)
    try:
        check_context()
        yield
    finally:
        _CONTEXT.reset(token)


def uses_desktop(tool: str) -> bool:
    return tool.startswith(("desktop_", "ui_", "interaction_", "browser_", "chrome_", "discord_",
                           "whatsapp_", "youtube_", "dialog_", "wincom_", "vision_")) or tool in {
        "screen_observe", "computer_observe", "take_screenshot", "launch_installed_app",
        "open_application", "open_application_and_type", "open_known_folder", "open_url",
        "google_search", "focus_window", "focus_window_advanced", "close_window", "inspect_window",
    }


@contextmanager
def input_lane(tool: str):
    """Serialize backend access, including legacy callers; containers stay outside."""
    if not uses_desktop(tool):
        yield
        return
    while not _INPUT_LOCK.acquire(timeout=0.025):
        check_context()
        from ..process_control import check_cancelled
        check_cancelled()
    try:
        check_context()
        yield
    finally:
        _INPUT_LOCK.release()
