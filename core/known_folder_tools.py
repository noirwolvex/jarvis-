"""Verified, allowlisted Windows known-folder navigation without model calls."""
from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from pathlib import PureWindowsPath
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .permissions import Risk
from .tools import ToolSpec
from .execution_telemetry import record_backend


_DOWNLOADS_ID = uuid.UUID("374de290-123f-4565-9164-39c4925e467b")


def _windows_only() -> None:
    if os.name != "nt":
        raise RuntimeError("Known-folder navigation requires a Windows desktop")


def _downloads_path() -> str:
    """Use Windows' actual redirected Downloads directory, not ~/Downloads."""
    class Guid(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    shell = ctypes.windll.shell32
    shell.SHGetKnownFolderPath.argtypes = (
        ctypes.POINTER(Guid), ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    raw = ctypes.c_wchar_p()
    guid = Guid.from_buffer_copy(_DOWNLOADS_ID.bytes_le)
    record_backend("windows_api", phase="resolve", detail="Resolve the Windows Downloads known folder")
    status = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(raw))
    if status != 0:
        raise RuntimeError(f"Windows could not resolve Downloads (HRESULT=0x{status & 0xffffffff:08x})")
    try:
        path = str(raw.value or "")
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(raw, ctypes.c_void_p))
    if not path or not os.path.isdir(path):
        raise RuntimeError("The Windows Downloads known folder is not currently available")
    return os.path.realpath(path)


def _folder_windows(path: str) -> list[int]:
    """Read Explorer's live Shell.Application LocationURL for each window."""
    import pythoncom
    from win32com.client import Dispatch

    target = os.path.normcase(os.path.realpath(path))
    hwnds: set[int] = set()
    pythoncom.CoInitialize()
    try:
        record_backend("windows_shell_com", phase="observe", detail="Read Explorer folder locations")
        windows = Dispatch("Shell.Application").Windows()
        for item in windows:
            try:
                if PureWindowsPath(str(item.FullName)).name.casefold() != "explorer.exe":
                    continue
                url = urlsplit(str(item.LocationURL))
                if url.scheme.casefold() != "file" or url.netloc not in ("", "localhost"):
                    continue
                # url2pathname already decodes URL escapes. Decoding twice changes
                # literal percent sequences in legitimate folder names.
                visited = os.path.normcase(os.path.realpath(url2pathname(url.path)))
                if visited == target:
                    hwnd = int(item.HWND)
                    if hwnd > 0:
                        hwnds.add(hwnd)
            except (AttributeError, OSError, TypeError, ValueError, pythoncom.com_error):
                continue  # An unreadable location is not verification.
    finally:
        pythoncom.CoUninitialize()
    return sorted(hwnds)


def _foreground_hwnd() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def _verify_existing_folder_window(hwnd: int, path: str, *, activate: bool = True) -> bool:
    """Ensure the foreground HWND still belongs to the exact known folder."""
    from .app_tools import _focus
    from .process_control import check_cancelled

    check_cancelled()
    if activate and not _focus(hwnd):
        return False
    check_cancelled()
    if _foreground_hwnd() != hwnd or hwnd not in _folder_windows(path):
        return False
    check_cancelled()
    # COM reads can outlive a focus change. Require a final independent read.
    return _foreground_hwnd() == hwnd


def open_known_folder(folder: str = "Downloads", timeout_seconds: float = 8) -> str:
    """Open/reuse a verified Explorer Downloads window; never guess a UI coordinate."""
    _windows_only()
    if str(folder).casefold() != "downloads":
        raise ValueError("Only the allowlisted Downloads folder is supported")
    if not 2 <= float(timeout_seconds) <= 20:
        raise ValueError("Known-folder navigation timeout must be 2-20 seconds")

    from .process_control import check_cancelled
    from .desktop_input import InputDeliveryError, InputNotDispatchedError

    check_cancelled()
    target = _downloads_path()
    matches = _folder_windows(target)
    if len(matches) > 1:
        raise InputNotDispatchedError("Several Explorer windows show Downloads; select one explicitly")
    reused = bool(matches)
    if not reused:
        # Exactly one dispatch. An unverified launch is never automatically retried.
        check_cancelled()
        record_backend("windows_api", detail="Open the resolved Downloads folder")
        os.startfile(target)

    deadline = time.monotonic() + float(timeout_seconds)
    activation_attempts: set[int] = set()
    while time.monotonic() < deadline:
        check_cancelled()
        matches = _folder_windows(target)
        if len(matches) > 1:
            raise InputDeliveryError("Downloads opened but multiple matching Explorer windows exist")
        verified = False
        if matches:
            hwnd = matches[0]
            activate = hwnd not in activation_attempts
            activation_attempts.add(hwnd)
            verified = _verify_existing_folder_window(hwnd, target, activate=activate)
        if verified:
            return "VERIFIED: " + json.dumps({
                "action": "open_known_folder",
                "folder": "Downloads",
                "window_handle": matches[0],
                "location_verified": True,
                "foreground_verified": True,
                "reused_existing_window": reused,
            })
        time.sleep(0.07)
    raise InputDeliveryError(
        "Downloads navigation was requested but its exact Explorer location "
        "and foreground could not be verified; inspect before attempting again"
    )


def register_known_folder_tools(registry) -> None:
    registry.register(ToolSpec(
        "open_known_folder",
        "Open or reuse the Windows Downloads known folder. Verify Explorer's "
        "live location and foreground identity. Never assume an app launch proves navigation.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "enum": ["Downloads"]},
                "timeout_seconds": {"type": "number", "minimum": 2, "maximum": 20},
            },
            "required": ["folder"],
            "additionalProperties": False,
        },
        open_known_folder,
    ))
