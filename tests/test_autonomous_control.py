from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.desktop_input import _INPUT, _send_unicode_text, InputDeliveryError, paste_text
from core.desktop_observation import DesktopObservationGate
from core.orchestrator import TaskOrchestrator, tool_succeeded
from core.permissions import PermissionEngine, Risk
from core.task_tools import register_task_tools
from core.tools import ToolRegistry, ToolSpec


class DurableExecutionTests(unittest.TestCase):
    def test_checkpoint_captures_uncertain_action_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            task = state.begin("write a report")
            state.set_plan([{"id": "a", "description": "write"}])
            state.update_step("a", "running")
            state.start_action("write_file", {"password": "private-value", "text": "password=hunter2"})
            persisted = (Path(tmp) / f"{task.task_id}.json").read_text(encoding="utf-8")
            self.assertNotIn("private-value", persisted)
            self.assertNotIn("hunter2", persisted)
            restored = TaskOrchestrator(tmp).restore(task.task_id)
            self.assertEqual(restored.status, "interrupted")
            self.assertEqual(restored.plan[0].status, "pending")
            self.assertEqual(restored.in_flight["name"], "write_file")
            with self.assertRaises(ValueError):
                state.restore("../outside")

    def test_dependency_cycles_and_premature_steps_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("test")
            for plan in (
                [{"id": "a", "description": "a", "depends_on": ["a"]}],
                [{"id": "a", "description": "a", "depends_on": ["missing"]}],
                [{"id": "a", "description": "a"}, {"id": "a", "description": "duplicate"}],
            ):
                with self.assertRaises(ValueError):
                    state.set_plan(plan)
            state.set_plan([{"id": "a", "description": "a"}, {"id": "b", "description": "b", "depends_on": ["a"]}])
            with self.assertRaises(ValueError):
                state.update_step("b", "completed")
            state.update_step("a", "completed")
            state.update_step("b", "running")

    def test_new_mutation_invalidates_previous_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("test")
            state.record_tool("write", {}, "done", 1, 1, mutation=True)
            self.assertTrue(state.needs_action_review())
            state.record_tool("read", {}, "actual", 1, 2)
            state.verify("saved", True, "actual")
            self.assertTrue(state.all_required_verifications_passed())
            state.record_tool("write", {}, "done", 1, 3, mutation=True)
            self.assertFalse(state.all_required_verifications_passed())
            state.verify("saved", False, "mismatch")
            self.assertFalse(state.all_required_verifications_passed())
            state.verify("saved", True, "fixed and read back")
            self.assertTrue(state.all_required_verifications_passed())

    def test_failed_process_and_browser_guard_are_not_success(self):
        for result in ("exit_code=1\nfailed", "cwd=x\nexit_code=-9", "BROWSER_ACTION_BLOCKED: captcha", "CANCELLED: stop"):
            self.assertFalse(tool_succeeded(result))
        self.assertTrue(tool_succeeded("exit_code=0\nokay"))

    def test_verification_requires_observed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("test")
            registry = ToolRegistry()
            register_task_tools(registry, state)
            state.record_tool("write_file", {}, "written", 1, 1, mutation=True)
            args = {"claim": "saved", "verified": True, "evidence": "intended value"}
            self.assertTrue(registry.execute("task_verify", args).startswith("ERROR"))
            state.record_tool("read_file", {}, "actual value", 1, 2)
            self.assertTrue(json.loads(registry.execute("task_verify", args))["verified"])

    def test_recall_reads_previous_checkpoint_without_replaying_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            old = state.begin("original task")
            state.start_action("write_file", {"path": "report"})
            state.begin("continue")
            current_id = state.current.task_id
            registry = ToolRegistry()
            register_task_tools(registry, state)
            result = json.loads(registry.execute("task_recall", {"task_id": old.task_id}))
            self.assertEqual(result["uncertain_action"]["name"], "write_file")
            self.assertEqual(state.current.task_id, current_id)

    def test_retry_limit_requires_new_successful_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("retry")
            for turn in range(3):
                state.record_tool("action", {}, "ERROR: failed", 1, turn)
            self.assertTrue(state.repeated_failure("action", {}))
            state.record_tool("observe", {}, "new evidence", 1, 4)
            self.assertFalse(state.repeated_failure("action", {}))


