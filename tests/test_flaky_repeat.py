"""scripts/flaky_repeat.py: repeat one unittest id and report x/N with an exact bound."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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

# Fails at import time on the second load only; counts every load of the module.
IMPORT_FLAKY = '''
import unittest
from pathlib import Path

LOADS = Path({loads!r})
COUNT = int(LOADS.read_text()) + 1 if LOADS.exists() else 1
LOADS.write_text(str(COUNT))
if COUNT == 2:
    raise RuntimeError('import flake in ' + str(LOADS))


class Loaded(unittest.TestCase):
    def test_passes(self):
        pass
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
        return self.report_id(self.module + '.Synthetic.' + name, *args)

    def report_id(self, test_id, *args):
        result = self.tool(test_id, '--json', *args)
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

    def write_import_flaky(self):
        name = 'import_flaky_' + uuid.uuid4().hex[:12]
        loads = self.dir / (name + '.loads')
        (self.dir / (name + '.py')).write_text(IMPORT_FLAKY.format(loads=str(loads)),
                                               encoding='utf-8')
        return name + '.Loaded.test_passes', loads

    def test_fresh_process_counts_a_later_import_failure_as_an_error_event(self):
        test_id, loads = self.write_import_flaky()
        result = self.tool(test_id, '-n', '4', '--fresh-process', '--json')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual((value['runs'], value['passed'], value['errors'], value['failure_events']),
                         (4, 3, 1, 1))
        self.assertEqual(value['signatures'], [{'signature': 'ChildLoadError', 'count': 1}])
        self.assertEqual(loads.read_text(), '4')
        self.assertNotIn(self.dir.name, result.stdout + result.stderr)

    def test_fresh_process_import_failure_on_the_first_run_is_a_usage_error(self):
        test_id, loads = self.write_import_flaky()
        loads.write_text('1')  # the first child is the module's second load
        result = self.tool(test_id, '-n', '3', '--fresh-process', '--json')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': 'invalid_test_id'})
        self.assertEqual(loads.read_text(), '2')

    def test_in_process_repeats_import_the_module_once(self):
        test_id, loads = self.write_import_flaky()
        value = self.report_id(test_id, '-n', '3')
        self.assertEqual((value['passed'], value['failure_events']), (3, 0))
        self.assertEqual(loads.read_text(), '1')

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
            (FileNotFoundError(2, 'No such file', r'C:\Users\Jane Doe\vault\x.md'),
             "FileNotFoundError: [Errno 2] No such file: '<path>'"),
            (PermissionError(13, 'denied', '/home/Jane Doe/vault/x.md'),
             "PermissionError: [Errno 13] denied: '<path>'"),
            (OSError(5, 'denied', r'\\server\share name\x.md'),
             "OSError: [Errno 5] denied: '<path>'"),
            (AssertionError(r"'C:\Program Files\vault' != 'other'"),
             "AssertionError: '<path>' != 'other'"),
            (AssertionError('"/tmp/Jane Doe/x.md" missing'), 'AssertionError: "<path>" missing'),
            (AssertionError('ratio 1/30 and a/b stay'), 'AssertionError: ratio 1/30 and a/b stay'),
            (AssertionError('relative notes/a b.md is kept'),
             'AssertionError: relative notes/a b.md is kept'),
            (AssertionError(''), 'AssertionError'),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.tool.signature(type(error), error), expected)
        long = self.tool.signature(AssertionError, AssertionError('x' * 1000))
        self.assertLessEqual(len(long), self.tool.MAX_SIGNATURE_CHARS)


class SummaryAndRepeatTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), 'scripts/flaky_repeat.py is missing')
        self.tool = _load('flaky_repeat_summary_under_test', TOOL)

    def test_in_process_load_failure_after_the_first_run_is_an_error_event(self):
        results = [('pass', None), self.tool.UsageError('invalid_test_id'), ('pass', None)]
        with mock.patch.object(self.tool, 'run_once', side_effect=results):
            counts, signatures, not_shown, _ = self.tool.repeat('m.C.test_x', 3)
        self.assertEqual(counts, {'pass': 2, 'fail': 0, 'error': 1, 'skip': 0})
        self.assertEqual((signatures, not_shown), ({'LoadError': 1}, 0))
        with mock.patch.object(self.tool, 'run_once',
                               side_effect=self.tool.UsageError('invalid_test_id')):
            with self.assertRaises(self.tool.UsageError):
                self.tool.repeat('m.C.test_x', 3)

    def test_wide_bound_advice_depends_on_observed_failures(self):
        events = stats.rate_upper_bounds(50, 1000)
        self.assertGreater(events['clopper_pearson_upper'] - events['observed_rate'], 0.01)
        text = self.tool.summarize(50, 1000, 0, events)
        self.assertIn('wide at N=1000', text)
        self.assertIn('more than 0.01', text)
        self.assertNotIn('hundreds', text)
        self.assertIn('more repeats narrow it', text)
        zero = self.tool.summarize(0, 30, 0, stats.rate_upper_bounds(0, 30))
        self.assertIn('hundreds of targeted repeats narrow it', zero)


if __name__ == '__main__':
    unittest.main()
