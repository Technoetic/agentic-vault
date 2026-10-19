"""Content-free failure classification for concurrent run-receipt test children.

Test support only; nothing in the engine imports it. The concurrent-prepare test
in test_vault_runs.py fails intermittently on Windows and its root cause is not
measured. This module turns each failing child into a signature that can be
compared across runs: exception class names, errno, winerror, the innermost
code site and a path category derived from the engine's runtime layout
(vault_state.RUNTIME_DIR, the advisory lock and guard names, the staged
temporary prefix and the run receipt names). It never returns a raw path, note
text or a free-form exception message.

A retry is deliberately not added here or in the engine. One is considered only
after the lock path's delete-pending state is confirmed: access denied (winerror
5, or EACCES without winerror from os.open's C runtime mapping) on a lock or
guard file. Even then it reuses the existing bounded schedule
(vault_state.REPLACE_RETRY_DELAYS) instead of a new one.
"""
from __future__ import annotations

import builtins
import errno as errno_codes
import json
import ntpath
import re

RUNTIME_DIR = '00-meta/.agentic-vault/runtime'
CATEGORIES = ('lock', 'temp', 'receipt', 'manifest', 'other')
MAX_CHAIN = 8
MAX_FAILURES = 16
_RUNTIME_PARTS = tuple(part.casefold() for part in RUNTIME_DIR.split('/'))
_HEX = '[0-9a-f]{64}'
_PATTERNS = (
    ('lock', 'guard', re.compile('lock-' + _HEX + r'\.json\.guard')),
    ('lock', 'lease', re.compile('lock-' + _HEX + r'\.json')),
    ('manifest', None, re.compile('run-' + _HEX + r'-prepared\.json')),
    ('receipt', None, re.compile('run-' + _HEX + r'-completed\.json')),
    ('receipt', None, re.compile('run-compare-' + _HEX + r'\.json')),
)
TEMP_PREFIX = '.vault-state-'
_CODE_RE = re.compile(r'[a-z_]{1,80}')
_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}')
_CRASH_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,3})(?::|$)')
ENGINE_TYPES = frozenset({'RunError', 'StateError', 'EvidenceError', 'HealthcheckError'})
BUILTIN_TYPES = frozenset(name for name, value in vars(builtins).items()
                          if isinstance(value, type) and issubclass(value, BaseException))


def _parts(path):
    if isinstance(path, bytes) or path is None:
        return None
    try:
        text = str(path)
    except Exception:  # noqa: BLE001 - an unprintable value is simply unknown
        return None
    if not text or '\x00' in text:
        return None
    # ntpath splits on both separators, so POSIX and Windows layouts share one rule.
    drive, rest = ntpath.splitdrive(text)
    return [part for part in re.split(r'[\\/]+', rest) if part]


def _match(path):
    parts = _parts(path)
    if not parts:
        return 'other', None
    name = parts[-1].casefold()
    if name.startswith(TEMP_PREFIX):
        # _atomic stages next to its target, so the prefix names a temp anywhere.
        return 'temp', None
    if tuple(part.casefold() for part in parts[-1 - len(_RUNTIME_PARTS):-1]) != _RUNTIME_PARTS:
        return 'other', None
    for category, lock_part, pattern in _PATTERNS:
        if pattern.fullmatch(name):
            return category, lock_part
    return 'other', None


def path_category(path):
    """Return one of CATEGORIES for a filesystem path; unknown or missing is 'other'."""
    return _match(path)[0]


def lock_file(path):
    """Return 'guard' or 'lease' for a lock path, else None."""
    return _match(path)[1]


def _type_name(error):
    name = type(error).__name__
    return name if name in BUILTIN_TYPES or name in ENGINE_TYPES else 'other'


def _site(error):
    tb = getattr(error, '__traceback__', None)
    if tb is None:
        return None
    while tb.tb_next is not None:
        tb = tb.tb_next
    code = tb.tb_frame.f_code
    function = code.co_name if _NAME_RE.fullmatch(code.co_name) else None
    module = ntpath.basename(code.co_filename.replace('/', '\\'))
    module = module[:-3] if module.endswith('.py') else None
    if function is None:
        return None
    return module + '.' + function if module and _NAME_RE.fullmatch(module) else function


def _integer(value):
    return value if type(value) is int else None


