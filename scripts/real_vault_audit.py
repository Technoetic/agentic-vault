#!/usr/bin/env python3
"""Draw a seeded audit sample from a real-vault population and bound the misses from its labels.

    python scripts/real_vault_audit.py draw --population POPULATION.json --k 20 --out SAMPLE.json
    python scripts/real_vault_audit.py bound --sample SAMPLE.json --labels LABELS.json [--population POPULATION.json]

A repository evaluation tool like flaky_repeat.py: not a plugin resource, never
run by hooks, CI gates or default runs. It makes no network or model calls and
never opens the vault; it reads only the JSON files it is given.

draw reads a population of item references and writes a sample file. A
population is either {"population_id": ..., "items": [{"id", "path", "line",
"match"}], "source_sha256"?} or a saved `vault_ssot.py check-support
--normalize-periods` JSON, whose rows with normalized_match none become items
(id "path:line"). Items may carry only those four fields, so note text never
enters the sample. Each item is ranked by the lowercase SHA-256 hex of the
UTF-8 text '<seed>:<canonical item JSON>' (ties by id) and the first k form the
sample: a uniform draw without replacement when the seed is random, and the
same draw on every Python version. Without --seed the seed comes from
secrets.token_hex(16). The sample file records the seed, N, k, the population
digest, source_sha256 when present and the sampled references in rank order,
never labels, and is never overwritten.

bound reads the sample and a labels file {"labels": {"<id>": "miss" | "ok"}}
that labels every sampled id exactly once. x is the number of misses and the
bound is eval_stats.hypergeometric_upper_bound(x, k, N): the largest number of
misses K among the N items with P(X <= x | N, K, k) >= alpha. With
--population the population digest and the draw are recomputed and must match
the sample. With --flagged-true-positives T, the exact count of true positives
from labelling the whole flagged set, it adds lower_recall = T / (T + K) and
upper_missed_share = K / (T + K) at the bound K. The output carries only counts,
the seed and digests: no ids, labels or note text. It is an input for human
judgement, not a gate. Exit code 0 on success whatever x is, 2 for usage
errors, which are reported as content-free codes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat

ROOT = Path(__file__).resolve().parents[1]
EVAL_STATS_PATH = ROOT / 'scripts' / 'eval_stats.py'
TOOL = 'real_vault_audit'
VERSION = 1
CONFIDENCE = 0.95
MAX_POPULATION_BYTES = 64 * 1024 * 1024
MAX_SAMPLE_BYTES = 16 * 1024 * 1024
MAX_LABELS_BYTES = 16 * 1024 * 1024
MAX_POPULATION_ITEMS = 200_000
MAX_SAMPLE = 5_000
MAX_PATH_CHARS = 1024
# A check-support item id is 'path:line'.
MAX_ID_CHARS = MAX_PATH_CHARS + 16
MAX_POPULATION_ID_CHARS = 200
MAX_LINE = 2 ** 31 - 1
MAX_TRUE_POSITIVES = 10 ** 9
ITEM_FIELDS = ('id', 'path', 'line', 'match')
LABELS = ('miss', 'ok')
GENERIC_FIELDS = ('population_id', 'items', 'source_sha256')
SAMPLE_FIELDS = ('tool', 'kind', 'version', 'population_id', 'population_kind', 'population_sha256',
                 'seed', 'N', 'k', 'draw_method', 'items')
POPULATION_KINDS = ('generic', 'check_support_normalized_periods')
# vault_ssot check-support output: its method names the format, and every
# normalized_periods entry has one of these classes (vault_period.MATCHES + abstain).
CHECK_SUPPORT_METHOD = 'normalized_literal_presence_in_cited_note_body'
NORMALIZED_MATCHES = ('equal', 'contained', 'none', 'abstain')
CHECK_SUPPORT_SELECTION = 'none'
CHECK_SUPPORT_POPULATION_ID = 'check-support normalized_match=none'
DRAW_METHOD = ("rank items by the lowercase SHA-256 hex of the UTF-8 text '<seed>:<canonical item JSON>' "
               "(ties by id) and take the first k; a uniform draw without replacement when the seed is random")
AUTHORITY = 'human_judgement_input_not_gate'
TRUE_POSITIVES_BASIS = ('flagged_true_positives must be the exact count of true positives from labelling the '
                        'whole flagged set; an estimated count needs a joint interval, which is not given')
SEED_RE = re.compile(r'[0-9a-f]{1,128}')
SHA256_RE = re.compile(r'[0-9a-f]{64}')
MATCH_RE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')
# References are vault-relative: a rooted, UNC or drive path would put local directory names in a sample.
_ABSOLUTE_RE = re.compile(r'[/\\]|[A-Za-z]:')


class UsageError(Exception):
    """A content-free usage error code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: ARG002 - never echo the rejected value
        raise UsageError('invalid_arguments')


