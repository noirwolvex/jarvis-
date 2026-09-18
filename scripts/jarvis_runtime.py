from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = ROOT / "daemon" / "rust" / "Cargo.toml"
RUNTIME_ROOT = ROOT / ".jarvis" / "rust-runtime"
DASHBOARD_URL = "http://127.0.0.1:3000/"

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


def _run_checked(command: list[str], env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}")


def _daemon_executable() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return ROOT / "daemon" / "rust" / "target" / "debug" / f"jarvis-daemon{suffix}"


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
    _run_checked(
        [
            cargo,
            "build",
            "--quiet",
            "--locked",
            "--manifest-path",
            str(RUST_MANIFEST),
            "--features",
            "native",
            "--bin",
            "jarvis-daemon",
        ]
    )
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
            with urllib.request.urlopen(DASHBOARD_URL, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    print(f"JARVIS_DASHBOARD_READY {DASHBOARD_URL} status={response.status}", flush=True)
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Dashboard did not become reachable: {last_error}")


def run_dashboard(kind: str) -> int:
    daemon: subprocess.Popen[Any] | None = None
    dashboard: subprocess.Popen[Any] | None = None
    daemon_log = None
    try:
        daemon, daemon_log, _session, env, _engine = bootstrap()
        npm = _tool("npm.cmd" if os.name == "nt" else "npm")
        script = "dev:dashboard" if kind == "dev" else "start:dashboard"
        dashboard = subprocess.Popen([npm, "run", script], cwd=ROOT, env=env)
        _wait_dashboard(dashboard)
        print("JARVIS_RUNTIME_RUST_ACTIVE Python worker missions are configured fail-closed through Rust.", flush=True)
        return dashboard.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        _stop_process(dashboard)
        _stop_process(daemon)
        if daemon_log is not None:
            daemon_log.close()


def _read_worker_message(process: subprocess.Popen[str], request_id: str | None = None) -> dict[str, Any]:
    assert process.stdout is not None
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"Python worker exited with code {process.returncode}: {stderr[-2000:]}")
        line = process.stdout.readline()
        if not line:
            time.sleep(0.02)
            continue
        value = json.loads(line)
        if request_id is None and value.get("type") == "ready":
            return value
        if request_id is not None and value.get("type") == "result" and value.get("id") == request_id:
            return value
    raise RuntimeError("Timed out waiting for Python worker protocol response")


def _worker_probe(process: subprocess.Popen[str], probe: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    request_id = uuid.uuid4().hex
    process.stdin.write(json.dumps({"protocol": 1, "id": request_id, "action": "native_input_probe", "probe": probe}) + "\n")
    process.stdin.flush()
    result = _read_worker_message(process, request_id)
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
    daemon: subprocess.Popen[Any] | None = None
    worker: subprocess.Popen[str] | None = None
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
        ready = _read_worker_message(worker)
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

            _worker_probe(worker, {"kind": "click", "x": ex, "y": ey})
            root.update()
            if root.focus_get() is not entry:
                raise RuntimeError(
                    f"Independent mouse verification failed on display {display_id}: Rust click did not focus the target entry"
                )

            token = f"RUST-LIVE-{display_id}-{uuid.uuid4().hex[:8]}"
            _worker_probe(worker, {"kind": "type_text", "text": token})
            root.update()
            if entry.get() != token:
                raise RuntimeError(
                    f"Independent keyboard verification failed on display {display_id}: expected {token!r}, got {entry.get()!r}"
                )

            bx = button.winfo_rootx() + button.winfo_width() // 2
            by = button.winfo_rooty() + button.winfo_height() // 2
            _worker_probe(worker, {"kind": "click", "x": bx, "y": by})
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
            _stop_process(worker)
        _stop_process(daemon)
        if daemon_log is not None:
            daemon_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start JARVIS X with strict Rust native execution")
    parser.add_argument("command", choices=("dev", "start", "verify-live"))
    args = parser.parse_args()
    if args.command == "verify-live":
        return live_qualify()
    return run_dashboard(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
