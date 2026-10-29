from __future__ import annotations

import contextlib
import copy
from functools import partial
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_runs as runs

try:
    improvements = importlib.import_module('vault_improvements')
except ModuleNotFoundError:
    improvements = None

# Receipt clocks in the UTC isoformat that vault_evidence.timestamp writes.
EARLIER = '2026-10-29T23:00:00+00:00'
PLANNED = '2026-10-30T00:00:00+00:00'
LATER = '2026-10-30T01:00:00+00:00'
ROLES = ('baseline', 'general', 'specialized', 'composed')
NODE_IDS = {'baseline': 'base', 'general': 'gen', 'specialized': 'spec', 'composed': 'both'}
PASSES = {'baseline': 0, 'general': 4, 'specialized': 2, 'composed': 10}
ORDER_KEYS = {'plan_created_at', 'recorded_before_plan', 'receipt_order'}
INSPECT_KEYS = {'ok', 'id', 'path', 'definition', 'compatibility_sha256', 'status',
                'incomplete_ids', 'nodes', 'authority', 'requires_review', 'applies_changes'}
COMPARE_KEYS = {'ok', 'id', 'status', 'decision', 'candidate_id', 'reasons', 'incomplete_ids',
                'comparisons', 'authority', 'requires_review', 'applies_changes'}


def clock(value):
    """Stamp every receipt published inside the block with created_at `value`."""
    return mock.patch.object(runs.evidence, 'timestamp', return_value=value)


class VaultImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(improvements, 'improvement experiment coordinator is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write_json('00-meta/vault-config.json', {
            'deny_zones': ['private'], 'exclude_dirs': ['excluded']})
        self.write('notes/prompt.md', '# Prompt\nAsk for evidence.\n')
        self.fixture = {'version': 1, 'repetitions': 2, 'cases': [
            {'id': category, 'category': category, 'prompt': 'Handle this scenario.',
             'assertions': [{'id': 'boundary', 'description': 'Respect authority', 'hard': True},
                            {'id': 'answer', 'description': 'Answer correctly', 'hard': False}]}
            for category in ('representative', 'authority', 'ambiguity', 'tool_failure', 'injection')]}
        self.write_json('input/fixture.json', self.fixture)
        self.write_json('input/tools.json', {'version': 1, 'tools': []})
        self.write('output/evidence.txt', 'Controlled fixture observations.\n')
        self.arms = {}
        for role in ('baseline', 'general', 'specialized', 'composed'):
            self.write('notes/' + role + '.md', '# ' + role + '\nCandidate instructions.\n')
            self.arms[role] = self.prepare_run(role)
        self.definition = {'version': 1, 'task_class': 'note-review', 'nodes': [
            {'id': 'base', 'role': 'baseline', 'run_id': self.arms['baseline']['id'], 'parents': []},
            {'id': 'gen', 'role': 'general', 'run_id': self.arms['general']['id'], 'parents': ['base']},
            {'id': 'spec', 'role': 'specialized', 'run_id': self.arms['specialized']['id'], 'parents': ['base']},
            {'id': 'both', 'role': 'composed', 'run_id': self.arms['composed']['id'], 'parents': ['spec', 'gen']}]}
        self.write_json('input/plan.json', self.definition)

    def write(self, relative, value):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode('utf-8') if isinstance(value, str) else value)
        return path

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value, allow_nan=False))

    def prepare_run(self, role, **kwargs):
        options = dict(model='fixture-model', provider='fixture-provider', token_budget=2048)
        options.update(kwargs)
        return runs.prepare(self.vault, 'notes/' + role + '.md', ['notes/prompt.md'],
                            'input/fixture.json', 'input/tools.json', **options)

    def plan(self):
        return improvements.prepare(self.vault, 'input/plan.json')

    def complete(self, role, passed=0, *, hard=True, error=False):
        arm = self.arms[role]
        outcomes = []
        for index, (case, repetition) in enumerate(
                (case, repetition) for case in self.fixture['cases'] for repetition in (1, 2)):
            outcomes.append({'case_id': case['id'], 'repetition': repetition,
                'status': 'error' if error and index == 0 else 'complete',
                'assertions': [{'id': 'boundary', 'passed': hard},
                               {'id': 'answer', 'passed': index < passed}],
                'evidence': ['output/evidence.txt']})
        relative = 'input/' + role + '-report.json'
        self.write_json(relative, {'version': 1, 'run_id': arm['id'],
            'manifest_sha256': arm['id'], 'outcomes': outcomes})
        return runs.record(self.vault, arm['id'], relative)

    def finish(self, counts=(0, 4, 2, 10)):
        for role, count in zip(self.arms, counts):
            self.complete(role, count)

    def report(self, *, verdict='helped', isolation='single_component', pairs=2, lesson=True):
        evidence = [{'run_id': self.arms[role]['id'], 'case_id': 'authority', 'repetition': rep}
                    for role in ('baseline', 'composed') for rep in range(1, pairs + 1)]
        return {'version': 1, 'edits': [{'node_id': 'both', 'component': 'instructions',
            'change': 'Add evidence checks.', 'observed': 'More recorded assertions passed.',
            'isolation': isolation, 'verdict': verdict, 'evidence': evidence}],
            'lesson': {'cue': 'When reviewing a note.', 'operation': 'Check evidence first.',
                       'edit_indices': [0], 'confidence': 'low'} if lesson else None}

    def record(self, plan, report=None):
        self.write_json('input/reflection.json', self.report() if report is None else report)
        return improvements.record(self.vault, plan['id'], 'input/reflection.json')

    def rejected(self, function, *args):
        with self.assertRaises(improvements.ImprovementError) as caught:
            function(*args)
        self.assertRegex(str(caught.exception), r'^[a-z_]+$')
        return str(caught.exception)

    def assert_advisory(self, result):
        self.assertEqual(result['authority'], 'advisory_host_observations_only')
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['applies_changes'])

    def fresh(self):
        """Another setUp vault with the same inputs, hence the same run and plan IDs."""
        case = type(self)(methodName='runTest')
        self.addCleanup(case.doCleanups)
        case.setUp()
        return case

    def ordered(self, before, *, rest=True):
        """Complete `before` roles, then prepare the plan, then (optionally) the rest."""
        for role in before:
            with clock(EARLIER):
                self.complete(role, PASSES[role])
        with clock(PLANNED):
            plan = self.plan()
        for role in ROLES:
            if rest and role not in before:
                with clock(LATER):
                    self.complete(role, PASSES[role])
        return plan

    def assert_receipt_order(self, plan_id, before, order):
        """The opt-in adds exactly three top-level keys, writes nothing, changes nothing else."""
        runtime = self.vault / runs.RUNTIME_DIR
        for function, keys in ((improvements.inspect, INSPECT_KEYS), (improvements.compare, COMPARE_KEYS)):
            default = function(self.vault, plan_id)
            files = {path.name: path.read_bytes() for path in runtime.iterdir()}
            opted = function(self.vault, plan_id, receipt_order=True)
            self.assertEqual({path.name: path.read_bytes() for path in runtime.iterdir()}, files)
            self.assertEqual(set(default), keys)
            self.assertEqual([key for key in opted if key not in ORDER_KEYS], list(default))
            self.assertEqual({key: value for key, value in opted.items() if key not in ORDER_KEYS}, default)
            self.assertEqual({key: opted[key] for key in ORDER_KEYS},
                             {'plan_created_at': PLANNED, 'receipt_order': order,
                              'recorded_before_plan': [NODE_IDS[role] for role in before]})
            for item in opted.get('comparisons', []):
                self.assertTrue(ORDER_KEYS.isdisjoint(item))
        return opted

    def test_prepare_reuses_immutable_plan_and_inspect_is_read_only(self):
        plan = self.plan()
        before = (self.vault / plan['path']).read_bytes()
        self.assertEqual(self.plan()['id'], plan['id'])
        self.assertEqual(before, (self.vault / plan['path']).read_bytes())
        files = set((self.vault / runs.RUNTIME_DIR).iterdir())
        inspected = improvements.inspect(self.vault, plan['id'])
        self.assertEqual(set((self.vault / runs.RUNTIME_DIR).iterdir()), files)
        self.assertEqual([n['id'] for n in inspected['nodes']], ['base', 'gen', 'spec', 'both'])
        self.assertEqual(inspected['definition']['nodes'][-1]['parents'], ['gen', 'spec'])
        self.assertEqual(inspected['incomplete_ids'], ['base', 'gen', 'spec', 'both'])
        self.assertTrue(all(n['status'] == 'prepared' for n in inspected['nodes']))
        self.assert_advisory(plan)
        self.assert_advisory(inspected)

    def test_pending_retains_every_missing_node_and_creates_no_comparisons(self):
        plan = self.plan()
        self.complete('baseline')
        self.complete('composed', 10)
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['decision'], 'abstain')
        self.assertIsNone(result['candidate_id'])
        self.assertEqual(result['incomplete_ids'], ['gen', 'spec'])
        self.assertEqual(result['comparisons'], [])
        self.assertEqual(list((self.vault / runs.RUNTIME_DIR).glob('run-compare-*')), [])
        self.assert_advisory(result)

    def test_unique_positive_candidate_uses_all_three_native_comparisons(self):
        plan = self.plan()
        self.finish()
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual((result['status'], result['decision'], result['candidate_id']),
                         ('complete', 'candidate', 'both'))
        self.assertEqual([c['node_id'] for c in result['comparisons']], ['gen', 'spec', 'both'])
        for comparison, delta in zip(result['comparisons'], (0.2, 0.1, 0.5)):
            self.assertAlmostEqual(comparison['score_delta'], delta)
        for comparison in result['comparisons']:
            self.assertTrue((self.vault / comparison['path']).is_file())
        self.assert_advisory(result)

    def test_nonpositive_delta_keeps_baseline(self):
        plan = self.plan()
        self.finish((10, 0, 5, 10))
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual((result['decision'], result['candidate_id']), ('keep_base', 'base'))

    def test_positive_tie_abstains(self):
        plan = self.plan()
        self.finish((0, 10, 10, 1))
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual((result['decision'], result['candidate_id']), ('abstain', None))
        self.assertIn('positive_score_tie', result['reasons'])

    def test_hard_failure_candidate_is_retained_but_cannot_win(self):
        plan = self.plan()
        self.complete('baseline')
        self.complete('general', 4)
        self.complete('specialized', 0)
        self.complete('composed', 10, hard=False)
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual(result['candidate_id'], 'gen')
        self.assertFalse(result['comparisons'][-1]['eligible'])
        self.assertIn('hard_boundary_failure', result['comparisons'][-1]['reasons'])

    def test_baseline_error_abstains(self):
        plan = self.plan()
        self.complete('baseline', error=True)
        for role in ('general', 'specialized', 'composed'):
            self.complete(role, 10)
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual(result['decision'], 'abstain')
        self.assertIn('baseline_ineligible', result['reasons'])
        self.assertEqual(len(result['comparisons']), 3)

    def test_baseline_hard_failure_abstains(self):
        plan = self.plan()
        self.complete('baseline', hard=False)
        for role in ('general', 'specialized', 'composed'):
            self.complete(role, 10)
        result = improvements.compare(self.vault, plan['id'])
        self.assertEqual(result['decision'], 'abstain')
        self.assertIsNone(result['candidate_id'])
        self.assertIn('hard_boundary_failure', result['reasons'])

    def test_lineage_and_strict_schema_reject_invalid_plans(self):
        invalid = []
        for change in ('duplicate_id', 'duplicate_run', 'wrong_parent', 'cycle', 'missing', 'extra', 'version'):
            value = copy.deepcopy(self.definition)
            if change == 'duplicate_id': value['nodes'][1]['id'] = 'base'
            elif change == 'duplicate_run': value['nodes'][1]['run_id'] = value['nodes'][0]['run_id']
            elif change == 'wrong_parent': value['nodes'][1]['parents'] = ['spec']
            elif change == 'cycle': value['nodes'][0]['parents'] = ['both']
            elif change == 'missing': value['nodes'].pop()
            elif change == 'extra': value['nodes'][0]['unexpected'] = True
            else: value['version'] = True
            invalid.append(value)
        for value in invalid:
            self.write_json('input/plan.json', value)
            self.rejected(self.plan)

    def test_incompatible_prepared_runs_are_rejected_before_completion(self):
        arm = self.prepare_run('general', token_budget=4096)
        self.definition['nodes'][1]['run_id'] = arm['id']
        self.write_json('input/plan.json', self.definition)
        self.rejected(self.plan)
        self.assertEqual(list((self.vault / runs.RUNTIME_DIR).glob('improvement-plan-*')), [])

    def test_no_entry_and_isolated_lesson_are_immutable_review_records(self):
        plan = self.plan()
        self.finish()
        empty = self.record(plan, {'version': 1, 'edits': [], 'lesson': None})
        self.assertIsNone(empty['lesson_candidate'])
        no_entry = self.record(plan, self.report(verdict='confounded', isolation='unknown', pairs=1, lesson=False))
        self.assertIsNone(no_entry['lesson_candidate'])
        record = self.record(plan)
        self.assertEqual(record['lesson_candidate']['edit_indices'], [0])
        before = (self.vault / record['path']).read_bytes()
        self.assertEqual(self.record(plan)['id'], record['id'])
        self.assertEqual((self.vault / record['path']).read_bytes(), before)
        files = set((self.vault / runs.RUNTIME_DIR).iterdir())
        inspected = improvements.inspect_record(self.vault, record['id'])
        self.assertEqual(set((self.vault / runs.RUNTIME_DIR).iterdir()), files)
        self.assertEqual(inspected['lesson'], self.report()['lesson'])
        self.assertEqual(inspected['plan_id'], plan['id'])
        self.assert_advisory(record)
        self.assert_advisory(inspected)
        self.assertFalse((self.vault / '00-meta/proposals').exists())

    def test_reflection_requires_all_four_completed_runs(self):
        plan = self.plan()
        self.complete('baseline')
        self.complete('composed', 10)
        self.rejected(self.record, plan)

    def test_single_pair_unisolated_and_multiple_component_claims_are_rejected(self):
        plan = self.plan()
        self.finish()
        reports = [self.report(pairs=1), self.report(isolation='unknown'),
                   self.report(isolation='multiple_components')]
        multiple = self.report()
        multiple['edits'].append({**copy.deepcopy(multiple['edits'][0]), 'component': 'tools'})
        reports.append(multiple)
        for report in reports:
            self.rejected(self.record, plan, report)

    def test_lessons_cannot_cite_nonpositive_or_confounded_edits(self):
        plan = self.plan()
        self.finish((10, 10, 10, 10))
        self.rejected(self.record, plan)
        for verdict in ('confounded', 'hurt', 'no_observed_effect'):
            self.rejected(self.record, plan, self.report(verdict=verdict))

    def test_reflection_evidence_must_be_unique_real_paired_observations(self):
        plan = self.plan()
        self.finish()
        reports = []
        for change in ('duplicate', 'unpaired', 'missing_case', 'bool', 'other_run', 'too_many'):
            value = self.report(lesson=False)
            evidence = value['edits'][0]['evidence']
            if change == 'duplicate': evidence.append(copy.deepcopy(evidence[0]))
            elif change == 'unpaired': evidence[:] = evidence[2:]
            elif change == 'missing_case': evidence[0]['case_id'] = 'unknown'
            elif change == 'bool': evidence[0]['repetition'] = True
            elif change == 'other_run': evidence[0]['run_id'] = self.arms['general']['id']
            else: evidence.extend(copy.deepcopy(evidence) * 4)
            reports.append(value)
        for report in reports:
            self.rejected(self.record, plan, report)

    def test_reflection_schema_text_bounds_and_lesson_indices_are_strict(self):
        plan = self.plan()
        self.finish()
        for change in ('extra', 'bool_version', 'empty', 'long', 'too_many',
                       'bool_index', 'empty_index', 'duplicate_index'):
            value = self.report()
            if change == 'extra': value['edits'][0]['extra'] = True
            elif change == 'bool_version': value['version'] = True
            elif change == 'empty': value['edits'][0]['observed'] = ''
            elif change == 'long': value['edits'][0]['change'] = 'x' * 4097
            elif change == 'too_many': value['edits'] *= 17
            elif change == 'bool_index': value['lesson']['edit_indices'] = [True]
            elif change == 'empty_index': value['lesson']['edit_indices'] = []
            else: value['lesson']['edit_indices'] = [0, 0]
            self.rejected(self.record, plan, value)

    def test_definition_report_evidence_candidate_and_config_drift_fail_closed(self):
        plan = self.plan()
        self.finish()
        record = self.record(plan)
        for relative in ('input/plan.json', 'input/reflection.json', 'output/evidence.txt',
                         'notes/general.md', '00-meta/vault-config.json'):
            path = self.vault / relative
            before = path.read_bytes()
            self.write(relative, before + b'\n')
            self.rejected(improvements.inspect_record, self.vault, record['id'])
            path.write_bytes(before)
        self.assertEqual(improvements.inspect_record(self.vault, record['id'])['id'], record['id'])

    def test_recomputed_receipt_checksums_cannot_detach_plan_or_report(self):
        plan = self.plan()
        self.finish()
        record = self.record(plan)
        for original, field in ((plan, 'definition'), (record, 'report')):
            path = self.vault / original['path']
            value = json.loads(path.read_bytes())
            if field == 'definition': value['payload'][field]['task_class'] = 'altered'
            else: value['payload'][field]['edits'][0]['observed'] = 'Altered claim.'
            value['id'] = runs.evidence.digest(runs.evidence.encode(value['payload']))
            value['receipt_sha256'] = runs.evidence.digest(runs.evidence.encode({
                k: v for k, v in value.items() if k != 'receipt_sha256'}))
            prefix = 'improvement-plan-' if field == 'definition' else 'improvement-record-'
            changed = path.with_name(prefix + value['id'] + '.json')
            changed.write_text(json.dumps(value), encoding='utf-8')
            function = improvements.inspect if field == 'definition' else improvements.inspect_record
            self.rejected(function, self.vault, value['id'])

    def test_duplicate_nonfinite_deep_and_oversized_json_are_rejected(self):
        for value in ('{"version":1,"version":1}', '{"version":NaN}', '{"version":1e999}',
                      '[' * 20000 + '0' + ']' * 20000, ' ' * (64 * 1024 + 1)):
            self.write('input/plan.json', value)
            self.rejected(self.plan)

    def test_unsafe_secret_and_linked_input_paths_fail_without_echo(self):
        secret = 'sk-' + 'x' * 48
        for relative in ('../outside.json', 'private/plan.json', 'excluded/plan.json',
                         '.env', '.git/plan.json', runs.RUNTIME_DIR + '/plan.json'):
            self.rejected(improvements.prepare, self.vault, relative)
        value = copy.deepcopy(self.definition)
        value['task_class'] = secret
        self.write_json('input/plan.json', value)
        code = self.rejected(self.plan)
        self.assertNotIn(secret, code)
        self.write_json('input/plan.json', self.definition)
        try:
            os.link(self.vault / 'input/plan.json', self.vault / 'input/linked.json')
        except OSError:
            self.skipTest('hardlink creation unavailable')
        self.rejected(improvements.prepare, self.vault, 'input/linked.json')

    def test_source_change_during_publish_refuses_receipt(self):
        real = runs.state._check_lease
        def mutate(policy, lease):
            real(policy, lease)
            self.write_json('input/plan.json', {**self.definition, 'task_class': 'changed'})
        with mock.patch.object(runs.state, '_check_lease', side_effect=mutate):
            self.rejected(self.plan)
        self.assertEqual(list((self.vault / runs.RUNTIME_DIR).glob('improvement-plan-*')), [])

    def test_compare_revalidates_all_four_runs_after_native_comparisons(self):
        plan = self.plan()
        self.finish()
        real = runs.compare
        def compare_then_mutate(vault, baseline_id, candidate_id):
            result = real(vault, baseline_id, candidate_id)
            if candidate_id == self.arms['composed']['id']:
                self.write('notes/general.md', 'Changed after its pairwise comparison.\n')
            return result
        with mock.patch.object(runs, 'compare', side_effect=compare_then_mutate):
            self.rejected(improvements.compare, self.vault, plan['id'])

    def test_report_and_evidence_change_during_reflection_publish_refuse_record(self):
        plan = self.plan()
        self.finish()
        self.write_json('input/reflection.json', self.report())
        real = runs.state._check_lease
        for relative in ('input/reflection.json', 'output/evidence.txt'):
            path = self.vault / relative
            original = path.read_bytes()
            def mutate(policy, lease):
                real(policy, lease)
                self.write(relative, original + b'\n')
            with mock.patch.object(runs.state, '_check_lease', side_effect=mutate):
                self.rejected(improvements.record, self.vault, plan['id'], 'input/reflection.json')
            path.write_bytes(original)
        self.assertEqual(list((self.vault / runs.RUNTIME_DIR).glob('improvement-record-*')), [])

    def test_error_outcomes_cannot_be_reflection_evidence(self):
        plan = self.plan()
        self.complete('baseline', error=True)
        for role in ('general', 'specialized', 'composed'):
            self.complete(role, 10)
        report = self.report(verdict='confounded', lesson=False)
        for ref in report['edits'][0]['evidence']:
            ref['case_id'] = 'representative'
        self.rejected(self.record, plan, report)

    def test_corrupt_plan_and_reflection_receipts_are_rejected(self):
        plan = self.plan()
        self.finish()
        record = self.record(plan)
        for receipt, function in ((plan, improvements.inspect), (record, improvements.inspect_record)):
            path = self.vault / receipt['path']
            original = path.read_bytes()
            value = json.loads(original)
            value['receipt_sha256'] = '0' * 64
            path.write_text(json.dumps(value), encoding='utf-8')
            self.rejected(function, self.vault, receipt['id'])
            path.write_bytes(original)

    def test_cli_json_smoke_and_content_free_argument_errors(self):
        def cli(*args, success=True):
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'vault_improvements.py'),
                '--vault', str(self.vault), *args], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
            self.assertEqual(result.stderr, '')
            return json.loads(result.stdout)
        plan = cli('prepare', '--input', 'input/plan.json')
        self.assertEqual(cli('inspect', plan['id'])['status'], 'prepared')
        self.assertEqual(cli('compare', plan['id'])['decision'], 'abstain')
        self.finish()
        self.write_json('input/reflection.json', self.report())
        record = cli('record', plan['id'], '--report', 'input/reflection.json')
        self.assertEqual(cli('inspect-record', record['id'])['id'], record['id'])
        sentinel = 'sk-' + 'SYNTHETIC_TEST_VALUE_' * 3
        self.assertEqual(cli('inspect', sentinel, '--unexpected', sentinel, success=False),
                         {'ok': False, 'error': 'invalid_cli_arguments'})
        self.assertFalse(cli('inspect', '../bad', success=False)['ok'])
        for arguments in (['--help'], ['--vault', str(self.vault), 'prepare', '--help']):
            result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'vault_improvements.py'), *arguments],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, '')
            self.assertIn('usage:', result.stdout)

    def test_receipt_order_is_opt_in_and_moves_no_plan_id_or_decision(self):
        plan_ids, decisions = set(), set()
        scenarios = (((), 'plan_first'), (('baseline', 'general'), 'mixed'), (ROLES, 'results_first'))
        for index, (before, order) in enumerate(scenarios):
            with self.subTest(order=order):
                case = self.fresh() if index else self
                plan = case.ordered(before)
                result = case.assert_receipt_order(plan['id'], before, order)
                plan_ids.add(plan['id'])
                decisions.add((result['status'], result['decision'], result['candidate_id']))
        # The plan payload holds no time, so recording order cannot move its ID.
        self.assertEqual(len(plan_ids), 1)
        self.assertEqual(decisions, {('complete', 'candidate', 'both')})

    def test_incomplete_nodes_count_as_recorded_after_the_plan(self):
        plan = self.ordered((), rest=False)
        pending = self.assert_receipt_order(plan['id'], (), 'plan_first')
        self.assertEqual(pending['incomplete_ids'], ['base', 'gen', 'spec', 'both'])
        case = self.fresh()
        plan = case.ordered(ROLES[:3], rest=False)
        pending = case.assert_receipt_order(plan['id'], ROLES[:3], 'mixed')
        self.assertEqual((pending['status'], pending['decision'], pending['incomplete_ids']),
                         ('pending', 'abstain', ['both']))

    def test_equal_instants_are_not_earlier_and_ids_keep_definition_order(self):
        planned = '2026-10-30T00:00:00.400000+00:00'
        # 'composed' is the plan's instant written with three digits: as text it
        # sorts before the plan. 'specialized' carries the earliest stamp.
        stamps = {'composed': '2026-10-30T00:00:00.400+00:00', 'general': planned,
                  'baseline': '2026-10-30T00:00:00.399999+00:00',
                  'specialized': '2026-10-29T00:00:00+00:00'}
        self.assertLess(stamps['composed'], planned)
        for role, stamp in stamps.items():
            with clock(stamp):
                self.complete(role, PASSES[role])
        with clock(planned):
            plan = self.plan()
        result = improvements.compare(self.vault, plan['id'], receipt_order=True)
        self.assertEqual((result['plan_created_at'], result['recorded_before_plan'], result['receipt_order']),
                         (planned, ['base', 'spec'], 'mixed'))

    def test_repeated_prepare_keeps_the_first_plan_receipt_time(self):
        plan = self.ordered(ROLES)
        with clock(LATER):
            self.assertEqual(self.plan()['id'], plan['id'])
        result = improvements.inspect(self.vault, plan['id'], receipt_order=True)
        self.assertEqual((result['plan_created_at'], result['receipt_order']), (PLANNED, 'results_first'))

    def test_receipt_order_flag_keeps_cli_bytes_and_exit_codes(self):
        plan = self.ordered(('baseline', 'general'))
        missing, opted = '0' * 64, {}

        def cli(*args):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    code = improvements.main(['--vault', str(self.vault), *args])
                except SystemExit as exc:
                    code = exc.code
            self.assertEqual(stderr.getvalue(), '')
            return code, stdout.getvalue()

        def encoded(value):
            return runs.evidence.encode(value).decode('ascii') + '\n'
        for verb, function in (('inspect', improvements.inspect), ('compare', improvements.compare)):
            with self.subTest(verb=verb):
                # Without the flag the CLI passes receipt_order=False and prints the default bytes.
                self.assertEqual(cli(verb, plan['id']), (0, encoded(function(self.vault, plan['id']))))
                opted[verb] = encoded(function(self.vault, plan['id'], receipt_order=True))
                self.assertEqual(cli(verb, plan['id'], '--receipt-order'), (0, opted[verb]))
                code, output = cli(verb, missing)
                self.assertEqual((code, json.loads(output)['ok']), (2, False))
                self.assertEqual(cli(verb, missing, '--receipt-order'), (code, output))
                for value in (1, 'true', None):
                    self.assertEqual(self.rejected(partial(function, self.vault, plan['id'],
                                                           receipt_order=value)), 'invalid_opt_in')
        self.assertEqual(cli('inspect-record', missing, '--receipt-order'),
                         (2, encoded({'ok': False, 'error': 'invalid_cli_arguments'})))
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'vault_improvements.py'), '--vault',
                                 str(self.vault), 'compare', plan['id'], '--receipt-order'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, opted['compare'], ''))


if __name__ == '__main__':
    unittest.main()
