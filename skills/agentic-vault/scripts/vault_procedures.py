#!/usr/bin/env python3
"""Bounded advisory lookup of explicitly catalogued procedures; never execute."""
from __future__ import annotations

import argparse
from functools import wraps
from pathlib import Path

import vault_runs as runs
import vault_retrieval as retrieval

MAX_PROCEDURES = 32
MAX_STEPS = 16
MAX_EDGES = 64
MAX_RUNS = 16
MAX_OUTPUT_BYTES = 64 * 1024
PROCEDURE_FIELDS = {'id', 'task_class', 'cues', 'preconditions', 'steps', 'edges',
                    'candidate', 'instructions', 'run_ids', 'conditions'}
CONDITION_FIELDS = {'model', 'provider', 'token_budget', 'tools_sha256'}


class ProcedureError(Exception):
    """Content-free operational error code."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ProcedureError:
            raise
        except (runs.RunError, runs.state.StateError, runs.evidence.EvidenceError,
                runs.HealthcheckError, ValueError, OSError, UnicodeError, TypeError,
                KeyError, IndexError, RecursionError, OverflowError) as exc:
            raise ProcedureError('unsafe_or_invalid_input') from exc
    return call


def _conditions(value):
    runs._safe_json(value)
    runs._fields(value, CONDITION_FIELDS, 'invalid_conditions')
    runs._text(value['model'], label=True)
    runs._text(value['provider'], label=True)
    runs._hash(value['tools_sha256'])
    if type(value['token_budget']) is not int or not 1 <= value['token_budget'] <= 1000000:
        raise ProcedureError('invalid_conditions')
    return value


def _binding(value):
    runs._fields(value, {'path', 'sha256', 'size'}, 'invalid_binding')
    runs._text(value['path'], maximum=1024)
    runs._hash(value['sha256'])
    if type(value['size']) is not int or not 0 <= value['size'] <= runs.MAX_SOURCE_BYTES:
        raise ProcedureError('invalid_binding')


def _strings(value, maximum, length):
    if not isinstance(value, list) or len(value) > maximum:
        raise ProcedureError('invalid_count')
    for item in value:
        runs._text(item, maximum=length)


def _dag(steps, edges):
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ProcedureError('invalid_steps')
    order = {}
    for index, step in enumerate(steps):
        runs._fields(step, {'id', 'instruction'}, 'invalid_step')
        runs._text(step['id'], label=True)
        runs._text(step['instruction'], maximum=1024)
        if step['id'] in order:
            raise ProcedureError('duplicate_step')
        order[step['id']] = index
    if not isinstance(edges, list) or len(edges) > MAX_EDGES:
        raise ProcedureError('invalid_edges')
    seen, outgoing, degree = set(), {k: [] for k in order}, {k: 0 for k in order}
    for edge in edges:
        runs._fields(edge, {'before', 'after'}, 'invalid_edge')
        before, after = edge['before'], edge['after']
        if (not isinstance(before, str) or not isinstance(after, str) or before not in order
                or after not in order or before == after or (before, after) in seen):
            raise ProcedureError('invalid_dependency')
        seen.add((before, after))
        outgoing[before].append(after)
        degree[after] += 1
    ready, visited = [k for k in order if not degree[k]], 0
    while ready:
        node = ready.pop()
        visited += 1
        for after in outgoing[node]:
            degree[after] -= 1
            if not degree[after]:
                ready.append(after)
    if visited != len(order):
        raise ProcedureError('cyclic_dependencies')
    if any(order[a] >= order[b] for a, b in seen):
        raise ProcedureError('dependency_order_conflict')


def _catalog(value):
    runs._safe_json(value)
    runs._fields(value, {'version', 'procedures'}, 'invalid_catalog')
    records = value['procedures']
    if type(value['version']) is not int or value['version'] != 1 or not isinstance(records, list) or len(records) > MAX_PROCEDURES:
        raise ProcedureError('invalid_catalog')
    ids, run_ids, total_bytes = set(), set(), 0
    for record in records:
        runs._fields(record, PROCEDURE_FIELDS, 'invalid_procedure')
        runs._text(record['id'], label=True)
        runs._text(record['task_class'], label=True)
        if record['id'] in ids:
            raise ProcedureError('duplicate_procedure')
        ids.add(record['id'])
        _strings(record['cues'], 8, 256)
        if not record['cues']:
            raise ProcedureError('missing_cues')
        _strings(record['preconditions'], 8, 512)
        _dag(record['steps'], record['edges'])
        _binding(record['candidate'])
        if not isinstance(record['instructions'], list) or not 1 <= len(record['instructions']) <= 8:
            raise ProcedureError('invalid_instructions')
        paths = set()
        for binding in [record['candidate'], *record['instructions']]:
            _binding(binding)
            if binding['path'].casefold() in paths:
                raise ProcedureError('duplicate_binding')
            paths.add(binding['path'].casefold())
            total_bytes += binding['size']
        if total_bytes > runs.MAX_TOTAL_BYTES:
            raise ProcedureError('source_set_too_large')
        if not isinstance(record['run_ids'], list) or not 1 <= len(record['run_ids']) <= 4:
            raise ProcedureError('invalid_run_count')
        for run_id in record['run_ids']:
            runs._hash(run_id)
        if len(set(record['run_ids'])) != len(record['run_ids']):
            raise ProcedureError('duplicate_run')
        run_ids.update(record['run_ids'])
        _conditions(record['conditions'])
    if len(run_ids) > MAX_RUNS:
        raise ProcedureError('too_many_runs')
    return records


def _execution_conditions(manifest):
    return {key: manifest[key] for key in ('model', 'provider', 'token_budget')} | {
        'tools_sha256': runs.evidence.digest(runs.evidence.encode(manifest['tools']))}


def _candidate(store, record, conditions, cache):
    if record['conditions'] != conditions:
        return None, 'reuse_conditions_mismatch'
    texts = []
    for binding in [record['candidate'], *record['instructions']]:
        runs._fingerprint_record(store, binding, markdown=True)
        actual, raw = store.fingerprint(binding['path'], markdown=True)
        if actual != binding:
            return None, 'stale_source_binding'
        texts.append((binding, raw.decode('utf-8-sig')))
    steps = []
    for step in record['steps']:
        source = next((binding for binding, text in texts if step['instruction'] in text), None)
        if source is None:
            return None, 'step_not_bound_to_instruction'
        steps.append({**step, 'source': source})
    receipts = []
    for run_id in record['run_ids']:
        if run_id not in cache:
            cache[run_id] = runs.inspect(store.vault, run_id)
        run = cache[run_id]
        manifest, completion = run['manifest'], run['completion']
        if run['status'] != 'complete' or completion is None:
            return None, 'run_not_complete'
        if (manifest['candidate'] != record['candidate']
                or sorted(manifest['sources'], key=lambda x: x['path']) != sorted(record['instructions'], key=lambda x: x['path'])
                or _execution_conditions(manifest) != conditions):
            return None, 'run_binding_or_conditions_mismatch'
        if any(outcome['status'] != 'complete' or any(check['passed'] is not True for check in outcome['assertions'])
               for outcome in completion['outcomes']):
            return None, 'failed_or_error_observation'
        receipts.append({'run_id': run_id, 'manifest_sha256': run['manifest_sha256'],
                         'evidence': completion['evidence'], 'authority': run['authority']})
    return {'id': record['id'], 'task_class': record['task_class'], 'cues': record['cues'],
            'preconditions': record['preconditions'], 'steps': steps, 'edges': record['edges'],
            'candidate': record['candidate'], 'instructions': record['instructions'],
            'reuse_conditions': conditions, 'evidence': receipts,
            'authority': 'advisory_current_host_observations_only', 'requires_review': True,
            'applies_changes': False, 'uncertainty': [
                'Preconditions are declarations, not verified satisfaction or execution authority.',
                'Substring bindings and successful host assertions do not prove full procedure execution, human review or truth.']}, None


@_public
def lookup(vault, catalog, task_class, query, *, conditions, limit=5, max_bytes=32768):
    """Read only selected catalogue/receipt bindings; abstain on stale or failed data."""
    if (type(limit) is not int or not 1 <= limit <= 10 or type(max_bytes) is not int
            or not 512 <= max_bytes <= MAX_OUTPUT_BYTES):
        raise ProcedureError('invalid_limits')
    runs._text(task_class, label=True)
    runs._text(query, maximum=512)
    _conditions(conditions)
    store = runs.Store(vault)
    catalog_info, value = store.json_file(catalog)
    records = _catalog(value)
    eligible = {p['id']: p for p in records if p['task_class'] == task_class}
    docs = {key: {'text': ' '.join([*p['cues'], *p['preconditions'],
                                  *(s['instruction'] for s in p['steps'])])} for key, p in eligible.items()}
    terms = retrieval._legacy._WORD_RE.findall(retrieval._legacy._normalize(query))
    scores = retrieval._bm25(docs, terms)
    result = {'ok': True, 'status': 'no_match', 'matches': [], 'excluded': [], 'truncated': False,
              'reasons': [], 'catalog': catalog_info, 'authority': 'advisory',
              'requires_review': True, 'applies_changes': False}
    if len(runs.evidence.encode(result)) > max_bytes:
        raise ProcedureError('output_budget_too_small')
    cache = {}
    for key in sorted(scores, key=lambda k: (-scores[k], k)):
        record = eligible[key]
        try:
            candidate, reason = _candidate(store, record, conditions, cache)
        except (runs.RunError, runs.state.StateError, runs.evidence.EvidenceError, OSError, ValueError):
            candidate, reason = None, 'unsafe_stale_or_unavailable_evidence'
        if candidate is not None and len(result['matches']) >= limit:
            result['truncated'] = True
            if 'result_limit' not in result['reasons']:
                result['reasons'].append('result_limit')
            continue
        target = result['matches'] if candidate is not None else result['excluded']
        target.append(candidate if candidate is not None else {'id': key, 'reason': reason})
        # Reserve room for the explicit truncation marker even on the last item.
        if len(runs.evidence.encode(result)) + 80 > max_bytes:
            target.pop()
            result['truncated'] = True
            if 'output_byte_budget' not in result['reasons']:
                result['reasons'].append('output_byte_budget')
    for candidate in result['matches']:
        for item in candidate['evidence']:
            if runs.inspect(vault, item['run_id']) != cache[item['run_id']]:
                raise ProcedureError('inputs_changed_during_lookup')
        for binding in [candidate['candidate'], *candidate['instructions']]:
            actual, _ = store.fingerprint(binding['path'], markdown=True)
            if actual != binding:
                raise ProcedureError('inputs_changed_during_lookup')
    current_catalog, _ = store.json_file(catalog)
    if current_catalog != catalog_info:
        raise ProcedureError('catalog_changed_during_lookup')
    store.policy.fresh()
    result['status'] = 'matches' if result['matches'] else 'no_match'
    if len(runs.evidence.encode(result)) > max_bytes:
        raise ProcedureError('output_budget_too_small')
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print(runs.evidence.encode({'ok': False, 'error': 'invalid_cli_arguments'}).decode('ascii'))
        self.exit(2)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    lookup_parser = commands.add_parser('lookup')
    for key in ('catalog', 'task-class', 'query', 'conditions'):
        lookup_parser.add_argument('--' + key, required=True)
    lookup_parser.add_argument('--limit', type=int, default=5)
    lookup_parser.add_argument('--max-bytes', type=int, default=32768)
    args = parser.parse_args(argv)
    try:
        store = runs.Store(args.vault)
        info, conditions = store.json_file(args.conditions)
        result = lookup(args.vault, args.catalog, args.task_class, args.query,
                        conditions=conditions, limit=args.limit, max_bytes=args.max_bytes)
        after, _ = store.json_file(args.conditions)
        if info != after:
            raise ProcedureError('conditions_changed_during_lookup')
        store.policy.fresh()
        print(runs.evidence.encode(result).decode('ascii'))
        return 0
    except (ProcedureError, runs.RunError, runs.state.StateError, runs.evidence.EvidenceError,
            OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        print(runs.evidence.encode({'ok': False, 'error': 'unsafe_or_invalid_input'}).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
