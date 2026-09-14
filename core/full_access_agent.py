from __future__ import annotations

import ctypes
import json
import os
import time
from typing import Any, Callable

from .agent import AgentEvent, JarvisAgent, _tool_schemas
from .browser_mission_contract import (
    google_search_result_count,
    minimum_tab_count,
    required_google_searches,
    required_new_tabs,
)
from .vision_tools import is_internal_vision_message, vision_followup_message

_DESKTOP_BROWSER_INPUT_TOOLS = {
    "desktop_click",
    "desktop_type",
    "desktop_press",
    "desktop_hotkey",
    "desktop_scroll",
    "desktop_double_click",
    "desktop_click_button",
    "desktop_drag",
    "desktop_mouse_down",
    "desktop_mouse_up",
    "desktop_key_down",
    "desktop_key_up",
}
_BROWSER_SIGNALS = (
    "browser", "chrome", "google", "website", "web page", "http://", "https://", "new tab", "another tab", "search for",
    "متصفح", "كروم", "جوجل", "موقع", "صفحة", "تبويب", "ابحث", "بحث",
)
_FULL_TOOL_SIGNALS = (
    " git", "git ", "github", "repo", "repository", "code", "coding", "vscode", "visual studio code",
    "file", "folder", "directory", "terminal", "powershell", "command", "script", "npm ", "cargo ", "database", "supabase",
    "ملف", "مجلد", "كود", "جيت", "قاعدة بيانات",
)
_BROWSER_PROFILE_EXPLICIT = {
    "google_search",
    "screen_observe",
    "wait",
    "take_screenshot",
    "list_windows",
    "focus_window",
    "focus_window_advanced",
    "inspect_window",
    "close_window",
    "find_installed_app",
    "launch_installed_app",
    "open_application",
    "open_application_and_type",
    "open_url",
}
_BROWSER_PROFILE_PREFIXES = ("browser_", "chrome_", "task_", "dialog_", "desktop_")


def _chrome_tab_rows() -> list[dict]:
    try:
        from .chrome_cdp import chrome_is_connected, chrome_tabs

        if not chrome_is_connected():
            return []
        payload = json.loads(chrome_tabs())
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _foreground_is_chrome() -> bool:
    """Return true only when a Chrome/Chromium process owns the current foreground window."""
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return False
    try:
        from .chrome_cdp import chrome_is_connected

        if not chrome_is_connected():
            return False
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        import psutil

        name = psutil.Process(int(pid.value)).name().casefold()
        return name in {"chrome.exe", "chromium.exe", "chrome", "chromium"}
    except Exception:
        return False


def _browser_focused_goal(user_text: str) -> bool:
    lowered = str(user_text or "").casefold()
    return any(signal in lowered for signal in _BROWSER_SIGNALS)


def _needs_full_toolset(user_text: str) -> bool:
    lowered = " " + str(user_text or "").casefold() + " "
    return any(signal in lowered for signal in _FULL_TOOL_SIGNALS)


