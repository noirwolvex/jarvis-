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

    def close(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
            self._session = ""
            self._sequence = 0
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
            sent = False
            try:
                self._socket.sendall(struct.pack(">I", len(encoded)) + encoded)
                sent = True
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
                if mutating and sent:
                    raise RustEngineExecutionError(
                        "Rust engine connection failed after dispatch; action outcome is uncertain. Re-observe before retrying."
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
        process_id, title = foreground.get("process_id"), foreground.get("title")
        if not isinstance(process_id, int) or process_id < 1 or not isinstance(title, str) or not title.strip():
            raise RustEngineUnavailable("Rust daemon returned an invalid foreground binding")
        return {"process_id": process_id, "title": title}

    def click(self, x: int, y: int, status: dict[str, Any] | None = None) -> dict[str, Any]:
        state = status or self.status()
        display = self._display_for_point(state, int(x), int(y))
        display_id = int(display["id"])
        foreground = self._foreground(state)
        capture = self.capture(display_id)
        frame = capture.get("frame")
        if not isinstance(frame, dict) or not isinstance(frame.get("id"), str):
            raise RustEngineExecutionError("Rust daemon capture did not return a usable frame")
        return self._request(
            {
                "kind": "click",
                "display_id": display_id,
                "frame_id": frame["id"],
                "x": int(x),
                "y": int(y),
                "foreground": foreground,
            },
            self.config.capability("input", display_id),
            mutating=True,
        )

    def type_text(self, text: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
        if not text or len(text) > 4096 or "\0" in text:
            raise ValueError("Rust text input requires 1-4096 safe characters")
        state = status or self.status()
        displays = self._displays(state)
        if not displays:
            raise RustEngineUnavailable("Rust daemon did not report any capturable display")
        display_id = int(displays[0].get("id", -1))
        if display_id < 0:
            raise RustEngineUnavailable("Rust daemon returned an invalid display id")
        foreground = self._foreground(state)
        capture = self.capture(display_id)
        frame = capture.get("frame")
        if not isinstance(frame, dict) or not isinstance(frame.get("id"), str):
            raise RustEngineExecutionError("Rust daemon capture did not return a usable frame")
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
    if status.get("simulation") is True or status.get("native_input") is not True:
        if mode == "auto":
            return None, None
        raise RustEngineUnavailable("Rust engine requires simulation=false and native_input=true")
    return client, status


def native_engine_status() -> str:
    mode = native_engine_mode()
    client = _client()
    if client is None:
        return json.dumps({"mode": mode, "backend": "python", "rust_configured": False}, ensure_ascii=False)
    try:
        status = client.status()
        return json.dumps(
            {"mode": mode, "backend": "rust", "rust_configured": True, "daemon": status},
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
    """Overlay supported native mutations with Rust while preserving safe Python fallback in auto mode."""
    originals = {name: registry._tools.get(name) for name in ("desktop_click", "desktop_type", "desktop_click_button")}

    def desktop_click(x: int, y: int) -> str:
        client, status = _preflight()
        original = originals["desktop_click"]
        if client is None:
            if original is None:
                raise RuntimeError("Python desktop click fallback is unavailable")
            return original.handler(x=x, y=y)
        result = client.click(int(x), int(y), status)
        return "RUST_EXECUTED: native click dispatched with fresh frame + foreground binding; independent verification required. " + json.dumps(result, ensure_ascii=False)

    def desktop_type(text: str) -> str:
        client, status = _preflight()
        original = originals["desktop_type"]
        if client is None:
            if original is None:
                raise RuntimeError("Python desktop type fallback is unavailable")
            return original.handler(text=text)
        result = client.type_text(text, status)
        return "RUST_EXECUTED: native text input dispatched with fresh frame + foreground binding; independent verification required. " + json.dumps(result, ensure_ascii=False)

    def desktop_click_button(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        normalized = str(button).strip().lower()
        count = max(1, min(int(clicks), 3))
        original = originals["desktop_click_button"]
        if normalized != "left":
            if native_engine_mode() == "rust":
                raise RustEngineUnavailable("Rust engine currently accepts left-button desktop clicks only")
            if original is None:
                raise RuntimeError("Python desktop button fallback is unavailable")
            return original.handler(x=x, y=y, button=button, clicks=count)

        client, status = _preflight()
        if client is None:
            if original is None:
                raise RuntimeError("Python desktop button fallback is unavailable")
            return original.handler(x=x, y=y, button=button, clicks=count)
        results: list[dict[str, Any]] = []
        for index in range(count):
            # Re-observe Rust state between repeated clicks. Never replay against stale evidence.
            current = status if index == 0 else client.status()
            results.append(client.click(int(x), int(y), current))
        return "RUST_EXECUTED: native left click(s) dispatched with per-action frame binding; independent verification required. " + json.dumps(results, ensure_ascii=False)

    for name, handler in (("desktop_click", desktop_click), ("desktop_type", desktop_type), ("desktop_click_button", desktop_click_button)):
        original = originals[name]
        if original is None:
            continue
        registry.register(
            ToolSpec(
                name=original.name,
                description=original.description + " When JARVIS_NATIVE_ENGINE=rust (or auto with a healthy daemon), the mutation is dispatched through the Rust execution daemon over loopback mTLS.",
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
