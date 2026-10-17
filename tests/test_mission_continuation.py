"""Resume a paused journal without replaying tools or clearing cancellation."""
import tempfile
import threading
from concurrent.futures import Future
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.fast_execution_agent import FastExecutionFullAccessAgent
from core.full_access_bridge import run_agent_mission
from core.orchestrator import TaskOrchestrator
from scripts.verify_autonomous_mission import run_with_quota_recovery


class MissionContinuationTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        orchestrator = TaskOrchestrator(directory.name)
        orchestrator.begin("fixture")
        orchestrator.finish("waiting_user", "AI_PROVIDER_RATE_LIMITED: fixture")
        self.agent = SimpleNamespace(orchestrator=orchestrator, reset=Mock(),
            _is_stopped=Mock(return_value=False), tools=SimpleNamespace(permissions=None),
            run=Mock(return_value="AI_PROVIDER_RATE_LIMITED: fixture"))
        for target in ("core.process_control.set_cancellation", "core.desktop_control_tools.release_held_inputs"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_bridge_keeps_journal_and_messages_on_resume(self):
        journal = self.agent.orchestrator.current
        result = run_agent_mission(self.agent, "fixture", resume_current=True)
        self.agent.reset.assert_not_called()
        self.agent.run.assert_called_once_with("fixture", emit=None, resume_current=True)
        self.assertIs(self.agent.orchestrator.current, journal)
        self.assertEqual(result["task_id"], journal.task_id)

    def test_resume_refuses_changed_goal_completed_mission_cancel_or_pending_request(self):
        for condition in ("changed", "completed", "cancel", "external", "control", "_pending_model", "_interrupted_tool"):
            with self.subTest(condition=condition):
                self.agent.orchestrator.current.status = "completed" if condition == "completed" else "waiting_user"
                self.agent._is_stopped.return_value = condition == "cancel"
                kwargs = {"resume_current": True}
                if condition == "external":
                    kwargs["cancel_event"] = threading.Event()
                    kwargs["cancel_event"].set()
                if condition == "control":
                    kwargs["mission_control"] = SimpleNamespace(cancelled=True)
                if condition.startswith("_"):
                    setattr(self.agent, condition, Future())
                with self.assertRaises((ValueError, RuntimeError)):
                    run_agent_mission(self.agent, "changed" if condition == "changed" else "fixture", **kwargs)
                self.agent.run.assert_not_called()
                self.agent.reset.assert_not_called()
                if condition.startswith("_"):
                    delattr(self.agent, condition)

    def test_fast_agent_continuation_bypasses_compiler(self):
        agent = object.__new__(FastExecutionFullAccessAgent)
        with patch("core.full_access_agent.FullAccessJarvisAgent.run", return_value="continued") as run, \
                patch("core.fast_execution_agent.execute_fast_mission") as fast, \
                patch("core.whatsapp_fast_mission.execute_whatsapp_ordinal_mission") as whatsapp:
            self.assertEqual(agent.run("fixture", resume_current=True), "continued")
        run.assert_called_once_with("fixture", emit=None, resume_current=True)
        fast.assert_not_called()
        whatsapp.assert_not_called()


class QualificationCooldownTests(TestCase):
    def setUp(self):
        self.agent = SimpleNamespace(_provider_pause_retry_seconds=0.5, _is_stopped=Mock(return_value=False),
            orchestrator=SimpleNamespace(current=SimpleNamespace(metrics={})))
        self.control = SimpleNamespace(cancelled=False)
        self.paused = {"status": "waiting_user", "result": "AI_PROVIDER_RATE_LIMITED: fixture", "task_id": "original"}
        self.now = 0

    def advance(self, seconds):
        self.now += seconds

    def test_cooldown_resumes_same_agent_goal_and_control_with_bounded_retries(self):
        with patch("scripts.verify_autonomous_mission.run_agent_mission", return_value=self.paused) as run, \
                patch("scripts.verify_autonomous_mission.time.monotonic", side_effect=lambda: self.now), \
                patch("scripts.verify_autonomous_mission.time.sleep", side_effect=self.advance):
            result = run_with_quota_recovery(self.agent, "fixture", None, self.control)
        self.assertEqual(result, self.paused)
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list[1:]:
            self.assertEqual(call.args, (self.agent, "fixture"))
            self.assertIs(call.kwargs["mission_control"], self.control)
            self.assertTrue(call.kwargs["resume_current"])

    def test_cancel_during_cooldown_never_resumes(self):
        def cancel(_):
            self.control.cancelled = True
        with patch("scripts.verify_autonomous_mission.run_agent_mission", return_value=self.paused) as run, \
                patch("scripts.verify_autonomous_mission.time.sleep", side_effect=cancel):
            result = run_with_quota_recovery(self.agent, "fixture", Mock(), self.control)
        self.assertEqual(result["status"], "cancelled")
        run.assert_called_once()

    def test_other_pauses_or_unvalidated_metadata_are_never_retried(self):
        for delay in (None, -1, float("nan"), 62, True):
            self.agent._provider_pause_retry_seconds = delay
            with patch("scripts.verify_autonomous_mission.run_agent_mission", return_value=self.paused) as run:
                run_with_quota_recovery(self.agent, "fixture", Mock(), self.control)
                run.assert_called_once()
        self.agent._provider_pause_retry_seconds = 0.5
        with patch("scripts.verify_autonomous_mission.run_agent_mission", return_value={
                "status": "waiting_user", "result": "HUMAN_VERIFICATION_REQUIRED"}) as run:
            run_with_quota_recovery(self.agent, "fixture", Mock(), self.control)
            run.assert_called_once()