class FullAccessJarvisAgent(JarvisAgent):
    """Full Access execution profile with hard browser-completion, vision, and human-verification boundaries."""

    def _system_prompt(self, user_text: str = "") -> str:
        base = super()._system_prompt(user_text)
        return base + """

Full Access execution profile:
- Optimize for low latency and verified completion. Prefer one reliable semantic/direct tool over several exploratory mouse or keyboard steps when both achieve the same requested result.
- For an explicit Google search, prefer google_search. It performs a guarded verified search in one call. Set new_tab=true only when the user explicitly asks for a new/additional tab.
- For browser, tab, Google, or web-search requests, do not launch Chrome through launch_installed_app or open_application. Use the guarded managed Chrome/CDP tools so JARVIS controls the exact selected tab.
- If the user explicitly says "new tab" or "another tab", that is a structural requirement: use chrome_new_tab or google_search(new_tab=true) for that step. browser_navigate/open_url on the current tab does NOT satisfy a new-tab request.
- Preserve earlier result tabs when the user asks for a later search in a new tab. Complete every clause in order before returning a final answer.
- For desktop applications, prefer semantic Windows UI Automation (inspect_window/dialog tools) when controls are labeled. Use screen_observe when the task genuinely depends on visual layout, canvas content, unlabeled controls, or coordinates that semantic inspection cannot resolve. A screen_observe result is supplied to you as an actual image on the next turn with virtual-desktop coordinate mapping.
- Do not take repeated screenshots when the visible state has not materially changed. After a visual action, verify the resulting state with semantic inspection or a fresh visual observation when needed.
- General desktop mouse/keyboard tools are also protected by the browser challenge guard whenever Chrome is the foreground window. Never use desktop input as an alternate path around a CAPTCHA or human-verification checkpoint.
- If a guarded browser action encounters CAPTCHA, anti-bot, or human verification, stop the current run immediately. Leave the current Chrome window and tab open and unchanged. Do not close it, switch away, navigate elsewhere, launch another browser, or attempt an alternate automation path. The user must complete that checkpoint manually before JARVIS continues.
"""

    def _tool_schemas_for_goal(self, user_text: str) -> list[dict[str, Any]]:
        schemas = _tool_schemas(self.tools)
        if not _browser_focused_goal(user_text) or _needs_full_toolset(user_text):
            return schemas
        # Browser-heavy missions do not need dev/Git/filesystem/system schemas on every
        # model turn. Keep browser + task + window/desktop/dialog/vision/app fallback tools.
        # This only changes what the model sees; PermissionEngine remains authoritative.
        return [
            schema for schema in schemas
            if (
                str(schema.get("function", {}).get("name", "")) in _BROWSER_PROFILE_EXPLICIT
                or str(schema.get("function", {}).get("name", "")).startswith(_BROWSER_PROFILE_PREFIXES)
            )
        ]

    def _browser_action_guard(self, tool_name: str) -> str | None:
        # Native/desktop input must not become a side channel around the DOM/CDP CAPTCHA guard.
        if tool_name in _DESKTOP_BROWSER_INPUT_TOOLS and _foreground_is_chrome():
            return super()._browser_action_guard("browser_click")
        return super()._browser_action_guard(tool_name)

    def _replace_internal_vision(self, followup: dict[str, Any]) -> None:
        # Keep only the newest screenshot in the live model context. Image bytes never enter
        # MemoryStore or tool traces, which keeps subsequent turns fast and bounded.
        self.messages = [
            message for message in self.messages
            if not (isinstance(message, dict) and is_internal_vision_message(message))
        ]
        self.messages.append(followup)

    def run(self, user_text: str, emit: Callable[[AgentEvent], None] | None = None) -> str:
        self.orchestrator.begin(user_text)
        self.workspace_context.save_snapshot()
        self.messages.append({"role": "user", "content": user_text})
        self.memory.add("user", user_text)

        requested_new_tab_count = required_new_tabs(user_text)
        requested_google_search_count = required_google_searches(user_text)
        initial_tab_count = len(_chrome_tab_rows())
        minimum_required_tabs = minimum_tab_count(initial_tab_count, requested_new_tab_count)
        turn_tool_schemas = self._tool_schemas_for_goal(user_text)

        try:
            for turn in range(self.max_turns):
                self.orchestrator.start_turn(turn + 1)
                emit and emit(AgentEvent("status", f"Thinking… (turn {turn + 1})"))
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": self._system_prompt(user_text)}, *self.messages],
                    tools=turn_tool_schemas,
                    tool_choice="auto",
                )
                message = response.choices[0].message
                self.messages.append(message.model_dump(exclude_none=True))
                tool_calls = getattr(message, "tool_calls", None) or []
                if not tool_calls:
                    rows = _chrome_tab_rows()
                    actual_tab_count = len(rows)
                    actual_google_search_count = google_search_result_count(rows)

                    unmet: list[str] = []
                    if requested_new_tab_count and actual_tab_count < minimum_required_tabs:
                        unmet.append(
                            f"the user requested {requested_new_tab_count} additional browser tab(s), "
                            f"but only {actual_tab_count} tab(s) are currently verified; "
                            f"at least {minimum_required_tabs} are required"
                        )
                    if (
                        requested_google_search_count
                        and actual_google_search_count < requested_google_search_count
                    ):
                        unmet.append(
                            f"the user requested {requested_google_search_count} Google search(es), "
                            f"but only {actual_google_search_count} Google search-result tab(s) are verified"
                        )

                    if unmet:
                        contract_hint = (
                            "The original browser mission is not complete: "
                            + "; ".join(unmet)
                            + ". Continue executing the original request. For every explicit new-tab step, "
                            "use chrome_new_tab or google_search(new_tab=true), keep previous result tabs open, "
                            "perform the requested search/action inside the newly selected tab, and verify the resulting tab/URL before finishing."
                        )
                        emit and emit(AgentEvent("status", contract_hint))
                        self.messages.append({"role": "user", "content": contract_hint})
                        continue

                    result = (message.content or "Done.").strip()
                    if self.orchestrator.current and self.orchestrator.current.plan:
                        pending = [
                            step for step in self.orchestrator.current.plan
                            if step.status not in {"completed", "skipped"}
                        ]
                        if pending:
                            result = "I stopped before all planned steps were completed: " + ", ".join(step.id for step in pending)
                            self.orchestrator.finish("incomplete", result)
                            self.memory.add("assistant", result)
                            return result
                    self.memory.add("assistant", result)
                    self.orchestrator.finish("completed", result)
                    return result

                vision_followups: list[dict[str, Any]] = []
                for call in tool_calls:
                    name = call.function.name
                    started = time.perf_counter()
                    challenge_pause = False
                    try:
                        arguments = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError as exc:
                        result = f"ERROR: invalid tool arguments for {name}: {exc}"
                        arguments = {}
                    else:
                        emit and emit(AgentEvent("tool", f"Requesting tool: {name}", name))
                        guard_result = self._browser_action_guard(name)
                        if guard_result:
                            result = guard_result
                            challenge_pause = result.startswith("BROWSER_ACTION_BLOCKED:")
                        else:
                            approved = self.approval(name, arguments)
                            result = self._execute_tool(name, arguments, approved=approved)
                            # Fast-path navigation tools can detect a challenge after navigation.
                            challenge_pause = str(result).startswith("BROWSER_ACTION_BLOCKED:")

                    duration_ms = (time.perf_counter() - started) * 1000.0
                    self.orchestrator.record_tool(name, arguments, result, duration_ms, turn + 1)
                    emit and emit(AgentEvent("tool_result", result, name))
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

                    if name == "screen_observe":
                        followup = vision_followup_message(result)
                        if followup is not None:
                            vision_followups.append(followup)

                    if challenge_pause:
                        pause_result = (
                            "Human verification is required in the current browser tab. "
                            "JARVIS paused and left the page open without closing, switching, navigating, or interacting with the challenge. "
                            "Complete the CAPTCHA or human-verification step manually, then ask JARVIS to continue."
                        )
                        emit and emit(AgentEvent("status", pause_result, name))
                        self.memory.add("assistant", pause_result)
                        self.orchestrator.finish("waiting_user", pause_result)
                        return pause_result

                    hint = self.orchestrator.recovery_hint(result, name)
                    if hint:
                        emit and emit(AgentEvent("status", hint, name))
                        self.messages.append({"role": "user", "content": hint})

                # Append multimodal context only after every tool_call has a matching tool result,
                # preserving the Chat Completions tool-call protocol for parallel tool responses.
                if vision_followups:
                    self._replace_internal_vision(vision_followups[-1])

            result = "I reached the execution limit before the requested outcome was verified."
            self.memory.add("assistant", result)
            self.orchestrator.finish("incomplete", result)
            return result
        except Exception as exc:
            result = f"ERROR: {type(exc).__name__}: {exc}"
            self.memory.add("assistant", result)
            self.orchestrator.finish("failed", result)
            raise
