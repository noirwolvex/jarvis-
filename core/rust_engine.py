from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_MAX_FRAME_BYTES = 256 * 1024
_ALPN = "jarvis-execution/1"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ENGINE_MODES = {"auto", "python", "rust"}


class RustEngineUnavailable(RuntimeError):
    """The Rust execution daemon cannot safely be used before an action is dispatched."""


class RustEngineExecutionError(RuntimeError):
    """A Rust-backed action failed or became uncertain after dispatch."""


def native_engine_mode(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    mode = str(source.get("JARVIS_NATIVE_ENGINE", "auto")).strip().lower()
    if mode not in _ENGINE_MODES:
        raise ValueError("JARVIS_NATIVE_ENGINE must be auto, python, or rust")
    return mode


def _repo_root(env: Mapping[str, str]) -> Path:
    configured = str(env.get("JARVIS_REPO_ROOT", "")).strip()
    return Path(configured or os.getcwd()).expanduser().resolve()


def _resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _capability_map(env: Mapping[str, str], single_name: str, json_name: str) -> dict[int, str]:
    result: dict[int, str] = {}
    single = str(env.get(single_name, "")).strip()
    if single:
        if len(single) > 128 or "\0" in single:
            raise ValueError(f"{single_name} is invalid")
        result[0] = single

    encoded = str(env.get(json_name, "")).strip()
    if not encoded:
        return result
    try:
        parsed = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{json_name} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{json_name} must be a JSON object")
    for raw_display, raw_capability in parsed.items():
        try:
            display_id = int(raw_display)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{json_name} display ids must be integers") from exc
        capability = str(raw_capability).strip()
        if display_id < 0 or not capability or len(capability) > 128 or "\0" in capability:
            raise ValueError(f"{json_name} contains an invalid capability")
        result[display_id] = capability
    return result


@dataclass(frozen=True)
class RustEngineConfig:
    host: str
    port: int
    server_name: str
    ca_path: Path
    client_cert_path: Path
    client_key_path: Path
    observe_capabilities: dict[int, str]
    input_capabilities: dict[int, str]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RustEngineConfig | None:
        source = os.environ if env is None else env
        mode = native_engine_mode(source)
        if mode == "python":
            return None

        host = str(source.get("JARVIS_DAEMON_HOST", "127.0.0.1")).strip()
        if host not in _ALLOWED_HOSTS:
            raise ValueError("JARVIS_DAEMON_HOST must be loopback")
        try:
            port = int(str(source.get("JARVIS_DAEMON_PORT", "7443")).strip())
        except ValueError as exc:
            raise ValueError("JARVIS_DAEMON_PORT is invalid") from exc
        if port < 1 or port > 65535:
            raise ValueError("JARVIS_DAEMON_PORT is invalid")
        server_name = str(source.get("JARVIS_DAEMON_SERVER_NAME", "localhost")).strip()
        if not server_name or len(server_name) > 253 or "\0" in server_name:
            raise ValueError("JARVIS_DAEMON_SERVER_NAME is invalid")

        required = {
            "JARVIS_DAEMON_CA": str(source.get("JARVIS_DAEMON_CA", "")).strip(),
            "JARVIS_DAEMON_CLIENT_CERT": str(source.get("JARVIS_DAEMON_CLIENT_CERT", "")).strip(),
            "JARVIS_DAEMON_CLIENT_KEY": str(source.get("JARVIS_DAEMON_CLIENT_KEY", "")).strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            if mode == "auto":
                return None
            raise RustEngineUnavailable("Rust engine configuration is incomplete: " + ", ".join(missing))

        root = _repo_root(source)
        config = cls(
            host=host,
            port=port,
            server_name=server_name,
            ca_path=_resolve_path(required["JARVIS_DAEMON_CA"], root),
            client_cert_path=_resolve_path(required["JARVIS_DAEMON_CLIENT_CERT"], root),
            client_key_path=_resolve_path(required["JARVIS_DAEMON_CLIENT_KEY"], root),
            observe_capabilities=_capability_map(
                source,
                "JARVIS_DAEMON_OBSERVE_CAPABILITY",
                "JARVIS_DAEMON_OBSERVE_CAPABILITIES_JSON",
            ),
            input_capabilities=_capability_map(
                source,
                "JARVIS_DAEMON_INPUT_CAPABILITY",
                "JARVIS_DAEMON_INPUT_CAPABILITIES_JSON",
            ),
        )
        missing_files = [
            str(path)
            for path in (config.ca_path, config.client_cert_path, config.client_key_path)
            if not path.is_file()
        ]
        if missing_files:
            if mode == "auto":
                return None
            raise RustEngineUnavailable("Rust engine TLS material is missing: " + ", ".join(missing_files))
        return config

    def capability(self, kind: str, display_id: int) -> str:
        table = self.observe_capabilities if kind == "observe" else self.input_capabilities
        value = table.get(int(display_id))
        if not value:
            raise RustEngineUnavailable(f"Rust daemon has no {kind} capability configured for display {display_id}")
        return value


class RustDaemonClient:
    """Persistent, length-framed TLS 1.3 client for the local Rust execution daemon."""

    def __init__(self, config: RustEngineConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._socket: ssl.SSLSocket | None = None
        self._session = ""
        self._sequence = 0
        self._keyboard_frame_cache: tuple[int, int, int, dict[str, Any], float] | None = None

    def close(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
            self._session = ""
            self._sequence = 0
            self._keyboard_frame_cache = None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    @staticmethod
    def _recv_exact(sock: ssl.SSLSocket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("Rust daemon closed the IPC connection")
            data.extend(chunk)
        return bytes(data)

    @classmethod
    def _read_frame(cls, sock: ssl.SSLSocket) -> dict[str, Any]:
        size = struct.unpack(">I", cls._recv_exact(sock, 4))[0]
        if size < 1 or size > _MAX_FRAME_BYTES:
            raise RuntimeError("Rust daemon returned an invalid frame length")
        payload = json.loads(cls._recv_exact(sock, size).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Rust daemon returned a non-object reply")
        return payload

    def _connect(self) -> None:
        self.close()
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(self.config.ca_path))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.config.client_cert_path), str(self.config.client_key_path))
        context.set_alpn_protocols([_ALPN])
        raw: socket.socket | None = None
        tls: ssl.SSLSocket | None = None
        try:
            raw = socket.create_connection((self.config.host, self.config.port), timeout=5.0)
            tls = context.wrap_socket(raw, server_hostname=self.config.server_name)
            tls.settimeout(10.0)
            if tls.selected_alpn_protocol() != _ALPN:
                raise RuntimeError("Rust daemon ALPN protocol mismatch")
            hello = self._read_frame(tls)
            session = hello.get("session")
            if hello.get("type") != "hello" or hello.get("protocol") != 1 or not isinstance(session, str):
                raise RuntimeError("Unsupported Rust daemon protocol")
            maximum = hello.get("max_frame_bytes")
            if not isinstance(maximum, int) or maximum < 1024 or maximum > _MAX_FRAME_BYTES:
                raise RuntimeError("Rust daemon advertised an invalid frame limit")
            self._socket = tls
            self._session = session
            self._sequence = 0
        except Exception as exc:
            if tls is not None:
                try:
                    tls.close()
                except OSError:
                    pass
            elif raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass
            raise RustEngineUnavailable(f"Rust daemon connection failed: {type(exc).__name__}: {exc}") from exc

    def _request(
        self,
        action: dict[str, Any],
        capability_id: str | None = None,
        *,
        mutating: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self._socket is None or self._sequence >= 240:
                self._connect()
            assert self._socket is not None
            request_id = str(uuid.uuid4())
            self._sequence += 1
            request = {
                "protocol": 1,
                "session": self._session,
                "seq": self._sequence,
                "expires_at_ms": int(time.time() * 1000) + 5000,
                "request_id": request_id,
                "capability_id": capability_id,
                "action": action,
            }
            encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if len(encoded) > _MAX_FRAME_BYTES:
                raise ValueError("Rust daemon request exceeds frame limit")
            dispatch_may_have_started = False
            try:
                # sendall can fail after transmitting a partial request. Mark a mutation
                # uncertain before the first write attempt so auto mode never replays it
                # through the Python fallback after a transport error.
                dispatch_may_have_started = mutating
                from .execution_telemetry import record_backend
                record_backend("rust_native", phase="execute" if mutating else "observe",
                               detail=str(action.get("kind", "request")))
                self._socket.sendall(struct.pack(">I", len(encoded)) + encoded)
                for _ in range(64):
                    reply = self._read_frame(self._socket)
                    if reply.get("type") != "result" or reply.get("request_id") != request_id:
                        continue
                    data = reply.get("data")
                    if reply.get("ok") is not True:
                        detail = data.get("error") if isinstance(data, dict) else None
                        raise RustEngineExecutionError(str(detail or "Rust daemon rejected the action"))
                    if not isinstance(data, dict):
                        raise RustEngineExecutionError("Rust daemon returned invalid result data")
                    return data
                raise RustEngineExecutionError("Rust daemon did not return a terminal result")
            except RustEngineExecutionError:
                raise
            except Exception as exc:
                self.close()
                if dispatch_may_have_started:
                    raise RustEngineExecutionError(
                        "Rust engine connection failed after dispatch may have started; action outcome is uncertain. Re-observe before retrying."
                    ) from exc
                raise RustEngineUnavailable(f"Rust daemon request failed: {type(exc).__name__}: {exc}") from exc

    def status(self) -> dict[str, Any]:
        return self._request({"kind": "status"})

    def capture(self, display_id: int) -> dict[str, Any]:
        return self._request(
            {"kind": "capture", "display_id": int(display_id)},
            self.config.capability("observe", display_id),
        )

    def emergency_stop(self) -> dict[str, Any]:
        return self._request({"kind": "emergency_stop"}, mutating=True)

    @staticmethod
    def _displays(status: dict[str, Any]) -> list[dict[str, Any]]:
        rows = status.get("displays")
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    @classmethod
    def _display_for_point(cls, status: dict[str, Any], x: int, y: int) -> dict[str, Any]:
        for row in cls._displays(status):
            try:
                left, top = int(row["x"]), int(row["y"])
                width, height = int(row["width"]), int(row["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if width > 0 and height > 0 and left <= x < left + width and top <= y < top + height:
                return row
        raise RustEngineUnavailable("Rust daemon did not report a display containing the requested coordinate")

    @staticmethod
    def _foreground(status: dict[str, Any]) -> dict[str, Any]:
        foreground = status.get("foreground")
        if not isinstance(foreground, dict):
            raise RustEngineUnavailable("Rust daemon did not report a foreground window")
        hwnd = foreground.get("hwnd")
        process_id, title = foreground.get("process_id"), foreground.get("title")
        if (
            not isinstance(hwnd, int) or hwnd < 1
            or not isinstance(process_id, int) or process_id < 1
            or not isinstance(title, str) or len(title) > 512 or "\0" in title
        ):
            raise RustEngineUnavailable("Rust daemon returned an invalid foreground binding")
        return {"hwnd": hwnd, "process_id": process_id, "title": title}

    @staticmethod
    def _foreground_center(hwnd: int) -> tuple[int, int] | None:
        if os.name != "nt":
            return None
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            if not ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
                return None
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return None
            return (
                int((rect.left + rect.right) // 2),
                int((rect.top + rect.bottom) // 2),
            )
        except Exception:
            return None

    def _input_context_for_display(
        self,
        state: dict[str, Any],
        display_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        foreground = self._foreground(state)
        capture = self.capture(display_id)
        frame = capture.get("frame")
        if not isinstance(frame, dict) or not isinstance(frame.get("id"), str):
            raise RustEngineExecutionError("Rust daemon capture did not return a usable frame")
        return foreground, frame

    def _keyboard_input_context(
        self,
        status: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        state = status or self.status()
        displays = self._displays(state)
        if not displays:
            raise RustEngineUnavailable("Rust daemon did not report any capturable display")
        foreground = self._foreground(state)
        center = self._foreground_center(int(foreground["hwnd"]))
        selected: dict[str, Any] | None = None
        if center is not None:
            try:
                selected = self._display_for_point(state, center[0], center[1])
            except RustEngineUnavailable:
                selected = None
        if selected is None:
            selected = next(
                (
                    row for row in displays
                    if int(row.get("id", -1)) in self.config.input_capabilities
                    and int(row.get("id", -1)) in self.config.observe_capabilities
                ),
                displays[0],
            )
        display_id = int(selected.get("id", -1))
        if display_id < 0:
            raise RustEngineUnavailable("Rust daemon returned an invalid display id")
        # Keyboard/wheel actions do not use frame pixels or coordinates. Reuse one
        # very recent authorized frame across a tight input burst instead of forcing
        # xcap.capture_image() before every key. The daemon still checks the frame's
        # own <2s monotonic TTL and independently verifies HWND+PID before each action.
        now = time.monotonic()
        cached = self._keyboard_frame_cache
        if cached is not None:
            cached_display, cached_hwnd, cached_pid, cached_frame, cached_at = cached
            if (
                cached_display == display_id
                and cached_hwnd == foreground["hwnd"]
                and cached_pid == foreground["process_id"]
                and now - cached_at < 0.75
                and isinstance(cached_frame.get("id"), str)
            ):
                return display_id, foreground, cached_frame

        captured_foreground, frame = self._input_context_for_display(state, display_id)
        if (
            captured_foreground["hwnd"] != foreground["hwnd"]
            or captured_foreground["process_id"] != foreground["process_id"]
        ):
            self._keyboard_frame_cache = None
            raise RustEngineUnavailable("Foreground changed while preparing keyboard input")
        self._keyboard_frame_cache = (
            display_id,
            int(foreground["hwnd"]),
            int(foreground["process_id"]),
            frame,
            now,
        )
        return display_id, captured_foreground, frame

    def click(self, x: int, y: int, status: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.click_button(x, y, "left", 1, status)

    def click_button(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(button).strip().casefold()
        if normalized not in {"left", "right", "middle"}:
            raise ValueError("Rust mouse button must be left, right, or middle")
        count = int(clicks)
        if not 1 <= count <= 3:
            raise ValueError("Rust click count must be between 1 and 3")
        self._keyboard_frame_cache = None
        state = status or self.status()
        display = self._display_for_point(state, int(x), int(y))
        display_id = int(display["id"])
        foreground, frame = self._input_context_for_display(state, display_id)
        return self._request(
            {
                "kind": "click_button",
                "display_id": display_id,
                "frame_id": frame["id"],
                "x": int(x),
                "y": int(y),
                "button": normalized,
                "clicks": count,
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def pointer_move(
        self,
        x: int,
        y: int,
        status: dict[str, Any] | None = None,
        *,
        duration: float = 0.0,
    ) -> dict[str, Any]:
        seconds = float(duration)
        if not 0 <= seconds <= 2:
            raise ValueError("Rust pointer duration must be between 0 and 2 seconds")
        self._keyboard_frame_cache = None
        state = status or self.status()
        duration_ms = int(round(seconds * 1000))
        if duration_ms and "timed_pointer_move" not in state.get("input_features", []):
            raise RustEngineUnavailable("Rust daemon does not support timed pointer movement; rebuild the daemon or use duration=0")
        display = self._display_for_point(state, int(x), int(y))
        display_id = int(display["id"])
        foreground, frame = self._input_context_for_display(state, display_id)
        return self._request(
            {
                "kind": "pointer_move",
                "display_id": display_id,
                "frame_id": frame["id"],
                "x": int(x),
                "y": int(y),
                **({"duration_ms": duration_ms} if duration_ms else {}),
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.25,
        button: str = "left",
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = str(button).strip().casefold()
        if normalized not in {"left", "right", "middle"}:
            raise ValueError("Rust drag button must be left, right, or middle")
        seconds = float(duration)
        if not 0.05 <= seconds <= 2.0:
            raise ValueError("Rust drag duration must be between 0.05 and 2 seconds")
        self._keyboard_frame_cache = None
        state = status or self.status()
        start_display = self._display_for_point(state, int(start_x), int(start_y))
        end_display = self._display_for_point(state, int(end_x), int(end_y))
        if int(start_display["id"]) != int(end_display["id"]):
            raise RustEngineUnavailable("Rust drag currently requires start and end on the same display")
        display_id = int(start_display["id"])
        foreground, frame = self._input_context_for_display(state, display_id)
        return self._request(
            {
                "kind": "drag",
                "display_id": display_id,
                "frame_id": frame["id"],
                "start_x": int(start_x),
                "start_y": int(start_y),
                "end_x": int(end_x),
                "end_y": int(end_y),
                "duration_ms": int(round(seconds * 1000)),
                "button": normalized,
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def scroll(
        self,
        clicks: int,
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        amount = int(clicks)
        if not -1000 <= amount <= 1000:
            raise ValueError("Rust scroll requires -1000..1000 wheel steps")
        display_id, foreground, frame = self._keyboard_input_context(status)
        return self._request(
            {
                "kind": "scroll",
                "display_id": display_id,
                "frame_id": frame["id"],
                "clicks": amount,
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def press_key(
        self,
        key: str,
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = str(key).strip()
        if not value or len(value) > 32 or "\0" in value:
            raise ValueError("Rust key name must contain 1-32 safe characters")
        display_id, foreground, frame = self._keyboard_input_context(status)
        return self._request(
            {
                "kind": "press_key",
                "display_id": display_id,
                "frame_id": frame["id"],
                "key": value,
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def hotkey(
        self,
        keys: list[str],
        status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = [str(key).strip() for key in keys]
        if not 1 <= len(values) <= 8 or any(not key or len(key) > 32 or "\0" in key for key in values):
            raise ValueError("Rust hotkey requires 1-8 safe key names")
        display_id, foreground, frame = self._keyboard_input_context(status)
        return self._request(
            {
                "kind": "hotkey",
                "display_id": display_id,
                "frame_id": frame["id"],
                "keys": values,
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def type_text(self, text: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
        if not text or len(text) > 4096 or "\0" in text:
            raise ValueError("Rust text input requires 1-4096 safe characters")
        display_id, foreground, frame = self._keyboard_input_context(status)
        return self._request(
            {
                "kind": "type_text",
                "display_id": display_id,
                "frame_id": frame["id"],
                "text": text,
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )


_CLIENT: RustDaemonClient | None = None
_CLIENT_CONFIG: RustEngineConfig | None = None
_CLIENT_GUARD = threading.Lock()


def _client() -> RustDaemonClient | None:
    global _CLIENT, _CLIENT_CONFIG
    config = RustEngineConfig.from_env()
    if config is None:
        return None
    with _CLIENT_GUARD:
        if _CLIENT is None or _CLIENT_CONFIG != config:
            if _CLIENT is not None:
                _CLIENT.close()
            _CLIENT_CONFIG = config
            _CLIENT = RustDaemonClient(config)
        return _CLIENT


def _preflight() -> tuple[RustDaemonClient | None, dict[str, Any] | None]:
    mode = native_engine_mode()
    client = _client()
    if client is None:
        return None, None
    try:
        status = client.status()
    except RustEngineUnavailable:
        if mode == "auto":
            return None, None
        raise
    if status.get("emergency_stopped") is True:
        raise RustEngineUnavailable("Rust daemon emergency stop is latched; restart the daemon before native execution")
    if (
        status.get("simulation") is True
        or status.get("native_input") is not True
        or status.get("capture_available") is False
    ):
        if mode == "auto":
            return None, None
        raise RustEngineUnavailable("Rust engine requires simulation=false, native_input=true, and native capture availability")
    if not client._displays(status):
        if mode == "auto":
            return None, None
        raise RustEngineUnavailable("Rust engine did not report any available displays")
    return client, status


def native_engine_status() -> str:
    mode = native_engine_mode()
    client = _client()
    if client is None:
        return json.dumps({"mode": mode, "backend": "python", "rust_configured": False}, ensure_ascii=False)
    try:
        status = client.status()
        displays = client._displays(status)
        capability_ready = all(
            int(row.get("id", -1)) in client.config.observe_capabilities
            and int(row.get("id", -1)) in client.config.input_capabilities
            for row in displays
        ) if displays else False
        input_ready = (
            status.get("simulation") is False
            and status.get("native_input") is True
            and status.get("capture_available") is not False
            and status.get("emergency_stopped") is not True
            and capability_ready
        )
        return json.dumps(
            {
                "mode": mode,
                "backend": "rust" if input_ready else ("python" if mode == "auto" else "rust"),
                "rust_configured": True,
                "rust_input_ready": input_ready,
                "daemon": status,
            },
            ensure_ascii=False,
        )
    except RustEngineUnavailable as exc:
        if mode == "auto":
            return json.dumps(
                {"mode": mode, "backend": "python", "rust_configured": True, "rust_available": False, "reason": str(exc)},
                ensure_ascii=False,
            )
        raise


def rust_engine_emergency_stop_best_effort() -> None:
    try:
        client = _client()
        if client is not None:
            client.emergency_stop()
    except Exception:
        pass


def register_rust_engine_tools(registry: ToolRegistry) -> None:
    """Route atomic desktop mutations through Rust in strict mode.

    Auto mode may use the Python implementation only before any Rust mutation was
    dispatched. Stateful cross-request key/mouse holds remain Python-only and are
    deliberately unavailable in strict Rust mode; use atomic hotkey/drag instead.
    """
    names = (
        "desktop_click",
        "desktop_type",
        "desktop_click_button",
        "desktop_move",
        "desktop_scroll",
        "desktop_double_click",
        "desktop_drag",
        "desktop_press",
        "desktop_hotkey",
        "desktop_mouse_down",
        "desktop_mouse_up",
        "desktop_key_down",
        "desktop_key_up",
    )
    originals = {name: registry._tools.get(name) for name in names}

    def fallback(name: str, **kwargs: Any) -> str:
        original = originals.get(name)
        if original is None:
            raise RuntimeError(f"Python fallback is unavailable for {name}")
        from .execution_telemetry import record_backend
        record_backend("python_native", detail="Compatibility input adapter")
        return original.handler(**kwargs)

    def run_atomic(name: str, invoke, **fallback_args: Any) -> str:
        client, status = _preflight()
        if client is None:
            return fallback(name, **fallback_args)
        try:
            result = invoke(client, status)
        except RustEngineUnavailable:
            if native_engine_mode() == "auto":
                return fallback(name, **fallback_args)
            raise
        return (
            "RUST_EXECUTED: native input dispatched with fresh frame + foreground binding; "
            "independent application verification required. "
            + json.dumps(result, ensure_ascii=False)
        )

    def desktop_click(x: int, y: int) -> str:
        return run_atomic(
            "desktop_click",
            lambda client, status: client.click(int(x), int(y), status),
            x=x,
            y=y,
        )

    def desktop_type(text: str) -> str:
        return run_atomic(
            "desktop_type",
            lambda client, status: client.type_text(text, status),
            text=text,
        )

    def desktop_click_button(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        normalized = str(button).strip().casefold()
        count = max(1, min(int(clicks), 3))
        return run_atomic(
            "desktop_click_button",
            lambda client, status: client.click_button(
                int(x), int(y), normalized, count, status
            ),
            x=x,
            y=y,
            button=button,
            clicks=count,
        )

    def desktop_double_click(x: int, y: int) -> str:
        return run_atomic(
            "desktop_double_click",
            lambda client, status: client.click_button(
                int(x), int(y), "left", 2, status
            ),
            x=x,
            y=y,
        )

    def desktop_move(x: int, y: int, duration: float | None = None) -> str:
        def invoke(client, status):
            seconds = float(duration) if duration is not None else 0.0
            if duration is None and "timed_pointer_move" in status.get("input_features", []):
                pointer = status.get("pointer")
                if isinstance(pointer, dict) and all(isinstance(pointer.get(key), int) for key in ("x", "y")):
                    try:
                        source = client._display_for_point(status, pointer["x"], pointer["y"])
                        target = client._display_for_point(status, int(x), int(y))
                        if source["id"] == target["id"]:
                            seconds = 0.08
                    except RustEngineUnavailable:
                        pass  # No known in-display path; the client still validates the destination.
            return client.pointer_move(int(x), int(y), status, duration=seconds)
        return run_atomic(
            "desktop_move",
            invoke,
            x=x,
            y=y,
            duration=0.08 if duration is None else duration,
        )

    def desktop_drag(
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.25,
        button: str = "left",
    ) -> str:
        return run_atomic(
            "desktop_drag",
            lambda client, status: client.drag(
                int(start_x),
                int(start_y),
                int(end_x),
                int(end_y),
                float(duration),
                str(button),
                status,
            ),
            start_x=start_x,
            start_y=start_y,
            end_x=end_x,
            end_y=end_y,
            duration=duration,
            button=button,
        )

    def desktop_scroll(clicks: int) -> str:
        return run_atomic(
            "desktop_scroll",
            lambda client, status: client.scroll(int(clicks), status),
            clicks=clicks,
        )

    def desktop_press(key: str) -> str:
        return run_atomic(
            "desktop_press",
            lambda client, status: client.press_key(str(key), status),
            key=key,
        )

    def desktop_hotkey(keys: list[str]) -> str:
        return run_atomic(
            "desktop_hotkey",
            lambda client, status: client.hotkey(list(keys), status),
            keys=keys,
        )

    def stateful_python_only(name: str, **kwargs: Any) -> str:
        if native_engine_mode() == "rust":
            raise RustEngineUnavailable(
                f"{name} is a stateful cross-request input primitive and is disabled in strict Rust mode; "
                "use desktop_hotkey or desktop_drag so the daemon can guarantee release on failure."
            )
        return fallback(name, **kwargs)

    def desktop_mouse_down(button: str = "left") -> str:
        return stateful_python_only("desktop_mouse_down", button=button)

    def desktop_mouse_up(button: str = "left") -> str:
        return stateful_python_only("desktop_mouse_up", button=button)

    def desktop_key_down(key: str) -> str:
        return stateful_python_only("desktop_key_down", key=key)

    def desktop_key_up(key: str) -> str:
        return stateful_python_only("desktop_key_up", key=key)

    handlers = {
        "desktop_click": desktop_click,
        "desktop_type": desktop_type,
        "desktop_click_button": desktop_click_button,
        "desktop_move": desktop_move,
        "desktop_scroll": desktop_scroll,
        "desktop_double_click": desktop_double_click,
        "desktop_drag": desktop_drag,
        "desktop_press": desktop_press,
        "desktop_hotkey": desktop_hotkey,
        "desktop_mouse_down": desktop_mouse_down,
        "desktop_mouse_up": desktop_mouse_up,
        "desktop_key_down": desktop_key_down,
        "desktop_key_up": desktop_key_up,
    }
    for name, handler in handlers.items():
        original = originals.get(name)
        if original is None:
            continue
        registry.register(
            ToolSpec(
                name=original.name,
                description=original.description
                + " In strict Rust mode, supported atomic input is dispatched through the "
                  "Rust execution daemon over loopback mTLS with fresh-frame and foreground binding.",
                risk=original.risk,
                input_schema=original.input_schema,
                handler=handler,
            )
        )

    registry.register(
        ToolSpec(
            "native_engine_status",
            "Report whether Full Access native input is currently routed through the Rust execution daemon or the Python fallback, including bounded daemon status metadata.",
            Risk.SAFE,
            {"type": "object", "properties": {}, "additionalProperties": False},
            native_engine_status,
        )
    )

