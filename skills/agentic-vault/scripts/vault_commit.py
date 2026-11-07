#!/usr/bin/env python3
"""Explicit commit of declared files for vaults shared by concurrent sessions.

Off by default: nothing runs this command unless a person or agent calls it.
Every input path carries the sha256 of the working file the caller means to
commit, so a file rewritten by another writer is refused, not swept in.

Default mode runs `git add -- PATHS` then `git commit -- PATHS`, which leaves
other sessions' staged entries alone and keeps the repository's commit hooks.
The result is checked against the hash printed by that commit, never against a
fresh look at HEAD, because another session may already have moved HEAD.

--own-lines is for the shared notes (hot, handoff, log, index), whose working
file may hold another session's unfinished lines. It applies the caller's edits
to the file as it stands in HEAD, commits with commit-tree and a compare-and-swap
update-ref, and then moves the shared index entries to match. The pre-commit hook
runs on the proposed tree; commit-tree alone would skip it.

Refusals and failures are fixed codes in one JSON object on stdout. Paths and
note text stay out of it; `path_index` is the position of a refused --path.
Exit status: 0 committed, 1 refused or failed, 2 invalid arguments.

A commit tool, not a lock: it cannot stop a session that uses plain `git commit`,
which writes the whole shared index as the next tree. See
docs/concurrent-commits.md for the measured conditions and the boundaries.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

from vault_evidence import EvidenceError, decode, encode, stable_read
from vault_healthcheck import HealthcheckError, validate_config
from vault_paths import relative_parts, resolve_note_path


ABSENT = 'absent'
MAX_PATHS = 256
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_CONFIG_BYTES = 256 * 1024
MAX_PATCH_BYTES = 1024 * 1024
MAX_EDITS = 64
MAX_MESSAGE_CHARACTERS = 1000
MAX_ACTOR_CHARACTERS = 80
GIT_TIMEOUT_SECONDS = 600
BASE_DELAY_MS = 20
# One lock-failure budget per commit, measured on synthetic repositories under
# extreme load (no pause between commits, 4 to 6 writers). With a 300 ms hook, 5
# attempts finished 54 of 240 commits; 60 attempts finished 598 of 600, and both
# misses were vanished stage entries, which the restart from `git add` now
# handles. With a heavier hook and a 5,000-entry index, one of 144 commits still
# used all 60 attempts, about 21 seconds of waiting, so this is a default and not
# a guarantee. docs/concurrent-commits.md holds the conditions.
DEFAULT_MAX_ATTEMPTS = 60
DEFAULT_MAX_DELAY_MS = 1000
MAX_ATTEMPTS_LIMIT = 1000
MAX_DELAY_LIMIT_MS = 60000

SHARED_NOTE_KEYS = ('hot_note', 'handoff_note', 'log_note', 'index_note')
OPERATION_MARKERS = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
                     'rebase-merge', 'rebase-apply')
# Variables that would point this tool at another index or repository, or change
# how a pathspec is read, when it starts inside a hook or a scripted session.
CLEARED_ENVIRONMENT = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_PREFIX',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE',
    'GIT_LITERAL_PATHSPECS', 'GIT_GLOB_PATHSPECS', 'GIT_NOGLOB_PATHSPECS',
    'GIT_ICASE_PATHSPECS',
)
LOCK_MARKERS = (b'index.lock', b'head.lock', b'cannot lock ref', b".lock': file exists",
                b'index file open failed', b'unable to write new index file')
PATHSPEC_MARKERS = (b'did not match any file',)
NOTHING_MARKERS = (b'nothing to commit', b'nothing added to commit',
                   b'no changes added to commit')
# core.abbrev=40 makes the commit summary carry a full hash on SHA-1 and a prefix
# that cannot collide on SHA-256. The summary line is the last one that matches,
# since pre-commit and post-commit hooks print to the same stream first.
COMMIT_LINE = re.compile(rb'^\[[^\n]*? ([0-9a-f]{40,64})\] ', re.MULTILINE)
HASH_RE = re.compile(r'[0-9A-Fa-f]{64}')
CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
TREE_LINE = re.compile(rb'([0-7]{6}) (blob|tree|commit) ([0-9a-f]{40,64})\t')
STAGE_LINE = re.compile(rb'([0-7]{6}) ([0-9a-f]{40,64}) ([0-3])\t')

SINGLE_OPTIONS = ('--vault', '--message', '--actor', '--max-attempts', '--max-delay-ms',
                  '--own-lines')

ERRORS = (EvidenceError, HealthcheckError, OSError, ValueError, TypeError, KeyError,
          RecursionError, OverflowError)


class _Stop(Exception):
    """End the run with a fixed status and reason."""

    def __init__(self, status, reason, path_index=None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.path_index = path_index


class _ArgumentError(Exception):
    pass


def _refuse(reason, path_index=None):
    raise _Stop('refused', reason, path_index)


def _fail(reason):
    raise _Stop('failed', reason)


def _git_environment(extra=None):
    environment = {key: value for key, value in os.environ.items()
                   if key not in CLEARED_ENVIRONMENT}
    # Failures are told apart by git's message text, which is translated otherwise.
    environment.update(LC_ALL='C', LANGUAGE='C')
    environment.update(extra or {})
    return environment


def _run_git(vault, args, *, env=None, stdin=None):
    """Run git in the vault. Pathspec literals stay per path (see _specs)."""
    return subprocess.run(
        ['git', '-c', 'core.abbrev=40', '-c', 'core.quotepath=false', *args],
        cwd=vault, env=_git_environment(env), input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def classify_failure(text):
    """Return 'lock', 'pathspec', 'nothing' or None for git's combined output."""
    lowered = text.lower()
    if any(marker in lowered for marker in LOCK_MARKERS):
        return 'lock'
    if any(marker in lowered for marker in PATHSPEC_MARKERS):
        return 'pathspec'
    if any(marker in lowered for marker in NOTHING_MARKERS):
        return 'nothing'
    return None


