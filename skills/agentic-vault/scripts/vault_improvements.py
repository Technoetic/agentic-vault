#!/usr/bin/env python3
"""Opt-in candidate lineage and source-bound, review-only reflections.

The host supplies observations. This coordinator never executes instructions,
calls a model, applies a patch or promotes a lesson. Hashes bind selected bytes;
they do not certify causality, truth, authority or general effectiveness.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from functools import wraps
from pathlib import Path

import vault_runs as runs

evidence = runs.evidence
state = runs.state
ROLES = ('baseline', 'general', 'specialized', 'composed')
ISOLATIONS = ('single_component', 'multiple_components', 'unknown')
VERDICTS = ('helped', 'hurt', 'no_observed_effect', 'confounded')


class ImprovementError(Exception):
    """A content-free validation or operational error code."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ImprovementError:
            raise
        except runs.RunError as exc:
            raise ImprovementError(str(exc)) from exc
        except (state.StateError, evidence.EvidenceError, runs.HealthcheckError,
                OSError, UnicodeError, ValueError, TypeError, RuntimeError,
                OverflowError) as exc:
            raise ImprovementError('unsafe_or_invalid_input') from exc
    return call


def _result(**fields):
    return dict(ok=True, **fields, authority=runs.AUTHORITY,
                requires_review=True, applies_changes=False)


def _digest(value):
    return evidence.digest(evidence.encode(value))


def _name(record_id, *, reflection=False):
    runs._hash(record_id)
    return ('improvement-record-' if reflection else 'improvement-plan-') + record_id + '.json'


def _path(name):
    return runs.RUNTIME_DIR + '/' + name


def _definition(value):
    runs._safe_json(value)
    runs._fields(value, {'version', 'task_class', 'nodes'}, 'invalid_plan')
    runs._text(value['task_class'], label=True)
    if (type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['nodes'], list) or len(value['nodes']) != 4):
        raise ImprovementError('invalid_plan')
    nodes, ids, run_ids = {}, set(), set()
    for node in value['nodes']:
        runs._fields(node, {'id', 'role', 'run_id', 'parents'}, 'invalid_node')
        node_id = runs._text(node['id'], label=True)
        run_id = runs._hash(node['run_id'])
        role, parents = node['role'], node['parents']
        if (not isinstance(role, str) or role not in ROLES or role in nodes
                or node_id in ids or run_id in run_ids or not isinstance(parents, list)
                or len(parents) > 2):
            raise ImprovementError('invalid_or_duplicate_node')
        for parent in parents:
            runs._text(parent, label=True)
        if len(set(parents)) != len(parents):
            raise ImprovementError('invalid_lineage')
        nodes[role] = {**node, 'parents': sorted(parents)}
        ids.add(node_id)
        run_ids.add(run_id)
    baseline = nodes['baseline']['id']
    expected = {'baseline': [], 'general': [baseline], 'specialized': [baseline],
                'composed': sorted([nodes['general']['id'], nodes['specialized']['id']])}
    if any(nodes[role]['parents'] != expected[role] for role in ROLES):
        raise ImprovementError('invalid_lineage')
    return {**value, 'nodes': [nodes[role] for role in ROLES]}


def _run_states(store, definition):
    values, compatibility = [], None
    for node in definition['nodes']:
        prepared = runs._load_prepared(store, node['run_id'])
        completed = runs._load_completion(store, prepared)
        current = runs._compatibility(prepared['payload'])
        if compatibility is not None and current != compatibility:
            raise ImprovementError('incompatible_execution_contracts')
        compatibility = current
        values.append((node, prepared, completed))
    return values, _digest(compatibility)


def _plan_payload(store, source, definition):
    values, compatibility = _run_states(store, definition)
    return dict(definition_source=source, definition=definition,
                compatibility_sha256=compatibility,
                config_sha256=store.policy.config_sha256), values


def _validate_plan(store, payload):
    runs._fields(payload, {'definition_source', 'definition', 'compatibility_sha256',
                          'config_sha256'}, 'invalid_plan_receipt')
    runs._fingerprint_record(store, payload['definition_source'], limit=runs.MAX_JSON_BYTES)
    runs._current_files(store, [payload['definition_source']], limit=runs.MAX_JSON_BYTES)
    source, value = store.json_file(payload['definition_source']['path'])
    expected, values = _plan_payload(store, source, _definition(value))
    if expected != payload:
        raise ImprovementError('plan_binding_mismatch')
    store.policy.fresh()
    return values


