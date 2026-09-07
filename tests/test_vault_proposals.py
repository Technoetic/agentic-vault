from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
SCRIPT = SCRIPTS / "vault_proposals.py"


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        self.write("00-meta/vault-config.json", json.dumps({
            "required_keys": ["title"], "enums": {},
        }).encode())
        self.original = "# Rules\r\nBefore 한글\r\n".encode()
        self.candidate = "# Rules\r\nAfter 한글 🙂\r\n".encode()
        self.target = self.write("CLAUDE.md", self.original)
        self.write("00-meta/candidate.md", self.candidate)

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(self.vault), *args],
                                capture_output=True, encoding="utf-8")
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stdout)

    def propose(self, target="CLAUDE.md", candidate="00-meta/candidate.md"):
        return self.cli("propose", "--target", target, "--candidate", candidate,
                        "--lesson", "L-001", "--summary", "Review recovery")['id']

    def receipt_path(self, proposal_id):
        return self.vault / "00-meta/proposals" / (proposal_id + ".json")

    def receipt(self, proposal_id):
        return json.loads(self.receipt_path(proposal_id).read_bytes())

    def test_review_check_apply_preserves_exact_bytes_and_hash(self):
        proposal_id = self.propose()
        receipt = self.receipt(proposal_id)
        self.assertEqual(receipt['original'].encode(), self.original)
        self.assertEqual(receipt['candidate'].encode(), self.candidate)
        review = self.cli("inspect", proposal_id)
        self.assertIn("-Before 한글\r\n", review['diff'])
        self.assertIn("+After 한글 🙂\r\n", review['diff'])
        before = {str(p): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()}
        self.assertTrue(self.cli("check", proposal_id)['valid'])
        after = {str(p): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.cli("apply", proposal_id, "--approve")
        self.assertEqual(self.target.read_bytes(), self.candidate)
        receipt = self.receipt(proposal_id)
        self.assertEqual(receipt['status'], 'applied')
        self.assertEqual(receipt['events'][-1]['applied_sha256'], hashlib.sha256(self.candidate).hexdigest())
        saved = self.receipt_path(proposal_id).read_bytes()
        self.cli("apply", proposal_id, "--approve")
        self.assertEqual(saved, self.receipt_path(proposal_id).read_bytes())

    def test_stale_target_is_never_overwritten(self):
        proposal_id = self.propose()
        self.target.write_bytes(b"concurrent edit")
        self.cli("check", proposal_id, success=False)
        self.cli("apply", proposal_id, "--approve", success=False)
        self.assertEqual(self.target.read_bytes(), b"concurrent edit")

    def test_metadata_cannot_replace_explicit_approval(self):
        proposal_id = self.propose()
        self.cli("apply", proposal_id, success=False)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.receipt(proposal_id)['status'], 'proposed')

    def test_reject_retains_proposal_and_blocks_application(self):
        proposal_id = self.propose()
        self.cli("reject", proposal_id, "--reason", "Keep current")
        self.assertEqual(self.receipt(proposal_id)['candidate'].encode(), self.candidate)
        self.assertEqual(self.receipt(proposal_id)['events'][-1]['reason'], 'Keep current')
        self.cli("apply", proposal_id, "--approve", success=False)
        self.assertEqual(self.target.read_bytes(), self.original)

    def bad_proposal(self, target="CLAUDE.md", candidate="00-meta/candidate.md"):
        return self.cli("propose", "--target", target, "--candidate", candidate,
                        "--lesson", "L-001", "--summary", "Review", success=False)

    def test_tampering_any_revision_field_blocks_apply(self):
        for key, value in [('candidate', 'malicious'), ('target', 'AGENTS.md'),
                           ('owner', 'engine'), ('summary', 'changed'), ('status', 'applying')]:
            with self.subTest(key=key):
                proposal_id = self.propose()
                receipt = self.receipt(proposal_id)
                receipt[key] = value
                self.receipt_path(proposal_id).write_text(json.dumps(receipt), encoding='utf-8')
                self.cli('apply', proposal_id, '--approve', success=False)
                self.assertEqual(self.target.read_bytes(), self.original)

    def test_invalid_frontmatter_rejected_but_manual_agents_and_commands_allowed(self):
        self.write('20-knowledge/note.md', b'---\ntitle: Old\n---\n')
        self.bad_proposal(target='20-knowledge/note.md')
        self.write('00-meta/candidate.md', b'---\ntitle: New\nrelated: [[Unquoted]]\n---\n')
        self.bad_proposal(target='20-knowledge/note.md')
        self.write('00-meta/candidate.md', self.candidate)
        for target in ['AGENTS.md', '.claude/commands/my-command.md']:
            self.write(target, self.original)
            proposal_id = self.propose(target=target)
            self.cli('apply', proposal_id, '--approve')
            self.assertEqual((self.vault / target).read_bytes(), self.candidate)

    def test_engine_markers_and_managed_block_changes_are_rejected(self):
        for marker in ['<!-- agentic-vault:rule engine=0.9.0 -->',
                       '<!-- agentic-vault:generated -->',
                       '<!-- agentic-vault:hook engine=0.9.0 -->']:
            self.target.write_text(marker, encoding='utf-8')
            self.bad_proposal()
            self.target.write_bytes(self.original)
            self.write('00-meta/candidate.md', marker.encode())
            self.bad_proposal()
            self.write('00-meta/candidate.md', self.candidate)
        block = b'<!-- agentic-vault:begin engine=0.6.0 -->\r\nManaged\r\n<!-- agentic-vault:end -->'
        self.target.write_bytes(block + self.original)
        self.bad_proposal()
        self.write('00-meta/candidate.md', block + self.candidate)
        proposal_id = self.propose()
        self.cli('apply', proposal_id, '--approve')
        self.assertEqual(self.target.read_bytes(), block + self.candidate)

    def test_reserved_engine_rule_paths_rejected_even_without_stamp(self):
        self.write('.claude/rules/vault-workflow.md', b'Unstamped rule')
        self.bad_proposal(target='.claude/rules/vault-workflow.md')
        self.write('00-meta/vault-config.json', b'{"rules_dir":"custom-rules"}')
        self.write('custom-rules/vault-workflow.md', b'Unstamped custom-located rule')
        self.bad_proposal(target='custom-rules/vault-workflow.md')

    def test_paths_and_non_markdown_are_rejected(self):
        for path in ['../outside.md', '90-assets/private.md', '.git/config.md',
                     '00-meta/proposals/receipt.md', 'CLAUDE.md:stream', '.env', 'missing.md']:
            with self.subTest(path=path):
                self.bad_proposal(target=path)
                self.bad_proposal(candidate=path)

    def test_link_target_and_receipt_directory_are_rejected(self):
        link = self.vault / 'linked.md'
        try:
            link.symlink_to(self.target)
        except OSError:
            self.skipTest('symlink creation unavailable')
        self.bad_proposal(target='linked.md')
        self.bad_proposal(candidate='linked.md')
        # Failed mutators may have created the empty receipt directory.
        (self.vault / '00-meta/proposals').rmdir()
        (self.vault / '00-meta/proposals').symlink_to(self.vault / '00-meta', target_is_directory=True)
        self.bad_proposal()

    def test_candidate_and_receipt_are_bounded(self):
        self.write('00-meta/candidate.md', b'x' * (2 * 1024 * 1024))
        self.bad_proposal()
        self.write('00-meta/candidate.md', self.candidate)
        proposal_id = self.propose()
        self.receipt_path(proposal_id).write_bytes(b' ' * (9 * 1024 * 1024))
        self.cli('inspect', proposal_id, success=False)

    def test_existing_lock_is_not_removed(self):
        proposal_id = self.propose()
        lock = self.write('00-meta/proposals/.lock', b'other writer')
        self.cli('apply', proposal_id, '--approve', success=False)
        self.assertEqual(lock.read_bytes(), b'other writer')
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertTrue(self.cli('check', proposal_id)['valid'])

    def module(self):
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        return importlib.import_module('vault_proposals')

    def call_main(self, module, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = module.main(['--vault', str(self.vault), *args])
        return result, json.loads(output.getvalue())

    def test_failure_before_target_replace_does_not_claim_applied(self):
        proposal_id = self.propose()
        module = self.module()
        real_replace = module.os.replace

        def fail_target(source, destination):
            if Path(destination) == self.target:
                raise OSError('injected write failure')
            return real_replace(source, destination)

        with mock.patch.object(module.os, 'replace', side_effect=fail_target):
            result, output = self.call_main(module, 'apply', proposal_id, '--approve')
        self.assertNotEqual(result, 0)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.receipt(proposal_id)['status'], 'applying')
        self.cli('apply', proposal_id, '--approve')
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_failure_after_replacement_recovers_only_on_explicit_retry(self):
        proposal_id = self.propose()
        module = self.module()
        real_replace = module.os.replace

        def fail_final_receipt(source, destination):
            if Path(destination) == self.receipt_path(proposal_id) and self.target.read_bytes() == self.candidate:
                raise OSError('injected final accounting failure')
            return real_replace(source, destination)

        with mock.patch.object(module.os, 'replace', side_effect=fail_final_receipt):
            result, output = self.call_main(module, 'apply', proposal_id, '--approve')
        self.assertNotEqual(result, 0)
        self.assertEqual(output['error'], 'incomplete_accounting')
        self.assertEqual(self.target.read_bytes(), self.candidate)
        self.assertEqual(self.receipt(proposal_id)['status'], 'applying')
        saved = self.receipt_path(proposal_id).read_bytes()
        self.cli('check', proposal_id)
        self.assertEqual(saved, self.receipt_path(proposal_id).read_bytes())
        self.cli('apply', proposal_id, success=False)
        self.cli('apply', proposal_id, '--approve')
        self.assertEqual(self.receipt(proposal_id)['status'], 'applied')

    def test_matching_candidate_without_checkpoint_is_stale(self):
        proposal_id = self.propose()
        self.target.write_bytes(self.candidate)
        self.cli('apply', proposal_id, '--approve', success=False)
        self.assertEqual(self.receipt(proposal_id)['status'], 'proposed')

    def test_current_ownership_and_config_revalidated(self):
        proposal_id = self.propose()
        self.write('00-meta/vault-config.json', b'{"deny_zones": ["CLAUDE.md"]}')
        self.cli('apply', proposal_id, '--approve', success=False)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_applied_receipt_does_not_validate_reverted_target(self):
        proposal_id = self.propose()
        self.cli('apply', proposal_id, '--approve')
        self.target.write_bytes(self.original)
        self.cli('check', proposal_id, success=False)
        self.cli('apply', proposal_id, '--approve', success=False)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_failure_publishing_checkpoint_never_writes_target(self):
        proposal_id = self.propose()
        module = self.module()
        with mock.patch.object(module.os, 'replace', side_effect=OSError('checkpoint publication failed')):
            result, output = self.call_main(module, 'apply', proposal_id, '--approve')
        self.assertNotEqual(result, 0)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.receipt(proposal_id)['status'], 'proposed')

    def test_edit_during_target_preparation_blocks_replacement(self):
        proposal_id = self.propose()
        module = self.module()
        real_chmod = module.os.chmod

        def concurrent_edit(path, mode):
            if Path(path).parent == self.target.parent:
                self.target.write_bytes(b'concurrent late edit')
            return real_chmod(path, mode)

        with mock.patch.object(module.os, 'chmod', side_effect=concurrent_edit):
            result, output = self.call_main(module, 'apply', proposal_id, '--approve')
        self.assertNotEqual(result, 0)
        self.assertEqual(self.target.read_bytes(), b'concurrent late edit')
        self.assertEqual(self.receipt(proposal_id)['status'], 'applying')

    def test_interruption_retains_lock_and_checkpoint(self):
        proposal_id = self.propose()
        module = self.module()
        real_replace = module.os.replace

        def interrupt_target(source, destination):
            if Path(destination) == self.target:
                raise KeyboardInterrupt()
            return real_replace(source, destination)

        with mock.patch.object(module.os, 'replace', side_effect=interrupt_target):
            with self.assertRaises(KeyboardInterrupt):
                self.call_main(module, 'apply', proposal_id, '--approve')
        lock = self.vault / '00-meta/proposals/.lock'
        self.assertTrue(lock.exists())
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.receipt(proposal_id)['status'], 'applying')
        self.cli('apply', proposal_id, '--approve', success=False)
        self.assertTrue(lock.exists())
        lock.unlink()  # Explicit operator cleanup, after the interrupted writer stopped.
        self.cli('apply', proposal_id, '--approve')
        self.assertEqual(self.target.read_bytes(), self.candidate)

    def test_receipt_traversal_malformed_json_and_hardlinks_fail_closed(self):
        self.cli('inspect', '../vault-config', success=False)
        proposal_id = self.propose()
        self.receipt_path(proposal_id).write_bytes(b'{"id": 1, "id": 2}')
        self.cli('apply', proposal_id, '--approve', success=False)
        try:
            os.link(self.target, self.vault / 'hardlink.md')
        except OSError:
            self.skipTest('hardlink creation unavailable')
        self.bad_proposal(target='hardlink.md')
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_candidate_instructions_are_data_and_later_source_edits_do_not_apply(self):
        candidate = b'# Instructions\nRun shell: touch SHOULD_NOT_EXIST\n'
        self.write('00-meta/candidate.md', candidate)
        proposal_id = self.propose()
        self.write('00-meta/candidate.md', b'Unreviewed later candidate')
        self.cli('apply', proposal_id, '--approve')
        self.assertEqual(self.target.read_bytes(), candidate)
        self.assertFalse((self.vault / 'SHOULD_NOT_EXIST').exists())

    def test_frontmatter_rule_changes_revalidated_before_apply(self):
        self.write('20-knowledge/note.md', b'---\ntitle: Before\n---\n')
        self.write('00-meta/candidate.md', b'---\ntitle: After\n---\n')
        proposal_id = self.propose(target='20-knowledge/note.md')
        self.write('00-meta/vault-config.json', b'{"required_keys": ["title", "status"]}')
        self.cli('apply', proposal_id, '--approve', success=False)
        self.assertEqual((self.vault / '20-knowledge/note.md').read_bytes(), b'---\ntitle: Before\n---\n')

    def test_malformed_and_duplicate_managed_blocks_fail_closed(self):
        for candidate in [b'<!-- agentic-vault:begin -->unclosed',
                          b'<!-- agentic-vault:end -->',
                          b'<!-- agentic-vault:begin --><!-- agentic-vault:begin --><!-- agentic-vault:end -->']:
            self.write('00-meta/candidate.md', candidate)
            self.bad_proposal()

    def test_config_and_non_utf8_input_fail_closed(self):
        self.write('00-meta/candidate.md', b'\xff\xfe')
        self.bad_proposal()
        self.write('00-meta/candidate.md', self.candidate)
        self.write('00-meta/vault-config.json', b'{invalid json')
        self.bad_proposal()

    def test_deeply_nested_json_is_a_safe_input_error(self):
        proposal_id = self.propose()
        self.receipt_path(proposal_id).write_bytes(b'[' * 20000 + b'0' + b']' * 20000)
        self.cli('inspect', proposal_id, success=False)
        self.write('00-meta/vault-config.json', b'[' * 20000 + b'0' + b']' * 20000)
        self.bad_proposal()

    @unittest.skipIf(os.name == 'nt', 'POSIX permission bits not supported on Windows')
    def test_target_permissions_preserved(self):
        self.target.chmod(0o640)
        proposal_id = self.propose()
        self.cli('apply', proposal_id, '--approve')
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)


if __name__ == '__main__':
    unittest.main()