def _fold(relative):
    return unicodedata.normalize('NFC', relative).casefold()


class _Item:
    def __init__(self, rel, expected, position):
        self.rel = rel
        self.expected = expected
        self.position = position
        self.edits = []


class _State:
    def __init__(self):
        self.status = 'failed'
        self.reason = None
        self.commit = None
        self.path_index = None
        self.attempts = 0
        self.lock_failures = 0
        self.pathspec_restarts = 0
        self.head_conflicts = 0
        self.left_staged = False
        self.lock = None
        self.declared = 0
        self.actual = 0
        self.missing = 0
        self.undeclared = 0
        self.content_mismatch = 0
        self.staged_replaced = 0

    def report(self):
        return {'type': 'vault_commit', 'schema_version': 1, 'status': self.status,
                'reason': self.reason, 'commit': self.commit, 'path_index': self.path_index,
                'attempts': self.attempts, 'lock_failures': self.lock_failures,
                'pathspec_restarts': self.pathspec_restarts,
                'head_conflicts': self.head_conflicts, 'left_staged': self.left_staged,
                'lock': self.lock, 'declared': self.declared, 'actual': self.actual,
                'missing': self.missing, 'undeclared': self.undeclared,
                'content_mismatch': self.content_mismatch,
                'staged_replaced': self.staged_replaced}


class _Context:
    def __init__(self, vault, message, actor, max_attempts, max_delay_ms, sleep, capture):
        self.vault = vault
        self.message = message
        self.actor = actor
        self.trailer = f'Vault-Actor: {actor}'
        self.max_attempts = max_attempts
        self.max_delay_ms = max_delay_ms
        self.sleep = sleep
        self.capture = capture
        self.items = []
        self.denied = ()
        self.git_dir = None

    def git(self, args, *, env=None, stdin=None):
        try:
            result = _run_git(self.vault, args, env=env, stdin=stdin)
        except subprocess.TimeoutExpired as exc:
            raise _Stop('failed', 'git_timeout') from exc
        except OSError as exc:
            raise _Stop('failed', 'git_unavailable') from exc
        if result.returncode != 0 and self.capture is not None:
            self.capture.append(result.stdout + result.stderr)
        return result

    def backoff(self, attempt):
        if attempt < self.max_attempts:
            ceiling = min(self.max_delay_ms, BASE_DELAY_MS << min(attempt - 1, 16))
            self.sleep(ceiling * random.random() / 1000)


