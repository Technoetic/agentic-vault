from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    state = importlib.import_module('vault_state')
except ModuleNotFoundError:
    state = None


def sha(data):
    return hashlib.sha256(data).hexdigest()


class VaultStateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(state, 'cooperative state implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write('00-meta/vault-config.json', json.dumps({
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
            'handoff_note': 'notes/handoff.md', 'hot_note': 'notes/hot.md',
            'log_note': 'notes/log.md',
        }).encode())
        self.write('notes/handoff.md', b'---\ntitle: Handoff\n---\nHuman narrative\r\n')
        self.write('notes/tasks.md', b'## Blocked\n- [ ] [B-001] Wait for owner\n## Doing\n- [ ] Translate label\n')
        self.write('notes/log.md', b'- 2026-10-07 10:00 | Codex | [ops] Fixture event\n')

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def project(self, **kwargs):
        return state.project_handoff(self.vault, 'notes/log.md', 'notes/tasks.md',
                                     'notes/handoff.md', **kwargs)

    def test_partial_edit_preserves_untouched_mixed_newlines_and_korean(self):
        original = '첫줄\r\nold\nlast\r\n'.encode()
        path = self.write('notes/mixed.md', original)
        result = state.patch_note(self.vault, 'notes/mixed.md', sha(original),
                                  [{'start': 8, 'end': 11, 'replacement': 'new'}])
        self.assertEqual(path.read_bytes(), '첫줄\r\nnew\nlast\r\n'.encode())
        self.assertEqual(result['before_sha256'], sha(original))
        self.assertEqual(result['status'], 'applied')

    def test_stale_base_leaves_original_unchanged(self):
        path = self.write('notes/edit.md', b'old\r\n')
        with self.assertRaisesRegex(state.StateError, 'stale_base'):
            state.patch_note(self.vault, 'notes/edit.md', '0' * 64,
                             [{'start': 0, 'end': 3, 'replacement': b'new'}])
        self.assertEqual(path.read_bytes(), b'old\r\n')

    def test_overlapping_and_split_utf8_edits_are_rejected(self):
        original = '한글 text\n'.encode()
        path = self.write('notes/edit.md', original)
        cases = [[{'start': 1, 'end': 3, 'replacement': 'x'}],
                 [{'start': 0, 'end': 3, 'replacement': 'x'},
                  {'start': 2, 'end': 6, 'replacement': 'y'}]]
        for edits in cases:
            with self.subTest(edits=edits):
                with self.assertRaises(state.StateError):
                    state.patch_note(self.vault, 'notes/edit.md', sha(original), edits)
        self.assertEqual(path.read_bytes(), original)

    def test_replacement_and_aggregate_edit_output_are_bounded(self):
        path = self.write('notes/edit.md', b'a\nb\n')
        for edits in ([{'start': 0, 'end': 1, 'replacement': b'x' * (256 * 1024 + 1)}],
                      [{'start': 0, 'end': 1, 'replacement': b'x' * (128 * 1024)},
                       {'start': 2, 'end': 3, 'replacement': b'y' * (128 * 1024)}]):
            with self.subTest(size=sum(len(edit['replacement']) for edit in edits)):
                with self.assertRaisesRegex(state.StateError, 'output_too_large'):
                    state.patch_note(self.vault, 'notes/edit.md', sha(b'a\nb\n'), edits)
        self.assertEqual(path.read_bytes(), b'a\nb\n')

    def test_policy_and_runtime_reserved_paths_are_rejected(self):
        for relative in ('private/no.md', 'excluded/no.md', '../outside.md',
                         '00-meta/.agentic-vault/runtime/fake.md', 'notes/fake.md:stream'):
            with self.subTest(relative=relative):
                with self.assertRaises(state.StateError):
                    state.patch_note(self.vault, relative, '0' * 64, [])

    def test_hardlinked_note_is_rejected_without_touching_other_name(self):
        target = self.write('notes/edit.md', b'old\n')
        other = self.vault / 'notes/other.md'
        os.link(target, other)
        with self.assertRaises(state.StateError):
            state.patch_note(self.vault, 'notes/edit.md', sha(b'old\n'),
                             [{'start': 0, 'end': 3, 'replacement': 'new'}])
        self.assertEqual(other.read_bytes(), b'old\n')

    def test_runtime_symlink_is_rejected(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        directory = self.vault / '00-meta/.agentic-vault'
        directory.mkdir()
        try:
            (directory / 'runtime').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'symlink unavailable: {exc}')
        with self.assertRaises(state.StateError):
            with state.advisory_lock(self.vault, 'task'):
                self.fail('unsafe runtime lock acquired')
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_runtime_junction_is_rejected_on_windows_without_symlink_privilege(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        directory = self.vault / '00-meta/.agentic-vault'
        directory.mkdir()
        junction = directory / 'runtime'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                capture_output=True, text=True)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        with self.assertRaises(state.StateError):
            with state.advisory_lock(self.vault, 'task'):
                self.fail('runtime junction acquired')
        self.assertEqual(list(outside.iterdir()), [])

    def test_lock_has_token_pid_utc_and_bounded_wait(self):
        with state.advisory_lock(self.vault, 'shared', ttl_seconds=60, wait_seconds=0) as lease:
            saved = json.loads(Path(lease['lock_path']).read_text())
            self.assertEqual(saved['token'], lease['token'])
            self.assertEqual(saved['pid'], os.getpid())
            self.assertTrue(saved['created_at'].endswith('+00:00'))
            started = time.monotonic()
            with self.assertRaisesRegex(state.StateError, 'lock_busy'):
                with state.advisory_lock(self.vault, 'shared', ttl_seconds=60, wait_seconds=.06):
                    self.fail('second writer entered')
            self.assertLess(time.monotonic() - started, .5)
        self.assertFalse(Path(lease['lock_path']).exists())

    def test_release_never_removes_replacement_owner(self):
        with state.advisory_lock(self.vault, 'shared') as lease:
            path = Path(lease['lock_path'])
            replacement = json.loads(path.read_text())
            replacement['token'] = 'a' * 32
            path.write_text(json.dumps(replacement))
        self.assertEqual(json.loads(path.read_text())['token'], 'a' * 32)

    def test_expired_owner_release_preserves_real_cooperating_replacement(self):
        replacement_context = None
        try:
            with state.advisory_lock(self.vault, 'shared', ttl_seconds=.02) as first:
                time.sleep(.05)
                replacement_context = state.advisory_lock(self.vault, 'shared', ttl_seconds=60)
                replacement = replacement_context.__enter__()
            path = Path(replacement['lock_path'])
            self.assertEqual(json.loads(path.read_text())['token'], replacement['token'])
            self.assertNotEqual(first['token'], replacement['token'])
        finally:
            if replacement_context is not None:
                replacement_context.__exit__(None, None, None)

    def test_lease_timestamp_starts_at_acquisition_after_bounded_wait(self):
        acquired = threading.Event()
        release_started = []
        def hold():
            with state.advisory_lock(self.vault, 'shared'):
                acquired.set()
                time.sleep(.14)
                release_started.append(time.time())
        writer = threading.Thread(target=hold)
        writer.start()
        self.addCleanup(writer.join)
        self.assertTrue(acquired.wait(1))
        with state.advisory_lock(self.vault, 'shared', ttl_seconds=.1, wait_seconds=.5) as lease:
            created = state.datetime.fromisoformat(lease['created_at']).timestamp()
            self.assertGreaterEqual(created, release_started[0])

    def test_expiry_requires_both_utc_and_old_mtime(self):
        with state.advisory_lock(self.vault, 'shared', ttl_seconds=.02) as first:
            path = Path(first['lock_path'])
            time.sleep(.035)
            # A future/fresh mtime is conservatively non-expired independent
            # of filesystem timing and fsync latency on Windows.
            future = time.time() + 60
            os.utime(path, (future, future))
            with self.assertRaisesRegex(state.StateError, 'lock_busy'):
                with state.advisory_lock(self.vault, 'shared', ttl_seconds=1):
                    self.fail('fresh mtime should prevent recovery')
            past = time.time() - 60
            os.utime(path, (past, past))
            with state.advisory_lock(self.vault, 'shared', ttl_seconds=1) as next_owner:
                self.assertNotEqual(first['token'], next_owner['token'])
            path.write_text(json.dumps({'replacement': True}))
        self.assertTrue(path.exists())

    def test_guard_disappearing_after_exclusive_collision_is_retried(self):
        real_exclusive = state._exclusive
        raced = False
        def guard_race(policy, name, record):
            nonlocal raced
            if name.endswith('.guard') and not raced:
                raced = True
                # Model the other cooperating holder removing its guard
                # immediately after our O_EXCL observed it.
                raise FileExistsError()
            return real_exclusive(policy, name, record)
        with mock.patch.object(state, '_exclusive', side_effect=guard_race):
            with state.advisory_lock(self.vault, 'shared', wait_seconds=.2) as lease:
                self.assertEqual(json.loads(Path(lease['lock_path']).read_text())['token'], lease['token'])

    def test_invalid_lock_and_hardlink_are_not_recovered(self):
        with state.advisory_lock(self.vault, 'shared') as lease:
            path = Path(lease['lock_path'])
            path.write_bytes(b'{broken')
            with self.assertRaisesRegex(state.StateError, 'lock_busy'):
                with state.advisory_lock(self.vault, 'shared', wait_seconds=.01):
                    self.fail('invalid lock recovered')
        path.unlink()
        with state.advisory_lock(self.vault, 'linked') as lease:
            os.link(lease['lock_path'], self.vault / 'notes/lock-copy.json')
            with self.assertRaises(state.StateError):
                with state.advisory_lock(self.vault, 'linked'):
                    self.fail('hardlinked lock acquired')
        self.assertTrue(Path(lease['lock_path']).exists())

    def test_invalid_and_unbounded_timing_rejected(self):
        for ttl, wait in ((0, 0), (float('nan'), 0), (1, float('inf')), (1, -1), (1, 61)):
            with self.subTest(ttl=ttl, wait=wait):
                with self.assertRaises(state.StateError):
                    with state.advisory_lock(self.vault, 'shared', ttl_seconds=ttl, wait_seconds=wait):
                        self.fail('invalid timing accepted')

    def test_two_concurrent_writers_same_base_have_one_winner(self):
        path = self.write('notes/edit.md', b'old\n')
        code = """import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from vault_state import patch_note,StateError
try:
 r=patch_note(Path(sys.argv[2]),'notes/edit.md',sys.argv[3],[{'start':0,'end':3,'replacement':sys.argv[4]}])
 print(r['status'])
except StateError as e: print(str(e))
"""
        workers = [subprocess.Popen([sys.executable, '-c', code, str(SCRIPTS), str(self.vault),
                                     sha(b'old\n'), word], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True) for word in ('one', 'two')]
        results = [process.communicate(timeout=10) for process in workers]
        self.assertEqual(sum(out.strip() == 'applied' for out, _ in results), 1, results)
        self.assertTrue(all(not err for _, err in results), results)
        self.assertIn(path.read_bytes(), (b'one\n', b'two\n'))

    def test_policy_changed_immediately_before_replace_blocks_write(self):
        path = self.write('notes/edit.md', b'old\n')
        real_fsync = state.os.fsync
        changed = False
        def change_policy(fd):
            nonlocal changed
            result = real_fsync(fd)
            if not changed and any(path.parent.glob('.vault-state-*')):
                changed = True
                self.write('00-meta/vault-config.json', b'{"deny_zones":["notes"]}')
            return result
        with mock.patch.object(state.os, 'fsync', side_effect=change_policy):
            with self.assertRaises(state.StateError):
                state.patch_note(self.vault, 'notes/edit.md', sha(b'old\n'),
                                 [{'start': 0, 'end': 3, 'replacement': 'new'}])
        self.assertEqual(path.read_bytes(), b'old\n')
        self.assertEqual(list(path.parent.glob('.vault-state-*')), [])

    def patch_with_config(self, path, original, edits, expected_config):
        self.assertIn('expected_config_sha256', inspect.signature(state.patch_note).parameters,
                      'patch_note must bind the proposal configuration generation')
        return state.patch_note(self.vault, path, sha(original), edits,
                                expected_config_sha256=expected_config)

    def test_expected_config_generation_changed_before_policy_construction_blocks_write(self):
        original = b'old\r\n'
        path = self.write('notes/edit.md', original)
        config = self.vault / '00-meta/vault-config.json'
        expected_config = sha(config.read_bytes())
        real_policy = state._Policy
        changed = False
        def change_before_policy(vault):
            nonlocal changed
            if not changed:
                changed = True
                newer = json.loads(config.read_text())
                newer['vault_name'] = 'Different allowed generation'
                config.write_bytes(json.dumps(newer).encode())
            return real_policy(vault)
        with mock.patch.object(state, '_Policy', side_effect=change_before_policy):
            with self.assertRaisesRegex(state.StateError, 'configuration_changed'):
                self.patch_with_config('notes/edit.md', original,
                                       [{'start': 0, 'end': 3, 'replacement': 'new'}], expected_config)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.vault / state.RUNTIME_DIR).exists())

    def test_matching_expected_config_generation_allows_byte_preserving_edit(self):
        original = b'keep\r\nold\nlast\r\n'
        path = self.write('notes/edit.md', original)
        expected_config = sha((self.vault / '00-meta/vault-config.json').read_bytes())
        result = self.patch_with_config('notes/edit.md', original,
                                       [{'start': 6, 'end': 9, 'replacement': 'new'}], expected_config)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(path.read_bytes(), b'keep\r\nnew\nlast\r\n')

    def test_invalid_expected_config_hashes_are_rejected(self):
        original = b'old\n'
        path = self.write('notes/edit.md', original)
        for invalid in (True, 1, {}, [], 'not-a-hash', 'A' * 64, '0' * 63):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(state.StateError, 'invalid_hash'):
                    self.patch_with_config('notes/edit.md', original,
                                           [{'start': 0, 'end': 3, 'replacement': 'new'}], invalid)
        self.assertEqual(path.read_bytes(), original)

    def test_note_changed_immediately_before_replace_blocks_lost_update(self):
        path = self.write('notes/edit.md', b'old\n')
        real_fsync = state.os.fsync
        changed = False
        def change_note(fd):
            nonlocal changed
            result = real_fsync(fd)
            if not changed and any(path.parent.glob('.vault-state-*')):
                changed = True
                path.write_bytes(b'other writer\n')
            return result
        with mock.patch.object(state.os, 'fsync', side_effect=change_note):
            with self.assertRaisesRegex(state.StateError, 'stale_base'):
                state.patch_note(self.vault, 'notes/edit.md', sha(b'old\n'),
                                 [{'start': 0, 'end': 3, 'replacement': 'new'}])
        self.assertEqual(path.read_bytes(), b'other writer\n')
        self.assertEqual(list(path.parent.glob('.vault-state-*')), [])

    def test_projection_is_dry_run_and_preserves_narrative_on_explicit_apply(self):
        original = (self.vault / 'notes/handoff.md').read_bytes()
        dry = self.project()
        self.assertEqual(dry['status'], 'proposed')
        self.assertEqual((self.vault / 'notes/handoff.md').read_bytes(), original)
        applied = self.project(apply=True, expected_sha256=sha(original))
        candidate = (self.vault / 'notes/handoff.md').read_bytes()
        self.assertEqual(applied['status'], 'applied')
        self.assertTrue(candidate.startswith(original))
        self.assertIn(b'agentic-vault:handoff begin', candidate)
        repeat = self.project(apply=True, expected_sha256=sha(candidate))
        self.assertEqual((self.vault / 'notes/handoff.md').read_bytes(), candidate)
        self.assertEqual(repeat['status'], 'unchanged')

    def test_projection_preserves_legacy_ids_and_stable_new_ids_across_reordering(self):
        first = self.project()['projection']['items']
        self.assertEqual(first[0]['id'], 'B-001')
        fresh = first[1]['id']
        self.write('notes/tasks.md', b'## Doing\n- [ ] Translate label\n## Blocked\n- [ ] [B-001] Wait for owner\n')
        second = self.project()['projection']['items']
        self.assertEqual(second[0]['id'], fresh)
        self.assertEqual(second[1]['id'], 'B-001')
        self.assertNotEqual(self.project(namespace='different')['projection']['items'][0]['id'], fresh)

    def test_stable_id_collision_is_explicit_and_legacy_duplicate_is_rejected(self):
        registry = {}
        identifier = state.stable_item_id('tasks', 'Translate label', registry=registry)
        registry[identifier] = '0' * 64
        with self.assertRaisesRegex(state.StateError, 'id_collision'):
            state.stable_item_id('tasks', 'Translate label', registry=registry)
        self.write('notes/tasks.md', b'## Blocked\n- [ ] [B-001] First\n- [ ] [B-001] Different\n')
        with self.assertRaisesRegex(state.StateError, 'id_collision'):
            self.project()

    def test_removed_blocker_requires_exact_previous_anchor_hash(self):
        old = self.project()['block'].encode()
        self.write('notes/tasks.md', b'## Doing\n- [ ] Translate label\n')
        current = self.project()['projection']
        warnings = state.detect_removed_blockers(old, current, expected_sha256=sha(old))
        self.assertEqual([w['id'] for w in warnings], ['B-001'])
        self.assertEqual(warnings[0]['code'], 'removed_blocker')
        with self.assertRaisesRegex(state.StateError, 'stale_previous_anchor'):
            state.detect_removed_blockers(old, current, expected_sha256='0' * 64)
        with self.assertRaises(TypeError):
            state.detect_removed_blockers(old, current)

    def test_fenced_tasks_and_machine_markers_are_not_executed_or_replaced(self):
        self.write('notes/tasks.md', b'```md\n## Blocked\n- [ ] [B-003] Fake\n```\n## Doing\n- [ ] Real\n')
        result = self.project()
        self.assertEqual(len(result['projection']['items']), 1)
        original = b'Narrative\n```\n<!-- agentic-vault:handoff begin -->\n{}\n<!-- agentic-vault:handoff end -->\n```\n'
        self.write('notes/handoff.md', original)
        self.project(apply=True, expected_sha256=sha(original))
        self.assertTrue((self.vault / 'notes/handoff.md').read_bytes().startswith(original))

    def test_projection_source_drift_is_checked_before_apply(self):
        original = (self.vault / 'notes/handoff.md').read_bytes()
        real_patch = state.patch_note
        def drift(*args, **kwargs):
            self.write('notes/tasks.md', b'## Doing\n- [ ] Changed\n')
            return real_patch(*args, **kwargs)
        with mock.patch.object(state, 'patch_note', side_effect=drift):
            with self.assertRaisesRegex(state.StateError, 'source_changed'):
                self.project(apply=True, expected_sha256=sha(original))
        self.assertEqual((self.vault / 'notes/handoff.md').read_bytes(), original)

    def test_review_projection_configuration_drift_at_writer_boundary_blocks_apply(self):
        anchor = self.vault / 'notes/handoff.md'
        original = anchor.read_bytes()
        real_patch = state.patch_note
        def drift(*args, **kwargs):
            config = json.loads((self.vault / state.CONFIG_PATH).read_bytes())
            config['vault_name'] = 'Allowed but different projection generation'
            self.write(state.CONFIG_PATH, json.dumps(config).encode())
            return real_patch(*args, **kwargs)
        with mock.patch.object(state, 'patch_note', side_effect=drift):
            with self.assertRaisesRegex(state.StateError, 'expected_configuration_changed'):
                self.project(apply=True, expected_sha256=sha(original))
        self.assertEqual(anchor.read_bytes(), original)

    def test_review_nested_blocked_sections_retain_scope_until_same_or_higher_heading(self):
        self.write('notes/tasks.md', (
            '## Blocked\n### Awaiting owner\n- [ ] [B-001] Wait for owner\n'
            '#### Details\n- [ ] [B-002] Confirm details\n'
            '### Another blocked group\n- [ ] [B-003] Still blocked\n'
            '## Doing\n- [ ] [D-001] Work in progress\n'
            '### Blocked\n- [ ] [B-004] Nested blocker\n'
            '### Active\n- [ ] [D-002] Nested active item\n'
            '## Blocked\n### Child\n- [x] [D-003] Explicit completion\n'
            '# Other\n- [ ] [D-004] Outside blocked parent\n'
        ).encode())
        result = self.project()
        self.assertEqual({item['id']: item['state'] for item in result['projection']['items']}, {
            'B-001': 'blocked', 'B-002': 'blocked', 'B-003': 'blocked',
            'D-001': 'pending', 'B-004': 'blocked', 'D-002': 'pending',
            'D-003': 'completed', 'D-004': 'pending',
        })

    def test_review_malformed_same_token_lease_fields_fail_with_safe_state_error(self):
        real_atomic = state._atomic
        cases = [('created_at', []), ('created_at', None), ('created_at', 'bad-date'),
                 ('created_at', '2026-10-07T12:00:00'),
                 ('created_at', '2026-10-07T12:00:00+09:00'),
                 ('ttl_seconds', float('inf')), ('ttl_seconds', float('nan')),
                 ('ttl_seconds', True), ('ttl_seconds', 0), ('ttl_seconds', 86401),
                 ('ttl_seconds', 10 ** 400),
                 ('pid', True), ('pid', 0), ('version', True)]
        for number, (field, invalid) in enumerate(cases):
            with self.subTest(field=field, invalid=invalid):
                relative = f'notes/edit-{number}.md'
                target = self.write(relative, b'old\n')
                def tamper(path, data, before_replace):
                    # Every subcase has a distinct lease and target; preserved
                    # malformed locks from earlier subcases remain untouched.
                    lock_key = 'note:' + relative.casefold()
                    lock = self.vault / state.RUNTIME_DIR / ('lock-' + sha(lock_key.encode()) + '.json')
                    record = json.loads(lock.read_bytes())
                    record[field] = invalid
                    lock.write_bytes(json.dumps(record).encode())
                    return real_atomic(path, data, before_replace)
                with mock.patch.object(state, '_atomic', side_effect=tamper):
                    try:
                        state.patch_note(self.vault, relative, sha(b'old\n'),
                                         [{'start': 0, 'end': 3, 'replacement': 'new'}])
                    except Exception as error:
                        self.assertIsInstance(error, state.StateError)
                        self.assertRegex(str(error), r'^(invalid_lease|lock_owner_changed)$')
                    else:
                        self.fail('malformed same-token lease authorized a write')
                self.assertEqual(target.read_bytes(), b'old\n')


if __name__ == '__main__':
    unittest.main()
