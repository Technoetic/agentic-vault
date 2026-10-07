#!/usr/bin/env python3
"""Revision-bound literal fact coverage before explicit source quarantine.

Only caller-selected Markdown files are read. Retrieval exclusions are not a
permission boundary for explicitly selected ingest inputs; deny zones, reserved
runtime/receipt paths, source hashes and portable path checks still apply.
This tool never moves sources, writes notes or makes semantic truth judgments.
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


def check_compile(vault, sources, targets, required_claims):
    """Compare bounded literals; hashes bind current originals and compiled notes."""
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
        # Numbers/dates use exact extracted tokens. Names use conservative
        # Unicode word boundaries: a longer contiguous name is different.
        # Attached Korean particles may need a separate structured label or
        # an explicit host claim; this is literal coverage, not linguistic NER.
        if kind == 'number':
            present = literal in target_numbers
        elif kind == 'date':
            present = literal in target_dates
        elif kind == 'name':
            pattern = re.compile(r'(?<!\w)' + re.escape(literal) + r'(?!\w)')
            present = any(pattern.search(body) is not None for body in target_texts)
        else:
            present = any(literal in body for body in target_texts)
        rows.append({'kind': kind, 'literal': literal, 'source_paths': sorted(paths), 'preserved': present})
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
    return {'status': 'needs_review' if missing or not rows else 'complete',
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise QualityError('input_byte_limit')
        request = decode_json(data)
        if not isinstance(request, dict) or set(request) != {'sources', 'targets', 'required_claims'}:
            raise QualityError('invalid_request')
        result = check_compile(args.vault, request['sources'], request['targets'], request['required_claims'])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] == 'complete' else 1
    except (QualityError, ProposalError, ValueError, OSError, RuntimeError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, QualityError) else 'invalid_input'
        print(json.dumps({'status': 'error', 'error': code, 'authority': 'none'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
