from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


class _ChromeRuntime:
    """Own all Playwright Sync API objects on one dedicated Python thread."""

    def __init__(self) -> None:
        self._commands: queue.Queue[tuple[str, dict[str, Any], queue.Queue[Any]]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="jarvis-chrome-cdp", daemon=True)
        self._thread.start()
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._browser: Any = None
        self._playwright: Any = None
        self._pages: list[Any] = []
        self._page: Any = None
        self._session_type = "unknown"
        self._endpoint = ""

    def _run(self) -> None:
        # The loop is created before Playwright starts. Commands are processed here,
        # keeping every Sync API object thread-affine and isolated from the agent loop.
        try:
            self._ready.set()
            while True:
                command, args, reply = self._commands.get()
                if command == "shutdown":
                    try:
                        if self._browser is not None:
                            self._browser.close()
                    finally:
                        if self._playwright is not None:
                            self._playwright.stop()
                    reply.put(None)
                    return
                try:
                    result = getattr(self, f"_cmd_{command}")(**args)
                    reply.put((True, result))
                except Exception as exc:
                    reply.put((False, exc))
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()

    def call(self, command: str, **args: Any) -> Any:
        self._ready.wait(timeout=5)
        if self._startup_error is not None:
            raise RuntimeError(f"Chrome runtime thread failed: {self._startup_error}")
        reply: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._commands.put((command, args, reply))
        ok, value = reply.get(timeout=60)
        if ok:
            return value
        raise value

    def _cmd_connect(self, endpoint: str, session_type: str = "real") -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        if self._browser is not None:
            try:
                self._browser.contexts
            except Exception:
                self._browser = None
                self._playwright = None

        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(endpoint, timeout=5000)

        pages: list[Any] = []
        for context in self._browser.contexts:
            pages.extend(context.pages)
        if not pages:
            context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            self._page = context.new_page()
            pages = [self._page]
        else:
            self._page = pages[-1]
        self._pages = pages
        self._session_type = session_type
        self._endpoint = endpoint
        return {
            "connected": True,
            "endpoint": endpoint,
            "session_type": session_type,
            "pages": _page_rows(pages),
            "active_url": self._page.url,
            "active_title": self._page.title(),
        }

    def _refresh_pages(self) -> list[Any]:
        pages: list[Any] = []
        for context in self._browser.contexts:
            pages.extend(context.pages)
        self._pages = pages
        if self._page not in pages and pages:
            self._page = pages[-1]
        return pages

    def _cmd_tabs(self) -> list[dict[str, Any]]:
        return _page_rows(self._refresh_pages())

    def _cmd_use_tab(self, index: int) -> dict[str, Any]:
        pages = self._refresh_pages()
        if index < 0 or index >= len(pages):
            raise IndexError(f"Tab index {index} is out of range; {len(pages)} tabs are available.")
        self._page = pages[index]
        return {
            "selected": index,
            "session_type": self._session_type,
            "title": self._page.title(),
            "url": self._page.url,
        }

    def _cmd_current(self) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("No browser tab is selected.")
        return {
            "session_type": self._session_type,
            "title": self._page.title(),
            "url": self._page.url,
        }

    def _cmd_page(self, operation: str, **args: Any) -> Any:
        if self._page is None:
            raise RuntimeError("No browser page is open")
        page = self._page
        if operation == "goto":
            page.goto(args["url"], wait_until="domcontentloaded", timeout=30000)
            return {"title": page.title(), "url": page.url}
        if operation == "title":
            return page.title()
        if operation == "url":
            return page.url
        if operation == "body_text":
            return page.locator("body").inner_text()[:20000]
        if operation == "links":
            return page.locator("a").evaluate_all(
                "els => els.slice(0, 300).map(a => ({text:(a.innerText||a.textContent||'').trim(), href:a.href})).filter(x => x.text || x.href)"
            )
        if operation == "click":
            selector = args["selector"]
            locator = page.get_by_text(selector, exact=True)
            if locator.count() == 0:
                locator = page.locator(selector)
            locator.first.click(timeout=15000)
            return True
        if operation == "fill":
            page.locator(args["selector"]).first.fill(args["text"], timeout=15000)
            return True
        if operation == "wait":
            page.locator(args["selector"]).first.wait_for(
                state="visible",
                timeout=max(500, min(int(args.get("timeout_ms", 15000)), 60000)),
            )
            return True
        if operation == "press":
            page.keyboard.press(args["key"])
            return True
        if operation == "screenshot":
            page.screenshot(path=args["path"], full_page=False)
            return True
        raise ValueError(f"Unsupported Chrome page operation: {operation}")


def _runtime() -> _ChromeRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = _ChromeRuntime()
    return _RUNTIME


_RUNTIME: _ChromeRuntime | None = None


def _cdp_url() -> str:
    return os.getenv("JARVIS_CHROME_CDP_URL", "http://127.0.0.1:9222").rstrip("/")