def classify(error):
    """Return a bounded, content-free description of an exception chain.

    Follows __cause__, else the implicit __context__ (also when suppressed with
    `from None`, as _atomic does for replace failures), for at most MAX_CHAIN links.
    """
    chain, seen, via = [], set(), None
    while error is not None and len(chain) < MAX_CHAIN and id(error) not in seen:
        seen.add(id(error))
        message = str(error) if not isinstance(error, OSError) else ''
        entry = {'type': _type_name(error),
                 'code': message if _CODE_RE.fullmatch(message) else None,
                 'errno': _integer(getattr(error, 'errno', None)),
                 'winerror': _integer(getattr(error, 'winerror', None)),
                 'site': _site(error)}
        if isinstance(error, OSError):
            entry['path'], part = _match(error.filename)
            if part:
                entry['lock_file'] = part
            if error.filename2 is not None:
                entry['path2'] = path_category(error.filename2)
        if via:
            entry['via'] = via
        chain.append(entry)
        if error.__cause__ is not None:
            error, via = error.__cause__, 'cause'
        else:
            error, via = error.__context__, 'context'
    return chain


def lock_access_denied(chain):
    """True when access was denied on a lock or guard path (delete-pending candidate)."""
    return any(entry.get('path') == 'lock'
               and (entry.get('winerror') == 5
                    or (entry.get('winerror') is None and entry.get('errno') == errno_codes.EACCES))
               for entry in chain)


def summarize(chain):
    """One line naming the deepest OS-level link, the engine codes and the suspicion flag."""
    if not chain:
        return 'no_exception'
    os_entries = [entry for entry in chain if 'path' in entry]
    deepest = os_entries[-1] if os_entries else chain[-1]
    words = [deepest['type'], 'errno=%s' % deepest['errno'], 'winerror=%s' % deepest['winerror']]
    if 'path' in deepest:
        path = deepest['path']
        if deepest.get('lock_file'):
            path += '(' + deepest['lock_file'] + ')'
        words.append('path=' + path)
        if 'path2' in deepest:
            words.append('path2=' + deepest['path2'])
    if deepest.get('site'):
        words.append('site=' + deepest['site'])
    codes = [entry['code'] for entry in chain if entry['code']]
    if codes:
        words.append('codes=' + '>'.join(codes))
    if lock_access_denied(chain):
        words.append('lock_access_denied')
    return ' '.join(words)


def crash_class(stderr):
    """Exception class from the last line of an uncaught traceback, else 'unknown'."""
    lines = [line for line in (stderr or '').splitlines() if line.strip()]
    if not lines or not any(line.startswith('Traceback') for line in lines):
        return 'unknown'
    match = _CRASH_RE.match(lines[-1])
    if not match:
        return 'unknown'
    name = match.group(1).rsplit('.', 1)[-1]
    return name if name in BUILTIN_TYPES or name in ENGINE_TYPES else 'other'


def child_failure(returncode, stdout, stderr):
    """None for a well-formed child result, else a content-free failure record."""
    try:
        value = json.loads(stdout)
    except (TypeError, ValueError):
        value = None
    if returncode == 2 and isinstance(value, dict) and isinstance(value.get('unexpected_error'), list):
        chain = value['unexpected_error'][:MAX_CHAIN]
        return {'failure': 'unexpected_error', 'summary': value.get('summary'), 'chain': chain}
    if returncode != 0:
        return {'failure': 'crash', 'returncode': returncode if type(returncode) is int else None,
                'class': crash_class(stderr), 'json_stdout': isinstance(value, dict)}
    if stderr:
        return {'failure': 'stderr_output', 'class': crash_class(stderr)}
    if not isinstance(value, dict):
        return {'failure': 'invalid_output'}
    return None


def report(label, failures, total):
    """Assertion message for failing children; bounded and content-free."""
    shown = failures[:MAX_FAILURES]
    denied = sum(1 for failure in failures if failure.get('chain') is not None
                 and lock_access_denied(failure['chain']))
    lines = ['%s: %d of %d children failed; lock_access_denied=%d'
             % (label, len(failures), total, denied)]
    for failure in shown:
        brief = failure.get('summary') or failure.get('class') or ''
        lines.append('  child %s: %s %s' % (failure.get('child'), failure['failure'], brief))
    lines.append(json.dumps(shown, sort_keys=True))
    return '\n'.join(lines)
