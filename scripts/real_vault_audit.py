#!/usr/bin/env python3
"""Draw a seeded audit sample from a real-vault population and bound the misses from its labels.

    python scripts/real_vault_audit.py plan --population-size 500 --target-share 0.05 --oc 5,25,26
    python scripts/real_vault_audit.py draw --population POPULATION.json --k 20 --out SAMPLE.json
    python scripts/real_vault_audit.py bound --sample SAMPLE.json --labels LABELS.json --population POPULATION.json

A repository evaluation tool like flaky_repeat.py: not a plugin resource, never
run by hooks, CI gates or default runs. It makes no network or model calls and
never opens the vault; it reads only the JSON files it is given.

draw reads a population of item references and writes a sample file. A
population is either {"population_id": ..., "items": [{"id", "path", "line",
"match"}], "source_sha256"?} or a saved `vault_ssot.py check-support
--normalize-periods` JSON, whose rows with normalized_match none become items
(id "path:line"). Items may carry only those four fields, but the id (up to
1,040 characters) and population_id (up to 200) are copied into the sample as
given, so ids should be references such as "path:line" to keep note text out
of the sample. A path is a vault-relative POSIX path, so one note has one
spelling: backslashes, the characters :*?"<>|, and empty, '.' or '..'
segments or segments ending in a dot or space are refused (the goldset path
rule of evaluate_recall.py without its .md suffix). Each item is ranked by the
lowercase SHA-256 hex of the UTF-8 text '<seed>:<canonical item JSON>' (ties
by id) and the first k form the sample: a uniform draw without replacement
when the seed is random, and the same draw on every Python version. Without
--seed the seed comes from secrets.token_hex(16), which a real audit should
use: --seed reproduces a recorded draw, and a seed a person picks, or tries
several of, is not random. The sample file records the seed, N, k, the
confidence that bound will use (--confidence, default 0.95), the population
digest, source_sha256 when present and the sampled references in rank order,
never labels, and is never overwritten. draw also prints sample_sha256, the SHA-256
of the canonical sample JSON, to note down before labelling.

bound reads the sample and a labels file {"labels": {"<id>": "miss" | "ok"}}
that labels every sampled id exactly once. x is the number of misses and the
bound is eval_stats.hypergeometric_upper_bound(x, k, N) at the confidence the
sample records: the largest number of misses K among the N items with
P(X <= x | N, K, k) >= alpha. A --confidence that differs from the recorded
one is refused, so the level is never chosen after the labels are seen. With
--population the population digest and the draw are recomputed and must match
the sample; without it the sample is trusted as written and
population_verified is false. The report carries the sample_sha256 of the
sample as read, which equals the value draw printed only while the sample is
unchanged (the recomputed draw does not cover k or the confidence). With
--flagged-true-positives T, the exact count of true positives
from labelling the whole flagged set, it adds lower_recall = T / (T + K) and
upper_missed_share = K / (T + K) at the bound K. The output carries only counts,
the seed and digests: no ids, labels or note text. It is an input for human
judgement, not a gate. Exit code 0 on success whatever x is, 2 for usage
errors, which are reported as content-free codes.

plan is arithmetic for the step before draw and takes no input file. For N
items (--population-size, the N that draw will report) and a target share P
(--target-share) it reads P from its decimal text, as the confidence is read
(Fraction(repr(P))), so target_misses = floor(P * N) and ltpd_misses =
ceil(P * N) are exact integers; a float product moves them (0.29 * 100
floors to 28). sample_size is the smallest k from max(x, 1) to min(N, 5000)
at which x or fewer misses (--expected-misses x, default 0) give bound an
upper_misses of at most target_misses. The bound never rises with k, so a
bisection finds it, and at that k exactly the samples with x or fewer misses
pass. strict_ltpd_sample_size is the smallest k with
P(X <= x | N, ltpd_misses, k) <= alpha: the consumer's risk at the LTPD as
the NIST/SEMATECH e-Handbook of Statistical Methods (section 6.2.2) defines
it. bound instead passes when P(X <= x | N, target_misses + 1, k) < alpha,
so the two sizes can differ. A size without such a k is null with a reason
code. --oc K1,K2,... adds the exact acceptance probability
P(X <= x | N, K, sample_size) for each K, printed as a float. The output
carries the numbers, a fixed basis, warnings and the authority, never ids,
paths or labels. The exit code is 0 for valid arguments, a null size
included, and 2 for usage errors.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
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
MAX_OC_POINTS = 100
ITEM_FIELDS = ('id', 'path', 'line', 'match')
LABELS = ('miss', 'ok')
GENERIC_FIELDS = ('population_id', 'items', 'source_sha256')
SAMPLE_FIELDS = ('tool', 'kind', 'version', 'population_id', 'population_kind', 'population_sha256',
                 'seed', 'N', 'k', 'confidence', 'draw_method', 'items')
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
PLAN_BASIS = ('target_misses = floor(target_share * N) and ltpd_misses = ceil(target_share * N), from the decimal '
              'text of target_share; sample_size: the smallest k from max(expected_misses, 1) to min(N, %d) at '
              'which expected_misses or fewer misses give bound an upper_misses of at most target_misses; '
              'strict_ltpd_sample_size: the smallest k in that range with P(X <= expected_misses | N, ltpd_misses, '
              "k) <= alpha, the consumer's risk at the LTPD (NIST/SEMATECH e-Handbook of Statistical Methods, 6.2.2); "
              'exact hypergeometric arithmetic, not the binomial approximation for lots much larger than the sample'
              % MAX_SAMPLE)
OC_BASIS = ('acceptance_probability: P(X <= expected_misses | N, K, sample_size) for X ~ Hypergeometric(N, K, '
            'sample_size), computed exactly and printed as a float; at sample_size it is the probability that bound '
            'gives upper_misses <= target_misses when K of the N items are misses')
PLAN_WARNINGS = (
    'a planned size is not a guarantee: only the labels of the drawn sample give a bound',
    'it assumes a uniform random draw, complete and correct labels and a population fixed before the draw',
    'fix the seed, k and confidence before labelling; do not redraw or change k after seeing labels',
    'the sample file records neither target_share nor expected_misses: keep this output with the sample_sha256 '
    'that draw prints in the work record',
    'check that N here equals the N in the draw summary',
)
SEED_RE = re.compile(r'[0-9a-f]{1,128}')
SHA256_RE = re.compile(r'[0-9a-f]{64}')
MATCH_RE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
# One --oc entry: a decimal miss count, spaces around it allowed.
OC_POINT_RE = re.compile(r' *([0-9]{1,9}) *')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')
# References are vault-relative POSIX paths, as vault_ssot.py writes them. A rooted, UNC or drive path would
# put local directory names in a sample, and a backslash, '.' or '..' would give one note two spellings and
# two digests. evaluate_recall.py refuses the same for goldset paths.
_PATH_CHARS_REFUSED = '\\:*?"<>|'


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


def _alpha(stats, confidence, code: str):
    """1 - confidence as an exact fraction; eval_stats.exact_alpha decides which levels are valid."""
    try:
        return stats.exact_alpha(confidence)
    except ValueError as exc:
        raise UsageError(code) from exc


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
    if any(char in path for char in _PATH_CHARS_REFUSED) or any(
            part in ('', '.', '..') or part.rstrip(' .') != part for part in path.split('/')):
        raise UsageError(code)
    return path


def _line(value, code: str) -> int:
    if type(value) is not int or not 1 <= value <= MAX_LINE:
        raise UsageError(code)
    return value


def item_reference(raw) -> dict:
    """An item reduced to its references; any other field is refused, and the id is kept as given."""
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


def sample_digest(sample: dict) -> str:
    """SHA-256 of the canonical sample JSON, so line endings, indentation and encoding do not change it."""
    return _sha256(canonical(sample))


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
    _alpha(_load_eval_stats(), args.confidence, 'invalid_confidence')
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
    # The confidence is fixed here with the seed and k, before any label exists; bound refuses another level.
    sample.update({'seed': seed, 'N': size, 'k': args.k, 'confidence': args.confidence, 'draw_method': DRAW_METHOD,
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
               'N': size, 'k': args.k, 'confidence': args.confidence, 'seed': seed,
               'population_sha256': sample['population_sha256'], 'sample_sha256': sample_digest(sample)}
    if 'source_sha256' in sample:
        summary['source_sha256'] = sample['source_sha256']
    return summary


def bound(args) -> dict:
    stats = _load_eval_stats()
    if args.confidence is not None:
        _alpha(stats, args.confidence, 'invalid_confidence')
    true_positives = args.flagged_true_positives
    if true_positives is not None and not 0 <= true_positives <= MAX_TRUE_POSITIVES:
        raise UsageError('invalid_true_positives')
    sample = _sample_from(read_json(args.sample, MAX_SAMPLE_BYTES, 'sample'))
    confidence = sample['confidence']
    alpha = _alpha(stats, confidence, 'invalid_sample')
    # The level was fixed by draw before labelling; choosing another one after seeing x voids the bound.
    if args.confidence is not None and args.confidence != confidence:
        raise UsageError('confidence_mismatch')
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
    result = stats.hypergeometric_upper_bound(misses, sample['k'], sample['N'], confidence=confidence)
    upper = result['upper_misses']
    report = {'tool': TOOL, 'command': 'bound', 'version': VERSION,
              'N': sample['N'], 'k': sample['k'], 'x': misses,
              'confidence': confidence, 'alpha': float(alpha), 'seed': sample['seed'],
              'sample_sha256': sample_digest(sample),
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


def _target_share(value) -> Fraction:
    """The share as an exact fraction of its decimal text, as eval_stats.exact_alpha reads a confidence.

    A float product decides boundaries wrongly: 0.29 * 100 is 28.999999999999996
    and 0.07 * 100 is 7.000000000000001, so floor and ceil would give 28 and 8.
    """
    if type(value) is not float or not 0 < value < 1:
        raise UsageError('invalid_target_share')
    return Fraction(repr(value))


def _oc_points(text: str, size: int) -> list:
    """Up to MAX_OC_POINTS comma-separated miss counts K from 0 to N, in the order given."""
    parts = text.split(',')
    matches = [OC_POINT_RE.fullmatch(part) for part in parts]
    if len(parts) > MAX_OC_POINTS or not all(matches):
        raise UsageError('invalid_oc')
    points = [int(match.group(1)) for match in matches]
    if any(point > size for point in points):
        raise UsageError('invalid_oc')
    return points


def _smallest_size(passes, low: int, high: int):
    """The smallest k in [low, high] with passes(k), for a test that stays true once true; None if none passes."""
    if low > high or not passes(high):
        return None
    while low < high:
        middle = (low + high) // 2
        if passes(middle):
            high = middle
        else:
            low = middle + 1
    return low


def plan(args) -> dict:
    stats = _load_eval_stats()
    alpha = _alpha(stats, args.confidence, 'invalid_confidence')
    size = args.population_size
    if not 1 <= size <= MAX_POPULATION_ITEMS:
        raise UsageError('invalid_population_size')
    share = _target_share(args.target_share)
    misses = args.expected_misses
    if not 0 <= misses <= size:
        raise UsageError('invalid_expected_misses')
    points = None if args.oc is None else _oc_points(args.oc, size)
    # Integers from the exact fraction: no float product decides a boundary.
    target, ltpd = math.floor(share * size), math.ceil(share * size)
    low, high = max(misses, 1), min(size, MAX_SAMPLE)

    def bounded(k):
        # bound's own test. Fewer misses never give a larger bound, so every x or fewer passes with x.
        return stats.hypergeometric_upper_bound(misses, k, size, confidence=args.confidence)['upper_misses'] <= target

    def strict(k):
        # P(X <= x | N, ltpd, k) <= alpha in exact integers, compared as hypergeometric_upper_bound compares;
        # _hypergeometric_cdf_numerator is the exact sum eval_stats uses for the bound itself.
        return (stats._hypergeometric_cdf_numerator(misses, k, size, ltpd) * alpha.denominator
                <= alpha.numerator * math.comb(size, k))

    # Both tests stay true once true as k grows: X can only rise when an item is added to the sample.
    sample_size = _smallest_size(bounded, low, high) if misses <= target else None
    strict_size = _smallest_size(strict, low, high) if misses < ltpd else None
    report = {'tool': TOOL, 'command': 'plan', 'version': VERSION, 'N': size,
              'target_share': args.target_share, 'target_misses': target, 'ltpd_misses': ltpd,
              'expected_misses': misses, 'confidence': args.confidence, 'alpha': float(alpha),
              'sample_size': sample_size,
              'sample_size_reason': None if sample_size is not None else (
                  'expected_misses_exceed_target' if misses > target else 'exceeds_draw_maximum'),
              'strict_ltpd_sample_size': strict_size,
              'strict_ltpd_sample_size_reason': None if strict_size is not None else (
                  'expected_misses_reach_ltpd' if misses >= ltpd else 'exceeds_draw_maximum'),
              'basis': PLAN_BASIS, 'warnings': list(PLAN_WARNINGS), 'authority': AUTHORITY}
    if points is not None:
        report['oc_basis'] = OC_BASIS
        report['oc'] = None
        if sample_size is not None:
            # The exact fractions run to hundreds of digits, so only their nearest floats are printed.
            total = math.comb(size, sample_size)
            report['oc'] = [{'K': point, 'acceptance_probability': float(Fraction(
                stats._hypergeometric_cdf_numerator(misses, sample_size, size, point), total))} for point in points]
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
    sampler.add_argument('--seed', help='hex seed that reproduces a recorded draw; leave it out for a real audit '
                                        '(default: a fresh secrets.token_hex(16))')
    sampler.add_argument('--confidence', type=float, default=CONFIDENCE,
                         help='one-sided confidence that bound will use, strictly between 0.5 and 1 '
                              '(default %.2f); recorded in the sample before labelling' % CONFIDENCE)
    sampler.add_argument('--out', required=True, help='new sample file; an existing file is never replaced')
    bounder = commands.add_parser('bound', help='exact upper bound on the misses from a fully labelled sample')
    bounder.add_argument('--sample', required=True, help='sample file written by draw')
    bounder.add_argument('--labels', required=True,
                         help='JSON {"labels": {"<id>": "miss" or "ok"}} covering every sampled id')
    bounder.add_argument('--population',
                         help='population file to recompute the population digest and the draw from; without it '
                              'the sample is trusted as written and population_verified is false')
    bounder.add_argument('--flagged-true-positives', type=int, metavar='T',
                         help='exact true positives from labelling the whole flagged set; adds recall bounds')
    bounder.add_argument('--confidence', type=float,
                         help='check only: must equal the confidence the sample records (draw --confidence)')
    planner = commands.add_parser('plan', help='sample size for a target miss share before any draw; no input file')
    planner.add_argument('--population-size', type=int, required=True, metavar='N',
                         help='number of items in the population (1-%d), the N that draw will report'
                              % MAX_POPULATION_ITEMS)
    planner.add_argument('--target-share', type=float, required=True, metavar='P',
                         help='largest miss share the bound should show (upper_misses <= floor(P * N)), strictly '
                              'between 0 and 1; read from its decimal text')
    planner.add_argument('--expected-misses', type=int, default=0, metavar='X',
                         help='misses the sample may contain and still pass (default 0)')
    planner.add_argument('--confidence', type=float, default=CONFIDENCE,
                         help='one-sided confidence, strictly between 0.5 and 1 (default %.2f); give draw the same '
                              'value' % CONFIDENCE)
    planner.add_argument('--oc', metavar='K1,K2,...',
                         help='up to %d comma-separated miss counts K from 0 to N; adds the exact acceptance '
                              'probability at sample_size for each' % MAX_OC_POINTS)
    return parser


def _fail(code):
    print(json.dumps({'ok': False, 'error': code}))
    return 2


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        report = {'draw': draw, 'bound': bound, 'plan': plan}[args.command](args)
    except UsageError as error:
        return _fail(str(error))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
