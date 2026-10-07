from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'hooks/session_checkpoint.py'
RUNTIME = '00-meta/.agentic-vault/runtime'
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HOOK.is_file(), 'minimal checkpoint hook implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Match the runtime's canonical namespace for path-specific drift probes.
        self.vault = Path(self.tmp.name).resolve() / 'vault'
        self.vault.mkdir()
        self.write('00-meta/vault-config.json', json.dumps({
            'deny_zones': ['private'], 'exclude_dirs': ['excluded', RUNTIME],
            'hot_note': 'notes/hot.md', 'handoff_note': 'notes/handoff.md',
            'log_note': 'notes/log.md',
        }).encode())
        for relative in ('notes/hot.md', 'notes/handoff.md', 'notes/log.md'):
            self.write(relative, b'BUSINESS-CONTENT never in runtime\r\n')

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_hook(self, event, *, cwd=None, arguments=()):
        raw = event if isinstance(event, str) else json.dumps(event)
        return subprocess.run([sys.executable, str(HOOK), '--vault', str(cwd or self.vault),
                               *arguments], input=raw, capture_output=True, text=True,
                              encoding='utf-8', timeout=10)

    def records(self):
        return list((self.vault / RUNTIME).glob('checkpoint-*.json'))

    def test_checkpoint_only_records_hashes_event_and_pending_ids(self):
        originals = {p: p.read_bytes() for p in (self.vault / 'notes').glob('*.md')}
        result = self.run_hook({'hook_event_name': 'PreCompact', 'session_id': 's-123',
                               'pending_references': ['B-001'],
                               'transcript_path': '../secret/transcript',
                               'messages': ['TRANSCRIPT-SECRET'], 'reason': 'DO-NOT-STORE'})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(len(self.records()), 1)
        raw = self.records()[0].read_text()
        for forbidden in ('BUSINESS-CONTENT', 'TRANSCRIPT-SECRET', 'DO-NOT-STORE', 'secret/transcript', 's-123'):
            self.assertNotIn(forbidden, raw)
        saved = json.loads(raw)
        self.assertEqual(saved['events'][-1]['event'], 'PreCompact')
        self.assertEqual(saved['pending_references'], ['B-001'])
        self.assertEqual(saved['sources'][0]['sha256'], hashlib.sha256(originals[self.vault / 'notes/hot.md']).hexdigest())
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_session_end_does_not_invent_completion_and_keeps_bounded_history(self):
        for _ in range(18):
            result = self.run_hook({'hook_event_name': 'SessionEnd', 'session_id': 'same'})
            self.assertEqual(result.stderr, '')
        saved = json.loads(self.records()[0].read_text())
        self.assertLessEqual(len(saved['events']), 16)
        self.assertNotIn('completed', saved)
        self.assertNotIn('status', saved)
        self.assertTrue(all(e['event'] == 'SessionEnd' for e in saved['events']))

    def test_nonvault_is_silent_and_creates_nothing(self):
        empty = Path(self.tmp.name) / 'nonvault'
        empty.mkdir()
        result = self.run_hook('{broken', cwd=empty)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(list(empty.iterdir()), [])

    def test_malformed_oversized_and_wrong_events_have_safe_diagnostics(self):
        for event in ('{broken', 'x' * (64 * 1024 + 1), '[]',
                      {'hook_event_name': 'SessionStart'},
                      {'hook_event_name': []}, {'hook_event_name': {}},
                      {'hook_event_name': 'PreCompact', 'session_id': 'x' * 129},
                      {'hook_event_name': 'PreCompact', 'pending_references': ['secret\nvalue']},
                      '{"hook_event_name":"PreCompact","hook_event_name":"SessionEnd"}'):
            with self.subTest(event=str(event)[:80]):
                result = self.run_hook(event)
                self.assertEqual((result.returncode, result.stdout), (0, ''))
                self.assertIn('invalid checkpoint event', result.stderr)
                self.assertLess(len(result.stderr), 150)
        self.assertEqual(self.records(), [])

    def test_tampered_prior_event_type_does_not_escape_safe_hook_diagnostic(self):
        self.run_hook({'hook_event_name': 'PreCompact', 'session_id': 'same'})
        record = self.records()[0]
        saved = json.loads(record.read_text())
        saved['events'][0]['event'] = []
        record.write_text(json.dumps(saved))
        result = self.run_hook({'hook_event_name': 'SessionEnd', 'session_id': 'same'})
        self.assertEqual((result.returncode, result.stdout), (0, ''))
        self.assertIn('checkpoint unavailable', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_manifest_event_argument_accepts_omitted_but_rejects_mismatched_input(self):
        result = self.run_hook({'session_id': 'same'}, arguments=('--event', 'PreCompact'))
        self.assertEqual(result.stderr, '')
        mismatch = self.run_hook({'hook_event_name': 'SessionEnd'}, arguments=('--event', 'PreCompact'))
        self.assertIn('invalid checkpoint event', mismatch.stderr)

    def test_denied_configured_source_does_not_leave_partial_checkpoint(self):
        self.write('00-meta/vault-config.json', b'{"deny_zones":["private"],"hot_note":"private/no.md"}')
        self.write('private/no.md', b'PRIVATE-NOTE')
        result = self.run_hook({'hook_event_name': 'PreCompact'})
        self.assertEqual(result.stdout, '')
        self.assertIn('checkpoint unavailable', result.stderr)
        self.assertNotIn('PRIVATE-NOTE', result.stderr)
        self.assertEqual(self.records(), [])

    def test_hardlinked_runtime_checkpoint_is_rejected(self):
        result = self.run_hook({'hook_event_name': 'PreCompact', 'session_id': 'same'})
        self.assertEqual(result.stderr, '')
        record = self.records()[0]
        original = record.read_bytes()
        other = self.vault / 'notes/checkpoint-copy.json'
        os.link(record, other)
        result = self.run_hook({'hook_event_name': 'SessionEnd', 'session_id': 'same'})
        self.assertIn('checkpoint unavailable', result.stderr)
        self.assertEqual(other.read_bytes(), original)

    def test_parallel_checkpoints_preserve_both_events(self):
        workers = [subprocess.Popen([sys.executable, str(HOOK), '--vault', str(self.vault)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [worker.communicate(json.dumps({'hook_event_name': 'PreCompact',
                                                  'session_id': 'same'}), timeout=10)
                   for worker in workers]
        self.assertTrue(all(output == ('', '') for output in outputs), outputs)
        saved = json.loads(self.records()[0].read_text())
        self.assertEqual(len(saved['events']), 2)

    def test_review_malformed_same_token_lease_returns_bounded_hook_diagnostic(self):
        spec = importlib.util.spec_from_file_location('checkpoint_review_test', HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        import vault_state as state
        real_atomic = state._atomic
        originals = {p: p.read_bytes() for p in (self.vault / 'notes').glob('*.md')}
        def tamper(path, data, before_replace, **kwargs):
            lock = next((self.vault / RUNTIME).glob('lock-*.json'))
            record = json.loads(lock.read_bytes())
            record['created_at'] = []
            lock.write_bytes(json.dumps(record).encode())
            return real_atomic(path, data, before_replace, **kwargs)
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(state, '_atomic', side_effect=tamper), \
             mock.patch.object(sys, 'stdin', io.StringIO('{"hook_event_name":"PreCompact"}')), \
             mock.patch.object(sys, 'stdout', output), mock.patch.object(sys, 'stderr', errors):
            try:
                result = hook.main(['--vault', str(self.vault), '--event', 'PreCompact'])
            except Exception as error:
                self.fail(f'malformed lease escaped the safe hook boundary: {type(error).__name__}')
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), 'agentic-vault: checkpoint unavailable\n')
        self.assertEqual(self.records(), [])
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_fix3_checkpoint_source_read_policy_drift_has_safe_diagnostic(self):
        spec = importlib.util.spec_from_file_location('checkpoint_fix3_test', HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        import vault_state as state
        original_config = (self.vault / state.CONFIG_PATH).read_bytes()
        originals = {p: p.read_bytes() for p in (self.vault / 'notes').glob('*.md')}
        real_read = state._Policy.read_note
        switched = []
        def drift(policy, selected):
            result = real_read(policy, selected)
            if selected == 'notes/log.md' and any((self.vault / RUNTIME).glob('.vault-state-*')):
                config = json.loads(original_config)
                config['deny_zones'].append('notes')
                self.write(state.CONFIG_PATH, json.dumps(config).encode())
                switched.append(True)
            return result
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(state._Policy, 'read_note', drift), \
             mock.patch.object(sys, 'stdin', io.StringIO('{"hook_event_name":"PreCompact"}')), \
             mock.patch.object(sys, 'stdout', output), mock.patch.object(sys, 'stderr', errors):
            result = hook.main(['--vault', str(self.vault), '--event', 'PreCompact'])
        self.assertEqual(switched, [True])
        self.assertEqual((result, output.getvalue()), (0, ''))
        self.assertEqual(errors.getvalue(), 'agentic-vault: checkpoint unavailable\n')
        self.assertEqual(self.records(), [])
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_fix3_checkpoint_update_rechecks_policy_after_runtime_base_read(self):
        self.run_hook({'hook_event_name': 'PreCompact', 'session_id': 'same'})
        record = self.records()[0]
        original = record.read_bytes()
        import vault_state as state
        real_read = state._read
        switched = []
        def drift(resolver, limit=state.MAX_BYTES):
            result = real_read(resolver, limit)
            selected = resolver()
            if selected == record and any((self.vault / RUNTIME).glob('.vault-state-*')):
                config = json.loads((self.vault / state.CONFIG_PATH).read_bytes())
                config['deny_zones'].append(RUNTIME)
                self.write(state.CONFIG_PATH, json.dumps(config).encode())
                switched.append(True)
            return result
        with mock.patch.object(state, '_read', side_effect=drift):
            with self.assertRaisesRegex(state.StateError, 'configuration_changed'):
                state.write_checkpoint(self.vault, 'SessionEnd', session_id='same')
        self.assertEqual(switched, [True])
        self.assertEqual(record.read_bytes(), original)

    def test_fix4_checkpoint_cleanup_preserves_revoked_runtime_without_reads_or_unlinks(self):
        self.run_hook({'hook_event_name': 'PreCompact', 'session_id': 'cleanup'})
        record = self.records()[0]
        original = record.read_bytes()
        import vault_state as state
        real_read, real_fresh, real_unlink = state._read, state._Policy.fresh, Path.unlink
        changed, recognized, reads, unlinks = [], [], [], []
        def read(resolver, limit=state.MAX_BYTES):
            selected = resolver()
            if recognized and selected.as_posix().startswith((self.vault / RUNTIME).as_posix() + '/'):
                reads.append(selected.name)
            result = real_read(resolver, limit)
            if selected == record and any((self.vault / RUNTIME).glob('.vault-state-*')):
                config = json.loads((self.vault / state.CONFIG_PATH).read_bytes())
                config['deny_zones'].append(RUNTIME)
                self.write(state.CONFIG_PATH, json.dumps(config).encode())
                changed.append(True)
            return result
        def fresh(policy):
            try:
                return real_fresh(policy)
            except state.StateError:
                if changed:
                    recognized.append(True)
                raise
        def unlink(path, *args, **kwargs):
            if recognized and path.as_posix().startswith((self.vault / RUNTIME).as_posix() + '/'):
                unlinks.append(path.name)
            return real_unlink(path, *args, **kwargs)
        with mock.patch.object(state, '_read', side_effect=read), \
             mock.patch.object(state._Policy, 'fresh', fresh), mock.patch.object(Path, 'unlink', unlink):
            with self.assertRaisesRegex(state.StateError, 'configuration_changed'):
                state.write_checkpoint(self.vault, 'SessionEnd', session_id='cleanup')
        self.assertTrue(recognized)
        self.assertEqual(unlinks, [])
        self.assertEqual(reads, [])
        self.assertEqual(record.read_bytes(), original)
        self.assertEqual(len(list((self.vault / RUNTIME).glob('.vault-state-*'))), 1)
        self.assertEqual(len(list((self.vault / RUNTIME).glob('*.guard'))), 1)


if __name__ == '__main__':
    unittest.main()
