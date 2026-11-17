from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _winfs import PLAIN_PATH_LIMIT, extended
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import PLAIN_PATH_LIMIT, extended


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'evaluate_task_workflows.py'
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'


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


class TemporaryFolderTests(unittest.TestCase):
    """The demo keeps tempfile's default folder unless its deepest receipt reaches the plain path limit."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('evaluate_task_workflows', SCRIPT)
        cls.demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.demo)
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        cls.evidence = importlib.import_module('vault_evidence')
        cls.runtime_dir = importlib.import_module('vault_state').RUNTIME_DIR
        # The longest path the demo creates below the temporary folder:
        # /tmp<8 characters>/vault/<runtime folder>/task-contract-<64 hex>.json
        cls.below = (len('/tmp') + 8 + len('/vault/') + len(cls.runtime_dir)
                     + len('/task-contract-') + 64 + len('.json'))

    def chosen(self, length):
        """A temporary folder path of that length and the `dir` the demo chooses for it."""
        root = 'C:\\' if os.name == 'nt' else '/'
        folder = root + 'x' * (length - len(root))
        with mock.patch.object(self.demo.tempfile, 'gettempdir', return_value=folder):
            return folder, self.demo._temporary_parent(self.evidence, self.runtime_dir)

    def test_the_default_folder_is_kept_while_the_deepest_receipt_stays_below_the_plain_limit(self):
        for length in (33, PLAIN_PATH_LIMIT - self.below - 1):
            with self.subTest(length=length):
                self.assertIsNone(self.chosen(length)[1])

    def test_the_extended_length_form_is_chosen_at_the_plain_limit_and_only_on_windows(self):
        for length in (PLAIN_PATH_LIMIT - self.below, 150):
            with self.subTest(length=length):
                folder, chosen = self.chosen(length)
                self.assertEqual(chosen, str(extended(folder)) if os.name == 'nt' else None)


if __name__ == '__main__':
    unittest.main()
