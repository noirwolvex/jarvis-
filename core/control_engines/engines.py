"""Five concrete engines over JARVIS's existing, policy-checked adapters."""
from __future__ import annotations

from typing import Protocol, Any

from .models import Action, Check
from .scheduler import check_context


class Provider(Protocol):
    def validate(self, tool: str, arguments: dict[str, Any]) -> None: ...
    def dispatch(self, tool: str, arguments: dict[str, Any]) -> str: ...
    def cleanup(self) -> None: ...


class Engine:
    operations: dict[str, str] = {}
    reads: frozenset[str] = frozenset()

    def __init__(self, provider: Provider):
        self.provider = provider

    def call(self, action: Action) -> tuple[str, dict[str, Any]]:
        tool = self.operations[action.action_type]
        arguments = action.arguments
        self.provider.validate(tool, arguments)
        return tool, arguments

    def execute(self, action: Action) -> str:
        check_context()
        tool, arguments = self.call(action)
        return self.provider.dispatch(tool, arguments)


class MouseEngine(Engine):
    """Semantic clicks first; explicit raw operations retain the scene guard."""
    operations = {"click": "interaction_click", "move": "desktop_move",
                  "click_coordinates": "desktop_click_button", "click_button": "desktop_click_button",
                  "desktop_click": "desktop_click_button",
                  "double_click": "desktop_double_click", "drag": "desktop_drag",
                  "scroll": "interaction_scroll", "cursor": "desktop_cursor",
                  "mouse_down": "desktop_mouse_down", "mouse_up": "desktop_mouse_up"}
    reads = frozenset({"cursor"})


class KeyboardEngine(Engine):
    """Use exact semantic field readback and native Unicode delivery underneath."""
    operations = {"type_text": "interaction_type", "type": "interaction_type",
                  "desktop_type": "interaction_type",
                  "hotkey": "interaction_hotkey", "desktop_hotkey": "interaction_hotkey",
                  "press_key": "desktop_press", "press": "desktop_press",
                  "desktop_press": "desktop_press",
                  "key_down": "desktop_key_down", "key_up": "desktop_key_up"}


class SmartUIEngine(Engine):
    """Reuse live DOM/UIA routing, disambiguation and stale-target detection."""
    operations = {"inspect": "interaction_inspect", "resolve": "interaction_resolve",
                  "focus": "interaction_focus", "launch": "launch_installed_app",
                  "open_folder": "open_known_folder", "navigate": "browser_navigate"}
    reads = frozenset({"inspect", "resolve"})


class VisionEngine(Engine):
    """Capture/perception and local OCR through registered, permission-aware tools."""
    operations = {"observe": "computer_observe", "capture": "screen_observe", "ocr": "vision_ocr"}
    reads = frozenset(operations)


class VerificationEngine(Engine):
    """Independent native/DOM postconditions; never accepts free-form proof."""
    operations = {"verify": "interaction_wait"}
    reads = frozenset(operations)

    def check(self, condition: Check) -> str:
        check_context()
        self.provider.validate(condition.tool, condition.arguments)
        return self.provider.dispatch(condition.tool, dict(condition.arguments))


class AgentProvider:
    """Adapter preserving Full Access policy, confirmations, tracing and Rust routing."""
    def __init__(self, agent):
        self.agent = agent

    def validate(self, tool: str, arguments: dict[str, Any]) -> None:
        registry = self.agent.tools
        if tool not in registry._tools:
            raise ValueError(f"Control backend unavailable: {tool}")
        registry._validators[tool].validate(arguments)

    def dispatch(self, tool: str, arguments: dict[str, Any]) -> str:
        from ..workflow_tools import WorkflowExecutor
        # Reuse the existing durable child dispatch; never invoke a handler directly.
        return WorkflowExecutor(self.agent)._dispatch({"tool": tool, "arguments": arguments},
                                                     getattr(self.agent, "_active_emit", None))

    def cleanup(self) -> None:
        # Agent dispatch cleans its own synthetic holds on the input worker, while
        # still holding the shared lane. Global cleanup here could release another
        # agent's keys or race a timed-out native call that is still draining.
        pass

    def record_verification(self, action_id: str, evidence: str, *, verified: bool = True) -> None:
        self.agent.orchestrator.verify(f"Typed control {action_id} postcondition", verified, evidence)
