"""Narrow, verified navigation in an existing Windows Chrome window.

This adapter owns only browser-chrome navigation, never arbitrary page input.
The general desktop/browser boundary deliberately remains in force.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import re
from collections import deque
from typing import Any
from urllib.parse import urlparse

from .desktop_input import InputDeliveryError, InputNotDispatchedError
from .browser_semantic import BrowserChallengeBlocked
from .execution_telemetry import record_backend
from .process_control import check_cancelled
from .ui_state import wait_until

_CHROME_NAMES = {"chrome.exe", "chromium.exe", "chrome", "chromium"}
_MAX_NODES = 1800
_MAX_TEXT = 65536
_MAX_LINKS = 300


class ChromeObservationPending(InputNotDispatchedError):
    """Chrome's UI inventory is settling; only observation may retry."""

_CHALLENGE = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|verify (?:that )?you are human|"
    r"prove you are human|human verification|security check|bot verification|"
    r"anti[- ]?bot|unusual traffic|تحقق.{0,20}(?:بشر|إنسان)", re.IGNORECASE,
)


def _window(hwnd: int):
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=hwnd).wrapper_object()


def _window_class(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    name = ctypes.create_unicode_buffer(256)
    if not user32.GetClassNameW(hwnd, name, 256):
        raise InputNotDispatchedError("Window class could not be inspected")
    return name.value


def _identity(hwnd: int) -> dict[str, Any]:
    check_cancelled()
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        raise InputNotDispatchedError("Existing Chrome window control requires Windows")
    import psutil
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
    user32.IsWindow.restype = ctypes.wintypes.BOOL
    user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
    user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
    user32.GetWindow.restype = ctypes.wintypes.HWND
    if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
        raise InputNotDispatchedError("Chrome window closed or became hidden; inspect again")
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = psutil.Process(pid.value)
    if process.name().casefold() not in _CHROME_NAMES:
        raise InputNotDispatchedError("Window no longer belongs to Chrome")
    if not _window_class(hwnd).startswith("Chrome_WidgetWin_") or user32.GetWindow(hwnd, 4):
        raise InputNotDispatchedError("Chrome window is a popup or unsupported application surface")
    return {"hwnd": int(hwnd), "process_id": pid.value, "process_created": process.create_time()}


def _assert_window(window: dict[str, Any], *, foreground: bool = False) -> None:
    check_cancelled()
    try:
        current = _identity(int(window["hwnd"]))
        if any(current[key] != window[key] for key in ("hwnd", "process_id", "process_created")):
            raise InputNotDispatchedError("Chrome window identity changed; no input delivered")
        if foreground:
            from .semantic_ui_tools import _foreground_hwnd
            if _foreground_hwnd() != current["hwnd"]:
                raise InputNotDispatchedError("Chrome lost foreground; no further input delivered")
    except InputDeliveryError:
        raise
    except Exception as exc:
        check_cancelled()
        raise InputNotDispatchedError("Chrome window identity could not be verified") from exc


def _kind(control) -> str:
    return str(control.element_info.control_type)


def _binding(control) -> tuple:
    info = control.element_info
    runtime_id = tuple(info.runtime_id or ())
    if not runtime_id:
        raise InputNotDispatchedError("Chrome UI control has no stable runtime identity")
    return (int(info.process_id), runtime_id, str(info.control_type), str(info.automation_id))


def _visible(control) -> bool:
    return bool(control.is_visible())


def _aria_role(control) -> str:
    # AriaRole is exposed by IUIAutomationElement, including Chromium's UIA bridge.
    try:
        return str(control.element_info.element.CurrentAriaRole or "").casefold()
    except (AttributeError, NotImplementedError):
        return ""


def _browser_chrome(win) -> dict[str, Any]:
    """Inspect browser toolbar controls without mistaking page inputs for the omnibox."""
    queue = deque((child, False, 1) for child in win.children())
    documents, editors, tabs = [], [], []
    visited = 0
    while queue:
        check_cancelled()
        control, in_toolbar, depth = queue.popleft()
        visited += 1
        if visited > _MAX_NODES or depth > 40:
            raise InputNotDispatchedError("Chrome UI inspection was truncated; no navigation allowed")
        kind = _kind(control)
        visible = _visible(control)
        if visible and (kind in {"Window", "Menu"} or _aria_role(control) in {"dialog", "alertdialog"}):
            raise InputNotDispatchedError("An unexpected Chrome dialog or menu is open")
        if kind == "Document":
            if visible:
                documents.append(control)
            continue  # A web page's toolbar/edit is never browser chrome.
        in_toolbar = in_toolbar or kind == "ToolBar"
        if visible and kind == "Edit" and in_toolbar and control.is_enabled():
            editors.append(control)
        if kind == "TabItem":
            tabs.append(control)
        # Chromium exposes zero-size/offscreen layout containers with visible
        # descendants. Their own visibility does not hide the entire subtree.
        queue.extend((child, in_toolbar, depth + 1) for child in control.children())
    return {"documents": documents, "editors": editors, "tabs": tabs}


def find_chrome_window() -> dict[str, Any] | None:
    """Choose foreground Chrome, otherwise first suitable window in Windows Z-order."""
    if os.name != "nt":
        return None
    from .app_tools import _visible_windows
    from .semantic_ui_tools import _foreground_hwnd
    import psutil
    check_cancelled()
    foreground = _foreground_hwnd()
    windows = _visible_windows()
    windows.sort(key=lambda row: row["hwnd"] != foreground)  # Stable: retain Z-order.
    for row in windows:
        check_cancelled()
        # Avoid querying protected unrelated processes (Task Manager, system UI).
        # An unreadable Chromium surface still fails closed rather than launching
        # an extra browser on an uncertain inventory.
        if not _window_class(row["hwnd"]).startswith("Chrome_WidgetWin_"):
            continue
        try:
            name = psutil.Process(row["pid"]).name().casefold()
        except psutil.NoSuchProcess:
            continue
        except Exception as exc:
            raise InputNotDispatchedError("An open window's process could not be inspected; refusing to launch another Chrome") from exc
        if name not in _CHROME_NAMES:
            continue
        identity = _identity(row["hwnd"])
        chrome = _browser_chrome(_window(identity["hwnd"]))
        if not chrome["editors"]:
            raise InputNotDispatchedError("An existing Chrome surface has no readable browser toolbar; inspect or restore it before navigation")
        if len(chrome["editors"]) != 1:
            raise InputNotDispatchedError("Chrome browser toolbar is ambiguous; inspect the intended window")
        _assert_window(identity)
        return identity
    return None


def _value(control, *, allow_empty: bool = False) -> str:
    for attribute in ("iface_value", "iface_legacy_iaccessible"):
        try:
            value = getattr(control, attribute).CurrentValue
        except Exception as exc:
            if isinstance(exc, AttributeError) or type(exc).__name__ == "NoPatternInterfaceError":
                continue
            raise InputNotDispatchedError("Chrome control value inspection failed") from exc
        if isinstance(value, str) and value:
            return value
    if allow_empty:
        return ""
    raise ChromeObservationPending("Chrome does not expose an independently readable document URL/value")


def _document_url(document, *, allow_empty: bool = False) -> str:
    value = _value(document, allow_empty=allow_empty).strip()
    if not value and allow_empty:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "chrome", "chrome-search", "about"}:
        raise InputNotDispatchedError("Chrome document did not expose a valid loaded-page URL")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise InputNotDispatchedError("Chrome loaded-page URL is incomplete")
    return value


