#!/usr/bin/env python3
"""Revision-bound literal fact coverage before explicit source quarantine.

Only caller-selected Markdown files are read. Retrieval exclusions are not a
permission boundary for explicitly selected ingest inputs; deny zones, reserved
runtime/receipt paths, source hashes and portable path checks still apply.
This tool never moves sources, writes notes or makes semantic truth judgments.
The opt-in reverse direction (report_unsupported / --report-unsupported) lists
target literals found in no selected source. It is an observation only: it does
not change status, review_reason or the exit code, and a literal is not truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

from vault_proposals import Store, ProposalError, decode_json, read_bounded
from vault_recall import _read_regular_bytes

MAX_INPUT_BYTES = 128 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 16
MAX_CLAIMS = 64
MAX_FACTS = 512
HASH_RE = re.compile(r'[0-9a-f]{64}')
DATE_RE = re.compile(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)')
NUMBER_RE = re.compile(r'(?<![A-Za-z0-9_])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:%|만원|개|대|명|원|건|억|시간|분|초|kg|mm|kva|kv)?(?![0-9_])')
NAME_RE = re.compile(r'(?im)^\s*(?:name|person|organization|company|이름|담당자|회사|인명)\s*:\s*([^\n]{1,128})$')
WIKI_RE = re.compile(r'\[\[([^\]\n|#]{1,128})(?:#[^\]\n|]*)?(?:\|[^\]\n]*)?\]\]')


class QualityError(Exception):
    """Safe error code without source content."""


def _normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _body(text):
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((i for i, line in enumerate(lines[1:65], 1) if line.strip() == '---'), None)
        if end is not None:
            return '\n'.join(lines[end + 1:])
    return text


def _bindings(store, rows, role, budget):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise QualityError('invalid_' + role + '_list')
    found = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256'}:
            raise QualityError('invalid_binding')
        relative, expected = row['path'], row['sha256']
        if not isinstance(relative, str) or not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
            raise QualityError('invalid_binding')
        try:
            path = store.note_path(relative)
            canonical = path.relative_to(store.vault.resolve()).as_posix()
            if canonical.casefold().startswith('00-meta/.agentic-vault/runtime/'):
                raise QualityError('reserved_runtime')
            if canonical in seen:
                raise QualityError('duplicate_binding')
            seen.add(canonical)
            data, stable = _read_regular_bytes(path, MAX_FILE_BYTES)
            if not stable or len(data) > MAX_FILE_BYTES:
                raise QualityError('unsafe_or_oversized_file')
            budget[0] += len(data)
            if budget[0] > MAX_TOTAL_BYTES:
                raise QualityError('input_byte_limit')
            store.note_path(relative)
        except QualityError:
            raise
        except OverflowError:
            raise QualityError('input_byte_limit') from None
        except (ProposalError, OSError, ValueError, RuntimeError):
            raise QualityError('unsafe_path') from None
        if hashlib.sha256(data).hexdigest() != expected:
            raise QualityError(role + '_changed')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            raise QualityError('invalid_utf8') from None
        found.append({'path': canonical, 'sha256': expected, 'bytes': len(data), 'text': text})
    return found


def _fact_rows(sources):
    facts = {}
    for source in sources:
        text = _body(source['text'])
        date_spans = []
        for match in DATE_RE.finditer(text):
            date_spans.append((match.start(), match.end()))
            key = ('date', _normalize(match.group()))
            facts.setdefault(key, set()).add(source['path'])
        # Both regex iterators are ordered. Advance each date interval once,
        # avoiding a complete interval rescan for every numeric occurrence.
        date_index = 0
        for match in NUMBER_RE.finditer(text):
            position = match.start()
            while date_index < len(date_spans) and date_spans[date_index][1] <= position:
                date_index += 1
            if date_index < len(date_spans) and date_spans[date_index][0] <= position < date_spans[date_index][1]:
                continue
            key = ('number', _normalize(match.group()).rstrip('.'))
            if len(key[1]) > 1024:
                raise QualityError('fact_literal_limit')
            facts.setdefault(key, set()).add(source['path'])
        for regex in (NAME_RE, WIKI_RE):
            for match in regex.finditer(text):
                value = match.group(1).strip().strip('"\'')
                if value:
                    facts.setdefault(('name', _normalize(value)), set()).add(source['path'])
        if len(facts) > MAX_FACTS:
            raise QualityError('fact_limit')
    return facts


def _present(kind, literal, numbers, dates, texts):
    """Apply one matching rule in either direction over the other side's literals."""
    # Numbers/dates use exact extracted tokens. Names use conservative
    # Unicode word boundaries: a longer contiguous name is different.
    # Attached Korean particles may need a separate structured label or
    # an explicit host claim; this is literal coverage, not linguistic NER.
    if kind == 'number':
        return literal in numbers
    if kind == 'date':
        return literal in dates
    if kind == 'name':
        pattern = re.compile(r'(?<!\w)' + re.escape(literal) + r'(?!\w)')
        return any(pattern.search(body) is not None for body in texts)
    return any(literal in body for body in texts)


