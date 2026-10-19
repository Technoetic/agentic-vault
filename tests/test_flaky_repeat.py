"""scripts/flaky_repeat.py: repeat one unittest id and report x/N with an exact bound."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'scripts' / 'flaky_repeat.py'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stats = _load('flaky_repeat_eval_stats_under_test', ROOT / 'scripts' / 'eval_stats.py')

SYNTHETIC = '''
import unittest
from pathlib import Path

STATE = Path({state!r})


def _next():
    count = int(STATE.read_text()) + 1 if STATE.exists() else 1
    STATE.write_text(str(count))
    return count


class Synthetic(unittest.TestCase):
    def test_every_kth(self):
        count = _next()
        if count % 3 == 0:
            self.fail('deterministic failure in ' + str(STATE) + '\\nsecond line')
        if count % 5 == 0:
            raise OSError(5, 'synthetic denial', str(STATE))

    def test_distinct(self):
        self.fail('distinct failure %d' % _next())

    def test_always_passes(self):
        print('noise on stdout')

    def test_skipped(self):
        self.skipTest('synthetic skip')
'''


class FlakyRepeatTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), 'scripts/flaky_repeat.py is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.module = 'synthetic_flaky_' + uuid.uuid4().hex[:12]
        self.state = self.dir / 'state.txt'
        (self.dir / (self.module + '.py')).write_text(
            SYNTHETIC.format(state=str(self.state)), encoding='utf-8')

    def tool(self, *args, timeout=120):
        return subprocess.run([sys.executable, '-B', str(TOOL), '--path', str(self.dir), *args],
                              capture_output=True, text=True, encoding='utf-8', timeout=timeout)

    def report(self, name, *args):
        result = self.tool(self.module + '.Synthetic.' + name, '--json', *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.dir.name, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_every_kth_failure_counts_match_and_bound_equals_eval_stats(self):
        value = self.report('test_every_kth', '-n', '15')
        self.assertEqual(value['mode'], 'in_process')
        self.assertEqual((value['runs'], value['passed'], value['failed'], value['errors'],
                          value['skipped'], value['failure_events'], value['trials']),
                         (15, 8, 5, 2, 0, 7, 15))
        self.assertEqual(value['bounds']['clopper_pearson_upper'],
                         stats.clopper_pearson_upper_bound(7, 15))
        self.assertEqual(value['bounds'], stats.rate_upper_bounds(7, 15))
        self.assertEqual(value['signatures'], [
            {'signature': 'AssertionError: deterministic failure in <path>', 'count': 5},
            {'signature': "OSError: [Errno 5] synthetic denial: '<path>'", 'count': 2}])
        bound = '%.4f' % stats.clopper_pearson_upper_bound(7, 15)
        self.assertTrue(value['summary'].startswith(
            '7/15 failures; failure rate <= ' + bound
            + ' (one-sided 95% Clopper-Pearson, scripts/eval_stats.py)'), value['summary'])
        self.assertTrue(value['wide_bound'])

    def test_text_output_states_x_over_n_and_exit_code_is_zero_with_failures(self):
        result = self.tool(self.module + '.Synthetic.test_every_kth', '-n', '6')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        bound = '%.4f' % stats.clopper_pearson_upper_bound(3, 6)
        self.assertIn('3/6 failures; failure rate <= ' + bound
                      + ' (one-sided 95% Clopper-Pearson, scripts/eval_stats.py)', result.stdout)
        self.assertIn('wide', result.stdout)
        self.assertNotIn('noise on stdout', result.stdout)
        self.assertNotIn(self.dir.name, result.stdout + result.stderr)

    def test_zero_failures_are_not_observed_never_fixed_and_small_n_is_wide(self):
        value = self.report('test_always_passes', '-n', '5')
        self.assertEqual((value['passed'], value['failure_events']), (5, 0))
        bound = stats.clopper_pearson_upper_bound(0, 5)
        self.assertEqual(value['bounds']['clopper_pearson_upper'], bound)
        self.assertIn('not observed in 5 runs (rate <= %.4f' % bound, value['summary'])
        self.assertTrue(value['wide_bound'])
        result = self.tool(self.module + '.Synthetic.test_always_passes', '-n', '5')
        self.assertEqual(result.returncode, 0)
        self.assertIn('not observed in 5 runs', result.stdout)
        self.assertIn('wide', result.stdout)
        self.assertNotIn('noise on stdout', result.stdout)
        for text in (result.stdout, json.dumps(value)):
            self.assertNotIn('fixed', text.casefold())

    def test_hundreds_of_passing_runs_give_a_narrow_bound(self):
        value = self.report('test_always_passes', '-n', '400')
        self.assertEqual(value['failure_events'], 0)
        self.assertLess(value['bounds']['clopper_pearson_upper'], 0.01)
        self.assertFalse(value['wide_bound'])
        self.assertNotIn('wide', value['summary'])

    def test_fresh_process_runs_each_repeat_in_a_new_interpreter(self):
        value = self.report('test_always_passes', '-n', '3', '--fresh-process')
        self.assertEqual((value['mode'], value['passed'], value['failure_events']),
                         ('fresh_process', 3, 0))
        value = self.report('test_every_kth', '-n', '6', '--fresh-process')
        self.assertEqual((value['failed'], value['errors'], value['passed']), (2, 1, 3))
        self.assertEqual(value['bounds']['clopper_pearson_upper'],
                         stats.clopper_pearson_upper_bound(3, 6))

    def test_signatures_are_bounded(self):
        value = self.report('test_distinct', '-n', '15')
        self.assertEqual(value['failed'], 15)
        self.assertEqual(len(value['signatures']), 10)
        self.assertEqual(value['signatures_not_shown'], 5)

    def test_skipped_runs_leave_no_trials_and_no_bound(self):
        value = self.report('test_skipped', '-n', '4')
        self.assertEqual((value['skipped'], value['trials'], value['bounds']), (4, 0, None))
        self.assertIn('no executed runs', value['summary'])

    def test_usage_errors_exit_2_without_echoing_values(self):
        sentinel = 'SYNTHETIC_ECHO_SENTINEL_' + uuid.uuid4().hex
        target = self.module + '.Synthetic.test_always_passes'
        cases = {
            'missing_id': (),
            'zero_runs': (target, '-n', '0'),
            'non_numeric_runs': (target, '-n', sentinel),
            'too_many_runs': (target, '-n', '10001'),
            'unknown_option': (target, '--' + sentinel),
            'bad_timeout': (target, '--fresh-process', '--timeout', '-1'),
            'unknown_module': ('no_such_module_' + sentinel + '.T.test_x',),
            'unknown_method': (self.module + '.Synthetic.test_' + sentinel,),
            'several_tests': (self.module + '.Synthetic',),
            'unknown_module_fresh': ('no_such_module_' + sentinel + '.T.test_x', '--fresh-process'),
        }
        for label, args in cases.items():
            with self.subTest(case=label):
                result = self.tool(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn(sentinel, result.stdout + result.stderr)
                self.assertNotIn(self.dir.name, result.stdout + result.stderr)
                self.assertIn(json.loads(result.stdout)['error'],
                              ('invalid_arguments', 'invalid_test_id'))
        self.assertFalse(self.state.exists())

    def test_help_remains_available(self):
        result = self.tool('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--fresh-process', result.stdout)


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), 'scripts/flaky_repeat.py is missing')
        self.tool = _load('flaky_repeat_under_test', TOOL)

    def test_signature_keeps_class_and_first_line_without_paths(self):
        cases = [
            (AssertionError('1 != 2 : C:\\Users\\someone\\vault\\note.md\nsecond line'),
             'AssertionError: 1 != 2 : <path>'),
            (AssertionError('missing /home/someone/vault/note.md here'),
             'AssertionError: missing <path> here'),
            (OSError(5, 'denied', '\\\\server\\share\\vault\\x.md'),
             "OSError: [Errno 5] denied: '<path>'"),
            (PermissionError(13, 'denied', 'D:/vault/x.md'),
             "PermissionError: [Errno 13] denied: '<path>'"),
            (AssertionError('ratio 1/30 and a/b stay'), 'AssertionError: ratio 1/30 and a/b stay'),
            (AssertionError(''), 'AssertionError'),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.tool.signature(type(error), error), expected)
        long = self.tool.signature(AssertionError, AssertionError('x' * 1000))
        self.assertLessEqual(len(long), self.tool.MAX_SIGNATURE_CHARS)


if __name__ == '__main__':
    unittest.main()
