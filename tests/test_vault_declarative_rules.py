from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))


class DeclarativeRuleTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('vault_declarative_rules')
        except ModuleNotFoundError:
            self.fail('declarative rules implementation is missing')

    def test_default_literal_exists_count_and_substitution_with_unicode(self):
        text = 'Keep [NS] and [NS] bytes 원문'
        result = self.module.evaluate_rules(text, [
            {'id': 'exists', 'type': 'existence', 'pattern': '[NS]'},
            {'id': 'count', 'type': 'occurrence', 'pattern': '[NS]', 'min': 2, 'max': 2},
            {'id': 'replace', 'type': 'substitution', 'pattern': '[NS]', 'replacement': 'NS'},
        ])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['output'], 'Keep NS and NS bytes 원문')
        self.assertTrue(all(r['passed'] for r in result['results']))
        self.assertEqual(result['results'][1]['count'], 2)
        self.assertEqual(result['authority'], 'data_rule_evaluation_only')

    def test_missing_existence_and_count_excess_are_actual_failures(self):
        result = self.module.evaluate_rules('a a', [
            {'type': 'existence', 'pattern': 'b'},
            {'type': 'occurrence', 'pattern': 'a', 'min': 0, 'max': 1},
        ])
        self.assertFalse(result['passed'])
        self.assertEqual([r['passed'] for r in result['results']], [False, False])

    def test_regex_requires_explicit_opt_in_and_never_reports_fake_pass(self):
        result = self.module.evaluate_rules('aaaa', [{'type': 'existence', 'pattern': 'a+', 'mode': 'regex'}])
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['passed'])
        self.assertEqual(result['results'][0]['status'], 'regex_disabled')

    def test_regex_real_worker_and_pathological_timeout(self):
        result = self.module.evaluate_rules('NS 12 NS 25', [
            {'type': 'occurrence', 'mode': 'regex', 'pattern': r'NS \d+', 'min': 2, 'max': 2},
            {'type': 'substitution', 'mode': 'regex', 'pattern': r'(NS) (\d+)', 'replacement': r'\1-\2'},
        ], allow_regex=True, timeout_seconds=3)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(result['output'], 'NS-12 NS-25')
        before = time.monotonic()
        result = self.module.evaluate_rules('a' * 20000 + '!', [
            {'type': 'existence', 'mode': 'regex', 'pattern': '(a+)+$'},
        ], allow_regex=True, timeout_seconds=.5)
        self.assertEqual(result['status'], 'timeout', result)
        self.assertIsNone(result['passed'])
        self.assertLess(time.monotonic() - before, 4)

    def test_invalid_regex_and_missing_worker_are_visible(self):
        result = self.module.evaluate_rules('text', [{'type': 'existence', 'pattern': '[', 'mode': 'regex'}],
                                            allow_regex=True, timeout_seconds=3)
        self.assertEqual(result['status'], 'invalid_pattern')
        self.assertIsNone(result['passed'])
        with mock.patch.object(self.module, '_regex_process', side_effect=OSError('private detail')):
            result = self.module.evaluate_rules('a', [{'type': 'existence', 'pattern': 'a', 'mode': 'regex'}],
                                                allow_regex=True)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('private detail', str(result))

    def test_bounds_duplicate_ids_unknown_fields_and_code_are_data(self):
        for text, rules in [('x' * (64 * 1024 + 1), []), ('abc', [{'type': 'existence', 'pattern': ''}]),
                            ('abc', [{'type': 'existence', 'pattern': 'a' * 513}]),
                            ('abc', [{'type': 'execute', 'pattern': 'a'}]),
                            ('abc', [{'type': 'existence', 'pattern': 'a', 'command': 'cmd'}]),
                            ('abc', [{'id': 'same', 'type': 'existence', 'pattern': 'a'}] * 2)]:
            with self.subTest(rules=rules[:1]), self.assertRaises(self.module.RuleError):
                self.module.evaluate_rules(text, rules)
        code = "__import__('os').system('do-not-run')"
        result = self.module.evaluate_rules(code, [{'type': 'existence', 'pattern': code}])
        self.assertTrue(result['passed'])

    def test_match_and_output_caps_prevent_unbounded_regex_or_expansion(self):
        result = self.module.evaluate_rules('a' * 3000, [
            {'type': 'occurrence', 'pattern': 'a', 'mode': 'regex', 'min': 1},
        ], allow_regex=True, timeout_seconds=3)
        self.assertEqual(result['status'], 'match_limit')
        result = self.module.evaluate_rules('a' * 2000, [
            {'type': 'substitution', 'pattern': 'a', 'replacement': 'x' * 100},
        ])
        self.assertEqual(result['status'], 'output_limit')
        self.assertIsNone(result['passed'])

    def test_regex_substitution_bounds_backreference_expansion_before_allocation(self):
        # Each input/group is bounded, but repeated backreferences can multiply
        # that bound. The fixed worker must stop at the output budget.
        result = self.module.evaluate_rules('a' * 4000, [
            {'type': 'substitution', 'mode': 'regex', 'pattern': '(a+)', 'replacement': r'\1' * 64},
        ], allow_regex=True, timeout_seconds=3)
        self.assertEqual(result['status'], 'output_limit')
        self.assertEqual(result['output'], 'a' * 4000)
        self.assertIsNone(result['passed'])
        # A benign finite expansion makes the worker's peak allocation directly
        # observable without running an unbounded pattern in the test process.
        tracemalloc.start()
        try:
            result = self.module._evaluate_one('a' * 5000, {'type': 'substitution', 'pattern': '(a+)',
                                                           'replacement': r'\1' * 1000}, regex=True)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(result[0]['status'], 'output_limit')
        self.assertLess(peak, 512 * 1024, 'bounded worker must reject expansion before allocating it')

    def test_cli_reports_timeout_and_rejects_malformed_input(self):
        command = [sys.executable, str(SCRIPTS / 'vault_declarative_rules.py')]
        payload = {'text': 'a' * 20000 + '!', 'rules': [{'type': 'existence', 'mode': 'regex', 'pattern': '(a+)+$'}]}
        run = subprocess.run(command + ['--allow-regex', '--timeout-seconds', '.5'],
                             input=json.dumps(payload), text=True, capture_output=True, timeout=5)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(json.loads(run.stdout)['status'], 'timeout')
        for raw in ('{bad', '[' * 10000):
            run = subprocess.run(command, input=raw, text=True, capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertNotIn('Traceback', run.stderr)

    def test_non_utf8_surrogates_and_nonfinite_timing_are_safe_api_errors(self):
        for text, rules in [('\ud800', []), ('x', [{'type': 'existence', 'pattern': '\ud800'}]),
                            ('x', [{'type': 'substitution', 'pattern': 'x', 'replacement': '\ud800'}])]:
            with self.assertRaises(self.module.RuleError):
                self.module.evaluate_rules(text, rules)
        for value in (float('nan'), float('inf'), True, 0, 3.1):
            with self.assertRaises(self.module.RuleError):
                self.module.evaluate_rules('x', [], timeout_seconds=value)


if __name__ == '__main__':
    unittest.main()