def _load_eval_stats():
    spec = importlib.util.spec_from_file_location('real_vault_audit_eval_stats', EVAL_STATS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value) -> str:
    """Compact JSON with sorted keys, as the evaluation scripts hash it (UTF-8, not ASCII-escaped)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('repeated key')
        result[key] = value
    return result


def _no_constant(name):
    raise ValueError('non-finite number')


def read_json(path, limit: int, label: str):
    """One regular JSON file of at most limit bytes.

    json.loads detects UTF-8 (with or without BOM), UTF-16 and UTF-32 from the
    bytes, so a check-support result saved by Windows PowerShell 5.1
    redirection also loads. Repeated keys and NaN or Infinity are refused.
    """
    try:
        metadata = os.stat(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise UsageError(label + '_unreadable')
        if metadata.st_size > limit:
            raise UsageError(label + '_too_large')
        with open(path, 'rb') as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise UsageError(label + '_unreadable') from exc
    if len(raw) > limit:
        raise UsageError(label + '_too_large')
    try:
        return json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise UsageError(label + '_invalid_json') from exc


def _text(value, limit: int, code: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit or _CONTROL_RE.search(value):
        raise UsageError(code)
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:  # a lone surrogate from a JSON escape
        raise UsageError(code) from exc
    return value


def _relative_path(value, code: str) -> str:
    path = _text(value, MAX_PATH_CHARS, code)
    if _ABSOLUTE_RE.match(path):
        raise UsageError(code)
    return path


def _line(value, code: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_LINE:
        raise UsageError(code)
    return value


def item_reference(raw) -> dict:
    """An item reduced to its references; any other field is refused, so content never enters a sample."""
    if not isinstance(raw, dict):
        raise UsageError('invalid_item')
    if not set(raw) <= set(ITEM_FIELDS):
        raise UsageError('item_field_not_allowed')
    if 'id' not in raw:
        raise UsageError('invalid_item')
    item = {'id': _text(raw['id'], MAX_ID_CHARS, 'invalid_item')}
    if 'path' in raw:
        item['path'] = _relative_path(raw['path'], 'invalid_item')
    if 'line' in raw:
        item['line'] = _line(raw['line'], 'invalid_item')
    if 'match' in raw:
        if not isinstance(raw['match'], str) or not MATCH_RE.fullmatch(raw['match']):
            raise UsageError('invalid_item')
        item['match'] = raw['match']
    return item


def _unique_ids(items) -> None:
    ids = [item['id'] for item in items]
    if len(set(ids)) != len(ids):
        raise UsageError('duplicate_item_id')


def _source_digest(value) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise UsageError('invalid_source_sha256')
    return value


def _generic_population(data: dict) -> dict:
    if not set(data) <= set(GENERIC_FIELDS):
        raise UsageError('population_field_not_allowed')
    population_id = _text(data['population_id'], MAX_POPULATION_ID_CHARS, 'invalid_population_id')
    items = data['items']
    if not isinstance(items, list):
        raise UsageError('invalid_population')
    if len(items) > MAX_POPULATION_ITEMS:
        raise UsageError('population_too_large')
    population = {'kind': 'generic', 'population_id': population_id,
                  'items': [item_reference(raw) for raw in items]}
    if 'source_sha256' in data:
        population['source_sha256'] = _source_digest(data['source_sha256'])
    return population


def _check_support_population(data: dict) -> dict:
    """Rows of one check-support result whose normalized_match is none; only path and lines are read."""
    if 'normalized_periods' not in data:
        raise UsageError('normalize_periods_missing')
    path = _relative_path(data.get('path'), 'invalid_check_support')
    entries = data['normalized_periods']
    if not isinstance(entries, list):
        raise UsageError('invalid_check_support')
    items = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('normalized_match') not in NORMALIZED_MATCHES:
            raise UsageError('invalid_check_support')
        line = _line(entry.get('line'), 'invalid_check_support')
        if entry['normalized_match'] == CHECK_SUPPORT_SELECTION:
            items.append(item_reference({'id': '%s:%d' % (path, line), 'path': path, 'line': line,
                                         'match': CHECK_SUPPORT_SELECTION}))
    if len(items) > MAX_POPULATION_ITEMS:
        raise UsageError('population_too_large')
    return {'kind': 'check_support_normalized_periods', 'population_id': CHECK_SUPPORT_POPULATION_ID,
            'items': items, 'source_sha256': _source_digest(data.get('source_sha256'))}


def population_from(data) -> dict:
    """{kind, population_id, items, source_sha256?} from a generic or check-support population file."""
    if isinstance(data, dict) and 'population_id' in data and 'items' in data:
        population = _generic_population(data)
    elif isinstance(data, dict) and data.get('method') == CHECK_SUPPORT_METHOD:
        population = _check_support_population(data)
    else:
        raise UsageError('unknown_population_format')
    if not population['items']:
        raise UsageError('empty_population')
    _unique_ids(population['items'])
    return population


def population_digest(population: dict) -> str:
    """SHA-256 of the canonical population: items in id order, so the file's item order does not matter."""
    body = {'population_id': population['population_id'],
            'items': sorted(population['items'], key=lambda item: item['id'])}
    if 'source_sha256' in population:
        body['source_sha256'] = population['source_sha256']
    return _sha256(canonical(body))


