from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

VISION_MARKER = "[JARVIS_SCREEN_OBSERVATION]"
_MAX_DIMENSION = 1280
_MAX_FILE_BYTES = 1_500_000


def _workspace() -> Path:
    return Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()


def _bounded_size(width: int, height: int, max_dimension: int = _MAX_DIMENSION) -> tuple[int, int]:
    if width < 1 or height < 1:
        raise ValueError("Screen dimensions must be positive")
    limit = max(320, min(int(max_dimension), 1920))
    longest = max(width, height)
    if longest <= limit:
        return width, height
    scale = limit / float(longest)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _virtual_origin() -> tuple[int, int]:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return 0, 0
    user32 = ctypes.windll.user32
    # SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN. ImageGrab(all_screens=True) follows this desktop origin.
    return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))


def screen_observe(max_dimension: int = _MAX_DIMENSION, quality: int = 76, settle_ms: int = 300) -> str:
    """Capture all visible monitors into one bounded JPEG for model visual understanding."""
    if os.name != "nt":
        raise RuntimeError("Visual screen observation is supported on Windows only")

    from PIL import ImageGrab, ImageChops, ImageStat
    from .desktop_observation import foreground_identity, remember_signature

    # Keep capture and input in physical pixels across monitors with different DPI.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass
    foreground = foreground_identity()
    capture_started = time.monotonic()
    image = ImageGrab.grab(all_screens=True)
    stable = False
    deadline = capture_started + max(60, min(settle_ms, 1000)) / 1000
    previous = image.resize((96, 54)).convert("RGB")
    while time.monotonic() < deadline:
        time.sleep(0.06)
        image = ImageGrab.grab(all_screens=True)
        current = image.resize((96, 54)).convert("RGB")
        difference = sum(ImageStat.Stat(ImageChops.difference(previous, current)).mean) / 3
        if difference <= 1.5:
            stable = True
            break
        previous = current
    if foreground != foreground_identity():
        raise RuntimeError("Foreground changed during capture; observe again")
    source_width, source_height = image.size
    source_signature = image.resize((192, 108)).convert("RGB")
    origin_x, origin_y = _virtual_origin()
    target_width, target_height = _bounded_size(source_width, source_height, max_dimension)
    if (target_width, target_height) != (source_width, source_height):
        image = image.resize((target_width, target_height))
    if image.mode != "RGB":
        image = image.convert("RGB")

    out_dir = _workspace() / ".jarvis" / "vision"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"screen-{uuid.uuid4().hex}.jpg"
    bounded_quality = max(45, min(int(quality), 88))
    image.save(path, format="JPEG", quality=bounded_quality, optimize=True)
    size = path.stat().st_size
    if size < 1 or size > _MAX_FILE_BYTES:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise RuntimeError(f"Visual observation size is outside the allowed range: {size} bytes")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    remember_signature(digest, source_signature)
    # Bounded disk retention, including long missions. Never follow arbitrary file names.
    for old in sorted(out_dir.glob("screen-*.jpg"), key=lambda item: item.stat().st_mtime, reverse=True)[8:]:
        old.unlink(missing_ok=True)
    payload = {
        "path": str(path),
        "mime": "image/jpeg",
        "width": target_width,
        "height": target_height,
        "source_width": source_width,
        "source_height": source_height,
        "virtual_origin_x": origin_x,
        "virtual_origin_y": origin_y,
        "desktop_scale_x": source_width / float(target_width),
        "desktop_scale_y": source_height / float(target_height),
        "bytes": size,
        "sha256": digest,
        "captured_at_ms": int(time.time() * 1000),
        "foreground_hwnd": foreground,
        "stable": stable,
        "scene_bound": True,
        "capture_ms": round((time.monotonic() - capture_started) * 1000, 2),
    }
    return "VERIFIED: " + json.dumps(payload, ensure_ascii=False)


def _payload_from_result(result: str) -> dict[str, Any] | None:
    prefix = "VERIFIED: "
    if not str(result).startswith(prefix):
        return None
    try:
        payload = json.loads(str(result)[len(prefix):])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        return None
    return payload


def vision_followup_message(result: str) -> dict[str, Any] | None:
    """Build an in-memory multimodal message without placing image bytes in tool output or memory."""
    payload = _payload_from_result(result)
    if payload is None:
        return None
    path = Path(str(payload["path"])).resolve()
    vision_root = (_workspace() / ".jarvis" / "vision").resolve()
    if vision_root != path.parent or not path.is_file():
        return None
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_FILE_BYTES:
        return None
    if payload.get("sha256") and hashlib.sha256(raw).hexdigest() != payload["sha256"]:
        return None
    encoded = base64.b64encode(raw).decode("ascii")

    width = int(payload.get("width") or 0)
    height = int(payload.get("height") or 0)
    source_width = int(payload.get("source_width") or width)
    source_height = int(payload.get("source_height") or height)
    origin_x = int(payload.get("virtual_origin_x") or 0)
    origin_y = int(payload.get("virtual_origin_y") or 0)
    scale_x = float(payload.get("desktop_scale_x") or 1.0)
    scale_y = float(payload.get("desktop_scale_y") or 1.0)

    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    f"{VISION_MARKER} Current Windows virtual-desktop observation. "
                    f"The supplied image is {width}x{height}; it represents a {source_width}x{source_height} desktop "
                    f"whose origin is ({origin_x},{origin_y}). If you must translate an image pixel (ix,iy) to desktop "
                    f"coordinates, use x={origin_x}+ix*{scale_x:.6f} and y={origin_y}+iy*{scale_y:.6f}. "
                    "Use the image only to understand visible UI state. Prefer semantic browser/UI Automation when available, "
                    "and do not infer hidden or off-screen content."
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            },
        ],
    }


def is_internal_vision_message(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    first = content[0]
    return isinstance(first, dict) and str(first.get("text") or "").startswith(VISION_MARKER)


def register_vision_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "screen_observe",
            "Capture the current Windows virtual desktop as a bounded visual observation and make the image available to the vision-capable model, including exact virtual-desktop coordinate mapping. Use when DOM/UI Automation metadata is insufficient, for visual layouts, unlabeled controls, canvas content, or coordinate decisions. Do not call repeatedly when semantic inspection is enough.",
            Risk.LOW,
            {
                "type": "object",
                "properties": {
                    "max_dimension": {"type": "integer", "minimum": 320, "maximum": 1920},
                    "quality": {"type": "integer", "minimum": 45, "maximum": 88},
                    "settle_ms": {"type": "integer", "minimum": 60, "maximum": 1000},
                },
                "additionalProperties": False,
            },
            screen_observe,
        )
    )
