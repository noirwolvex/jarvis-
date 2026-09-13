from __future__ import annotations

import json
import time
from typing import Callable

from .agent import AgentEvent, JarvisAgent, _tool_schemas


class FullAccessJarvisAgent(JarvisAgent):
    """Full Access execution profile with a hard human-verification pause boundary."""

    def _system_prompt(self, user_text: str = "") -> str:
        base = super()._system_prompt(user_text)
        return base + """

Full Access browser routing:
- For browser, tab, Google, or web-search requests, do not launch Chrome through launch_installed_app or open_application. Use chrome_connect_cdp and chrome_new_tab so JARVIS controls the exact selected tab.
- If a guarded browser action encounters CAPTCHA, anti-bot, or human verification, stop the current run immediately. Leave the current Chrome window and tab open and unchanged. Do not close it, switch away, navigate elsewhere, launch another browser, or attempt an alternate automation path. The user must complete that checkpoint manually before JARVIS continues.
"""

    def run(self, user_text: str, emit: Callable[[AgentEvent], None] | None = None) -> str:
        self.orchestrator.begin(user_text)
        self.workspace_context.save_snapshot()
        self.messages.append({"role": "user", "content": user_text})
        self.memory.add("user", user_text)
        try:
            for turn in range(self.max_turns):
                self.orchestrator.start_turn(turn + 1)
                emit and emit(AgentEvent("status", f"Thinking… (turn {turn + 1})"))
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": self._system_prompt(user_text)}, *self.messages],
                    tools=_tool_schemas(self.tools),
                    tool_choice="auto",
                )
                message = response.choices[0].message
                self.messages.append(message.model_dump(exclude_none=True))
                tool_calls = getattr(message, "tool_calls", None) or []
                if not tool_calls:
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

                    duration_ms = (time.perf_counter() - started) * 1000.0
                    self.orchestrator.record_tool(name, arguments, result, duration_ms, turn + 1)
                    emit and emit(AgentEvent("tool_result", result, name))
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

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

            result = "I reached the execution limit before the requested outcome was verified."
            self.memory.add("assistant", result)
            self.orchestrator.finish("incomplete", result)
            return result
        except Exception as exc:
            result = f"ERROR: {type(exc).__name__}: {exc}"
            self.memory.add("assistant", result)
            self.orchestrator.finish("failed", result)
            raise
