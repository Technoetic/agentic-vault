"""Environment variation runs for selected tests: controls first, then blame by shuffled accumulation."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/env_vary.py'


def load():
    if not SCRIPT.exists():
        return None
    spec = importlib.util.spec_from_file_location('env_vary_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


env_vary = load()
NAMES = ('utf8_mode', 'env_minimal', 'home', 'cwd', 'temp', 'tz')


def outcome(status='pass', reason=None):
    return {'outcome': status, 'skip_reason': reason, 'signature': None}


class Scripted:
    """A fake runner: `react` maps a set of enabled variations to one test's outcome."""

    def __init__(self, react, tests=('t.A.a', 't.A.b')):
        self.react = react
        self.tests = tests
        self.calls = []

    def __call__(self, enabled):
        enabled = tuple(enabled)
        self.calls.append(enabled)
        return {'tests': {name: self.react(name, set(enabled), len(self.calls)) for name in self.tests},
                'probe': {}}


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(env_vary, 'scripts/env_vary.py has not been implemented')

    def investigate(self, runner, order=NAMES):
        return env_vary.investigate(runner, order, control_runs=2)

    def test_quiet_runs_stop_after_the_full_variation(self):
        runner = Scripted(lambda name, enabled, call: outcome())
        report = self.investigate(runner)
        self.assertEqual(runner.calls, [(), (), NAMES])
        self.assertEqual(report['verdict'], 'no_difference_observed')
        self.assertEqual(report['all_on']['differences'], [])
        self.assertEqual(report['culprits'], [])

    def test_controls_run_first_and_unstable_tests_leave_the_comparison(self):
        def react(name, enabled, call):
            if name == 't.A.a':
                return outcome('pass' if call == 1 else 'fail')
            return outcome()
        runner = Scripted(react)
        report = self.investigate(runner)
        self.assertEqual(report['control']['unstable_tests'], ['t.A.a'])
        self.assertEqual(report['control']['stable_tests'], ['t.A.b'])
        self.assertEqual(report['verdict'], 'no_difference_observed')

    def test_every_test_unstable_stops_before_any_variation(self):
        runner = Scripted(lambda name, enabled, call: outcome('pass' if call == 1 else 'fail'))
        report = self.investigate(runner)
        self.assertEqual(report['verdict'], 'control_unstable')
        self.assertEqual(runner.calls, [(), ()])
        self.assertIsNone(report['all_on'])

    def test_accumulation_keeps_harmless_variations_on_and_turns_the_culprit_off(self):
        runner = Scripted(lambda name, enabled, call: outcome('fail' if 'tz' in enabled and name == 't.A.a' else 'pass'))
        report = self.investigate(runner, ('home', 'tz', 'temp'))
        self.assertEqual(runner.calls, [(), (), ('home', 'tz', 'temp'), ('home',), ('home', 'tz'), ('home', 'temp')])
        self.assertEqual(report['verdict'], 'environment_sensitive')
        self.assertEqual(report['culprits'], ['tz'])
        self.assertEqual(report['kept_without_difference'], ['home', 'temp'])
        self.assertEqual([step['variation'] for step in report['attribution']], ['home', 'tz', 'temp'])
        self.assertEqual([step['culprit'] for step in report['attribution']], [False, True, False])
        self.assertEqual(report['attribution'][1]['enabled'], ['home', 'tz'])
        self.assertEqual(report['attribution'][1]['differences'][0]['test_id'], 't.A.a')

    def test_several_culprits_are_all_named_in_shuffled_order(self):
        runner = Scripted(lambda name, enabled, call: outcome(
            'fail' if name == 't.A.a' and enabled & {'tz', 'home'} else 'pass'))
        report = self.investigate(runner, ('tz', 'temp', 'home'))
        self.assertEqual(report['culprits'], ['tz', 'home'])

    def test_an_interaction_blames_the_later_switch_so_the_order_matters(self):
        def react(name, enabled, call):
            return outcome('fail' if name == 't.A.a' and {'tz', 'home'} <= enabled else 'pass')
        self.assertEqual(self.investigate(Scripted(react), ('tz', 'temp', 'home'))['culprits'], ['home'])
        self.assertEqual(self.investigate(Scripted(react), ('home', 'temp', 'tz'))['culprits'], ['tz'])

    def test_all_on_difference_with_no_step_difference_reports_unattributed_tests(self):
        def react(name, enabled, call):
            # Different only on the single all-on run (call 3): a one-off, or an interaction.
            return outcome('fail' if call == 3 and name == 't.A.a' else 'pass')
        report = self.investigate(Scripted(react), NAMES)
        self.assertEqual(report['verdict'], 'environment_sensitive')
        self.assertEqual(report['culprits'], [])
        self.assertEqual(report['unattributed_tests'], ['t.A.a'])

    def test_skip_differences_list_test_and_reason(self):
        def react(name, enabled, call):
            if name == 't.A.a' and 'home' in enabled:
                return outcome('skip', 'needs a home folder')
            return outcome()
        report = self.investigate(Scripted(react), ('home', 'tz'))
        self.assertEqual(report['verdict'], 'environment_sensitive')
        self.assertEqual(report['all_on']['skip_differences'],
                         [{'test_id': 't.A.a', 'control': None, 'varied': 'needs a home folder'}])
        self.assertEqual(report['culprits'], ['home'])

    def test_a_changed_skip_reason_is_a_difference_even_while_both_skip(self):
        def react(name, enabled, call):
            if name == 't.A.a':
                return outcome('skip', 'because varied' if enabled else 'because control')
            return outcome()
        report = self.investigate(Scripted(react), ('home',))
        self.assertEqual(report['all_on']['skip_differences'],
                         [{'test_id': 't.A.a', 'control': 'because control', 'varied': 'because varied'}])
        self.assertEqual(report['verdict'], 'environment_sensitive')

    def test_always_skipped_tests_are_named_because_they_prove_nothing(self):
        def react(name, enabled, call):
            return outcome('skip', 'windows only') if name == 't.A.a' else outcome()
        report = self.investigate(Scripted(react))
        self.assertEqual(report['verdict'], 'no_difference_observed')
        self.assertEqual(report['always_skipped'], [{'test_id': 't.A.a', 'reason': 'windows only'}])

    def test_a_test_missing_from_a_run_is_a_difference(self):
        class Dropping(Scripted):
            def __call__(self, enabled):
                result = super().__call__(enabled)
                if enabled:
                    del result['tests']['t.A.a']
                return result
        report = self.investigate(Dropping(lambda name, enabled, call: outcome()), ('home',))
        self.assertEqual(report['all_on']['differences'],
                         [{'test_id': 't.A.a', 'control': 'pass', 'varied': 'absent'}])

    def test_shuffle_is_seeded_and_recorded(self):
        first = env_vary.shuffled(NAMES, 123)
        self.assertEqual(first, env_vary.shuffled(NAMES, 123))
        self.assertEqual(sorted(first), sorted(NAMES))
        self.assertTrue(any(env_vary.shuffled(NAMES, seed) != first for seed in range(1, 8)))

    def test_invalid_control_count_is_rejected(self):
        with self.assertRaises(env_vary.UsageError):
            env_vary.investigate(Scripted(lambda *a: outcome()), NAMES, control_runs=1)


class VariationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(env_vary, 'scripts/env_vary.py has not been implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = {'PATH': 'p', 'SYSTEMROOT': 'r', 'SECRET_THING': 'x', 'HOME': 'h', 'USERPROFILE': 'u',
                     'APPDATA': 'a', 'LOCALAPPDATA': 'l', 'TEMP': 't', 'TMP': 't', 'PYTHONUTF8': '1',
                     'PYTHONPATH': 'q'}

    def build(self, *names):
        return env_vary.build_environment(names, self.base, Path(self.tmp.name), 'CTRL')

    def test_control_keeps_the_environment_and_fixes_only_the_bytecode_switch(self):
        env, cwd = self.build()
        self.assertEqual(cwd, 'CTRL')
        self.assertEqual(env, {**self.base, 'PYTHONDONTWRITEBYTECODE': '1'})

    def test_utf8_mode_is_explicitly_zero_rather_than_removed(self):
        env, _ = self.build('utf8_mode')
        self.assertEqual(env['PYTHONUTF8'], '0')

    def test_env_minimal_keeps_os_essentials_and_drops_the_rest(self):
        env, _ = self.build('env_minimal')
        self.assertNotIn('SECRET_THING', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['PATH'], 'p')
        self.assertEqual(env['SYSTEMROOT'], 'r')
        self.assertEqual(env['TEMP'], 't')
        self.assertEqual(env['HOME'], 'h')

    def test_home_family_points_at_one_new_empty_folder(self):
        env, _ = self.build('home')
        for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                     'XDG_DATA_HOME'):
            self.assertTrue(Path(env[name]).is_dir(), name)
            self.assertEqual(list(Path(env[name]).iterdir()), [], name)
        self.assertNotIn('HOMEDRIVE', env)
        self.assertNotIn('HOMEPATH', env)

    def test_cwd_and_temp_contain_a_space_and_hangul(self):
        env, cwd = self.build('cwd', 'temp')
        for path in (cwd, env['TEMP'], env['TMP'], env['TMPDIR']):
            self.assertIn(' ', Path(path).name)
            self.assertTrue(any('가' <= ch <= '힣' for ch in Path(path).name), path)
            self.assertTrue(Path(path).is_dir())

    def test_tz_uses_an_offset_no_real_zone_has(self):
        env, _ = self.build('tz')
        self.assertEqual(env['TZ'], env_vary.TZ_VALUE)
        self.assertNotIn(env_vary.TZ_VALUE, ('UTC0', 'UTC'))

    def test_environment_is_independent_of_the_order_of_names(self):
        one = self.build('tz', 'env_minimal', 'utf8_mode')[0]
        two = self.build('utf8_mode', 'tz', 'env_minimal')[0]
        self.assertEqual(one, two)

    def test_unknown_variation_is_a_usage_error(self):
        with self.assertRaises(env_vary.UsageError):
            self.build('nope')


