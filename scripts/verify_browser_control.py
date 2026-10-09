"""Opt-in live CDP qualification against isolated loopback test pages only.

Run: python -m scripts.verify_browser_control
Uses an existing Chrome installation with a temporary headless profile. No personal
tabs, cookies, accounts, messages or external destinations are used.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from core import chrome_cdp
from core.browser_semantic import register_browser_semantic_tools
from core.interaction_scene import register_interaction_scene_tools
from core.desktop_observation import DesktopObservationGate
from core.computer_perception import register_perception_tools
from core.mission_control import MissionControl
from core.full_access_agent import FullAccessJarvisAgent
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec
from core.universal_interaction import register_universal_interaction_tools
from core.workflow_tools import register_workflow_tools
from scripts.jarvis_runtime import _stop_dashboard
from scripts.interface_qualification_fixture import INTERFACE_HTML, qualify_interface, qualify_challenge_stop


class FixturePage(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass  # The owned browser may close an idle connection during cleanup.

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/interface":
            content = INTERFACE_HTML
        elif self.path == "/challenge":
            content = '''<h1>Human verification</h1><button onclick="fetch('/interface-event',{
                method:'POST',body:JSON.stringify({kind:'challenge-click'})})">Verify</button>'''
        elif self.path == "/next":
            content = '<h1>Navigation verified</h1><a href="/">Return</a>'
        elif self.path == "/frame":
            content = '<label>Frame draft<input aria-label="Frame draft"></label>'
        else:
            content = '''<label>Draft<input aria-label="Draft"></label>
            <label>Search<input type="search" aria-label="Search"></label>
            <button onclick="document.querySelector('#result').textContent='Clicked once';this.disabled=true">Apply</button>
            <div id="result"></div><a href="/next">Next page</a>
            <iframe id="child" src="/frame" title="Test frame"></iframe>
            <div style="height:2400px">Scroll test area</div>'''
        data = ('<!doctype html><meta charset="utf-8"><title>JARVIS Control Fixture</title>' + content).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        if self.path != '/interface-event' or not 0 < length <= 8192:
            self.send_error(400)
            return
        self.server.interface_events.append(json.loads(self.rfile.read(length)))
        self.send_response(204)
        self.end_headers()


def verify_ordered_workflow(registry, runtime, directory: Path, base: str) -> dict:
    """Run the real dispatcher/journal without constructing a network model client."""
    agent = FullAccessJarvisAgent.__new__(FullAccessJarvisAgent)
    agent.tools = registry
    agent.orchestrator = TaskOrchestrator(str(directory / "workflow-journal"))
    agent.orchestrator.strict_order = True
    agent.orchestrator.begin("Type fixture drafts, apply, navigate, return, and type again")
    agent.approval = lambda *_: True  # Isolated fixture tools only, inside Full Access.
    agent._stop = threading.Event()
    agent._device_action_active = threading.Event()
    agent._desktop_observation = DesktopObservationGate()
    agent.require_action_confirmation = True
    confirmations = []
    def confirm_owned_fixture(snapshot):
        request = snapshot.get("pendingConfirmation")
        if request is None or request["id"] in confirmations:
            return
        # This test responder has authority ONLY for the isolated loopback
        # fixture's Apply button. It must never become a runtime auto-approver.
        expected = {"surface": "browser", "browser_target": {"role": "button", "name": "Apply"}}
        if request["tool"] != "interaction_click" or json.loads(request["details"]) != expected:
            raise AssertionError("Unexpected confirmation in the owned browser test")
        if runtime.call("page", operation="url") != base + "/":
            raise AssertionError("Confirmation left the owned loopback fixture")
        confirmations.append(request["id"])
        agent.mission_control.command("confirm", request["id"])
    agent.mission_control = MissionControl(confirm_owned_fixture, confirmation_timeout=2)
    model_calls = 0
    def unexpected_model_call(**_kwargs):
        nonlocal model_calls
        model_calls += 1
        raise RuntimeError("A deterministic fixture workflow must not contact a model")
    agent._chat_completion = unexpected_model_call
    registry.register(ToolSpec("browser_check_challenge", "Inspect the owned browser fixture", Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda: json.dumps(runtime.call("page", operation="challenge_state"))))
    register_workflow_tools(agent)
    register_perception_tools(registry)
    runtime.call("page", operation="goto", url=base + "/")

    def call(tool, target, **arguments):
        return {"tool": tool, "arguments": {"surface": "browser", "browser_target": target, **arguments}}

    draft = {"role": "textbox", "name": "Draft"}
    calls = [
        call("interaction_type", draft, text="Replace this placeholder", replace=True),
        call("interaction_hotkey", draft, keys=["Control", "a"]),
        call("interaction_type", draft, text=" Workflow مرحبا ", replace=True),
        call("interaction_wait", draft, state="text", text=" Workflow مرحبا ", timeout_ms=0),
        call("interaction_focus", draft),
        call("interaction_wait", draft, state="focused", timeout_ms=0),
        call("interaction_type", {"role": "searchbox", "name": "Search"}, text="fixture search", replace=True),
        {**call("interaction_click", {"role": "button", "name": "Apply"}),
         "checkpoint": call("interaction_wait", {"selector": "#result"}, state="text", text="Clicked once")},
        {**call("interaction_click", {"role": "link", "name": "Next page"}),
         "checkpoint": call("interaction_wait", {"selector": "h1"}, state="text", text="Navigation verified")},
        {**call("interaction_click", {"role": "link", "name": "Return"}),
         "checkpoint": call("interaction_wait", draft, state="visible")},
        call("interaction_type", draft, text="Returned successfully", replace=True),
    ]
    steps = [{"id": f"step{index}", "description": item["tool"], **item}
             for index, item in enumerate(calls, 1)]
    arguments = {"workflow_id": "live-browser-fixture", "steps": steps}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as executor:
        agent._tool_executor = executor
        observed = agent._execute_tool("computer_observe", {"surface": "browser", "visual": "never"}, approved=True)
        if not observed.startswith("VERIFIED:") or not json.loads(observed[len("VERIFIED: "):])["semantic"]["nodes"]:
            raise RuntimeError("Live combined perception did not resolve the owned controls")
        result = agent._execute_tool("workflow_execute", arguments, approved=True)
        if not result.startswith("VERIFIED:"):
            raise RuntimeError(f"Live ordered workflow failed: {result}")
        trace_count = len(agent.orchestrator.current.traces)
        resumed = agent._execute_tool("workflow_execute", arguments, approved=True)
        if not resumed.startswith("VERIFIED:") or len(agent.orchestrator.current.traces) != trace_count:
            raise RuntimeError("Completed workflow replayed actions during resume")
    if runtime.call("page", operation="url") != base + "/":
        raise RuntimeError("Ordered workflow did not return to the fixture page")
    if len(confirmations) != 1:
        raise RuntimeError("Consequential fixture activation did not request exactly one confirmation")
    return {"steps": len(steps), "completed": True, "model_calls": model_calls, "resume_replays": 0,
            "one_time_confirmations": len(confirmations), "semantic_perception": True,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2)}


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixturePage)
    server.interface_events = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = None
    runtime = None
    timings = {}
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-browser-qualification-") as directory:
            profile = Path(directory).resolve()
            try:
                process = subprocess.Popen([
                    chrome_cdp._chrome_executable(), "--headless=new", "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "about:blank",
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                deadline = time.monotonic() + 15
                active_port = profile / "DevToolsActivePort"
                while True:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Isolated Chrome did not expose CDP")
                    try:
                        port = int(active_port.read_text().splitlines()[0])
                        break
                    except (OSError, ValueError, IndexError):
                        pass  # Chrome briefly holds this file while publishing it.
                    time.sleep(0.05)
                runtime = chrome_cdp._runtime()
                runtime.call("connect", endpoint=f"http://127.0.0.1:{port}", session_type="managed")
                base = f"http://127.0.0.1:{server.server_port}"
                runtime.call("page", operation="goto", url=base + "/")
                registry = ToolRegistry()
                registry.permissions.set_access_mode("full")
                register_browser_semantic_tools(registry)
                register_universal_interaction_tools(registry)
                register_interaction_scene_tools(registry)

                def action(name, **arguments):
                    started = time.perf_counter()
                    result = registry.execute(name, arguments, approved=True)
                    timings[name] = round((time.perf_counter() - started) * 1000, 2)
                    if str(result).startswith(("ERROR", "PERMISSION_DENIED")):
                        raise RuntimeError(str(result))
                    return str(result)

                def resolve(query, **extra):
                    raw = action("interaction_resolve", surface="browser", query=query, **extra)
                    return json.loads(raw.removeprefix("VERIFIED: "))["action_target"]

                draft = resolve("Draft")
                typed = action("interaction_type", text="JARVIS مرحبا", replace=True, **draft)
                if not typed.startswith("VERIFIED:"):
                    raise RuntimeError("Browser typing lacked exact readback")
                action("interaction_type", text=" + appended", **resolve("Draft"))
                action("interaction_hotkey", keys=["ctrl", "a"], **resolve("Draft"))
                action("interaction_type", text="Frame text", replace=True, **resolve("Frame draft", frame_selector="#child"))
                action("interaction_click", **resolve("Apply"))
                action("interaction_wait", surface="browser", browser_target={"selector": "#result"}, state="text", text="Clicked once")
                scrolled = action("interaction_scroll", surface="browser", delta_y=450)
                if not scrolled.startswith("VERIFIED:"):
                    raise RuntimeError("Browser scroll position did not change")
                action("interaction_scroll", surface="browser", delta_y=-450)
                action("interaction_click", **resolve("Next page"))
                action("interaction_wait", surface="browser", browser_target={"selector": "h1"}, state="text", text="Navigation verified")
                if runtime.call("page", operation="url") != base + "/next":
                    raise RuntimeError("Next-page URL did not verify")
                workflow = verify_ordered_workflow(registry, runtime, profile, base)
                interface_matrix = qualify_interface(registry, runtime, base, server.interface_events)
                tabs = runtime.call("tabs")
                if len(tabs) != 1:
                    raise RuntimeError("Unexpected duplicate tabs")
                # Qualify explicit tab creation against the same real Chrome window.
                # The runtime verifies the CDP window ID before navigating this target.
                created = runtime.call("new_tab", url=base + "/next")
                if not created["verified"] or len(runtime.call("tabs")) != 2:
                    raise RuntimeError("Explicit same-window new tab was not verified")
                focused = runtime.call("focus_selected")
                selected = runtime.call("current")
                if not focused["verified"] or selected["url"] != base + "/next":
                    raise RuntimeError("Current-window selection did not preserve the selected tab")
                # Some headless Chrome builds mark every page focused/visible. Such
                # an inventory must be rejected rather than choosing the last page.
                try:
                    current = runtime.call("select_current_window")
                    if current["url"] != base + "/next":
                        raise AssertionError("Current-window selection chose another page")
                    selection_status = "selected_current"
                except RuntimeError as exc:
                    if "ambiguous or changed" not in str(exc):
                        raise
                    try:
                        runtime.call("current")
                    except RuntimeError as missing:
                        if "No browser tab is selected" not in str(missing):
                            raise
                    else:
                        raise AssertionError("Ambiguous focus retained an unsafe selected page")
                    selection_status = "ambiguous_headless_focus_rejected"
                # Explicitly select the known owned tab after the ambiguity test.
                # Challenge qualification runs last; only browser cleanup follows.
                runtime.call("use_tab", index=0)
                challenge_stop = qualify_challenge_stop(registry, runtime, base, server.interface_events)
                print("LIVE_BROWSER_QUALIFICATION_OK " + json.dumps({
                    "backend": "chrome_cdp", "typing": True, "iframe_typing": True,
                    "click": True, "scroll": True, "navigation": True, "tabs": len(tabs),
                    "explicit_same_window_new_tab": True, "current_tab_selection": selection_status,
                    "ordered_workflow": workflow,
                    "interface_matrix": interface_matrix,
                    "human_verification_stop": challenge_stop,
                    "last_action_duration_ms": timings,
                }), flush=True)
            finally:
                try:
                    if runtime is not None:
                        runtime.call("shutdown")
                finally:
                    chrome_cdp._RUNTIME = None
                    _stop_dashboard(process)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
