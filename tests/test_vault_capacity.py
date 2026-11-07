"""Read-only capacity check: sizes against read limits and a Git size-history estimate."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
DOCTOR = SCRIPTS / 'vault_doctor.py'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_capacity as capacity
import vault_recall
import vault_state

KIB = 1024
DAY = 86400
START = 1_700_000_000


class CapacityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name).resolve() / 'vault'
        self.vault.mkdir()
        self.config = {'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
                       'handoff_note': '00-meta/handoff.md', 'hot_note': '00-meta/hot.md',
                       'log_note': '00-meta/log.md', 'tasks_note': '00-meta/tasks.md',
                       'index_note': '00-meta/index.md'}
        self.write_config()

    def write_config(self):
        self.write('00-meta/vault-config.json', json.dumps(self.config, ensure_ascii=False).encode())

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def git(self, *args, date=None):
        env = dict(os.environ)
        if date is not None:
            env['GIT_COMMITTER_DATE'] = env['GIT_AUTHOR_DATE'] = f'{date} +0000'
        result = subprocess.run(['git', *args], cwd=self.vault, capture_output=True, env=env,
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def init_repo(self):
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'core.autocrlf', 'false')

    def commit_sizes(self, relative, points):
        """Commit one note at each (timestamp, size) point."""
        for moment, size in points:
            self.write(relative, b'x' * size)
            self.git('add', '--', relative)
            self.git('commit', '-q', '--allow-empty', '-m', 'fixture', date=moment)

    def target(self, report, role):
        return next(item for item in report['targets'] if item['role'] == role)

    def limit(self, target, name):
        return next(item for item in target['limits'] if item['name'] == name)


class SizeTests(CapacityCase):
    def test_limits_come_from_the_modules_that_enforce_them(self):
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(report['limits'], {'state_read_bytes': vault_state.MAX_BYTES,
                                            'recall_file_bytes': vault_recall.MAX_FILE_BYTES,
                                            'history_commits': 40})

    def test_every_configured_target_is_compared_with_both_limits(self):
        for name in ('handoff', 'hot', 'log', 'tasks', 'index'):
            self.write(f'00-meta/{name}.md', b'small\n')
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual([item['role'] for item in report['targets']],
                         ['handoff', 'hot', 'log', 'tasks', 'index'])
        for target in report['targets']:
            self.assertEqual(target['status'], 'ready')
            self.assertEqual([item['name'] for item in target['limits']],
                             ['state_read', 'recall_file'])
        self.assertEqual((report['status'], report['reason_code'], report['next_action']),
                         ('ready', 'within_limits', 'none'))

    def test_limit_boundaries_match_the_enforcing_comparison(self):
        self.write('00-meta/hot.md', b'x' * vault_state.MAX_BYTES)
        self.write('00-meta/log.md', b'x' * (vault_state.MAX_BYTES + 1))
        self.write('00-meta/index.md', b'x' * vault_recall.MAX_FILE_BYTES)
        self.write('00-meta/tasks.md', b'x' * (vault_recall.MAX_FILE_BYTES + 1))
        report = capacity.diagnose_capacity(self.vault)
        hot, log = self.target(report, 'hot'), self.target(report, 'log')
        index, tasks = self.target(report, 'index'), self.target(report, 'tasks')
        self.assertEqual(self.limit(hot, 'state_read')['status'], 'warning')
        self.assertEqual(self.limit(log, 'state_read')['status'], 'over_limit')
        self.assertEqual(self.limit(index, 'recall_file')['status'], 'warning')
        self.assertEqual(self.limit(index, 'state_read')['status'], 'over_limit')
        self.assertEqual(self.limit(tasks, 'recall_file')['status'], 'over_limit')

    def test_usage_warning_starts_at_eighty_percent(self):
        edge = math.ceil(vault_state.MAX_BYTES * .8)
        self.write('00-meta/hot.md', b'x' * (edge - 1))
        self.write('00-meta/log.md', b'x' * edge)
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(self.target(report, 'hot')['status'], 'ready')
        log = self.target(report, 'log')
        self.assertEqual((log['status'], log['reason_code']), ('warning', 'near_byte_limit'))
        self.assertEqual((report['status'], report['reason_code']), ('warning', 'near_byte_limit'))

    def test_over_limit_reuses_the_doctor_reason(self):
        self.write('00-meta/log.md', b'x' * (vault_state.MAX_BYTES + 1))
        report = capacity.diagnose_capacity(self.vault)
        log = self.target(report, 'log')
        self.assertEqual((log['status'], log['reason_code'], log['next_action']),
                         ('over_limit', 'source_byte_limit', 'reduce_the_note_or_review_the_budget'))
        self.assertEqual((report['status'], report['reason_code']), ('fail', 'source_byte_limit'))

    def test_missing_and_unconfigured_targets_are_named_without_failing(self):
        del self.config['tasks_note']
        self.config['handoff_note'] = ''
        self.write_config()
        self.write('00-meta/hot.md', b'small\n')
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(self.target(report, 'tasks')['status'], 'disabled')
        self.assertEqual(self.target(report, 'handoff')['status'], 'disabled')
        self.assertEqual(self.target(report, 'log')['status'], 'missing')
        self.assertEqual(report['status'], 'ready')

    def test_a_note_in_a_missing_folder_is_missing(self):
        self.config['tasks_note'] = 'nowhere/tasks.md'
        self.write_config()
        self.assertEqual(self.target(capacity.diagnose_capacity(self.vault), 'tasks')['status'],
                         'missing')

    def test_a_path_with_a_line_break_cannot_split_the_text_report(self):
        self.config['tasks_note'] = '00-meta/one\nforged_line: ok.md'
        self.write_config()
        report = capacity.diagnose_capacity(self.vault)
        target = self.target(report, 'tasks')
        self.assertEqual((target['status'], target['path']), ('unsafe_path', ''))
        self.assertFalse(any(line.startswith('forged_line') for line in capacity.text_lines(report)))

    def test_targets_outside_the_policy_are_not_measured(self):
        self.write('private/secret.md', b'x' * (vault_recall.MAX_FILE_BYTES + 1))
        self.write('00-meta/plain.txt', b'x')
        self.config['tasks_note'] = 'private/secret.md'
        self.config['log_note'] = '00-meta/plain.txt'
        self.write_config()
        report = capacity.diagnose_capacity(self.vault)
        for role in ('tasks', 'log'):
            target = self.target(report, role)
            self.assertEqual(target['status'], 'unsafe_path')
            self.assertIsNone(target['size_bytes'])
            self.assertEqual(target['limits'], [])
        self.assertEqual((report['status'], report['reason_code']), ('fail', 'note_path_unsafe'))
        self.assertFalse(report['complete'])

    def test_a_hardlinked_target_is_refused(self):
        source = self.write('00-meta/hot.md', b'small\n')
        try:
            os.link(source, self.vault / 'linked.md')
        except OSError:
            self.skipTest('hard links unavailable')
        report = capacity.diagnose_capacity(self.vault)
        target = self.target(report, 'hot')
        self.assertEqual((target['status'], target['reason_code']), ('unsafe_path', 'unsafe_file'))

    def test_a_vault_without_config_is_a_fixed_code(self):
        (self.vault / '00-meta/vault-config.json').unlink()
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual((report['status'], report['targets']), ('unavailable', []))
        self.assertEqual(report['history'], 'not_read')
        self.assertRegex(report['reason_code'], r'^[a-z_]+$')
        self.assertNotIn(str(self.vault), json.dumps(report))

    def test_the_guide_lists_every_report_history_value(self):
        row = next(line for line in (ROOT / 'docs/capacity.md').read_text(encoding='utf-8').splitlines()
                   if line.startswith('| `history` |'))
        names = set(re.findall(r'`([a-z_]+)`', row)) - {'history'}
        self.assertEqual(names, {'available', 'not_read', *capacity._RUN_FAILURES})

    def test_without_git_only_usage_is_reported(self):
        self.write('00-meta/hot.md', b'x' * (vault_state.MAX_BYTES - 10))
        with mock.patch.object(capacity.subprocess, 'run', side_effect=FileNotFoundError()):
            report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(report['history'], 'git_unavailable')
        self.assertFalse(report['complete'])
        target = self.target(report, 'hot')
        self.assertEqual(target['status'], 'warning')
        self.assertIsNone(self.limit(target, 'state_read')['estimated_days_to_limit'])
        self.assertEqual(target['history']['status'], 'git_unavailable')

    def test_a_git_in_the_current_folder_is_not_run(self):
        self.write('00-meta/hot.md', b'small\n')
        planted = self.vault / ('git.exe' if os.name == 'nt' else 'git')
        planted.write_bytes(b'planted\n')
        planted.chmod(0o755)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.vault)
        seen = []
        real = subprocess.run

        def run(command, **options):
            seen.append(command[0])
            return real(command, **options)

        with mock.patch.object(capacity.subprocess, 'run', run):
            capacity.diagnose_capacity(self.vault)
        self.assertTrue(seen)
        for program in seen:
            self.assertTrue(os.path.isabs(program))
            self.assertNotEqual(Path(program).parent.resolve(), self.vault.resolve())

    def test_no_git_on_the_path_is_git_unavailable(self):
        self.write('00-meta/hot.md', b'small\n')
        with mock.patch.object(capacity, 'git_executable', lambda: None):
            report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(report['history'], 'git_unavailable')

    def test_a_folder_outside_git_reports_usage_only(self):
        self.write('00-meta/hot.md', b'small\n')
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(report['history'], 'git_history_unavailable')
        self.assertEqual(self.target(report, 'hot')['status'], 'ready')

    def test_git_failure_stops_after_one_attempt(self):
        for name in ('handoff', 'hot', 'log', 'tasks', 'index'):
            self.write(f'00-meta/{name}.md', b'small\n')
        with mock.patch.object(capacity.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('git', 1)) as run:
            report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report['history'], 'git_timeout')


class ForecastTests(CapacityCase):
    def setUp(self):
        super().setUp()
        self.init_repo()

    def test_steady_growth_gives_an_estimated_reach_time(self):
        start = vault_state.MAX_BYTES - 8 * KIB
        points = [(START + day * DAY, start + day * KIB) for day in range(5)]
        self.commit_sizes('00-meta/hot.md', points)
        report = capacity.diagnose_capacity(self.vault)
        target = self.target(report, 'hot')
        history = target['history']
        self.assertEqual((history['status'], history['samples'], history['estimate']),
                         ('estimated', 5, True))
        self.assertEqual(history['window_days'], 4.0)
        self.assertEqual(history['slope_bytes_per_day'], 1024.0)
        self.assertEqual(self.limit(target, 'state_read')['estimated_days_to_limit'], 4.0)
        recall = self.limit(target, 'recall_file')
        self.assertEqual(recall['estimated_days_to_limit'],
                         round((vault_recall.MAX_FILE_BYTES - points[-1][1]) / KIB, 1))
        self.assertTrue(report['complete'])
        self.assertEqual(report['history'], 'available')

    def test_forecast_inside_the_horizon_warns_below_the_usage_threshold(self):
        limit = vault_state.MAX_BYTES
        size = limit * 7 // 10
        step = (limit - size) // 10
        points = [(START + day * DAY, size - (2 - day) * step) for day in range(3)]
        self.commit_sizes('00-meta/hot.md', points)
        target = self.target(capacity.diagnose_capacity(self.vault), 'hot')
        self.assertLess(size / limit, .8)
        self.assertEqual(self.limit(target, 'state_read')['estimated_days_to_limit'], 10.0)
        self.assertEqual((target['status'], target['reason_code']),
                         ('warning', 'forecast_within_horizon'))

    def test_forecast_uses_the_working_file_as_the_current_size(self):
        self.commit_sizes('00-meta/hot.md', [(START, 1000), (START + DAY, 2000)])
        self.write('00-meta/hot.md', b'x' * 3000)
        target = self.target(capacity.diagnose_capacity(self.vault), 'hot')
        self.assertEqual(target['size_bytes'], 3000)
        self.assertEqual(self.limit(target, 'state_read')['estimated_days_to_limit'],
                         round((vault_state.MAX_BYTES - 3000) / 1000, 1))

    def test_a_limit_already_passed_has_no_reach_time(self):
        size = vault_state.MAX_BYTES + 100
        self.commit_sizes('00-meta/hot.md', [(START, size - 100), (START + DAY, size)])
        target = self.target(capacity.diagnose_capacity(self.vault), 'hot')
        self.assertIsNone(self.limit(target, 'state_read')['estimated_days_to_limit'])
        self.assertIsNotNone(self.limit(target, 'recall_file')['estimated_days_to_limit'])

    def test_identical_commit_times_have_no_reach_time(self):
        with self.assertRaises(statistics.StatisticsError):
            statistics.linear_regression([1.0, 1.0], [1.0, 2.0])
        self.commit_sizes('00-meta/hot.md', [(START, 100), (START, 200), (START, 300)])
        target = self.target(capacity.diagnose_capacity(self.vault), 'hot')
        self.assertEqual(target['history']['status'], 'same_commit_time')
        self.assertIsNone(target['history']['slope_bytes_per_day'])
        self.assertIsNone(self.limit(target, 'state_read')['estimated_days_to_limit'])

    def test_shrinking_and_flat_history_have_no_reach_time(self):
        self.commit_sizes('00-meta/hot.md', [(START, 3000), (START + DAY, 2000)])
        self.commit_sizes('00-meta/log.md', [(START + 2 * DAY, 2000)])
        self.write('00-meta/log.md', b'y' * 2000)
        self.git('add', '--', '00-meta/log.md')
        self.git('commit', '-q', '-m', 'fixture', date=START + 3 * DAY)
        report = capacity.diagnose_capacity(self.vault)
        for role in ('hot', 'log'):
            target = self.target(report, role)
            self.assertEqual(target['history']['status'], 'not_growing')
            self.assertIsNone(self.limit(target, 'recall_file')['estimated_days_to_limit'])

    def test_a_single_sample_and_an_untracked_note_have_no_reach_time(self):
        self.commit_sizes('00-meta/hot.md', [(START, 100)])
        self.write('00-meta/log.md', b'untracked\n')
        report = capacity.diagnose_capacity(self.vault)
        self.assertEqual(self.target(report, 'hot')['history']['status'], 'insufficient_history')
        self.assertEqual(self.target(report, 'log')['history']['samples'], 0)
        self.assertEqual(self.target(report, 'log')['history']['status'], 'insufficient_history')
        self.assertEqual(report['history'], 'available')

    def test_history_is_bounded_to_forty_commits(self):
        points = [(START + day * DAY, 100 + day) for day in range(capacity.HISTORY_COMMITS + 3)]
        self.commit_sizes('00-meta/hot.md', points)
        history = self.target(capacity.diagnose_capacity(self.vault), 'hot')['history']
        self.assertEqual(history['samples'], capacity.HISTORY_COMMITS)
        self.assertEqual(history['window_days'], float(capacity.HISTORY_COMMITS - 1))

    def test_a_commit_that_deleted_the_note_is_not_a_sample(self):
        self.commit_sizes('00-meta/hot.md', [(START, 100), (START + DAY, 200)])
        self.git('rm', '-q', '--', '00-meta/hot.md')
        self.git('commit', '-q', '-m', 'fixture', date=START + 2 * DAY)
        self.commit_sizes('00-meta/hot.md', [(START + 3 * DAY, 300)])
        history = self.target(capacity.diagnose_capacity(self.vault), 'hot')['history']
        self.assertEqual(history['samples'], 3)

    def test_paths_are_passed_literally_with_hangul_spaces_and_pathspec_characters(self):
        for relative in ('00-meta/한글 메모.md', '00-meta/[draft] note.md'):
            with self.subTest(relative=relative):
                self.config['tasks_note'] = relative
                self.write_config()
                self.commit_sizes(relative, [(START, 1000), (START + DAY, 2000)])
                target = self.target(capacity.diagnose_capacity(self.vault), 'tasks')
                self.assertEqual(target['path'], relative)
                self.assertEqual(target['history']['samples'], 2)
                self.assertEqual(target['history']['slope_bytes_per_day'], 1000.0)

    def test_a_pathspec_pattern_in_a_name_does_not_match_another_note(self):
        relative = '00-meta/[ab] note.md'
        self.config['tasks_note'] = relative
        self.write_config()
        self.commit_sizes(relative, [(START, 1000), (START + DAY, 2000)])
        self.commit_sizes('00-meta/a note.md', [(START + 2 * DAY, 3000), (START + 3 * DAY, 4000)])
        history = self.target(capacity.diagnose_capacity(self.vault), 'tasks')['history']
        self.assertEqual((history['samples'], history['window_days']), (2, 1.0))

    def test_a_vault_in_a_repository_subfolder_reads_its_own_paths(self):
        self.commit_sizes('00-meta/hot.md', [(START, 1000), (START + DAY, 2000)])
        inner = self.vault / 'nested'
        inner.mkdir()
        (inner / '00-meta').mkdir()
        (inner / '00-meta/vault-config.json').write_bytes(
            json.dumps({'hot_note': '00-meta/hot.md'}).encode())
        (inner / '00-meta/hot.md').write_bytes(b'x' * 10)
        self.git('add', '--', 'nested')
        self.git('commit', '-q', '-m', 'fixture', date=START + 2 * DAY)
        (inner / '00-meta/hot.md').write_bytes(b'x' * 20)
        self.git('add', '--', 'nested')
        self.git('commit', '-q', '-m', 'fixture', date=START + 3 * DAY)
        history = self.target(capacity.diagnose_capacity(inner), 'hot')['history']
        self.assertEqual((history['samples'], history['slope_bytes_per_day']), (2, 10.0))

    def test_unexpected_cat_file_output_is_not_a_forecast(self):
        self.commit_sizes('00-meta/hot.md', [(START, 1000), (START + DAY, 2000)])
        real = subprocess.run
        def run(command, **options):
            result = real(command, **options)
            if 'cat-file' in command:
                result.stdout = result.stdout.splitlines(True)[0]
            return result
        with mock.patch.object(capacity.subprocess, 'run', run):
            report = capacity.diagnose_capacity(self.vault)
        target = self.target(report, 'hot')
        self.assertEqual(target['history']['status'], 'history_unreadable')
        self.assertIsNone(self.limit(target, 'state_read')['estimated_days_to_limit'])
        self.assertFalse(report['complete'])

    def test_each_note_uses_one_log_and_one_cat_file_call(self):
        self.commit_sizes('00-meta/hot.md', [(START, 1000), (START + DAY, 2000)])
        self.commit_sizes('00-meta/log.md', [(START + 2 * DAY, 1000), (START + 3 * DAY, 2000)])
        self.config['handoff_note'] = '00-meta/hot.md'
        self.write_config()
        real = subprocess.run
        commands = []
        def run(command, **options):
            commands.append(command)
            return real(command, **options)
        with mock.patch.object(capacity.subprocess, 'run', run):
            capacity.diagnose_capacity(self.vault)
        self.assertEqual(len(commands), 4)
        self.assertTrue(all('core.quotepath=false' in command for command in commands))


class DoctorTests(CapacityCase):
    def run_doctor(self, *arguments):
        return subprocess.run([sys.executable, '-B', str(DOCTOR), '--vault', str(self.vault),
                               *arguments], capture_output=True, text=True, encoding='utf-8',
                              timeout=60)

    def fingerprint(self):
        return {path.relative_to(self.vault).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.vault.rglob('*')) if path.is_file()}

    def test_doctor_output_is_unchanged_without_the_flag(self):
        self.write('00-meta/hot.md', b'small\n')
        result = self.run_doctor('--format', 'json')
        self.assertNotIn('capacity', json.loads(result.stdout))
        self.assertNotIn('capacity', self.run_doctor().stdout)

    def test_doctor_flag_adds_a_report_and_keeps_the_exit_code(self):
        self.write('00-meta/hot.md', b'small\n')
        self.write('00-meta/handoff.md', b'small\n')
        self.write('00-meta/log.md', b'x' * (vault_state.MAX_BYTES + 1))
        plain = self.run_doctor('--format', 'json')
        result = self.run_doctor('--format', 'json', '--capacity')
        self.assertEqual(result.returncode, plain.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['capacity']['status'], 'fail')
        self.assertEqual(report['capacity']['schema_version'], 1)

    def test_doctor_text_lines_mark_the_value_as_an_estimate(self):
        self.init_repo()
        self.commit_sizes('00-meta/hot.md', [(START, 1000), (START + DAY, 2000)])
        result = self.run_doctor('--capacity')
        self.assertIn('capacity: ready (within_limits)', result.stdout)
        self.assertIn('capacity_history: available', result.stdout)
        line = next(item for item in result.stdout.splitlines()
                    if item.startswith('capacity_target: hot'))
        self.assertIn('00-meta/hot.md', line)
        self.assertIn('estimated_days', line)
        self.assertIn('estimate', result.stdout)

    def test_doctor_stays_fail_soft_when_the_module_fails(self):
        self.write('00-meta/hot.md', b'small\n')
        import importlib.util
        spec = importlib.util.spec_from_file_location('doctor_capacity_test', DOCTOR)
        doctor = importlib.util.module_from_spec(spec)
        saved = list(sys.path)
        try:
            sys.modules[spec.name] = doctor
            spec.loader.exec_module(doctor)
            output = []
            with mock.patch.object(capacity, 'diagnose_capacity', side_effect=OSError('SECRET')), \
                 mock.patch('builtins.print', lambda text, **_: output.append(text)):
                code = doctor.main(['--vault', str(self.vault), '--capacity'])
        finally:
            sys.path[:] = saved
        self.assertIn('capacity: unavailable (probe_failed)', output[0])
        self.assertNotIn('SECRET', output[0])
        self.assertIn(code, (0, 1))

    def test_doctor_stays_fail_soft_when_the_module_is_missing(self):
        self.write('00-meta/hot.md', b'small\n')
        import importlib.util
        spec = importlib.util.spec_from_file_location('doctor_capacity_gone', DOCTOR)
        doctor = importlib.util.module_from_spec(spec)
        saved = list(sys.path)
        try:
            sys.modules[spec.name] = doctor
            spec.loader.exec_module(doctor)
            output = []
            with mock.patch.dict(sys.modules, {'vault_capacity': None}), \
                 mock.patch('builtins.print', lambda text, **_: output.append(text)):
                doctor.main(['--vault', str(self.vault), '--capacity'])
                doctor.main(['--vault', str(self.vault), '--capacity', '--format', 'json'])
        finally:
            sys.path[:] = saved
        self.assertIn('capacity: unavailable (probe_failed)', output[0])
        self.assertEqual(json.loads(output[1])['capacity']['reason_code'], 'probe_failed')

    def test_capacity_changes_nothing_in_the_vault(self):
        self.init_repo()
        self.commit_sizes('00-meta/hot.md', [(START, 1000), (START + DAY, 2000)])
        self.write('00-meta/log.md', b'x' * (vault_state.MAX_BYTES + 1))
        before = self.fingerprint()
        result = self.run_doctor('--capacity', '--format', 'json')
        self.assertIn('capacity', json.loads(result.stdout))
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse((self.vault / vault_state.RUNTIME_DIR).exists())


if __name__ == '__main__':
    unittest.main()
