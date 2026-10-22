from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'evaluate_memory_reliability.py'


def load_example():
    if not SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location('memory_reliability_example', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemoryReliabilityExampleTests(unittest.TestCase):
    def test_native_receipts_and_real_source_drift_exercise_both_tools(self):
        module = load_example()
        self.assertIsNotNone(module, 'source-bound memory reliability example is missing')
        report = module.evaluate_reliability()
        self.assertTrue(report['ok'])
        self.assertEqual(report['backend'], 'deterministic-synthetic')
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['network_calls'], 0)
        observed = {case['id']: case for case in report['cases']}
        for identifier in ('assembly_selected', 'assembly_conflict',
                           'procedure_match', 'procedure_no_match',
                           'procedure_conditions_mismatch', 'procedure_stale'):
            self.assertIn(identifier, observed)
            self.assertTrue(observed[identifier]['passed'], identifier)
        self.assertEqual(observed['assembly_selected']['value'], '30 days')
        self.assertEqual(observed['procedure_match']['ids'], ['startup'])
        self.assertEqual(observed['procedure_stale']['ids'], [])
        self.assertTrue(report['temporary_vault_only'])

    def test_cli_outputs_checked_json_and_success_exit(self):
        self.assertTrue(SCRIPT.is_file(), 'source-bound example CLI is missing')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT)],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['ok'])
        self.assertGreaterEqual(len(report['cases']), 6)
        self.assertTrue(all(case['passed'] for case in report['cases']))


if __name__ == '__main__':
    unittest.main()
