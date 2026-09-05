from __future__ import annotations

import os
from enum import IntEnum


class Risk(IntEnum):
    SAFE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class PermissionEngine:
    """Centralized policy gate for every local tool invocation."""

    def __init__(self) -> None:
        self.require_approval = os.getenv("JARVIS_REQUIRE_APPROVAL", "true").lower() == "true"
        self.allow_destructive = os.getenv("JARVIS_ALLOW_DESTRUCTIVE", "false").lower() == "true"

    def check(self, tool_name: str, risk: Risk, approved: bool = False) -> tuple[bool, str]:
        if risk >= Risk.HIGH and not self.allow_destructive:
            return False, f"Blocked: {tool_name} is classified as {risk.name.lower()} and destructive access is disabled."
        if risk >= Risk.MEDIUM and self.require_approval and not approved:
            return False, f"Approval required before running {tool_name}."
        return True, "approved"