def _specs(items):
    # Per-path magic and not `git --literal-pathspecs`: that option is exported to
    # hooks and would turn the pathspec magic of the pre-commit gate into plain
    # text, so the gate would match nothing and pass.
    return [f':(literal){item.rel}' for item in items]


def _valid_message(message):
    if (not isinstance(message, str) or not message or message != message.strip()
            or len(message) > MAX_MESSAGE_CHARACTERS or CONTROL_RE.search(message)):
        return False
    try:
        message.encode('utf-8')
    except UnicodeError:
        return False
    return True


def _valid_actor(actor):
    return _valid_message(actor) and len(actor) <= MAX_ACTOR_CHARACTERS


def _load_config(vault):
    try:
        raw = stable_read(
            lambda: resolve_note_path(vault, '00-meta/vault-config.json', ('.git',)),
            MAX_CONFIG_BYTES, contents=True)[2]
        return validate_config(decode(raw))
    except ERRORS as exc:
        raise _Stop('refused', 'invalid_vault_config') from exc


def _resolve(context, relative, position):
    try:
        return resolve_note_path(context.vault, relative, context.denied)
    except ValueError as exc:
        reason = 'denied_path' if 'denied zone' in str(exc) else 'unsafe_path'
        raise _Stop('refused', reason, position) from exc


def _parse_items(context, entries):
    if not isinstance(entries, (list, tuple)) or not entries:
        _refuse('no_paths')
    if len(entries) > MAX_PATHS:
        _refuse('too_many_paths')
    seen = set()
    for position, (raw, expected) in enumerate(entries):
        try:
            parts = relative_parts(raw, 'path')
        except ValueError as exc:
            raise _Stop('refused', 'unsafe_path', position) from exc
        relative = '/'.join(parts)
        if not isinstance(expected, str) or not (
                expected == ABSENT or HASH_RE.fullmatch(expected)):
            _refuse('invalid_hash', position)
        path = _resolve(context, relative, position)
        if path.is_dir():
            _refuse('folder_path', position)
        folded = _fold(relative)
        if folded in seen:
            _refuse('duplicate_path', position)
        seen.add(folded)
        context.items.append(_Item(relative, expected.lower(), position))


def _attach_edits(context, edits, shared):
    if edits is None:
        return
    if not isinstance(edits, list) or not edits or len(edits) > MAX_EDITS:
        _refuse('invalid_patch')
    declared = {_fold(item.rel): item for item in context.items}
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'path', 'old', 'new'}
                or not all(isinstance(edit[key], str) for key in edit) or not edit['old']):
            _refuse('invalid_patch')
        folded = _fold(edit['path'].replace('\\', '/'))
        if folded not in shared:
            _refuse('own_lines_not_shared')
        if folded not in declared:
            _refuse('own_lines_undeclared')
        item = declared[folded]
        if item.expected == ABSENT:
            _refuse('invalid_patch', item.position)
        try:
            item.edits.append((edit['old'].encode('utf-8'), edit['new'].encode('utf-8')))
        except UnicodeError as exc:
            raise _Stop('refused', 'invalid_patch') from exc


def _read_patch(context, patch_file):
    path = Path(patch_file).resolve()
    try:
        relative = path.relative_to(context.vault.resolve())
    except ValueError:
        relative = None
    if relative is not None:
        path = _resolve(context, relative.as_posix(), None)
    try:
        value = decode(stable_read(lambda: path, MAX_PATCH_BYTES, contents=True)[2])
    except (EvidenceError, OSError) as exc:
        raise _Stop('refused', 'invalid_patch') from exc
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'edits'}
            or value['schema_version'] != 1):
        _refuse('invalid_patch')
    return value['edits']


def _open_repository(context):
    result = context.git(['rev-parse', '--is-inside-work-tree', '--show-prefix',
                          '--absolute-git-dir'])
    lines = result.stdout.decode('utf-8', 'surrogateescape').splitlines()
    if result.returncode != 0 or len(lines) != 3 or lines[0] != 'true':
        _refuse('not_a_repository')
    if lines[1]:
        _refuse('vault_not_repository_root')
    context.git_dir = Path(lines[2])


