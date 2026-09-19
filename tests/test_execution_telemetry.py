import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from core.autonomous_orchestrator import AutonomousTaskOrchestrator
from core.execution_telemetry import ToolResult, begin_execution, finish_execution, record_backend
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec


class ExecutionTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.permissions.check = Mock(return_value=(True, "fixture"))

    def register(self, name, handler):
        self.registry.register(ToolSpec(name, "fixture", Risk.LOW,
            {"type": "object", "additionalProperties": False}, handler))

    def test_nested_dispatch_and_string_compatibility(self):
        def inner():
            record_backend("rust_native", detail="type_text")
            return "DELIVERED: typed"
        def outer():
            record_backend("windows_uia", phase="resolve")
            self.registry.execute("inner", {})
            record_backend("windows_uia", phase="verify")
            return "VERIFIED: exact text"
        self.register("inner", inner)
        self.register("outer", outer)
        result = self.registry.execute("outer", {})
        self.assertTrue(result.startswith("VERIFIED:"))
        self.assertEqual(result.execution["backend"], "rust_native")
        self.assertEqual(len(result.execution["operations"]), 3)
        self.assertEqual(copy.deepcopy(result).execution, result.execution)

    def test_denied_invalid_and_uninstrumented_tools_do_not_claim_rust(self):
        handler = Mock(return_value="RUST_EXECUTED: fixture")
        self.register("desktop_type", handler)
        self.assertEqual(self.registry.execute("desktop_type", {}).execution["backend"], "unreported")
        self.assertEqual(self.registry.execute("desktop_type", {"extra": 1}).execution["backend"], "not_dispatched")
        self.registry.permissions.check.return_value = (False, "disabled")
        self.assertEqual(self.registry.execute("desktop_type", {}).execution["backend"], "not_dispatched")
        self.assertEqual(handler.call_count, 1)

    def test_error_keeps_attempted_adapter_and_resets_context(self):
        def fail():
            record_backend("rust_native", detail="click")
            raise RuntimeError("uncertain delivery")
        self.register("fail", fail)
        self.register("read", lambda: "ok")
        result = self.registry.execute("fail", {})
        self.assertIn("uncertain", result)
        self.assertEqual(result.execution["backend"], "rust_native")
        self.assertEqual(self.registry.execute("read", {}).execution["operations"], [])

    def test_concurrent_reads_have_isolated_spans(self):
        barrier = threading.Barrier(2)
        def call(engine):
            span, token = begin_execution()
            barrier.wait(timeout=2)
            record_backend(engine, phase="observe")
            return finish_execution(span, token, "read", dispatched=True).execution
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(call, ["windows_uia", "chrome_cdp"]))
        self.assertEqual([item["backend"] for item in results], ["windows_uia", "chrome_cdp"])

    def test_cdp_owner_thread_preserves_caller_telemetry(self):
        from core.chrome_cdp import _ChromeRuntime
        from core.process_control import set_cancellation
        set_cancellation(lambda: False)
        runtime = _ChromeRuntime()
        runtime._page = Mock(url="https://fixture.invalid/")
        runtime._page.title.return_value = "fixture"
        self.register("read", lambda: str(runtime.call("current")))
        try:
            result = self.registry.execute("read", {})
            self.assertEqual(result.execution["backend"], "chrome_cdp")
        finally:
            # shutdown uses its own queue envelope and has no real browser to close.
            import queue
            from contextvars import copy_context
            runtime._commands.put(("shutdown", {}, queue.Queue(), threading.Event(), copy_context()))
            runtime._thread.join(timeout=2)
        self.assertFalse(runtime._thread.is_alive())

    def test_readback_does_not_replace_mutation_backend_and_checkpoint_keeps_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = AutonomousTaskOrchestrator(directory)
            task = orchestrator.begin("Type text")
            orchestrator.set_plan(["Type text"])
            orchestrator.update_step("step-1", "running")
            result = ToolResult("DELIVERED: typed", {"backend": "python_native", "operations": [
                {"engine": "windows_uia", "phase": "resolve", "detail": "editor"},
                {"engine": "python_native", "phase": "execute", "detail": "type"}]})
            orchestrator.record_tool("ui_type_native", {}, result, 2, 1, mutation=True)
            orchestrator.record_tool("ui_inspect", {}, ToolResult("read", {"backend": "windows_uia", "operations": []}), 1, 1)
            self.assertEqual(orchestrator.live_task_graph()[0]["execution_backend"], "python_native")
            restored = AutonomousTaskOrchestrator(directory)
            restored.restore(task.task_id)
            self.assertEqual(restored.current.traces[0].operations, result.execution["operations"])
            self.assertEqual(restored.current.traces[0].resolution_backend, "windows_uia")


if __name__ == "__main__":
    unittest.main()
