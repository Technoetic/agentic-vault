import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'))

class ExportTests(unittest.TestCase):
    def test_selected_notes_project_links_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault = root / 'vault'
            (vault / '00-meta').mkdir(parents=True)
            (vault / '00-meta/vault-config.json').write_text('{"required_keys":["title"],"enums":{}}', encoding='utf-8')
            (vault / 'a.md').write_text('---\ntitle: A\nstatus: active\n---\n[[b|Bee]] `[[b]]`\n', encoding='utf-8')
            (vault / 'b.md').write_text('# Bee\n', encoding='utf-8')
            module = importlib.import_module('vault_okf')
            manifest = module.export_bundle(vault, ['a.md', 'b.md'], root / 'bundle')
            self.assertEqual(manifest['spec_commit'], 'ad30107c31c06aec8a7d5636e0d1058118604e6f')
            self.assertIn('okf_version: "0.2"', (root / 'bundle/index.md').read_text(encoding='utf-8'))
            result = (root / 'bundle/concepts/a.md').read_text(encoding='utf-8')
            self.assertIn('type: "concept"', result)
            self.assertIn('[Bee](b.md)', result)
            self.assertIn('`[[b]]`', result)
            self.assertIn('status: active', result)

if __name__ == '__main__':
    unittest.main()

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.vault = self.root / 'vault'
        (self.vault / '00-meta').mkdir(parents=True)
        (self.vault / '00-meta/vault-config.json').write_text('{"required_keys":["title"],"enums":{},"deny_zones":["secret"]}', encoding='utf-8')
        self.module = importlib.import_module('vault_okf')
    def write(self, name, body):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
    def test_rejects_collisions_reserved_denied_and_existing_destination(self):
        self.write('a.md', '# A')
        self.write('A.md', '# A')
        self.write('index.md', '# index')
        self.write('secret/a.md', '# secret')
        for notes in (['a.md', 'A.md'], ['index.md'], ['secret/a.md'], ['../outside.md']):
            with self.subTest(notes=notes), self.assertRaises(self.module.ExportError):
                self.module.export_bundle(self.vault, notes, self.root / 'out')
            self.assertFalse((self.root / 'out').exists())
        (self.root / 'out').mkdir()
        with self.assertRaises(self.module.ExportError):
            self.module.export_bundle(self.vault, ['a.md'], self.root / 'out')
    def test_losses_and_code_and_determinism(self):
        self.write('a.md', '[[missing]] ![[missing]]\n```\n[[missing]]\n```\n')
        first = self.module.export_bundle(self.vault, ['a.md'], self.root / 'one')
        second = self.module.export_bundle(self.vault, ['a.md'], self.root / 'two')
        self.assertEqual(first, second)
        self.assertEqual([x['kind'] for x in first['losses']], ['missing_frontmatter', 'unresolved_or_ambiguous_wikilink', 'embed'])
        self.assertIn('```\n[[missing]]\n```', (self.root / 'one/concepts/a.md').read_text(encoding='utf-8'))
    def test_source_drift_does_not_publish(self):
        from unittest import mock
        self.write('a.md', '# A')
        original = self.module.Store.fresh_policy
        def change(store):
            original(store)
            (self.vault / 'a.md').write_text('# Changed', encoding='utf-8')
        with mock.patch.object(self.module.Store, 'fresh_policy', change):
            with self.assertRaises(self.module.ExportError):
                self.module.export_bundle(self.vault, ['a.md'], self.root / 'out')
        self.assertFalse((self.root / 'out').exists())
    def test_manifest_hashes_actual_output_and_cli_refuses_existing(self):
        import hashlib
        import subprocess
        self.write('a.md', '# A')
        result = self.module.export_bundle(self.vault, ['a.md'], self.root / 'out')
        self.assertEqual(json.loads((self.root / 'out/manifest.json').read_text(encoding='utf-8')), result)
        self.assertEqual(result['path_mapping'], {'a.md': 'concepts/a.md'})
        for item in result['outputs']:
            self.assertEqual(hashlib.sha256((self.root / 'out' / item['path']).read_bytes()).hexdigest(), item['sha256'])
        proc = subprocess.run([sys.executable, str(Path(self.module.__file__)), '--vault', str(self.vault), '--note', 'a.md', '--output', str(self.root / 'out')], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(json.loads(proc.stdout)['ok'])
    def test_symlink_parent_is_refused(self):
        self.write('a.md', '# A')
        link = self.root / 'alias'
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest('directory symlinks unavailable')
        with self.assertRaises(self.module.ExportError):
            self.module.export_bundle(self.vault, ['a.md'], link / 'out')
        self.assertFalse((self.root / 'out').exists())
    def test_failure_removes_staging(self):
        from unittest import mock
        self.write('a.md', '# A')
        with mock.patch.object(self.module, '_publish', side_effect=OSError('fail')):
            with self.assertRaises(self.module.ExportError):
                self.module.export_bundle(self.vault, ['a.md'], self.root / 'out')
        self.assertFalse((self.root / 'out').exists())
        self.assertEqual(list(self.root.glob('.okf-*')), [])
    def test_excluded_directory_is_not_read(self):
        from unittest import mock
        config = self.vault / '00-meta/vault-config.json'
        config.write_text('{"required_keys":["title"],"enums":{},"exclude_dirs":["excluded"]}', encoding='utf-8')
        self.write('excluded/x.md', '# secret')
        original = self.module.Store.fingerprint
        with mock.patch.object(self.module.Store, 'fingerprint', autospec=True, side_effect=original) as read:
            with self.assertRaises(self.module.ExportError):
                self.module.export_bundle(self.vault, ['excluded/x.md'], self.root / 'out')
            read.assert_not_called()
    def test_projected_links_have_no_false_loss_and_code_is_literal(self):
        self.write('a.md', '[[b]]\n``outer ` [[b]] ` outer``\n```text\n```not-a-closing-fence\n[[b]]\n```\n')
        self.write('b.md', '# B')
        manifest = self.module.export_bundle(self.vault, ['a.md', 'b.md'], self.root / 'out')
        body = (self.root / 'out/concepts/a.md').read_text(encoding='utf-8')
        self.assertIn('[b](b.md)', body)
        self.assertIn('``outer ` [[b]] ` outer``', body)
        self.assertIn('```not-a-closing-fence\n[[b]]', body)
        self.assertFalse(any(x['kind'] == 'unconverted_markdown_link' for x in manifest['losses']))
    def test_case_colliding_directory_components_are_rejected(self):
        self.write('A/x.md', '# x')
        self.write('a/y.md', '# y')
        with self.assertRaises(self.module.ExportError):
            self.module.export_bundle(self.vault, ['A/x.md', 'a/y.md'], self.root / 'out')
    def test_macos_publish_uses_rename_exclusive(self):
        from unittest import mock
        fake = mock.Mock(return_value=0)
        library = mock.Mock(renamex_np=fake)
        with mock.patch.object(self.module.sys, 'platform', 'darwin'), mock.patch.object(self.module.os, 'name', 'posix'), mock.patch.object(self.module.ctypes, 'CDLL', return_value=library):
            self.module._publish(Path('/tmp/source'), Path('/tmp/dest'))
        self.assertEqual(fake.call_args.args[-1], 4)