def draw_items(items, k: int, seed: str) -> list:
    """The k items with the smallest SHA-256 of '<seed>:<canonical item JSON>', ties by id."""
    ranked = sorted((_sha256(seed + ':' + canonical(item)), item['id'], index)
                    for index, item in enumerate(items))
    return [items[index] for _, _, index in ranked[:k]]


def _sample_from(data) -> dict:
    """A sample file as draw wrote it; labels or other added fields are refused."""
    if not isinstance(data, dict):
        raise UsageError('invalid_sample')
    keys = set(data)
    if not set(SAMPLE_FIELDS) <= keys or not keys <= set(SAMPLE_FIELDS) | {'source_sha256'}:
        raise UsageError('invalid_sample')
    if (data['tool'] != TOOL or data['kind'] != 'sample' or type(data['version']) is not int
            or data['version'] != VERSION or data['draw_method'] != DRAW_METHOD
            or data['population_kind'] not in POPULATION_KINDS):
        raise UsageError('invalid_sample')
    _text(data['population_id'], MAX_POPULATION_ID_CHARS, 'invalid_sample')
    if not isinstance(data['seed'], str) or not SEED_RE.fullmatch(data['seed']):
        raise UsageError('invalid_sample')
    if not isinstance(data['population_sha256'], str) or not SHA256_RE.fullmatch(data['population_sha256']):
        raise UsageError('invalid_sample')
    if 'source_sha256' in data:
        _source_digest(data['source_sha256'])
    population_size, sample_size, items = data['N'], data['k'], data['items']
    if (type(population_size) is not int or type(sample_size) is not int
            or not 1 <= population_size <= MAX_POPULATION_ITEMS
            or not 1 <= sample_size <= min(population_size, MAX_SAMPLE)
            or not isinstance(items, list) or len(items) != sample_size):
        raise UsageError('invalid_sample')
    references = [item_reference(raw) for raw in items]
    _unique_ids(references)
    return {**data, 'items': references}


def _labels_from(data, ids) -> int:
    """The number of misses; every sampled id is labelled exactly once, nothing else."""
    if not isinstance(data, dict) or set(data) != {'labels'} or not isinstance(data['labels'], dict):
        raise UsageError('invalid_labels')
    labels = data['labels']
    if any(value not in LABELS for value in labels.values()):
        raise UsageError('invalid_label_value')
    if any(item_id not in labels for item_id in ids):
        raise UsageError('labels_incomplete')
    if len(labels) != len(ids):
        raise UsageError('labels_extra')
    return sum(labels[item_id] == 'miss' for item_id in ids)


