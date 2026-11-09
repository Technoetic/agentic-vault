#!/usr/bin/env python3
"""Paired offline observations against independently authored synthetic labels.

Both arms see the same immutable original/candidate pairs. The labels describe
selected-record preservation, not business truth or model answer correctness.
The deliberate literal-control arm must miss some changes in the bundled pack.
No vault, model, network, installation or note write is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from vault_compile_quality import QualityError, check_preservation
from vault_evidence import EvidenceError, decode, stable_read
from vault_relation_probes import RelationProbeError, validate_relation_probes

DEFAULT_PACK = ROOT / 'tests/fixtures/relation_preservation/pack.json'
MAX_PACK_BYTES = 128 * 1024
MAX_CASES = 64
IDENTIFIER = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
LIMITATIONS = ['authored_selected_record_labels_not_semantic_truth', 'no_model',
               'no_network', 'synthetic_pack_not_population_accuracy',
               'no_context_budget_optimization_or_performance_claim',
               'unselected_text_and_paraphrase_truth_not_verified']


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_pack(path=DEFAULT_PACK):
    """Read only one bounded, explicitly selected regular JSON fixture."""
    try:
        _, _, raw = stable_read(lambda: Path(path), MAX_PACK_BYTES, contents=True)
        pack = decode(raw)
    except (EvidenceError, OSError, UnicodeError, ValueError):
        raise ValueError('invalid_pack') from None
    if (not isinstance(pack, dict) or set(pack) != {'schema_version', 'synthetic', 'cases'}
            or type(pack['schema_version']) is not int or pack['schema_version'] != 1
            or pack['synthetic'] is not True or not isinstance(pack['cases'], list)
            or not 1 <= len(pack['cases']) <= MAX_CASES):
        raise ValueError('invalid_pack')
    seen = set()
    keys = {'id', 'category', 'original', 'candidate', 'probes', 'expected_preserved'}
    for row in pack['cases']:
        if (not isinstance(row, dict) or set(row) != keys
                or not isinstance(row['id'], str) or not IDENTIFIER.fullmatch(row['id'])
                or row['id'] in seen or not isinstance(row['category'], str)
                or not IDENTIFIER.fullmatch(row['category'])
                or type(row['expected_preserved']) is not bool
                or not isinstance(row['original'], str) or not isinstance(row['candidate'], str)):
            raise ValueError('invalid_case')
        seen.add(row['id'])
        if max(len(row[k].encode('utf-8')) for k in ('original', 'candidate')) > 32 * 1024:
            raise ValueError('case_text_limit')
        try:
            validate_relation_probes(row['probes'])
        except RelationProbeError:
            raise ValueError('invalid_case_probes') from None
    return pack, digest(raw)


def source_fingerprints():
    files = [SCRIPTS/'vault_compile_quality.py', SCRIPTS/'vault_relation_probes.py', Path(__file__)]
    return {p.name: digest(p.read_bytes()) for p in files}


def evaluate(path=DEFAULT_PACK, *, record_arm='record-probes'):
    if record_arm not in ('record-probes', 'literal-control'):
        raise ValueError('invalid_record_arm')
    pack, pack_sha = load_pack(path)
    sources = source_fingerprints()
    empty = {'correct_cases': 0, 'preserved_cases': 0, 'observed_changes': 0,
             'missed_changes': 0, 'false_change_alarms': 0}
    arms = {'literal': empty.copy(), 'record_probes': empty.copy()}
    paired = {key: 0 for key in ('both_match', 'literal_only_matches', 'record_only_matches', 'neither_matches')}
    rows = []
    for case in pack['cases']:
        try:
            literal = check_preservation(case['original'], case['candidate'])
            record = (check_preservation(case['original'], case['candidate'], relation_probes=case['probes'])
                      if record_arm == 'record-probes' else literal)
        except (QualityError, RelationProbeError):
            raise ValueError('invalid_case_source') from None
        expected = case['expected_preserved']
        observations = {'literal': literal['status'] == 'complete',
                        'record_probes': record['status'] == 'complete'}
        matches = {name: observed == expected for name, observed in observations.items()}
        for name, observed in observations.items():
            arms[name]['correct_cases'] += int(matches[name])
            key = ('preserved_cases' if observed else 'false_change_alarms') if expected else (
                'missed_changes' if observed else 'observed_changes')
            arms[name][key] += 1
        paired_key = ('both_match' if matches['literal'] and matches['record_probes'] else
                      'literal_only_matches' if matches['literal'] else
                      'record_only_matches' if matches['record_probes'] else 'neither_matches')
        paired[paired_key] += 1
        rows.append({'id': case['id'], 'category': case['category'], 'expected_preserved': expected,
                     'literal_predicted_preserved': observations['literal'],
                     'record_predicted_preserved': observations['record_probes'],
                     'literal_matches_label': matches['literal'], 'record_matches_label': matches['record_probes'],
                     'original_sha256': digest(case['original'].encode('utf-8')),
                     'candidate_sha256': digest(case['candidate'].encode('utf-8'))})
    if source_fingerprints() != sources or load_pack(path)[1] != pack_sha:
        raise ValueError('evaluation_input_changed')
    return {'schema_version': 1,
            'status': 'complete' if arms['record_probes']['correct_cases'] == len(rows) else 'needs_review',
            'authority': 'synthetic_fixture_observation_not_semantic_truth',
            'case_count': len(rows), 'pack_sha256': pack_sha, 'source_sha256': sources,
            'record_arm': record_arm, 'arms': arms, 'paired': paired, 'cases': rows,
            'limitations': LIMITATIONS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, default=DEFAULT_PACK)
    parser.add_argument('--record-arm', choices=('record-probes', 'literal-control'), default='record-probes')
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.pack, record_arm=args.record_arm)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] == 'complete' else 1
    except (ValueError, EvidenceError, OSError, UnicodeError):
        print(json.dumps({'status': 'error', 'error': 'invalid_evaluation_input', 'authority': 'none'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
