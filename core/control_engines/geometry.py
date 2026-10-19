"""Explicit physical-pixel transforms; no primary-monitor or DPI assumptions."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self):
        if any(isinstance(v, bool) or not math.isfinite(v) for v in (self.left, self.top, self.right, self.bottom)):
            raise ValueError("Rectangle coordinates must be finite")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Rectangle must have positive dimensions")

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def center(self, margin: float = 0) -> tuple[int, int]:
        if not math.isfinite(margin) or margin < 0 or 2 * margin >= min(self.right - self.left, self.bottom - self.top):
            raise ValueError("Safe margin leaves no interior target")
        x, y = math.floor((self.left + self.right) / 2), math.floor((self.top + self.bottom) / 2)
        if not self.contains(x, y) or not (self.left + margin <= x < self.right - margin and self.top + margin <= y < self.bottom - margin):
            raise ValueError("No safely inset physical pixel exists")
        return x, y


@dataclass(frozen=True)
class CoordinateMap:
    """Image/window-local pixels to a physical desktop rectangle, including negative origins."""
    bounds: Rect
    image_width: int
    image_height: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.image_width, self.image_height)):
            raise ValueError("Image dimensions must be positive integers")

    def point(self, x: float, y: float) -> tuple[int, int]:
        if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < self.image_width and 0 <= y < self.image_height):
            raise ValueError("Point lies outside the observed image")
        return (math.floor(self.bounds.left + x * (self.bounds.right - self.bounds.left) / self.image_width),
                math.floor(self.bounds.top + y * (self.bounds.bottom - self.bounds.top) / self.image_height))

    def rectangle(self, rect: Rect) -> Rect:
        if not (0 <= rect.left < rect.right <= self.image_width and 0 <= rect.top < rect.bottom <= self.image_height):
            raise ValueError("Target rectangle lies outside the observed image")
        sx = (self.bounds.right - self.bounds.left) / self.image_width
        sy = (self.bounds.bottom - self.bounds.top) / self.image_height
        return Rect(self.bounds.left + rect.left * sx, self.bounds.top + rect.top * sy,
                    self.bounds.left + rect.right * sx, self.bounds.top + rect.bottom * sy)


@dataclass(frozen=True)
class VisualTarget:
    description: str
    bounds: Rect
    observation_id: str
    observed_at: float
    window_hwnd: int
    confidence: float | None
    method: str
    text: str = ""
    coordinate_system: str = "physical_desktop"

    def validate(self, *, observation_id: str, window_hwnd: int, max_age: float = 2,
                 minimum_confidence: float = 0.85) -> None:
        age = time.monotonic() - self.observed_at
        if (not self.observation_id or self.observation_id != observation_id
                or self.window_hwnd <= 0 or self.window_hwnd != window_hwnd
                or not math.isfinite(age) or not 0 <= age <= max_age):
            raise ValueError("Visual target is stale or belongs to another window; observe again")
        if self.confidence is None or not math.isfinite(self.confidence) or not minimum_confidence <= self.confidence <= 1:
            raise ValueError("Visual target confidence is unavailable or insufficient; use semantic resolution")


def relative_point(x: int, y: int, dx: int, dy: int, displays: list[Rect]) -> tuple[int, int]:
    """Reject monitor gaps as well as points outside the virtual desktop."""
    if any(type(v) is not int for v in (x, y, dx, dy)):
        raise ValueError("Pointer offsets require physical integer pixels")
    point = x + dx, y + dy
    if not any(display.contains(*point) for display in displays):
        raise ValueError("Pointer destination is outside every observed display")
    return point