def _tree_entry(context, revision, relative):
    """Return (mode, type, oid) for the exact path, or None when it is not there."""
    result = context.git(['ls-tree', '-z', revision, '--', relative])
    if result.returncode != 0 or not result.stdout:
        return None
    match = TREE_LINE.match(result.stdout)
    if not match:
        return None
    return (match.group(1).decode('ascii'), match.group(2).decode('ascii'),
            match.group(3).decode('ascii'))


def _check_absent_markers(context):
    for item in context.items:
        if item.expected != ABSENT:
            continue
        entry = _tree_entry(context, 'HEAD', item.rel)
        if entry is None:
            _refuse('absent_not_tracked', item.position)
        if entry[1] != 'blob':
            _refuse('folder_path', item.position)


def _require_no_operation(context):
    if any((context.git_dir / marker).exists() for marker in OPERATION_MARKERS):
        _refuse('operation_in_progress')


def _read_worktree(context, item):
    try:
        return stable_read(lambda: _resolve(context, item.rel, item.position),
                           MAX_FILE_BYTES, contents=True)
    except FileNotFoundError:
        return None
    except EvidenceError as exc:
        code = str(exc)
        if code == 'input_too_large':
            raise _Stop('refused', 'file_too_large', item.position) from exc
        if code == 'unsafe_file':
            raise _Stop('refused', 'unsafe_path', item.position) from exc
        raise _Stop('refused', 'hash_mismatch', item.position) from exc


def _check_files(context):
    """Refuse when a working file differs from its declared hash; return the bytes."""
    contents = {}
    for item in context.items:
        snapshot = _read_worktree(context, item)
        if snapshot is None:
            if item.expected != ABSENT:
                _refuse('hash_mismatch', item.position)
            contents[item.rel] = None
        elif item.expected == ABSENT or snapshot[0]['sha256'] != item.expected:
            _refuse('hash_mismatch', item.position)
        else:
            contents[item.rel] = snapshot[2]
    return contents


def _blob_oid(context, relative, data, *, write):
    args = ['hash-object', '--stdin', f'--path={relative}']
    if write:
        args.append('-w')
    result = context.git(args, stdin=data)
    if result.returncode != 0:
        _fail('blob_hash_failed')
    return result.stdout.decode('ascii').strip()


def _lock_state(context):
    try:
        metadata = (context.git_dir / 'index.lock').stat()
    except OSError:
        return {'present': False, 'age_seconds': None}
    return {'present': True, 'age_seconds': max(0, int(time.time() - metadata.st_mtime))}


def _parse_commit_hash(output):
    matches = COMMIT_LINE.findall(output)
    return matches[-1].decode('ascii') if matches else None


def _committed_paths(context, commit):
    result = context.git(['diff-tree', '--no-commit-id', '--root', '-r', '-z',
                          '--name-only', '--no-renames', commit])
    if result.returncode != 0:
        raise _Stop('committed_mismatch', 'verification_unavailable')
    return {part.decode('utf-8', 'surrogateescape')
            for part in result.stdout.split(b'\0') if part}


def _commit_subject(context, commit):
    result = context.git(['cat-file', 'commit', commit])
    if result.returncode != 0:
        raise _Stop('committed_mismatch', 'verification_unavailable')
    body = result.stdout.partition(b'\n\n')[2]
    return body.split(b'\n', 1)[0]


def _verify(context, state, commit, expected):
    """Compare what the named commit changed with what the caller declared."""
    declared = {item.rel for item in context.items}
    actual = _committed_paths(context, commit)
    state.declared, state.actual = len(declared), len(actual)
    state.missing = len(declared - actual)
    state.undeclared = len(actual - declared)
    for item in context.items:
        entry = _tree_entry(context, commit, item.rel)
        oid = entry[2] if entry is not None else None
        if oid != expected[item.rel]:
            state.content_mismatch += 1
    if state.missing or state.undeclared:
        raise _Stop('committed_mismatch', 'paths_differ')
    if state.content_mismatch:
        raise _Stop('committed_mismatch', 'content_differs')
    if _commit_subject(context, commit) != context.message.encode('utf-8'):
        raise _Stop('committed_mismatch', 'message_differs')


