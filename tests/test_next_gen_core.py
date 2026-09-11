from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.memory import MemoryStore
from core.orchestrator import PlanStep, TaskOrchestrator
from core.permissions import PermissionEngine, Risk
from core.workspace_context import WorkspaceContext


class NextGenCoreTests(unittest.TestCase):
    def test_memory_search_and_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = MemoryStore(str(Path(tmp) / "memory.db"))
            memory.remember_fact("The active project is SPACE-ZONE")
            memory.add("note", "The user prefers focused Git changes")
            memory.set_preference("coding_style", "focused")
            matches = memory.search("active project")
            self.assertEqual(matches[0]["kind"], "fact")
            self.assertEqual(memory.preferences()["coding_style"], "focused")
            self.assertIn("Relevant long-term facts", memory.context("active project"))

    def test_workspace_context_persists_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "src").mkdir()
            (workspace / "README.md").write_text("test", encoding="utf-8")
            context = WorkspaceContext(tmp)
            project = context.set_project("JARVIS", "python", {"tests": True})
            self.assertEqual(project["name"], "JARVIS")
            snapshot = context.snapshot()
            names = {entry["name"] for entry in snapshot["entries"]}
            self.assertIn("src", names)
            self.assertIn("README.md", names)
            self.assertEqual(snapshot["project"]["kind"], "python")

    def test_plan_and_verification_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                orchestrator = TaskOrchestrator()
                task = orchestrator.begin("build and verify")
                orchestrator.set_plan([
                    PlanStep("step-1", "inspect"),
                    PlanStep("step-2", "change", ["step-1"]),
                ])
                self.assertEqual([s.id for s in orchestrator.ready_steps()], ["step-1"])
                orchestrator.update_step("step-1", "completed", "inspection ok")
                self.assertEqual([s.id for s in orchestrator.ready_steps()], ["step-2"])
                orchestrator.verify("change exists", True, "read-back matched")
                orchestrator.finish("completed", "done")
                trace = Path(tmp) / ".jarvis" / "traces" / f"{task.task_id}.json"
                payload = json.loads(trace.read_text(encoding="utf-8"))
                self.assertEqual(payload["plan"][1]["depends_on"], ["step-1"])
                self.assertTrue(payload["verifications"][0]["verified"])

    def test_permission_categories(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "JARVIS_REQUIRE_APPROVAL": "false",
                "JARVIS_ALLOW_GIT_WRITE": "false",
            },
            clear=False,
        ):
            engine = PermissionEngine()
            ok, reason = engine.check("git_commit", Risk.MEDIUM, approved=True)
            self.assertFalse(ok)
            self.assertIn("Git write", reason)
            ok, _ = engine.check("git_status", Risk.LOW, approved=False)
            self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
