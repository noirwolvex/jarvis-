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
        self.allow_shell = os.getenv("JARVIS_ALLOW_SHELL", "false").lower() == "true"
        self.allow_network = os.getenv("JARVIS_ALLOW_NETWORK", "true").lower() == "true"
        self.allow_git_write = os.getenv("JARVIS_ALLOW_GIT_WRITE", "true").lower() == "true"
        self.allow_filesystem_write = os.getenv("JARVIS_ALLOW_FILESYSTEM_WRITE", "true").lower() == "true"
        self.allow_browser_write = os.getenv("JARVIS_ALLOW_BROWSER_WRITE", "true").lower() == "true"
        self.deny_tools = {
            item.strip() for item in os.getenv("JARVIS_DENY_TOOLS", "").split(",") if item.strip()
        }

    def _category_allowed(self, tool_name: str) -> tuple[bool, str]:
        if tool_name in self.deny_tools:
            return False, f"Tool {tool_name} is explicitly denied by policy."
        if tool_name == "run_powershell" and not self.allow_shell:
            return False, "PowerShell execution is disabled by policy. Set JARVIS_ALLOW_SHELL=true to enable it explicitly."
        if tool_name.startswith("git_") and tool_name in {"git_add", "git_commit", "git_push", "git_checkout", "git_pull", "git_merge", "git_rebase"}:
            if not self.allow_git_write:
                return False, "Git write operations are disabled by policy."
        if tool_name in {"write_file", "notepad_save_as"} and not self.allow_filesystem_write:
            return False, "Filesystem writes are disabled by policy."
        if tool_name in {"browser_click", "browser_type", "browser_press"} and not self.allow_browser_write:
            return False, "Browser write interactions are disabled by policy."
        if tool_name in {"open_url", "browser_navigate", "chrome_connect_cdp", "chrome_start_managed"} and not self.allow_network:
            return False, "Network/browser access is disabled by policy."
        return True, "allowed"

    def check(self, tool_name: str, risk: Risk, approved: bool = False) -> tuple[bool, str]:
        allowed, reason = self._category_allowed(tool_name)
        if not allowed:
            return False, reason

        # Arbitrary shell execution can mutate the whole machine even though the
        # registry historically classified it as MEDIUM. Treat it as HIGH at the
        # policy boundary so destructive access must be explicitly enabled.
        effective_risk = max(risk, Risk.HIGH) if tool_name == "run_powershell" else risk

        if effective_risk >= Risk.HIGH and not self.allow_destructive:
            return False, f"Blocked: {tool_name} is classified as {effective_risk.name.lower()} and destructive access is disabled."
        if effective_risk >= Risk.MEDIUM and self.require_approval and not approved:
            return False, f"Approval required before running {tool_name}."
        return True, "approved"
