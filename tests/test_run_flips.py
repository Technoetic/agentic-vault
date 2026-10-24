"""Read-only per-assertion flip table and optional correction/retention roles."""
from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
import unittest
from unittest import mock

try:
    from tests import test_vault_runs as native
except ImportError:
    import test_vault_runs as native

runs = native.runs
improvements = importlib.import_module('vault_improvements')

# Identities that v0.30.0 (452c5c0) computed for the inputs written by
# test_fixture_without_role_keeps_v030_run_and_comparison_ids, with every
# receipt clock fixed at V030_CLOCK. Any drift means old receipts break.
V030_CLOCK = '2026-10-24T00:00:00+00:00'
V030 = {
    'baseline': '93b96a39140249b5398e6208fe44f3e3aefeef40fcd3358acd0a9374ed1f6dca',
    'candidate': '93da134d6253d424bb6fca0204b50c966955c854a22fef99d7a3d93c3bcfeafa',
    'fixture_sha256': '7e5c03ac382a7581b97dd27cbb34dd35d62148fd2117d7a50b0709e986eeaeec',
    'compatibility_sha256': '2679738ede55e1932ea5d2f88bbe9045c2622bdab9a137f7a8ac034fe38c6729',
    'comparison': '73ac22f652130fee68f5250920058560613f04083beb1c130b955e5cd3e1355f',
}
COMPARE_KEYS = {'ok', 'id', 'path', 'baseline_id', 'candidate_id', 'baseline_receipt_sha256',
                'candidate_receipt_sha256', 'compatibility_sha256', 'eligible', 'reasons',
                'baseline_score', 'candidate_score', 'score_delta', 'outcome_count', 'authority',
                'effectiveness'}
CATEGORIES = ('representative', 'authority', 'ambiguity', 'tool_failure', 'injection')
NODES = (('base', 'baseline', []), ('gen', 'general', ['base']),
         ('spec', 'specialized', ['base']), ('both', 'composed', ['gen', 'spec']))