def _document_state(document, *, include_links: bool = False) -> dict[str, Any]:
    try:
        text = document.iface_text.DocumentRange.GetText(_MAX_TEXT + 1)
    except Exception as exc:
        raise ChromeObservationPending("Chrome page text is unavailable; cannot check for human verification") from exc
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        raise InputNotDispatchedError("Chrome page text inspection was truncated; no navigation allowed")
    evidence = []
    if _CHALLENGE.search(text):
        evidence.append("visible_page_human_verification_text")
    links = []
    queue = deque((child, 1) for child in document.children())
    visited = 0
    while queue:
        check_cancelled()
        control, depth = queue.popleft()
        visited += 1
        if visited > _MAX_NODES or depth > 40:
            raise InputNotDispatchedError("Chrome page UI inspection was truncated; no navigation allowed")
        if not _visible(control):
            continue
        kind = _kind(control)
        role = _aria_role(control)
        if kind in {"Window", "Menu"} or role in {"dialog", "alertdialog"}:
            raise InputNotDispatchedError("An unexpected page dialog or menu requires inspection")
        name = str(control.element_info.name or "")
        if _CHALLENGE.search(name):
            evidence.append("visible_control_human_verification_label")
        if include_links and kind == "Hyperlink":
            try:
                href = _value(control)
            except InputNotDispatchedError:
                href = ""
            if urlparse(href).scheme in {"http", "https"}:
                rect = control.rectangle()
                if rect.right <= rect.left or rect.bottom <= rect.top:
                    continue
                links.append({"text": name[:500], "href": href,
                              "_order": (rect.top, rect.left, visited)})
                if len(links) > _MAX_LINKS:
                    raise InputNotDispatchedError("Chrome visible link inventory was truncated; no ordinal navigation allowed")
        queue.extend((child, depth + 1) for child in control.children())
    # UIA child depth is not reading order: a heading nested in a result can be
    # encountered after a footer link. Use fresh visible geometry for ordinals.
    links.sort(key=lambda link: link["_order"])
    for link in links:
        link.pop("_order")
    return {"evidence": list(dict.fromkeys(evidence)), "links": links}


