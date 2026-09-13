from __future__ import annotations

import json
import os
import sys
from typing import Any


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def run_mission(goal: str) -> dict[str, Any]:
    if not goal.strip():
        return {"ok": False, "error": "Mission cannot be empty"}

    # Import first so .env loading completes, then configure this dedicated child
    # process for the explicit Full Access desktop profile.
    from .agent import JarvisAgent
    from .permissions import Risk

    os.environ["JARVIS_ACCESS_MODE"] = "full"
    os.environ["JARVIS_FULL_ACCESS_REQUIRE_APPROVAL"] = "true"

    agent = JarvisAgent()

    # Desktop/browser/workspace mutations are approved by the explicit session-level
    # Full Access opt-in. HIGH/CRITICAL tools still require a separate future approval
    # surface and therefore fail closed here. This keeps raw shell/destructive actions
    # out of unattended execution while preserving broad interactive device control.
    def approve(tool_name: str, _arguments: dict[str, Any]) -> bool:
        spec = agent.tools._tools.get(tool_name)
        if spec is None:
            return False
        if tool_name == "run_powershell":
            return False
        return spec.risk <= Risk.MEDIUM

    agent.approval = approve
    result = agent.run(goal)
    summary = agent.orchestrator.summary()
    current = agent.orchestrator.current
    status = str(summary.get("status", "unknown"))
    tools_used = int(summary.get("tools_used", 0) or 0)
    failures = int(summary.get("failures", 0) or 0)
    recoveries = int(summary.get("recoveries", 0) or 0)
    verifications = int(summary.get("verifications", 0) or 0)
    verified = bool(summary.get("verified", False))

    if current is not None and current.plan:
        incomplete = [step.id for step in current.plan if step.status not in {"completed", "skipped"}]
    else:
        incomplete = []

    ok = status == "completed" and tools_used > 0 and not incomplete
    if not ok and status == "completed" and tools_used == 0:
        status = "incomplete"

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
        "incomplete_steps": incomplete,
        "access_mode": "full",
        "high_risk_requires_separate_approval": True,
    }


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
