#!/usr/bin/env python3
"""Opt-in frozen execution receipts and advisory behavior comparisons.

The host executes scenarios and supplies observations; this module never calls
a model or applies a lesson. Hashes bind selected bytes, not truth, identity,
authorization or statistical effectiveness. Locks protect cooperating writers;
checksums detect ordinary corruption and are not signatures. Retain each run's
candidate and prompt files separately so their exact versions stay inspectable.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from functools import wraps
import os
from pathlib import Path
import re

import vault_evidence as evidence
import vault_state as state
from jev_client import _matches_sensitive, contains_sensitive
from vault_healthcheck import HealthcheckError
from vault_paths import relative_parts, resolve_note_path, zone_matches

RUNTIME_DIR = state.RUNTIME_DIR
MAX_JSON_BYTES = 64 * 1024
MAX_RECORD_BYTES = state.MAX_RUNTIME_BYTES
MAX_SOURCE_BYTES = 256 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_FILES = 64
MAX_CASES = 16
MAX_ASSERTIONS = 8
MAX_REPETITIONS = 10
HASH_RE = re.compile(r'[0-9a-f]{64}')
LABEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}')
CATEGORIES = frozenset({'representative', 'authority', 'ambiguity', 'tool_failure', 'injection'})
AUTHORITY = 'advisory_host_observations_only'


class RunError(Exception):
    """A content-free operational error code."""


def _public(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except RunError:
            raise
        except (state.StateError, evidence.EvidenceError, HealthcheckError, ValueError,
                OSError, UnicodeError, RecursionError, OverflowError, TypeError) as exc:
            raise RunError('unsafe_or_invalid_input') from exc
    return call


def _hash(value):
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise RunError('invalid_hash_or_id')
    return value


def _fields(value, fields, code):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise RunError(code)


def _text(value, *, label=False, maximum=4096):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 and c not in '\n\r\t' for c in value)
            or (label and not LABEL_RE.fullmatch(value))):
        raise RunError('invalid_text')
    if _matches_sensitive(value):
        raise RunError('sensitive_input')
    return value


def _safe_json(value):
    if contains_sensitive(value):
        raise RunError('sensitive_input')
    raw = evidence.encode(value)
    if len(raw) > MAX_JSON_BYTES:
        raise RunError('input_too_large')
    return raw


def _fixture(value):
    _safe_json(value)
    _fields(value, {'version', 'repetitions', 'cases'}, 'invalid_fixture')
    if (type(value['version']) is not int or value['version'] != 1
            or type(value['repetitions']) is not int
            or not 2 <= value['repetitions'] <= MAX_REPETITIONS
            or not isinstance(value['cases'], list) or not 5 <= len(value['cases']) <= MAX_CASES):
        raise RunError('invalid_fixture')
    cases, ids, categories = [], set(), set()
    for case in value['cases']:
        _fields(case, {'id', 'category', 'prompt', 'assertions'}, 'invalid_case')
        case_id = _text(case['id'], label=True)
        if case_id in ids or not isinstance(case['category'], str) or case['category'] not in CATEGORIES:
            raise RunError('invalid_or_duplicate_case')
        ids.add(case_id)
        categories.add(case['category'])
        _text(case['prompt'])
        checks = case['assertions']
        if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_ASSERTIONS:
            raise RunError('invalid_assertions')
        assertions, seen = [], set()
        for check in checks:
            _fields(check, {'id', 'description', 'hard'}, 'invalid_assertion')
            check_id = _text(check['id'], label=True)
            if check_id in seen or type(check['hard']) is not bool:
                raise RunError('invalid_or_duplicate_assertion')
            seen.add(check_id)
            _text(check['description'])
            assertions.append(dict(check))
        # Every scenario has a non-negotiable boundary assertion.
        if not any(check['hard'] for check in assertions):
            raise RunError('missing_hard_assertion')
        cases.append({**case, 'assertions': sorted(assertions, key=lambda c: c['id'])})
    if categories != CATEGORIES:
        raise RunError('missing_scenario_category')
    return {**value, 'cases': sorted(cases, key=lambda c: c['id'])}


def _tools(value):
    _safe_json(value)
    _fields(value, {'version', 'tools'}, 'invalid_tool_contracts')
    if (type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['tools'], list) or len(value['tools']) > MAX_FILES):
        raise RunError('invalid_tool_contracts')
    seen = set()
    for tool in value['tools']:
        _fields(tool, {'name', 'input_schema', 'output_schema'}, 'invalid_tool_contract')
        name = _text(tool['name'], label=True)
        if name in seen or not isinstance(tool['input_schema'], dict) or not isinstance(tool['output_schema'], dict):
            raise RunError('invalid_or_duplicate_tool')
        seen.add(name)
    return {**value, 'tools': sorted(value['tools'], key=lambda t: t['name'])}


class Store:
    def __init__(self, vault):
        self.policy = state._Policy(vault)
        self.vault = self.policy.vault
        self.root = self.policy.root

    def path(self, relative, *, markdown=False):
        _text(relative, maximum=1024)
        parts = relative_parts(relative, 'run input')
        if any(part.casefold() == '.env' or part.casefold().startswith('.env.') for part in parts):
            raise RunError('credential_file_forbidden')
        if any(zone_matches(parts, zone) for zone in
               (RUNTIME_DIR, evidence.STORE_DIR, '00-meta/proposals')):
            raise RunError('reserved_store_path')
        path = resolve_note_path(self.vault, relative, self.policy.rules)
        if markdown and path.suffix.casefold() != '.md':
            raise RunError('invalid_markdown_path')
        return path

    def fingerprint(self, relative, *, limit=MAX_SOURCE_BYTES, markdown=False):
        path = self.path(relative, markdown=markdown)
        canonical = path.relative_to(self.root).as_posix()
        info, _, raw = evidence.stable_read(
            lambda: self.path(canonical, markdown=markdown), limit, contents=True)
        # Only hashes are retained for user files. Still reject selected secrets
        # before recording paths or accepting the evidence as safe metadata.
        text = raw.decode('utf-8-sig')
        if _matches_sensitive(text):
            raise RunError('sensitive_input')
        return {'path': canonical, **info}, raw

    def json_file(self, relative):
        info, raw = self.fingerprint(relative, limit=MAX_JSON_BYTES)
        value = evidence.decode(raw)
        _safe_json(value)
        return info, value

    def capture(self, paths, *, limit=MAX_SOURCE_BYTES, markdown=False, used_bytes=0):
        files, seen = [], set()
        for path in paths:
            info, _ = self.fingerprint(path, limit=limit, markdown=markdown)
            if info['path'].casefold() in seen:
                raise RunError('duplicate_file_binding')
            seen.add(info['path'].casefold())
            used_bytes += info['size']
            if used_bytes > MAX_TOTAL_BYTES:
                raise RunError('file_set_too_large')
            files.append(info)
        return files

    def record_path(self, name):
        return self.policy.runtime(name)

    def read_record(self, name):
        _, _, raw = evidence.stable_read(lambda: self.record_path(name), MAX_RECORD_BYTES, contents=True)
        value = evidence.decode(raw)
        _fields(value, {'version', 'kind', 'id', 'created_at', 'payload', 'receipt_sha256'}, 'invalid_receipt')
        if type(value['version']) is not int or value['version'] != 1:
            raise RunError('invalid_receipt')
        _hash(value['id'])
        _hash(value['receipt_sha256'])
        try:
            parsed = datetime.fromisoformat(value['created_at'])
            if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise RunError('invalid_receipt')
        if value['receipt_sha256'] != evidence.digest(evidence.encode({
                k: v for k, v in value.items() if k != 'receipt_sha256'})):
            raise RunError('receipt_checksum_mismatch')
        return value

    def publish(self, name, kind, record_id, payload, recheck, lease):
        value = dict(version=1, kind=kind, id=record_id, created_at=evidence.timestamp(), payload=payload)
        value['receipt_sha256'] = evidence.digest(evidence.encode(value))
        raw = evidence.encode(value) + b'\n'
        if len(raw) > MAX_RECORD_BYTES:
            raise RunError('receipt_too_large')
        path = self.record_path(name)
        def replace(temporary):
            with state._lease_guard(self.policy, lease):
                current = self.policy.fresh()
                state._check_lease(current, lease)
                recheck()
                checked = current.runtime(name)
                if checked.exists():
                    raise RunError('immutable_receipt_exists')
                evidence.regular(temporary.lstat())
                self.policy.fresh()
                os.replace(temporary, checked)
        state._atomic(path, raw, replace, cleanup_guard=lambda temporary:
            state._cleanup_temporary(self.policy, name, temporary, runtime=True))
        return value


def _fingerprint_record(store, value, *, limit=MAX_SOURCE_BYTES, markdown=False):
    _fields(value, {'path', 'sha256', 'size'}, 'invalid_file_binding')
    _hash(value['sha256'])
    if type(value['size']) is not int or not 0 <= value['size'] <= limit:
        raise RunError('invalid_file_binding')
    canonical = store.path(value['path'], markdown=markdown).relative_to(store.root).as_posix()
    if canonical != value['path']:
        raise RunError('noncanonical_path')


def _current_files(store, files, *, limit=MAX_SOURCE_BYTES):
    total = 0
    seen = set()
    for value in files:
        _fingerprint_record(store, value, limit=limit)
        if value['path'].casefold() in seen:
            raise RunError('duplicate_file_binding')
        seen.add(value['path'].casefold())
        total += value['size']
        if total > MAX_TOTAL_BYTES:
            raise RunError('file_set_too_large')
        actual, _ = store.fingerprint(value['path'], limit=limit)
        if actual != value:
            raise RunError('stale_source')


def _compatibility(manifest):
    # Candidate and selected instruction/prompt sources are the treatment.
    # They remain bound in each manifest but deliberately differ across arms.
    return {key: manifest[key] for key in ('fixture', 'tools', 'model', 'provider',
                                           'token_budget', 'config_sha256')}


def _manifest(store, value):
    _fields(value, {'candidate', 'candidate_version', 'sources', 'fixture_source',
                   'tools_source', 'fixture', 'tools', 'model', 'provider',
                   'token_budget', 'config_sha256'}, 'invalid_manifest')
    _text(value['candidate_version'], label=True)
    _text(value['model'], label=True)
    _text(value['provider'], label=True)
    if type(value['token_budget']) is not int or not 1 <= value['token_budget'] <= 1000000:
        raise RunError('invalid_token_budget')
    _hash(value['config_sha256'])
    if value['config_sha256'] != store.policy.config_sha256:
        raise RunError('configuration_changed')
    _fingerprint_record(store, value['candidate'], markdown=True)
    if not isinstance(value['sources'], list) or not 1 <= len(value['sources']) <= MAX_FILES:
        raise RunError('invalid_sources')
    for source in value['sources']:
        _fingerprint_record(store, source, markdown=True)
    for key in ('fixture_source', 'tools_source'):
        _fingerprint_record(store, value[key], limit=MAX_JSON_BYTES)
    if value['fixture'] != _fixture(value['fixture']) or value['tools'] != _tools(value['tools']):
        raise RunError('noncanonical_definition')
    _current_files(store, [value['candidate'], *value['sources'], value['fixture_source'], value['tools_source']])
    # A file hash and its parsed scenario/contracts must agree; a recomputed
    # checksum cannot silently change the rubric while keeping a source binding.
    _, fixture = store.json_file(value['fixture_source']['path'])
    _, tools = store.json_file(value['tools_source']['path'])
    if _fixture(fixture) != value['fixture'] or _tools(tools) != value['tools']:
        raise RunError('definition_binding_mismatch')
    store.policy.fresh()


def _names(run_id):
    _hash(run_id)
    return ('run-' + run_id + '-prepared.json', 'run-' + run_id + '-completed.json')


def _load_prepared(store, run_id):
    name, _ = _names(run_id)
    receipt = store.read_record(name)
    if receipt['kind'] != 'prepared' or receipt['id'] != run_id:
        raise RunError('invalid_prepared_receipt')
    manifest = receipt['payload']
    if evidence.digest(evidence.encode(manifest)) != run_id:
        raise RunError('manifest_identity_mismatch')
    _manifest(store, manifest)
    return receipt


def _outcomes(store, report, manifest, run_id):
    _safe_json(report)
    _fields(report, {'version', 'run_id', 'manifest_sha256', 'outcomes'}, 'invalid_report')
    if (type(report['version']) is not int or report['version'] != 1
            or report['run_id'] != run_id or report['manifest_sha256'] != run_id):
        raise RunError('report_binding_mismatch')
    cases = {case['id']: case for case in manifest['fixture']['cases']}
    repetitions = manifest['fixture']['repetitions']
    outcomes = report['outcomes']
    if not isinstance(outcomes, list) or len(outcomes) != len(cases) * repetitions:
        raise RunError('incomplete_coverage')
    seen, refs, normalized = set(), set(), []
    for outcome in outcomes:
        _fields(outcome, {'case_id', 'repetition', 'status', 'assertions', 'evidence'}, 'invalid_outcome')
        case_id, repetition = outcome['case_id'], outcome['repetition']
        if (not isinstance(case_id, str) or case_id not in cases or type(repetition) is not int
                or not 1 <= repetition <= repetitions or (case_id, repetition) in seen
                or not isinstance(outcome['status'], str) or outcome['status'] not in ('complete', 'error')):
            raise RunError('invalid_or_duplicate_outcome')
        seen.add((case_id, repetition))
        checks = outcome['assertions']
        expected = {check['id'] for check in cases[case_id]['assertions']}
        if not isinstance(checks, list) or len(checks) != len(expected):
            raise RunError('incomplete_assertions')
        actual = set()
        for check in checks:
            _fields(check, {'id', 'passed'}, 'invalid_observed_assertion')
            if (not isinstance(check['id'], str) or check['id'] not in expected
                    or check['id'] in actual or type(check['passed']) is not bool):
                raise RunError('invalid_observed_assertion')
            actual.add(check['id'])
        paths = outcome['evidence']
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
            raise RunError('invalid_evidence_count')
        canonical = [store.path(path).relative_to(store.root).as_posix() for path in paths]
        if len({path.casefold() for path in canonical}) != len(canonical):
            raise RunError('duplicate_evidence')
        refs.update(canonical)
        if len(refs) > MAX_FILES:
            raise RunError('invalid_evidence_count')
        normalized.append({**outcome, 'assertions': sorted(checks, key=lambda c: c['id']),
                           'evidence': sorted(canonical)})
    return sorted(normalized, key=lambda o: (o['case_id'], o['repetition'])), sorted(refs)


def _load_completion(store, prepared):
    run_id = prepared['id']
    _, name = _names(run_id)
    if not store.record_path(name).exists():
        return None
    receipt = store.read_record(name)
    if receipt['kind'] != 'completed' or receipt['id'] != run_id:
        raise RunError('invalid_completion_receipt')
    payload = receipt['payload']
    _fields(payload, {'manifest_sha256', 'prepared_receipt_sha256', 'report_source',
                      'outcomes', 'evidence'}, 'invalid_completion')
    if (payload['manifest_sha256'] != run_id
            or payload['prepared_receipt_sha256'] != prepared['receipt_sha256']):
        raise RunError('completion_binding_mismatch')
    _fingerprint_record(store, payload['report_source'], limit=MAX_JSON_BYTES)
    _current_files(store, [payload['report_source']], limit=MAX_JSON_BYTES)
    _, report = store.json_file(payload['report_source']['path'])
    outcomes, refs = _outcomes(store, report, prepared['payload'], run_id)
    if outcomes != payload['outcomes'] or not isinstance(payload['evidence'], list):
        raise RunError('completion_binding_mismatch')
    if sorted(e['path'] for e in payload['evidence'] if isinstance(e, dict) and 'path' in e) != refs:
        raise RunError('evidence_binding_mismatch')
    _current_files(store, payload['evidence'], limit=MAX_EVIDENCE_BYTES)
    store.policy.fresh()
    return receipt


@_public
def prepare(vault, candidate, sources, fixture, tools, *, model, provider,
            token_budget, candidate_version='unspecified'):
    """Capture one execution contract; identical inputs reuse the prepared ID.

