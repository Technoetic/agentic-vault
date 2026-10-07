#!/usr/bin/env python3
"""Read-only capture/derivation observations bound to explicit original bytes.

Origin and verifier names are host-reported metadata, not authenticated identity.
Hashes establish point-in-time bindings, never truth, approval or permission.
Explicit ingest inputs may be retrieval-excluded; deny zones always apply.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys

from vault_paths import relative_parts, zone_matches
from jev_client import _normalized


class _LazyEvidence:
    # Capture and the pure staged analyzer must be importable by standalone lint
    # without importing its healthcheck-dependent evidence adapter recursively.
    def __getattr__(self, name):
        return getattr(importlib.import_module('vault_evidence'), name)


evidence = _LazyEvidence()

MAX_FILE_BYTES = 256 * 1024
MAX_SET_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024
MAX_SOURCES = 16
MAX_BINDINGS = 32
MAX_DEPTH = 8
CAPTURE_FIELDS = frozenset({'agentic_vault_capture', 'title', 'type', 'status',
    'ai_priority', 'tags', 'created', 'updated', 'captured_via', 'content_origin',
    'captured_at', 'body_sha256', 'body_bytes', 'classification', 'capture_display'})
MANIFEST_FIELDS = frozenset({'version', 'creator', 'created_at', 'config_sha256',
    'target', 'sources', 'trust', 'manifest_sha256', 'authority'})
SOURCE_FIELDS = frozenset({'path', 'sha256', 'size', 'content_origin', 'classification',
    'trust', 'provenance'})
TARGET_FIELDS = frozenset({'path', 'sha256', 'size'})
RESERVED = ('00-meta/.agentic-vault/runtime', '00-meta/evidence', '00-meta/proposals')
HASH_RE = re.compile(r'[0-9a-f]{64}\Z')
ORIGINS = frozenset({'own', 'forwarded', 'url'})


class ProvenanceError(Exception):
    """A content-free operational code."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _encode(value):
    try:
        data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProvenanceError('invalid_json') from None
    if len(data) > MAX_JSON_BYTES:
        raise ProvenanceError('manifest_byte_limit')
    return data


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceError('duplicate_json_key')
        result[key] = value
    return result


def _constant(_value):
    raise ProvenanceError('non_finite_json')


def _decode(data):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProvenanceError('invalid_json') from None


def _label(value, code='invalid_label'):
    if not isinstance(value, str) or not value.strip() or len(value) > 100 or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProvenanceError(code)
    return value


def _identity(value):
    return ' '.join(_normalized(value).casefold().split())


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ProvenanceError('invalid_timestamp') from None
    return parsed


def render_capture(body, *, captured_via, content_origin='own', captured_at=None,
                   classification='unclassified') -> bytes:
    """Host creates metadata; original body UTF-8 bytes follow the header intact."""
    if not isinstance(body, str):
        raise ProvenanceError('invalid_body')
    try:
        original = body.encode('utf-8')
    except UnicodeError:
        raise ProvenanceError('invalid_body') from None
    if len(original) > MAX_FILE_BYTES:
        raise ProvenanceError('body_byte_limit')
    _label(captured_via)
    _label(classification)
    if not isinstance(content_origin, str) or content_origin not in ORIGINS:
        raise ProvenanceError('invalid_origin')
    if captured_at is None:
        captured_at = datetime.now(timezone.utc)
    if not isinstance(captured_at, datetime):
        raise ProvenanceError('invalid_timestamp')
    # Telegram/local capture timestamps are local wall time when legacy callers
    # supply naive datetime objects. Attach the host's local timezone explicitly.
    captured_at = captured_at.astimezone() if captured_at.tzinfo is None else captured_at
    at = captured_at.isoformat()
    _time(at)
    fields = {'agentic_vault_capture': 1, 'title': 'Jarvis capture', 'type': 'reference',
        'status': 'draft', 'ai_priority': 'low', 'tags': ['capture'],
        'created': captured_at.date().isoformat(), 'updated': captured_at.date().isoformat(),
        'captured_via': captured_via, 'content_origin': content_origin, 'captured_at': at,
        'body_sha256': _sha(original), 'body_bytes': len(original), 'classification': classification,
        'capture_display': f"수신: {captured_at.strftime('%Y-%m-%d %H:%M:%S')} · 채널: {captured_via}"}
    header = '---\n' + ''.join(f'{key}: {json.dumps(value, ensure_ascii=False)}\n'
                               for key, value in fields.items()) + '---\n'
    result = header.encode('utf-8') + original
    if len(result) > MAX_FILE_BYTES:
        raise ProvenanceError('capture_byte_limit')
    return result


