#!/usr/bin/env python3
"""Opt-in source-bound, caller-reported resource costs for native run receipts.

No commands/models execute and no provider accounts are read. Instrument-log
origin and recorder labels are caller descriptions, not authenticated metering.
Complete scope is a host declaration, not proof that every attempt was reported.
"""
from __future__ import annotations

from pathlib import Path

import vault_runs as runs

RunError = runs.RunError
evidence = runs.evidence
state = runs.state
PHASES = frozenset({'generation', 'evaluation', 'selection', 'recording'})
METRICS = ('input_tokens', 'output_tokens', 'calls', 'elapsed_ms', 'cost_microusd')
MAX_VALUE = 10 ** 15
METERING = 'caller_described_not_authenticated'
SCOPE_AUTHORITY = 'host_declaration_not_exhaustiveness_proof'


def _name(cost_id):
    return 'run-cost-' + runs._hash(cost_id) + '.json'


def _entries(store, value, run_id):
    runs._safe_json(value)
    runs._fields(value, {'version', 'run_id', 'scope_complete', 'entries'}, 'invalid_cost_report')
    if (type(value['version']) is not int or value['version'] != 1
            or value['run_id'] != run_id or type(value['scope_complete']) is not bool
            or not isinstance(value['entries'], list) or not 1 <= len(value['entries']) <= runs.MAX_FILES):
        raise RunError('invalid_cost_report')
    normalized, ids, refs = [], set(), set()
    for entry in value['entries']:
        runs._fields(entry, {'id', 'phase', 'status', 'observation', 'recorder',
                            'evidence', 'metrics', 'cost_basis'}, 'invalid_cost_entry')
        entry_id = runs._text(entry['id'], label=True)
        runs._text(entry['recorder'], label=True)
        if (entry_id in ids or not isinstance(entry['phase'], str) or entry['phase'] not in PHASES
                or not isinstance(entry['status'], str) or entry['status'] not in ('complete', 'error', 'discarded')
                or not isinstance(entry['observation'], str)
                or entry['observation'] not in ('instrument_log', 'host_report', 'unknown')
                or not isinstance(entry['cost_basis'], str)
                or entry['cost_basis'] not in ('billed', 'estimated', 'unknown')):
            raise RunError('invalid_or_duplicate_cost_entry')
        ids.add(entry_id)
        runs._fields(entry['metrics'], METRICS, 'invalid_cost_metrics')
        metrics = entry['metrics']
        for number in metrics.values():
            if number is not None and (type(number) is not int or not 0 <= number <= MAX_VALUE):
                raise RunError('invalid_cost_metric')
        if (metrics['cost_microusd'] is None) != (entry['cost_basis'] == 'unknown'):
            raise RunError('invalid_cost_basis')
        paths = entry['evidence']
        if not isinstance(paths, list) or len(paths) > runs.MAX_FILES:
            raise RunError('invalid_evidence_count')
        if entry['observation'] == 'unknown':
            if paths or any(number is not None for number in metrics.values()):
                raise RunError('invalid_unknown_observation')
        elif not paths:
            raise RunError('missing_cost_evidence')
        canonical = []
        for path in paths:
            path = store.path(path).relative_to(store.root).as_posix()
            if path.casefold() in refs:
                raise RunError('duplicate_cost_evidence')
            refs.add(path.casefold())
            canonical.append(path)
        if len(refs) > runs.MAX_FILES:
            raise RunError('invalid_evidence_count')
        normalized.append({**entry, 'metrics': dict(metrics), 'evidence': sorted(canonical)})
    normalized.sort(key=lambda entry: entry['id'])
    return normalized, sorted(path for entry in normalized for path in entry['evidence'])


def _payload(store, prepared, report_info, report):
    entries, refs = _entries(store, report, prepared['id'])
    inputs = {item['path'].casefold() for item in runs._input_files(prepared['payload'])}
    if report_info['path'].casefold() in inputs:
        raise RunError('cost_report_overlaps_input')
    inputs.add(report_info['path'].casefold())
    if any(path.casefold() in inputs for path in refs):
        raise RunError('cost_evidence_overlaps_input')
    files = store.capture(refs, limit=runs.MAX_EVIDENCE_BYTES, used_bytes=report_info['size'])
    return dict(run_id=prepared['id'], prepared_receipt_sha256=prepared['receipt_sha256'],
                report_source=report_info, scope_complete=report['scope_complete'],
                entries=entries, evidence=files)