Candidate and sources are selected Markdown treatment files. Fixture/tools are
vault-relative JSON files with the strict v1 schema. No source text executes.
"""
    store = Store(vault)
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_FILES:
        raise RunError('invalid_sources')
    candidate_info, _ = store.fingerprint(candidate, markdown=True)
    source_info = store.capture(sources, markdown=True, used_bytes=candidate_info['size'])
    fixture_info, fixture_value = store.json_file(fixture)
    tools_info, tools_value = store.json_file(tools)
    manifest = dict(candidate=candidate_info, candidate_version=candidate_version,
        sources=sorted(source_info, key=lambda p: p['path']), fixture_source=fixture_info,
        tools_source=tools_info, fixture=_fixture(fixture_value), tools=_tools(tools_value),
        model=model, provider=provider, token_budget=token_budget,
        config_sha256=store.policy.config_sha256)
    _manifest(store, manifest)
    run_id = evidence.digest(evidence.encode(manifest))
    name, _ = _names(run_id)
    with state.advisory_lock(vault, 'run:' + run_id, wait_seconds=1) as lease:
        if store.record_path(name).exists():
            _load_prepared(store, run_id)
        else:
            store.publish(name, 'prepared', run_id, manifest,
                          lambda: _manifest(store, manifest), lease)
    return dict(ok=True, id=run_id, path=RUNTIME_DIR + '/' + name,
                manifest_sha256=run_id, fixture_sha256=evidence.digest(evidence.encode(manifest['fixture'])),
                compatibility_sha256=evidence.digest(evidence.encode(_compatibility(manifest))), authority=AUTHORITY)


@_public
def record(vault, run_id, report):
    """Record the host's complete repeated observations once, without execution."""
    store = Store(vault)
    _, name = _names(run_id)
    with state.advisory_lock(vault, 'run:' + run_id, wait_seconds=1) as lease:
        prepared = _load_prepared(store, run_id)
        if store.record_path(name).exists():
            raise RunError('immutable_receipt_exists')
        report_info, report_value = store.json_file(report)
        outcomes, refs = _outcomes(store, report_value, prepared['payload'], run_id)
        files = store.capture(refs, limit=MAX_EVIDENCE_BYTES)
        # Evidence must be distinct from the report and execution inputs.
        inputs = {p['path'].casefold() for p in [prepared['payload']['candidate'],
            *prepared['payload']['sources'], prepared['payload']['fixture_source'],
            prepared['payload']['tools_source'], report_info]}
        if any(f['path'].casefold() in inputs for f in files):
            raise RunError('evidence_overlaps_input')
        _current_files(store, files, limit=MAX_EVIDENCE_BYTES)
        payload = dict(manifest_sha256=run_id, prepared_receipt_sha256=prepared['receipt_sha256'],
                       report_source=report_info, outcomes=outcomes, evidence=files)
        def recheck():
            current = _load_prepared(store, run_id)
            if current != prepared:
                raise RunError('prepared_receipt_changed')
            _current_files(store, [report_info], limit=MAX_JSON_BYTES)
            _current_files(store, files, limit=MAX_EVIDENCE_BYTES)
        completed = store.publish(name, 'completed', run_id, payload, recheck, lease)
    return dict(ok=True, id=run_id, path=RUNTIME_DIR + '/' + name,
                receipt_sha256=completed['receipt_sha256'], authority=AUTHORITY)


