#!/usr/bin/env python3
"""Bounded cooperative locks, revision-bound edits and local handoff projections.

Only cooperating writers observe the locks. A replace is atomic for one file;
policy/hash rechecks do not freeze the filesystem or form a multi-file transaction.
Runtime access is an internal namespace exception to inventory exclusions only:
configured deny zones still apply, and ordinary note APIs reserve runtime paths.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import uuid

from vault_evidence import EvidenceError, decode, regular, stable_read
from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import relative_parts, resolve_note_path, zone_matches

RUNTIME_DIR = '00-meta/.agentic-vault/runtime'
CONFIG_PATH = '00-meta/vault-config.json'
MAX_BYTES = 256 * 1024
MAX_RUNTIME_BYTES = 64 * 1024
MAX_ITEMS = 128
MAX_WAIT_SECONDS = 60
MAX_TTL_SECONDS = 86400
HASH_RE = re.compile(r'[0-9a-f]{64}')
TOKEN_RE = re.compile(r'[0-9a-f]{32}')
ITEM_ID_RE = re.compile(r'(?:[A-Za-z][A-Za-z0-9_]{0,23}-?\d{1,10}|av-[0-9a-f]{20})')
PENDING_ID_RE = re.compile(r'(?:[A-Za-z][A-Za-z0-9_]{0,23}-?\d{1,10}|av-[0-9a-f]{20}|[0-9a-f]{32,64})')
BEGIN = '<!-- agentic-vault:handoff begin -->'
END = '<!-- agentic-vault:handoff end -->'
EVENTS = frozenset({'PreCompact', 'SessionEnd'})


class StateError(Exception):
    """Content-free operational error code."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value, code='invalid_hash'):
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise StateError(code)
    return value