def _path_mode(context, state):
    specs = _specs(context.items)
    expected = None
    last_kind = None
    for attempt in range(1, context.max_attempts + 1):
        state.attempts = attempt
        _require_no_operation(context)
        contents = _check_files(context)
        if expected is None:
            expected = {relative: None if data is None
                        else _blob_oid(context, relative, data, write=False)
                        for relative, data in contents.items()}
        result = context.git(['add', '--', *specs])
        stage = 'add'
        if result.returncode == 0:
            state.left_staged = True
            result = context.git(['commit', '-m', context.message, '-m', context.trailer,
                                  '--', *specs])
            stage = 'commit'
            if result.returncode == 0:
                state.left_staged = False
                commit = _parse_commit_hash(result.stdout)
                if commit is None:
                    raise _Stop('committed_mismatch', 'commit_hash_unreadable')
                state.commit = commit
                _verify(context, state, commit, expected)
                return
        kind = classify_failure(result.stdout + result.stderr)
        last_kind = kind
        if kind == 'lock':
            state.lock_failures += 1
        elif kind == 'pathspec':
            # The stage entry itself can vanish under a concurrent commit, so
            # retrying only the commit never recovers: start again from git add.
            state.pathspec_restarts += 1
        elif kind == 'nothing':
            state.left_staged = False
            _fail('nothing_to_commit')
        else:
            _fail(f'{stage}_rejected')
        context.backoff(attempt)
    if last_kind == 'lock':
        state.lock = _lock_state(context)
        _fail('index_lock_timeout')
    _fail('pathspec_retries_exhausted')


def _apply_edits(base, edits, working):
    text = base
    for old, new in edits:
        if text.count(old) != 1:
            _refuse('patch_anchor_not_unique')
        text = text.replace(old, new, 1)
    folded = working.replace(b'\r\n', b'\n')
    for _, new in edits:
        if new.replace(b'\r\n', b'\n') not in folded:
            # Lines committed here but missing from the working file would be
            # reverted by the next `git add`.
            _refuse('patch_not_in_worktree')
    return text


def _resolve_head(context):
    result = context.git(['rev-parse', '--verify', '--quiet', 'HEAD^{commit}'])
    if result.returncode != 0:
        return None
    return result.stdout.decode('ascii').strip()


def _is_ignored(context, relative):
    # update-index takes any path; git add is what normally honours .gitignore.
    return context.git(['check-ignore', '-q', '--', relative]).returncode == 0


def _build_tree(context, head, contents, index):
    """Write HEAD plus the declared changes to a scratch index; return the plan."""
    scratch = {'GIT_INDEX_FILE': index}
    if context.git(['read-tree', head], env=scratch).returncode != 0:
        _fail('index_build_failed')
    planned = {}
    lines = []
    for item in context.items:
        data = contents[item.rel]
        entry = _tree_entry(context, head, item.rel)
        if data is None:
            lines.append((b'0', None, item.rel))
            planned[item.rel] = None
            continue
        if entry is None and _is_ignored(context, item.rel):
            _fail('add_rejected')
        mode = entry[0] if entry is not None and entry[1] == 'blob' else '100644'
        if item.edits:
            if entry is None or entry[1] != 'blob':
                _refuse('patch_anchor_not_unique', item.position)
            base = context.git(['cat-file', 'blob', entry[2]])
            if base.returncode != 0:
                _fail('index_build_failed')
            patched = _apply_edits(base.stdout, item.edits, data)
            result = context.git(['hash-object', '-w', '--stdin'], stdin=patched)
            if result.returncode != 0:
                _fail('blob_hash_failed')
            oid = result.stdout.decode('ascii').strip()
        else:
            oid = _blob_oid(context, item.rel, data, write=True)
        lines.append((mode.encode('ascii'), oid, item.rel))
        planned[item.rel] = oid
    payload = _index_info(lines, len(head))
    if context.git(['update-index', '-z', '--index-info'], env=scratch,
                   stdin=payload).returncode != 0:
        _fail('index_build_failed')
    result = context.git(['write-tree'], env=scratch)
    if result.returncode != 0:
        _fail('index_build_failed')
    return result.stdout.decode('ascii').strip(), planned


