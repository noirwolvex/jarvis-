"""Owned browser/vision qualification for the typed engines; no model calls."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.control_engines import Action, AgentProvider, CancellationToken, Check, ControlSession, RetryPolicy
from core.control_engines.integration import register_control_engines
from core.desktop_observation import DesktopObservationGate
from core.full_access_agent import FullAccessJarvisAgent
from core.mission_control import MissionControl
from core.orchestrator import TaskOrchestrator


def qualify_typed_engines(registry, runtime, directory, base):
    """Real CDP effects through the same agent/permission/journal path as production."""
    agent = FullAccessJarvisAgent.__new__(FullAccessJarvisAgent)
    agent.tools = registry
    agent.orchestrator = TaskOrchestrator(str(directory / "typed-traces"))
    agent.orchestrator.begin("Owned typed control qualification")
    agent._stop = threading.Event()
    agent._device_action_active = threading.Event()
    agent._desktop_observation = DesktopObservationGate()
    agent.approval = lambda *_: True  # Only this isolated fixture registry.
    agent.require_action_confirmation = True
    confirmations = []

    def confirm_fixture(snapshot):
        request = snapshot.get("pendingConfirmation")
        if not request or request["id"] in confirmations:
            return
        expected = {"surface": "browser", "browser_target": {"role": "button", "name": "Apply"}}
        if (request["tool"] != "interaction_click" or json.loads(request["details"]) != expected
                or runtime.call("page", operation="url") != base + "/"):
            raise AssertionError("Typed fixture confirmation left its owned scope")
        confirmations.append(request["id"])
        agent.mission_control.command("confirm", request["id"])

    agent.mission_control = MissionControl(confirm_fixture, confirmation_timeout=2)
    register_control_engines(agent)
    runtime.call("page", operation="goto", url=base + "/")
    draft = {"surface": "browser", "browser_target": {"role": "textbox", "name": "Draft"}}
    label = "Typed JARVIS مرحبا"
    actions = [
        Action("inspect", "inspect", {"surface": "browser"}),
        Action("focus", "focus", target=draft, expected_outcome=Check("interaction_wait", {**draft, "state": "focused"})),
        Action("type", "type_text", {"text": label, "replace": True}, target=draft,
               expected_outcome=Check("interaction_wait", {**draft, "state": "text", "text": label}), dependencies=("focus",)),
        Action("click", "click", target={"surface": "browser", "browser_target": {"role": "button", "name": "Apply"}},
               expected_outcome=Check("interaction_wait", {"surface": "browser", "browser_target": {"selector": "#result"},
                                                          "state": "text", "text": "Clicked once"}), dependencies=("type",)),
        Action("navigate", "click", target={"surface": "browser", "browser_target": {"role": "link", "name": "Next page"}},
               expected_outcome=Check("interaction_wait", {"surface": "browser", "browser_target": {"selector": "h1"},
                                                          "state": "text", "text": "Navigation verified"}), dependencies=("click",)),
    ]
    started = time.perf_counter()
    with ThreadPoolExecutor(1) as executor, ControlSession(AgentProvider(agent)) as session:
        agent._tool_executor = executor
        report = session.run(actions)
        if not report["completed"]:
            raise AssertionError(json.dumps(report, ensure_ascii=False))
        trace_count = len(agent.orchestrator.current.traces)
        session.run(actions)
        if len(agent.orchestrator.current.traces) != trace_count:
            raise AssertionError("Typed action replayed an existing action ID")
        missing = Check("interaction_wait", {"surface": "browser", "browser_target": {"role": "button", "name": "Absent control"},
                                             "state": "visible", "timeout_ms": 0})
        partial = session.run([
            Action("missing", "click", target={"surface": "browser", "browser_target": {"role": "button", "name": "Absent control"}},
                   preconditions=(missing,), retry_policy=RetryPolicy(2, 0)),
            Action("not_run", "click", target=draft),
        ])
        if partial["completed"] or partial["remaining"] != ["not_run"] or partial["actions"][0]["retry_count"] != 1:
            raise AssertionError("Missing target did not preserve partial completion")
        token = CancellationToken()
        token.cancel()
        cancelled = session.execute(Action("cancelled", "inspect", {"surface": "browser"}), token)
        session._pending.result(timeout=1)
        if cancelled.status.value != "CANCELLED":
            raise AssertionError("Pre-cancelled control action executed")
    if runtime.call("page", operation="url") != base + "/next" or len(confirmations) != 1:
        raise AssertionError("Typed workflow outcome or confirmation count mismatch")
    return {"verified_steps": len(actions), "resume_replays": 0, "model_calls": 0,
            "missing_target_safely_blocked": True, "cancelled_without_input": True,
            "one_time_confirmations": len(confirmations), "duration_ms": round((time.perf_counter() - started) * 1000, 2)}


def qualify_local_ocr():
    """Actual Windows OCR over a generated image, never the user's screen."""
    from PIL import Image, ImageDraw, ImageFont
    from core.control_engines.vision import WindowsOCR
    image = Image.new("RGB", (700, 110), "white")
    ImageDraw.Draw(image).text((15, 15), "JARVIS CONTROL READY", fill="black", font=ImageFont.truetype("arial.ttf", 32))
    started = time.perf_counter()
    rows = WindowsOCR().recognize(image, language="en-US", timeout=3)
    if "JARVIS CONTROL READY" not in " ".join(row.text for row in rows):
        raise AssertionError("Local OCR did not recognize the generated fixture")
    return {"recognized": True, "localized_regions": len(rows), "local_only": True,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2)}


if __name__ == "__main__":
    print(json.dumps(qualify_local_ocr()))