class InputPrecisionTests(unittest.TestCase):
    def test_sendinput_uses_full_abi_and_unicode_surrogates(self):
        self.assertEqual(ctypes.sizeof(_INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        calls = []
        def send(count, inputs, size):
            calls.append((count, [item.ki.wScan for item in inputs], size))
            return count
        with patch("core.desktop_input.ctypes.windll", SimpleNamespace(user32=SimpleNamespace(SendInput=send)), create=True):
            _send_unicode_text("A\U0001f600\r\n")
        self.assertEqual(calls[0][1], [65, 65, 0xD83D, 0xD83D, 0xDE00, 0xDE00, 13, 13])

    def test_partial_input_never_falls_back_and_duplicates_text(self):
        with patch("core.desktop_input._send_unicode_text", side_effect=InputDeliveryError("partial")), patch("core.desktop_input._clipboard_paste") as clipboard:
            with self.assertRaises(InputDeliveryError):
                paste_text("hello")
            clipboard.assert_not_called()

    def test_observation_bounds_freshness_and_foreground(self):
        gate = DesktopObservationGate()
        frame = {"foreground_hwnd": 7, "virtual_origin_x": -1920, "virtual_origin_y": 0, "source_width": 3840, "source_height": 1080}
        with patch("core.desktop_observation.foreground_identity", return_value=7):
            gate.observe(frame)
            gate.check({"x": -100, "y": 500})
            for point in ({"x": 1920, "y": 0}, {"x": True, "y": 2}, {"x": 1, "y": 1080}):
                with self.assertRaises(ValueError):
                    gate.check(point)
            gate.observed_at -= 11
            with self.assertRaises(ValueError):
                gate.check({})
        gate.observe(frame)
        with patch("core.desktop_observation.foreground_identity", return_value=8), self.assertRaises(ValueError):
            gate.check({})

    def test_invalid_arguments_never_reach_handler(self):
        registry = ToolRegistry()
        handler = Mock(return_value="okay")
        registry.register(ToolSpec("bounded", "test", Risk.SAFE, {"type": "object", "properties": {"x": {"type": "integer", "minimum": 0}}, "required": ["x"], "additionalProperties": False}, handler))
        for arguments in ({"x": -1}, {"x": True}, {"x": 1, "unexpected": 2}):
            self.assertTrue(registry.execute("bounded", arguments).startswith("ERROR"))
        handler.assert_not_called()

    def test_scene_is_checked_again_at_dispatch(self):
        gate = DesktopObservationGate()
        gate.observe({"foreground_hwnd": 7, "virtual_origin_x": 0, "virtual_origin_y": 0,
                      "source_width": 100, "source_height": 100, "scene_bound": True, "sha256": "fixture"})
        with patch("core.desktop_observation.foreground_identity", return_value=7), patch("core.desktop_observation.scene_matches", return_value=False), self.assertRaises(ValueError):
            gate.check({"x": 20, "y": 20})
        self.assertIsNone(gate.frame)

    def test_capture_is_stable_bounded_and_digest_checked(self):
        import core.vision_tools as vision
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp, patch.object(vision, "_workspace", return_value=Path(tmp)), patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (100, 100), "blue")), patch("core.desktop_observation.foreground_identity", return_value=7), patch("core.vision_tools.time.sleep"), patch("core.vision_tools.ctypes.windll"):
            for _ in range(10):
                result = vision.screen_observe()
            payload = vision._payload_from_result(result)
            self.assertTrue(payload["stable"])
            self.assertEqual(payload["foreground_hwnd"], 7)
            self.assertEqual(len(list((Path(tmp) / ".jarvis/vision").glob("screen-*.jpg"))), 8)
            self.assertIsNotNone(vision.vision_followup_message(result))
            Path(payload["path"]).write_bytes(b"tampered")
            self.assertIsNone(vision.vision_followup_message(result))

    def test_restricted_mode_denies_raw_input_even_with_approval(self):
        engine = PermissionEngine()
        engine.set_access_mode("restricted")
        for name in ("desktop_click", "desktop_move", "chrome_new_tab", "google_search", "directory_create"):
            self.assertFalse(engine.check(name, Risk.LOW, approved=True)[0], name)