def _index_info(lines, oid_length):
    records = []
    for mode, oid, relative in lines:
        value = (oid or '0' * oid_length).encode('ascii')
        records.append(mode + b' ' + value + b'\t' + relative.encode('utf-8') + b'\0')
    return b''.join(records)


def _run_pre_commit(context, index):
    # commit-tree runs no hooks, so the gate runs here on the proposed tree.
    result = context.git(['hook', 'run', '--ignore-missing', 'pre-commit'],
                         env={'GIT_INDEX_FILE': index})
    if result.returncode == 0:
        return
    if result.returncode == 129 or b'not a git command' in result.stderr:
        _refuse('hook_runner_unavailable')
    _fail('commit_rejected')


def _count_replaced_stage(context, state, parent):
    for item in context.items:
        result = context.git(['ls-files', '-s', '-z', '--', *_specs([item])])
        match = STAGE_LINE.match(result.stdout) if result.returncode == 0 else None
        if match is None:
            continue
        entry = _tree_entry(context, parent, item.rel)
        before = entry[2] if entry is not None else None
        current = _tree_entry(context, 'HEAD', item.rel)
        after = current[2] if current is not None else None
        if match.group(2).decode('ascii') not in (before, after):
            state.staged_replaced += 1


def _sync_shared_index(context, state, parent):
    """Point the shared index at HEAD for the declared paths.

    Without this the next plain `git commit` would write the old entry and revert
    the commit. A slower writer must not leave its older blob behind a newer one,
    so the entries are read from HEAD and the write counts only if HEAD is still
    that commit afterwards. Whichever writer moves HEAD last settles the entries.
    """
    _count_replaced_stage(context, state, parent)
    for attempt in range(1, context.max_attempts + 1):
        head = _resolve_head(context)
        lines = []
        for item in context.items:
            entry = _tree_entry(context, head, item.rel)
            if entry is None:
                lines.append((b'0', None, item.rel))
            else:
                lines.append((entry[0].encode('ascii'), entry[2], item.rel))
        result = context.git(['update-index', '-z', '--index-info'],
                             stdin=_index_info(lines, len(head)))
        if result.returncode == 0:
            if _resolve_head(context) == head:
                return
        elif classify_failure(result.stdout + result.stderr) == 'lock':
            state.lock_failures += 1
        else:
            break
        context.backoff(attempt)
    state.lock = _lock_state(context)
    raise _Stop('committed_index_stale', 'shared_index_not_updated')


def _tree_mode(context, state):
    with tempfile.TemporaryDirectory(prefix='vault-commit-', dir=context.git_dir,
                                     ignore_cleanup_errors=True) as scratch:
        for attempt in range(1, context.max_attempts + 1):
            state.attempts = attempt
            _require_no_operation(context)
            contents = _check_files(context)
            head = _resolve_head(context)
            if head is None:
                _refuse('no_head')
            index = os.path.join(scratch, f'index-{attempt}')
            tree, planned = _build_tree(context, head, contents, index)
            result = context.git(['rev-parse', f'{head}^{{tree}}'])
            if result.returncode == 0 and result.stdout.decode('ascii').strip() == tree:
                _fail('nothing_to_commit')
            _run_pre_commit(context, index)
            result = context.git(['commit-tree', tree, '-p', head, '-m', context.message,
                                  '-m', context.trailer])
            if result.returncode != 0:
                _fail('commit_rejected')
            commit = result.stdout.decode('ascii').strip()
            result = context.git(['update-ref', '-m', 'vault-commit', 'HEAD', commit, head])
            if result.returncode == 0:
                state.commit = commit
                stale = None
                try:
                    _sync_shared_index(context, state, head)
                except _Stop as stop:
                    stale = stop
                try:
                    _verify(context, state, commit, planned)
                except _Stop:
                    if stale is None:
                        raise
                if stale is not None:
                    raise stale
                return
            if classify_failure(result.stdout + result.stderr) != 'lock':
                _fail('ref_update_failed')
            state.head_conflicts += 1
            context.backoff(attempt)
    _fail('head_conflict_timeout')


