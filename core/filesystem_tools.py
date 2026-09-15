from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .dev_tools import _workspace_path
from .permissions import Risk
from .tools import ToolSpec


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def file_copy(source: str, destination: str) -> str:
    src, dst = _workspace_path(source), _workspace_path(destination)
    if not src.is_file() or src.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Copy requires a file no larger than 16 MiB")
    data = src.read_bytes()
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Source grew beyond the copy limit")
    # Exclusive creation preserves existing destinations, including a race after path checks.
    with dst.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    expected = hashlib.sha256(data).hexdigest()
    if _digest(dst) != expected:
        raise RuntimeError("Copy read-back did not match; inspect destination before retrying")
    return "VERIFIED: " + json.dumps({"destination": str(dst), "bytes": len(data), "sha256": expected})


def directory_create(path: str) -> str:
    target = _workspace_path(path)
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise RuntimeError("Directory was not created")
    return "VERIFIED: " + json.dumps({"directory": str(target)})


def register_filesystem_tools(registry) -> None:
    registry.register(ToolSpec("file_copy", "Copy one workspace file to a new destination and verify its SHA-256. Existing destinations are never overwritten.", Risk.MEDIUM,
        {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}, "required": ["source", "destination"], "additionalProperties": False}, file_copy))
    registry.register(ToolSpec("directory_create", "Create a directory within the configured workspace and verify it exists.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}, directory_create))