def check_compile(vault, sources, targets, required_claims, *, report_unsupported=False):
    """Compare bounded literals; hashes bind current originals and compiled notes.

    report_unsupported=True adds the precision direction: number/date/name
    literals extracted from targets with the same scope and limits that appear
    in no selected source. Default output is unchanged when it is False.
    """
    if not isinstance(required_claims, list) or len(required_claims) > MAX_CLAIMS:
        raise QualityError('invalid_required_claims')
    if any(not isinstance(item, str) or not item.strip() or len(item) > 1024 for item in required_claims):
        raise QualityError('invalid_required_claim')
    try:
        store = Store(Path(vault))
        config_path = store.path('00-meta/vault-config.json')
        config_sha = hashlib.sha256(read_bounded(config_path, MAX_FILE_BYTES)).hexdigest()
        budget = [0]
        originals = _bindings(store, sources, 'source', budget)
        compiled = _bindings(store, targets, 'target', budget)
    except QualityError:
        raise
    except (ProposalError, OSError, ValueError, RuntimeError):
        raise QualityError('invalid_vault_policy') from None
    source_texts = {source['path']: _normalize(_body(source['text'])) for source in originals}
    target_texts = [_normalize(_body(target['text'])) for target in compiled]
    target_facts = _fact_rows(compiled)
    target_numbers = {literal for kind, literal in target_facts if kind == 'number'}
    target_dates = {literal for kind, literal in target_facts if kind == 'date'}
    facts = _fact_rows(originals)
    for claim in required_claims:
        normalized = _normalize(claim)
        matched = {path for path, body in source_texts.items() if normalized in body}
        if not matched:
            raise QualityError('claim_not_in_source')
        facts.setdefault(('claim', normalized), set()).update(matched)
        if len(facts) > MAX_FACTS:
            raise QualityError('fact_limit')
    rows = []
    for (kind, literal), paths in sorted(facts.items()):
        present = _present(kind, literal, target_numbers, target_dates, target_texts)
        rows.append({'kind': kind, 'literal': literal, 'source_paths': sorted(paths), 'preserved': present})
    unsupported = None
    if report_unsupported:
        # Precision direction with the already extracted target literals: no
        # new extraction, budget or fact limit. Host claims are source-only.
        source_numbers = {literal for kind, literal in facts if kind == 'number'}
        source_dates = {literal for kind, literal in facts if kind == 'date'}
        source_bodies = list(source_texts.values())
        unsupported = [{'kind': kind, 'literal': literal, 'target_paths': sorted(paths)}
                       for (kind, literal), paths in sorted(target_facts.items())
                       if not _present(kind, literal, source_numbers, source_dates, source_bodies)]
    # Recheck policy and every original after analysis. This is a point-in-time
    # guard, not an atomic multi-file snapshot or authority to quarantine.
    try:
        current_store = Store(Path(vault))
        current_config_sha = hashlib.sha256(read_bounded(current_store.path('00-meta/vault-config.json'), MAX_FILE_BYTES)).hexdigest()
        if current_config_sha != config_sha:
            raise QualityError('policy_changed')
        final_budget = [0]
        _bindings(current_store, sources, 'source', final_budget)
        _bindings(current_store, targets, 'target', final_budget)
        if hashlib.sha256(read_bounded(current_store.path('00-meta/vault-config.json'), MAX_FILE_BYTES)).hexdigest() != config_sha:
            raise QualityError('policy_changed')
    except QualityError:
        raise
    except (ProposalError, OSError, ValueError, RuntimeError):
        raise QualityError('source_policy_changed') from None
    missing = [row for row in rows if not row['preserved']]
    result = {'status': 'needs_review' if missing or not rows else 'complete',
              'review_reason': ('missing_literal_facts' if missing else 'no_selected_literal_facts' if not rows else None),
              'originals_current': True, 'config_sha256': config_sha,
              'source_bindings': [{k: source[k] for k in ('path', 'sha256', 'bytes')} for source in originals],
              'target_bindings': [{k: target[k] for k in ('path', 'sha256', 'bytes')} for target in compiled],
              'facts_checked': len(rows), 'facts_preserved': len(rows) - len(missing),
              'literal_coverage': ((len(rows) - len(missing)) / len(rows) if rows else None),
              'missing_facts': missing,
              'semantic_authority': 'advisory_unverified',
              'name_scope': 'structured labels and literal wiki targets with conservative Unicode word boundaries; attached Korean particles may require separate labels or explicit host claims; no linguistic NER',
              'authority': 'coverage_observation_only_not_permission_or_truth',
              'limits': {'files_per_role': MAX_FILES, 'file_bytes': MAX_FILE_BYTES, 'read_bytes_per_sweep': MAX_TOTAL_BYTES}}
    if unsupported is not None:
        # Rates use distinct (kind, literal) pairs across all selected targets.
        checked = len(target_facts)
        result.update({
            'target_literals_checked': checked,
            'unsupported_target_literals': unsupported,
            'unsupported_by_kind': {kind: sum(1 for row in unsupported if row['kind'] == kind)
                                    for kind in ('date', 'name', 'number')},
            'fabrication_candidate_rate': (len(unsupported) / checked if checked else None),
            'fabrication_scope': ('target number/date/name literals found in no selected source, using the same '
                                  'extraction, matching rules and limits; warning-only observation that never changes '
                                  'status, review_reason or exit code; paraphrase, unit and format variants cause '
                                  'misses and false alarms; a literal is not truth'),
        })
    return result


