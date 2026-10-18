"""Real Windows sharing failures at the cooperative writer boundary."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import importlib
import ntpath
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
state = importlib.import_module('vault_state')
paths = importlib.import_module('vault_paths')


@contextmanager
def reader_without_delete_share(path):
    """An ordinary external reader which prevents MoveFileEx replacement."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


@unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
class WindowsStateReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # CI temp paths can include 8.3 aliases; mocks must match the resolved root.
        self.vault = Path(self.tmp.name).resolve(strict=True)
        self.config = self.write('00-meta/vault-config.json', b'{"deny_zones":["private"]}')
        self.target = self.write('notes/edit.md', b'old\r\n')
        self.source = self.write('notes/source.md', b'observed\n')

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def patch(self):
        return state.patch_note(self.vault, 'notes/edit.md', hashlib.sha256(b'old\r\n').hexdigest(),
                                [{'start': 0, 'end': 3, 'replacement': 'new'}],
                                source_hashes={'notes/source.md': hashlib.sha256(b'observed\n').hexdigest()},
                                expected_config_sha256=hashlib.sha256(self.config.read_bytes()).hexdigest())

    def assert_no_staged_files(self):
        self.assertEqual(list(self.target.parent.glob('.vault-state-*')), [])
        runtime = self.vault / state.RUNTIME_DIR
        self.assertEqual(list(runtime.glob('*.guard')), [])
        self.assertEqual(list(runtime.glob('lock-*.json')), [])

    def test_real_reader_released_after_sharing_failure_allows_revision_bound_edit(self):
        failed, closed = threading.Event(), threading.Event()
        acquired = threading.Event()
        thread_errors = []
        def reader():
            try:
                with reader_without_delete_share(self.target):
                    acquired.set()
                    if not failed.wait(5):
                        raise AssertionError('writer never reached the real sharing failure')
            except BaseException as error:
                thread_errors.append(error)
            finally:
                closed.set()
        thread = threading.Thread(target=reader)
        thread.start()
        self.addCleanup(lambda: (failed.set(), thread.join(5)))
        self.assertTrue(acquired.wait(5))
        real_replace = os.replace
        observed_codes = []
        def observe_replace(src, dst):
            try:
                return real_replace(src, dst)
            except OSError as error:
                observed_codes.append(error.winerror)
                failed.set()
                self.assertTrue(closed.wait(5))
                raise
        try:
            with mock.patch.object(state.os, 'replace', side_effect=observe_replace):
                result = self.patch()
        except OSError as error:
            self.fail(f'transient reader prevented edit: winerror={error.winerror}')
        self.assertFalse(thread_errors, thread_errors)
        self.assertTrue(observed_codes)
        self.assertTrue(all(code in (5, 32) for code in observed_codes))
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(self.target.read_bytes(), b'new\r\n')
        self.assert_no_staged_files()

    def test_persistent_real_reader_fails_bounded_with_sanitized_error_and_original(self):
        started = time.monotonic()
        with reader_without_delete_share(self.target):
            with self.assertRaises(state.StateError) as caught:
                self.patch()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(str(caught.exception), 'replace_failed')
        self.assertIn(caught.exception.winerror, (5, 32))
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(self.target.read_bytes(), b'old\r\n')
        self.assert_no_staged_files()

    def test_retry_rechecks_external_target_source_and_policy_changes(self):
        for changed in ('target', 'source', 'config'):
            with self.subTest(changed=changed):
                self.target.write_bytes(b'old\r\n')
                self.source.write_bytes(b'observed\n')
                self.config.write_bytes(b'{"deny_zones":["private"]}')
                real_replace = os.replace
                codes = []
                with reader_without_delete_share(self.target):
                    def replace_then_change(src, dst):
                        try:
                            return real_replace(src, dst)
                        except OSError as error:
                            codes.append(error.winerror)
                            selected = {'target': self.target, 'source': self.source, 'config': self.config}[changed]
                            selected.write_bytes(b'other writer\n' if changed != 'config'
                                                 else b'{"deny_zones":["private"],"vault_name":"changed"}')
                            raise
                    with mock.patch.object(state.os, 'replace', side_effect=replace_then_change):
                        with self.assertRaisesRegex(state.StateError,
                                                    {'target': 'stale_base', 'source': 'source_changed',
                                                     'config': 'configuration_changed'}[changed]):
                            self.patch()
                self.assertEqual(len(codes), 1)
                self.assertEqual(self.target.read_bytes(), b'other writer\n' if changed == 'target' else b'old\r\n')
                if changed != 'config':
                    self.assert_no_staged_files()
                else:
                    # Revoked policy intentionally retains staged/owner recovery evidence.
                    self.assertEqual(len(list(self.target.parent.glob('.vault-state-*'))), 1)
                    # Use a fresh runtime namespace for the next subcase/cleanup.
                    self.config.write_bytes(b'{"deny_zones":["private"]}')

    def test_nonretryable_replace_error_is_immediate_and_content_free(self):
        error = OSError('PRIVATE_CONTENT must never escape')
        error.winerror = 112
        with mock.patch.object(state.os, 'replace', side_effect=error), \
                mock.patch.object(state.time, 'sleep', side_effect=AssertionError('nonretryable error slept')):
            with self.assertRaises(state.StateError) as caught:
                self.patch()
        self.assertEqual(str(caught.exception), 'replace_failed')
        self.assertEqual(caught.exception.winerror, 112)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(self.target.read_bytes(), b'old\r\n')
        self.assert_no_staged_files()

    def test_checkpoint_replace_keeps_existing_receipt_when_reader_stays_open(self):
        first = state.write_checkpoint(self.vault, 'PreCompact', session_id='sharing-fixture')
        receipt = self.vault / first['path']
        before = receipt.read_bytes()
        with reader_without_delete_share(receipt):
            with self.assertRaises(state.StateError) as caught:
                state.write_checkpoint(self.vault, 'SessionEnd', session_id='sharing-fixture')
        self.assertEqual(str(caught.exception), 'replace_failed')
        self.assertIn(caught.exception.winerror, (5, 32))
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(list(receipt.parent.glob('.vault-state-*')), [])
        self.assertEqual(list(receipt.parent.glob('*.guard')), [])
        self.assertEqual(list(receipt.parent.glob('lock-*.json')), [])

    def test_posix_branch_preserves_original_os_error_without_retry(self):
        # Swap only vault_state's OS facade, leaving real Path operations alone.
        # This exercises the unchanged branch without pretending to run Linux.
        import types
        posix = types.SimpleNamespace(name='posix', fdopen=os.fdopen, fsync=os.fsync, fstat=os.fstat,
                                      chmod=os.chmod)
        original_error = OSError('controlled POSIX replacement failure')
        def fail(_temporary):
            raise original_error
        with mock.patch.object(state, 'os', posix), \
                mock.patch.object(state.time, 'sleep', side_effect=AssertionError('POSIX retried')):
            with self.assertRaises(OSError) as caught:
                state._atomic(self.target, b'new\r\n', fail)
        self.assertIs(caught.exception, original_error)
        self.assertEqual(self.target.read_bytes(), b'old\r\n')
        self.assertEqual(list(self.target.parent.glob('.vault-state-*')), [])

    def test_guard_deleted_during_realpath_keeps_containment_and_original_io_path(self):
        guard = self.write(state.RUNTIME_DIR + '/lock-fixture.json.guard', b'{}\n')
        real_final = ntpath._getfinalpathname
        deleted = False
        def resolve_then_delete(selected):
            nonlocal deleted
            result = real_final(selected)
            if not deleted and Path(selected) == guard:
                deleted = True
                guard.unlink()
            return result
        with mock.patch.object(ntpath, '_getfinalpathname', side_effect=resolve_then_delete):
            result = paths.resolve_note_path(self.vault, state.RUNTIME_DIR + '/lock-fixture.json.guard')
        self.assertTrue(deleted)
        self.assertEqual(str(result), '\\\\?\\' + str(guard))
        self.assertFalse(result.exists())

    def test_extended_drive_comparison_preserves_escape_deny_and_device_boundaries(self):
        real_resolve = Path.resolve
        candidate = self.vault / 'notes/edit.md'
        for destination, denied in (
                (Path('\\\\?\\' + str(self.vault) + '-other\\edit.md'), ()),
                (Path('\\\\?\\' + str(self.vault / 'private/edit.md')), ('private',)),
                (Path('\\\\.\\' + str(candidate)), ()),
                (Path('\\\\?\\UNC\\localhost\\unmounted-fixture\\edit.md'), ())):
            with self.subTest(destination=str(destination)):
                def resolve(selected, *args, **kwargs):
                    return destination if selected == candidate else real_resolve(selected, *args, **kwargs)
                with mock.patch.object(Path, 'resolve', resolve):
                    with self.assertRaises(ValueError):
                        paths.resolve_note_path(self.vault, 'notes/edit.md', denied)

    def test_drive_prefix_alias_requires_same_existing_root_identity(self):
        prefixed = Path('\\\\?\\' + str(self.target))
        with mock.patch.object(paths.os.path, 'samefile', return_value=False):
            with self.assertRaisesRegex(ValueError, 'root_identity_changed'):
                paths._contained_relative_path(prefixed, self.vault)
        self.assertEqual(paths._contained_relative_path(prefixed, self.vault).as_posix(), 'notes/edit.md')
        prefixed_root = Path('\\\\?\\' + str(self.vault))
        self.assertEqual(paths._contained_relative_path(self.target, prefixed_root).as_posix(), 'notes/edit.md')
        resolved = paths.resolve_note_path(prefixed_root, 'notes/edit.md')
        self.assertTrue(str(resolved).startswith('\\\\?\\'))
        self.assertEqual(resolved.read_bytes(), b'old\r\n')

    def test_nonwindows_containment_does_not_alias_windows_prefixes(self):
        import types
        with mock.patch.object(paths, 'os', types.SimpleNamespace(name='posix')):
            self.assertEqual(paths._contained_relative_path(self.target, self.vault).as_posix(), 'notes/edit.md')
            with self.assertRaises(ValueError):
                paths._contained_relative_path(Path('\\\\?\\' + str(self.target)), self.vault)


if __name__ == '__main__':
    unittest.main()
