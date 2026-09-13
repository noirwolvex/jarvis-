from __future__ import annotations

import base64
import hashlib
import json
import os
import time
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


def screen_observe(max_dimension: int = _MAX_DIMENSION, quality: int = 76) -> str:
    """Capture all visible monitors into one bounded JPEG for model visual understanding."""
    if os.name != "nt":
        raise RuntimeError("Visual screen observation is supported on Windows only")

    from PIL import ImageGrab

    image = ImageGrab.grab(all_screens=True)
    width, height = image.size
    target_width, target_height = _bounded_size(width, height, max_dimension)
    if (target_width, target_height) != (width, height):
        image = image.resize((target_width, target_height))
    if image.mode != "RGB":
        image = image.convert("RGB")

    out_dir = _workspace() / ".jarvis" / "vision"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"screen-{int(time.time() * 1000)}.jpg"
    bounded_quality = max(45, min(int(quality), 88))
    image.save(path, format="JPEG", quality=bounded_quality, optimize=True)
    size = path.stat().st_size
    if size < 1 or size > _MAX_FILE_BYTES:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise RuntimeError(f"Visual observation size is outside the allowed range: {size} bytes")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "path": str(path),
        "mime": "image/jpeg",
        "width": target_width,
        "height": target_height,
        "bytes": size,
        "sha256": digest,
        "captured_at_ms": int(time.time() * 1000),
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
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    f"{VISION_MARKER} Current Windows screen observation. "
                    "Use it only to understand visible UI state and coordinates needed for the user's current task. "
                    "Prefer semantic browser/UI automation when available; do not infer hidden or off-screen content."
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
            "Capture the current Windows desktop as a bounded visual observation and make the image available to the vision-capable model. Use when DOM/UI Automation metadata is insufficient, for visual layouts, unlabeled controls, canvas content, or coordinate decisions. Do not call repeatedly when semantic inspection is enough.",
            Risk.LOW,
            {
                "type": "object",
                "properties": {
                    "max_dimension": {"type": "integer", "minimum": 320, "maximum": 1920},
                    "quality": {"type": "integer", "minimum": 45, "maximum": 88},
                },
                "additionalProperties": False,
            },
            screen_observe,
        )
    )