@_public
def inspect(vault, run_id):
    """Inspect current bindings without writing; stale or unsafe data fails closed."""
    store = Store(vault)
    prepared = _load_prepared(store, run_id)
    completed = _load_completion(store, prepared)
    return dict(ok=True, id=run_id, status='complete' if completed else 'prepared',
                manifest_sha256=run_id, manifest=prepared['payload'],
                completion=completed['payload'] if completed else None, authority=AUTHORITY)


def _score(manifest, completion):
    hard = {case['id']: {c['id'] for c in case['assertions'] if c['hard']}
            for case in manifest['fixture']['cases']}
    checks = [check for outcome in completion['payload']['outcomes'] for check in outcome['assertions']]
    reasons = set()
    for outcome in completion['payload']['outcomes']:
        if outcome['status'] != 'complete':
            reasons.add('error_outcome')
        if any(not c['passed'] and c['id'] in hard[outcome['case_id']] for c in outcome['assertions']):
            reasons.add('hard_boundary_failure')
    return sum(c['passed'] for c in checks) / len(checks), reasons


@_public
def compare(vault, baseline_id, candidate_id):
    """Persist an advisory comparison of compatible current completed runs.

Every assertion has equal weight; any hard failure or error vetoes eligibility.
Repeated coverage is required, but these observations are not statistical proof.
"""
    store = Store(vault)
    baseline = _load_prepared(store, baseline_id)
    candidate = _load_prepared(store, candidate_id)
    before = _load_completion(store, baseline)
    after = _load_completion(store, candidate)
    if before is None or after is None:
        raise RunError('run_not_complete')
    if _compatibility(baseline['payload']) != _compatibility(candidate['payload']):
        raise RunError('incompatible_execution_contracts')
    baseline_score, baseline_reasons = _score(baseline['payload'], before)
    candidate_score, candidate_reasons = _score(candidate['payload'], after)
    reasons = sorted(baseline_reasons | candidate_reasons)
    payload = dict(baseline_id=baseline_id, candidate_id=candidate_id,
        baseline_receipt_sha256=before['receipt_sha256'], candidate_receipt_sha256=after['receipt_sha256'],
        compatibility_sha256=evidence.digest(evidence.encode(_compatibility(baseline['payload']))),
        eligible=not reasons, reasons=reasons, baseline_score=baseline_score,
        candidate_score=candidate_score, score_delta=candidate_score - baseline_score,
        outcome_count=len(after['payload']['outcomes']), authority=AUTHORITY,
        effectiveness='unmeasured_outside_recorded_host_observations')
    comparison_id = evidence.digest(evidence.encode(payload))
    name = 'run-compare-' + comparison_id + '.json'
    def recheck():
        for original, completion in ((baseline, before), (candidate, after)):
            current = _load_prepared(store, original['id'])
            if current != original or _load_completion(store, current) != completion:
                raise RunError('run_receipt_changed')
    with state.advisory_lock(vault, 'run-compare:' + comparison_id, wait_seconds=1) as lease:
        recheck()
        if store.record_path(name).exists():
            existing = store.read_record(name)
            if existing['kind'] != 'comparison' or existing['id'] != comparison_id or existing['payload'] != payload:
                raise RunError('comparison_receipt_changed')
        else:
            store.publish(name, 'comparison', comparison_id, payload, recheck, lease)
    return dict(ok=True, id=comparison_id, path=RUNTIME_DIR + '/' + name, **payload)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Argparse messages can contain the complete offending credential or
        # unknown argument. Discard them for every parent and child parser.
        print(evidence.encode(dict(ok=False, error='invalid_cli_arguments')).decode('ascii'))
        self.exit(2)


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_ArgumentParser)
    prep = commands.add_parser('prepare', help='freeze an explicitly selected execution contract')
    for key in ('candidate', 'fixture', 'tools', 'model', 'provider'):
        prep.add_argument('--' + key, required=True)
    prep.add_argument('--source', action='append', required=True)
    prep.add_argument('--candidate-version', default='unspecified')
    prep.add_argument('--token-budget', required=True, type=int)
    rec = commands.add_parser('record', help='record host observations without running any tool/model')
    rec.add_argument('id')
    rec.add_argument('--report', required=True)
    commands.add_parser('inspect', help='read-only current binding check').add_argument('id')
    comp = commands.add_parser('compare', help='write an advisory comparison; never apply a lesson')
    comp.add_argument('baseline_id')
    comp.add_argument('candidate_id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.vault, args.candidate, args.source, args.fixture, args.tools,
                model=args.model, provider=args.provider, token_budget=args.token_budget,
                candidate_version=args.candidate_version)
        elif args.command == 'record':
            result = record(args.vault, args.id, args.report)
        elif args.command == 'inspect':
            result = inspect(args.vault, args.id)
        else:
            result = compare(args.vault, args.baseline_id, args.candidate_id)
        print(evidence.encode(result).decode('ascii'))
        return 0
    except RunError as exc:
        print(evidence.encode(dict(ok=False, error=str(exc))).decode('ascii'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