def _load_plan(store, plan_id):
    receipt = store.read_record(_name(plan_id))
    if (receipt['kind'] != 'improvement_plan' or receipt['id'] != plan_id
            or _digest(receipt['payload']) != plan_id):
        raise ImprovementError('invalid_plan_receipt')
    return receipt, _validate_plan(store, receipt['payload'])


def _same_plan(store, original, values):
    current, actual = _load_plan(store, original['id'])
    if current != original or actual != values:
        raise ImprovementError('plan_or_run_receipt_changed')


def _incomplete(values):
    return [node['id'] for node, _, completed in values if completed is None]


def _opt_in(receipt_order):
    if type(receipt_order) is not bool:
        raise ImprovementError('invalid_opt_in')


def _receipt_order(plan, values):
    """Local-clock recording order of the plan and completion receipts.

    read_record has already parsed every created_at with this interpreter's
    datetime.fromisoformat and required a zero UTC offset. Only a strictly earlier
    completion is listed, in role order (baseline, general, specialized, composed),
    which is the stored plan definition and the inspect nodes order; an equal instant
    is not earlier and an incomplete node counts as after the plan. Not observation
    time or a signature.
    """
    planned = datetime.fromisoformat(plan['created_at'])
    before = [node['id'] for node, _, completed in values if completed is not None
              and datetime.fromisoformat(completed['created_at']) < planned]
    order = ('plan_first' if not before
             else 'results_first' if len(before) == len(values) else 'mixed')
    return dict(plan_created_at=plan['created_at'], recorded_before_plan=before,
                receipt_order=order)


@_public
def prepare(vault, input_path):
    """Bind a strict four-node lineage to compatible existing frozen runs."""
    store = runs.Store(vault)
    source, value = store.json_file(input_path)
    payload, values = _plan_payload(store, source, _definition(value))
    plan_id = _digest(payload)
    name = _name(plan_id)
    def recheck():
        actual = _validate_plan(store, payload)
        if actual != values:
            raise ImprovementError('run_receipt_changed')
    with state.advisory_lock(vault, 'improvement-plan:' + plan_id, wait_seconds=1) as lease:
        recheck()
        if store.record_path(name).exists():
            existing, actual = _load_plan(store, plan_id)
            if existing['payload'] != payload or actual != values:
                raise ImprovementError('plan_or_run_receipt_changed')
        else:
            store.publish(name, 'improvement_plan', plan_id, payload, recheck, lease)
    return _result(id=plan_id, path=_path(name), definition=payload['definition'],
                   compatibility_sha256=payload['compatibility_sha256'])


@_public
def inspect(vault, plan_id, *, receipt_order=False):
    """Read a plan and every node status without creating receipts.

    receipt_order=True adds plan_created_at, recorded_before_plan and receipt_order
    at the top level: the local-clock recording order of existing receipts.
    """
    _opt_in(receipt_order)
    receipt, values = _load_plan(runs.Store(vault), plan_id)
    missing = _incomplete(values)
    return _result(id=plan_id, path=_path(_name(plan_id)),
        definition=receipt['payload']['definition'],
        compatibility_sha256=receipt['payload']['compatibility_sha256'],
        status='prepared' if missing else 'complete', incomplete_ids=missing,
        nodes=[{**node, 'status': 'complete' if completed else 'prepared'}
               for node, _, completed in values],
        **(_receipt_order(receipt, values) if receipt_order else {}))


@_public
def compare(vault, plan_id, *, receipt_order=False):
    """Compare all three candidates, preserving pending and ineligible arms.

    receipt_order=True adds the same three fields as inspect at the top level only,
    never inside comparisons[]; decisions, reasons and errors stay the same.
    """
    _opt_in(receipt_order)
    store = runs.Store(vault)
    plan, values = _load_plan(store, plan_id)
    order = _receipt_order(plan, values) if receipt_order else {}
    missing = _incomplete(values)
    if missing:
        return _result(id=plan_id, status='pending', decision='abstain', candidate_id=None,
                       reasons=['incomplete_runs'], incomplete_ids=missing, comparisons=[],
                       **order)
    baseline_node, baseline, completed = values[0]
    _, baseline_reasons = runs._score(baseline['payload'], completed)
    comparisons = [dict(node_id=node['id'], **runs.compare(vault, baseline['id'], prepared['id']))
                   for node, prepared, _ in values[1:]]
    # Native pairwise comparisons do not revalidate the fourth arm or plan.
    _same_plan(store, plan, values)
    decision, winner, reasons = 'keep_base', baseline_node['id'], ['no_positive_eligible_delta']
    if baseline_reasons:
        decision, winner, reasons = 'abstain', None, ['baseline_ineligible', *sorted(baseline_reasons)]
    else:
        eligible = [item for item in comparisons if item['eligible'] and item['score_delta'] > 0]
        if eligible:
            best = max(item['score_delta'] for item in eligible)
            leaders = [item for item in eligible if item['score_delta'] == best]
            if len(leaders) == 1:
                decision, winner, reasons = 'candidate', leaders[0]['node_id'], ['unique_positive_eligible_delta']
            else:
                decision, winner, reasons = 'abstain', None, ['positive_score_tie']
    return _result(id=plan_id, status='complete', decision=decision, candidate_id=winner,
                   reasons=reasons, incomplete_ids=[], comparisons=comparisons, **order)


