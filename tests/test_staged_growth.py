"""Opt-in staged token growth check: HEAD is the baseline, nothing else is stored."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_healthcheck as health

NOTE = '00-meta/hot.md'


def body(words):
    return '---\ntitle: "Hot"\n---\n\n' + 'word ' * words + '\n'


class StagedGrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / 'vault'
        self.repo.mkdir()
        isolate_git(self)
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'core.autocrlf', 'false')

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.repo, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def config(self, **overrides):
        raw = {'required_keys': ['title'], 'enums': {}, 'hot_note': NOTE, 'handoff_note': '',
               'hot_max_tokens': 20, 'handoff_max_tokens': 0}
        raw.update(overrides)
        return health.validate_config(raw)

    def commit(self, text=None, *, path=NOTE):
        if text is not None:
            self.write(path, text)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'fixture')

    def stage(self, text, *, path=NOTE):
        self.write(path, text)
        self.git('add', '-A')

    def staged(self, **overrides):
        warnings = []
        errors = health.validate_staged(self.repo, self.config(**overrides),
                                        warning_diagnostics=warnings)
        return errors, warnings

    def assert_clean(self, **overrides):
        self.assertEqual(self.staged(**overrides), ([], []))

    def test_keys_stay_out_of_the_validated_config_by_default(self):
        config = self.config()
        self.assertNotIn('staged_growth_check', config)
        self.assertNotIn('staged_growth_max_tokens', config)
        self.assertNotIn('staged_growth_check', health.DEFAULT_CONFIG)

    def test_invalid_settings_fail_closed(self):
        for overrides in ({'staged_growth_check': 'strict'}, {'staged_growth_check': True},
                          {'staged_growth_check': ['warn']}, {'staged_growth_check': None},
                          {'staged_growth_max_tokens': -1}, {'staged_growth_max_tokens': '5'},
                          {'staged_growth_max_tokens': True}, {'staged_growth_max_tokens': 1.5}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(health.HealthcheckError):
                    self.config(**overrides)

    def test_absent_and_off_modes_do_not_read_the_baseline(self):
        self.commit(body(100))
        self.stage(body(200))
        with mock.patch.object(health, '_staged_growth_baseline',
                               side_effect=AssertionError('baseline read')):
            self.assert_clean()
            self.assert_clean(staged_growth_check='off')

    def test_warn_reports_growth_over_budget_without_blocking(self):
        self.commit(body(100))
        self.stage(body(101))
        errors, warnings = self.staged(staged_growth_check='warn')
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        finding = warnings[0]
        self.assertEqual((finding['code'], finding['severity'], finding['path']),
                         ('staged-token-growth', 'warning', NOTE))
        self.assertEqual(finding['details']['reason'], 'over_budget')
        self.assertEqual(finding['details']['budget_tokens'], 20)
        self.assertGreater(finding['details']['after_tokens'], finding['details']['before_tokens'])
        self.assertIn('[staged-token-growth]', health.format_extension_warning(finding))

    def test_block_reports_growth_over_budget_as_an_error(self):
        self.commit(body(100))
        self.stage(body(101))
        errors, warnings = self.staged(staged_growth_check='block')
        self.assertEqual(warnings, [])
        self.assertEqual(len(errors), 1)
        self.assertIn(NOTE, errors[0])
        self.assertIn('over_budget', errors[0])

    def test_warn_without_a_warning_list_is_silent_and_skips_the_baseline(self):
        self.commit(body(100))
        self.stage(body(200))
        with mock.patch.object(health, '_staged_growth_baseline',
                               side_effect=AssertionError('baseline read')):
            self.assertEqual(
                health.validate_staged(self.repo, self.config(staged_growth_check='warn')), [])

    def test_block_without_a_warning_list_still_blocks(self):
        self.commit(body(100))
        self.stage(body(101))
        errors = health.validate_staged(self.repo, self.config(staged_growth_check='block'))
        self.assertEqual(len(errors), 1)

    def test_shrinking_or_equal_note_over_budget_passes(self):
        self.commit(body(100))
        self.stage(body(99))
        self.assert_clean(staged_growth_check='block')
        self.git('commit', '-q', '-m', 'shrink')
        same_size = body(99).replace('word', 'wurd')
        self.stage(same_size)
        self.assert_clean(staged_growth_check='block')

    def test_growth_inside_the_budget_passes_without_a_growth_limit(self):
        self.commit(body(5))
        self.stage(body(8))
        self.assert_clean(staged_growth_check='block')

    def test_growth_limit_applies_inside_the_budget(self):
        self.commit(body(5))
        self.stage(body(30))
        errors, warnings = self.staged(staged_growth_check='warn', hot_max_tokens=10_000,
                                       staged_growth_max_tokens=10)
        self.assertEqual(errors, [])
        self.assertEqual(warnings[0]['details']['reason'], 'growth_limit')
        self.assertEqual(warnings[0]['details']['max_growth_tokens'], 10)

    def test_growth_limit_boundary_is_exclusive(self):
        before, after = body(5), body(15)
        allowed = health.estimate_tokens(after) - health.estimate_tokens(before)
        self.commit(before)
        self.stage(after)
        self.assert_clean(staged_growth_check='block', hot_max_tokens=10_000,
                          staged_growth_max_tokens=allowed)
        errors, _ = self.staged(staged_growth_check='block', hot_max_tokens=10_000,
                                staged_growth_max_tokens=allowed - 1)
        self.assertEqual(len(errors), 1)

    def test_a_note_exactly_on_its_budget_passes(self):
        after = body(15)
        budget = health.estimate_tokens(after)
        self.commit(body(1))
        self.stage(after)
        self.assert_clean(staged_growth_check='block', hot_max_tokens=budget)
        errors, _ = self.staged(staged_growth_check='block', hot_max_tokens=budget - 1)
        self.assertEqual(len(errors), 1)
        self.assertIn('over_budget', errors[0])

    def test_crossing_the_budget_counts_as_over_budget(self):
        self.commit(body(1))
        self.stage(body(100))
        errors, warnings = self.staged(staged_growth_check='warn')
        self.assertEqual((errors, warnings[0]['details']['reason']), ([], 'over_budget'))

    def test_new_note_has_no_baseline(self):
        self.commit('fixture\n', path='README.txt')
        self.stage(body(100))
        self.assert_clean(staged_growth_check='block', staged_growth_max_tokens=1)

    def test_unborn_head_has_no_baseline(self):
        self.stage(body(100))
        self.assert_clean(staged_growth_check='block')

    def test_rename_compares_with_the_old_path(self):
        self.commit(body(100), path='00-meta/older.md')
        self.git('mv', '00-meta/older.md', NOTE)
        self.assert_clean(staged_growth_check='block')
        self.stage(body(103))
        self.assertEqual(self.git('diff', '--cached', '--name-status').stdout.split()[0][:1], b'R')
        errors, _ = self.staged(staged_growth_check='block')
        self.assertEqual(len(errors), 1)

    def test_rename_that_shrinks_passes(self):
        self.commit(body(100), path='00-meta/older.md')
        self.git('mv', '00-meta/older.md', NOTE)
        self.stage(body(97))
        self.assert_clean(staged_growth_check='block')

    def test_renaming_a_budgeted_note_away_is_outside_the_check(self):
        self.commit(body(100))
        self.git('mv', NOTE, '00-meta/elsewhere.md')
        self.stage(body(103), path='00-meta/elsewhere.md')
        self.assert_clean(staged_growth_check='block')

    def test_only_budgeted_notes_are_checked(self):
        self.commit(body(100), path='00-meta/other.md')
        self.stage(body(200), path='00-meta/other.md')
        self.assert_clean(staged_growth_check='block')

    def test_a_zero_budget_turns_the_check_off_for_that_note(self):
        self.commit(body(100))
        self.stage(body(200))
        self.assert_clean(staged_growth_check='block', hot_max_tokens=0,
                          staged_growth_max_tokens=1)

    def test_the_handoff_note_uses_its_own_budget(self):
        path = '00-meta/handoff.md'
        self.commit(body(100), path=path)
        self.stage(body(101), path=path)
        errors, warnings = self.staged(staged_growth_check='warn', handoff_note=path,
                                       handoff_max_tokens=30)
        self.assertEqual(errors, [])
        self.assertEqual((warnings[0]['path'], warnings[0]['details']['budget_tokens']), (path, 30))

    def test_one_file_as_hot_and_handoff_takes_the_smaller_budget(self):
        self.commit(body(1))
        self.stage(body(100))
        for hot, handoff in ((10, 10_000), (10_000, 10)):
            with self.subTest(hot=hot, handoff=handoff):
                errors, warnings = self.staged(staged_growth_check='warn', handoff_note=NOTE,
                                               hot_max_tokens=hot, handoff_max_tokens=handoff)
                self.assertEqual(errors, [])
                self.assertEqual([item['details']['budget_tokens'] for item in warnings], [10])

    def test_hangul_is_measured_with_the_engine_estimator(self):
        path = '00-meta/한글 메모.md'
        self.commit('---\ntitle: "메모"\n---\n\n' + '가' * 100 + '\n', path=path)
        self.stage('---\ntitle: "메모"\n---\n\n' + '가' * 101 + '\n', path=path)
        errors, warnings = self.staged(staged_growth_check='warn', hot_note=path)
        self.assertEqual(errors, [])
        self.assertEqual(warnings[0]['path'], path)
        self.assertEqual(warnings[0]['details']['after_tokens'],
                         health.estimate_tokens(health.read_index_text(self.repo, path)))

    def test_byte_order_mark_change_is_not_growth(self):
        self.commit('﻿' + body(100))
        self.stage(body(100))
        self.assert_clean(staged_growth_check='block')

    def test_unreadable_baseline_warns_in_warn_mode_and_blocks_in_block_mode(self):
        self.commit(body(100))
        self.stage(body(101))
        failure = health.HealthcheckError('git show failed')
        with mock.patch.object(health, '_staged_growth_baseline', side_effect=failure):
            errors, warnings = self.staged(staged_growth_check='warn')
            self.assertEqual(errors, [])
            self.assertEqual([x['code'] for x in warnings], ['staged-token-growth-unverified'])
            self.assertNotIn('git show failed', json.dumps(warnings))
            errors, warnings = self.staged(staged_growth_check='block')
            self.assertEqual((len(errors), warnings), (1, []))
            self.assertNotIn('git show failed', errors[0])

    def test_baseline_that_is_not_utf8_is_unverified(self):
        self.write(NOTE, '')
        (self.repo / NOTE).write_bytes(b'\xff\xfe' + b'word ' * 100)
        self.commit()
        self.stage(body(101))
        errors, warnings = self.staged(staged_growth_check='warn')
        self.assertEqual(errors, [])
        self.assertEqual([x['code'] for x in warnings], ['staged-token-growth-unverified'])

    def test_no_result_file_is_written(self):
        self.commit(body(100))
        self.stage(body(101))
        listing = sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob('*')
                         if '.git' not in p.parts)
        self.staged(staged_growth_check='block')
        self.assertEqual(listing, sorted(p.relative_to(self.repo).as_posix()
                                         for p in self.repo.rglob('*') if '.git' not in p.parts))

    def test_the_vault_copy_runs_the_check_alone(self):
        # Vaults receive vault_healthcheck.py without its warning extension modules.
        self.write('00-meta/vault-config.json', json.dumps({
            'required_keys': ['title'], 'enums': {}, 'hot_note': NOTE, 'handoff_note': '',
            'hot_max_tokens': 20, 'staged_growth_check': 'warn'}))
        self.commit(body(100))
        self.stage(body(101))
        alone = Path(self.tmp.name) / 'alone'
        alone.mkdir()
        shutil.copy2(SCRIPTS / 'vault_healthcheck.py', alone / 'vault_healthcheck.py')
        result = subprocess.run(
            [sys.executable, '-B', str(alone / 'vault_healthcheck.py'), '--vault', str(self.repo),
             '--staged'], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('[staged-token-growth]', result.stderr)

        config = json.loads((self.repo / '00-meta/vault-config.json').read_text(encoding='utf-8'))
        config['staged_growth_check'] = 'block'
        self.write('00-meta/vault-config.json', json.dumps(config))
        self.git('add', '-A')
        blocked = subprocess.run(
            [sys.executable, '-B', str(alone / 'vault_healthcheck.py'), '--vault', str(self.repo),
             '--staged'], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        self.assertIn('over_budget', blocked.stderr)


if __name__ == '__main__':
    unittest.main()