def draw(args) -> dict:
    if not 1 <= args.k <= MAX_SAMPLE:
        raise UsageError('invalid_sample_size')
    if args.seed is None:
        seed = secrets.token_hex(16)
    else:
        seed = args.seed.lower()
        if not SEED_RE.fullmatch(seed):
            raise UsageError('invalid_seed')
    if os.path.lexists(args.out):
        raise UsageError('output_exists')
    population = population_from(read_json(args.population, MAX_POPULATION_BYTES, 'population'))
    size = len(population['items'])
    if args.k > size:
        raise UsageError('sample_exceeds_population')
    sample = {'tool': TOOL, 'kind': 'sample', 'version': VERSION,
              'population_id': population['population_id'], 'population_kind': population['kind'],
              'population_sha256': population_digest(population)}
    if 'source_sha256' in population:
        sample['source_sha256'] = population['source_sha256']
    sample.update({'seed': seed, 'N': size, 'k': args.k, 'draw_method': DRAW_METHOD,
                   'items': draw_items(population['items'], args.k, seed)})
    text = json.dumps(sample, ensure_ascii=False, indent=2) + '\n'
    try:
        # Exclusive creation: a sample is the commitment made before labelling,
        # so an existing file is never replaced by a new draw.
        with open(args.out, 'x', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise UsageError('output_exists') from exc
    except OSError as exc:
        raise UsageError('output_unwritable') from exc
    summary = {'tool': TOOL, 'command': 'draw', 'version': VERSION, 'population_kind': population['kind'],
               'N': size, 'k': args.k, 'seed': seed, 'population_sha256': sample['population_sha256']}
    if 'source_sha256' in sample:
        summary['source_sha256'] = sample['source_sha256']
    return summary


def bound(args) -> dict:
    stats = _load_eval_stats()
    try:
        alpha = stats.exact_alpha(args.confidence)
    except ValueError as exc:
        raise UsageError('invalid_confidence') from exc
    true_positives = args.flagged_true_positives
    if true_positives is not None and not 0 <= true_positives <= MAX_TRUE_POSITIVES:
        raise UsageError('invalid_true_positives')
    sample = _sample_from(read_json(args.sample, MAX_SAMPLE_BYTES, 'sample'))
    ids = [item['id'] for item in sample['items']]
    misses = _labels_from(read_json(args.labels, MAX_LABELS_BYTES, 'labels'), ids)
    verified = False
    if args.population is not None:
        population = population_from(read_json(args.population, MAX_POPULATION_BYTES, 'population'))
        if population_digest(population) != sample['population_sha256'] or len(population['items']) != sample['N']:
            raise UsageError('population_mismatch')
        if draw_items(population['items'], sample['k'], sample['seed']) != sample['items']:
            raise UsageError('sample_mismatch')
        verified = True
    result = stats.hypergeometric_upper_bound(misses, sample['k'], sample['N'], confidence=args.confidence)
    upper = result['upper_misses']
    report = {'tool': TOOL, 'command': 'bound', 'version': VERSION,
              'N': sample['N'], 'k': sample['k'], 'x': misses,
              'confidence': args.confidence, 'alpha': float(alpha), 'seed': sample['seed'],
              'population_sha256': sample['population_sha256'], 'population_verified': verified,
              'upper_misses': upper, 'upper_miss_share': result['upper_miss_share'],
              'method': result['method'], 'authority': AUTHORITY}
    if 'source_sha256' in sample:
        report['source_sha256'] = sample['source_sha256']
    if true_positives is not None:
        # K / (T + K) rises with K, so the bound K gives the largest missed share and the lowest recall.
        total = true_positives + upper
        report.update(flagged_true_positives=true_positives,
                      lower_recall=true_positives / total if total else None,
                      upper_missed_share=upper / total if total else None,
                      flagged_true_positives_basis=TRUE_POSITIVES_BASIS)
    return report


def _parser():
    parser = _Parser(description=__doc__.split('\n\n')[0],
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    sampler = commands.add_parser('draw', help='draw a seeded uniform sample of item references')
    sampler.add_argument('--population', required=True,
                         help='generic population JSON or a saved check-support --normalize-periods result')
    sampler.add_argument('--k', type=int, required=True,
                         help='sample size, 1-%d and at most the population size' % MAX_SAMPLE)
    sampler.add_argument('--seed', help='hex seed (default: secrets.token_hex(16)); fix it before labelling')
    sampler.add_argument('--out', required=True, help='new sample file; an existing file is never replaced')
    bounder = commands.add_parser('bound', help='exact upper bound on the misses from a fully labelled sample')
    bounder.add_argument('--sample', required=True, help='sample file written by draw')
    bounder.add_argument('--labels', required=True,
                         help='JSON {"labels": {"<id>": "miss" or "ok"}} covering every sampled id')
    bounder.add_argument('--population',
                         help='population file to recompute the population digest and the draw from')
    bounder.add_argument('--flagged-true-positives', type=int, metavar='T',
                         help='exact true positives from labelling the whole flagged set; adds recall bounds')
    bounder.add_argument('--confidence', type=float, default=CONFIDENCE,
                         help='one-sided confidence, strictly between 0.5 and 1 (default %.2f)' % CONFIDENCE)
    return parser


def _fail(code):
    print(json.dumps({'ok': False, 'error': code}))
    return 2


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        report = draw(args) if args.command == 'draw' else bound(args)
    except UsageError as error:
        return _fail(str(error))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
