"""Offline screenshot-encoding benchmark. Uses synthetic images; never reads the desktop."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import statistics
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw


def run(iterations: int = 30) -> dict:
    image = Image.new("RGB", (1280, 720), "#151923")
    draw = ImageDraw.Draw(image)
    for row in range(30):
        y = 12 + row * 23
        draw.rectangle((12, y, 240, y + 18), fill="#252d40")
        draw.text((20, y + 2), f"Application navigation item {row}", fill="white")
        draw.text((260, y + 2), f"Editor line {row}: deterministic input with exact result verification", fill="#8ab4f8")
    result = {"fixture": "synthetic 1280x720 interface; no OS capture or model calls", "iterations": iterations}
    with tempfile.TemporaryDirectory(prefix="jarvis-encoding-bench-") as directory:
        path = Path(directory) / "frame.jpg"
        for label, optimized in [("previous_optimized_disk_readback", True), ("current_single_encode", False)]:
            durations = []
            for index in range(iterations + 3):
                started = time.perf_counter()
                if optimized:
                    image.save(path, format="JPEG", quality=76, optimize=True)
                    raw = path.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                else:
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=76)
                    raw = buffer.getvalue()
                    digest = hashlib.sha256(raw).hexdigest()
                    path.write_bytes(raw)
                duration = (time.perf_counter() - started) * 1000
                if index >= 3:
                    durations.append(duration)
            assert len(digest) == 64
            ordered = sorted(durations)
            result[label] = {"median_ms": round(statistics.median(durations), 3),
                             "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 3),
                             "jpeg_bytes": len(raw)}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.iterations <= 1000:
        parser.error("iterations must be between 5 and 1000")
    print(json.dumps(run(args.iterations), indent=2))
