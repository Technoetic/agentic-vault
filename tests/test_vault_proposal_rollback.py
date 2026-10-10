from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
SCRIPT = SCRIPTS / 'vault_proposals.py'


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        self.module = importlib.import_module('vault_proposals')
        self.original = '# Rules\r\nBefore 한글 🧭\r\n'.encode('utf-8')
        self.candidate = '# Rules\r\nAfter 한글 🐾\r\n'.encode('utf-8')
        self.config = self.write('00-meta/vault-config.json', b'{"required_keys":["title"],"enums":{}}')
        self.target = self.write('CLAUDE.md', self.original)
        self.write('00-meta/candidate.md', self.candidate)
        self.proposal_id = self.cli('propose', '--target', 'CLAUDE.md', '--candidate',
                                    '00-meta/candidate.md', '--lesson', 'L-001',
                                    '--summary', 'Undo one proposal')['id']
        self.cli('apply', self.proposal_id, '--approve')
        self.receipt_path = self.vault / '00-meta/proposals' / (self.proposal_id + '.json')

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault), *args],
                                capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout else {}

    def call(self, *args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = self.module.main(['--vault', str(self.vault), *args])
        return code, json.loads(stream.getvalue())

    def receipt(self):
        return json.loads(self.receipt_path.read_bytes())

    def inventory(self):
        return {p.relative_to(self.vault).as_posix(): p.read_bytes()
                for p in self.vault.rglob('*') if p.is_file()}

    def preview(self):
        return self.cli('rollback-preview', self.proposal_id)

    def rollback(self, preview_hash, success=True):
        return self.cli('rollback', self.proposal_id, '--approve', '--preview-hash',
                        preview_hash, success=success)

    def test_cli_preview_is_read_only_and_rollback_preserves_exact_bytes_and_all_events(self):
        before = self.inventory()
        applied_events = self.receipt()['events']
        preview = self.preview()
        self.assertEqual(before, self.inventory())
        self.assertEqual(preview['preview_sha256'], self.preview()['preview_sha256'])
        self.assertEqual(preview['binding']['candidate_sha256'], hashlib.sha256(self.candidate).hexdigest())
        self.assertEqual(preview['binding']['original_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertIn('-After 한글 🐾\r\n', preview['diff'])
        self.assertIn('+Before 한글 🧭\r\n', preview['diff'])
        result = self.rollback(preview['preview_sha256'])
        self.assertEqual(result['status'], 'rolled_back')
        self.assertEqual(self.target.read_bytes(), self.original)
        receipt = self.receipt()
        self.assertEqual(receipt['events'][:5], applied_events)
        self.assertEqual([e['event'] for e in receipt['events'][5:]],
                         ['rollback_approved', 'rolling_back', 'rolled_back'])
        self.assertEqual(receipt['events'][-1]['restored_sha256'], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(receipt['status'], 'rolled_back')
        saved = self.inventory()
        self.assertTrue(self.rollback(preview['preview_sha256'])['idempotent'])
        self.assertEqual(saved, self.inventory())
        self.cli('apply', self.proposal_id, '--approve', success=False)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_rollback_requires_explicit_approval_and_correct_preview_hash(self):
        preview = self.preview()
        before = self.inventory()
        for args in [('rollback', self.proposal_id, '--preview-hash', preview['preview_sha256']),
                     ('rollback', self.proposal_id, '--approve'),
                     ('rollback', self.proposal_id, '--approve', '--preview-hash', '0' * 64)]:
            self.cli(*args, success=False)
            self.assertEqual(before, self.inventory())

    def test_user_edit_after_preview_is_never_overwritten(self):
        preview = self.preview()
        self.target.write_bytes(b'User edit after preview\n')
        before = self.inventory()
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(before, self.inventory())
        self.cli('rollback-preview', self.proposal_id, success=False)

    def test_changed_config_or_receipt_invalidates_preview_without_target_write(self):
        preview = self.preview()
        original_config = self.config.read_bytes()
        for modification in (lambda: self.config.write_bytes(original_config + b'\n'),
                             lambda: self.receipt_path.write_bytes(self.receipt_path.read_bytes() + b' ')):
            with self.subTest(modification=modification):
                modification()
                before = self.inventory()
                self.rollback(preview['preview_sha256'], success=False)
                self.assertEqual(before, self.inventory())
                self.config.write_bytes(original_config)

    def test_corrupt_before_image_and_duplicate_or_nonfinite_receipt_refuse_preview(self):
        original_receipt = self.receipt_path.read_bytes()
        receipt = self.receipt()
        receipt['original'] = 'Changed before image'
        altered = json.dumps(receipt).encode()
        duplicate = original_receipt.rstrip()[:-1] + b',"status":"applied"}'
        nonfinite = original_receipt.replace(b'"version":1', b'"version":NaN')
        for raw in (altered, duplicate, nonfinite):
            with self.subTest(raw=raw[:20]):
                self.receipt_path.write_bytes(raw)
                before = self.inventory()
                self.cli('rollback-preview', self.proposal_id, success=False)
                self.assertEqual(before, self.inventory())
        self.receipt_path.write_bytes(original_receipt)

    def test_late_user_edit_during_temp_preparation_prevents_replacement(self):
        preview = self.preview()
        real_chmod = self.module.os.chmod
        def edit_target(path, mode):
            if Path(path).parent == self.target.parent:
                self.target.write_bytes(b'Late user edit')
            return real_chmod(path, mode)
        with mock.patch.object(self.module.os, 'chmod', side_effect=edit_target):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), b'Late user edit')
        self.assertEqual(self.receipt()['status'], 'rolling_back')
        before = self.inventory()
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(before, self.inventory())

    def test_policy_change_during_temp_preparation_prevents_replacement(self):
        preview = self.preview()
        real_chmod = self.module.os.chmod
        def edit_policy(path, mode):
            if Path(path).parent == self.target.parent:
                self.config.write_bytes(b'{"deny_zones":["CLAUDE.md"]}')
            return real_chmod(path, mode)
        with mock.patch.object(self.module.os, 'chmod', side_effect=edit_policy):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_receipt_change_during_temp_preparation_prevents_replacement(self):
        preview = self.preview()
        real_chmod = self.module.os.chmod
        def edit_receipt(path, mode):
            if Path(path).parent == self.target.parent:
                self.receipt_path.write_bytes(self.receipt_path.read_bytes() + b' ')
            return real_chmod(path, mode)
        with mock.patch.object(self.module.os, 'chmod', side_effect=edit_receipt):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_corrupted_prepared_restore_bytes_never_replace_target(self):
        preview = self.preview()
        real_chmod = self.module.os.chmod
        def corrupt_temporary(path, mode):
            if Path(path).parent == self.target.parent:
                Path(path).write_bytes(b'Corrupt prepared restore bytes')
            return real_chmod(path, mode)
        with mock.patch.object(self.module.os, 'chmod', side_effect=corrupt_temporary):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), self.candidate)
        self.assertEqual(self.receipt()['status'], 'rolling_back')

    def test_receipt_change_after_guard_target_read_blocks_replacement(self):
        preview = self.preview()
        real_read = self.module.stable_read
        def edit_after_read(resolver, limit, **kwargs):
            result = real_read(resolver, limit, **kwargs)
            if resolver() == self.target and self.receipt()['status'] == 'rolling_back':
                self.receipt_path.write_bytes(self.receipt_path.read_bytes() + b' ')
            return result
        with mock.patch.object(self.module, 'stable_read', side_effect=edit_after_read):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_user_edit_during_last_guard_config_read_blocks_replacement(self):
        preview = self.preview()
        real_read = self.module.stable_read
        state = {'armed': False, 'changed': False}
        def edit_after_read(resolver, limit, **kwargs):
            result = real_read(resolver, limit, **kwargs)
            if resolver() == self.target and self.receipt()['status'] == 'rolling_back':
                state['armed'] = True
            if resolver() == self.config and state['armed'] and not state['changed']:
                self.target.write_bytes(b'User edit during final configuration read')
                state['changed'] = True
            return result
        with mock.patch.object(self.module, 'stable_read', side_effect=edit_after_read):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), b'User edit during final configuration read')

    def failed_accounting(self, preview_hash):
        real_replace = self.module.os.replace
        def fail_final_receipt(source, destination):
            if Path(destination) == self.receipt_path and self.target.read_bytes() == self.original:
                raise OSError('Injected final accounting failure')
            return real_replace(source, destination)
        with mock.patch.object(self.module.os, 'replace', side_effect=fail_final_receipt):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview_hash)
        self.assertNotEqual(code, 0)
        self.assertEqual(output['error'], 'incomplete_accounting')
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.receipt()['status'], 'rolling_back')

    def test_failed_final_accounting_recovers_only_with_explicit_retry(self):
        preview = self.preview()
        self.failed_accounting(preview['preview_sha256'])
        before = self.inventory()
        recovered_preview = self.preview()
        self.assertTrue(recovered_preview['recovery_available'])
        self.assertEqual(recovered_preview['preview_sha256'], preview['preview_sha256'])
        self.assertEqual(before, self.inventory())
        self.cli('rollback', self.proposal_id, '--preview-hash', preview['preview_sha256'], success=False)
        self.assertEqual(before, self.inventory())
        self.rollback(preview['preview_sha256'])
        self.assertEqual(self.receipt()['status'], 'rolled_back')

    def test_recovery_refuses_later_edit_even_after_original_was_restored(self):
        preview = self.preview()
        self.failed_accounting(preview['preview_sha256'])
        self.target.write_bytes(b'Later user edit after replacement')
        before = self.inventory()
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(before, self.inventory())

    def test_checkpoint_publication_failure_never_writes_target(self):
        preview = self.preview()
        with mock.patch.object(self.module.os, 'replace', side_effect=OSError('Checkpoint failed')):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), self.candidate)
        self.assertEqual(self.receipt()['status'], 'applied')
        self.rollback(preview['preview_sha256'])

    def test_failure_before_replacement_can_resume_from_candidate_checkpoint(self):
        preview = self.preview()
        real_replace = self.module.os.replace
        def fail_target(source, destination):
            if Path(destination) == self.target:
                raise OSError('Replacement failed')
            return real_replace(source, destination)
        with mock.patch.object(self.module.os, 'replace', side_effect=fail_target):
            code, output = self.call('rollback', self.proposal_id, '--approve',
                                     '--preview-hash', preview['preview_sha256'])
        self.assertNotEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), self.candidate)
        self.assertEqual(self.receipt()['status'], 'rolling_back')
        self.rollback(preview['preview_sha256'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_existing_lock_is_preserved_and_preview_does_not_take_lock(self):
        preview = self.preview()
        lock = self.write('00-meta/proposals/.lock', b'Existing writer')
        self.assertEqual(self.preview()['preview_sha256'], preview['preview_sha256'])
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(lock.read_bytes(), b'Existing writer')
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_interruption_after_restore_retains_lock_and_checkpoint_for_manual_recovery(self):
        preview = self.preview()
        real_replace = self.module.os.replace
        def interrupt_after_restore(source, destination):
            result = real_replace(source, destination)
            if Path(destination) == self.target:
                raise KeyboardInterrupt()
            return result
        with mock.patch.object(self.module.os, 'replace', side_effect=interrupt_after_restore):
            with self.assertRaises(KeyboardInterrupt):
                self.call('rollback', self.proposal_id, '--approve',
                          '--preview-hash', preview['preview_sha256'])
        lock = self.vault / '00-meta/proposals/.lock'
        self.assertTrue(lock.exists())
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.receipt()['status'], 'rolling_back')
        self.rollback(preview['preview_sha256'], success=False)
        self.assertTrue(lock.exists())
        lock.unlink()  # Operator cleanup only after the interrupted writer stopped.
        self.rollback(preview['preview_sha256'])
        self.assertEqual(self.receipt()['status'], 'rolled_back')

    def test_unsafe_target_and_receipt_links_are_refused(self):
        preview = self.preview()
        link = self.vault / 'other.md'
        try:
            os.link(self.target, link)
        except OSError:
            self.skipTest('Hardlink creation unavailable')
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(link.read_bytes(), self.candidate)
        link.unlink()
        os.link(self.receipt_path, self.vault / 'receipt-copy.json')
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_denied_excluded_reserved_and_shared_targets_never_get_undo_preview(self):
        for target in ('50-projects/My handoff.md', '50-projects/My tasks.md'):
            self.write(target, b'---\ntitle: Before\n---\n')
            self.write('00-meta/candidate.md', b'---\ntitle: After\n---\n')
            proposal = self.cli('propose', '--target', target, '--candidate', '00-meta/candidate.md',
                                '--lesson', 'L-002', '--summary', 'Shared state')['id']
            self.cli('apply', proposal, '--approve')
            self.cli('rollback-preview', proposal, success=False)
        self.config.write_bytes(b'{"exclude_dirs":["CLAUDE.md"]}')
        self.cli('rollback-preview', self.proposal_id, success=False)
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_metrics_remain_readable_and_rolled_back_proposal_is_ineligible(self):
        metrics = importlib.import_module('vault_lesson_metrics')
        observation = dict(attempt_id='A-001', session_id='S-001', applicability='applicable',
                           outcome='success', verification_status='unverified', evidence_ids=[])
        metrics.record_observation(self.vault, self.proposal_id, observation)
        preview = self.preview()
        self.rollback(preview['preview_sha256'])
        summary = metrics.summarize(self.vault, self.proposal_id)
        self.assertEqual(summary['proposal_status'], 'rolled_back')
        self.assertFalse(summary['current_eligibility']['eligible'])
        self.assertEqual(summary['counts']['observations'], 1)

    def test_not_applied_receipts_refuse_preview(self):
        self.write('00-meta/second.md', b'# Different candidate\n')
        proposal = self.cli('propose', '--target', 'CLAUDE.md', '--candidate', '00-meta/second.md',
                            '--lesson', 'L-002', '--summary', 'Pending proposal')['id']
        self.cli('rollback-preview', proposal, success=False)

        self.cli('reject', proposal, '--reason', 'Keep current')
        self.cli('rollback-preview', proposal, success=False)

    def test_legacy_applied_receipt_binds_current_config_since_preview(self):
        receipt = self.receipt()
        for key in ('config_sha256', 'policy_sha256'):
            receipt['events'][3].pop(key, None)
        self.module.Store(self.vault).save(receipt)
        self.config.write_bytes(b'{"required_keys":["title"],"enums":{},"hot_max_tokens":2000}')
        preview = self.preview()
        self.assertEqual(preview['binding']['config_sha256'], hashlib.sha256(self.config.read_bytes()).hexdigest())
        self.rollback(preview['preview_sha256'])
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_restored_original_must_still_pass_current_frontmatter_policy(self):
        target = self.write('20-knowledge/test.md', b'---\ntitle: Before\n---\n')
        self.write('00-meta/candidate.md', b'---\ntitle: After\nstatus: active\n---\n')
        proposal = self.cli('propose', '--target', '20-knowledge/test.md', '--candidate',
                            '00-meta/candidate.md', '--lesson', 'L-003', '--summary', 'Schema')['id']
        self.cli('apply', proposal, '--approve')
        self.config.write_bytes(b'{"required_keys":["title","status"]}')
        self.cli('rollback-preview', proposal, success=False)
        self.assertEqual(target.read_bytes(), b'---\ntitle: After\nstatus: active\n---\n')

    def test_rolled_back_idempotent_retry_refuses_later_user_edit(self):
        preview = self.preview()
        self.rollback(preview['preview_sha256'])
        self.target.write_bytes(b'User edit after completed rollback')
        before = self.inventory()
        self.rollback(preview['preview_sha256'], success=False)
        self.assertEqual(before, self.inventory())

    def test_cli_argument_errors_redact_unknown_values_without_mutating_files(self):
        sensitive_fixture = 'sk-test-only-not-a-real-key-' + 'x' * 4096
        before = self.inventory()
        cases = [
            ['rollback-preview', self.proposal_id, '--unknown-option', sensitive_fixture],
            ['rollback', self.proposal_id, '--approve', '--preview-hash', '0' * 64,
             '--unknown-option', sensitive_fixture],
            ['rollback', self.proposal_id, '--preview-hash', '0' * 64,
             '--approve=' + sensitive_fixture],
            [sensitive_fixture],
        ]
        for args in cases:
            result = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault), *args],
                                    capture_output=True, encoding='utf-8', timeout=30)
            self.assertTrue(sensitive_fixture not in result.stdout + result.stderr,
                            'CLI argument error disclosed an untrusted argument value')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, '')
            self.assertLessEqual(len(result.stdout), 64)
            self.assertEqual(json.loads(result.stdout), {'error': 'invalid_cli_arguments'})
            self.assertEqual(before, self.inventory())

    def test_cli_parent_and_rollback_help_remain_available(self):
        before = self.inventory()
        for args, expected_options in [(['--help'], ['rollback-preview', 'rollback']),
                                       (['--vault', str(self.vault), 'rollback', '--help'],
                                        ['--approve', '--preview-hash'])]:
            result = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                                    encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, '')
            self.assertIn('usage:', result.stdout)
            for option in expected_options:
                self.assertIn(option, result.stdout)
            self.assertEqual(before, self.inventory())

    def test_selected_ingest_exclusions_do_not_override_deny_or_rollback_boundaries(self):
        compile_quality = importlib.import_module('vault_compile_quality')
        self.config.write_bytes(b'{"exclude_dirs":["10-inbox","CLAUDE.md"],"deny_zones":["private"]}')
        contents = b'Amount: 12\nApproval remains pending.\n'
        source = self.write('10-inbox/source.md', contents)
        compiled = self.write('20-knowledge/result.md', contents)
        binding = lambda path: {'path': path.relative_to(self.vault).as_posix(),
                                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        before = self.inventory()
        try:
            result = compile_quality.check_compile(self.vault, [binding(source)], [binding(compiled)],
                                                   ['Approval remains pending.'])
        except compile_quality.QualityError:
            self.fail('Explicitly selected ingest source was blocked by retrieval exclusions')
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['originals_current'])
        denied = self.write('private/source.md', contents)
        with self.assertRaises(compile_quality.QualityError):
            compile_quality.check_compile(self.vault, [binding(denied)], [binding(compiled)], [])
        denied.unlink()
        self.cli('rollback-preview', self.proposal_id, success=False)
        self.assertEqual(before, self.inventory())
        self.assertEqual(self.target.read_bytes(), self.candidate)


if __name__ == '__main__':
    unittest.main()
