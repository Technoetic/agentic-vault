#!/usr/bin/env python3
"""Opt-in, model/generation-bound token estimates from bounded measurements.

Reported samples and a host-supplied counter are observations, not certified
provider measurements. The optional tiktoken adapter invokes the real tokenizer;
its encoding cache may need downloading on the caller's first opted-in call.
Default estimates remain unchanged. This module never selects a calibration
for hooks/retrievers or sends input to a provider implicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys

from jev_client import contains_sensitive
from vault_egress import scan_text
from vault_healthcheck import estimate_tokens

MAX_SAMPLES = 128
MAX_TEXT = 64 * 1024
MAX_COUNT = 1_000_000
MAX_INPUT = 256 * 1024
ORIGINS = frozenset({'reported_sample', 'local_tokenizer', 'host_counter'})
HASH_RE = re.compile(r'[0-9a-f]{64}')
HANGUL_RE = re.compile(r'[\uac00-\ud7a3]')
SAMPLE_KEYS = frozenset({'text_sha256', 'hangul_chars', 'other_chars', 'tokens',
                         'model_id', 'generation', 'measurement_origin'})


class CalibrationError(Exception):
    """Safe content-free diagnostic code."""


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}', value):
        raise CalibrationError('invalid_model_or_generation')
    return value


def _count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise CalibrationError('invalid_measurement_count')
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _samples(samples, model_id, generation):
    _label(model_id)
    _label(generation)
    if not isinstance(samples, list) or not 2 <= len(samples) <= MAX_SAMPLES:
        raise CalibrationError('invalid_sample_count')
    seen = set()
    clean = []
    for row in samples:
        if not isinstance(row, dict) or set(row) != SAMPLE_KEYS:
            raise CalibrationError('invalid_sample_schema')
        if row['model_id'] != model_id or row['generation'] != generation:
            raise CalibrationError('measurement_binding_mismatch')
        if not isinstance(row['measurement_origin'], str) or row['measurement_origin'] not in ORIGINS:
            raise CalibrationError('invalid_measurement_origin')
        digest = row['text_sha256']
        if not isinstance(digest, str) or not HASH_RE.fullmatch(digest) or digest in seen:
            raise CalibrationError('invalid_or_duplicate_measurement_hash')
        seen.add(digest)
        h, o, tokens = (_count(row[key]) for key in ('hangul_chars', 'other_chars', 'tokens'))
        if h + o == 0 or tokens == 0:
            raise CalibrationError('empty_measurement')
        clean.append(dict(row))
    return clean


def _errors(rows, coefficients):
    relative = [abs(coefficients['hangul'] * row['hangul_chars'] +
                    coefficients['other'] * row['other_chars'] - row['tokens']) / row['tokens']
                for row in rows]
    return {'mean_relative_error': sum(relative) / len(relative),
            'max_relative_error': max(relative), 'sample_count': len(rows)}


def calibrate(samples, model_id, generation):
    """Fit two character coefficients; sample metadata remains caller-reported."""
    rows = _samples(samples, model_id, generation)
    hh = sum(r['hangul_chars'] ** 2 for r in rows)
    oo = sum(r['other_chars'] ** 2 for r in rows)
    ho = sum(r['hangul_chars'] * r['other_chars'] for r in rows)
    ht = sum(r['hangul_chars'] * r['tokens'] for r in rows)
    ot = sum(r['other_chars'] * r['tokens'] for r in rows)
    determinant = hh * oo - ho * ho
    if determinant <= max(1, hh * oo) * 1e-12:
        raise CalibrationError('rank_deficient_measurements')
    coefficients = {'hangul': (ht * oo - ot * ho) / determinant,
                    'other': (ot * hh - ht * ho) / determinant}
    if any(not math.isfinite(c) or not 0 < c <= 16 for c in coefficients.values()):
        raise CalibrationError('implausible_coefficients')
    result = {'schema_version': 1, 'model_id': model_id, 'generation': generation,
              'coefficients': coefficients, 'samples_sha256': _digest(rows),
              'measurement_origins': sorted({r['measurement_origin'] for r in rows}),
              'authority': 'reported_measurements_not_independent_verification',
              'selection': 'explicit_only', **_errors(rows, coefficients)}
    result['calibration_sha256'] = _digest(result)
    return result


def _validated(calibration, model_id, generation):
    _label(model_id)
    _label(generation)
    if not isinstance(calibration, dict):
        raise CalibrationError('invalid_calibration')
    value = dict(calibration)
    digest = value.pop('calibration_sha256', None)
    try:
        if not isinstance(digest, str) or digest != _digest(value):
            raise CalibrationError('calibration_changed')
    except (ValueError, TypeError, OverflowError):
        raise CalibrationError('invalid_calibration') from None
    if value.get('schema_version') != 1 or value.get('model_id') != model_id or value.get('generation') != generation:
        raise CalibrationError('calibration_binding_mismatch')
    coefficients = value.get('coefficients')
    if not isinstance(coefficients, dict) or set(coefficients) != {'hangul', 'other'}:
        raise CalibrationError('invalid_coefficients')
    if any(type(c) not in (int, float) or not math.isfinite(c) or not 0 < c <= 16 for c in coefficients.values()):
        raise CalibrationError('invalid_coefficients')
    return coefficients


def _text(text):
    if not isinstance(text, str) or not text or len(text.encode('utf-8')) > MAX_TEXT:
        raise CalibrationError('invalid_or_oversized_text')
    return len(HANGUL_RE.findall(text)), len(text) - len(HANGUL_RE.findall(text))


def estimate(text, *, calibration=None, model_id=None, generation=None):
    _text(text)
    if calibration is None:
        return estimate_tokens(text)
    coefficients = _validated(calibration, model_id, generation)
    h, o = _text(text)
    return math.ceil(h * coefficients['hangul'] + o * coefficients['other'])


def check_drift(calibration, samples, *, threshold=.2):
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise CalibrationError('invalid_drift_threshold')
    if not isinstance(calibration, dict):
        raise CalibrationError('invalid_calibration')
    model, generation = calibration.get('model_id'), calibration.get('generation')
    coefficients = _validated(calibration, model, generation)
    result = _errors(_samples(samples, model, generation), coefficients)
    return {**result, 'drift': result['mean_relative_error'] > threshold,
            'threshold': threshold, 'model_id': model, 'generation': generation,
            'authority': 'reported_measurements_not_independent_verification'}


def _measurement(text, model_id, generation, count, origin):
    h, o = _text(text)
    _label(model_id)
    _label(generation)
    count = _count(count)
    if count == 0:
        raise CalibrationError('empty_measurement')
    return {'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'hangul_chars': h, 'other_chars': o, 'tokens': count,
            'model_id': model_id, 'generation': generation, 'measurement_origin': origin}


def measure_local(text, model_id, generation, *, encoding_name=None):
    _text(text)
    _label(model_id)
    _label(generation)
    try:
        import tiktoken
    except ImportError:
        raise CalibrationError('optional_tokenizer_unavailable') from None
    try:
        encoding = tiktoken.get_encoding(encoding_name) if encoding_name else tiktoken.encoding_for_model(model_id)
        count = len(encoding.encode(text, disallowed_special=()))
    except (KeyError, ValueError, OSError):
        raise CalibrationError('optional_tokenizer_failed') from None
    return _measurement(text, model_id, generation, count, 'local_tokenizer')


def measure_counter(text, model_id, generation, *, counter, allow_network=False):
    """Host adapter for an approved real count API; no endpoint/key guessing."""
    _text(text)
    _label(model_id)
    _label(generation)
    if allow_network is not True:
        raise CalibrationError('network_optin_required')
    # Preserve the complete shared D1 guard: credentials can span line breaks.
    # The egress inspection adds identifier/incomplete handling below.
    if contains_sensitive(text):
        raise CalibrationError('sensitive_input')
    inspection = scan_text(text)
    if any(row['code'] == 'egress-incomplete' for row in inspection):
        raise CalibrationError('inspection_incomplete')
    if inspection:
        raise CalibrationError('sensitive_input')
    if not callable(counter):
        raise CalibrationError('counter_unavailable')
    try:
        count = counter(model=model_id, text=text)
    except Exception:
        raise CalibrationError('counter_failed') from None
    return _measurement(text, model_id, generation, count, 'host_counter')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('calibrate', 'drift', 'estimate', 'measure-local'), required=True)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise CalibrationError('input_byte_limit')
        def reject_constant(value):
            raise CalibrationError('nonfinite_json')
        request = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(request, dict):
            raise CalibrationError('invalid_request')
        if args.action == 'calibrate':
            result = calibrate(**request)
        elif args.action == 'drift':
            result = check_drift(**request)
        elif args.action == 'measure-local':
            result = measure_local(**request)
        else:
            result = {'tokens': estimate(**request), 'selection': 'explicit_only' if request.get('calibration') else 'legacy_default'}
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (CalibrationError, ValueError, TypeError, OSError, RecursionError, UnicodeError):
        print(json.dumps({'status': 'unavailable_or_invalid', 'authority': 'none'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