def _load(store, cost_id):
    receipt = store.read_record(_name(cost_id))
    if receipt['kind'] != 'run_cost' or receipt['id'] != cost_id:
        raise RunError('invalid_cost_receipt')
    payload = receipt['payload']
    runs._fields(payload, {'run_id', 'prepared_receipt_sha256', 'report_source',
                           'scope_complete', 'entries', 'evidence'}, 'invalid_cost_receipt')
    if evidence.digest(evidence.encode(payload)) != cost_id:
        raise RunError('cost_identity_mismatch')
    prepared = runs._load_prepared(store, payload['run_id'])
    if prepared['receipt_sha256'] != payload['prepared_receipt_sha256']:
        raise RunError('cost_prepared_binding_mismatch')
    runs._fingerprint_record(store, payload['report_source'], limit=runs.MAX_JSON_BYTES)
    report_info, report = store.json_file(payload['report_source']['path'])
    if report_info != payload['report_source']:
        raise RunError('stale_cost_report')
    # Reparse the bound report and recapture its actual referenced files. A
    # recomputed receipt checksum/ID cannot bless unrelated normalized entries.
    # JSON types are significant: Python's equality would accept true == 1 or
    # 100.0 == 100 in a forged normalized payload with a recomputed checksum.
    if evidence.encode(_payload(store, prepared, report_info, report)) != evidence.encode(payload):
        raise RunError('cost_report_binding_mismatch')
    store.policy.fresh()
    return receipt


def _totals(entries):
    known, unknown, totals = {}, {}, {}
    for metric in METRICS:
        values = [entry['metrics'][metric] for entry in entries]
        known[metric] = sum(number for number in values if number is not None)
        unknown[metric] = sum(number is None for number in values)
        totals[metric] = None if unknown[metric] else known[metric]
    return dict(totals=totals, known_subtotals=known, unknown_counts=unknown)


def _scope(payload):
    entries = payload['entries']
    missing = sorted(PHASES - {entry['phase'] for entry in entries})
    unknown_observations = sum(entry['observation'] == 'unknown' for entry in entries)
    unknown_metrics = sum(number is None for entry in entries for number in entry['metrics'].values())
    return dict(scope_complete=payload['scope_complete'], missing_phases=missing,
                unknown_observations=unknown_observations,
                full_cost_available=payload['scope_complete'] and not missing
                    and not unknown_observations and not unknown_metrics)


def _view(receipt):
    payload = receipt['payload']
    return dict(ok=True, id=receipt['id'],
                receipt_sha256=receipt['receipt_sha256'], **payload,
                **_totals(payload['entries']), **{key: value for key, value in _scope(payload).items()
                                                if key != 'scope_complete'},
                authority=runs.AUTHORITY, metering=METERING, scope_authority=SCOPE_AUTHORITY)


@runs._public
def record(vault, run_id, report):
    """Bind selected cost report/evidence to a current prepared native receipt."""
    store = runs.Store(vault)
    prepared = runs._load_prepared(store, run_id)
    report_info, report_value = store.json_file(report)
    payload = _payload(store, prepared, report_info, report_value)
    cost_id = evidence.digest(evidence.encode(payload))
    name = _name(cost_id)
    def recheck():
        current = runs._load_prepared(store, run_id)
        if current != prepared:
            raise RunError('prepared_receipt_changed')
        info, value = store.json_file(report_info['path'])
        if info != report_info or _payload(store, current, info, value) != payload:
            raise RunError('cost_sources_changed')
    with state.advisory_lock(vault, 'run-cost:' + cost_id, wait_seconds=1) as lease:
        if store.record_path(name).exists():
            raise RunError('immutable_receipt_exists')
        receipt = store.publish(name, 'run_cost', cost_id, payload, recheck, lease)
    return dict(ok=True, id=cost_id, run_id=run_id, path=runs.RUNTIME_DIR + '/' + name,
                receipt_sha256=receipt['receipt_sha256'], authority=runs.AUTHORITY,
                metering=METERING, scope_authority=SCOPE_AUTHORITY)


@runs._public
def inspect(vault, cost_id):
    """Read-only source/currentness validation with recomputed reported totals."""
    return _view(_load(runs.Store(vault), cost_id))