def parse_capture(data: bytes):
    """Return (validated host-reported metadata or None, exact original body)."""
    if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
        raise ProvenanceError('capture_byte_limit')
    # Only the fixed capture marker inside leading frontmatter identifies this
    # format. Origin-like instructions in an ordinary note/body carry no trust.
    if not data.startswith(b'---\n'):
        return None, data
    end = data.find(b'\n---\n', 4, 16 * 1024)
    if end < 0:
        if b'agentic_vault_capture:' in data[:16*1024]:
            raise ProvenanceError('invalid_capture_header')
        return None, data
    header = data[4:end]
    if not re.search(rb'^agentic_vault_capture\s*:', header, re.M):
        return None, data
    values = {}
    try:
        for line in header.decode('utf-8').splitlines():
            key, value = line.split(':', 1)
            if key in values or key not in CAPTURE_FIELDS:
                raise ProvenanceError('invalid_capture_header')
            values[key] = _decode(value.strip().encode('utf-8'))
    except (ValueError, UnicodeError):
        raise ProvenanceError('invalid_capture_header') from None
    if set(values) != CAPTURE_FIELDS or type(values['agentic_vault_capture']) is not int or values['agentic_vault_capture'] != 1:
        raise ProvenanceError('invalid_capture_header')
    _label(values['captured_via'])
    _label(values['classification'])
    _time(values['captured_at'])
    if not isinstance(values['content_origin'], str) or values['content_origin'] not in ORIGINS:
        raise ProvenanceError('invalid_origin')
    body = data[end+5:]
    if type(values['body_bytes']) is not int or values['body_bytes'] != len(body) or values['body_sha256'] != _sha(body):
        raise ProvenanceError('capture_body_changed')
    return values, body


class _Reader:
    def __init__(self, vault):
        try:
            self.store = evidence.Store(Path(vault))
        except (ValueError, OSError, evidence.EvidenceError, evidence.HealthcheckError):
            raise ProvenanceError('invalid_vault_policy') from None
        self.observed = {}
        self.total = 0

    def path(self, relative, *, markdown=True):
        try:
            path = self.store.path(relative)
            canonical = path.relative_to(self.store.root).as_posix()
        except (ValueError, OSError, evidence.EvidenceError):
            raise ProvenanceError('unsafe_path') from None
        folded = canonical.casefold()
        if any(folded == reserved or folded.startswith(reserved + '/') for reserved in RESERVED):
            raise ProvenanceError('reserved_path')
        if markdown and path.suffix.casefold() != '.md':
            raise ProvenanceError('invalid_markdown_path')
        return canonical

    def read(self, binding, *, markdown=True):
        if not isinstance(binding, dict) or set(binding) != {'path', 'sha256'} or not isinstance(binding['sha256'], str) or not HASH_RE.fullmatch(binding['sha256']):
            raise ProvenanceError('invalid_binding')
        canonical = self.path(binding['path'], markdown=markdown)
        existing = self.observed.get(canonical)
        if existing:
            if existing[0]['sha256'] != binding['sha256']:
                raise ProvenanceError('conflicting_binding')
            return existing[0], existing[1]
        if len(self.observed) >= MAX_BINDINGS:
            raise ProvenanceError('binding_count_limit')
        try:
            info, _mark, data = self.store.fingerprint(canonical, limit=MAX_FILE_BYTES, contents=True)
            data.decode('utf-8')
        except (ValueError, OSError, UnicodeError, evidence.EvidenceError):
            raise ProvenanceError('unsafe_or_unreadable_original') from None
        if info['sha256'] != binding['sha256']:
            raise ProvenanceError('original_changed')
        self.total += info['size']
        if self.total > MAX_SET_BYTES:
            raise ProvenanceError('original_byte_limit')
        self.observed[canonical] = (info, data)
        return info, data

    def current(self):
        """A second bounded byte/hash sweep; no metadata-only currentness claim."""
        try:
            self.store.fresh_policy()
            for canonical, (saved, _data) in self.observed.items():
                self.path(canonical, markdown=False)
                actual, _mark, _ = self.store.fingerprint(canonical, limit=MAX_FILE_BYTES)
                if actual != saved:
                    raise ProvenanceError('original_changed')
            self.store.fresh_policy()
        except (ValueError, OSError, evidence.EvidenceError):
            raise ProvenanceError('original_or_policy_changed') from None


