import json
import unittest

from core.model_context import compact_bookkeeping_result


class ModelContextTests(unittest.TestCase):
    def test_updates_retain_all_steps_evidence_and_unfinished_workflows(self):
        steps = [{"id": str(i), "description": "draft " + str(i), "action": "draft " + str(i),
                  "status": "FAILED" if i == 1 else "COMPLETED", "mutation_delivered": False,
                  "result": "exact evidence " + str(i), "dependencies": [str(i - 1)] if i else [],
                  "expected_result": "", "recovered": False} for i in range(6)]
        payload = {"task_id": "fixture", "goal": "long goal " * 500, "task_graph": steps,
                   "workflows": [{"id": "remaining", "status": "blocked", "remaining": ["step7"]}],
                   "verified": False, "recovery_history": ["repeated history" * 500],
                   "graph_rewrites": [{"failed_step_id": "1", "inserted_step_ids": ["5"]}]}
        raw = json.dumps(payload)
        compact = compact_bookkeeping_result("task_update_step", raw)
        actual = json.loads(compact)
        self.assertLess(len(compact), len(raw) / 4)
        self.assertEqual(actual['workflows'], payload['workflows'])
        self.assertEqual(actual['graph_rewrites'], payload['graph_rewrites'])
        self.assertFalse(actual['verified'])
        self.assertEqual(len(actual['task_graph']), len(steps))
        for before, after in zip(steps, actual['task_graph']):
            self.assertEqual(after['result'], before['result'])
            self.assertEqual(after['status'], before['status'])
            self.assertFalse(after['mutation_delivered'])
        self.assertEqual(payload['task_graph'], steps)

    def test_never_compacts_error_observation_action_or_explicit_status(self):
        text = '{"task_id":"fixture","goal":"keep exact","result":"important"}'
        for name in ('task_status', 'task_recall', 'interaction_scene', 'workflow_status', 'ui_type'):
            self.assertEqual(compact_bookkeeping_result(name, text), text)
        error = 'ERROR: Verify the previous outcome first'
        self.assertEqual(compact_bookkeeping_result('task_update_step', error), error)

    def test_plan_keeps_differing_action_and_description_and_retry_policy(self):
        payload = [{'id': 'a', 'description': 'Open record', 'action': 'click exact control',
                    'retry_policy': {'max_attempts': 0}, 'required_state': ['observed'], 'result': ''}]
        result = json.loads(compact_bookkeeping_result('task_plan', json.dumps(payload)))
        self.assertEqual(result[0]['action'], 'click exact control')
        self.assertEqual(result[0]['retry_policy'], {'max_attempts': 0})
        self.assertEqual(result[0]['required_state'], ['observed'])
