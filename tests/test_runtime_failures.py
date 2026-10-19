"""Content-free classification of failures in concurrent run-receipt children.

The classifier names the exception class, errno, winerror and a path category
derived from the engine's runtime layout. It never returns a raw path, note text
or an arbitrary exception message.
"""
from __future__ import annotations

import errno
import importlib
import json
from pathlib import Path, PureWindowsPath, PurePosixPath
import sys
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'skills' / 'agentic-vault' / 'scripts'
for entry in (str(TESTS), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
try:
    failures = importlib.import_module('runtime_failures')
except ModuleNotFoundError:
    failures = None

SECRET_DIR = 'SYNTHETIC_PRIVATE_DIRECTORY_7c1f'
HEX = 'ab' * 32


def runtime(name, *, windows=True):
    base = (PureWindowsPath('C:/Users') if windows else PurePosixPath('/home')) / SECRET_DIR / 'vault'
    return str(base / '00-meta' / '.agentic-vault' / 'runtime' / name)


def os_error(cls, number, path, *, winerror=None, path2=None):
    error = cls(number, 'synthetic', path, None, path2) if path2 else cls(number, 'synthetic', path)
    if winerror is not None:
        error.winerror = winerror
    return error


def chained(inner, *, suppress=False):
    """Mimic the engine: RunError <- StateError <- OSError, optionally `from None`."""
    class StateError(Exception):
        pass

    class RunError(Exception):
        pass

    try:
        try:
            try:
                raise inner
            except OSError as exc:
                if suppress:
                    raise StateError('replace_failed') from None
                raise StateError('unsafe_lock') from exc
        except StateError as exc:
            raise RunError('unsafe_or_invalid_input') from exc
    except RunError as exc:
        return exc


class PathCategoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(failures, 'runtime failure classifier is missing')

    def test_engine_layout_constants_match_vault_state_and_vault_runs(self):
        state = importlib.import_module('vault_state')
        runs = importlib.import_module('vault_runs')
        self.assertEqual(failures.RUNTIME_DIR, state.RUNTIME_DIR)
        prepared, completed = runs._names(HEX)
        self.assertEqual(failures.path_category(runtime(prepared)), 'manifest')
        self.assertEqual(failures.path_category(runtime(completed)), 'receipt')

    def test_each_runtime_category_on_windows_and_posix_paths(self):
        cases = {
            'lock-' + HEX + '.json': ('lock', 'lease'),
            'lock-' + HEX + '.json.guard': ('lock', 'guard'),
            '.vault-state-k2j_9x': ('temp', None),
            'run-' + HEX + '-prepared.json': ('manifest', None),
            'run-' + HEX + '-completed.json': ('receipt', None),
            'run-compare-' + HEX + '.json': ('receipt', None),
            'checkpoint-' + HEX + '.json': ('other', None),
            'LOCK-' + HEX + '.JSON.GUARD': ('lock', 'guard'),
        }
        for name, (category, lock_file) in cases.items():
            for windows in (True, False):
                with self.subTest(name=name, windows=windows):
                    path = runtime(name, windows=windows)
                    self.assertEqual(failures.path_category(path), category)
                    self.assertEqual(failures.lock_file(path), lock_file)

    def test_paths_outside_the_runtime_directory_or_unknown_are_other(self):
        outside = str(PureWindowsPath('C:/Users') / SECRET_DIR / 'notes' / ('lock-' + HEX + '.json'))
        for value in (outside, runtime(''), None, 7, b'\xff', '', 'run-' + HEX + '-prepared.json',
                      runtime('lock-short.json'), runtime('run-' + HEX + '-other.json')):
            with self.subTest(value=repr(value)[:40]):
                self.assertEqual(failures.path_category(value), 'other')
        # A staged temporary file sits next to its target, so the prefix alone names it.
        note_temp = str(PureWindowsPath('C:/Users') / SECRET_DIR / 'notes' / '.vault-state-abc')
        self.assertEqual(failures.path_category(note_temp), 'temp')

    def test_extended_length_prefix_is_classified(self):
        path = '\\\\?\\' + runtime('lock-' + HEX + '.json.guard')
        self.assertEqual(failures.path_category(path), 'lock')


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(failures, 'runtime failure classifier is missing')

    def assert_content_free(self, value):
        text = json.dumps(value)
        for secret in (SECRET_DIR, 'synthetic', 'C:', '/home', 'agentic-vault', HEX):
            self.assertNotIn(secret, text)

    def test_winerror_5_on_guard_is_lock_access_denied(self):
        error = chained(os_error(PermissionError, errno.EACCES,
                                 runtime('lock-' + HEX + '.json.guard'), winerror=5))
        chain = failures.classify(error)
        self.assertEqual([entry['type'] for entry in chain],
                         ['RunError', 'StateError', 'PermissionError'])
        self.assertEqual([entry['code'] for entry in chain],
                         ['unsafe_or_invalid_input', 'unsafe_lock', None])
        last = chain[-1]
        self.assertEqual((last['errno'], last['winerror'], last['path'], last['lock_file']),
                         (errno.EACCES, 5, 'lock', 'guard'))
        self.assertEqual(last['via'], 'cause')
        self.assertTrue(failures.lock_access_denied(chain))
        self.assert_content_free(chain)
        summary = failures.summarize(chain)
        self.assertIn('PermissionError', summary)
        self.assertIn('errno=13', summary)
        self.assertIn('winerror=5', summary)
        self.assertIn('path=lock(guard)', summary)
        self.assertIn('lock_access_denied', summary)
        self.assert_content_free(summary)

    def test_crt_open_eacces_without_winerror_on_lock_also_counts(self):
        # os.open maps ERROR_ACCESS_DENIED to EACCES through the C runtime and
        # leaves winerror unset; the earlier diagnosis observed exactly this shape.
        error = chained(os_error(PermissionError, errno.EACCES, runtime('lock-' + HEX + '.json.guard')))
        chain = failures.classify(error)
        self.assertIsNone(chain[-1]['winerror'])
        self.assertTrue(failures.lock_access_denied(chain))

    def test_winerror_32_sharing_violation_is_not_lock_access_denied(self):
        error = chained(os_error(PermissionError, errno.EACCES,
                                 runtime('lock-' + HEX + '.json'), winerror=32))
        chain = failures.classify(error)
        self.assertEqual((chain[-1]['winerror'], chain[-1]['path'], chain[-1]['lock_file']),
                         (32, 'lock', 'lease'))
        self.assertFalse(failures.lock_access_denied(chain))
        self.assertIn('winerror=32', failures.summarize(chain))

    def test_access_denied_on_other_categories_is_not_lock_access_denied(self):
        for name, category in (('.vault-state-x1', 'temp'),
                               ('run-' + HEX + '-prepared.json', 'manifest'),
                               ('run-' + HEX + '-completed.json', 'receipt')):
            with self.subTest(category=category):
                chain = failures.classify(chained(os_error(
                    PermissionError, errno.EACCES, runtime(name), winerror=5)))
                self.assertEqual(chain[-1]['path'], category)
                self.assertNotIn('lock_file', chain[-1])
                self.assertFalse(failures.lock_access_denied(chain))
                self.assert_content_free(chain)

    def test_suppressed_context_is_followed_for_replace_failures(self):
        # _atomic raises StateError('replace_failed') from None; the OSError is
        # still the implicit context and carries both replace paths.
        inner = os_error(PermissionError, errno.EACCES, runtime('.vault-state-q'), winerror=5,
                         path2=runtime('run-' + HEX + '-completed.json'))
        chain = failures.classify(chained(inner, suppress=True))
        self.assertEqual(chain[1]['code'], 'replace_failed')
        self.assertEqual(chain[-1]['via'], 'context')
        self.assertEqual((chain[-1]['path'], chain[-1]['path2']), ('temp', 'receipt'))
        self.assert_content_free(chain)

    def test_errno_only_and_pathless_errors(self):
        chain = failures.classify(FileNotFoundError(errno.ENOENT, 'synthetic'))
        self.assertEqual((chain[0]['type'], chain[0]['errno'], chain[0]['path']),
                         ('FileNotFoundError', errno.ENOENT, 'other'))
        chain = failures.classify(ValueError('Synthetic note text ' + SECRET_DIR))
        self.assertEqual(chain, [{'type': 'ValueError', 'code': None, 'errno': None,
                                  'winerror': None, 'site': chain[0]['site']}])
        self.assert_content_free(chain)

    def test_unknown_exception_type_and_message_are_not_echoed(self):
        error = type('Synthetic' + SECRET_DIR, (Exception,), {})('not a code: ' + SECRET_DIR)
        chain = failures.classify(error)
        self.assertEqual(chain[0]['type'], 'other')
        self.assertIsNone(chain[0]['code'])
        self.assert_content_free(chain)

    def test_site_names_the_innermost_frame_without_paths(self):
        def _exclusive_probe():
            raise PermissionError(errno.EACCES, 'synthetic', runtime('lock-' + HEX + '.json.guard'))
        try:
            _exclusive_probe()
        except PermissionError as exc:
            chain = failures.classify(exc)
        self.assertEqual(chain[0]['site'], 'test_runtime_failures._exclusive_probe')
        self.assert_content_free(chain)

    def test_chain_is_bounded_and_cycles_stop(self):
        first = ValueError('a')
        second = ValueError('b')
        first.__cause__ = second
        second.__cause__ = first
        self.assertEqual(len(failures.classify(first)), 2)
        head = error = ValueError('x')
        for _ in range(50):
            nxt = ValueError('x')
            error.__cause__ = nxt
            error = nxt
        self.assertEqual(len(failures.classify(head)), failures.MAX_CHAIN)

    def test_child_output_summary_handles_crashes_and_timeouts(self):
        self.assertEqual(failures.crash_class('Traceback (most recent call last):\n  File "'
                                              + runtime('x') + '"\nPermissionError: [Errno 13] '
                                              + runtime('lock-' + HEX + '.json') + '\n'),
                         'PermissionError')
        self.assertEqual(failures.crash_class('garbage ' + SECRET_DIR), 'unknown')
        self.assertEqual(failures.crash_class(''), 'unknown')

    def test_child_failure_records_and_report_are_content_free(self):
        self.assertIsNone(failures.child_failure(0, '{"ok": true}', ''))
        self.assertIsNone(failures.child_failure(0, '{"ok": false, "error": "lock_busy"}', ''))
        chain = failures.classify(chained(os_error(PermissionError, errno.EACCES,
                                                   runtime('lock-' + HEX + '.json.guard'))))
        stdout = json.dumps({'unexpected_error': chain, 'summary': failures.summarize(chain)})
        records = [
            failures.child_failure(2, stdout, ''),
            failures.child_failure(1, '', 'Traceback (most recent call last):\nOSError: ' + runtime('x')),
            failures.child_failure(0, '{"ok": true}', 'warning at ' + runtime('x')),
            failures.child_failure(0, 'not json ' + SECRET_DIR, ''),
        ]
        self.assertEqual([record['failure'] for record in records],
                         ['unexpected_error', 'crash', 'stderr_output', 'invalid_output'])
        self.assertEqual(records[1]['class'], 'OSError')
        for index, record in enumerate(records):
            record['child'] = index
        message = failures.report('concurrent prepare', records, 4)
        self.assertTrue(message.startswith('concurrent prepare: 4 of 4 children failed; '
                                           'lock_access_denied=1'))
        self.assertIn('path=lock(guard)', message)
        self.assert_content_free(message)


if __name__ == '__main__':
    unittest.main()
