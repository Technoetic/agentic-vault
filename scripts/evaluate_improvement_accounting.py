#!/usr/bin/env python3
"""Offline four-arm evaluator/cost contract example; no actual model measurement."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile

from evaluate_improvement_experiments import SyntheticVault, ROLES

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def evaluate():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    runs = importlib.import_module('vault_runs')
    improvements = importlib.import_module('vault_improvements')
    costs = importlib.import_module('vault_run_costs')
    with tempfile.TemporaryDirectory() as temporary:
        vault = Path(temporary) / 'vault'
        fixture = SyntheticVault(vault, runs, improvements)
        grader_path = 'evaluator/grader.py'
        grader = '# Synthetic grader snapshot; this file is never executed.\n'
        fixture.write(grader_path, grader)
        fixture.write_json('evaluator/config.json', {'rubric_version': 1})
        for role in ROLES:
            fixture.prepared[role] = runs.prepare(vault, 'notes/' + role + '.md',
                ['notes/instructions.md'], 'input/fixture.json', 'input/tools.json',
                model='synthetic-fixture-model', provider='synthetic-fixture-provider',
                token_budget=2048, candidate_version='synthetic-' + role,
                evaluator_sources=[grader_path], evaluator_config='evaluator/config.json')
        _check(len({item['compatibility_sha256'] for item in fixture.prepared.values()}) == 1,
               'all four roles must share an evaluator contract')
        parents = {'baseline': [], 'general': ['baseline'], 'specialized': ['baseline'],
                   'composed': ['general', 'specialized']}
        fixture.write_json('input/lineage.json', {'version': 1, 'task_class': 'synthetic-accounting',
            'nodes': [{'id': role, 'role': role, 'run_id': fixture.prepared[role]['id'],
                       'parents': parents[role]} for role in ROLES]})
        plan = improvements.prepare(vault, 'input/lineage.json')
        fixture.complete({'baseline': 0, 'general': 6, 'specialized': 4, 'composed': 9})
        plan_comparison = improvements.compare(vault, plan['id'])
        _check(plan_comparison['candidate_id'] == 'composed', 'synthetic composed arm should win')

        receipts = {}
        for role in ROLES:
            entries = []
            phases = ['generation', 'evaluation', 'selection', 'recording']
            if role == 'composed':
                phases.append('generation')  # Include an abandoned generation attempt.
            for index, phase in enumerate(phases):
                entry_id = phase + '-' + str(index)
                unknown = role == 'specialized' and phase == 'generation'
                evidence_path = 'usage/' + role + '-' + entry_id + '.txt'
                metrics = dict(input_tokens=10, output_tokens=5, calls=1,
                               elapsed_ms=20, cost_microusd=100)
                if unknown:
                    metrics = {key: None for key in metrics}
                else:
                    fixture.write(evidence_path, 'Synthetic host-reported resource event ' + entry_id + '\n')
                entries.append(dict(id=entry_id, phase=phase,
                    status='discarded' if index == 4 else 'complete',
                    observation='unknown' if unknown else 'host_report', recorder='synthetic-host',
                    evidence=[] if unknown else [evidence_path], metrics=metrics,
                    cost_basis='unknown' if unknown else 'estimated'))
            report = 'input/' + role + '-cost.json'
            fixture.write_json(report, dict(version=1, run_id=fixture.prepared[role]['id'],
                scope_complete=role != 'specialized', entries=entries))
            receipts[role] = costs.record(vault, fixture.prepared[role]['id'], report)

        composed = costs.inspect(vault, receipts['composed']['id'])
        _check(any(entry['status'] == 'discarded' for entry in composed['entries']),
               'discarded attempt must remain visible')
        _check(composed['totals']['cost_microusd'] == 500, 'discarded cost must count')
        summary = costs.summarize(vault, [receipts[role]['id'] for role in ROLES])
        _check(summary['totals']['cost_microusd'] is None, 'unknown cost cannot become zero')
        _check(summary['known_subtotals']['cost_microusd'] == 1600, 'known cost subtotal mismatch')
        comparison = costs.compare(vault, receipts['baseline']['id'], receipts['composed']['id'])
        _check(comparison['cost_comparison_available'], 'complete declared reports should compare')
        _check(comparison['reported_deltas']['cost_microusd'] == 100, 'reported cost delta mismatch')
        withheld = costs.compare(vault, receipts['baseline']['id'], receipts['specialized']['id'])
        _check(not withheld['cost_comparison_available'], 'incomplete cost comparison must be withheld')

        fixture.write(grader_path, '# Mutated synthetic grader snapshot\n')
        rejected = False
        try:
            runs.inspect(vault, fixture.prepared['composed']['id'])
        except runs.RunError:
            rejected = True
        _check(rejected, 'a mutated evaluator must invalidate its run binding')
        fixture.write(grader_path, grader)
        costs.inspect(vault, receipts['composed']['id'])
        return dict(status='complete', native_runs=4, cost_receipts=4,
            shared_evaluator=True, discarded_attempt_retained=True, unknown_cost_is_null=True,
            evaluator_mutation_rejected=True, complete_cost_comparison=True,
            incomplete_cost_comparison_withheld=True, requires_review=True, applies_changes=False,
            measurements='synthetic_host_reports_only')


def main():
    print(json.dumps(evaluate(), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
