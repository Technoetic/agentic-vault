from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
SCRIPT = SCRIPTS / 'vault_index_fold.py'


class IndexFoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='av-fold-test-')
        self.vault = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.write('00-meta/vault-config.json', b'{"required_keys":["title"],"enums":{}}')
        self.index = ('# Index\r\n- History: [[Index history]]\r\n'
                      '- [x] T-1 [[Alpha]]  \r\n- [ ] T-2 [[Beta]]\r\n'
                      '- [x] T-3 [[Gamma]]\r\n').encode()
        self.write('00-meta/index.md', self.index)
        self.write('20-knowledge/Index history.md', b'# History\n')
        for name in ['Alpha', 'Beta', 'Gamma']:
            self.write('20-knowledge/'+name+'.md', ('# '+name+'\n').encode())

    def write(self, relative, raw):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def sha(self, relative):
        return hashlib.sha256((self.vault / relative).read_bytes()).hexdigest()

    def manifest(self):
        return {
            'version': 1,
            'source': {'path': '00-meta/index.md', 'expected_sha256': self.sha('00-meta/index.md')},
            'destination': {'path': '20-knowledge/Index history.md', 'expected_sha256': self.sha('20-knowledge/Index history.md')},
            'destination_link': {'start_line': 2, 'end_line': 2, 'quote': '- History: [[Index history]]\r\n'},
            'items': [{'id': 'T-1', 'state': 'completed',
                       'source': {'start_line': 3, 'end_line': 3, 'quote': '- [x] T-1 [[Alpha]]  \r\n'},
                       'references': [{'path': '20-knowledge/Alpha.md', 'expected_sha256': self.sha('20-knowledge/Alpha.md')}]}],
        }

    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'Read-only index fold preview implementation is missing')
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        return importlib.import_module('vault_index_fold')

    def preview(self, manifest=None):
        return self.module().preview(self.vault, self.manifest() if manifest is None else manifest)

    def incomplete(self, manifest, reason):
        result = self.preview(manifest)
        self.assertEqual(result['status'], 'incomplete')
        self.assertFalse(result['complete'])
        self.assertFalse(result['apply_supported'])
        self.assertIsNone(result['projected_index'])
        self.assertIsNone(result['archive_target'])
        self.assertIn(reason, [issue['reason'] for issue in result['issues']])
        return result

    def inventory(self):
        return {p.relative_to(self.vault).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.vault.rglob('*') if p.is_file()}

    def cli(self, *extra, raw=None):
        self.module()
        self.write('00-meta/fold.json', json.dumps(self.manifest(), ensure_ascii=False).encode() if raw is None else raw)
        before = self.inventory()
        result = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault),
                                 '--input', '00-meta/fold.json', *extra],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(self.inventory(), before)
        return result

    def test_preview_preserves_literal_rows_and_every_file_without_apply(self):
        before = self.inventory()
        result = self.preview()
        self.assertEqual(result['status'], 'preview')
        self.assertTrue(result['complete'])
        self.assertFalse(result['apply_supported'])
        self.assertEqual(result['moved'][0]['quote'], '- [x] T-1 [[Alpha]]  \r\n')
        self.assertEqual(result['projected_index']['content'],
                         '# Index\r\n- History: [[Index history]]\r\n'
                         '- [ ] T-2 [[Beta]]\r\n- [x] T-3 [[Gamma]]\r\n')
        self.assertEqual(result['archive_target']['projected_content'], '# History\n- [x] T-1 [[Alpha]]  \r\n')
        self.assertEqual(result['archive_target']['append'], '- [x] T-1 [[Alpha]]  \r\n')
        self.assertEqual(result['reachability'][0]['via_destination'], '20-knowledge/Index history.md')
        self.assertEqual(result['reachability'][0]['references'], ['20-knowledge/Alpha.md'])
        self.assertEqual(self.inventory(), before)

    def test_host_active_state_is_retained_even_when_row_has_checked_box(self):
        manifest = self.manifest()
        manifest['items'][0]['state'] = 'active'
        result = self.preview(manifest)
        self.assertTrue(result['complete'])
        self.assertEqual(result['moved'], [])
        self.assertEqual(result['retained'][0]['reason'], 'not_completed')
        self.assertEqual(result['projected_index']['content'].encode(), self.index)
        self.assertEqual(result['archive_target']['projected_content'], '# History\n')

    def test_changed_source_hash_blocks_even_when_selected_quote_still_matches(self):
        manifest = self.manifest()
        self.write('00-meta/index.md', self.index+b'other row\n')
        self.incomplete(manifest, 'stale_source')

    def test_source_hash_is_required_not_optional(self):
        manifest = self.manifest()
        del manifest['source']['expected_sha256']
        self.incomplete(manifest, 'invalid_manifest')

    def test_quote_requires_exact_trailing_spaces_and_crlf(self):
        manifest = self.manifest()
        manifest['items'][0]['source']['quote'] = '- [x] T-1 [[Alpha]]\n'
        self.incomplete(manifest, 'source_quote_mismatch')

    def test_missing_existing_destination_is_incomplete(self):
        manifest = self.manifest()
        (self.vault/'20-knowledge/Index history.md').unlink()
        self.incomplete(manifest, 'missing_destination')

    def test_stale_destination_hash_is_incomplete(self):
        manifest = self.manifest()
        self.write('20-knowledge/Index history.md', b'# Changed history\n')
        self.incomplete(manifest, 'stale_destination')

    def test_destination_containing_same_id_with_different_row_is_conflict(self):
        self.write('20-knowledge/Index history.md', b'# History\n- T-1 different content\n')
        self.incomplete(self.manifest(), 'destination_item_conflict')

    def test_exact_destination_link_must_remain_in_projected_index(self):
        manifest = self.manifest()
        manifest['items'][0]['source'] = copy.deepcopy(manifest['destination_link'])
        manifest['items'][0]['references'] = []
        self.incomplete(manifest, 'destination_link_removed')

    def test_destination_link_quote_must_point_to_given_existing_file(self):
        self.write('20-knowledge/Other history.md', b'# Other\n')
        manifest = self.manifest()
        manifest['destination'] = {'path': '20-knowledge/Other history.md', 'expected_sha256': self.sha('20-knowledge/Other history.md')}
        self.incomplete(manifest, 'destination_not_linked')

    def test_duplicate_ids_are_rejected(self):
        manifest = self.manifest()
        manifest['items'].append(copy.deepcopy(manifest['items'][0]))
        self.incomplete(manifest, 'duplicate_item_id')

    def test_overlapping_row_ranges_are_rejected_before_any_projection(self):
        manifest = self.manifest()
        other = copy.deepcopy(manifest['items'][0]);other['id'] = 'T-99'
        manifest['items'].append(other)
        self.incomplete(manifest, 'overlapping_rows')

    def test_missing_explicit_row_reference_blocks_reachability(self):
        manifest = self.manifest()
        (self.vault/'20-knowledge/Alpha.md').unlink()
        self.incomplete(manifest, 'missing_reference')

    def test_unmapped_wikilink_is_not_guessed_or_searched(self):
        manifest = self.manifest()
        manifest['items'][0]['references'] = []
        self.incomplete(manifest, 'unresolved_row_link')

    def test_reference_hash_is_current_bound(self):
        manifest = self.manifest()
        self.write('20-knowledge/Alpha.md', b'# Alpha changed\n')
        self.incomplete(manifest, 'stale_reference')

    def test_same_source_destination_cannot_form_a_plan(self):
        manifest = self.manifest()
        manifest['destination'] = copy.deepcopy(manifest['source'])
        self.incomplete(manifest, 'same_source_destination')

    def test_deny_and_secret_paths_rejected_before_body_read(self):
        module = self.module()
        actual = module.stable_read
        for path in ['20-knowledge/_archive/private.md', '90-assets/private.md', '.env', '00-meta/secrets.json']:
            with self.subTest(path=path):
                secret = self.write(path, b'NEVER_OUTPUT_OR_READ_SECRET_BODY')
                manifest = self.manifest()
                manifest['source']['path'] = path
                def guard(resolver, *args, **kwargs):
                    resolved = resolver()
                    if resolved == secret:
                        self.fail('Denied/secret body was handed to the shared reader')
                    return actual(resolver, *args, **kwargs)
                with mock.patch.object(module, 'stable_read', side_effect=guard):
                    result = self.incomplete(manifest, 'unsafe_path')
                self.assertNotIn('NEVER_OUTPUT_OR_READ_SECRET_BODY', json.dumps(result))

    def assert_secret_zero_open(self, manifest, secret):
        module = self.module();actual = module.stable_read;opened = []
        def observe(resolver, *args, **kwargs):
            resolved = resolver()
            if resolved == secret:
                opened.append(resolved)
            return actual(resolver, *args, **kwargs)
        with mock.patch.object(module, 'stable_read', side_effect=observe):
            result = self.preview(manifest)
        self.assertEqual(opened, [], 'Known credential file reached the body reader')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['issues'], [{'reason': 'unsafe_path'}])
        self.assertNotIn('FAKE_SENSITIVE_SENTINEL', json.dumps(result))

    def credential_paths(self):
        return ['20-knowledge/'+name for name in
                ['credentials.md', 'token.md', 'secrets.md', 'auth-config.md',
                 'service-account.md', 'API-KEY.md', 'token-config.md',
                 '.ssh/keys.md', '.aws/config.md', 'private.key']]

    def test_known_credential_source_stems_segments_and_suffixes_never_open(self):
        for path in self.credential_paths():
            with self.subTest(path=path):
                secret = self.write(path, self.index+b'FAKE_SENSITIVE_SENTINEL\n')
                manifest = self.manifest()
                manifest['source'] = {'path': path, 'expected_sha256': self.sha(path)}
                self.assert_secret_zero_open(manifest, secret)

    def test_known_credential_destination_stems_segments_and_suffixes_never_open(self):
        for path in self.credential_paths():
            with self.subTest(path=path):
                target = Path(path).stem
                index = self.index.replace(b'[[Index history]]', ('[['+target+']]').encode())
                self.write('00-meta/index.md', index)
                secret = self.write(path, b'# History\nFAKE_SENSITIVE_SENTINEL\n')
                manifest = self.manifest()
                manifest['destination'] = {'path': path, 'expected_sha256': self.sha(path)}
                manifest['destination_link']['quote'] = '- History: [['+target+']]\r\n'
                self.assert_secret_zero_open(manifest, secret)

    def test_known_credential_reference_stems_segments_and_suffixes_never_open(self):
        for path in self.credential_paths():
            with self.subTest(path=path):
                target = Path(path).stem
                index = self.index.replace(b'[[Alpha]]', ('[['+target+']]').encode())
                self.write('00-meta/index.md', index)
                secret = self.write(path, b'# Reference\nFAKE_SENSITIVE_SENTINEL\n')
                manifest = self.manifest()
                manifest['items'][0]['source']['quote'] = '- [x] T-1 [['+target+']]  \r\n'
                manifest['items'][0]['references'] = [{'path': path, 'expected_sha256': self.sha(path)}]
                self.assert_secret_zero_open(manifest, secret)

    def test_known_credential_manifest_paths_never_open_in_cli(self):
        module = self.module();actual = module.stable_read
        for path in ['00-meta/auth-config.json', '00-meta/service-account.json',
                     '00-meta/API-KEY.json', '00-meta/token-config.json', '.aws/fold.json']:
            with self.subTest(path=path):
                secret = self.write(path, json.dumps(self.manifest()).encode());opened = []
                def observe(resolver, *args, **kwargs):
                    resolved = resolver()
                    if resolved == secret:
                        opened.append(resolved)
                    return actual(resolver, *args, **kwargs)
                with mock.patch.object(module, 'stable_read', side_effect=observe), mock.patch('builtins.print') as output:
                    code = module.main(['--vault', str(self.vault), '--input', path])
                self.assertEqual(opened, [], 'Known credential manifest reached the body reader')
                self.assertEqual(code, 1)
                report = json.loads(output.call_args.args[0])
                self.assertEqual(report['issues'], [{'reason': 'unsafe_path'}])
                self.assertIsNone(report['projected_index'])

    def test_hardlinked_reference_is_rejected(self):
        alpha = self.vault/'20-knowledge/Alpha.md'
        try:os.link(alpha, self.vault/'20-knowledge/alias.md')
        except OSError as error:self.skipTest(str(error))
        self.incomplete(self.manifest(), 'unsafe_file')

    def test_symlinked_reference_is_rejected(self):
        manifest = self.manifest()
        alpha = self.vault/'20-knowledge/Alpha.md'
        alpha.unlink()
        try:alpha.symlink_to(self.vault/'20-knowledge/Beta.md')
        except OSError as error:self.skipTest(str(error))
        self.incomplete(manifest, 'unsafe_path')

    def test_lf_only_row_count_preserves_literal_unicode_separator(self):
        index = self.index.replace(b'[[Alpha]]', '[[Alpha]] 🌱\u2028literal'.encode())
        self.write('00-meta/index.md', index)
        manifest = self.manifest()
        manifest['items'][0]['source']['quote'] = '- [x] T-1 [[Alpha]] 🌱\u2028literal  \r\n'
        result = self.preview(manifest)
        self.assertTrue(result['complete'])
        self.assertEqual(result['moved'][0]['quote'], '- [x] T-1 [[Alpha]] 🌱\u2028literal  \r\n')

    def test_manifest_size_limit_applies_to_host_supplied_objects(self):
        manifest = self.manifest()
        manifest['items'][0]['source']['quote'] = 'x' * (300 * 1024)
        self.incomplete(manifest, 'manifest_too_large')

    def test_document_read_limit_is_enforced(self):
        module = self.module()
        with mock.patch.object(module, 'MAX_FILE_BYTES', 64):
            self.incomplete(self.manifest(), 'input_too_large')

    def test_changed_source_after_capture_invalidates_entire_preview(self):
        module = self.module();actual = module.stable_read;changed = False
        def mutate_between_files(resolver, *args, **kwargs):
            nonlocal changed
            path = resolver()
            if path == self.vault/'20-knowledge/Index history.md' and not changed:
                self.write('00-meta/index.md', self.index+b'Concurrent row\n');changed = True
            return actual(resolver, *args, **kwargs)
        with mock.patch.object(module, 'stable_read', side_effect=mutate_between_files):
            self.incomplete(self.manifest(), 'file_changed_during_preview')

    def test_cli_is_read_only_and_accepts_only_explicit_manifest(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'preview')
        self.assertFalse(report['apply_supported'])

    def test_cli_rejects_duplicate_json_keys_without_echoing_input(self):
        result = self.cli(raw=b'{"version":1,"version":2,"secret":"NEVER_ECHO"}')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b'')
        self.assertNotIn(b'NEVER_ECHO', result.stdout)

    def test_cli_has_no_apply_option(self):
        result = self.cli('--apply')
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