SAMPLE = textwrap.dedent('''\
    import os
    import pathlib
    import tempfile
    import time
    import unittest


    class Sample(unittest.TestCase):
        def test_stable(self):
            self.assertEqual(1, 1)

        def test_needs_utf8(self):
            self.assertNotEqual(os.environ.get('PYTHONUTF8'), '0')

        @unittest.skipIf(os.environ.get('TZ'), 'zone is fixed')
        def test_skipped_under_tz(self):
            self.assertEqual(1, 1)

        @unittest.skip('never here')
        def test_always_skipped(self):
            self.fail('not reached')

        def test_unstable(self):
            mark = pathlib.Path(os.environ['SAMPLE_MARK'])
            count = int(mark.read_text()) + 1 if mark.exists() else 1
            mark.write_text(str(count))
            self.assertEqual(count % 2, 1)
''')


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(env_vary, 'scripts/env_vary.py has not been implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        (self.folder / 'envvary_sample.py').write_text(SAMPLE, encoding='utf-8')

    def run_cli(self, *args):
        environment = {**os.environ, 'SAMPLE_MARK': str(self.folder / 'mark.txt'), 'PYTHONDONTWRITEBYTECODE': '1'}
        environment.pop('TZ', None)
        environment.pop('PYTHONUTF8', None)
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--path', str(self.folder), '--json', *args],
                              capture_output=True, env=environment, timeout=240)

    def test_real_child_runs_find_the_culprits_and_the_skip_difference(self):
        done = self.run_cli('envvary_sample', '--seed', '7')
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout.decode('ascii'))
        self.assertEqual(report['verdict'], 'environment_sensitive')
        self.assertEqual(report['seed'], 7)
        self.assertEqual(sorted(report['shuffled_order']), sorted(NAMES))
        self.assertEqual(report['shuffled_order'], env_vary.shuffled(sorted(NAMES), 7))
        self.assertEqual(sorted(report['culprits']), ['tz', 'utf8_mode'])
        self.assertEqual(report['control']['unstable_tests'], ['envvary_sample.Sample.test_unstable'])
        self.assertEqual(report['all_on']['skip_differences'],
                         [{'test_id': 'envvary_sample.Sample.test_skipped_under_tz', 'control': None,
                           'varied': 'zone is fixed'}])
        self.assertEqual(report['always_skipped'],
                         [{'test_id': 'envvary_sample.Sample.test_always_skipped', 'reason': 'never here'}])
        self.assertTrue(report['variation_effect']['tz']['observed'])
        self.assertTrue(report['variation_effect']['cwd']['observed'])
        self.assertTrue(report['variation_effect']['temp']['observed'])
        self.assertTrue(report['variation_effect']['home']['observed'])
        self.assertNotIn(self.tmp.name, done.stdout.decode('ascii'))

    def test_no_test_selected_is_a_usage_error_not_a_full_run(self):
        done = self.run_cli()
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(done.stdout), {'ok': False, 'error': 'invalid_arguments'})

    def test_unknown_test_and_bad_arguments_are_content_free(self):
        done = self.run_cli('no_such_module_here')
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(done.stdout), {'ok': False, 'error': 'invalid_test_id'})
        done = self.run_cli('envvary_sample', '--variation', 'secret-name')
        self.assertEqual(done.returncode, 2)
        self.assertNotIn(b'secret-name', done.stdout + done.stderr)

    def test_timeout_is_a_run_level_error_for_every_test(self):
        slow = 'import time, unittest\n\nclass Slow(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(30)\n'
        (self.folder / 'envvary_slow.py').write_text(slow, encoding='utf-8')
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--path', str(self.folder), '--json',
                               '--timeout', '2', 'envvary_slow.Slow.test_slow'],
                              capture_output=True, env=environment, timeout=120)
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(done.stdout), {'ok': False, 'error': 'control_run_failed'})

    def test_module_imports_nothing_from_the_runtime_tree(self):
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertNotIn('skills/agentic-vault', text)
        hooks = (ROOT / 'hooks/hooks.json').read_text(encoding='utf-8')
        self.assertNotIn('env_vary', hooks)


if __name__ == '__main__':
    unittest.main()
