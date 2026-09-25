from __future__ import annotations

import os
from enum import IntEnum


class Risk(IntEnum):
    SAFE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


ACCESS_MODES = {"restricted", "standard", "full"}


class PermissionEngine:
    """Centralized policy gate for every local tool invocation."""

    def __init__(self) -> None:
        self._configured_require_approval = os.getenv("JARVIS_REQUIRE_APPROVAL", "true").lower() == "true"
        self._configured_allow_destructive = os.getenv("JARVIS_ALLOW_DESTRUCTIVE", "false").lower() == "true"
        self._configured_allow_shell = os.getenv("JARVIS_ALLOW_SHELL", "false").lower() == "true"
        self._configured_allow_network = os.getenv("JARVIS_ALLOW_NETWORK", "true").lower() == "true"
        self._configured_allow_git_write = os.getenv("JARVIS_ALLOW_GIT_WRITE", "true").lower() == "true"
        self._configured_allow_filesystem_write = os.getenv("JARVIS_ALLOW_FILESYSTEM_WRITE", "true").lower() == "true"
        self._configured_allow_browser_write = os.getenv("JARVIS_ALLOW_BROWSER_WRITE", "true").lower() == "true"
        self.full_access_require_approval = os.getenv("JARVIS_FULL_ACCESS_REQUIRE_APPROVAL", "true").lower() == "true"
        self.deny_tools = {
            item.strip() for item in os.getenv("JARVIS_DENY_TOOLS", "").split(",") if item.strip()
        }
        self.access_mode = "standard"
        self.set_access_mode(os.getenv("JARVIS_ACCESS_MODE", "standard"))

    def set_access_mode(self, mode: str) -> dict[str, object]:
        normalized = str(mode).strip().lower().replace("_", "-")
        if normalized == "full-access":
            normalized = "full"
        if normalized not in ACCESS_MODES:
            raise ValueError(f"Unknown access mode: {mode}")

        self.access_mode = normalized
        if normalized == "restricted":
            # Read/observe-oriented mode. Explicit deny-list remains authoritative.
            self.require_approval = True
            self.allow_destructive = False
            self.allow_shell = False
            self.allow_network = False
            self.allow_git_write = False
            self.allow_filesystem_write = False
            self.allow_browser_write = False
        elif normalized == "full":
            # Full Access expands capability categories but does not bypass deny_tools,
            # workspace path checks, browser challenge guards, or other hard boundaries.
            self.require_approval = self.full_access_require_approval
            self.allow_destructive = True
            self.allow_shell = True
            self.allow_network = True
            self.allow_git_write = True
            self.allow_filesystem_write = True
            self.allow_browser_write = True
        else:
            self.require_approval = self._configured_require_approval
            self.allow_destructive = self._configured_allow_destructive
            self.allow_shell = self._configured_allow_shell
            self.allow_network = self._configured_allow_network
            self.allow_git_write = self._configured_allow_git_write
            self.allow_filesystem_write = self._configured_allow_filesystem_write
            self.allow_browser_write = self._configured_allow_browser_write
        return self.summary()

    def summary(self) -> dict[str, object]:
        return {
            "mode": self.access_mode,
            "require_approval": self.require_approval,
            "allow_destructive": self.allow_destructive,
            "allow_shell": self.allow_shell,
            "allow_network": self.allow_network,
            "allow_git_write": self.allow_git_write,
            "allow_filesystem_write": self.allow_filesystem_write,
            "allow_browser_write": self.allow_browser_write,
            "denied_tools": sorted(self.deny_tools),
        }

    def _category_allowed(self, tool_name: str) -> tuple[bool, str]:
        if tool_name in self.deny_tools:
            return False, f"Tool {tool_name} is explicitly denied by policy."
        if self.access_mode == "restricted" and (
            tool_name.startswith("desktop_") and tool_name != "desktop_cursor"
            or tool_name.startswith(("ui_", "discord_", "youtube_", "workflow_", "interaction_")) and tool_name not in {"ui_inspect", "ui_resolve", "ui_wait_state", "interaction_inspect", "workflow_review", "workflow_status"}
            or tool_name in {"open_application", "open_application_and_type", "launch_installed_app", "focus_window", "focus_window_advanced", "close_window", "vscode_open"}
        ):
            return False, "Desktop interaction is disabled in restricted mode."
        if tool_name == "run_powershell" and not self.allow_shell:
            return False, "PowerShell execution is disabled by policy."
        if tool_name.startswith("git_") and tool_name in {"git_add", "git_commit", "git_push", "git_checkout", "git_pull", "git_merge", "git_rebase", "git_branch"}:
            if not self.allow_git_write:
                return False, "Git write operations are disabled by policy."
        if tool_name in {"write_file", "notepad_save_as", "file_copy", "directory_create"} and not self.allow_filesystem_write:
            return False, "Filesystem writes are disabled by policy."
        if tool_name.startswith("youtube_") and (not self.allow_browser_write or not self.allow_network):
            return False, "YouTube interaction is disabled by browser/network policy."
        if tool_name.startswith("discord_") and not self.allow_network:
            return False, "Discord interaction is disabled by network policy."
        if tool_name.startswith(("browser_", "chrome_", "dialog_")) and tool_name not in {
            "browser_read_page", "browser_links", "browser_page_state", "browser_check_challenge", "browser_screenshot", "browser_wait", "browser_semantic_snapshot", "browser_wait_state",
            "chrome_tabs", "chrome_current_tab", "chrome_is_connected", "dialog_inspect",
        } and not self.allow_browser_write:
            return False, "Browser write interactions are disabled by policy."
        if tool_name in {"open_url", "google_search", "browser_navigate", "chrome_new_tab", "chrome_connect_cdp", "chrome_start_managed"} and not self.allow_network:
            return False, "Network/browser access is disabled by policy."
        return True, "allowed"

    def check(self, tool_name: str, risk: Risk, approved: bool = False) -> tuple[bool, str]:
        allowed, reason = self._category_allowed(tool_name)
        if not allowed:
            return False, reason

        # Arbitrary shell execution is high-impact regardless of how a registry
        # entry was historically classified.
        effective_risk = max(risk, Risk.HIGH) if tool_name == "run_powershell" else risk

        if effective_risk >= Risk.HIGH and not self.allow_destructive:
            return False, f"Blocked: {tool_name} is classified as {effective_risk.name.lower()} and destructive access is disabled."
        if effective_risk >= Risk.MEDIUM and self.require_approval and not approved:
            return False, f"Approval required before running {tool_name}."
        return True, "approved"
