from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Callable

_cancelled: Callable[[], bool] = lambda: False
_active: set[subprocess.Popen] = set()
_lock = threading.Lock()


def set_cancellation(check: Callable[[], bool]) -> None:
    global _cancelled
    _cancelled = check


def check_cancelled() -> None:
    if _cancelled():
        raise RuntimeError("Emergency stop is active")


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Only PIDs created and retained by this module are accepted here.
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        process.kill()


def stop_processes() -> None:
    with _lock:
        processes = list(_active)
    for process in processes:
        try:
            _terminate(process)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def run_command(argv: list[str], cwd=None, timeout: float = 60) -> str:
    """Run explicit argv with bounded retained output and cancellation. No shell expansion."""
    if _cancelled():
        return "CANCELLED: Command was not started"
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    with _lock:
        _active.add(process)
    output = bytearray()
    overflow = threading.Event()

    def drain():
        assert process.stdout is not None
        total = 0
        while chunk := process.stdout.read(4096):
            total += len(chunk)
            output.extend(chunk)
            del output[:-16384]
            if total > 1024 * 1024:
                overflow.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    deadline = time.monotonic() + max(0.1, min(timeout, 300))
    failure = ""
    try:
        while process.poll() is None:
            if _cancelled() or overflow.is_set() or time.monotonic() >= deadline:
                failure = "CANCELLED: Command stopped" if _cancelled() else "ERROR: Command exceeded its output/time limit"
                _terminate(process)
                break
            time.sleep(0.02)
        process.wait(timeout=2)
        reader.join(timeout=1)
        text = bytes(output).decode("utf-8", errors="replace")
        return f"{failure}\nexit_code={process.returncode}\n{text}".lstrip()
    finally:
        if process.poll() is None:
            process.kill()
        with _lock:
            _active.discard(process)
        if not reader.is_alive() and process.stdout:
            process.stdout.close()
