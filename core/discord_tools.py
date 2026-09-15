from __future__ import annotations

import ctypes
import json
import os
import time
from typing import Any

from .desktop_input import InputDeliveryError, paste_text
from .permissions import Risk
from .semantic_ui_tools import _control_name, _descendants, _focus_window, _foreground_hwnd, ui_type
from .tools import ToolRegistry, ToolSpec


def _discord_window():
    if os.name != "nt":
        raise RuntimeError("Discord desktop automation is supported on Windows only")
    from pywinauto import Desktop
    import psutil

    windows = Desktop(backend="uia").windows()
    candidates: list[tuple[int, Any]] = []
    for win in windows:
        try:
            hwnd = int(win.handle)
            pid = int(win.element_info.process_id)
            title = str(win.window_text() or "")
            process_name = psutil.Process(pid).name().casefold()
            if process_name == "discord.exe" or "discord" in title.casefold():
                candidates.append((hwnd, win))
        except Exception:
            continue
    if not candidates:
        raise RuntimeError("No visible Discord window was found; open Discord first")
    foreground = int(ctypes.windll.user32.GetForegroundWindow())
    for hwnd, win in candidates:
        if hwnd == foreground:
            return win
    return candidates[0][1]


def _visible_names(win: Any) -> list[str]:
    result: list[str] = []
    for control in _descendants(win)[:900]:
        try:
            if not control.is_visible():
                continue
        except Exception:
            pass
        name = _control_name(control)
        if name:
            result.append(name)
    return result


def _selected_target(win: Any, destination: str) -> bool:
    needle = str(destination).strip().casefold()
    if not needle:
        return False
    for control in _descendants(win)[:900]:
        try:
            if needle not in _control_name(control).casefold():
                continue
            if control.is_selected():
                return True
        except Exception:
            continue
    return False


def discord_go_to(destination: str) -> str:
    """Use Discord's Quick Switcher rather than visual mouse navigation."""
    target = " ".join(str(destination or "").split()).strip()
    if not target or len(target) > 200 or "\0" in target:
        raise ValueError("Discord destination must contain 1-200 safe characters")

    win = _discord_window()
    hwnd = _focus_window(win)
    before_names = _visible_names(win)

    from pywinauto.keyboard import send_keys

    send_keys("^k")
    time.sleep(0.10)
    paste_text(target)
    time.sleep(0.08)
    send_keys("{ENTER}")
    time.sleep(0.35)

    if _foreground_hwnd() != hwnd:
        raise RuntimeError("Discord lost foreground while navigating; inspect state before retrying")

    after_names = _visible_names(win)
    target_visible = any(target.casefold() in name.casefold() for name in after_names)
    selected = _selected_target(win, target)
    changed = before_names != after_names

    # Quick Switcher navigation is accepted only when Discord stayed foreground and the live
    # accessibility tree reflects the requested target plus either selection or a state change.
    if not target_visible or not (selected or changed):
        raise RuntimeError(
            f"Discord navigation to {target!r} could not be verified from the live accessibility tree"
        )
    return "VERIFIED: " + json.dumps(
        {
            "action": "discord_go_to",
            "destination": target,
            "window_hwnd": hwnd,
            "target_visible": target_visible,
            "selected": selected,
            "ui_changed": changed,
        },
        ensure_ascii=False,
    )


def discord_send_message(text: str) -> str:
    message = str(text or "")
    if not message or len(message) > 4000 or "\0" in message:
        raise ValueError("Discord message must contain 1-4000 safe characters")
    win = _discord_window()
    _focus_window(win)
    # ui_type auto-selects the most likely visible message/chat editor and fails closed if
    # Enter may have sent text but the post-send state cannot be verified.
    result = ui_type(text=message, target="", title="", submit=True, replace=False)
    if not result.startswith("VERIFIED:"):
        raise InputDeliveryError("Discord message send did not verify; do not resend automatically")
    return "VERIFIED: " + json.dumps(
        {"action": "discord_send_message", "characters": len(message), "delivery": result[len("VERIFIED: "):]},
        ensure_ascii=False,
    )


def discord_navigate_and_send(destination: str, text: str) -> str:
    navigated = discord_go_to(destination)
    if not navigated.startswith("VERIFIED:"):
        raise RuntimeError("Discord destination was not verified; message was not sent")
    sent = discord_send_message(text)
    if not sent.startswith("VERIFIED:"):
        raise InputDeliveryError("Discord message outcome is uncertain; do not retry automatically")
    return "VERIFIED: " + json.dumps(
        {
            "action": "discord_navigate_and_send",
            "destination": destination,
            "characters": len(str(text)),
            "navigation": json.loads(navigated[len("VERIFIED: "):]),
            "message": json.loads(sent[len("VERIFIED: "):]),
        },
        ensure_ascii=False,
    )


def register_discord_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "discord_go_to",
        "Fast Discord desktop navigation to a named server/channel/DM using Discord Quick Switcher and live UIA verification. Prefer this over mouse coordinates or repeated screenshots.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {"destination": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["destination"],
            "additionalProperties": False,
        },
        discord_go_to,
    ))
    registry.register(ToolSpec(
        "discord_send_message",
        "Send one explicitly requested message in the currently selected Discord conversation. Semantically resolves the message composer and verifies the post-send state; uncertain delivery fails closed to prevent duplicate messages.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 4000}},
            "required": ["text"],
            "additionalProperties": False,
        },
        discord_send_message,
    ))
    registry.register(ToolSpec(
        "discord_navigate_and_send",
        "Fast compound Discord action: navigate to a named server/channel/DM and send exactly one explicitly requested message, with verification and no model round-trip between navigation and send.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "minLength": 1, "maxLength": 200},
                "text": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            "required": ["destination", "text"],
            "additionalProperties": False,
        },
        discord_navigate_and_send,
    ))
