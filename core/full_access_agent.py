from __future__ import annotations

import ctypes
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable
from jsonschema import ValidationError

from .agent import AgentEvent, JarvisAgent, _tool_schemas
from .browser_mission_contract import (
    google_search_result_count,
    minimum_tab_count,
    required_google_searches,
    required_new_tabs,
)
from .vision_tools import _payload_from_result, is_internal_vision_message, vision_followup_message
from .desktop_observation import DesktopObservationGate
from .execution_telemetry import input_not_dispatched
from .orchestrator import tool_succeeded
from .permissions import Risk
from .model_context import compact_bookkeeping_result

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
    "interaction_click",
    "interaction_focus",
    "interaction_type",
    "interaction_hotkey",
    "interaction_scroll",
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
    "interaction_type",
    "interaction_hotkey",
    "interaction_scroll",
}
_DESKTOP_SCENE_MUTATION_TOOLS = {
    "desktop_click",
    "desktop_click_button",
    "desktop_double_click",
    "desktop_drag",
    "interaction_click",
    "interaction_focus",
}
_DESKTOP_REFRESH_ERRORS = (
    "ERROR: Fresh screen_observe required",
    "ERROR: Fresh stable screen_observe required",
    "ERROR: Foreground changed since observation",
)
_REJECTED_ACTION_ERRORS = (
    "PERMISSION_DENIED",
    "ERROR: Observe the last",
    "ERROR: Foreground",
    "ERROR: Desktop coordinate",
    *_DESKTOP_REFRESH_ERRORS,
)
_SEMANTIC_OBSERVATION_TOOLS = {
    "ui_inspect", "ui_resolve", "interaction_inspect", "interaction_scene",
    "interaction_resolve", "browser_semantic_snapshot",
}
_POSTCONDITION_READ_TOOLS = _SEMANTIC_OBSERVATION_TOOLS | {
    "interaction_wait", "ui_wait_state", "browser_wait_state", "browser_wait",
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
    "control_guide", "list_skills", "load_skill",
    "google_search",
    "screen_observe",
    "computer_observe",
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
    "open_url",
}
_BROWSER_PROFILE_PREFIXES = ("browser_", "chrome_", "task_", "dialog_", "desktop_", "ui_", "interaction_", "discord_", "whatsapp_", "youtube_", "workflow_")


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
        control = getattr(self, "mission_control", None)
        return self._stop.is_set() or bool(external and external.is_set()) or bool(control and control.cancelled)

    def reset(self) -> None:
        interrupted = getattr(self, "_interrupted_tool", None)
        if interrupted is not None and not interrupted.done():
            raise RuntimeError("Previous action is still stopping; wait for it to settle before starting another mission")
        pending_model = getattr(self, "_pending_model", None)
        if pending_model is not None and not pending_model.done():
            raise RuntimeError("Previous AI request is still stopping; its response will be discarded. Retry when it settles.")
        super().reset()
        self._interrupted_tool = None
        self._recovery_observed_reviews = set()
        self._stop.clear()
        self._device_action_active.clear()
        self._desktop_observation.invalidate()

    def _wait_for_model(self, *, emit=None, **kwargs):
        """Wait responsively; a late response can never authorize desktop input."""
        pending = getattr(self, "_pending_model", None)
        if pending is not None and not pending.done():
            raise RuntimeError("AI_PROVIDER_UNAVAILABLE: Previous AI request is still stopping; no new request was issued")
        if self._is_stopped():
            raise RuntimeError("CANCELLED: Mission cancelled before planning")
        executor = getattr(self, "_model_executor", None)
        if executor is None:
            executor = self._model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-planner")
        # The SDK timeout bounds individual network operations. This deadline also
        # bounds a slow/trickling response, including at most one configured fallback.
        budget = getattr(self, "ai_timeout_seconds", 45.0)
        if getattr(self, "_fallback_client", None) is not None:
            budget *= 2
        started = time.monotonic()
        next_notice = started + 10.0
        abandoned = threading.Event()
        future = self._pending_model = executor.submit(self._chat_completion, _cancel_check=abandoned.is_set, **kwargs)
        try:
            while True:
                if self._is_stopped():
                    self._stop.set()
                    abandoned.set()
                    future.cancel()
                    raise RuntimeError("CANCELLED: Mission cancelled while waiting for the AI provider")
                remaining = started + budget - time.monotonic()
                if remaining <= 0:
                    abandoned.set()
                    future.cancel()
                    raise RuntimeError(
                        f"AI_PROVIDER_UNAVAILABLE: Planning exceeded {budget:g} seconds. "
                        "The checkpoint was preserved; any late response will be discarded.")
                try:
                    return future.result(timeout=min(0.05, remaining))
                except FutureTimeout:
                    if future.done():
                        # Completion can race the polling timeout. Retrieve the
                        # actual response/exception rather than raising that poll.
                        return future.result()
                    now = time.monotonic()
                    if emit and now >= next_notice:
                        emit(AgentEvent("status", f"Waiting for the AI provider ({int(now - started)}s); cancellation remains available."))
                        next_notice = now + 10.0
        finally:
            if future.done():
                self._pending_model = None

    def close(self) -> None:
        executor = getattr(self, "_model_executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        super().close()

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

    def _recovery_observation(self) -> tuple[str, dict]:
        current = self.orchestrator.current
        if current and current.last_review_required_index >= 0:
            action = current.traces[current.last_review_required_index]
            browser = (action.name.startswith(("browser_", "chrome_"))
                       or action.arguments.get("surface") == "browser"
                       or bool(action.arguments.get("browser_target")))
            if browser:
                for name, args in (("interaction_inspect", {"surface": "browser"}),
                                   ("browser_semantic_snapshot", {}), ("browser_read_page", {})):
                    if name in self.tools._tools:
                        return name, args
        return "screen_observe", {}

    def _execute_tool(self, name: str, arguments: dict[str, Any], approved: bool = False) -> str:
        def dispatch():
            if self._is_stopped():
                return "CANCELLED: Emergency stop is active"
            control = getattr(self, "mission_control", None)
            if control is not None:
                try:
                    if control.boundary(self._is_stopped):
                        self._desktop_observation.invalidate()
                        if name.startswith("desktop_") and name not in {"desktop_cursor", "desktop_mouse_up", "desktop_key_up"}:
                            return "ERROR: Fresh screen_observe required after pause; resolve the intended input target again"
                except RuntimeError as exc:
                    return f"CANCELLED: {exc}"
            if name == "task_update_step" and arguments.get("status") == "completed" and self.orchestrator.current:
                from .whatsapp_fast_mission import typing_completion_error
                error = typing_completion_error(self.orchestrator.current, str(arguments.get("step_id", "")))
                if error:
                    return error
            if name in {"ui_type", "interaction_type"} and arguments.get("submit") is True:
                # The fast compiler is a deterministic source of the requested
                # write-only intent even when recovery later invokes the model.
                from .whatsapp_fast_mission import compile_chat_typing_mission
                current = self.orchestrator.current
                program = compile_chat_typing_mission(current.goal) if current else None
                if program and program[-1].tool in {"ui_type_native", "interaction_type"}:
                    return "PERMISSION_DENIED: This mission requests typing an unsent draft. Use submit=false; sending was not requested."
            raw_input = name.startswith("desktop_") and name != "desktop_cursor"
            mutation = self._is_mutation(name)
            if mutation and name not in {"desktop_mouse_up", "desktop_key_up"} and self.orchestrator.needs_action_review():
                return "ERROR: Observe the last action and call task_verify with evidence before another mutation. Use verified=false for a failed outcome, then diagnose and recover."
            if mutation and (control is not None or getattr(self, "require_action_confirmation", False)):
                from .action_confirmation import classify_action, ConfirmationRequirement
                spec = self.tools._tools.get(name)
                if spec is not None:
                    allowed, reason = self.tools.permissions.check(name, spec.risk, approved)
                    if not allowed:
                        return f"PERMISSION_DENIED: {reason}"
                    try:
                        self.tools._validators[name].validate(arguments)
                    except ValidationError:
                        # Preserve the registry's compact pre-dispatch error and
                        # telemetry; malformed arguments are not denied consent.
                        return self.tools.execute(name, arguments, approved)
                    try:
                        requirement = classify_action(name, arguments)
                        if requirement is None and spec.risk >= Risk.HIGH:
                            requirement = ConfirmationRequirement("high_risk", f"Allow {name} once?", "High-risk operation")
                        if requirement is not None:
                            if control is None:
                                return "PERMISSION_DENIED: This action requires one-time operator confirmation in the Control Center"
                            current = self.orchestrator.current
                            # Operator interaction with the dashboard can change
                            # focus. Raw input must retain its original window,
                            # never silently bind to the approval button's window.
                            from .desktop_observation import foreground_identity
                            bound_foreground = foreground_identity() if name.startswith("desktop_") else None
                            if bound_foreground == 0:
                                return "PERMISSION_DENIED: Cannot bind this input to a known foreground window"
                            control.confirm(name, arguments, requirement, current.task_id if current else "", self._is_stopped)
                            if control.boundary(self._is_stopped):
                                self._desktop_observation.invalidate()
                                if name.startswith("desktop_"):
                                    return "PERMISSION_DENIED: Input context changed while paused; observe and resolve the target again"
                            if bound_foreground is not None and foreground_identity() != bound_foreground:
                                self._desktop_observation.invalidate()
                                return "PERMISSION_DENIED: Foreground changed during confirmation; inspect and resolve the intended window again"
                    except Exception as exc:
                        return f"PERMISSION_DENIED: {exc}"
            # Coordinates must come from a stable observed scene. Focused keyboard and
            # wheel input do not need a screenshot just to prove where the foreground
            # window is: strict Rust binds every dispatch to a fresh HWND/PID itself.
            if name in _DESKTOP_OBSERVATION_REQUIRED_TOOLS:
                try:
                    self._desktop_observation.check(arguments)
                except ValueError as exc:
                    return f"ERROR: {exc}"
            device_busy = mutation or raw_input or name in {"screen_observe", "computer_observe"}
            activity = getattr(self, "_device_action_active", None)
            if device_busy and activity is not None:
                activity.set()
            try:
                if mutation:
                    # This is the single durable write-intent point. Callers must not
                    # duplicate start_action around _execute_tool.
                    self.orchestrator.start_action(name, arguments)
                result = self.tools.execute(name, arguments, approved)
            finally:
                if device_busy and activity is not None:
                    activity.clear()
            if name in {"screen_observe", "computer_observe"} and result.startswith("VERIFIED: "):
                frame = _payload_from_result(result)
                if frame is not None:
                    self._desktop_observation.observe(frame)
                else:
                    # A fresh semantic tree is valid observation evidence but is
                    # never a screenshot or permission to reuse old coordinates.
                    self._desktop_observation.invalidate()
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
        guard_result = self._browser_action_guard(name, arguments)
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
                    # Cancellation may return to the UI before a slow adapter
                    # exits. Keep cancellation latched and prevent reset from
                    # granting that old operation the next mission's authority.
                    self._stop.set()
                    future.cancel()
                    self._interrupted_tool = future
                    return "CANCELLED: Action interrupted; inspect state before any retry"

    def _system_prompt(self, user_text: str = "") -> str:
        base = super()._system_prompt(user_text)
        base += "\nTerminal tool permission: " + ("enabled by the operator" if getattr(self, "allow_shell", False) else "disabled; the operator must select terminal access in the Full Access UI before run_powershell can execute") + "."
        return base + """

Full Access execution profile:
- The operator can pause/resume/cancel at execution boundaries without another model call. Sending, destructive actions, arbitrary commands, and uncertain activations require one-time confirmation in the Control Center. Full Access does not replace that confirmation. Never approve your own action, switch tools to evade a denial, or use desktop input to operate JARVIS permission controls. A rejected action cancels the mission; preserve its checkpoint.
- A request to write or type text does not authorize sending it. Keep messages as unsent drafts unless the user explicitly requests submission; use submit=false and never add Enter or a Send click to a typing-only mission.
- Create a task_plan before multi-step work, honor its dependencies, and update each step using observed evidence. Use task_status for progress. After any uncertain mutation, observe before retrying: never blindly repeat writes, clicks, sends, or commands.
- Plan meaningful outcomes, not separate focus/inspect/type/verify bookkeeping steps for one editor change. Keep its necessary actions and readback together in one plan step and workflow. Complete each plan step immediately after its verified outcome, before starting the next; a completed step consumes its recent evidence. Do not postpone all step updates until the end or replay mutations merely to repair bookkeeping.
- You may return multiple tool calls in one response; JARVIS executes them in their listed order. Group independent preparation such as task_plan and the initial read in one response. Do not spend a model turn only announcing that a step is running. Never precompute a later action when its target or arguments depend on an unread result.
- When the user asks to continue or resume, use task_recall to recover the previous checkpoint, then inspect the live state and plan only remaining work. The current screen and file contents take precedence over saved evidence.
- After an action without a VERIFIED result, inspect the resulting state and call task_verify with a specific claim and observed evidence before another mutation. For a failed outcome, record verified=false, recover, then re-verify the SAME claim to resolve it. Verification cannot be invented from intended actions.
- Raw mouse coordinates require a stable screen_observe with the same foreground window and coordinates inside its virtual desktop. Focused Rust keyboard/hotkey/scroll input does not require a redundant screenshot before every key because the daemon binds every dispatch to a fresh HWND/PID. Consecutive focused native inputs may run as one bounded burst, followed by one fresh observation and review. Coordinate clicks/drags still force immediate post-action observation. Cursor arrival alone does not verify the application outcome.
- Optimize for low latency and verified completion. Prefer one reliable semantic/direct tool over several exploratory mouse or keyboard steps when both achieve the same requested result.
- For an explicit Google search, prefer google_search. It performs a guarded verified search in one call. Set new_tab=true only when the user explicitly asks for a new/additional tab.
- Browser navigation and Google tools reuse the user's open Chrome window through matching CDP or guarded Windows toolbar input. Start Chrome only if no browser window exists. Keep compound searches in the same resolved window; never use an unrelated CDP session for a follow-up. An existing-window session supports guarded navigation/search, but arbitrary page interaction requires matching DOM/CDP access.
- If the user explicitly says "new tab" or "another tab", that is a structural requirement: use chrome_new_tab or google_search(new_tab=true) for that step. browser_navigate/open_url on the current tab does NOT satisfy a new-tab request.
- Preserve earlier result tabs when the user asks for a later search in a new tab. Complete every clause in order before returning a final answer.
- For unfamiliar apps or sites, prefer interaction_inspect then interaction_click/interaction_focus/interaction_type/interaction_hotkey/interaction_scroll. These universal tools auto-route managed Chrome to exact DOM/CDP and other foreground apps to Windows UIA/Rust. Do not manually choose a lower-level backend unless the universal semantic route cannot represent the target.
- Use exact labels from fresh metadata, never guessed placeholder names. For desktop typing, omit target when the intended application has one unambiguous composer; otherwise use interaction_inspect(surface=desktop, control_type=Edit) to resolve it. Narrow control_type/query before requesting a full UI tree. Batch known actions and their actual postconditions with workflow_execute; do not spend separate model turns on each deterministic action.
- Browser targets must use exactly one shape: {role, name}, {selector}, or {node_id}. Copy the observed action_target/target arguments, not the entire node record; never mix node_id or selector with role/name. A node_id also requires its snapshot's expected_version as a sibling argument. Prefer a unique observed role/name when available. For exact editor text readback use interaction_wait(state=text, text=the_expected_value); page body text does not include all editor values.
- When an unfamiliar interface needs both semantic and visual understanding, use computer_observe once. It returns live semantic targets and adds a screen image only when coverage is incomplete; visual=always requests an image for visual ambiguity. Semantic-only results are valid observation evidence but cannot authorize coordinates. Respect separate_surfaces: a managed browser tab and the foreground screenshot may describe different interfaces.
- interaction_type never submits unless submit=true. For custom/canvas desktop editors with no usable UIA node, focused_fallback=true is allowed only after the intended control is already focused and Chrome is not the foreground surface; otherwise use screen_observe + a guarded click first.
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

    def _browser_action_guard(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str | None:
        if tool_name.startswith("interaction_") and (arguments or {}).get("surface") == "browser":
            # Explicit DOM actions target the selected CDP page, including an owned
            # background browser. The desktop foreground is a different surface.
            return super()._browser_action_guard("browser_click")
        # Native/desktop input must not become a side channel around the DOM/CDP CAPTCHA guard.
        if tool_name in _DESKTOP_BROWSER_INPUT_TOOLS and _foreground_is_chrome():
            # The connected CDP session may belong to a different Chrome process.
            # Inspect the actual foreground window before any generic desktop input.
            try:
                from .chrome_existing_window import _identity, read_existing_chrome
                from .semantic_ui_tools import _foreground_hwnd
                window = _identity(_foreground_hwnd())
                state = read_existing_chrome(window)
                if state.get("challenge_detected"):
                    return "BROWSER_ACTION_BLOCKED: Complete human verification manually in the current Chrome window."
            except Exception as exc:
                return f"ERROR: Current Chrome window could not be checked safely: {exc}"
            return None
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

    def _pause_for_native_stop(self, result: str, emit=None) -> str | None:
        if not (str(result).startswith("ERROR") and
                "RustEngineUnavailable: Rust daemon emergency stop is latched" in str(result)):
            return None
        message = ("Rust native input is emergency-stopped. Restart JARVIS to restore the native engine, "
                   "then continue the mission. No further clicks or typing were attempted.")
        self.orchestrator.finish("waiting_user", message)
        self.memory.add("assistant", message)
        emit and emit(AgentEvent("status", message))
        return message

    def run(self, user_text: str, emit: Callable[[AgentEvent], None] | None = None, *, resume_current: bool = False) -> str:
        self._active_emit = emit
        if not resume_current:
            try:
                from .interaction_scene import reset_interaction_scenes
                reset_interaction_scenes()
            except ImportError:
                pass
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
        # Only new-tab contracts need a starting tab count. Do not inspect an
        # unrelated Chrome session for every desktop mission, or eagerly evaluate
        # the fallback when a resumed fast mission already saved its baseline.
        initial_tab_count = 0
        if requested_new_tab_count:
            initial_tab_count = getattr(self, "_mission_initial_tab_count", None) if resume_current else None
            if initial_tab_count is None:
                initial_tab_count = len(_chrome_tab_rows())
        minimum_required_tabs = minimum_tab_count(initial_tab_count, requested_new_tab_count)
        turn_tool_schemas = self._tool_schemas_for_goal(user_text)
        self.orchestrator.current.metrics.update(
            model_tool_count=len(turn_tool_schemas),
            model_tool_schema_chars=len(json.dumps(turn_tool_schemas, ensure_ascii=False)),
        )

        try:
            for turn in range(self.max_turns):
                control = getattr(self, "mission_control", None)
                if control is not None:
                    if control.boundary(self._is_stopped):
                        self._desktop_observation.invalidate()
                if self._is_stopped():
                    self.orchestrator.finish("cancelled", "Emergency stop is active")
                    return "CANCELLED: Emergency stop is active"
                self.orchestrator.start_turn(turn + 1)
                self._compact_context(user_text)
                # Captures never trigger extra model requests or authorize raw input.
                self._refresh_live_vision()
                emit and emit(AgentEvent("status", f"Thinking… (turn {turn + 1})"))
                self.orchestrator.current.metrics["model_calls"] = self.orchestrator.current.metrics.get("model_calls", 0) + 1
                model_started = time.perf_counter()
                try:
                    response = self._wait_for_model(
                        emit=emit,
                        messages=[{"role": "system", "content": self._system_prompt(user_text)}, *self.messages],
                        tools=turn_tool_schemas,
                        tool_choice="auto",
                    )
                finally:
                    metrics = self.orchestrator.current.metrics
                    elapsed = max(0, round((time.perf_counter() - model_started) * 1000))
                    metrics["model_wait_ms"] = metrics.get("model_wait_ms", 0) + elapsed
                failover_notice = self.pop_provider_failover_notice()
                if failover_notice:
                    emit and emit(AgentEvent("status", failover_notice))
                if self._is_stopped():
                    self.orchestrator.finish("cancelled", "Emergency stop is active")
                    return "CANCELLED: Emergency stop is active"
                if control is not None and control.boundary(self._is_stopped):
                    self._desktop_observation.invalidate()
                message = response.choices[0].message
                self.messages.append(message.model_dump(exclude_none=True))
                tool_calls = getattr(message, "tool_calls", None) or []
                if not tool_calls:
                    if self.orchestrator.needs_action_review():
                        # An explicit negative readback is a failed postcondition,
                        # not permission to advance or retry the uncertain input.
                        # When the model has no further tools to propose, close the
                        # checkpoint as incomplete rather than prompting forever.
                        current = self.orchestrator.current
                        recent_review = next(
                            (record for record in reversed(current.verifications)
                             if record.evidence_trace_index >= current.last_review_required_index),
                            None,
                        )
                        if recent_review is not None and not recent_review.verified:
                            result = (
                                "Verification failed: the previous action's requested outcome "
                                "was not confirmed. Checkpoint preserved; no automatic input replay."
                            )
                            self.memory.add("assistant", result)
                            self.orchestrator.finish("incomplete", result)
                            return result
                        self.messages.append({"role": "user", "content": "An action is still unverified. Inspect its result and use task_verify with observed evidence before completing the mission."})
                        continue
                    unfinished_workflows = [item["id"] for item in self.orchestrator.current.workflows if item["status"] != "completed"]
                    if unfinished_workflows:
                        self.messages.append({"role": "user", "content": "Required workflow steps remain incomplete: " + ", ".join(unfinished_workflows) + ". Inspect and recover the current step; never skip it or restart completed actions."})
                        continue
                    browser_contract = requested_new_tab_count or requested_google_search_count
                    rows = _chrome_tab_rows() if browser_contract else []
                    actual_tab_count = len(rows)
                    actual_google_search_count = google_search_result_count(rows)

                    from .full_access_completion import native_browser_progress
                    native_progress = native_browser_progress(self.orchestrator.current) if browser_contract else None
                    if native_progress is not None:
                        actual_tab_count = native_progress["tab_count"]
                        actual_google_search_count = native_progress["google_search_count"]
                        minimum_required_tabs = minimum_tab_count(native_progress["initial_tab_count"], requested_new_tab_count)

                    unmet: list[str] = []
                    if native_progress is not None and native_progress.get("error"):
                        unmet.append(native_progress["error"] + "; inspect before recovery and never replay verified searches")
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
                        resolver = getattr(self.orchestrator, "step_is_resolved", None)
                        pending = [
                            step for step in self.orchestrator.current.plan
                            if not (resolver(step) if resolver is not None else step.status == "completed")
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
                    mutation = mutation and not str(result).startswith(_REJECTED_ACTION_ERRORS) and not input_not_dispatched(result)
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
                    model_result = compact_bookkeeping_result(name, result)
                    if len(model_result) < len(result):
                        metrics = self.orchestrator.current.metrics
                        metrics["bookkeeping_chars_saved"] = metrics.get("bookkeeping_chars_saved", 0) + len(result) - len(model_result)
                    if (name in _POSTCONDITION_READ_TOOLS and tool_succeeded(result)
                            and self.orchestrator.needs_action_review()):
                        model_result += (
                            "\nFresh observation is available for the preceding action. "
                            "Evaluate whether this evidence proves its requested outcome, then call task_verify "
                            "with verified=true or false before the next mutation. A visible or focused "
                            "control alone does not prove navigation, saving, or sending succeeded. "
                            "Do not repeat the delivered action or take another screenshot merely to record verification."
                        )
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": model_result})

                    native_pause = self._pause_for_native_stop(result, emit)
                    if native_pause:
                        for remaining in tool_calls[tool_calls.index(call) + 1:]:
                            self.messages.append({"role": "tool", "tool_call_id": remaining.id,
                                                  "content": "CANCELLED: Rust native input is emergency-stopped; this action was not executed."})
                        return native_pause

                    if name in {"screen_observe", "computer_observe"}:
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
                        review_key = (self.orchestrator.current.task_id,
                                      self.orchestrator.current.last_review_required_index)
                        observed_reviews = getattr(self, "_recovery_observed_reviews", set())
                        needs_review_observation = (
                            self.orchestrator.needs_action_review()
                            and ("InputDeliveryError" in str(result)
                                 or "Verification requires successful observation" in str(result))
                            and review_key not in observed_reviews
                        )
                        if (
                            (needs_review_observation or (
                                name in _DESKTOP_OBSERVATION_REQUIRED_TOOLS
                                and str(result).startswith(_DESKTOP_REFRESH_ERRORS)))
                            and not self._is_stopped()
                        ):
                            if needs_review_observation:
                                observed_reviews.add(review_key)
                                self._recovery_observed_reviews = observed_reviews
                            # Supply fresh read-only evidence after a rejected or uncertain
                            # action. Never replay input while its outcome needs review.
                            observed_started = time.perf_counter()
                            observe_name, observe_args = self._recovery_observation()
                            observed = self._execute_tool(observe_name, observe_args, approved=True)
                            self.orchestrator.record_tool(
                                observe_name, observe_args, observed,
                                (time.perf_counter() - observed_started) * 1000.0,
                                turn + 1,
                            )
                            emit and emit(AgentEvent("tool_result", observed, observe_name))
                            followup = vision_followup_message(observed)
                            if followup is not None:
                                vision_followups.append(followup)
                            hint += (
                                " JARVIS attempted a read-only state refresh without replaying input. "
                                "Use this actual observation to review the previous outcome; a screenshot alone does not mark it successful. "
                                "Continue only the unfinished step and preserve completed writes. "
                                + observe_name + " result: " + observed
                            )
                            metric = "desktop_recovery_observations" if observe_name == "screen_observe" else "browser_recovery_observations"
                            self.orchestrator.current.metrics[metric] = (
                                self.orchestrator.current.metrics.get(metric, 0) + 1
                            )
                        if self.orchestrator.repeated_failure(name, arguments):
                            result = f"Stopped after three failed {name} attempts without new successful observation. Last error: {result[:1200]} Inspect the saved checkpoint before continuing."
                            for remaining in tool_calls[tool_calls.index(call) + 1:]:
                                self.messages.append({"role": "tool", "tool_call_id": remaining.id,
                                                      "content": "CANCELLED: Earlier ordered action exhausted recovery attempts; this action was not executed."})
                            self.memory.add("assistant", result)
                            emit and emit(AgentEvent("status", result, name))
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
            if self._is_stopped():
                result = "CANCELLED: Mission cancelled; completed work remains in the checkpoint"
                self.memory.add("assistant", result)
                self.orchestrator.finish("cancelled", result)
                emit and emit(AgentEvent("status", result))
                return result
            if isinstance(exc, RuntimeError) and str(exc).startswith((
                "AI_PROVIDER_RATE_LIMITED:", "AI_PROVIDER_UNAVAILABLE:",
                "AI_PROVIDER_ACCESS_DENIED:", "AI_PROVIDER_FAILOVER_FAILED:",
            )):
                result = (
                    str(exc)
                    + " JARVIS paused with the current checkpoint intact; "
                    "continue the same mission when provider access is restored or configure a fallback provider."
                )
                self.memory.add("assistant", result)
                self.orchestrator.finish("waiting_user", result)
                emit and emit(AgentEvent("status", result))
                return result
            result = f"ERROR: {type(exc).__name__}: {exc}"
            self.memory.add("assistant", result)
            self.orchestrator.finish("failed", result)
            raise