def capture_policy(vault):
    """Optional legacy-vault capture guard; configured deny policy always wins."""
    from vault_paths import resolve_note_path
    try:
        config = resolve_note_path(Path(vault), '00-meta/vault-config.json', ('.git',))
        if not config.exists():
            return None
        reader = _Reader(vault)
        reader.path('10-inbox/jarvis', markdown=False)
        return reader
    except (ValueError, OSError, ProvenanceError):
        raise ProvenanceError('capture_destination_denied_or_unsafe') from None


def _source(reader, binding, nested=None, depth=0):
    info, data = reader.read(binding)
    if nested is not None:
        _inspect(reader, nested, depth+1)
        if nested['target'] != info:
            raise ProvenanceError('nested_target_mismatch')
        return {**info, 'content_origin': 'derived', 'classification': 'inherited',
                'trust': nested['trust'], 'provenance': nested}
    metadata, _body = parse_capture(data)
    origin = metadata['content_origin'] if metadata else 'unknown'
    return {**info, 'content_origin': origin,
            'classification': metadata['classification'] if metadata else 'unclassified',
            'trust': 'host_own' if origin == 'own' else 'untrusted', 'provenance': None}


def _inspect(reader, manifest, depth=0):
    if depth > MAX_DEPTH:
        raise ProvenanceError('derivation_depth_limit')
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ProvenanceError('invalid_manifest')
    _label(manifest['creator'])
    _time(manifest['created_at'])
    if type(manifest['version']) is not int or manifest['version'] != 1 or manifest['authority'] != 'provenance_observation_only':
        raise ProvenanceError('invalid_manifest')
    body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
    if manifest['manifest_sha256'] != _sha(_encode(body)):
        raise ProvenanceError('manifest_checksum_mismatch')
    if manifest['config_sha256'] != reader.store.config_sha:
        raise ProvenanceError('configuration_changed')
    target = manifest['target']
    if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
        raise ProvenanceError('invalid_target')
    actual_target, _data = reader.read({'path': target['path'], 'sha256': target['sha256']})
    if actual_target != target:
        raise ProvenanceError('target_changed')
    sources = manifest['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ProvenanceError('invalid_source_count')
    seen, trust = {target['path']}, 'host_own'
    for row in sources:
        if not isinstance(row, dict) or set(row) != SOURCE_FIELDS:
            raise ProvenanceError('invalid_source')
        expected = _source(reader, {'path': row['path'], 'sha256': row['sha256']}, row['provenance'], depth)
        if row != expected or row['path'] in seen:
            raise ProvenanceError('invalid_or_duplicate_source')
        seen.add(row['path'])
        if row['trust'] == 'untrusted':
            trust = 'untrusted'
    if manifest['trust'] != trust:
        raise ProvenanceError('trust_mismatch')
    return manifest


def derive_provenance(vault, sources, target, *, creator='host', source_manifests=None):
    """Return a host-reviewable manifest; never write notes, receipts or approvals."""
    _label(creator)
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise ProvenanceError('invalid_source_count')
    if source_manifests is None:
        source_manifests = {}
    if not isinstance(source_manifests, dict) or len(source_manifests) > MAX_SOURCES:
        raise ProvenanceError('invalid_source_manifests')
    reader = _Reader(vault)
    target_info, _data = reader.read(target)
    rows, seen = [], {target_info['path']}
    canonical_manifests = {reader.path(path): value for path, value in source_manifests.items()}
    for binding in sources:
        if not isinstance(binding, dict) or 'path' not in binding:
            raise ProvenanceError('invalid_binding')
        canonical = reader.path(binding['path'])
        if canonical in seen:
            raise ProvenanceError('duplicate_or_self_source')
        seen.add(canonical)
        nested = canonical_manifests.pop(canonical, None)
        rows.append(_source(reader, binding, nested))
    if canonical_manifests:
        raise ProvenanceError('unused_source_manifest')
    reader.current()
    manifest = {'version': 1, 'creator': creator, 'created_at': datetime.now(timezone.utc).isoformat(),
        'config_sha256': reader.store.config_sha, 'target': target_info, 'sources': rows,
        'trust': 'untrusted' if any(row['trust'] == 'untrusted' for row in rows) else 'host_own',
        'authority': 'provenance_observation_only'}
    manifest['manifest_sha256'] = _sha(_encode(manifest))
    return manifest


def _independent_evidence(reader, manifest, evidence_id, producer, consumer=None):
    """Validate bounded existing evidence data; declared identities stay unverified."""
    if evidence_id is None or producer is None:
        return False, 'independent_evidence_missing'
    _label(producer)
    if _identity(producer) != _identity(manifest['creator']):
        return False, 'producer_manifest_mismatch'
    try:
        store = reader.store
        receipt_path = store.receipt_path(evidence_id)
        info, _mark, data = evidence.stable_read(lambda: store.receipt_path(evidence_id), MAX_JSON_BYTES, contents=True)
        receipt = evidence.decode(data)
        evidence.validate_receipt(store, receipt, evidence_id)
        report = receipt['report']
        if report is None or _identity(report['verifier']) == _identity(producer):
            return False, 'reported_verifier_not_independent'
        if not all(check['status'] == 'verified' for check in report['checks']):
            return False, 'verification_gaps'
        bound_paths = {path: record[0] for path, record in reader.observed.items()}
        artifacts = {row['path']: row for row in receipt['artifacts']}
        if not all(artifacts.get(path) == bound for path, bound in bound_paths.items()):
            return False, 'evidence_artifact_binding_missing'
        evidence_rows = receipt['evidence']
        if not evidence_rows or any(row['path'] in artifacts for row in evidence_rows):
            return False, 'external_verification_output_missing'
        all_rows = receipt['artifacts'] + evidence_rows + [receipt['report_source']]
        if len(all_rows) > MAX_BINDINGS or sum(row['size'] for row in all_rows) > MAX_SET_BYTES:
            return False, 'evidence_limit'
        for row in all_rows:
            if row['size'] > MAX_FILE_BYTES:
                return False, 'evidence_limit'
            reader.read({'path': row['path'], 'sha256': row['sha256']}, markdown=False)
        # Reuse the existing structural/binding checker after bounding every
        # original; do not equate a current receipt with verifier authentication.
        checked = evidence.review(store, evidence_id)
        actual, _mark, _ = evidence.stable_read(lambda: store.receipt_path(evidence_id), MAX_JSON_BYTES)
        if actual != info or not checked['valid']:
            return False, 'evidence_stale'
        return True, 'reported_independent_bindings_current'
    except (ValueError, OSError, evidence.EvidenceError, ProvenanceError, TypeError):
        return False, 'evidence_unreadable_or_stale'


def validate_provenance(vault, manifest, *, privileged=False, evidence_id=None,
                        producer=None, consumer=None):
    """Reread approved originals and optional evidence. All observations are advisory."""
    if type(privileged) is not bool:
        raise ProvenanceError('invalid_privileged_flag')
    _encode(manifest)
    result = {'status': 'stale', 'bindings_current': False, 'independent_evidence_current': False,
        'authority': 'provenance_observation_only', 'limitations': ['identity_unverified',
            'host_reported_origin_not_authentication', 'point_in_time_not_workspace_lock',
            'no_truth_or_permission_granted'], 'issues': []}
    try:
        reader = _Reader(vault)
        _inspect(reader, manifest)
        if consumer is not None:
            reader.read(consumer)
        reader.current()
        result.update(bindings_current=True, status='current', trust=manifest['trust'])
        need = privileged and manifest['trust'] == 'untrusted'
        if need or evidence_id is not None:
            valid, reason = _independent_evidence(reader, manifest, evidence_id, producer, consumer)
            reader.current()
            result['independent_evidence_current'] = valid
            if not valid:
                result['issues'].append(reason)
                if need:
                    result['status'] = 'needs_independent_evidence'
        return result
    except ProvenanceError as exc:
        result.update(status='stale', bindings_current=False, independent_evidence_current=False)
        result['issues'].append(str(exc))
        return result


def _header_fields(text):
    """Small selected metadata surface, not a general YAML interpretation."""
    if not text.startswith(('---\n', '---\r\n')):
        return {}
    fields = {}
    for line in text.splitlines()[1:65]:
        if line == '---':
            break
        match = re.match(r'^([a-z_]+)\s*:\s*(.*?)\s*$', line)
        if not match or match[1] in fields:
            continue
        try:
            value = _decode(match[2].encode('utf-8'))
        except ProvenanceError:
            value = match[2].strip('"\'')
        fields[match[1]] = value
    return fields


def analyze_notes(notes, config=None):
    """Pure index-original checks; unresolved evidence is never declared absent.

Derived notes reference an explicitly saved manifest via frontmatter
provenance_manifest: "00-meta/provenance/<host-selected-id>.json". Optional
provenance_sources is a JSON list of literal note paths; wiki-link destinations
are resolved only when unique. The index-only inventory cannot authenticate an
external verifier or read excluded JSON sidecars, so privileged dependencies
emit provenance-unresolved until the separate explicit validator is run.
"""
    from vault_lint_extensions import make_warning
    if not isinstance(notes, dict) or not isinstance(config or {}, dict):
        return [make_warning('provenance-incomplete', '', 0, 'Provenance inventory is incomplete', details={'reason': 'invalid_inventory'})]
    if len(notes) > 256:
        return [make_warning('provenance-incomplete', '', 0, 'Provenance inventory exceeds inspection bounds', details={'reason': 'inventory_limit'})]
    config = config or {}
    denied = config.get('deny_zones', ['10-inbox/_processed', '20-knowledge/_archive',
        '50-projects/_completed', '90-assets', '.obsidian'])
    excluded = config.get('exclude_dirs', [])
    privileged = config.get('privileged_notes', [])
    if not isinstance(privileged, list):
        privileged = []
    privileged = {p.casefold() for p in privileged if isinstance(p, str)}
    privileged.update(config[p].casefold() for p in ('hot_note', 'handoff_note') if isinstance(config.get(p), str))
    if not isinstance(denied, (list, tuple)) or not isinstance(excluded, (list, tuple)) or not all(
            isinstance(rule, str) for rule in (*denied, *excluded)):
        return [make_warning('provenance-incomplete', '', 0, 'Provenance policy is incomplete', details={'reason': 'invalid_policy'})]
    allowed, metadata, unsafe, findings, total = {}, {}, set(), [], 0
    for path, text in sorted(notes.items(), key=lambda item: str(item[0])):
        try:
            parts = relative_parts(path, 'provenance original')
        except ValueError:
            findings.append(make_warning('provenance-incomplete', '', 0, 'Unsafe provenance inventory path', details={'reason': 'invalid_path'}))
            continue
        if any(zone_matches(parts, rule) for rule in (*denied, *excluded, '.git')) or tuple(
                p.casefold() for p in parts[:3]) == ('00-meta', '.agentic-vault', 'runtime'):
            continue
        if not isinstance(text, str):
            findings.append(make_warning('provenance-incomplete', path, 0, 'Original unavailable', details={'reason': 'invalid_original'}))
            continue
        try:
            data = text.encode('utf-8')
        except UnicodeError:
            data = b''
            unsafe.add(path)
        total += len(data)
        if len(allowed) >= 256 or total > MAX_SET_BYTES:
            findings.append(make_warning('provenance-incomplete', '', 0, 'Provenance scan incomplete', details={'reason': 'inventory_limit'}))
            break
        if len(data) > MAX_FILE_BYTES or path in unsafe:
            unsafe.add(path)
            findings.append(make_warning('provenance-incomplete', path, 0, 'Provenance original exceeds inspection bounds', details={'reason': 'original_limit'}))
            continue
        allowed[path] = text
        try:
            capture, _body = parse_capture(data)
            metadata[path] = capture or _header_fields(text)
        except ProvenanceError as exc:
            unsafe.add(path)
            metadata[path] = {}
            code = 'provenance-body-mismatch' if str(exc) == 'capture_body_changed' else 'provenance-invalid'
            findings.append(make_warning(code, path, 1, 'Capture metadata does not bind the selected original', details={'reason': str(exc)}))
    by_name = {}
    for path in allowed:
        by_name.setdefault(Path(path).stem.casefold(), []).append(path)
    for path, text in allowed.items():
        if path.casefold() not in privileged:
            continue
        fields = metadata[path]
        risk = fields.get('content_origin') in ('forwarded', 'url', 'derived') or fields.get('provenance_trust') == 'untrusted' or bool(fields.get('provenance_manifest'))
        references = fields.get('provenance_sources', [])
        references = references if isinstance(references, list) else []
        references = [ref for ref in references[:128] if isinstance(ref, str)]
        references += [m[1].split('|', 1)[0].split('#', 1)[0] for m in list(re.finditer(r'\[\[([^\]\n]+)\]\]', text))[:128]]
        for ref in references:
            candidate = ref if ref.casefold().endswith('.md') else ref + '.md'
            if '/' not in ref and '\\' not in ref:
                destinations = by_name.get(Path(ref).stem.casefold(), [])
                candidate = destinations[0] if len(destinations) == 1 else ''
            source = metadata.get(candidate, {})
            if candidate in unsafe or source.get('content_origin') in ('forwarded', 'url', 'derived') or source.get('provenance_trust') == 'untrusted' or source.get('provenance_manifest'):
                risk = True
        if risk:
            findings.append(make_warning('provenance-unresolved', path, 1,
                'Privileged provenance requires explicit current independent evidence; index-only inspection cannot verify it',
                details={'reason': 'index_inventory_cannot_verify_evidence', 'verified_by_is_authority': False}))
    if len(findings) > 128:
        return findings[:127] + [make_warning('provenance-incomplete', '', 0,
            'Provenance warnings exceed output bounds', details={'reason': 'warning_limit'})]
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    parser.add_argument('command', choices=('derive', 'validate'))
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_JSON_BYTES+1)
        if len(raw) > MAX_JSON_BYTES:
            raise ProvenanceError('input_byte_limit')
        request = evidence.decode(raw)
        if not isinstance(request, dict):
            raise ProvenanceError('invalid_request')
        if args.command == 'derive':
            if not {'sources', 'target'} <= set(request) or set(request) - {'sources', 'target', 'creator', 'source_manifests'}:
                raise ProvenanceError('invalid_request')
            result = derive_provenance(args.vault, **request)
            code = 0
        else:
            if 'manifest' not in request or set(request) - {'manifest', 'privileged', 'evidence_id', 'producer', 'consumer'}:
                raise ProvenanceError('invalid_request')
            result = validate_provenance(args.vault, **request)
            code = 0 if result['status'] == 'current' else 1
    except (ValueError, OSError, evidence.EvidenceError, ProvenanceError, UnicodeError, RecursionError):
        result, code = {'status': 'error', 'error': 'invalid_or_unsafe_request'}, 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
