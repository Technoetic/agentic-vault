from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import threading
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class LessonDeltaTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module('vault_lesson_delta')
        except ModuleNotFoundError:
            self.fail('lesson delta implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.write('00-meta/vault-config.json', json.dumps({
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
        }).encode())
        self.original = ('---\r\ntitle: Lessons\r\n---\r\nHuman narrative\r\n'
                         '## 대장\r\n'
                         '- 2026-10-01 | 횟수 2 | [L-001] Check bytes | 근거: old | 상태: 관찰중\r\n'
                         '- 2026-10-02 | 횟수 1 | Legacy lesson | 근거: original | 상태: 관찰중\n'
                         '## 기각 대장\r\nKeep this exactly\r\n').encode()
        self.target = self.write('notes/lessons.md', self.original)
        self.source = self.write('notes/observation.md', b'# Host observation\nReported, unverified\n')

    def write(self, name, data):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def prepare(self, ops):
        return self.module.prepare_delta(self.vault, 'notes/lessons.md', ops)

    def apply(self, proposal, approve=True):
        return self.module.apply_delta(self.vault, proposal, approve=approve)

    def ref(self):
        return {'path': 'notes/observation.md', 'sha256': sha(self.source.read_bytes()),
                'observation_id': '1' * 32, 'receipt_id': '2' * 32}

    def test_inc_preserves_historical_id_and_every_other_byte(self):
        p = self.prepare([{'op': 'INC', 'id': 'L-001', 'amount': 2, 'references': [self.ref()]}])
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertIn('횟수 4', p['diff'])
        self.assertEqual(p['authority'], 'observation_data_only')
        r = self.apply(p)
        self.assertEqual(r['status'], 'applied')
        current = self.target.read_bytes()
        oldline = self.original.splitlines(keepends=True)[5]
        changed = current.splitlines(keepends=True)[5]
        self.assertIn(b'[L-001]', changed)
        self.assertEqual(current.replace(changed, oldline, 1), self.original)
        self.assertEqual(r['references'][0]['observation_id'], '1' * 32)
        self.assertNotIn('verified', r)

    def test_add_deterministic_id_edit_and_retire_keep_lesson_text(self):
        ops = [{'op': 'ADD', 'text': 'Preserve 원문 bytes', 'date': '2026-10-07'}]
        one, two = self.prepare(ops), self.prepare(ops)
        self.assertEqual(one, two)
        identifier = one['changes'][0]['id']
        self.assertRegex(identifier, r'^av-[0-9a-f]{20}$')
        self.apply(one)
        self.apply(self.prepare([{'op': 'EDIT', 'id': identifier, 'text': 'Preserve all bytes'}]))
        self.apply(self.prepare([{'op': 'RETIRE', 'id': identifier}]))
        raw = self.target.read_bytes()
        self.assertIn(b'Preserve all bytes', raw)
        self.assertIn(b'retired', raw)
        self.assertIn(identifier.encode(), raw)
        self.assertIn(self.original, raw.replace(raw.splitlines(keepends=True)[5], b'', 1))

    def test_legacy_without_id_gets_stable_id_only_when_touched(self):
        p = self.prepare([{'op': 'ADD', 'text': 'Another principle', 'date': '2026-10-07'}])
        legacy = [x for x in p['inventory'] if x['text'] == 'Legacy lesson'][0]
        self.apply(self.prepare([{'op': 'INC', 'id': legacy['id']}]))
        edited = self.prepare([{'op': 'EDIT', 'id': legacy['id'], 'text': 'New principle'}])
        self.assertEqual(edited['changes'][0]['id'], legacy['id'])
        self.apply(edited)
        self.assertIn(b'New principle', self.target.read_bytes())

    def test_heading_mistake_retirement_keeps_symptom_and_cause(self):
        original = 'Narrative\r\n## M-031 — Keep originals\r\n\r\n**증상:** old\n**원인:** cause\r\n'.encode()
        self.target.write_bytes(original)
        self.apply(self.prepare([{'op': 'INC', 'id': 'M-031'}, {'op': 'RETIRE', 'id': 'M-031'}]))
        raw = self.target.read_bytes()
        self.assertIn(b'"count":2', raw)
        self.assertIn(b'"state":"retired"', raw)
        self.assertIn('**증상:** old\n**원인:** cause\r\n'.encode(), raw)

    def test_combined_add_and_first_row_increment_uses_nonoverlapping_partial_edits(self):
        p = self.prepare([{'op': 'ADD', 'text': 'New principle', 'date': '2026-10-07'},
                          {'op': 'INC', 'id': 'L-001'}])
        self.apply(p)
        raw = self.target.read_bytes()
        self.assertIn(b'New principle', raw)
        self.assertIn('횟수 3 | [L-001]'.encode(), raw)
        self.assertIn(self.original.splitlines(keepends=True)[6], raw)

    def test_fenced_examples_remain_byte_identical_and_retired_entries_cannot_increment(self):
        example = b'```md\n## L-001 - example\n```\n'
        self.target.write_bytes(self.original + example)
        self.apply(self.prepare([{'op': 'RETIRE', 'id': 'L-001'}]))
        self.assertTrue(self.target.read_bytes().endswith(example))
        with self.assertRaisesRegex(self.module.LessonDeltaError, 'retired'):
            self.prepare([{'op': 'INC', 'id': 'L-001'}])

    def test_source_hash_drift_at_actual_atomic_replace_keeps_target_intact(self):
        p = self.prepare([{'op': 'INC', 'id': 'L-001', 'references': [self.ref()]}])
        state = importlib.import_module('vault_state')
        actual = state._atomic
        def drift(path, data, before_replace):
            self.source.write_bytes(self.source.read_bytes() + b'Changed\n')
            return actual(path, data, before_replace)
        with mock.patch.object(state, '_atomic', side_effect=drift):
            with self.assertRaisesRegex(self.module.LessonDeltaError, 'source_changed'):
                self.apply(p)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_concurrent_same_proposal_increments_only_once_and_replay_is_stale(self):
        p = self.prepare([{'op': 'INC', 'id': 'L-001'}])
        gate, results = threading.Barrier(2), []
        def writer():
            gate.wait()
            try:
                results.append(self.apply(p)['status'])
            except self.module.LessonDeltaError:
                results.append('rejected')
        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(results), ['applied', 'rejected'])
        self.assertIn('횟수 3 | [L-001]'.encode(), self.target.read_bytes())
        with self.assertRaises(self.module.LessonDeltaError):
            self.apply(p)

    def test_hardlinked_selected_note_and_source_are_rejected(self):
        import os
        original = self.target.read_bytes()
        os.link(self.target, self.vault / 'notes/linked.md')
        with self.assertRaises(self.module.LessonDeltaError):
            self.prepare([{'op': 'INC', 'id': 'L-001'}])
        self.assertEqual(self.target.read_bytes(), original)

    def test_deny_policy_blocks_explicit_reference_before_open(self):
        ref = dict(self.ref(), path='private/secret.md')
        with self.assertRaises(self.module.LessonDeltaError):
            self.prepare([{'op': 'INC', 'id': 'L-001', 'references': [ref]}])

    def test_metadata_does_not_override_a_different_historical_id_or_counter(self):
        for identifier, count in [('L-002', 2), ('L-001', 99)]:
            meta = json.dumps({'id': identifier, 'count': count, 'state': 'active', 'references': []})
            raw = self.original.replace('상태: 관찰중\r\n'.encode(),
                                        ('상태: 관찰중 <!-- agentic-vault:lesson ' + meta + ' -->\r\n').encode(), 1)
            self.target.write_bytes(raw)
            with self.assertRaises(self.module.LessonDeltaError):
                self.prepare([{'op': 'INC', 'id': 'L-001'}])

    def test_engine_owned_rules_and_managed_blocks_cannot_be_changed_as_lessons(self):
        for raw, code in [(b'<!-- agentic-vault:rule engine=1 -->\n## M-001 - Rule\n', 'engine_owned'),
                          (b'<!-- agentic-vault:begin rules -->\n## M-001 - Rule\n<!-- agentic-vault:end rules -->\n',
                           'managed_block_changed')]:
            self.target.write_bytes(raw)
            with self.assertRaisesRegex(self.module.LessonDeltaError, code):
                self.prepare([{'op': 'INC', 'id': 'M-001'}])
        path = self.write('.claude/rules/vault-contract.md', b'## M-001 - Engine contract\n')
        with self.assertRaisesRegex(self.module.LessonDeltaError, 'engine_owned'):
            self.module.prepare_delta(self.vault, path.relative_to(self.vault).as_posix(), [{'op': 'INC', 'id': 'M-001'}])

    def test_missing_approval_never_creates_runtime_or_modifies_note(self):
        p = self.prepare([{'op': 'INC', 'id': 'L-001'}])
        with self.assertRaisesRegex(self.module.LessonDeltaError, 'approval'):
            self.apply(p, approve=False)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse((self.vault / '00-meta/.agentic-vault/runtime').exists())

    def test_stale_base_source_and_config_block_apply(self):
        for kind in ('target', 'source', 'config'):
            with self.subTest(kind=kind):
                self.target.write_bytes(self.original)
                self.source.write_bytes(b'# Host observation\nReported, unverified\n')
                config = self.vault / '00-meta/vault-config.json'
                oldconfig = config.read_bytes()
                p = self.prepare([{'op': 'INC', 'id': 'L-001', 'references': [self.ref()]}])
                path = self.target if kind == 'target' else self.source if kind == 'source' else config
                path.write_bytes(path.read_bytes() + b'\n')
                target_before = self.target.read_bytes()
                with self.assertRaises(self.module.LessonDeltaError):
                    self.apply(p)
                self.assertEqual(self.target.read_bytes(), target_before)
                config.write_bytes(oldconfig)

    def test_rehashed_forged_edits_and_candidate_cannot_apply(self):
        p = self.prepare([{'op': 'INC', 'id': 'L-001'}])
        p['edits'][0]['replacement'] = 'Unrelated takeover'
        p['proposal_sha256'] = sha(self.module.encode({k: v for k, v in p.items() if k != 'proposal_sha256'}))
        with self.assertRaisesRegex(self.module.LessonDeltaError, 'proposal'):
            self.apply(p)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_duplicate_ids_unknown_id_bad_counter_or_duplicate_add_abstain(self):
        for ops in ([{'op': 'INC', 'id': 'L-999'}],
                    [{'op': 'INC', 'id': 'L-001', 'amount': True}],
                    [{'op': 'INC', 'id': 'L-001', 'amount': 0}],
                    [{'op': 'EDIT', 'id': 'L-001', 'text': 'multi\nline'}],
                    [{'op': 'ADD', 'text': 'Check bytes', 'date': '2026-10-07'}]):
            with self.subTest(ops=ops), self.assertRaises(self.module.LessonDeltaError):
                self.prepare(ops)
        self.target.write_bytes(self.original + b'\n## L-001 - duplicate\n')
        with self.assertRaisesRegex(self.module.LessonDeltaError, 'duplicate'):
            self.prepare([{'op': 'INC', 'id': 'L-001'}])

    def test_denied_excluded_runtime_and_receipt_paths_are_rejected_before_read(self):
        for path in ('private/secret.md', 'excluded/secret.md',
                     '00-meta/.agentic-vault/runtime/check.md', '00-meta/proposals/receipt.md', '../outside.md'):
            with self.subTest(path=path), self.assertRaises(self.module.LessonDeltaError):
                self.module.prepare_delta(self.vault, path, [{'op': 'ADD', 'text': 'Hello', 'date': '2026-10-07'}])

    def test_patch_boundary_config_race_and_common_note_lock(self):
        p = self.prepare([{'op': 'INC', 'id': 'L-001'}])
        state = importlib.import_module('vault_state')
        actual = state.patch_note
        def race(*args, **kwargs):
            config = self.vault / '00-meta/vault-config.json'
            config.write_bytes(config.read_bytes() + b'\n')
            return actual(*args, **kwargs)
        with mock.patch.object(self.module.state, 'patch_note', side_effect=race):
            with self.assertRaises(self.module.LessonDeltaError):
                self.apply(p)
        self.assertEqual(self.target.read_bytes(), self.original)
        p = self.prepare([{'op': 'INC', 'id': 'L-001'}])
        with state.advisory_lock(self.vault, 'note:notes/lessons.md'):
            with self.assertRaisesRegex(self.module.LessonDeltaError, 'lock_busy'):
                self.apply(p)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_cli_round_trip_and_malformed_bounded_input(self):
        script = SCRIPTS / 'vault_lesson_delta.py'
        command = [sys.executable, str(script), '--vault', str(self.vault), 'prepare', '--path', 'notes/lessons.md']
        run = subprocess.run(command, input=json.dumps([{'op': 'INC', 'id': 'L-001'}]), text=True, encoding='utf-8', capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(run.stdout.isascii(), 'JSON CLI output must survive Windows console encodings')
        p = json.loads(run.stdout)
        run = subprocess.run([sys.executable, str(script), '--vault', str(self.vault), 'apply', '--approve'],
                             input=json.dumps(p), text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        for raw in ('{bad', '[' * 10000, 'x' * (512 * 1024 + 1)):
            run = subprocess.run(command, input=raw, text=True, capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertNotIn(str(self.vault), run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