def _observe(window: dict[str, Any], *, include_links: bool = False,
             allow_empty_document: bool = False,
             expected_url: str | None = None) -> tuple[dict, dict]:
    _assert_window(window)
    win = _window(window["hwnd"])
    if not win.is_enabled():
        raise InputNotDispatchedError("Chrome is blocked by a modal window")
    chrome = _browser_chrome(win)
    if not chrome["documents"] and len(chrome["editors"]) == 1:
        raise ChromeObservationPending("Chrome has not published its active document yet")
    if len(chrome["documents"]) != 1 or len(chrome["editors"]) != 1:
        # Chromium can temporarily expose zero or duplicate controls while its
        # accessibility bridge updates after focusing the omnibox or navigating.
        # No target may be chosen from that inventory. A bounded read-only poll
        # may wait for a unique inventory without repeating the preceding input.
        raise ChromeObservationPending(
            "Chrome active page or browser address field is missing/ambiguous "
            f"(documents={len(chrome['documents'])}, address_fields={len(chrome['editors'])})"
        )
    document, editor = chrome["documents"][0], chrome["editors"][0]
    binding = _binding(document)
    url = _document_url(document, allow_empty=allow_empty_document)
    challenge_url = bool(_CHALLENGE.search(url) or "/sorry/" in urlparse(url).path)
    # Completion polling is read-only. Avoid rescanning the previous page's
    # entire accessibility tree while Chrome has not reached the destination.
    # Every input guard still performs the full scan, as does the final result.
    if expected_url is not None and url != expected_url and not challenge_url:
        raise ChromeObservationPending("Chrome has not reached the requested document URL yet")
    state = _document_state(document, include_links=include_links)
    if challenge_url:
        state["evidence"].append("loaded_page_human_verification_url")
    # Check both object identity and URL after reading the page, not the omnibox draft.
    if _binding(document) != binding or _document_url(document, allow_empty=allow_empty_document) != url:
        raise InputNotDispatchedError("Chrome page changed during observation; inspect again")
    _assert_window(window)
    result = {
        "session_type": "existing-window", "window": dict(window), "url": url,
        "title": str(document.element_info.name or win.window_text()),
        "challenge_detected": bool(state["evidence"]), "evidence": state["evidence"],
        "document_identity": binding, "tab_count": len(chrome["tabs"]),
    }
    if include_links:
        result["links"] = state["links"]
    record_backend("windows_uia", phase="observe", detail="Inspect exact existing Chrome window")
    return result, {"document": document, "editor": editor, "editor_binding": _binding(editor)}


def _observe_ready(window: dict[str, Any], *, foreground: bool = False,
                   timeout: float = 3, **observation_options) -> tuple[dict, dict]:
    """Wait only for publication; never refocus, choose an ambiguous target or replay input."""
    last_pending: ChromeObservationPending | None = None

    def ready():
        nonlocal last_pending
        _assert_window(window, foreground=foreground)
        try:
            result = _observe(window, **observation_options)
        except ChromeObservationPending as exc:
            last_pending = exc
            return None
        _assert_window(window, foreground=foreground)
        return result

    try:
        return wait_until(ready, timeout=timeout, interval=0.05,
                          description="unique readable Chrome page and address field")
    except TimeoutError as exc:
        if last_pending is None:
            raise
        # Preserve pre-dispatch classification. The navigation wrapper separately
        # marks this uncertain when a native input was already attempted.
        raise ChromeObservationPending(
            f"Chrome interface did not become ready within {timeout:g}s: {last_pending}"
        ) from exc


def read_existing_chrome(window: dict[str, Any], *, include_links: bool = False) -> dict[str, Any]:
    return _observe_ready(window, include_links=include_links)[0]


