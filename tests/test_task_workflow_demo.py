from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'evaluate_task_workflows.py'


class TaskWorkflowDemoTests(unittest.TestCase):
    def test_checkout_cli_demonstrates_success_and_failure_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SCRIPT)], cwd=temporary,
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(list(Path(temporary).iterdir()), [])
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], 'complete')
        self.assertEqual(output['fixture_scope'], 'generated_synthetic_controls_not_external_benchmark')
        self.assertFalse(output['runtime_limits_enforced'])
        self.assertFalse(output['model_calls_made'])
        self.assertFalse(output['network_calls_made'])
        self.assertGreaterEqual(len(output['limitations']), 3)
        controls = output['controls']
        self.assertTrue(all(item['passed'] for item in controls.values()))
        self.assertEqual(controls['capture_to_success']['contract'], 'ready_for_execution_review')
        self.assertEqual(controls['capture_to_success']['staged'], 'passed')
        self.assertEqual(controls['capture_to_success']['cycle'], 'success')
        self.assertEqual(controls['inferred_default']['contract'], 'needs_clarification')
        self.assertEqual(controls['open_blocking_question']['contract'], 'needs_clarification')
        self.assertEqual(controls['hard_fail_with_model_support']['staged'], 'failed')
        self.assertFalse(controls['hard_fail_with_model_support']['cycle_success'])
        self.assertEqual(controls['unexecuted_checks']['not_run'], 'unverified')
        self.assertEqual(controls['unexecuted_checks']['no_evidence'], 'unverified')
        self.assertEqual(controls['superseded_contract']['staged'], 'stale')
        self.assertEqual(controls['bounded_observation']['repeated_failure'], 'no_progress')
        self.assertEqual(controls['bounded_observation']['generation_limit'], 'max_generations')
        self.assertNotIn(str(ROOT), result.stdout)
        self.assertNotIn(temporary, result.stdout)


if __name__ == '__main__':
    unittest.main()
