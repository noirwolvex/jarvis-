from __future__ import annotations

import ctypes
import json
import os
import time
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from .agent import AgentEvent, JarvisAgent, _tool_schemas
from .browser_mission_contract import (
    google_search_result_count,
    minimum_tab_count,
    required_google_searches,
    required_new_tabs,
)
from .vision_tools import is_internal_vision_message, vision_followup_message
from .desktop_observation import DesktopObservationGate
from .orchestrator import tool_succeeded
from .permissions import Risk

_DESKTOP_BROWSER_INPUT_TOOLS = {
    "dialog_set_field",
    "dialog_click_button",
    "dialog_save_file",
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
_DESKTOP_OBSERVATION_REQUIRED_TOOLS = {
    "desktop_click",
    "desktop_click_button",
    "desktop_double_click",
    "desktop_drag",
    "desktop_move",
}
_FOCUSED_NATIVE_BURST_TOOLS = {
    "desktop_type",
    "desktop_press",
    "desktop_hotkey",
    "desktop_scroll",
}
_DESKTOP_SCENE_MUTATION_TOOLS = {
    "desktop_click",
    "desktop_click_button",
    "desktop_double_click",
    "desktop_drag",
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
_BROWSER_PROFILE_PREFIXES = ("browser_", "chrome_", "task_", "dialog_", "desktop_", "ui_", "discord_", "whatsapp_", "youtube_", "workflow_")


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_turns = max(1, min(int(os.getenv("JARVIS_FULL_ACCESS_MAX_TURNS", str(max(160, self.max_turns)))), 512))
        self._stop = threading.Event()
        self._device_action_active = threading.Event()
        self._desktop_observation = DesktopObservationGate()
        self.orchestrator.strict_order = True

    def request_stop(self) -> None:
        self._stop.set()

    def _is_stopped(self) -> bool:
        external = getattr(self, "cancel_event", None)
        return self._stop.is_set() or bool(external and external.is_set())

    def reset(self) -> None:
        super().reset()
        self._stop.clear()
        self._device_action_active.clear()
        self._desktop_observation.invalidate()

    def _compact_context(self, goal: str) -> None:
        # Trim only at assistant boundaries so retained tool calls always keep their results.
        starts = [index for index, item in enumerate(self.messages) if item.get("role") == "assistant"]
        if len(starts) > 12:
            self.messages = [{"role": "user", "content": goal}, *self.messages[starts[-12]:]]

    def _is_mutation(self, name: str) -> bool:
        # Containers journal their child actions; they are not additional device actions.
        # Moving the pointer alone changes no application state and must not create an
        # expensive verification barrier before the click it is preparing.
        if name.startswith("workflow_") or name in {"desktop_cursor", "desktop_move"}:
            return False
        spec = self.tools._tools.get(name)
        return bool(spec and (spec.risk >= Risk.MEDIUM or name.startswith("desktop_") and name != "desktop_cursor"
                             or name in {"ui_focus", "open_url", "browser_navigate", "open_application", "focus_window", "focus_window_advanced", "chrome_new_tab", "chrome_select_tab"}))

    def _execute_tool(self, name: str, arguments: dict[str, Any], approved: bool = False) -> str:
        def dispatch():
            if self._is_stopped():
                return "CANCELLED: Emergency stop is active"
            raw_input = name.startswith("desktop_") and name != "desktop_cursor"
            mutation = self._is_mutation(name)
            if mutation and name not in {"desktop_mouse_up", "desktop_key_up"} and self.orchestrator.needs_action_review():
                return "ERROR: Observe the last action and call task_verify with evidence before another mutation. Use verified=false for a failed outcome, then diagnose and recover."
            # Coordinates must come from a stable observed scene. Focused keyboard and
            # wheel input do not need a screenshot just to prove where the foreground
            # window is: strict Rust binds every dispatch to a fresh HWND/PID itself.
            if name in _DESKTOP_OBSERVATION_REQUIRED_TOOLS:
                try:
                    self._desktop_observation.check(arguments)
                except ValueError as exc:
                    return f"ERROR: {exc}"
            device_busy = mutation or raw_input or name == "screen_observe"
            if device_busy:
                self._device_action_active.set()
            try:
                if mutation:
                    # This is the single durable write-intent point. Callers must not
                    # duplicate start_action around _execute_tool.
                    self.orchestrator.start_action(name, arguments)
                result = self.tools.execute(name, arguments, approved)
            finally:
                if device_busy:
                    self._device_action_active.clear()
            if name == "screen_observe" and result.startswith("VERIFIED: "):
                self._desktop_observation.observe(json.loads(result[len("VERIFIED: "):]))
            elif name in _DESKTOP_SCENE_MUTATION_TOOLS or name in _FOCUSED_NATIVE_BURST_TOOLS:
                # Any click/drag/type/hotkey/scroll may change visible state. Invalidate
                # coordinate authority, but do not force another screenshot between
                # consecutive focused native inputs.
                self._desktop_observation.invalidate()
            return result

        if self._is_stopped():
            return "CANCELLED: Emergency stop is active"
        # The browser guard itself dispatches a read tool. Run it before submitting
        # to the single worker, otherwise that nested read would deadlock the queue.
        guard_result = self._browser_action_guard(name)
        if guard_result:
            return guard_result
        if name.startswith("workflow_"):
            # The container runs outside the single tool executor so child dispatches
            # can use that executor without deadlocking. Each child is policy-checked.
            return self.tools.execute(name, arguments, approved)
        future = self._tool_executor.submit(dispatch)
        while True:
            try:
                return future.result(timeout=0.05)
            except FutureTimeout:
                if self._is_stopped():
                    future.cancel()
                    return "CANCELLED: Action interrupted; inspect state before any retry"

    def _system_prompt(self, user_text: str = "") -> str:
        base = super()._system_prompt(user_text)
        base += "\nTerminal tool permission: " + ("enabled by the operator" if getattr(self, "allow_shell", False) else "disabled; the operator must select terminal access in the Full Access UI before run_powershell can execute") + "."
        return base + """

Full Access execution profile:
- Create a task_plan before multi-step work, honor its dependencies, and update each step using observed evidence. Use task_status for progress. After any uncertain mutation, observe before retrying: never blindly repeat writes, clicks, sends, or commands.
- When the user asks to continue or resume, use task_recall to recover the previous checkpoint, then inspect the live state and plan only remaining work. The current screen and file contents take precedence over saved evidence.
- After an action without a VERIFIED result, inspect the resulting state and call task_verify with a specific claim and observed evidence before another mutation. For a failed outcome, record verified=false, recover, then re-verify the SAME claim to resolve it. Verification cannot be invented from intended actions.
- Raw mouse coordinates require a stable screen_observe with the same foreground window and coordinates inside its virtual desktop. Focused Rust keyboard/hotkey/scroll input does not require a redundant screenshot before every key because the daemon binds every dispatch to a fresh HWND/PID. Consecutive focused native inputs may run as one bounded burst, followed by one fresh observation and review. Coordinate clicks/drags still force immediate post-action observation. Cursor arrival alone does not verify the application outcome.
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

    def _replace_internal_vision(self, followup: dict[str, Any], *, live: bool = False) -> None:
        # Keep only the newest screenshot in the live model context. Image bytes never enter
        # MemoryStore or tool traces, which keeps subsequent turns fast and bounded.
        self.messages = [
            message for message in self.messages
            if not (isinstance(message, dict) and is_internal_vision_message(message))
        ]
        self.messages.append(followup)
        self._explicit_vision_pending = not live

    def _refresh_live_vision(self) -> None:
        # A requested screen observation must reach the next decision intact,
        # including its coordinate mapping. A background preview cannot replace it.
        if getattr(self, "_explicit_vision_pending", False):
            self._explicit_vision_pending = False
            return
        monitor = getattr(self, "live_monitor", None)
        if monitor is None:
            return
        observation = monitor.model_message()
        if observation:
            self._replace_internal_vision(observation, live=True)
        else:
            self.messages = [item for item in self.messages if not (is_internal_vision_message(item)
                and "Live preview observed" in str(item["content"][0].get("text", "")))]

    def run(self, user_text: str, emit: Callable[[AgentEvent], None] | None = None, *, resume_current: bool = False) -> str:
        self._active_emit = emit
        if not resume_current:
            self.orchestrator.begin(user_text)
            self.workspace_context.save_snapshot()
            self.messages.append({"role": "user", "content": user_text})
            self.memory.add("user", user_text)
        else:
            current = self.orchestrator.current
            if current is None or current.goal != user_text:
                raise ValueError("Recovery must retain the original active mission")
            current.status, current.finished_at = "running", None
            self.orchestrator._persist(current)
            self.messages.append({"role": "user", "content": "The deterministic path stopped. Continue this SAME mission from its failed step. Completed steps must not be replayed. Observe the live state and review any uncertain mutation before recovery. Current state: " + json.dumps(self.orchestrator.summary()) + " Recent evidence: " + json.dumps([{"tool": trace.name, "result": trace.result} for trace in current.traces[-4:]])})

        requested_new_tab_count = required_new_tabs(user_text)
        requested_google_search_count = required_google_searches(user_text)
        initial_tab_count = getattr(self, "_mission_initial_tab_count", len(_chrome_tab_rows())) if resume_current else len(_chrome_tab_rows())
        minimum_required_tabs = minimum_tab_count(initial_tab_count, requested_new_tab_count)
        turn_tool_schemas = self._tool_schemas_for_goal(user_text)

        try:
            for turn in range(self.max_turns):
                if self._is_stopped():
                    self.orchestrator.finish("cancelled", "Emergency stop is active")
                    return "CANCELLED: Emergency stop is active"
                self.orchestrator.start_turn(turn + 1)
                self._compact_context(user_text)
                # Captures never trigger extra model requests or authorize raw input.
                self._refresh_live_vision()
                emit and emit(AgentEvent("status", f"Thinking… (turn {turn + 1})"))
                self.orchestrator.current.metrics["model_calls"] = self.orchestrator.current.metrics.get("model_calls", 0) + 1
                response = self._chat_completion(
                    messages=[{"role": "system", "content": self._system_prompt(user_text)}, *self.messages],
                    tools=turn_tool_schemas,
                    tool_choice="auto",
                )
                failover_notice = self.pop_provider_failover_notice()
                if failover_notice:
                    emit and emit(AgentEvent("status", failover_notice))
                if self._is_stopped():
                    self.orchestrator.finish("cancelled", "Emergency stop is active")
                    return "CANCELLED: Emergency stop is active"
                message = response.choices[0].message
                self.messages.append(message.model_dump(exclude_none=True))
                tool_calls = getattr(message, "tool_calls", None) or []
                if not tool_calls:
                    if self.orchestrator.needs_action_review():
                        self.messages.append({"role": "user", "content": "An action is still unverified. Inspect its result and use task_verify with observed evidence before completing the mission."})
                        continue
                    unfinished_workflows = [item["id"] for item in self.orchestrator.current.workflows if item["status"] != "completed"]
                    if unfinished_workflows:
                        self.messages.append({"role": "user", "content": "Required workflow steps remain incomplete: " + ", ".join(unfinished_workflows) + ". Inspect and recover the current step; never skip it or restart completed actions."})
                        continue
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
                            if step.status != "completed"
                        ]
                        if pending:
                            self.messages.append({"role": "user", "content": "Required plan steps remain: " + ", ".join(step.id for step in pending) + ". Continue in order using observed evidence; do not silently skip these steps."})
                            continue
                    self.memory.add("assistant", result)
                    self.orchestrator.finish("completed", result)
                    return result

                vision_followups: list[dict[str, Any]] = []
                native_burst_pending = False
                for call in tool_calls:
                    if self._is_stopped():
                        self.orchestrator.finish("cancelled", "Emergency stop is active")
                        return "CANCELLED: Emergency stop is active"
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
                        approved = self.approval(name, arguments)
                        result = self._execute_tool(name, arguments, approved=approved)
                        challenge_pause = str(result).startswith("BROWSER_ACTION_BLOCKED:")

                    duration_ms = (time.perf_counter() - started) * 1000.0
                    mutation = self._is_mutation(name)
                    # Rejected dispatches do not constitute a new action to review.
                    mutation = mutation and not str(result).startswith(("PERMISSION_DENIED", "ERROR: Observe the last", "ERROR: Fresh screen", "ERROR: Foreground", "ERROR: Desktop coordinate"))
                    deferred_native_review = (
                        mutation
                        and name in _FOCUSED_NATIVE_BURST_TOOLS
                        and str(result).startswith("RUST_EXECUTED:")
                    )
                    self.orchestrator.record_tool(
                        name,
                        arguments,
                        result,
                        duration_ms,
                        turn + 1,
                        mutation=mutation,
                        review_required=mutation and not deferred_native_review,
                    )
                    if deferred_native_review:
                        native_burst_pending = True
                        self.orchestrator.current.metrics["native_input_burst_actions"] = (
                            self.orchestrator.current.metrics.get("native_input_burst_actions", 0) + 1
                        )
                    if mutation and result.startswith("VERIFIED:") and not name.startswith("desktop_"):
                        self.orchestrator.verify(f"{name} reported its postcondition", True, result)
                    emit and emit(AgentEvent("tool_result", result, name))
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

                    if name == "screen_observe":
                        followup = vision_followup_message(result)
                        if followup is not None:
                            vision_followups.append(followup)

                    if (
                        name.startswith("desktop_")
                        and name != "desktop_cursor"
                        and tool_succeeded(result)
                        and not self._is_stopped()
                    ):
                        if name in _FOCUSED_NATIVE_BURST_TOOLS and deferred_native_review:
                            # Do not stop after every key/type/hotkey/scroll. The daemon
                            # re-binds each atomic input to the foreground window; one
                            # observation is taken after the bounded burst.
                            continue
                        if name == "desktop_move":
                            # Rust verifies physical cursor arrival. Preserve the current
                            # observed scene so a following click can use it immediately.
                            continue
                        observed_started = time.perf_counter()
                        observed = self._execute_tool("screen_observe", {}, approved=True)
                        self.orchestrator.record_tool(
                            "screen_observe", {}, observed,
                            (time.perf_counter() - observed_started) * 1000.0,
                            turn + 1,
                        )
                        emit and emit(AgentEvent("tool_result", observed, "screen_observe"))
                        followup = vision_followup_message(observed)
                        if followup is not None:
                            vision_followups.append(followup)
                        # A coordinate/scene-changing desktop action still needs review
                        # before another mutation because later coordinates may now be stale.
                        for remaining in tool_calls[tool_calls.index(call) + 1:]:
                            self.messages.append({"role": "tool", "tool_call_id": remaining.id,
                                                  "content": "ERROR: Re-plan this action after evaluating the fresh desktop observation"})
                        break

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
                        if self.orchestrator.repeated_failure(name, arguments):
                            result = f"Stopped after three failed {name} attempts without new successful observation. Inspect the saved checkpoint before continuing."
                            self.orchestrator.finish("incomplete", result)
                            return result
                        # A later action may depend on the failed action even if it
                        # targets another application. Return all undelivered calls
                        # to the model together; do not silently run the remainder.
                        for remaining in tool_calls[tool_calls.index(call) + 1:]:
                            self.messages.append({"role": "tool", "tool_call_id": remaining.id,
                                                  "content": "ERROR: Not executed because an earlier ordered action failed. Recover that step first."})
                        self.messages.append({"role": "user", "content": hint})
                        break

                if native_burst_pending and not self._is_stopped():
                    # One post-burst observation replaces the old screenshot-after-every-key
                    # behavior. It supplies fresh evidence to the next model turn while
                    # still requiring the model to verify the application-level outcome.
                    observed_started = time.perf_counter()
                    observed = self._execute_tool("screen_observe", {}, approved=True)
                    self.orchestrator.record_tool(
                        "screen_observe", {}, observed,
                        (time.perf_counter() - observed_started) * 1000.0,
                        turn + 1,
                    )
                    emit and emit(AgentEvent("tool_result", observed, "screen_observe"))
                    followup = vision_followup_message(observed)
                    if followup is not None:
                        vision_followups.append(followup)
                    self.orchestrator.require_action_review()
                    self.orchestrator.current.metrics["native_input_bursts"] = (
                        self.orchestrator.current.metrics.get("native_input_bursts", 0) + 1
                    )

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