def _clear(state: dict[str, Any]) -> None:
    if state["challenge_detected"]:
        raise BrowserChallengeBlocked("BROWSER_ACTION_BLOCKED: Human verification is present in the existing Chrome window; complete it manually")


def focus_existing_chrome(window: dict[str, Any]) -> dict[str, Any]:
    """Bring an identified browser forward without navigating or typing."""
    from .semantic_ui_tools import _focus_window
    before = read_existing_chrome(window)
    _clear(before)
    _focus_window(_window(window["hwnd"]))
    _assert_window(window, foreground=True)
    after = read_existing_chrome(window)
    _clear(after)
    _assert_window(window, foreground=True)
    return {**after, "verified": True, "focused": True}


def _is_new_tab(url: str) -> bool:
    return url in {"chrome://newtab", "chrome://newtab/", "about:blank",
                   "chrome-search://local-ntp/local-ntp.html"}


def _address_matches_document(value: str, url: str) -> bool:
    if value == url or (not value and _is_new_tab(url)):
        return True
    # Chrome may elide the scheme and the root slash in the visible address.
    # Never normalize a query/fragment or treat any other draft as a loaded URL.
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    display = url[len(parsed.scheme) + 3:]
    alternatives = {display}
    if parsed.hostname and parsed.hostname.startswith("www.") and display.startswith("www."):
        alternatives.add(display[4:])
    if parsed.path == "/" and not parsed.query and not parsed.fragment:
        alternatives.update({candidate[:-1] for candidate in tuple(alternatives)})
        alternatives.add(url[:-1])
    return value in alternatives


def _selection_is_all(editor, expected: str) -> bool:
    try:
        pattern = editor.iface_text
        ranges = pattern.GetSelection()
        if ranges.Length != 1:
            return False
        selected = ranges.GetElement(0)
        entire = pattern.DocumentRange
        return (selected.CompareEndpoints(0, entire, 0) == 0
                and selected.CompareEndpoints(1, entire, 1) == 0
                and selected.GetText(len(expected) + 1) == expected)
    except Exception:
        return False


def _address_value(editor) -> str:
    try:
        value = editor.iface_value.CurrentValue
    except Exception as exc:
        raise InputNotDispatchedError("Chrome address field value cannot be verified") from exc
    if not isinstance(value, str):
        raise InputNotDispatchedError("Chrome address field value is invalid")
    return value


def navigate_existing_chrome(url: str, *, new_tab: bool = False,
                             window: dict[str, Any] | None = None) -> dict[str, Any]:
    """Navigate once, verifying toolbar input and the independent loaded document."""
    delivery = {"attempted": False}
    try:
        return _navigate_existing_chrome(url, new_tab=new_tab, window=window, delivery=delivery)
    except BrowserChallengeBlocked:
        raise
    except Exception as exc:
        if delivery["attempted"]:
            raise InputDeliveryError(
                "Existing Chrome navigation outcome is uncertain; inspect before retrying: " + str(exc)
            ) from exc
        raise