def _eligible_nodes(values):
    baseline_score, baseline_reasons = runs._score(values[0][1]['payload'], values[0][2])
    eligible = set()
    for node, prepared, completed in values[1:]:
        score, reasons = runs._score(prepared['payload'], completed)
        if not baseline_reasons and not reasons and score > baseline_score:
            eligible.add(node['id'])
    return eligible


def _report(value, values):
    runs._safe_json(value)
    runs._fields(value, {'version', 'edits', 'lesson'}, 'invalid_reflection')
    if (type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['edits'], list) or len(value['edits']) > 16):
        raise ImprovementError('invalid_reflection')
    baseline_id = values[0][1]['id']
    nodes = {node['id']: prepared['id'] for node, prepared, _ in values[1:]}
    observations = {prepared['id']: {(item['case_id'], item['repetition'])
        for item in completed['payload']['outcomes'] if item['status'] == 'complete'}
        for _, prepared, completed in values}
    edits, components = [], {}
    for edit in value['edits']:
        runs._fields(edit, {'node_id', 'component', 'change', 'observed', 'isolation',
                           'verdict', 'evidence'}, 'invalid_edit')
        node_id = edit['node_id']
        if not isinstance(node_id, str) or node_id not in nodes:
            raise ImprovementError('invalid_edit_node')
        component = runs._text(edit['component'], label=True)
        runs._text(edit['change'])
        runs._text(edit['observed'])
        isolation, verdict = edit['isolation'], edit['verdict']
        if (not isinstance(isolation, str) or isolation not in ISOLATIONS
                or not isinstance(verdict, str) or verdict not in VERDICTS
                or (isolation != 'single_component' and verdict != 'confounded')):
            raise ImprovementError('invalid_isolation_or_verdict')
        refs = edit['evidence']
        if not isinstance(refs, list) or not 1 <= len(refs) <= 16:
            raise ImprovementError('invalid_evidence_count')
        seen, paired = set(), {baseline_id: set(), nodes[node_id]: set()}
        for ref in refs:
            runs._fields(ref, {'run_id', 'case_id', 'repetition'}, 'invalid_evidence_ref')
            run_id = runs._hash(ref['run_id'])
            case_id = runs._text(ref['case_id'], label=True)
            repetition = ref['repetition']
            if (run_id not in paired or type(repetition) is not int
                    or (case_id, repetition) not in observations[run_id]
                    or (run_id, case_id, repetition) in seen):
                raise ImprovementError('invalid_or_duplicate_evidence_ref')
            seen.add((run_id, case_id, repetition))
            paired[run_id].add((case_id, repetition))
        count = len(paired[baseline_id] & paired[nodes[node_id]])
        if count < 1 or (verdict in ('helped', 'hurt') and count < 2):
            raise ImprovementError('insufficient_paired_observations')
        components.setdefault(node_id, set()).add(component)
        edits.append({**edit, 'evidence': sorted(refs,
            key=lambda ref: (ref['run_id'], ref['case_id'], ref['repetition']))})
    if any(len(components[edit['node_id']]) > 1 and edit['verdict'] in ('helped', 'hurt')
           for edit in edits):
        raise ImprovementError('confounded_component_claim')
    lesson = value['lesson']
    if lesson is not None:
        runs._fields(lesson, {'cue', 'operation', 'edit_indices', 'confidence'}, 'invalid_lesson')
        runs._text(lesson['cue'])
        runs._text(lesson['operation'])
        indices, confidence = lesson['edit_indices'], lesson['confidence']
        if (not isinstance(confidence, str) or confidence not in ('low', 'medium', 'high')
                or not isinstance(indices, list) or not 1 <= len(indices) <= len(edits)
                or any(type(index) is not int or not 0 <= index < len(edits) for index in indices)
                or len(set(indices)) != len(indices)):
            raise ImprovementError('invalid_lesson')
        eligible = _eligible_nodes(values)
        if any(edits[index]['verdict'] != 'helped'
               or edits[index]['isolation'] != 'single_component'
               or edits[index]['node_id'] not in eligible for index in indices):
            raise ImprovementError('lesson_not_supported')
        lesson = {**lesson, 'edit_indices': sorted(indices)}
    return dict(version=1, edits=edits, lesson=lesson)


