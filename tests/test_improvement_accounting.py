from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    example = importlib.import_module('evaluate_improvement_accounting')
except ModuleNotFoundError:
    example = None


class ImprovementAccountingIntegrationTests(unittest.TestCase):
    def test_four_arm_example_retains_costs_unknowns_and_evaluator_guard(self):
        self.assertIsNotNone(example, 'improvement accounting demonstration is missing')
        result = example.evaluate()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['native_runs'], 4)
        self.assertEqual(result['cost_receipts'], 4)
        self.assertTrue(result['shared_evaluator'])
        self.assertTrue(result['discarded_attempt_retained'])
        self.assertTrue(result['unknown_cost_is_null'])
        self.assertTrue(result['evaluator_mutation_rejected'])
        self.assertTrue(result['complete_cost_comparison'])
        self.assertTrue(result['incomplete_cost_comparison_withheld'])
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['applies_changes'])
        self.assertEqual(result['measurements'], 'synthetic_host_reports_only')


if __name__ == '__main__':
    unittest.main()