def _navigate_existing_chrome(url: str, *, new_tab: bool, window: dict[str, Any] | None,
                              delivery: dict[str, bool]) -> dict[str, Any]:
    from .semantic_ui_tools import _focus_window, _rust_hotkey_or_python, _rust_type_or_python
    parsed = urlparse(url)
    blank_tab = new_tab and url == "about:blank"
    if not blank_tab and (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or any(ord(char) < 32 for char in url) or len(url) > 4096):
        raise ValueError("Existing Chrome navigation requires a safe HTTP(S) URL")
    identity = dict(window or find_chrome_window() or {})
    if not identity:
        raise InputNotDispatchedError("No existing Chrome browser window was found")
    focused_for_readiness = False
    try:
        before, handles = _observe(identity)
    except ChromeObservationPending:
        # Background Chrome may not publish its active document until activated.
        # Activate only the already identified window, then await a full readable
        # page/challenge check before any keyboard input. Never switch windows to
        # resolve ambiguity or use the address draft as document evidence.
        _assert_window(identity)
        _focus_window(_window(identity["hwnd"]))
        _assert_window(identity, foreground=True)
        focused_for_readiness = True
        before, handles = _observe_ready(identity, foreground=True)
    initial_tab_count = before["tab_count"]
    _clear(before)
    if not _address_matches_document(_address_value(handles["editor"]), before["url"]):
        raise InputNotDispatchedError("Chrome address field contains a draft; no navigation allowed")
    if not focused_for_readiness:
        _focus_window(_window(identity["hwnd"]))
    _assert_window(identity, foreground=True)

    def guard(*, focused: bool = False, value: str | None = None,
              select_all: bool = False) -> None:
        _assert_window(identity, foreground=True)
        current, current_handles = _observe_ready(
            identity, foreground=True,
            allow_empty_document=before.get("page_kind") == "new_tab")
        _clear(current)
        if (current["url"] != before["url"] or current["document_identity"] != before["document_identity"]
                or current_handles["editor_binding"] != handles["editor_binding"]):
            raise InputNotDispatchedError("Chrome tab or address control changed before input")
        editor = handles["editor"]
        if focused and not editor.has_keyboard_focus():
            raise InputNotDispatchedError("Chrome address field lost focus; no input delivered")
        if value is None and not _address_matches_document(_address_value(editor), before["url"]):
            raise InputNotDispatchedError("Chrome address field acquired a draft before input")
        if value is not None and _address_value(editor) != value:
            raise InputNotDispatchedError("Chrome address field changed before input")
        if select_all and value and not _selection_is_all(editor, value):
            raise InputNotDispatchedError("Chrome address selection could not be verified; no text delivered")
        _assert_window(identity, foreground=True)

    def shortcut(keys: list[str], input_guard=guard) -> str:
        # Once a dispatch is attempted, any later validation failure must not be
        # reported as a harmless preflight failure or replayed by another engine.
        delivery["attempted"] = True
        return _rust_hotkey_or_python(keys, hwnd=identity["hwnd"], guard=input_guard)

    if new_tab:
        new_tab_engine = shortcut(["ctrl", "t"])

        def new_tab_loaded():
            _assert_window(identity, foreground=True)
            try:
                state, controls = _observe(identity, allow_empty_document=True)
            except ChromeObservationPending:
                return None
            _clear(state)
            # Some Chrome new-tab documents expose an empty Value. This is not
            # navigation evidence: bind it only after our own Ctrl+T, an extra tab,
            # a different document, and the empty focused browser address field.
            empty_created_tab = (not state["url"]
                and state["document_identity"] != before["document_identity"]
                and not _address_value(controls["editor"])
                and controls["editor"].has_keyboard_focus())
            if state["tab_count"] == before["tab_count"] + 1 and (_is_new_tab(state["url"]) or empty_created_tab):
                state["page_kind"] = "new_tab"
                return state, controls
            return None

        before, handles = wait_until(new_tab_loaded, timeout=3, description="new tab in the selected Chrome window")
        if blank_tab:
            return {**before, "verified": True, "new_tab": True, "requested_url": url,
                    "initial_tab_count": initial_tab_count,
                    "execution_engine": new_tab_engine,
                    "evidence": ["exact_window_identity", "new_tab_count", "independent_new_tab_document"]}

    editor = handles["editor"]
    # Ctrl+T normally focuses an empty omnibox already. Its verified binding is
    # sufficient here; typing still rechecks identity, focus, value and page state
    # immediately before dispatch, including popups or a user changing focus.
    if not (new_tab and editor.has_keyboard_focus() and not _address_value(editor)):
        shortcut(["ctrl", "l"])
    wait_until(lambda: editor.has_keyboard_focus() and (_assert_window(identity, foreground=True) is None),
               description="Chrome address field focus")
    original = _address_value(editor)
    if original and not _selection_is_all(editor, original):
        shortcut(["ctrl", "a"], lambda: guard(focused=True, value=original))
    # TextRange endpoints prevent accidental appending or typing into a web field.
    delivery["attempted"] = True
    engine = _rust_type_or_python(url, hwnd=identity["hwnd"],
                                 guard=lambda: guard(focused=True, value=original, select_all=True))
    shortcut(["enter"], lambda: guard(focused=True, value=url))

    def loaded():
        _assert_window(identity, foreground=True)
        try:
            state, _ = _observe(identity, expected_url=url)
        except ChromeObservationPending:
            return None
        _clear(state)
        # An address-bar draft is not proof of page navigation. Require the independent
        # active Document URL to equal the requested destination, including its query.
        if state["url"] == url:
            return state
        return None

    after = wait_until(loaded, timeout=10, interval=0.05, description="loaded document URL in existing Chrome")
    after.update(verified=True, new_tab=bool(new_tab), execution_engine=engine,
                 initial_tab_count=initial_tab_count,
                 evidence=["exact_window_identity", "native_address_input_readback", "independent_document_url"])
    return after