def _reflection_payload(store, plan, values, source, report):
    if _incomplete(values):
        raise ImprovementError('run_not_complete')
    return dict(plan_id=plan['id'], plan_receipt_sha256=plan['receipt_sha256'],
        report_source=source, report=_report(report, values),
        completion_payload_sha256={prepared['id']: _digest(completed['payload'])
                                   for _, prepared, completed in values},
        completion_receipt_sha256={prepared['id']: completed['receipt_sha256']
                                   for _, prepared, completed in values})


def _validate_reflection(store, payload):
    runs._fields(payload, {'plan_id', 'plan_receipt_sha256', 'report_source', 'report',
        'completion_payload_sha256', 'completion_receipt_sha256'}, 'invalid_reflection_receipt')
    plan, values = _load_plan(store, payload['plan_id'])
    runs._fingerprint_record(store, payload['report_source'], limit=runs.MAX_JSON_BYTES)
    runs._current_files(store, [payload['report_source']], limit=runs.MAX_JSON_BYTES)
    source, report = store.json_file(payload['report_source']['path'])
    if _reflection_payload(store, plan, values, source, report) != payload:
        raise ImprovementError('reflection_binding_mismatch')
    store.policy.fresh()
    return plan, values


@_public
def record(vault, plan_id, report_path):
    """Append an immutable reflection or NO_ENTRY; never write durable knowledge."""
    store = runs.Store(vault)
    plan, values = _load_plan(store, plan_id)
    source, report = store.json_file(report_path)
    payload = _reflection_payload(store, plan, values, source, report)
    record_id = _digest(payload)
    name = _name(record_id, reflection=True)
    def recheck():
        _same_plan(store, plan, values)
        _validate_reflection(store, payload)
    with state.advisory_lock(vault, 'improvement-record:' + record_id, wait_seconds=1) as lease:
        recheck()
        if store.record_path(name).exists():
            _load_reflection(store, record_id)
        else:
            store.publish(name, 'improvement_record', record_id, payload, recheck, lease)
    return _result(id=record_id, path=_path(name), plan_id=plan_id,
                   lesson_candidate=payload['report']['lesson'])


def _load_reflection(store, record_id):
    receipt = store.read_record(_name(record_id, reflection=True))
    if (receipt['kind'] != 'improvement_record' or receipt['id'] != record_id
            or _digest(receipt['payload']) != record_id):
        raise ImprovementError('invalid_reflection_receipt')
    _validate_reflection(store, receipt['payload'])
    return receipt


@_public
def inspect_record(vault, record_id):
    """Read and revalidate exactly one selected reflection without writing."""
    receipt = _load_reflection(runs.Store(vault), record_id)
    payload = receipt['payload']
    return _result(id=record_id, path=_path(_name(record_id, reflection=True)),
        plan_id=payload['plan_id'], edits=payload['report']['edits'],
        lesson=payload['report']['lesson'], lesson_candidate=payload['report']['lesson'])


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(evidence.encode(dict(ok=False, error='invalid_cli_arguments')).decode('ascii'))
        self.exit(2)


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_ArgumentParser)
    commands.add_parser('prepare', help='freeze a four-node lineage').add_argument('--input', required=True)
    for verb in ('inspect', 'compare', 'inspect-record'):
        sub = commands.add_parser(verb)
        sub.add_argument('id')
        if verb != 'inspect-record':
            sub.add_argument('--receipt-order', action='store_true',
                             help='opt-in local-clock recording order of the plan and completion '
                                  'receipts; not observation time or a signature')
    rec = commands.add_parser('record', help='record a review-only reflection or NO_ENTRY')
    rec.add_argument('id')
    rec.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.vault, args.input)
        elif args.command == 'record':
            result = record(args.vault, args.id, args.report)
        elif args.command == 'inspect-record':
            result = inspect_record(args.vault, args.id)
        else:
            function = {'inspect': inspect, 'compare': compare}[args.command]
            result = function(args.vault, args.id, receipt_order=args.receipt_order)
        print(evidence.encode(result).decode('ascii'))
        return 0
    except ImprovementError as exc:
        print(evidence.encode(dict(ok=False, error=str(exc))).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
