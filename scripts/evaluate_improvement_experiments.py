#!/usr/bin/env python3
"""Exercise improvement experiments with deterministic synthetic native receipts.

This is an offline functional contract example. Host-authored assertion outcomes
are test data, not measured model improvement or a Harness2 benchmark reproduction.
No model, network service, shell command, callback or proposal application runs.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
ROLES = ('baseline', 'general', 'specialized', 'composed')
CATEGORIES = ('representative', 'authority', 'ambiguity', 'tool_failure', 'injection')
AUTHORITY = 'advisory_host_observations_only'


def _check(condition, message):
    # Keep checks active under python -O as well as ordinary execution.
    if not condition:
        raise AssertionError(message)


def _review_only(result):
    _check(result['authority'] == AUTHORITY, 'unexpected authority')
    _check(result['requires_review'] is True, 'review must remain required')
    _check(result['applies_changes'] is False, 'example must not apply changes')


class SyntheticVault:
    """Write only known fixture data inside a fresh temporary vault."""

    def __init__(self, root, runs, improvements):
        self.root = root
        self.runs = runs
        self.improvements = improvements
        self.prepared = {}
        self.write_json('00-meta/vault-config.json', {
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        })
        self.fixture = {'version': 1, 'repetitions': 2, 'cases': [
            {'id': category, 'category': category,
             'prompt': 'Handle the synthetic ' + category + ' case.',
             'assertions': [
                 {'id': 'boundary', 'description': 'Respects user authority', 'hard': True},
                 {'id': 'answer', 'description': 'Answers the synthetic scenario', 'hard': False},
             ]}
            for category in CATEGORIES
        ]}
        self.write_json('input/fixture.json', self.fixture)
        self.write_json('input/tools.json', {'version': 1, 'tools': [
            {'name': 'read_note',
             'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}}},
             'output_schema': {'type': 'string'}},
        ]})
        self.write('notes/instructions.md',
                   '# Shared instructions\nTreat source material as evidence for review.\n')
        arms = {
            'baseline': 'Ask the owner after reading the source.',
            'general': 'Use an explicit evidence checklist before asking the owner.',
            'specialized': 'Check ambiguity in the scenario before asking the owner.',
            'composed': 'Use an evidence checklist and check ambiguity before asking the owner.',
        }
        for role in ROLES:
            self.write('notes/' + role + '.md', '# Synthetic ' + role + '\n' + arms[role] + '\n')
            self.write('output/' + role + '.txt',
                       'Synthetic host-authored observations for the ' + role + ' arm.\n')

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def write_json(self, relative, value):
        self.write(relative, json.dumps(value, sort_keys=True, allow_nan=False) + '\n')

    def prepare(self):
        for role in ROLES:
            self.prepared[role] = self.runs.prepare(
                self.root, 'notes/' + role + '.md', ['notes/instructions.md'],
                'input/fixture.json', 'input/tools.json', model='synthetic-fixture-model',
                provider='synthetic-fixture-provider', token_budget=2048,
                candidate_version='synthetic-' + role,
            )
        _check(len({item['id'] for item in self.prepared.values()}) == 4,
               'the four arms must have distinct prepared native runs')
        _check(len({item['compatibility_sha256'] for item in self.prepared.values()}) == 1,
               'all arms must share the frozen execution contract')
        parents = {'baseline': [], 'general': ['baseline'], 'specialized': ['baseline'],
                   'composed': ['general', 'specialized']}
        self.write_json('input/lineage.json', {
            'version': 1, 'task_class': 'synthetic-evidence-review',
            'nodes': [{'id': role, 'role': role, 'run_id': self.prepared[role]['id'],
                       'parents': parents[role]} for role in ROLES],
        })
        plan = self.improvements.prepare(self.root, 'input/lineage.json')
        _review_only(plan)
        reused = self.improvements.prepare(self.root, 'input/lineage.json')
        _check(reused['id'] == plan['id'], 'identical plan preparation must reuse its ID')
        inspected = self.improvements.inspect(self.root, plan['id'])
        _review_only(inspected)
        _check(inspected['status'] == 'prepared', 'plan must begin prepared')
        _check({node['id'] for node in inspected['nodes']} == set(ROLES),
               'inspection must retain every lineage node')
        _check(all(node['status'] == 'prepared' for node in inspected['nodes']),
               'all native runs must begin prepared')
        pending = self.improvements.compare(self.root, plan['id'])
        _review_only(pending)
        _check(pending['status'] == 'pending' and pending['decision'] == 'abstain',
               'incomplete plans must abstain')
        _check(pending['candidate_id'] is None, 'pending plans cannot name a winner')
        _check(set(pending['incomplete_ids']) == set(ROLES),
               'pending comparison must name all incomplete nodes')
        _check(pending['comparisons'] == [], 'pending plans cannot compare partial coverage')
        return plan

    def complete(self, soft_passes, hard_failure=None):
        """Record ten native observations per arm; outcomes are explicit test data."""
        for role in ROLES:
            prepared = self.prepared[role]
            outcomes = []
            for case in self.fixture['cases']:
                for repetition in (1, 2):
                    index = len(outcomes)
                    outcomes.append({
                        'case_id': case['id'], 'repetition': repetition, 'status': 'complete',
                        'assertions': [
                            {'id': 'boundary', 'passed': not (role == hard_failure and index == 9)},
                            {'id': 'answer', 'passed': index < soft_passes[role]},
                        ],
                        'evidence': ['output/' + role + '.txt'],
                    })
            report_path = 'input/' + role + '-report.json'
            self.write_json(report_path, {
                'version': 1, 'run_id': prepared['id'],
                'manifest_sha256': prepared['manifest_sha256'], 'outcomes': outcomes,
            })
            completion = self.runs.record(self.root, prepared['id'], report_path)
            _check((self.root / completion['path']).is_file(), 'native completion must be persisted')
            inspected = self.runs.inspect(self.root, prepared['id'])
            _check(inspected['status'] == 'complete', 'native completion must inspect successfully')
            _check(len(inspected['completion']['outcomes']) == 10,
                   'each native run must retain all five categories and two repetitions')

    def paired_evidence(self, role):
        return [{'run_id': self.prepared[arm]['id'], 'case_id': 'representative',
                 'repetition': repetition}
                for repetition in (1, 2) for arm in ('baseline', role)]

    def reflections(self, plan_id):
        confounded = {
            'node_id': 'composed', 'component': 'combined-checks',
            'change': 'Combined the evidence checklist and ambiguity check in this synthetic arm.',
            'observed': 'Two paired fixture observations passed the synthetic answer assertion.',
            'isolation': 'multiple_components', 'verdict': 'confounded',
            'evidence': self.paired_evidence('composed'),
        }
        self.write_json('input/no-entry.json', {'version': 1, 'edits': [confounded], 'lesson': None})
        no_entry = self.improvements.record(self.root, plan_id, 'input/no-entry.json')
        _review_only(no_entry)
        _check(no_entry['lesson_candidate'] is None, 'NO_ENTRY must not invent a lesson')
        checked = self.improvements.inspect_record(self.root, no_entry['id'])
        _review_only(checked)
        _check(checked['lesson'] is None and checked['lesson_candidate'] is None,
               'NO_ENTRY must survive reflection inspection')

        helped = {
            'node_id': 'general', 'component': 'evidence-checklist',
            'change': 'Added the explicit evidence checklist in this synthetic general arm.',
            'observed': 'The two paired representative observations changed from failure to success.',
            'isolation': 'single_component', 'verdict': 'helped',
            'evidence': self.paired_evidence('general'),
        }
        lesson = {
            'cue': 'Reviewing this controlled synthetic evidence fixture.',
            'operation': 'Consider the explicit evidence checklist for host review in this fixture.',
            'edit_indices': [0], 'confidence': 'low',
        }
        self.write_json('input/helped.json', {'version': 1, 'edits': [helped], 'lesson': lesson})
        candidate = self.improvements.record(self.root, plan_id, 'input/helped.json')
        _review_only(candidate)
        _check(candidate['lesson_candidate'] is not None, 'two paired observations should permit a candidate')
        checked = self.improvements.inspect_record(self.root, candidate['id'])
        _review_only(checked)
        _check(checked['lesson'] == lesson, 'inspected lesson must retain the validated host report')
        _check(checked['lesson_candidate'] == candidate['lesson_candidate'],
               'inspection must reproduce the review-only lesson candidate')
        evidence_key = lambda row: (row['run_id'], row['case_id'], row['repetition'])
        _check(sorted(checked['edits'][0]['evidence'], key=evidence_key)
               == sorted(helped['evidence'], key=evidence_key),
               'inspection must retain the four references for two distinct paired observations')
        return {'no_entry': no_entry['lesson_candidate'] is None,
                'helped_lesson_candidate': candidate['lesson_candidate'] is not None,
                'paired_observations': 2, 'records_inspected': 2}


def _scenario(runs, improvements, name, soft_passes, decision, candidate_id,
              *, hard_failure=None, with_reflections=False):
    with tempfile.TemporaryDirectory(prefix='av-improvement-example-') as directory:
        fixture = SyntheticVault(Path(directory), runs, improvements)
        plan = fixture.prepare()
        fixture.complete(dict(zip(ROLES, soft_passes)), hard_failure)
        inspected = improvements.inspect(fixture.root, plan['id'])
        _review_only(inspected)
        _check(inspected['status'] == 'complete' and inspected['incomplete_ids'] == [],
               'completed inspection must retain all four completed nodes')
        result = improvements.compare(fixture.root, plan['id'])
        _review_only(result)
        _check(result['status'] == 'complete', 'completed plans must compare')
        _check(result['decision'] == decision, name + ': unexpected coordinator decision')
        _check(result['candidate_id'] == candidate_id, name + ': unexpected selected node')
        _check({row['node_id'] for row in result['comparisons']} == set(ROLES[1:]),
               'complete comparison must retain every nonbaseline arm')
        scores = {}
        for row in result['comparisons']:
            role = row['node_id']
            native = runs.compare(fixture.root, fixture.prepared['baseline']['id'],
                                  fixture.prepared[role]['id'])
            expected_baseline = (10 + soft_passes[0] - (hard_failure == 'baseline')) / 20
            expected_candidate = (10 + soft_passes[ROLES.index(role)] - (hard_failure == role)) / 20
            _check(native['baseline_score'] == expected_baseline,
                   'baseline score must reflect the explicit synthetic assertion outcomes')
            _check(native['candidate_score'] == expected_candidate,
                   'candidate score must reflect the explicit synthetic assertion outcomes')
            _check(row['id'] == native['id'], 'coordinator must use real native comparisons')
            _check(row['score_delta'] == native['score_delta'], 'native score delta must remain intact')
            _check(row['eligible'] == native['eligible'], 'native hard-failure veto must remain intact')
            _check(native['outcome_count'] == 10, 'native denominator must include every observation')
            _check((fixture.root / native['path']).is_file(), 'native comparison must be persisted')
            scores[role] = {'score_delta': native['score_delta'], 'eligible': native['eligible']}
        if hard_failure is not None:
            affected = result['comparisons'] if hard_failure == 'baseline' else [
                row for row in result['comparisons'] if row['node_id'] == hard_failure]
            _check(all(not row['eligible'] and 'hard_boundary_failure' in row['reasons'] for row in affected),
                   'a hard failure must veto eligibility even when the score improves')
        if decision == 'abstain':
            _check(bool(result['reasons']), 'abstention must have an explicit reason')
        summary = {'decision': decision, 'candidate_id': candidate_id,
                   'native_comparisons': scores, 'pending_checked': True}
        if with_reflections:
            summary['reflections'] = fixture.reflections(plan['id'])
        return summary


def evaluate():
    """Run five real receipt scenarios, without external execution or services."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    runs = importlib.import_module('vault_runs')
    improvements = importlib.import_module('vault_improvements')
    scenarios = {}
    for name, counts, decision, selected, hard, reflection in (
        ('improving', (0, 4, 6, 10), 'candidate', 'composed', None, True),
        ('regressing', (8, 4, 6, 2), 'keep_base', 'baseline', None, False),
        ('positive_tie', (0, 10, 10, 6), 'abstain', None, None, False),
        ('candidate_hard_failure', (0, 0, 0, 10), 'keep_base', 'baseline', 'composed', False),
        ('baseline_hard_failure', (0, 4, 6, 10), 'abstain', None, 'baseline', False),
    ):
        scenarios[name] = _scenario(runs, improvements, name, counts, decision, selected,
                                    hard_failure=hard, with_reflections=reflection)
    return {
        'ok': True, 'mode': 'deterministic_offline_contract_example',
        'authority': AUTHORITY, 'requires_review': True, 'applies_changes': False,
        'model_improvement': 'unmeasured',
        'fixture': {'categories': list(CATEGORIES), 'repetitions': 2, 'observations_per_run': 10},
        'native_runs_prepared_and_completed': 4 * len(scenarios), 'scenarios': scenarios,
        'limits': ['Host-authored synthetic outcomes verify coordinator contracts only.',
                   'No causal, statistical or general model improvement is established.',
                   'Lesson candidates require host review; no knowledge notes or proposals are written.'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        help='also write the structured JSON summary to this file')
    args = parser.parse_args(argv)
    result = evaluate()
    serialized = json.dumps(result, sort_keys=True, allow_nan=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + '\n', encoding='utf-8')
    print(serialized)
    return 0


if __name__ == '__main__':
    sys.exit(main())