def _prepare(vault, entries, message, actor, edits, patch_file, max_attempts,
             max_delay_ms, sleep, capture):
    if (type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ATTEMPTS_LIMIT
            or type(max_delay_ms) is not int
            or not 0 <= max_delay_ms <= MAX_DELAY_LIMIT_MS):
        _refuse('invalid_retry_limit')
    if not _valid_message(message):
        _refuse('invalid_message')
    if not _valid_actor(actor):
        _refuse('invalid_actor')
    context = _Context(Path(vault), message, actor, max_attempts, max_delay_ms, sleep, capture)
    config = _load_config(context.vault)
    context.denied = (*config['deny_zones'], '.git')
    _parse_items(context, entries)
    shared = {_fold(config[key]) for key in SHARED_NOTE_KEYS if config.get(key)}
    if patch_file is not None:
        edits = _read_patch(context, patch_file)
    _attach_edits(context, edits, shared)
    for item in context.items:
        if _fold(item.rel) in shared and not item.edits:
            _refuse('shared_file_refused', item.position)
    _open_repository(context)
    _check_absent_markers(context)
    return context


def commit_paths(vault, entries, message, actor, *, edits=None, patch_file=None,
                 max_attempts=DEFAULT_MAX_ATTEMPTS, max_delay_ms=DEFAULT_MAX_DELAY_MS,
                 sleep=time.sleep, capture=None):
    """Commit (path, sha256-or-ABSENT) entries and return the report dictionary."""
    state = _State()
    try:
        context = _prepare(vault, entries, message, actor, edits, patch_file,
                           max_attempts, max_delay_ms, sleep, capture)
        if edits is not None or patch_file is not None:
            _tree_mode(context, state)
        else:
            _path_mode(context, state)
        state.status = 'committed'
    except _Stop as stop:
        state.status, state.reason = stop.status, stop.reason
        state.path_index = stop.path_index
    except ERRORS:
        state.reason = 'verification_unavailable' if state.commit else 'invalid_or_changed_input'
        state.status = 'committed_mismatch' if state.commit else 'failed'
    return state.report()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError(message)


def build_parser():
    parser = _Parser(description=__doc__, allow_abbrev=False,
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vault', required=True, type=Path)
    parser.add_argument('--path', required=True, action='append', dest='paths',
                        metavar='RELATIVE=SHA256',
                        help=f'working-file sha256, or {ABSENT} for a deletion; repeatable')
    parser.add_argument('--message', required=True, help='one-line commit message')
    parser.add_argument('--actor', required=True, help='recorded as a Vault-Actor trailer')
    parser.add_argument('--max-attempts', type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument('--max-delay-ms', type=int, default=DEFAULT_MAX_DELAY_MS)
    parser.add_argument('--own-lines', metavar='PATCH.json',
                        help='commit HEAD plus these edits for shared notes')
    parser.add_argument('--show-git-output', action='store_true',
                        help='copy failing git output, which may hold paths, to stderr')
    return parser


def _parse_entry(value):
    relative, separator, expected = value.rpartition('=')
    if not separator or not relative:
        raise _ArgumentError('invalid path entry')
    return relative, expected


def main(argv=None):
    tokens = list(sys.argv[1:] if argv is None else argv)
    try:
        for flag in SINGLE_OPTIONS:
            if sum(token == flag or token.startswith(flag + '=') for token in tokens) > 1:
                raise _ArgumentError('duplicate option')
        args = build_parser().parse_args(tokens)
        entries = [_parse_entry(value) for value in args.paths]
    except _ArgumentError:
        result = _State()
        result.reason = 'invalid_arguments'
        print(encode(result.report()).decode('ascii'))
        return 2
    capture = [] if args.show_git_output else None
    report = commit_paths(args.vault, entries, args.message, args.actor,
                          patch_file=args.own_lines, max_attempts=args.max_attempts,
                          max_delay_ms=args.max_delay_ms, capture=capture)
    print(encode(report).decode('ascii'))
    if capture:
        print(capture[-1].decode('utf-8', 'replace'), file=sys.stderr)
    return 0 if report['status'] == 'committed' else 1


if __name__ == '__main__':
    sys.exit(main())
