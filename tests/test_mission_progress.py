import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from core.autonomous_orchestrator import AutonomousTaskOrchestrator
from core.mission_progress import compact_task_graph


class MissionProgressTests(unittest.TestCase):
    def test_graph_is_emitted_before_mission_completion_and_transport_failure_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = AutonomousTaskOrchestrator(directory)
            snapshots = []
            orchestrator.on_task_graph = lambda nodes: snapshots.append(compact_task_graph(nodes))
            orchestrator.begin("fixture")
            orchestrator.set_plan(["Open fixture"])
            orchestrator.update_step("step-1", "running")
            self.assertEqual(snapshots[-1][0]["status"], "RUNNING")
            orchestrator.on_task_graph = Mock(side_effect=BrokenPipeError("disconnected"))
            orchestrator.record_tool("unreported_adapter", {}, "DELIVERED: fixture", 1, 1, mutation=True)
            self.assertEqual(orchestrator.current.tools_used, 1)
            self.assertGreater(orchestrator.current.metrics["progress_delivery_errors"], 0)

    def test_compact_payload_has_bounded_text_and_omits_tool_arguments(self):
        nodes = [{"id": "step-1", "action": "x" * 9000, "description": "fixture", "status": "RUNNING",
                  "dependencies": [], "result": "y" * 9000, "arguments": {"password": "never-send"}}]
        payload = compact_task_graph(nodes)
        self.assertEqual(len(payload[0]["action"]), 1000)
        self.assertEqual(len(payload[0]["result"]), 2000)
        self.assertNotIn("never-send", json.dumps(payload))

    def test_worker_stream_deduplicates_and_ignores_updates_after_stop(self):
        from core import full_access_worker as worker
        graph = [{"id": "step-1", "action": "fixture", "description": "fixture", "dependencies": [], "status": "RUNNING"}]
        def run(*args, **kwargs):
            emit = kwargs["task_graph_emit"]
            emit(graph)
            emit(graph)
            emit([{**graph[0], "status": "DELIVERED"}])
            worker._STOPPED.set()
            emit([{**graph[0], "status": "VERIFIED"}])
            return {"ok": True}
        worker._STOPPED.clear()
        try:
            with patch.object(worker, "_agent", return_value=Mock()), patch.object(worker, "run_agent_mission", side_effect=run), patch.object(worker, "_write") as write:
                result = worker._handle(json.dumps({"protocol": 1, "id": "fixture", "action": "run", "title": "fixture"}))
            self.assertTrue(result["ok"])
            packets = [call.args[0] for call in write.call_args_list]
            self.assertEqual([item["nodes"][0]["status"] for item in packets], ["RUNNING", "DELIVERED"])
            self.assertTrue(all(item["id"] == "fixture" and item["type"] == "task_graph" for item in packets))
        finally:
            worker._STOPPED.clear()


if __name__ == "__main__":
    unittest.main()
