#!/usr/bin/env python3
"""Hash-bound warning promotion with reproducible bundled zero-FP replay.

Config shape: warning_policy = {levels: {code: warning|fatal}, certificate: {...}}.
Absent policy stays warning-only. A certificate is evidence of this precise
checker and benign corpus, not identity, source trust, or permission to write.
Certificates are replayed locally; self-asserted counts cannot enable escalation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from vault_lint_extensions import PROMOTABLE_CODES, analyze_notes

RESOURCE = Path(__file__).resolve().parent / 'resources' / 'lint-benign-v1.json'
MAX_CORPUS_BYTES = 2_000_000


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def checker_sha256() -> str:
    """Bind analyzer and validator bytes; installed copies compute their own hash."""
    digest = hashlib.sha256()
    for filename in ('vault_lint_extensions.py', 'vault_warning_policy.py'):
        digest.update(filename.encode('utf-8') + b'\0')
        digest.update((Path(__file__).resolve().parent / filename).read_bytes())
    return digest.hexdigest()


def corpus_sha256() -> str:
    if RESOURCE.stat().st_size > MAX_CORPUS_BYTES:
        raise ValueError('bundled harmless corpus exceeds byte limit')
    return hashlib.sha256(RESOURCE.read_bytes()).hexdigest()


def build_promotion_certificate() -> dict:
    """Replay the fixed installed corpus and record exact deterministic results."""
    corpus_hash = corpus_sha256()
    corpus = json.loads(RESOURCE.read_text(encoding='utf-8'))
    cases = corpus.get('cases')
    if corpus.get('schema_version') != 1 or not isinstance(cases, list) or not 8 <= len(cases) <= 128:
        raise ValueError('invalid bundled harmless corpus schema or case count')
    results = []
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or case['id'] in seen:
            raise ValueError('invalid or duplicate harmless corpus case')
        seen.add(case['id'])
        findings = analyze_notes(case['notes'], case.get('config', {}))
        results.append({'id': case['id'], 'findings': findings})
    count = sum(len(x['findings']) for x in results)
    return {'schema_version': 1, 'checker_sha256': checker_sha256(),
            'corpus_sha256': corpus_hash, 'case_count': len(cases),
            'false_positives': count, 'result_sha256': hashlib.sha256(_json_bytes(results)).hexdigest(),
            'certified_codes': sorted(PROMOTABLE_CODES)}


def validate_promotion(policy, checker_hash: str, corpus_hash: str) -> dict:
    """Return valid/promoted_codes/errors; escalation always fails closed.

    Caller hashes must equal current installed bytes, and certificate fields must
    equal a fresh replay. Incomplete or nonzero harmless results cannot promote.
    No supplied counts, severity flag or verified_by label is trusted in isolation.
    """
    errors = []
    promoted = []
    if not isinstance(policy, dict):
        errors.append('warning_policy must be an object')
    else:
        levels = policy.get('levels', {})
        if not isinstance(levels, dict):
            errors.append('warning_policy.levels must be an object')
        else:
            for code, severity in sorted(levels.items()):
                if code not in PROMOTABLE_CODES or severity not in ('warning', 'fatal'):
                    errors.append('unknown warning code or severity')
                elif severity == 'fatal':
                    promoted.append(code)
    if promoted and not errors:
        try:
            replay = build_promotion_certificate()
            if checker_hash != replay['checker_sha256'] or corpus_hash != replay['corpus_sha256']:
                errors.append('caller checker/corpus hashes do not match current installed resources')
            certificate = policy.get('certificate')
            if not isinstance(certificate, dict):
                errors.append('explicit fatal promotion requires a zero-FP certificate')
            else:
                for field, actual in replay.items():
                    claimed = certificate.get(field)
                    if type(claimed) is not type(actual) or claimed != actual:
                        errors.append(f'promotion certificate {field} differs from current harmless replay')
            if replay['false_positives'] != 0:
                errors.append('current harmless corpus has nonzero false positives')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'promotion evidence unavailable: {type(exc).__name__}')
    return {'valid': not errors, 'promoted_codes': sorted(promoted) if not errors else [],
            'errors': errors}


def main() -> int:
    parser = argparse.ArgumentParser(description='Replay installed harmless lint corpus and emit promotion evidence')
    parser.add_argument('--certificate', action='store_true', required=True)
    parser.parse_args()
    try:
        certificate = build_promotion_certificate()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'valid': False, 'error': str(exc)}))
        return 1
    print(json.dumps(certificate, ensure_ascii=False, indent=2))
    return 0 if certificate['false_positives'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
