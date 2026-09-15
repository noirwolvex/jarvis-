from __future__ import annotations

import json
import os
import sys
from typing import Any


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def build_full_access_agent():
    """Build one fully routed Full Access agent. Persistent workers may safely reuse it between missions."""
    # Import first so .env loading completes, then configure this dedicated local profile.
    from .app_discovery_cache import enable_app_discovery_cache
    from .app_discovery_fast import enable_fast_app_discovery
    from .app_tools import register_app_tools
    from .browser_fast_tools import register_browser_fast_tools
    from .browser_tab_tools import register_browser_tab_tools
    from .chrome_session_tools import register_chrome_session_tools
    from .desktop_control_tools import register_desktop_control_tools
    from .discord_tools import register_discord_tools
    from .fast_execution_agent import FastExecutionFullAccessAgent
    from .full_access_browser_routing import register_full_access_browser_routing
    from .permissions import Risk
    from .rust_engine import register_rust_engine_tools
    from .semantic_ui_tools import register_semantic_ui_tools
    from .vision_tools import register_vision_tools
    from .filesystem_tools import register_filesystem_tools
    from .youtube_fast_tools import register_youtube_fast_tools

    # Resolve exact/common apps from bounded Windows sources first; only ambiguous names
    # fall through to the exhaustive install-tree scan. Cache the resulting resolver.
    enable_fast_app_discovery()
    enable_app_discovery_cache()

    agent = FastExecutionFullAccessAgent()
    agent.tools.permissions.full_access_require_approval = True
    agent.tools.permissions.set_access_mode("full")
    register_app_tools(agent.tools)
    register_browser_tab_tools(agent.tools)
    register_chrome_session_tools(agent.tools)
    register_browser_fast_tools(agent.tools)
    register_youtube_fast_tools(agent.tools)
    register_desktop_control_tools(agent.tools)
    register_semantic_ui_tools(agent.tools)
    register_discord_tools(agent.tools)
    register_vision_tools(agent.tools)
    register_filesystem_tools(agent.tools)
    # Overlay native desktop mutations after the normal Python tools exist. Auto mode
    # keeps the Python path as a pre-dispatch fallback when the Rust daemon is not ready;
    # strict rust mode fails closed instead of silently bypassing the daemon.
    register_rust_engine_tools(agent.tools)
    # Register last so browser_navigate/open_url/Chrome app launch cannot fall back
    # to a second unmanaged browser after the guarded CDP tools are installed.
    register_full_access_browser_routing(agent.tools)

    # Desktop/browser/workspace mutations are approved by explicit session-level Full Access.
    # HIGH/CRITICAL tools still require a separate approval surface and fail closed here.
    def approve(tool_name: str, _arguments: dict[str, Any]) -> bool:
        spec = agent.tools._tools.get(tool_name)
        if spec is None:
            return False
        if tool_name == "run_powershell":
            return bool(getattr(agent, "allow_shell", False))
        return spec.risk <= Risk.MEDIUM

    agent.approval = approve
    return agent


def run_agent_mission(agent, goal: str, emit=None, cancel_event=None, allow_shell: bool = False) -> dict[str, Any]:
    if not goal.strip():
        return {"ok": False, "error": "Mission cannot be empty"}

    from .desktop_control_tools import release_held_inputs
    from .full_access_completion import read_only_observation_verified

    # Reuse the expensive provider/client/tool/runtime objects, but never leak chat tool-call
    # protocol state from one mission into the next. Long-term MemoryStore remains intentional.
    agent.reset()
    agent.cancel_event = cancel_event
    agent.allow_shell = allow_shell
    from .process_control import set_cancellation
    set_cancellation(agent._is_stopped)
    try:
        result = agent.run(goal, emit=emit)
    finally:
        # Synthetic held keys/buttons are useful inside a multi-step gesture, but must never
        # survive mission completion, failure, CAPTCHA pause, or model/tool exceptions.
        release_held_inputs()

    summary = agent.orchestrator.summary()
    current = agent.orchestrator.current
    status = str(summary.get("status", "unknown"))
    tools_used = int(summary.get("tools_used", 0) or 0)
    failures = int(summary.get("failures", 0) or 0)
    recoveries = int(summary.get("recoveries", 0) or 0)
    verifications = int(summary.get("verifications", 0) or 0)
    verified = bool(summary.get("verified", False))
    observation_verified = read_only_observation_verified(goal, current)

    if current is not None and current.plan:
        incomplete = [step.id for step in current.plan if step.status not in {"completed", "skipped"}]
    else:
        incomplete = []

    requires_user_action = status == "waiting_user"
    completion_evidence = verified or observation_verified
    mission_completed = status == "completed" and tools_used > 0 and not incomplete and completion_evidence
    ok = requires_user_action or mission_completed

    if status == "completed" and not mission_completed:
        status = "incomplete"
        if not completion_evidence:
            result = f"{result} Verification is required before Full Access reports mission completion."

    return {
        "ok": ok,
        "action": "full_access_mission",
        "status": status,
        "result": result,
        "task_id": str(summary.get("task_id", "")),
        "tools_used": tools_used,
        "failures": failures,
        "recoveries": recoveries,
        "verifications": verifications,
        "verified": verified,
        "read_only_observation_verified": observation_verified,
        "incomplete_steps": incomplete,
        "access_mode": "full",
        "requires_user_action": requires_user_action,
        "mission_completed": mission_completed,
        "high_risk_requires_separate_approval": True,
    }


def run_mission(goal: str) -> dict[str, Any]:
    return run_agent_mission(build_full_access_agent(), goal)


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "run":
        _emit({"ok": False, "error": "Usage: python -m core.full_access_bridge run <mission>"})
        return 2
    mission = sys.argv[2]
    try:
        payload = run_mission(mission)
    except Exception as exc:  # fail closed and keep stdout machine-readable
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    _emit(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
