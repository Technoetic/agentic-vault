#!/usr/bin/env python3
"""Opt-in, read-only assembly of explicitly scoped current ledger evidence.

The host selects candidate lines; this module does not extract meaning from
prose. Source authority is supplied separately by the caller, never inferred
from source text, timestamps or a candidate's own claims. All results are
advisory point-in-time bindings, not truth or an authorization certificate.
"""
from __future__ import annotations

import argparse
from datetime import date
from functools import wraps

import vault_evidence as evidence
import vault_runs as runs
import vault_ssot as ssot
import vault_state as state
from vault_healthcheck import HealthcheckError

MAX_SOURCES = 8
MAX_CANDIDATES = 32
MAX_LIMITATIONS = 8
MAX_LINE = 20000
SYSTEM_LIMITATIONS = [
    'Host-selected literal identities and explicit scope/polarity only; no aliases or semantic negation inference.',
    'Ledger citations are reported, not independently checked for semantic entailment or factual truth.',
    'Source hashes and authority arguments are not signatures or proof of caller authorization.',
    'Freshness checks are point-in-time reads, not a multi-file transaction or filesystem freeze.',
]


class AssemblyError(Exception):
    """A content-free operational error code."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AssemblyError:
            raise
        except (runs.RunError, ssot.SSOTError, state.StateError, evidence.EvidenceError,
                HealthcheckError, ValueError, OSError, UnicodeError, RecursionError,
                OverflowError, TypeError, KeyError) as exc:
            raise AssemblyError('unsafe_or_invalid_input') from exc
    return call


def _fields(value, expected):
    runs._fields(value, expected, 'invalid_assembly_input')


def _date(value):
    if not isinstance(value, str) or not ssot.DATE_RE.fullmatch(value):
        raise ValueError()
    return date.fromisoformat(value)


def _canonical(store, value, *, markdown=True):
    path = store.path(value, markdown=markdown)
    return path.relative_to(store.root).as_posix()


def _bindings(store, values):
    if not isinstance(values, list) or len(values) > MAX_SOURCES:
        raise AssemblyError('invalid_source_count')
    result, seen = [], set()
    for value in values:
        _fields(value, {'path', 'sha256'})
        path = _canonical(store, value['path'])
        key = path.casefold()
        if key in seen:
            raise AssemblyError('duplicate_source_binding')
        seen.add(key)
        result.append({'path': path, 'sha256': runs._hash(value['sha256'])})
    return result


def _definition(store, value):
    runs._safe_json(value)
    _fields(value, {'version', 'query', 'sources', 'candidates', 'limitations'})
    if type(value['version']) is not int or value['version'] != 1:
        raise AssemblyError('invalid_version')
    query = value['query']
    _fields(query, {'subject', 'relation', 'scope', 'as_of'})
    for key in ('subject', 'relation', 'scope'):
        runs._text(query[key], maximum=256)
        if query[key] != query[key].strip():
            raise AssemblyError('invalid_query')
    _date(query['as_of'])
    sources = _bindings(store, value['sources'])
    source_names = {x['path'].casefold(): x['path'] for x in sources}
    candidates = value['candidates']
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise AssemblyError('invalid_candidate_count')
    selected, seen = [], set()
    for candidate in candidates:
        _fields(candidate, {'path', 'line'})
        path = _canonical(store, candidate['path'])
        line = candidate['line']
        if type(line) is not int or not 1 <= line <= MAX_LINE:
            raise AssemblyError('invalid_candidate_line')
        key = (path.casefold(), line)
        if key in seen:
            raise AssemblyError('duplicate_candidate')
        if key[0] not in source_names:
            raise AssemblyError('candidate_source_not_bound')
        seen.add(key)
        selected.append({'path': source_names[key[0]], 'line': line})
    limitations = value['limitations']
    if not isinstance(limitations, list) or len(limitations) > MAX_LIMITATIONS:
        raise AssemblyError('invalid_limitations')
    for limitation in limitations:
        runs._text(limitation, maximum=512)
    return {**value, 'query': dict(query), 'sources': sources, 'candidates': selected,
            'limitations': list(limitations)}


def _rows(info, text):
    parsed = ssot.parse_ledger(text)
    lines = text.splitlines()
    result = {}
    for table in parsed['tables']:
        headers = [x.strip().casefold() for x in table['headers']]
        supported = (table['schema_version'] == 2 and not table['duplicate_fields']
                     and not parsed['diagnostics'])
        scoped = headers.count('scope') == 1 and headers.count('polarity') == 1
        for row in table['rows']:
            fields = dict(row['fields'])
            fields['status'] = fields.get('status', '') or table.get('section_status', '')
            fields['scope'] = row['cells'][headers.index('scope')] if scoped else None
            fields['polarity'] = row['cells'][headers.index('polarity')] if scoped else None
            reasons = []
            if not supported:
                reasons.append('unsupported_schema')
            if not scoped or not fields['scope'] or not fields['polarity']:
                reasons.append('missing_scope_or_polarity')
            result[row['line']] = {'path': info['path'], 'sha256': info['sha256'],
                'line': row['line'], 'quote': lines[row['line'] - 1], 'fields': fields,
                'extraction_reasons': reasons}
    return result, lines


def _load(vault, input_path):
    store = runs.Store(vault)
    input_info, raw_definition = store.json_file(input_path)
    definition = _definition(store, raw_definition)
    sources, rows, lines, total = [], {}, {}, 0
    for binding in definition['sources']:
        info, raw = store.fingerprint(binding['path'], markdown=True)
        if info['sha256'] != binding['sha256']:
            raise AssemblyError('source_binding_changed')
        total += info['size']
        if total > runs.MAX_TOTAL_BYTES:
            raise AssemblyError('source_set_too_large')
        sources.append(info)
        rows[info['path']], lines[info['path']] = _rows(info, raw.decode('utf-8-sig'))
    candidates = []
    hashes = {x['path']: x['sha256'] for x in sources}
    for selected in definition['candidates']:
        path, line = selected['path'], selected['line']
        if line > len(lines[path]):
            raise AssemblyError('invalid_candidate_line')
        record = rows[path].get(line)
        if record is None:
            record = {**selected, 'sha256': hashes[path], 'quote': lines[path][line - 1],
                      'fields': None, 'extraction_reasons': ['unsupported_schema']}
        candidates.append(record)
    return store, input_info, definition, sources, rows, candidates


def _fresh(store, input_info, sources):
    store.policy.fresh()
    for old in [input_info, *sources]:
        current, _ = store.fingerprint(old['path'], limit=runs.MAX_JSON_BYTES if old is input_info
                                       else runs.MAX_SOURCE_BYTES, markdown=old is not input_info)
        if current['sha256'] != old['sha256']:
            raise AssemblyError('source_changed')
    store.policy.fresh()


def _result(definition, sources, **fields):
    result = dict(ok=True, version=1, authority=runs.AUTHORITY, requires_review=True,
                  applies_changes=False, query=definition['query'], sources=sources,
                  limitations=definition['limitations'], system_limitations=list(SYSTEM_LIMITATIONS), **fields)
    runs._safe_json(result)
    return result


@_public
def extract(vault, input_path):
    """Re-extract selected current ledger lines, without choosing a fact or policy."""
    store, input_info, definition, sources, _all_rows, candidates = _load(vault, input_path)
    result = _result(definition, sources, status='extracted', candidates=candidates)
    _fresh(store, input_info, sources)
    return result


def _period(fields, as_of):
    try:
        start = _date(fields.get('valid_from'))
        until = _date(fields['valid_until']) if fields.get('valid_until') else None
        if until is not None and until <= start:
            return 'unknown_or_invalid_validity'
    except (ValueError, TypeError):
        return 'unknown_or_invalid_validity'
    return 'outside_validity' if as_of < start or (until is not None and as_of >= until) else None


@_public
def assemble(vault, input_path, *, authorized_sources=None):
    """Select a unique current literal value within a separately authorized set.

    The caller must obtain source authority from its existing authorization.
    Supplying these arguments is explicit policy selection, not authentication.
    """
    store, input_info, definition, sources, all_rows, candidates = _load(vault, input_path)
    runs._safe_json([] if authorized_sources is None else authorized_sources)
    authority = _bindings(store, [] if authorized_sources is None else authorized_sources)
    declared = {x['path'].casefold(): x for x in sources}
    allowed = set()
    for binding in authority:
        key = binding['path'].casefold()
        if key not in declared or binding['sha256'] != declared[key]['sha256']:
            raise AssemblyError('authority_source_not_bound')
        allowed.add(declared[key]['path'])
    query = definition['query']
    as_of = _date(query['as_of'])
    reasons, blockers, eligible = set(), set(), []
    selected = {(x['path'], x['line']) for x in candidates}
    # The selected lines may not conceal other potentially current rows with
    # the same literal identity in an authorized ledger.
    for path in allowed:
        for line, row in all_rows[path].items():
            fields = row['fields']
            if (fields.get('subject') == query['subject'] and fields.get('relation') == query['relation']
                    and fields.get('scope') in (None, '', query['scope'])
                    and _period(fields, as_of) != 'outside_validity'
                    and (path, line) not in selected):
                blockers.add('selection_incomplete')
    for candidate in candidates:
        if candidate['path'] not in allowed:
            reasons.add('untrusted_source_excluded')
            continue
        fields = candidate['fields']
        if fields is None:
            blockers.update(candidate['extraction_reasons'])
            continue
        if fields.get('subject') != query['subject'] or fields.get('relation') != query['relation']:
            reasons.add('identity_mismatch')
            continue
        if candidate['extraction_reasons']:
            blockers.update(candidate['extraction_reasons'])
            continue
        if fields['scope'] != query['scope']:
            reasons.add('scope_mismatch')
            continue
        period = _period(fields, as_of)
        if period == 'outside_validity':
            reasons.add(period)
            continue
        if period:
            blockers.add(period)
            continue
        status = fields.get('status', '').strip().casefold()
        if status in ssot.REJECTED:
            blockers.add('rejected_candidate')
        elif status not in ssot.CONFIRMED:
            blockers.add('unconfirmed_candidate')
        elif fields['polarity'].strip().casefold() != 'positive':
            blockers.add('negative_or_uncertain_polarity')
        elif fields.get('superseded_by', '').strip():
            blockers.add('superseded_candidate')
        elif not fields.get('source', '').strip():
            blockers.add('missing_citation')
        elif not fields.get('value', '').strip():
            blockers.add('missing_value')
        else:
            eligible.append(candidate)
    values = {x['fields']['value'] for x in eligible}
    if len(values) > 1:
        blockers.add('conflicting_current_values')
    if blockers:
        status, value = 'abstain', None
    elif values:
        status, value = 'selected', next(iter(values))
    else:
        status, value = 'no_evidence', None
        if not allowed:
            reasons.add('no_authorized_evidence')
        elif not reasons:
            reasons.add('no_matching_current_evidence')
    result = _result(definition, sources, status=status, value=value,
                     reasons=sorted(reasons | blockers), evidence=eligible, candidates=candidates,
                     authorized_sources=authority)
    _fresh(store, input_info, sources)
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default diagnostics repeat untrusted command-line values.
        print('{"ok":false,"error":"invalid_arguments"}')
        raise SystemExit(2)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('--vault', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--authority', action='append', default=[], metavar='PATH=SHA256')
    args = parser.parse_args(argv)
    try:
        if args.action == 'extract' and not args.authority:
            result = extract(args.vault, args.input)
        elif args.action == 'assemble':
            authority = []
            for item in args.authority:
                if '=' not in item:
                    raise AssemblyError('invalid_authority_binding')
                path, sha256 = item.rsplit('=', 1)
                authority.append({'path': path, 'sha256': sha256})
            result = assemble(args.vault, args.input, authorized_sources=authority)
        else:
            raise AssemblyError('invalid_action')
        print(evidence.encode(result).decode('ascii'))
        return 0
    except AssemblyError:
        print('{"ok":false,"error":"unsafe_or_invalid_input"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