def _strip_bom(text):
    return text[1:] if text.startswith('\ufeff') else text


def _token_counts(body):
    """Occurrences of date and number tokens, extracted with the rules of _fact_rows."""
    counts = {}
    spans = []
    for match in DATE_RE.finditer(body):
        spans.append((match.start(), match.end()))
        key = ('date', _normalize(match.group()))
        counts[key] = counts.get(key, 0) + 1
    index = 0
    for match in NUMBER_RE.finditer(body):
        position = match.start()
        while index < len(spans) and spans[index][1] <= position:
            index += 1
        if index < len(spans) and spans[index][0] <= position < spans[index][1]:
            continue
        key = ('number', _normalize(match.group()).rstrip('.'))
        if len(key[1]) > 1024:
            raise QualityError('fact_literal_limit')
        counts[key] = counts.get(key, 0) + 1
    return counts


def _phrase_count(phrase, normalized_body):
    """Occurrences of a normalized phrase with conservative Unicode word boundaries on both ends."""
    return len(re.findall(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', normalized_body))


def _declared(values, name):
    if not isinstance(values, (list, tuple)) or len(values) > MAX_CLAIMS:
        raise QualityError('invalid_' + name)
    if any(not isinstance(item, str) or not item.strip() or len(item) > 1024 for item in values):
        raise QualityError('invalid_' + name)
    return [_normalize(item) for item in values]


def check_preservation(original, candidate, *, required_claims=(), expected_removals=(), expected_additions=()):
    """Occurrence-counted literal preservation from one note's original to its candidate, both given as text.

    Only the two texts are read and nothing is written. Bodies are compared
    without frontmatter or a leading BOM. Dates and numbers are counted as
    tokens with the check_compile extraction; name labels, literal wiki
    targets, declared claims and declared phrases are counted with conservative
    Unicode word boundaries. A fact whose count drops is an unexpected removal
    unless a declared removal names it. A declared removal must lower every
    count it names and a declared addition must raise every count it names; a
    declaration that contains date or number tokens is judged by those tokens
    (for example 90일 names the number 90). The result is advisory review
    material: it never permits or blocks an edit, and a literal is not truth.
    """
    claims = _declared(required_claims, 'required_claims')
    removals = _declared(expected_removals, 'expected_removals')
    additions = _declared(expected_additions, 'expected_additions')
    if set(claims) & set(removals) or set(additions) & set(removals):
        raise QualityError('conflicting_declarations')
    if not isinstance(original, str) or not isinstance(candidate, str):
        raise QualityError('invalid_text')
    if max(len(original.encode('utf-8')), len(candidate.encode('utf-8'))) > MAX_FILE_BYTES:
        raise QualityError('input_byte_limit')
    original_raw, candidate_raw = _body(_strip_bom(original)), _body(_strip_bom(candidate))
    bodies = {'original': _normalize(original_raw), 'candidate': _normalize(candidate_raw)}
    tokens = {'original': _token_counts(original_raw), 'candidate': _token_counts(candidate_raw)}

    def count(key, side):
        kind, literal = key
        if kind in ('date', 'number'):
            return tokens[side].get(key, 0)
        return _phrase_count(literal, bodies[side])

    facts = set(tokens['original'])
    facts.update(key for key in _fact_rows([{'path': 'original', 'text': _strip_bom(original)}]) if key[0] == 'name')
    for claim in claims:
        if not _phrase_count(claim, bodies['original']):
            raise QualityError('claim_not_in_original')
        facts.add(('claim', claim))
    if len(facts) > MAX_FACTS:
        raise QualityError('fact_limit')

    def declared_rows(declared):
        keys = sorted(_token_counts(declared)) or [('phrase', declared)]
        return [{'kind': kind, 'literal': literal, 'original_count': count((kind, literal), 'original'),
                 'candidate_count': count((kind, literal), 'candidate')} for kind, literal in keys]

    excused_tokens, excused_phrases, removal_rows = set(), set(removals), []
    for declared in removals:
        detail = declared_rows(declared)
        excused_tokens.update((row['kind'], row['literal']) for row in detail if row['kind'] in ('date', 'number'))
        in_original = all(row['original_count'] > 0 for row in detail)
        removal_rows.append({'literal': declared, 'in_original': in_original,
                             'removed': in_original and all(row['candidate_count'] < row['original_count']
                                                            for row in detail),
                             'counts': detail})
    addition_rows = []
    for declared in additions:
        detail = declared_rows(declared)
        addition_rows.append({'literal': declared,
                              'in_original': any(row['original_count'] > 0 for row in detail),
                              'in_candidate': all(row['candidate_count'] > 0 for row in detail),
                              'added': all(row['candidate_count'] > row['original_count'] for row in detail),
                              'counts': detail})
    rows = []
    for key in sorted(facts):
        before, after = count(key, 'original'), count(key, 'candidate')
        rows.append({'kind': key[0], 'literal': key[1], 'original_count': before, 'candidate_count': after,
                     'preserved': after >= before})

    def excused(row):
        if row['kind'] in ('date', 'number') and (row['kind'], row['literal']) in excused_tokens:
            return True
        return row['literal'] in excused_phrases

    unexpected = [{key: row[key] for key in ('kind', 'literal', 'original_count', 'candidate_count')}
                  for row in rows if not row['preserved'] and not excused(row)]
    reasons = []
    if unexpected:
        reasons.append('unexpected_removals')
    if any(not row['removed'] for row in removal_rows):
        reasons.append('declared_removal_not_observed')
    if any(not row['added'] for row in addition_rows):
        reasons.append('expected_addition_missing')
    if not rows and not removal_rows and not addition_rows:
        reasons.append('no_selected_literal_facts')
    return {'status': 'needs_review' if reasons else 'complete', 'review_reasons': reasons,
            'original_sha256': hashlib.sha256(original.encode('utf-8')).hexdigest(),
            'candidate_sha256': hashlib.sha256(candidate.encode('utf-8')).hexdigest(),
            'facts_checked': len(rows), 'facts_preserved': sum(row['preserved'] for row in rows),
            'unexpected_removals': unexpected, 'declared_removals': removal_rows,
            'expected_additions': addition_rows,
            'semantic_authority': 'advisory_unverified',
            'name_scope': 'structured labels and literal wiki targets with conservative Unicode word boundaries; attached Korean particles may require separate labels or explicit host claims; no linguistic NER',
            'authority': 'preservation_observation_only_not_permission_or_truth',
            'scope': ('stored original against stored candidate, body only (frontmatter and a leading BOM are '
                      'excluded); occurrence counts of date/number tokens (check_compile extraction) and of name '
                      'labels, literal wiki targets, declared claims and declared phrases (word boundaries); '
                      'declarations with date/number tokens are judged by those tokens; paraphrases and attached '
                      'particles show as missing, and a sentence rewritten with the same numbers and links is '
                      'caught only when it is declared as a claim')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    parser.add_argument('--report-unsupported', action='store_true',
                        help='also list target literals found in no selected source (observation only)')
    args = parser.parse_args(argv)
    try:
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise QualityError('input_byte_limit')
        request = decode_json(data)
        if not isinstance(request, dict) or set(request) != {'sources', 'targets', 'required_claims'}:
            raise QualityError('invalid_request')
        result = check_compile(args.vault, request['sources'], request['targets'], request['required_claims'],
                               report_unsupported=args.report_unsupported)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] == 'complete' else 1
    except (QualityError, ProposalError, ValueError, OSError, RuntimeError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, QualityError) else 'invalid_input'
        print(json.dumps({'status': 'error', 'error': code, 'authority': 'none'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