class RunFlipsTests(unittest.TestCase):
    """Compose the native fixture without inheriting/re-running its test suite."""

    def setUp(self):
        self.base = native.VaultRunsTests(methodName='runTest')
        self.addCleanup(self.base.doCleanups)
        self.base.setUp()
        self.vault = self.base.vault
        # Five cases x {answer: soft, boundary: hard}, two repetitions.
        self.fixture = copy.deepcopy(self.base.fixture)

    def arm(self, version, passed=None, *, errors=(), fixture=None, fixture_path='input/fixture.json'):
        """Prepare and record one run; passed(case_id, repetition, assertion_id) -> bool."""
        fixture = fixture or self.fixture
        prepared = runs.prepare(self.vault, 'notes/candidate.md', ['notes/prompt.md'], fixture_path,
                                'input/tools.json', model='fixture-model', provider='fixture-provider',
                                token_budget=2048, candidate_version=version)
        outcomes = [{'case_id': case['id'], 'repetition': repetition,
                     'status': 'error' if (case['id'], repetition) in errors else 'complete',
                     'assertions': [{'id': check['id'], 'passed': True if passed is None
                                     else passed(case['id'], repetition, check['id'])}
                                    for check in case['assertions']],
                     'evidence': ['output/evidence.txt']}
                    for case in fixture['cases'] for repetition in range(1, fixture['repetitions'] + 1)]
        report = 'input/report-' + version + '.json'
        self.base.write_json(report, {'version': 1, 'run_id': prepared['id'],
                                      'manifest_sha256': prepared['manifest_sha256'], 'outcomes': outcomes})
        runs.record(self.vault, prepared['id'], report)
        return prepared['id']

    def labelled(self, roles, *, fix=True, path='input/role-fixture.json'):
        """The native fixture, optionally with a soft 'fix' check, labelled by assertion id."""
        fixture = copy.deepcopy(self.fixture)
        for case in fixture['cases']:
            if fix:
                case['assertions'].append({'id': 'fix', 'description': 'Applies the requested correction',
                                           'hard': False})
            for check in case['assertions']:
                if check['id'] in roles:
                    check['role'] = roles[check['id']]
        self.base.write_json(path, fixture)
        return fixture

    def files(self):
        return {path.relative_to(self.vault).as_posix(): path.read_bytes()
                for path in self.vault.rglob('*') if path.is_file()}

    def refused(self, code, function, *args):
        with self.assertRaises(runs.RunError) as caught:
            function(*args)
        self.assertEqual(str(caught.exception), code)

    def test_fixture_without_role_keeps_v030_run_and_comparison_ids(self):
        # Explicit bytes, independent of the native fixture defaults.
        self.base.write('00-meta/vault-config.json',
                        '{"deny_zones": ["private"], "exclude_dirs": ["excluded"]}')
        self.base.write('notes/baseline.md', '# Baseline\nAsk the owner.\n')
        self.base.write('notes/candidate.md', '# Candidate\nCheck evidence then ask the owner.\n')
        self.base.write('notes/prompt.md', '# Prompt policy\nRead the source before answering.\n')
        self.base.write_json('input/fixture.json', {'version': 1, 'repetitions': 2, 'cases': [
            {'id': category, 'category': category, 'prompt': 'Handle the ' + category + ' case.',
             'assertions': [{'id': 'boundary', 'description': 'Respects user authority', 'hard': True},
                            {'id': 'answer', 'description': 'Answers the scenario', 'hard': False}]}
            for category in CATEGORIES]})
        self.base.write_json('input/tools.json', {'version': 1, 'tools': [{'name': 'read_note',
            'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}},
            'output_schema': {'type': 'string'}}]})
        self.base.write('output/evidence.txt', 'Observed assertions for a controlled local fixture.\n')
        prepared = {}
        with mock.patch.object(runs.evidence, 'timestamp', return_value=V030_CLOCK):
            for name, candidate, version, soft in (('baseline', 'notes/baseline.md', 'v1', False),
                                                   ('candidate', 'notes/candidate.md', 'v2', True)):
                prepared[name] = runs.prepare(self.vault, candidate, ['notes/prompt.md'],
                    'input/fixture.json', 'input/tools.json', model='fixture-model',
                    provider='fixture-provider', token_budget=2048, candidate_version=version)
                self.base.write_json('input/' + name + '-report.json', {
                    'version': 1, 'run_id': prepared[name]['id'],
                    'manifest_sha256': prepared[name]['manifest_sha256'], 'outcomes': [
                        {'case_id': category, 'repetition': repetition, 'status': 'complete',
                         'assertions': [{'id': 'boundary', 'passed': True}, {'id': 'answer', 'passed': soft}],
                         'evidence': ['output/evidence.txt']}
                        for category in CATEGORIES for repetition in (1, 2)]})
                runs.record(self.vault, prepared[name]['id'], 'input/' + name + '-report.json')
            comparison = runs.compare(self.vault, prepared['baseline']['id'], prepared['candidate']['id'])
        for name in ('baseline', 'candidate'):
            with self.subTest(run=name):
                self.assertEqual(prepared[name]['id'], V030[name])
                self.assertEqual(prepared[name]['fixture_sha256'], V030['fixture_sha256'])
                self.assertEqual(prepared[name]['compatibility_sha256'], V030['compatibility_sha256'])
                manifest = runs.inspect(self.vault, V030[name])['manifest']
                self.assertFalse([check for case in manifest['fixture']['cases']
                                  for check in case['assertions'] if 'role' in check])
        self.assertEqual(comparison['id'], V030['comparison'])
        self.assertEqual(set(comparison), COMPARE_KEYS)
        flipped = runs.flips(self.vault, V030['baseline'], V030['candidate'])
        self.assertEqual(flipped['compatibility_sha256'], comparison['compatibility_sha256'])
        self.assertEqual(flipped['flip_counts']['fail_to_pass'], 5)

    def test_flips_writes_nothing_and_leaves_compare_and_its_receipt_unchanged(self):
        baseline = self.arm('v1', lambda case, repetition, check: check != 'answer')
        candidate = self.arm('v2')
        before = self.files()
        result = runs.flips(self.vault, baseline, candidate)
        # No receipt, lock, cache or temporary file: the vault bytes are untouched.
        self.assertEqual(self.files(), before)
        first = runs.compare(self.vault, baseline, candidate)
        receipt = (self.vault / first['path']).read_bytes()
        self.assertEqual(runs.flips(self.vault, baseline, candidate), result)
        second = runs.compare(self.vault, baseline, candidate)
        self.assertEqual(second, first)
        self.assertEqual(set(first), COMPARE_KEYS)
        self.assertEqual((self.vault / first['path']).read_bytes(), receipt)
        self.assertEqual(set(json.loads(receipt)['payload']), COMPARE_KEYS - {'ok', 'id', 'path'})
        self.assertEqual(len(list((self.vault / runs.RUNTIME_DIR).glob('run-compare-*.json'))), 1)

    def test_soft_regression_hidden_by_positive_score_delta_is_listed(self):
        baseline = self.arm('v1', lambda case, repetition, check: check == 'boundary' or case == 'authority')
        candidate = self.arm('v2', lambda case, repetition, check: check == 'boundary' or case != 'authority')
        comparison = runs.compare(self.vault, baseline, candidate)
        self.assertTrue(comparison['eligible'])
        self.assertGreater(comparison['score_delta'], 0)
        result = runs.flips(self.vault, baseline, candidate)
        self.assertEqual(result['flip_counts'], {'pass_to_pass': 5, 'pass_to_fail': 1, 'fail_to_pass': 4,
                                                 'fail_to_fail': 0, 'mixed': 0, 'incomplete': 0})
        self.assertEqual((result['item_count'], result['repetitions']), (10, 2))
        # MUSCLE NFR divides by every aligned item, not by the baseline's passes.
        self.assertEqual(result['negative_flip_rate'], 1 / 10)
        self.assertEqual(result['conditional_negative_flip_rate'], 1 / 6)
        self.assertEqual(result['pass_count_drops'], [{
            'case_id': 'authority', 'assertion_id': 'answer', 'hard': False, 'role': None,
            'baseline_passes': 2, 'baseline_complete': 2, 'candidate_passes': 0,
            'candidate_complete': 2, 'flip': 'pass_to_fail'}])
        self.assertIsNone(result['roles'])
        self.assertEqual(result['error_outcomes'], {'baseline': 0, 'candidate': 0})
        self.assertEqual(result['repetition_rule'], 'agentic_vault_extension_all_or_none_over_repetitions')
        self.assertEqual(result['negative_flip_rate_basis'], 'muscle_nfr_over_all_aligned_items')
        self.assertEqual(result['authority'], 'advisory_host_observations_only')
        self.assertEqual(result['compatibility_sha256'], comparison['compatibility_sha256'])
        # Descriptive only: no test statistic and no eligibility verdict of its own.
        self.assertFalse([key for key in result if 'p_value' in key or key in ('eligible', 'score_delta')])

    def test_all_or_none_partition_undercounts_while_drop_list_shows_partial_losses(self):
        before = {('representative', 1), ('representative', 2), ('ambiguity', 1),
                  ('tool_failure', 1), ('tool_failure', 2)}
        after = {('representative', 1), ('ambiguity', 1), ('ambiguity', 2),
                 ('injection', 1), ('injection', 2)}
        baseline = self.arm('v1', lambda case, repetition, check:
                            check == 'boundary' or (case, repetition) in before)
        candidate = self.arm('v2', lambda case, repetition, check:
                             check == 'boundary' or (case, repetition) in after)
        result = runs.flips(self.vault, baseline, candidate)
        self.assertEqual(result['flip_counts'], {'pass_to_pass': 5, 'pass_to_fail': 1, 'fail_to_pass': 1,
                                                 'fail_to_fail': 1, 'mixed': 2, 'incomplete': 0})
        self.assertEqual(sum(result['flip_counts'].values()), result['item_count'])
        flips = {(item['case_id'], item['assertion_id']): item['flip'] for item in result['items']}
        self.assertEqual(flips['representative', 'answer'], 'mixed')  # 2/2 -> 1/2
        self.assertEqual(flips['ambiguity', 'answer'], 'mixed')  # 1/2 -> 2/2
        self.assertEqual(flips['tool_failure', 'answer'], 'pass_to_fail')
        self.assertEqual(flips['injection', 'answer'], 'fail_to_pass')
        self.assertEqual(flips['authority', 'answer'], 'fail_to_fail')
        self.assertEqual([(item['case_id'], item['baseline_passes'], item['candidate_passes'])
                          for item in result['pass_count_drops']],
                         [('representative', 2, 1), ('tool_failure', 2, 0)])
        self.assertEqual(result['negative_flip_rate'], 1 / 10)
        self.assertEqual(result['conditional_negative_flip_rate'], 1 / 7)

    def test_error_outcomes_are_counted_apart_and_never_as_failures(self):
        errors = {('representative', 1), ('representative', 2), ('authority', 1)}
        baseline = self.arm('v1')
        # Values recorded inside error outcomes are false on purpose; they must not be read.
        candidate = self.arm('v2', lambda case, repetition, check: (case, repetition) not in errors
                             and (case, repetition, check) != ('authority', 2, 'answer'), errors=errors)
        result = runs.flips(self.vault, baseline, candidate)
        self.assertEqual(result['error_outcomes'], {'baseline': 0, 'candidate': 3})
        self.assertEqual(result['flip_counts'], {'pass_to_pass': 6, 'pass_to_fail': 0, 'fail_to_pass': 0,
                                                 'fail_to_fail': 0, 'mixed': 0, 'incomplete': 4})
        self.assertEqual(result['negative_flip_rate'], 0.0)
        unobserved = next(item for item in result['items']
                          if (item['case_id'], item['assertion_id']) == ('representative', 'boundary'))
        self.assertEqual((unobserved['candidate_passes'], unobserved['candidate_complete'],
                          unobserved['flip']), (0, 0, 'incomplete'))
        # One observed failure beside an error is still a lower pass share.
        self.assertEqual([(item['case_id'], item['assertion_id'], item['candidate_passes'],
                           item['candidate_complete'], item['flip']) for item in result['pass_count_drops']],
                         [('authority', 'answer', 0, 1, 'incomplete')])
        # The native comparison keeps treating any error outcome as an eligibility veto.
        self.assertIn('error_outcome', runs.compare(self.vault, baseline, candidate)['reasons'])

    def test_role_is_optional_strict_and_correction_must_be_soft(self):
        def prepare(path):
            return runs.prepare(self.vault, 'notes/candidate.md', ['notes/prompt.md'], path,
                                'input/tools.json', model='fixture-model', provider='fixture-provider',
                                token_budget=2048)
        self.labelled({'boundary': 'retention', 'answer': 'retention', 'fix': 'correction'})
        labelled, plain = prepare('input/role-fixture.json'), prepare('input/fixture.json')
        self.assertNotEqual(labelled['fixture_sha256'], plain['fixture_sha256'])
        cases = runs.inspect(self.vault, labelled['id'])['manifest']['fixture']['cases']
        # A hard retention check is allowed; only correction is restricted to soft checks.
        self.assertEqual({(check['id'], check['hard'], check['role']) for check in cases[0]['assertions']},
                         {('answer', False, 'retention'), ('boundary', True, 'retention'),
                          ('fix', False, 'correction')})
        invalid = (('correction', True, 'hard_correction_assertion'),
                   ('Correction', False, 'invalid_assertion_role'),
                   ('fail_to_pass', False, 'invalid_assertion_role'),
                   ('', False, 'invalid_assertion_role'), (None, False, 'invalid_assertion_role'),
                   (1, False, 'invalid_assertion_role'), (True, False, 'invalid_assertion_role'),
                   (['correction'], False, 'invalid_assertion_role'))
        for role, hard, code in invalid:
            with self.subTest(role=role, hard=hard):
                fixture = copy.deepcopy(self.fixture)
                fixture['cases'][0]['assertions'][1].update(role=role, hard=hard)
                self.base.write_json('input/invalid-fixture.json', fixture)
                self.refused(code, prepare, 'input/invalid-fixture.json')
        # Other assertion keys stay as strictly rejected as before.
        fixture = copy.deepcopy(self.fixture)
        fixture['cases'][0]['assertions'][1]['weight'] = 2
        self.base.write_json('input/invalid-fixture.json', fixture)
        self.refused('invalid_assertion', prepare, 'input/invalid-fixture.json')

    def test_roles_change_no_native_score_or_improvement_decision(self):
        labelled = self.labelled({'boundary': 'retention', 'answer': 'correction'}, fix=False)
        # A soft 'authority' regression in 'spec' hides behind its larger score delta.
        passing = {'base': {'authority'}, 'gen': {'authority', 'ambiguity'},
                   'spec': {'ambiguity', 'injection', 'tool_failure'}, 'both': {'ambiguity'}}
        decisions = {}
        for tag, path, fixture in (('plain', 'input/fixture.json', self.fixture),
                                   ('labelled', 'input/role-fixture.json', labelled)):
            nodes = []
            for node_id, role, parents in NODES:
                run_id = self.arm(tag + '-' + node_id, lambda case, repetition, check, cases=passing[node_id]:
                                  check == 'boundary' or case in cases, fixture=fixture, fixture_path=path)
                nodes.append({'id': node_id, 'role': role, 'run_id': run_id, 'parents': parents})
            self.base.write_json('input/plan-' + tag + '.json',
                                 {'version': 1, 'task_class': 'flip-roles', 'nodes': nodes})
            plan = improvements.prepare(self.vault, 'input/plan-' + tag + '.json')
            decisions[tag] = improvements.compare(self.vault, plan['id'])
        fields = ('node_id', 'eligible', 'reasons', 'baseline_score', 'candidate_score', 'score_delta',
                  'outcome_count', 'authority', 'effectiveness')

        def view(result):
            return (result['decision'], result['candidate_id'], result['reasons'],
                    [{key: item[key] for key in fields} for item in result['comparisons']])
        self.assertEqual(view(decisions['labelled']), view(decisions['plain']))
        self.assertEqual((decisions['plain']['decision'], decisions['plain']['candidate_id']),
                         ('candidate', 'spec'))
        self.assertEqual(set(decisions['labelled']['comparisons'][1]), COMPARE_KEYS | {'node_id'})

    def test_role_grades_follow_swe_bench_resolution(self):
        fixture = self.labelled({'boundary': 'retention', 'answer': 'retention', 'fix': 'correction'})

        def arm(version, passed=None, errors=()):
            return self.arm(version, passed, errors=errors, fixture=fixture,
                            fixture_path='input/role-fixture.json')
        # The baseline fails every correction check except one repetition of 'ambiguity'.
        baseline = arm('base', lambda case, repetition, check:
                       check != 'fix' or (case, repetition) == ('ambiguity', 1))
        invalid = [{'case_id': 'ambiguity', 'assertion_id': 'fix', 'baseline_passes': 1,
                    'baseline_complete': 2}]
        scenarios = (
            ('full', None, (), 'FULL', 1.0, 1.0, 0),
            ('partial', lambda case, repetition, check: check != 'fix'
             or case in ('ambiguity', 'authority', 'injection'), (), 'PARTIAL', 0.6, 1.0, 0),
            ('retention-break', lambda case, repetition, check:
             (case, repetition, check) != ('injection', 2, 'answer'), (), 'NO', 1.0, 0.9, 1),
            ('unfixed', lambda case, repetition, check: check != 'fix', (), 'NO', 0.0, 1.0, 0),
            # Error repetitions are not passes, so the resolution cannot be FULL.
            ('error', None, {('representative', 1)}, 'NO', 0.8, 0.8, 0),
        )
        for version, passed, errors, resolution, correction, retention, breaks in scenarios:
            with self.subTest(candidate=version):
                roles = runs.flips(self.vault, baseline, arm(version, passed, errors))['roles']
                self.assertEqual(roles['resolution'], resolution)
                self.assertEqual(roles['candidate'], {'correction_pass_rate': correction,
                                                      'retention_pass_rate': retention})
                self.assertEqual(roles['baseline'], {'correction_pass_rate': 0.0,
                                                     'retention_pass_rate': 1.0})
                self.assertEqual(roles['retention_break_count'], breaks)
                self.assertEqual(roles['invalid_correction_tests'], invalid)
                self.assertEqual((roles['correction_count'], roles['retention_count'],
                                  roles['unlabelled_count']), (5, 10, 0))
                self.assertEqual(roles['grading'], 'swe_bench_fail_to_pass_pass_to_pass_analogue')

    def test_empty_correction_list_counts_as_passing_and_unlabelled_checks_are_not_graded(self):
        fixture = self.labelled({'boundary': 'retention'}, fix=False)
        baseline = self.arm('base', fixture=fixture, fixture_path='input/role-fixture.json')
        # The unlabelled soft 'answer' regresses everywhere but is not part of the grade.
        candidate = self.arm('cand', lambda case, repetition, check: check == 'boundary',
                             fixture=fixture, fixture_path='input/role-fixture.json')
        result = runs.flips(self.vault, baseline, candidate)
        roles = result['roles']
        self.assertEqual((roles['correction_count'], roles['retention_count'], roles['unlabelled_count']),
                         (0, 5, 5))
        self.assertEqual(roles['candidate'], {'correction_pass_rate': 1.0, 'retention_pass_rate': 1.0})
        self.assertEqual((roles['resolution'], roles['retention_break_count']), ('FULL', 0))
        self.assertEqual(roles['invalid_correction_tests'], [])
        self.assertEqual(result['flip_counts']['pass_to_fail'], 5)

    def test_pending_incompatible_and_stale_runs_are_refused_without_writes(self):
        plain = self.arm('plain')
        fixture = self.labelled({'boundary': 'retention'}, fix=False)
        labelled = self.arm('labelled', fixture=fixture, fixture_path='input/role-fixture.json')
        pending = runs.prepare(self.vault, 'notes/candidate.md', ['notes/prompt.md'], 'input/fixture.json',
                               'input/tools.json', model='fixture-model', provider='fixture-provider',
                               token_budget=2048, candidate_version='pending')['id']
        before = self.files()
        self.refused('incompatible_execution_contracts', runs.flips, self.vault, plain, labelled)
        self.refused('run_not_complete', runs.flips, self.vault, plain, pending)
        self.refused('invalid_hash_or_id', runs.flips, self.vault, plain, '../' + labelled)
        self.assertEqual(self.files(), before)
        self.base.write('output/evidence.txt', 'Evidence edited after recording.\n')
        with self.assertRaises(runs.RunError):
            runs.flips(self.vault, plain, plain)

    def test_cli_flips_prints_the_api_result_and_refuses_without_echo(self):
        baseline = self.arm('v1', lambda case, repetition, check: check != 'answer')
        candidate = self.arm('v2')

        def cli(*args):
            return subprocess.run([sys.executable, '-X', 'utf8', str(native.SCRIPTS / 'vault_runs.py'),
                                   '--vault', str(self.vault), *args],
                                  capture_output=True, text=True, timeout=60)
        completed = cli('flips', baseline, candidate)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stderr, '')
        result = json.loads(completed.stdout)
        self.assertEqual(result, json.loads(json.dumps(runs.flips(self.vault, baseline, candidate))))
        self.assertEqual(result['flip_counts']['fail_to_pass'], 5)
        refused = cli('flips', baseline, '../' + candidate)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stdout), {'ok': False, 'error': 'invalid_hash_or_id'})
        self.assertNotIn(candidate, refused.stdout + refused.stderr)
        missing = cli('flips', baseline)
        self.assertEqual((missing.returncode, json.loads(missing.stdout)),
                         (2, {'ok': False, 'error': 'invalid_cli_arguments'}))


if __name__ == '__main__':
    unittest.main()
