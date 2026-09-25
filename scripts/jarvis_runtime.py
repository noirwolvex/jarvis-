from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = ROOT / "daemon" / "rust" / "Cargo.toml"
RUNTIME_ROOT = ROOT / ".jarvis" / "rust-runtime"
DASHBOARD_URL = "http://127.0.0.1:3000/"
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 3000

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("The JARVIS Rust native-input runtime is Windows-only")


def _tool(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f"Required executable is not on PATH: {name}")
    return value


@contextmanager
def _runtime_lock():
    """Serialize launch and shutdown, including verify-live, across processes."""
    RUNTIME_ROOT.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: another process may already hold its open inode.
    with (RUNTIME_ROOT.parent / "runtime.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "Another JARVIS runtime is already starting or running. "
                "Use its dashboard, or stop it with Ctrl+C before starting another instance. "
                "The existing Rust daemon was not changed."
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _assert_dashboard_port_available() -> None:
    """Fail before provisioning or replacing Rust when an older server is listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((DASHBOARD_HOST, DASHBOARD_PORT))
    except OSError as exc:
        raise RuntimeError(
            f"Dashboard address {DASHBOARD_HOST}:{DASHBOARD_PORT} is unavailable or already in use. "
            f"Check the existing server at {DASHBOARD_URL} and stop it before restarting. "
            "The existing Rust daemon was not changed."
        ) from exc


def _dashboard_command(kind: str) -> list[str]:
    if kind not in {"dev", "start"}:
        raise ValueError("Dashboard mode must be dev or start")
    node = _tool("node")
    candidates = (ROOT / "node_modules" / "next" / "dist" / "bin" / "next",
                  ROOT / "apps" / "control-center" / "node_modules" / "next" / "dist" / "bin" / "next")
    cli = next((path for path in candidates if path.is_file()), None)
    if cli is None:
        raise RuntimeError("Next.js is not installed; run npm ci from the JARVIS repository first")
    # Pass argv directly to Node. No cmd.exe/npm.cmd shell or unquoted executable path.
    return [node, str(cli), kind, "--hostname", DASHBOARD_HOST, "--port", str(DASHBOARD_PORT)]


def _dashboard_owns_port(process: subprocess.Popen[Any]) -> bool:
    import psutil
    try:
        parent = psutil.Process(process.pid)
        owned = {parent.pid, *(child.pid for child in parent.children(recursive=True))}
        return any(connection.pid in owned and connection.status == psutil.CONN_LISTEN
                   and connection.laddr.port == DASHBOARD_PORT
                   and connection.laddr.ip in {DASHBOARD_HOST, "0.0.0.0"}
                   for connection in psutil.net_connections(kind="tcp"))
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied as exc:
        raise RuntimeError("Cannot verify ownership of the dashboard listener") from exc


def _stop_dashboard(process: subprocess.Popen[Any] | None) -> None:
    """Retire only the owned Next.js tree, including dev-server and Python children."""
    if process is None:
        return
    import psutil
    try:
        parent = psutil.Process(process.pid)
        # Avoid a recycled PID if the original child has already exited.
        if process.poll() is not None:
            return
        owned = list(reversed(parent.children(recursive=True))) + [parent]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=3)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if alive:
        _, alive = psutil.wait_procs(alive, timeout=3)
        if alive:
            raise RuntimeError("Could not stop the owned dashboard process tree")
    process.wait(timeout=3)


def _run_checked(command: list[str], env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")


def _rust_profile() -> str:
    profile = os.getenv("JARVIS_RUST_PROFILE", "release").strip().lower()
    if profile not in {"release", "debug"}:
        raise ValueError("JARVIS_RUST_PROFILE must be release or debug")
    return profile


def _daemon_build_command(cargo: str) -> list[str]:
    return [cargo, "build", "--quiet", "--locked", "--manifest-path", str(RUST_MANIFEST),
            "--features", "native", "--bin", "jarvis-daemon",
            *(["--release"] if _rust_profile() == "release" else [])]


def _daemon_executable() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return ROOT / "daemon" / "rust" / "target" / _rust_profile() / f"jarvis-daemon{suffix}"


def _stop_stale_project_daemons(executable: Path) -> list[int]:
    """Stop only stale jarvis-daemon processes launched from this repository binary."""
    if os.name != "nt":
        return []

    try:
        import psutil
    except Exception as exc:
        raise RuntimeError(
            "psutil is required to safely stop a stale JARVIS Rust daemon before rebuild"
        ) from exc

    target = os.path.normcase(os.path.abspath(str(executable)))
    current_pid = os.getpid()
    matches = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pid = int(process.info.get("pid") or 0)
            if pid <= 0 or pid == current_pid:
                continue
            exe = process.info.get("exe")
            if not isinstance(exe, str) or not exe:
                continue
            if os.path.normcase(os.path.abspath(exe)) == target:
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            continue

    if not matches:
        return []

    stopped: list[int] = []
    for process in matches:
        try:
            stopped.append(int(process.pid))
            process.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise RuntimeError(
                f"Cannot stop stale Rust daemon pid={process.pid} at {target}. "
                "Close the existing JARVIS session or run the terminal with sufficient permissions."
            ) from exc

    _, alive = psutil.wait_procs(matches, timeout=2.5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise RuntimeError(
                f"Cannot kill stale Rust daemon pid={process.pid} at {target}. "
                "Close the existing JARVIS session or run the terminal with sufficient permissions."
            ) from exc

    if alive:
        _, still_alive = psutil.wait_procs(alive, timeout=2.5)
        if still_alive:
            pids = ", ".join(str(process.pid) for process in still_alive)
            raise RuntimeError(
                f"Rust daemon executable is still locked by pid(s): {pids}. "
                "Close the existing JARVIS session before rebuilding."
            )

    # Windows can keep the image section mapped for a very short interval after exit.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        locked = False
        for process in psutil.process_iter(["exe"]):
            try:
                exe = process.info.get("exe")
                if isinstance(exe, str) and exe and os.path.normcase(os.path.abspath(exe)) == target:
                    locked = True
                    break
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
        if not locked:
            break
        time.sleep(0.05)

    print(
        "STOPPED_STALE_RUST_DAEMON "
        + json.dumps({"pids": stopped, "executable": str(executable)}, ensure_ascii=False),
        flush=True,
    )
    return stopped


def _start_daemon(executable: Path, config_path: Path, log_path: Path) -> tuple[subprocess.Popen[Any], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        [str(executable), str(config_path)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return process, log


def _stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _tls_status(session_dir: Path, process: subprocess.Popen[Any], timeout_s: float = 20.0) -> dict[str, Any]:
    from core.rust_engine import RustDaemonClient, RustEngineConfig

    config = RustEngineConfig(
        host="127.0.0.1",
        port=7443,
        server_name="localhost",
        ca_path=session_dir / "ca.pem",
        client_cert_path=session_dir / "client.pem",
        client_key_path=session_dir / "client-key.pem",
        observe_capabilities={0: "dev-observe"},
        input_capabilities={0: "dev-input"},
    )
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Rust daemon exited during startup with code {process.returncode}")
        client = RustDaemonClient(config)
        try:
            return client.status()
        except Exception as exc:  # daemon may still be binding/listening
            last_error = exc
            time.sleep(0.1)
        finally:
            client.close()
    raise RuntimeError(f"Rust daemon did not become ready: {last_error}")


def _rewrite_capabilities(config_path: Path, display_ids: list[int]) -> tuple[dict[str, str], dict[str, str]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    capabilities = config.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise RuntimeError("Generated Rust config contains no capabilities")
    template = capabilities[0]
    peer = template.get("peer_sha256")
    expiry = template.get("expires_at_ms")
    if not isinstance(peer, str) or len(peer) != 64 or not isinstance(expiry, int):
        raise RuntimeError("Generated Rust capability identity is invalid")

    observe = {str(display_id): f"runtime-observe-{display_id}" for display_id in display_ids}
    input_caps = {str(display_id): f"runtime-input-{display_id}" for display_id in display_ids}
    config["capabilities"] = [
        *[
            {
                "id": observe[str(display_id)],
                "peer_sha256": peer,
                "expires_at_ms": expiry,
                "scope": {"kind": "observe", "display_id": display_id},
            }
            for display_id in display_ids
        ],
        *[
            {
                "id": input_caps[str(display_id)],
                "peer_sha256": peer,
                "expires_at_ms": expiry,
                "scope": {"kind": "input", "display_id": display_id},
            }
            for display_id in display_ids
        ],
    ]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return observe, input_caps


def _runtime_env(session_dir: Path, observe: dict[str, str], input_caps: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "JARVIS_REPO_ROOT": str(ROOT),
            "JARVIS_CONTROL_MODE": "hybrid",
            # Fresh on every managed runtime start. It is consumed only by the Node
            # control plane to authenticate a browser session and is never written to disk.
            "JARVIS_CONTROL_PAIRING_TOKEN": secrets.token_urlsafe(32),
            "JARVIS_NATIVE_ENGINE": "rust",
            "JARVIS_DAEMON_HOST": "127.0.0.1",
            "JARVIS_DAEMON_PORT": "7443",
            "JARVIS_DAEMON_SERVER_NAME": "localhost",
            "JARVIS_DAEMON_CA": str(session_dir / "ca.pem"),
            "JARVIS_DAEMON_CLIENT_CERT": str(session_dir / "client.pem"),
            "JARVIS_DAEMON_CLIENT_KEY": str(session_dir / "client-key.pem"),
            "JARVIS_DAEMON_OBSERVE_CAPABILITY": "",
            "JARVIS_DAEMON_INPUT_CAPABILITY": "",
            "JARVIS_DAEMON_OBSERVE_CAPABILITIES_JSON": json.dumps(observe, separators=(",", ":")),
            "JARVIS_DAEMON_INPUT_CAPABILITIES_JSON": json.dumps(input_caps, separators=(",", ":")),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _strict_engine_status(env: dict[str, str]) -> dict[str, Any]:
    previous = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        from core import rust_engine

        with rust_engine._CLIENT_GUARD:
            if rust_engine._CLIENT is not None:
                rust_engine._CLIENT.close()
            rust_engine._CLIENT = None
            rust_engine._CLIENT_CONFIG = None
        status = json.loads(rust_engine.native_engine_status())
    finally:
        os.environ.clear()
        os.environ.update(previous)
    if status.get("backend") != "rust" or status.get("rust_input_ready") is not True:
        raise RuntimeError(f"Strict Rust engine preflight failed: {json.dumps(status, ensure_ascii=False)}")
    return status


def bootstrap() -> tuple[subprocess.Popen[Any], Any, Path, dict[str, str], dict[str, Any]]:
    _require_windows()
    _rust_profile()  # Reject invalid configuration before provisioning or stopping anything.
    cargo = _tool("cargo")
    session_dir = RUNTIME_ROOT / f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    session_dir.parent.mkdir(parents=True, exist_ok=True)

    _run_checked(
        [
            cargo,
            "run",
            "--quiet",
            "--manifest-path",
            str(RUST_MANIFEST),
            "--example",
            "dev_pki",
            "--",
            str(session_dir),
            "--native",
        ]
    )

    # A previous dev/verify session may have left this exact executable running.
    # Windows locks loaded .exe images, so cargo cannot replace jarvis-daemon.exe
    # until that process exits. Stop only the daemon whose executable path matches
    # this repository; never use a broad taskkill by image name.
    _stop_stale_project_daemons(_daemon_executable())

    _run_checked(_daemon_build_command(cargo))
    executable = _daemon_executable()
    if not executable.is_file():
        raise RuntimeError(f"Rust daemon executable was not produced: {executable}")

    config_path = session_dir / "config.json"
    first, first_log = _start_daemon(executable, config_path, session_dir / "daemon-discovery.log")
    try:
        status = _tls_status(session_dir, first)
    finally:
        _stop_process(first)
        first_log.close()

    displays = status.get("displays")
    if not isinstance(displays, list) or not displays:
        raise RuntimeError(f"Rust daemon reported no displays: {status}")
    display_ids = sorted(
        {
            int(row["id"])
            for row in displays
            if isinstance(row, dict) and isinstance(row.get("id"), int) and int(row["id"]) >= 0
        }
    )
    if not display_ids:
        raise RuntimeError(f"Rust daemon reported no usable display ids: {status}")

    observe, input_caps = _rewrite_capabilities(config_path, display_ids)
    daemon, daemon_log = _start_daemon(executable, config_path, session_dir / "daemon.log")
    try:
        _tls_status(session_dir, daemon)
        env = _runtime_env(session_dir, observe, input_caps)
        engine = _strict_engine_status(env)
    except Exception:
        _stop_process(daemon)
        daemon_log.close()
        raise

    print(
        "RUST_DAEMON_READY "
        + json.dumps(
            {
                "pid": daemon.pid,
                "display_ids": display_ids,
                "backend": engine.get("backend"),
                "rust_input_ready": engine.get("rust_input_ready"),
                "session": str(session_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return daemon, daemon_log, session_dir, env, engine


def _wait_dashboard(process: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 60.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Dashboard exited during startup with code {process.returncode}")
        try:
            if _dashboard_owns_port(process):
                with urllib.request.urlopen(DASHBOARD_URL, timeout=1.0) as response:
                    if response.status == 200 and process.poll() is None and _dashboard_owns_port(process):
                        print(f"JARVIS_DASHBOARD_READY {DASHBOARD_URL} status={response.status}", flush=True)
                        return
        except OSError as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"Dashboard did not become reachable: {last_error}")


def _open_paired_dashboard(env: dict[str, str]) -> None:
    token = env.get("JARVIS_CONTROL_PAIRING_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError("Managed Control Center pairing token is unavailable")
    pair_url = DASHBOARD_URL + "api/pair?token=" + urllib.parse.quote(token, safe="")
    # The secret is sent directly to the local browser but never printed to stdout/stderr.
    # /api/pair immediately redirects to / and stores only a signed HttpOnly session cookie.
    if not webbrowser.open(pair_url, new=2):
        raise RuntimeError("Could not open the authenticated Control Center in the default browser")
    print("JARVIS_CONTROL_SESSION_OPENED Authenticated local browser session requested.", flush=True)


def run_dashboard(kind: str) -> int:
    _require_windows()
    command = _dashboard_command(kind)
    with _runtime_lock():
        _assert_dashboard_port_available()
        return _run_dashboard(command)


def _run_dashboard(command: list[str]) -> int:
    daemon: subprocess.Popen[Any] | None = None
    dashboard: subprocess.Popen[Any] | None = None
    daemon_log = None
    try:
        daemon, daemon_log, _session, env, _engine = bootstrap()
        dashboard = subprocess.Popen(command, cwd=ROOT / "apps" / "control-center", env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _wait_dashboard(dashboard)
        _open_paired_dashboard(env)
        print("JARVIS_RUNTIME_RUST_ACTIVE Python worker missions are configured fail-closed through Rust.", flush=True)
        return dashboard.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            _stop_dashboard(dashboard)
        finally:
            _stop_process(daemon)
            if daemon_log is not None:
                daemon_log.close()


class _WorkerProtocolReader:
    """Drain owned diagnostic-worker pipes without blocking the response deadline."""

    MAX_LINE = 2 * 1024 * 1024

    def __init__(self, process: subprocess.Popen[str]):
        if process.stdout is None or process.stderr is None:
            raise ValueError("Diagnostic worker requires stdout and stderr pipes")
        self.process = process
        self._lines: queue.Queue[str] = queue.Queue(maxsize=8)
        self._closed = threading.Event()
        self._stdout_done = threading.Event()
        self._lock = threading.Lock()
        self._failure = ""
        self._stderr = ""
        self._threads = [
            threading.Thread(target=self._read_stdout, name="jarvis-probe-stdout", daemon=True),
            threading.Thread(target=self._read_stderr, name="jarvis-probe-stderr", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _fail(self, message: str) -> None:
        with self._lock:
            if not self._failure:
                self._failure = message

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            while not self._closed.is_set():
                line = self.process.stdout.readline(self.MAX_LINE + 1)
                if not line:
                    return
                if len(line) > self.MAX_LINE:
                    self._fail("Python worker protocol line exceeded its safety limit")
                    return
                try:
                    self._lines.put_nowait(line)
                except queue.Full:
                    self._fail("Python worker protocol queue exceeded its safety limit")
                    return
        except (OSError, ValueError) as exc:
            if not self._closed.is_set():
                self._fail(f"Python worker output pipe failed: {exc}")
        finally:
            self._stdout_done.set()

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            while not self._closed.is_set():
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    return
                with self._lock:
                    self._stderr = (self._stderr + chunk)[-8192:]
        except (OSError, ValueError) as exc:
            if not self._closed.is_set():
                self._fail(f"Python worker error pipe failed: {exc}")

    def read_message(self, request_id: str | None = None, timeout_s: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                failure = self._failure
            if failure:
                raise RuntimeError(failure)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Timed out waiting for Python worker protocol response")
            try:
                line = self._lines.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                if self._stdout_done.is_set() or self._closed.is_set():
                    self._threads[1].join(timeout=0.1)
                    with self._lock:
                        detail = self._stderr[-2000:]
                    raise RuntimeError(f"Python worker protocol closed (code={self.process.poll()}): {detail}")
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Python worker returned invalid protocol JSON") from exc
            if not isinstance(value, dict):
                raise RuntimeError("Python worker returned a non-object protocol message")
            if request_id is None and value.get("type") == "ready":
                return value
            if request_id is not None and value.get("type") == "result" and value.get("id") == request_id:
                return value

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        # Only this diagnostic child is owned here. Stopping it releases blocked pipe
        # reads; close handles after the reader threads finish using them.
        _stop_process(self.process)
        for thread in self._threads:
            thread.join(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def _read_worker_message(reader: _WorkerProtocolReader, request_id: str | None = None) -> dict[str, Any]:
    return reader.read_message(request_id)


def _worker_probe(process: subprocess.Popen[str], reader: _WorkerProtocolReader, probe: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    request_id = uuid.uuid4().hex
    process.stdin.write(json.dumps({"protocol": 1, "id": request_id, "action": "native_input_probe", "probe": probe}) + "\n")
    process.stdin.flush()
    result = _read_worker_message(reader, request_id)
    if result.get("ok") is not True:
        raise RuntimeError(f"Python worker native probe failed: {result.get('error')}")
    payload = result.get("payload")
    if not isinstance(payload, dict) or payload.get("backend") != "rust":
        raise RuntimeError(f"Python worker did not report Rust execution: {result}")
    rust_result = payload.get("result")
    if not isinstance(rust_result, dict) or rust_result.get("executed") is not True or rust_result.get("simulation") is not False:
        raise RuntimeError(f"Rust daemon did not confirm native execution: {result}")
    return result


def live_qualify() -> int:
    _require_windows()
    with _runtime_lock():
        _assert_dashboard_port_available()
        return _live_qualify()


def _live_qualify() -> int:
    daemon: subprocess.Popen[Any] | None = None
    worker: subprocess.Popen[str] | None = None
    worker_reader: _WorkerProtocolReader | None = None
    daemon_log = None
    try:
        daemon, daemon_log, _session, env, engine = bootstrap()
        env["JARVIS_RUST_LIVE_PROBE"] = "1"
        python = env.get("JARVIS_PYTHON_EXECUTABLE", "python").strip() or "python"
        worker = subprocess.Popen(
            [python, "-u", "-m", "core.full_access_worker"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        worker_reader = _WorkerProtocolReader(worker)
        ready = _read_worker_message(worker_reader)
        native = ready.get("native_engine")
        if not isinstance(native, dict) or native.get("backend") != "rust" or native.get("rust_input_ready") is not True:
            raise RuntimeError(f"Running Python worker is not connected to Rust: {ready}")

        import ctypes
        import tkinter as tk

        # Tk and JARVIS coordinate evidence must describe the same physical-pixel virtual
        # desktop. This call must happen before creating the qualification window.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass

        displays = engine.get("daemon", {}).get("displays")
        if not isinstance(displays, list) or not displays:
            raise RuntimeError(f"Rust daemon exposed no displays for live qualification: {engine}")

        root = tk.Tk()
        root.title(f"JARVIS Rust Live Qualification {uuid.uuid4().hex[:6]}")
        root.geometry("640x300")
        root.attributes("-topmost", True)
        tk.Label(root, text="JARVIS X — supervised Rust native input qualification", font=("Segoe UI", 13)).pack(pady=(24, 14))
        entry = tk.Entry(root, width=48, font=("Consolas", 12))
        entry.pack(pady=10)
        clicked = {"value": False}

        def mark_clicked() -> None:
            clicked["value"] = True

        button = tk.Button(root, text="Rust mouse target", command=mark_clicked, width=24)
        button.pack(pady=18)
        root.update()

        user32 = ctypes.windll.user32
        GA_ROOT = 2
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040
        raw_hwnd = int(root.winfo_id())
        root_hwnd = int(user32.GetAncestor(raw_hwnd, GA_ROOT) or raw_hwnd)
        qualified: list[dict[str, Any]] = []

        for row in displays:
            if not isinstance(row, dict):
                continue
            try:
                display_id = int(row["id"])
                left, top = int(row["x"]), int(row["y"])
                width, height = int(row["width"]), int(row["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if width < 200 or height < 160:
                continue

            window_width = min(640, max(180, width - 40))
            window_height = min(300, max(140, height - 40))
            target_x = left + max(10, (width - window_width) // 2)
            target_y = top + max(10, (height - window_height) // 2)
            moved = user32.SetWindowPos(
                root_hwnd,
                0,
                target_x,
                target_y,
                window_width,
                window_height,
                SWP_NOZORDER | SWP_SHOWWINDOW,
            )
            if not moved:
                raise RuntimeError(f"Could not move live qualification window to display {display_id}")
            root.lift()
            root.focus_force()
            root.update()
            time.sleep(0.12)
            root.update()

            entry.delete(0, tk.END)
            clicked["value"] = False
            ex = entry.winfo_rootx() + entry.winfo_width() // 2
            ey = entry.winfo_rooty() + entry.winfo_height() // 2
            if not (left <= ex < left + width and top <= ey < top + height):
                raise RuntimeError(
                    f"Qualification target ({ex},{ey}) is not inside display {display_id} "
                    f"({left},{top},{width},{height})"
                )

            _worker_probe(worker, worker_reader, {"kind": "click", "x": ex, "y": ey})
            root.update()
            if root.focus_get() is not entry:
                raise RuntimeError(
                    f"Independent mouse verification failed on display {display_id}: Rust click did not focus the target entry"
                )

            token = f"RUST-LIVE-{display_id}-{uuid.uuid4().hex[:8]}"
            _worker_probe(worker, worker_reader, {"kind": "type_text", "text": token})
            root.update()
            if entry.get() != token:
                raise RuntimeError(
                    f"Independent keyboard verification failed on display {display_id}: expected {token!r}, got {entry.get()!r}"
                )

            # Qualify real Virtual-Key delivery, not only Unicode text injection.
            # Ctrl+A must select the existing token; the replacement proves that the
            # modifier + letter shortcut was delivered atomically by the Rust daemon.
            replacement = f"HOTKEY-{display_id}-{uuid.uuid4().hex[:8]}"
            _worker_probe(worker, worker_reader, {"kind": "hotkey", "keys": ["ctrl", "a"]})
            _worker_probe(worker, worker_reader, {"kind": "type_text", "text": replacement})
            root.update()
            if entry.get() != replacement:
                raise RuntimeError(
                    f"Independent hotkey verification failed on display {display_id}: "
                    f"Ctrl+A did not select the prior text; got {entry.get()!r}"
                )

            # A standalone navigation key must also arrive through the VK path.
            prefix = "VK-"
            _worker_probe(worker, worker_reader, {"kind": "press_key", "key": "home"})
            _worker_probe(worker, worker_reader, {"kind": "type_text", "text": prefix})
            root.update()
            if entry.get() != prefix + replacement:
                raise RuntimeError(
                    f"Independent key verification failed on display {display_id}: "
                    f"Home did not move the caret to the start; got {entry.get()!r}"
                )

            bx = button.winfo_rootx() + button.winfo_width() // 2
            by = button.winfo_rooty() + button.winfo_height() // 2
            _worker_probe(worker, worker_reader, {"kind": "click", "x": bx, "y": by})
            root.update()
            if not clicked["value"]:
                raise RuntimeError(
                    f"Independent mouse verification failed on display {display_id}: Rust click did not invoke the target button"
                )
            qualified.append({"id": display_id, "x": left, "y": top, "width": width, "height": height})

        if len(qualified) != len([row for row in displays if isinstance(row, dict)]):
            raise RuntimeError(
                f"Not every reported display was qualified: qualified={qualified}, reported={displays}"
            )

        print(
            "LIVE_RUST_QUALIFICATION_OK "
            + json.dumps(
                {
                    "worker_backend": native.get("backend"),
                    "rust_input_ready": native.get("rust_input_ready"),
                    "daemon_native_input": engine.get("daemon", {}).get("native_input"),
                    "keyboard_token_verified": True,
                    "keyboard_hotkey_verified": True,
                    "keyboard_virtual_key_verified": True,
                    "mouse_focus_verified": True,
                    "mouse_button_verified": True,
                    "qualified_displays": qualified,
                }
            ),
            flush=True,
        )
        root.destroy()
        return 0
    finally:
        if worker is not None:
            try:
                if worker.stdin:
                    worker.stdin.write(json.dumps({"protocol": 1, "action": "stop"}) + "\n")
                    worker.stdin.flush()
            except Exception:
                pass
            if worker_reader is not None:
                worker_reader.close()
            else:
                _stop_process(worker)
        _stop_process(daemon)
        if daemon_log is not None:
            daemon_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start JARVIS X with strict Rust native execution")
    parser.add_argument("command", choices=("dev", "start", "verify-live"))
    args = parser.parse_args()
    try:
        if args.command == "verify-live":
            return live_qualify()
        return run_dashboard(args.command)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"JARVIS_STARTUP_ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