def _read(resolver, limit=MAX_BYTES):
    try:
        return stable_read(resolver, limit, contents=True)
    except (EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise StateError('unsafe_or_changed_source') from exc


class _Policy:
    def __init__(self, vault):
        self.vault = Path(vault)
        try:
            self.root = self.vault.resolve(strict=True)
            info, _, raw = _read(lambda: resolve_note_path(self.vault, CONFIG_PATH, ('.git',)))
            self.config = validate_config(decode(raw))
            self.config_sha256 = info['sha256']
            self.denied = (*self.config['deny_zones'], '.git')
            self.rules = (*self.denied, *self.config['exclude_dirs'], RUNTIME_DIR)
            resolve_note_path(self.vault, CONFIG_PATH, self.rules)
        except (EvidenceError, HealthcheckError, ValueError, OSError, RuntimeError) as exc:
            raise StateError('unsafe_configuration') from exc

    def note(self, relative):
        try:
            parts = relative_parts(relative, 'note')
            if zone_matches(parts, RUNTIME_DIR):
                raise StateError('reserved_runtime_path')
            path = resolve_note_path(self.vault, relative, self.rules)
            if path.suffix.casefold() != '.md':
                raise StateError('invalid_markdown_path')
            return path
        except (ValueError, OSError, RuntimeError) as exc:
            raise StateError('unsafe_path') from exc

    def runtime(self, name=''):
        try:
            if name and (len(relative_parts(name, 'runtime file')) != 1 or len(name) > 120):
                raise StateError('unsafe_runtime_path')
            return resolve_note_path(self.vault, RUNTIME_DIR + ('/' + name if name else ''),
                                     self.denied)
        except (ValueError, OSError, RuntimeError) as exc:
            raise StateError('unsafe_runtime_path') from exc

    def fresh(self):
        current = _Policy(self.vault)
        if current.config_sha256 != self.config_sha256:
            raise StateError('configuration_changed')
        return current

    def read_note(self, relative):
        info, mark, raw = _read(lambda: self.note(relative))
        return {'path': self.note(relative).relative_to(self.root).as_posix(), **info}, mark, raw


def _ensure_runtime(policy):
    for count in range(1, len(RUNTIME_DIR.split('/')) + 1):
        relative = '/'.join(RUNTIME_DIR.split('/')[:count])
        try:
            path = resolve_note_path(policy.vault, relative, policy.denied)
            path.mkdir(exist_ok=True)
            checked = resolve_note_path(policy.vault, relative, policy.denied)
            if not stat.S_ISDIR(checked.lstat().st_mode):
                raise StateError('unsafe_runtime_path')
        except (ValueError, OSError, RuntimeError) as exc:
            raise StateError('unsafe_runtime_path') from exc
    return policy.runtime()


def _exclusive(policy, name, record):
    _ensure_runtime(policy)
    path = policy.runtime(name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            regular(os.fstat(stream.fileno()))
            stream.write(_encode(record) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        _read(lambda: policy.runtime(name), MAX_RUNTIME_BYTES)
    except BaseException:
        # Leave an incomplete lock for conservative manual recovery.
        raise
    return path


def _pause(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise StateError('lock_busy')
    time.sleep(min(.02, remaining))


def _validate_owner_record(record, *, lease):
    fields = {'token', 'pid', 'created_at'}
    if lease:
        fields |= {'version', 'ttl_seconds'}
    if (not isinstance(record, dict) or set(record) != fields
            or not isinstance(record['token'], str) or not TOKEN_RE.fullmatch(record['token'])
            or type(record['pid']) is not int or record['pid'] <= 0
            or not isinstance(record['created_at'], str) or len(record['created_at']) > 48):
        raise StateError('invalid_lease')
    try:
        created = datetime.fromisoformat(record['created_at'])
        if created.utcoffset() is None or created.utcoffset().total_seconds() != 0:
            raise StateError('invalid_lease')
        timestamp = created.timestamp()
        if not math.isfinite(timestamp):
            raise StateError('invalid_lease')
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise StateError('invalid_lease') from exc
    if lease:
        ttl = record['ttl_seconds']
        if (type(record['version']) is not int or record['version'] != 1
                or isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                or not 0 < ttl <= MAX_TTL_SECONDS or not math.isfinite(ttl)):
            raise StateError('invalid_lease')
    return timestamp


def _owned(policy, name, token):
    info, _, raw = _read(lambda: policy.runtime(name), MAX_RUNTIME_BYTES)
    try:
        record = decode(raw)
    except EvidenceError as exc:
        raise StateError('lock_owner_changed') from exc
    if not isinstance(record, dict) or record.get('token') != token:
        raise StateError('lock_owner_changed')
    _validate_owner_record(record, lease=not name.endswith('.guard'))
    return record


def _cleanup_owner(policy, name, token):
    """Retain recovery evidence if current policy or owner validation fails."""
    current = policy.fresh()
    _owned(current, name, token)
    current = policy.fresh()
    path = current.runtime(name)
    policy.fresh()
    path.unlink()


@contextmanager
def _guard(policy, name, deadline):
    """Serialize transitions; interrupted or policy-revoked guards need manual recovery.

    Guards are deliberately never recovered by TTL; recursively recovering a
    transition guard would reintroduce the ownership race it prevents.
    """
    token = uuid.uuid4().hex
    guard_name = name + '.guard'
    while True:
        policy.fresh()
        try:
            _exclusive(policy, guard_name, {'token': token, 'pid': os.getpid(), 'created_at': _now()})
            break
        except FileExistsError:
            path = policy.runtime(guard_name)
            try:
                regular(path.lstat())
            except FileNotFoundError:
                # Another cooperating holder may release its short guard
                # between the O_EXCL collision and this identity check.
                _pause(deadline)
                continue
            except (EvidenceError, OSError) as exc:
                raise StateError('unsafe_lock') from exc
            _pause(deadline)
        except (EvidenceError, ValueError, OSError) as exc:
            raise StateError('unsafe_lock') from exc
    try:
        yield
    finally:
        try:
            _cleanup_owner(policy, guard_name, token)
        except (StateError, OSError):
            pass


def _expired(policy, name):
    try:
        _, mark, raw = _read(lambda: policy.runtime(name), MAX_RUNTIME_BYTES)
        record = decode(raw)
        created = _validate_owner_record(record, lease=True)
        ttl = record['ttl_seconds']
        # Both clocks must agree. Future/skewed clocks and a freshly touched
        # lock are not evidence of expiry. PID is metadata, never probed.
        wall = time.time()
        return wall - created >= ttl and wall - mark[3] / 1e9 >= ttl
    except (StateError, EvidenceError, ValueError, TypeError, OverflowError):
        return False


@contextmanager
def advisory_lock(vault, key, *, ttl_seconds=300, wait_seconds=0):
    """Acquire a bounded lease for cooperating writers; yield owner metadata.

    Expired well-formed leases require both UTC and mtime expiry. A stale,
    interrupted or policy-revoked guard remains blocked for manual recovery. Callers
    must finish before their TTL; patch_note additionally checks ownership at
    replace time. No process liveness signal or filesystem transaction is used.
    """
    if not isinstance(key, str) or not key or len(key.encode('utf-8')) > 1024 or '\x00' in key:
        raise StateError('invalid_lock_key')
    for value, maximum, positive in ((ttl_seconds, MAX_TTL_SECONDS, True),
                                      (wait_seconds, MAX_WAIT_SECONDS, False)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value > maximum
                or value < 0 or (positive and value == 0)):
            raise StateError('invalid_lock_timing')
    policy = _Policy(vault)
    name = 'lock-' + _sha(key.encode('utf-8')) + '.json'
    token = uuid.uuid4().hex
    deadline = time.monotonic() + wait_seconds
    record = dict(version=1, token=token, pid=os.getpid(), created_at=_now(), ttl_seconds=ttl_seconds)
    while True:
        with _guard(policy, name, deadline):
            path = policy.runtime(name)
            if path.exists():
                try:
                    regular(path.lstat())
                except (EvidenceError, OSError) as exc:
                    raise StateError('unsafe_lock') from exc
                if _expired(policy, name):
                    path.unlink()
            try:
                record['created_at'] = _now()
                path = _exclusive(policy, name, record)
                break
            except FileExistsError:
                pass
            except (EvidenceError, ValueError, OSError) as exc:
                raise StateError('unsafe_lock') from exc
        _pause(deadline)
    lease = dict(record, lock_path=str(path))
    try:
        yield lease
    finally:
        # Every cooperating acquisition/recovery/release uses the same guard,
        # so a checked owner cannot be swapped between the check and unlink.
        try:
            with _guard(policy, name, time.monotonic() + min(1, MAX_WAIT_SECONDS)):
                _cleanup_owner(policy, name, token)
        except (StateError, OSError):
            pass


def _lease_guard(policy, lease):
    return _guard(policy, Path(lease['lock_path']).name, time.monotonic() + 1)


def _check_lease(policy, lease):
    current = _owned(policy, Path(lease['lock_path']).name, lease['token'])
    expected = {key: lease[key] for key in ('version', 'token', 'pid', 'created_at', 'ttl_seconds')}
    created = _validate_owner_record(current, lease=True)
    if current != expected:
        raise StateError('invalid_lease')
    if time.time() - created >= current['ttl_seconds']:
        raise StateError('lock_expired')


def _edited(original, edits):
    if not isinstance(edits, list) or len(edits) > MAX_ITEMS:
        raise StateError('invalid_edits')
    try:
        original.decode('utf-8')
    except UnicodeError as exc:
        raise StateError('invalid_utf8') from exc
    converted = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'start', 'end', 'replacement'}:
            raise StateError('invalid_edit')
        start, end = edit['start'], edit['end']
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(original):
            raise StateError('invalid_edit_range')
        replacement = edit['replacement']
        if isinstance(replacement, str):
            if len(replacement) > MAX_BYTES:
                raise StateError('output_too_large')
            try:
                replacement = replacement.encode('utf-8')
            except UnicodeError as exc:
                raise StateError('invalid_utf8') from exc
        if not isinstance(replacement, bytes):
            raise StateError('invalid_replacement')
        if len(replacement) > MAX_BYTES:
            raise StateError('output_too_large')
        try:
            original[:start].decode('utf-8')
            original[:end].decode('utf-8')
            replacement.decode('utf-8')
        except UnicodeError as exc:
            raise StateError('split_or_invalid_utf8') from exc
        converted.append((start, end, replacement))
    converted.sort(key=lambda edit: (edit[0], edit[1]))
    if len(original) + sum(len(replacement) - (end - start) for start, end, replacement in converted) > MAX_BYTES:
        raise StateError('output_too_large')
    result, cursor, previous_start = [], 0, -1
    for start, end, replacement in converted:
        if start < cursor or start == previous_start:
            raise StateError('overlapping_edits')
        result.extend((original[cursor:start], replacement))
        cursor, previous_start = end, start
    result.append(original[cursor:])
    candidate = b''.join(result)
    if len(candidate) > MAX_BYTES:
        raise StateError('output_too_large')
    return candidate, sum(end - start + len(replacement) for start, end, replacement in converted)


def _cleanup_temporary(policy, relative, temporary, *, runtime=False):
    """Check current policy before cleanup metadata or deletion of our temporary."""
    current = policy.fresh()
    try:
        if runtime:
            target = current.runtime(relative)
            checked = current.runtime(temporary.name)
        else:
            target = current.note(relative)
            selected = temporary.relative_to(current.root).as_posix()
            checked = resolve_note_path(current.vault, selected, current.rules)
        if checked != temporary or checked.parent != target.parent:
            raise StateError('unsafe_temporary')
        regular(checked.lstat())
    except FileNotFoundError:
        raise
    except (EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise StateError('unsafe_temporary') from exc
    policy.fresh()


def _atomic(path, data, before_replace, *, cleanup_guard=None):
    """Retain our temporary for manual recovery when its cleanup guard refuses."""
    mode = stat.S_IMODE(path.lstat().st_mode) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix='.vault-state-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            regular(os.fstat(stream.fileno()))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        before_replace(temporary)
    finally:
        try:
            if cleanup_guard is not None:
                cleanup_guard(temporary)
            temporary.unlink()
        except FileNotFoundError:
            pass
        except StateError:
            # A failed current-policy/path check retains our staged file for
            # conservative manual recovery; do not inspect denied metadata.
            pass


def patch_note(vault, path, expected_sha256, edits, *, source_hashes=None,
               expected_config_sha256=None):
    """Apply UTF-8 byte-offset edits against one exact revision.

    Each edit is {start, end, replacement}, with half-open offsets in the
    original bytes and a UTF-8 str/bytes replacement. Other bytes stay intact.
    Optional explicit source_hashes binds upstream Markdown sources as well.
    expected_config_sha256 optionally binds the caller's configuration generation;
    policy is rechecked again immediately before the atomic replacement.
    This helper does not infer approval or establish the truth of edited facts.
    """
    _hash(expected_sha256)
    if expected_config_sha256 is not None:
        _hash(expected_config_sha256)
    policy = _Policy(vault)
    if expected_config_sha256 is not None and policy.config_sha256 != expected_config_sha256:
        raise StateError('expected_configuration_changed')
    target = policy.note(path)
    canonical = target.relative_to(policy.root).as_posix()
    if source_hashes is None:
        source_hashes = {}
    if not isinstance(source_hashes, dict) or len(source_hashes) > MAX_ITEMS:
        raise StateError('invalid_source_hashes')
    for relative, expected in source_hashes.items():
        policy.note(relative)
        _hash(expected)
    with advisory_lock(vault, 'note:' + canonical.casefold(), wait_seconds=1) as lease:
        info, _, original = policy.read_note(canonical)
        if info['sha256'] != expected_sha256:
            raise StateError('stale_base')
        candidate, changed = _edited(original, edits)
        def replace(temporary):
            with _lease_guard(policy, lease):
                current = policy.fresh()
                _check_lease(current, lease)
                latest, _, _ = current.read_note(canonical)
                if latest['sha256'] != expected_sha256:
                    raise StateError('stale_base')
                for relative, expected in source_hashes.items():
                    source, _, _ = current.read_note(relative)
                    if source['sha256'] != expected:
                        raise StateError('source_changed')
                checked = current.note(canonical)
                if checked != target:
                    raise StateError('unsafe_path')
                regular(temporary.lstat())
                # Source/target reads may have outlasted the earlier policy
                # observation. Close that observation before this replace;
                # this remains a point-in-time check, not a filesystem freeze.
                policy.fresh()
                os.replace(temporary, checked)
        if candidate != original:
            _atomic(target, candidate, replace,
                    cleanup_guard=lambda temporary: _cleanup_temporary(policy, canonical, temporary))
        else:
            # Unchanged results still enforce policy, revision and sources.
            with _lease_guard(policy, lease):
                current = policy.fresh()
                _check_lease(current, lease)
                latest, _, _ = current.read_note(canonical)
                if latest['sha256'] != expected_sha256:
                    raise StateError('stale_base')
                for relative, expected in source_hashes.items():
                    source, _, _ = current.read_note(relative)
                    if source['sha256'] != expected:
                        raise StateError('source_changed')
                policy.fresh()
        return dict(status='applied' if candidate != original else 'unchanged', path=canonical,
                    before_sha256=expected_sha256, after_sha256=_sha(candidate), bytes_changed=changed)


def stable_item_id(namespace, content, *, registry=None):
    if (not isinstance(namespace, str) or not namespace or len(namespace) > 256
            or not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > 8192):
        raise StateError('invalid_item')
    full = _sha((namespace + '\0' + ' '.join(content.split())).encode('utf-8'))
    identifier = 'av-' + full[:20]
    if registry is not None:
        if identifier in registry and registry[identifier] != full:
            raise StateError('id_collision')
        registry[identifier] = full
    return identifier


def _lines(raw):
    """Yield outside-fence lines with original byte ranges."""
    fence, offset = '', 0
    for line in raw.splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        text = line.decode('utf-8').rstrip('\r\n')
        body = text.lstrip(' ')
        indent = len(text) - len(body)
        match = re.match(r'(`{3,}|~{3,})(.*)$', body) if indent <= 3 else None
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ''
            continue
        if match and (match[1][0] == '~' or '`' not in match[2]):
            fence = match[1]
            continue
        yield start, offset, text


def _block_span(raw):
    start, body_start, found = None, None, None
    try:
        for begin, end, line in _lines(raw):
            if line.strip() == BEGIN:
                if start is not None or found is not None:
                    raise StateError('malformed_handoff_block')
                start, body_start = begin, end
            elif line.strip() == END:
                if start is None or found is not None:
                    raise StateError('malformed_handoff_block')
                found = (start, end, raw[body_start:begin])
                start = None
    except UnicodeError as exc:
        raise StateError('invalid_utf8') from exc
    if start is not None:
        raise StateError('malformed_handoff_block')
    return found


def _items(raw, namespace):
    records, seen, registry = [], {}, {}
    blocked_level = None
    try:
        for _, _, line in _lines(raw):
            heading = re.match(r' {0,3}(#{1,6})\s+(.+?)\s*#*$', line)
            if heading:
                level = len(heading[1])
                if blocked_level is not None and level <= blocked_level:
                    blocked_level = None
                if heading[2].strip().casefold() in {'blocked', '차단', '보류'} and blocked_level is None:
                    blocked_level = level
            blocked = blocked_level is not None
            task = re.match(r'\s*[-*+]\s+\[([ xX])\]\s+(.+)$', line)
            if not task:
                continue
            body = task[2]
            identifier = None
            tagged = re.match(r'\[([^]\n]+)\]\s*(.*)$', body)
            if tagged and ITEM_ID_RE.fullmatch(tagged[1]):
                identifier, body = tagged[1], tagged[2]
            if not body.strip() or len(body.encode('utf-8')) > 8192:
                raise StateError('invalid_item')
            identifier = identifier or stable_item_id(namespace, body, registry=registry)
            fingerprint = _sha(body.encode('utf-8'))
            if identifier in seen:
                raise StateError('id_collision' if seen[identifier] != fingerprint else 'duplicate_id')
            seen[identifier] = fingerprint
            records.append(dict(id=identifier, text=body,
                                state='completed' if task[1].lower() == 'x' else ('blocked' if blocked else 'pending')))
            if len(records) > MAX_ITEMS:
                raise StateError('too_many_items')
    except UnicodeError as exc:
        raise StateError('invalid_utf8') from exc
    return records


def _projection(value):
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('items'), list) or len(value['items']) > MAX_ITEMS):
        raise StateError('invalid_handoff_projection')
    seen = set()
    for item in value['items']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not ITEM_ID_RE.fullmatch(item['id']) or item['id'] in seen
                or not isinstance(item.get('state'), str)
                or item['state'] not in {'pending', 'blocked', 'completed'}):
            raise StateError('invalid_handoff_projection')
        seen.add(item['id'])
    return value


def detect_removed_blockers(previous_anchor, current_projection, *, expected_sha256):
    """Compare only the explicitly supplied exact previous anchor, never history guesses."""
    _hash(expected_sha256)
    if isinstance(previous_anchor, str):
        previous_anchor = previous_anchor.encode('utf-8')
    if not isinstance(previous_anchor, bytes) or len(previous_anchor) > MAX_BYTES:
        raise StateError('invalid_previous_anchor')
    if _sha(previous_anchor) != expected_sha256:
        raise StateError('stale_previous_anchor')
    current = _projection(current_projection)
    span = _block_span(previous_anchor)
    if span is None:
        return []
    try:
        previous = _projection(decode(span[2]))
    except EvidenceError as exc:
        raise StateError('invalid_handoff_projection') from exc
    current_ids = {item['id'] for item in current['items']}
    return [dict(code='removed_blocker', id=item['id'], previous_anchor_sha256=expected_sha256)
            for item in previous['items'] if item['state'] == 'blocked' and item['id'] not in current_ids]


def project_handoff(vault, log_path, tasks_path, anchor_path, *, namespace='handoff', apply=False,
                    expected_sha256=None, previous_anchor_path=None, previous_anchor_sha256=None):
    """Project explicit current sources into one machine block; default is dry run.

    Narrative bytes remain outside the replacement span. Log/task data is
    quoted as data. Legacy IDs remain intact. No previous anchor is inferred.
    """
    if type(apply) is not bool:
        raise StateError('invalid_apply_flag')
    policy = _Policy(vault)
    log_info, _, log = policy.read_note(log_path)
    tasks_info, _, tasks = policy.read_note(tasks_path)
    anchor_info, _, anchor = policy.read_note(anchor_path)
    if anchor_info['path'] in {log_info['path'], tasks_info['path']}:
        raise StateError('overlapping_projection_sources')
    if not isinstance(namespace, str) or len(namespace) > 128:
        raise StateError('invalid_namespace')
    items = _items(tasks, namespace + ':' + tasks_info['path'])
    log_events = [line for _, _, line in _lines(log) if line.startswith('- ')][:32]
    if any(len(line.encode('utf-8')) > 8192 for line in log_events):
        raise StateError('log_event_too_large')
    projection = dict(version=1, namespace=namespace, sources=[log_info, tasks_info],
                      items=items, log_events=log_events)
    warnings = []
    previous_checked = False
    source_hashes = {log_info['path']: log_info['sha256'], tasks_info['path']: tasks_info['sha256']}
    if previous_anchor_path is not None or previous_anchor_sha256 is not None:
        if previous_anchor_path is None or previous_anchor_sha256 is None:
            raise StateError('previous_anchor_binding_required')
        previous_info, _, previous = policy.read_note(previous_anchor_path)
        warnings = detect_removed_blockers(previous, projection, expected_sha256=previous_anchor_sha256)
        source_hashes[previous_info['path']] = previous_info['sha256']
        previous_checked = True
    newline = '\r\n' if b'\r\n' in anchor else '\n'
    block = newline.join((BEGIN, _encode(projection).decode(), END)) + newline
    span = _block_span(anchor)
    if span:
        edits = [dict(start=span[0], end=span[1], replacement=block)]
    else:
        prefix = '' if not anchor or anchor.endswith((b'\n', b'\r')) else newline
        edits = [dict(start=len(anchor), end=len(anchor), replacement=prefix + block)]
    candidate, _ = _edited(anchor, edits)
    result = dict(status='proposed', path=anchor_info['path'], before_sha256=anchor_info['sha256'],
                  after_sha256=_sha(candidate), projection=projection, block=block,
                  warnings=warnings, previous_anchor_checked=previous_checked, edits=edits)
    # Also protect the default dry-run publication after the optional exact
    # previous-anchor read, not only the privileged apply path.
    policy.fresh()
    if apply:
        if expected_sha256 is None:
            raise StateError('expected_hash_required')
        if expected_sha256 != anchor_info['sha256']:
            raise StateError('stale_base')
        applied = patch_note(vault, anchor_info['path'], expected_sha256, edits,
                             source_hashes=source_hashes, expected_config_sha256=policy.config_sha256)
        result.update(applied)
    return result


def _pending(value):
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        raise StateError('invalid_pending_references')
    if any(not isinstance(item, str) or not PENDING_ID_RE.fullmatch(item) for item in value):
        raise StateError('invalid_pending_references')
    return list(dict.fromkeys(value))


def _checkpoint_record(record, session_sha):
    if (not isinstance(record, dict) or set(record) != {'version', 'session_sha256', 'config_sha256',
                                                      'sources', 'events', 'pending_references'}
            or record['version'] != 1 or record['session_sha256'] != session_sha
            or not isinstance(record['events'], list) or len(record['events']) > 16):
        raise StateError('invalid_checkpoint')
    for event in record['events']:
        if (not isinstance(event, dict) or set(event) != {'event', 'at'}
                or not isinstance(event['event'], str) or event['event'] not in EVENTS):
            raise StateError('invalid_checkpoint')
        try:
            parsed = datetime.fromisoformat(event['at'])
            if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise StateError('invalid_checkpoint') from exc
    _hash(record['config_sha256'])
    _pending(record['pending_references'])
    return record


def write_checkpoint(vault, event, *, session_id=None, pending_references=None):
    """Store model-free hash/event accounting only; never edit business notes."""
    if not isinstance(event, str) or event not in EVENTS:
        raise StateError('invalid_checkpoint_event')
    if session_id is not None and (not isinstance(session_id, str) or not session_id or len(session_id) > 128):
        raise StateError('invalid_session_id')
    pending = _pending(pending_references) if pending_references is not None else None
    policy = _Policy(vault)
    session_sha = _sha((session_id or 'unspecified-session').encode('utf-8'))
    name = 'checkpoint-' + session_sha[:32] + '.json'
    with advisory_lock(vault, 'checkpoint:' + session_sha, wait_seconds=1) as lease:
        sources = []
        for key in ('hot_note', 'handoff_note', 'log_note', 'tasks_note'):
            relative = policy.config.get(key)
            if not relative:
                continue
            path = policy.note(relative)
            if not path.exists():
                continue
            info, _, _ = policy.read_note(relative)
            if info['path'] not in {source['path'] for source in sources}:
                sources.append(info)
        target = policy.runtime(name)
        previous, base_sha = None, None
        if target.exists():
            info, _, raw = _read(lambda: policy.runtime(name), MAX_RUNTIME_BYTES)
            try:
                previous = _checkpoint_record(decode(raw), session_sha)
            except EvidenceError as exc:
                raise StateError('invalid_checkpoint') from exc
            base_sha = info['sha256']
        history = previous['events'] if previous else []
        record = dict(version=1, session_sha256=session_sha, config_sha256=policy.config_sha256,
                      sources=sources, events=(history + [dict(event=event, at=_now())])[-16:],
                      pending_references=pending if pending is not None else (previous['pending_references'] if previous else []))
        raw = _encode(record) + b'\n'
        if len(raw) > MAX_RUNTIME_BYTES:
            raise StateError('checkpoint_too_large')
        def replace(temporary):
            with _lease_guard(policy, lease):
                current = policy.fresh()
                _check_lease(current, lease)
                for source in sources:
                    info, _, _ = current.read_note(source['path'])
                    if info['sha256'] != source['sha256']:
                        raise StateError('source_changed')
                checked = current.runtime(name)
                if base_sha is None:
                    if checked.exists():
                        raise StateError('checkpoint_changed')
                else:
                    info, _, _ = _read(lambda: current.runtime(name), MAX_RUNTIME_BYTES)
                    if info['sha256'] != base_sha:
                        raise StateError('checkpoint_changed')
                regular(temporary.lstat())
                policy.fresh()
                os.replace(temporary, checked)
        _atomic(target, raw, replace,
                cleanup_guard=lambda temporary: _cleanup_temporary(policy, name, temporary, runtime=True))
        return dict(path=RUNTIME_DIR + '/' + name, sha256=_sha(raw), event=event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    project = commands.add_parser('project-handoff')
    for key in ('log', 'tasks', 'anchor'):
        project.add_argument('--' + key, required=True)
    project.add_argument('--namespace', default='handoff')
    project.add_argument('--apply', action='store_true')
    project.add_argument('--expected-sha256')
    project.add_argument('--previous-anchor')
    project.add_argument('--previous-anchor-sha256')
    args = parser.parse_args(argv)
    try:
        result = project_handoff(args.vault, args.log, args.tasks, args.anchor, namespace=args.namespace,
                                 apply=args.apply, expected_sha256=args.expected_sha256,
                                 previous_anchor_path=args.previous_anchor,
                                 previous_anchor_sha256=args.previous_anchor_sha256)
        print(_encode(result).decode())
        return 0
    except (StateError, EvidenceError, HealthcheckError, ValueError, OSError, UnicodeError):
        print('{"error":"state_operation_unavailable"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