@runs._public
def summarize(vault, cost_ids):
    """Sum explicitly selected, unique runs/evidence; unknown totals stay null."""
    if not isinstance(cost_ids, list) or not 1 <= len(cost_ids) <= runs.MAX_FILES:
        raise RunError('invalid_cost_selection')
    selected = [runs._hash(cost_id) for cost_id in cost_ids]
    if len(set(selected)) != len(selected):
        raise RunError('duplicate_cost_selection')
    store = runs.Store(vault)
    receipts, run_ids, refs, entries, scopes = [], set(), set(), [], []
    for cost_id in sorted(selected):
        receipt = _load(store, cost_id)
        payload = receipt['payload']
        if payload['run_id'] in run_ids:
            raise RunError('duplicate_cost_run')
        run_ids.add(payload['run_id'])
        paths = {item['path'].casefold() for item in payload['evidence']}
        if paths & refs:
            raise RunError('shared_cost_evidence')
        refs.update(paths)
        entries.extend(payload['entries'])
        scopes.append(dict(id=cost_id, run_id=payload['run_id'], **_scope(payload)))
        receipts.append(receipt)
    for receipt in receipts:
        if _load(store, receipt['id']) != receipt:
            raise RunError('cost_receipt_changed')
    return dict(ok=True, cost_ids=sorted(selected), run_count=len(run_ids), entry_count=len(entries),
                scopes=scopes, scope_complete=all(scope['scope_complete'] for scope in scopes),
                full_cost_available=all(scope['full_cost_available'] for scope in scopes),
                **_totals(entries), authority=runs.AUTHORITY, metering=METERING,
                scope_authority=SCOPE_AUTHORITY)


@runs._public
def compare(vault, baseline_cost_id, candidate_cost_id):
    """Return native performance evidence and sufficiently scoped reported deltas."""
    store = runs.Store(vault)
    before = _load(store, baseline_cost_id)
    after = _load(store, candidate_cost_id)
    baseline, candidate = _view(before), _view(after)
    # Native completion, evaluator compatibility and hard-failure veto meanings
    # are preserved, including native comparison receipt publication.
    performance = runs.compare(vault, baseline['run_id'], candidate['run_id'])
    reasons = set()
    for view in (baseline, candidate):
        if not view['scope_complete']:
            reasons.add('scope_incomplete')
        if view['missing_phases']:
            reasons.add('missing_phases')
        if view['unknown_observations']:
            reasons.add('unknown_observations')
        if any(view['unknown_counts'].values()):
            reasons.add('unknown_metrics')
    bases = {entry['cost_basis'] for view in (baseline, candidate) for entry in view['entries']}
    basis = next(iter(bases)) if len(bases) == 1 and bases <= {'billed', 'estimated'} else None
    if basis is None:
        reasons.add('mixed_or_unequal_cost_basis')
    for receipt in (before, after):
        if _load(store, receipt['id']) != receipt:
            raise RunError('cost_receipt_changed')
    deltas = {metric: candidate['totals'][metric] - baseline['totals'][metric]
              if not reasons else None for metric in METRICS}
    return dict(ok=True, baseline_cost_id=baseline_cost_id, candidate_cost_id=candidate_cost_id,
                performance=performance, baseline=baseline, candidate=candidate,
                cost_comparison_available=not reasons, cost_reasons=sorted(reasons),
                cost_basis=basis, reported_deltas=deltas, authority=runs.AUTHORITY,
                metering=METERING, scope_authority=SCOPE_AUTHORITY)


def main(argv=None):
    parser = runs._ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=runs._ArgumentParser)
    rec = commands.add_parser('record', help='bind caller-reported costs; never execute tools/models')
    rec.add_argument('id')
    rec.add_argument('--report', required=True)
    commands.add_parser('inspect', help='read-only current source check').add_argument('id')
    commands.add_parser('summarize', help='sum explicit unique runs').add_argument('ids', nargs='+')
    comp = commands.add_parser('compare', help='native performance and reported resource evidence')
    comp.add_argument('baseline_id')
    comp.add_argument('candidate_id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'record':
            result = record(args.vault, args.id, args.report)
        elif args.command == 'inspect':
            result = inspect(args.vault, args.id)
        elif args.command == 'summarize':
            result = summarize(args.vault, args.ids)
        else:
            result = compare(args.vault, args.baseline_id, args.candidate_id)
        print(evidence.encode(result).decode('ascii'))
        return 0
    except RunError as exc:
        print(evidence.encode(dict(ok=False, error=str(exc))).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