def _chrome_executable() -> str:
    candidates = [
        os.getenv("JARVIS_CHROME_EXE", "").strip(),
        shutil.which("chrome.exe") or "",
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    raise FileNotFoundError("Google Chrome executable was not found. Set JARVIS_CHROME_EXE to chrome.exe.")


def _managed_runtime_dir() -> Path:
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    runtime = workspace / ".jarvis" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def _managed_profile_dir() -> Path:
    temp_root = os.getenv("TEMP") or os.getenv("TMP")
    base = Path(temp_root).resolve() if temp_root else Path.home() / "AppData" / "Local" / "Temp"
    return base / "jarvis-cdp-profile"


def _cdp_port(endpoint: str) -> int:
    try:
        return int(endpoint.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return 9222


def chrome_start_managed() -> str:
    endpoint = _cdp_url()
    port = _cdp_port(endpoint)
    exe = _chrome_executable()
    profile = _managed_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    runtime = _managed_runtime_dir()
    log_path = runtime / "chrome-cdp-start.log"
    command = [
        exe,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--start-maximized",
        "--new-window",
        "about:blank",
    ]
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n=== JARVIS managed Chrome start ===\n")
        log.write(json.dumps({"exe": exe, "args": command, "profile": str(profile), "endpoint": endpoint}, ensure_ascii=False) + "\n")
        log.flush()
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    deadline = time.time() + 15
    last_error = "unknown error"
    while time.time() < deadline:
        if process.poll() is not None:
            recent_log = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(
                f"Chrome exited during managed startup (exit_code={process.returncode}). "
                f"CDP endpoint {endpoint} never became available. Startup log: {log_path}\n{recent_log}"
            )
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.0) as response:
                if response.status == 200:
                    return json.dumps({
                        "started": True,
                        "pid": process.pid,
                        "endpoint": endpoint,
                        "profile": str(profile),
                        "log": str(log_path),
                        "session_type": "managed",
                    }, ensure_ascii=False)
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)

    recent_log = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    raise RuntimeError(
        f"Chrome started with pid={process.pid}, but CDP did not become available at {endpoint}: {last_error}. "
        f"Startup log: {log_path}\n{recent_log}"
    )


def chrome_connect_cdp() -> str:
    endpoint = _cdp_url()
    runtime = _runtime()
    try:
        result = runtime.call("connect", endpoint=endpoint, session_type="real")
        return json.dumps(result, ensure_ascii=False)
    except Exception as real_error:
        managed = json.loads(chrome_start_managed())
        result = runtime.call("connect", endpoint=endpoint, session_type="managed")
        result.update({
            "reused": False,
            "fallback_from_real": True,
            "real_error": str(real_error),
            "managed_pid": managed.get("pid"),
            "profile": managed.get("profile"),
            "startup_log": managed.get("log"),
            "note": "JARVIS could not attach to an existing remotely-debuggable Chrome, so it launched an isolated real Chrome profile.",
        })
        return json.dumps(result, ensure_ascii=False)


def chrome_tabs() -> str:
    return json.dumps(_runtime().call("tabs"), ensure_ascii=False)


def chrome_use_tab(index: int) -> str:
    return json.dumps(_runtime().call("use_tab", index=index), ensure_ascii=False)


def chrome_current_tab() -> str:
    return json.dumps(_runtime().call("current"), ensure_ascii=False)


def chrome_page_operation(operation: str, **args: Any) -> Any:
    """Run a Playwright page operation on the dedicated Chrome runtime thread."""
    return _runtime().call("page", operation=operation, **args)


def _page_rows(pages: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        try:
            rows.append({"index": index, "title": page.title(), "url": page.url})
        except Exception:
            rows.append({"index": index, "title": "", "url": getattr(page, "url", "")})
    return rows


def register_chrome_cdp_tools(registry) -> None:
    from .tools import ToolSpec
    from .permissions import Risk

    registry.register(ToolSpec(
        "chrome_start_managed",
        "Launch a dedicated visible Google Chrome instance with CDP enabled for JARVIS using an isolated TEMP profile. Startup diagnostics are persisted under .jarvis/runtime.",
        Risk.MEDIUM,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_start_managed,
    ))
    registry.register(ToolSpec(
        "chrome_connect_cdp",
        "Connect JARVIS to Chrome through CDP without exposing Playwright Sync API to the agent asyncio loop. Automatically uses an isolated managed Chrome fallback when needed.",
        Risk.MEDIUM,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_connect_cdp,
    ))
    registry.register(ToolSpec(
        "chrome_tabs",
        "List tabs exposed by the connected Chrome session with stable indexes, titles, URLs, and session context.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_tabs,
    ))
    registry.register(ToolSpec(
        "chrome_use_tab",
        "Select a tab from the connected Chrome session by index so existing browser tools operate on that actual tab.",
        Risk.LOW,
        {"type": "object", "properties": {"index": {"type": "integer", "minimum": 0}}, "required": ["index"]},
        chrome_use_tab,
    ))
    registry.register(ToolSpec(
        "chrome_current_tab",
        "Return the title, URL, and session type of the currently selected Chrome tab.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        chrome_current_tab,
    ))