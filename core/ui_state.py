"""Small, cancellation-aware helpers shared by semantic desktop workflows."""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, TypeVar

from .process_control import check_cancelled

T = TypeVar("T")


def wait_until(probe: Callable[[], T], timeout: float = 1.5, interval: float = 0.04,
               description: str = "requested UI state") -> T:
    """Poll observed state, returning immediately when satisfied. Never retries actions."""
    deadline = time.monotonic() + max(0.0, min(float(timeout), 15.0))
    while True:
        check_cancelled()
        result = probe()
        check_cancelled()
        if result:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {description}; inspect before retrying an action")
        time.sleep(min(max(float(interval), 0.01), 0.05, remaining))


def cancellable_delay(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, min(float(seconds), 5.0))
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


class SnapshotCache:
    """Cache detached read metadata only; never retains actionable UIA elements."""

    def __init__(self, ttl: float = 0.25, capacity: int = 8) -> None:
        self.ttl = ttl
        self.capacity = capacity
        self._rows: OrderedDict[Any, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.generation = 0

    def invalidate(self, hwnd: int | None = None) -> None:
        with self._lock:
            self.generation += 1
            if hwnd is None:
                self._rows.clear()
            else:
                for key in list(self._rows):
                    if isinstance(key, tuple) and key[0] == hwnd:
                        self._rows.pop(key, None)

    def get(self, key: Any) -> dict | None:
        with self._lock:
            item = self._rows.get(key)
            if item is None:
                return None
            created, data = item
            age = time.monotonic() - created
            if age > self.ttl:
                self._rows.pop(key, None)
                return None
            self._rows.move_to_end(key)
            result = copy.deepcopy(data)
            result.update(cached=True, cache_age_ms=round(age * 1000, 2))
            return result

    def put(self, key: Any, data: dict, generation: int) -> None:
        with self._lock:
            if generation != self.generation:
                return  # A mutation happened while this snapshot was being collected.
            self._rows[key] = (time.monotonic(), copy.deepcopy(data))
            self._rows.move_to_end(key)
            while len(self._rows) > self.capacity:
                self._rows.popitem(last=False)