class FullAgentFlowTests(unittest.TestCase):
    def test_mutation_requires_review_before_next_action(self):
        from core.full_access_agent import FullAccessJarvisAgent
        with tempfile.TemporaryDirectory() as tmp:
            agent = object.__new__(FullAccessJarvisAgent)
            agent._stop = threading.Event()
            agent._desktop_observation = DesktopObservationGate()
            agent.orchestrator = TaskOrchestrator(tmp)
            agent.tools = ToolRegistry()
            agent.tools.permissions.set_access_mode("full")
            mutation = Mock(return_value="Changed value")
            agent.tools.register(ToolSpec("test_write", "write", Risk.MEDIUM, {"type": "object"}, mutation))
            agent.tools.register(ToolSpec("test_read", "read", Risk.LOW, {"type": "object"}, lambda: "value = expected"))
            register_task_tools(agent.tools, agent.orchestrator)
            agent.approval = lambda *_: True
            agent._browser_action_guard = lambda *_: None
            agent._system_prompt = lambda *_: "test"
            agent.workspace_context = Mock()
            agent.memory = Mock()
            agent.messages = []
            agent.max_turns = 8
            agent.model = "fixture"
            def response(name=None, arguments=None):
                calls = [SimpleNamespace(id=f"call-{name}", function=SimpleNamespace(name=name, arguments=json.dumps(arguments or {})))] if name else []
                message = SimpleNamespace(content="Done", tool_calls=calls, model_dump=lambda **_: {"role": "assistant", "content": "Done"})
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])
            agent.client = Mock()
            agent.client.chat.completions.create.side_effect = [response("test_write"), response("test_write"), response("test_read"), response("task_verify", {"claim": "value updated", "verified": True, "evidence": "read returned expected"}), response()]
            with ThreadPoolExecutor(max_workers=1) as executor, patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
                agent._tool_executor = executor
                self.assertEqual(agent.run("update value and verify"), "Done")
            self.assertEqual(mutation.call_count, 1)
            self.assertTrue(agent.orchestrator.summary()["verified"])
            agent.request_stop()
            self.assertTrue(agent._execute_tool("test_write", {}, True).startswith("CANCELLED"))


class ProcessAndWorkerTests(unittest.TestCase):
    def tearDown(self):
        from core.process_control import set_cancellation
        set_cancellation(lambda: False)

    def test_command_exit_and_timeout(self):
        from core.process_control import run_command
        self.assertIn("exit_code=7", run_command([sys.executable, "-c", "raise SystemExit(7)"]))
        result = run_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
        self.assertTrue(result.startswith("ERROR:"))

    def test_cancelled_command_is_never_started(self):
        from core.process_control import run_command, set_cancellation
        set_cancellation(lambda: True)
        with patch("core.process_control.subprocess.Popen") as spawn:
            self.assertTrue(run_command(["unreachable"]).startswith("CANCELLED:"))
            spawn.assert_not_called()

    def test_worker_stop_does_not_wait_for_blocked_mission(self):
        script = "import time; import core.full_access_worker as w; w._handle=lambda raw: time.sleep(30); w.main()"
        process = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate('{"protocol":1,"id":"a","action":"run","title":"fixture"}\n{"protocol":1,"action":"stop"}\n', timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn('"type": "stopped"', stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


class WorkspaceFileTests(unittest.TestCase):
    def test_copy_verifies_bytes_and_refuses_overwrite_and_escape(self):
        from core.filesystem_tools import directory_create, file_copy
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"JARVIS_WORKSPACE": tmp}):
            (Path(tmp) / "source").write_bytes(b"expected")
            directory_create("output")
            self.assertTrue(file_copy("source", "output/copy").startswith("VERIFIED:"))
            self.assertEqual((Path(tmp) / "output/copy").read_bytes(), b"expected")
            with self.assertRaises(FileExistsError):
                file_copy("source", "output/copy")
            with self.assertRaises(PermissionError):
                file_copy("source", "../escape")


if __name__ == "__main__":
    unittest.main()
