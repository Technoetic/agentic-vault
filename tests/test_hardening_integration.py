"""Installed-bundle and host manifest paths exercised against synthetic vaults."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))


class HardeningLintIntegrationTests(unittest.TestCase):
    def test_complete_standalone_bundle_keeps_schema_fatal_and_reports_new_index_warning(self):
        with tempfile.TemporaryDirectory(prefix='vault installed bundle ') as temporary:
            vault = Path(temporary)
            bundle = vault / '00-meta/scripts'
            bundle.mkdir(parents=True)
            for source in SCRIPTS.glob('vault_*.py'):
                shutil.copyfile(source, bundle / source.name)
            for name in ('jev_client.py', 'jev_ask.py'):
                shutil.copyfile(SCRIPTS / name, bundle / name)
            shutil.copytree(SCRIPTS / 'resources', bundle / 'resources')
            (vault / '00-meta/vault-config.json').write_text('{}', encoding='utf-8')
            notes = vault / '20-knowledge'
            notes.mkdir()
            (notes / 'bad.md').write_text('No required frontmatter.', encoding='utf-8')
            (notes / 'key.md').write_text('---\ntitle: "Key"\ntype: reference\nstatus: active\nai_priority: high\ntags: [test]\ncreated: 2026-10-07\nupdated: 2026-10-07\n---\nghp_\u115f' + 'x' * 36, encoding='utf-8')
            subprocess.run(['git', 'init', '-q'], cwd=vault, check=True)
            subprocess.run(['git', 'add', '--', '20-knowledge', '00-meta/vault-config.json'], cwd=vault, check=True)
            result = subprocess.run([sys.executable, '-X', 'utf8', str(bundle / 'vault_healthcheck.py'),
                                     '--vault', str(vault), '--staged'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode, 1)
            text = (result.stdout + result.stderr).decode('utf-8')
            self.assertIn('egress-secret', text)
            self.assertIn('bad.md', text)
            self.assertNotIn('extensions unavailable', text)
            self.assertNotIn('x' * 36, text)

    def test_pure_warning_composition_covers_new_features_without_fatal_defaults(self):
        from vault_healthcheck import extension_warnings
        notes = {'20-knowledge/key.md': 'ghp_' + '\u3164' + 'x' * 36,
                 '20-knowledge/facts.md': '| subject | relation | value | valid_from | valid_until | recorded | source | superseded_by | status |\n|---|---|---|---|---|---|---|---|---|\n| A | power | 12 | 2026-01-01 | | 2026-10-07 | | | confirmed |\n',
                 '00-meta/hot.md': '---\nprovenance_manifest: "00-meta/provenance/selected.json"\nverified_by: "claimed reviewer"\n---\nHost summary.'}
        rows = extension_warnings(notes, {'hot_note': '00-meta/hot.md'})
        codes = {r['code'] for r in rows}
        self.assertTrue({'egress-secret', 'ssot-confirmed-source-missing', 'provenance-unresolved'} <= codes)
        self.assertTrue(all(r['severity'] == 'warning' for r in rows))
        self.assertNotIn('x' * 36, json.dumps(rows))

    def test_staged_scan_uses_sensitive_index_original_and_ignores_benign_dirty_rewrite(self):
        from vault_healthcheck import validate_config, validate_staged
        with tempfile.TemporaryDirectory(prefix='vault staged ') as temporary:
            vault = Path(temporary)
            subprocess.run(['git', 'init', '-q'], cwd=vault, check=True)
            note = vault / '20-knowledge/key.md'
            note.parent.mkdir()
            header = '---\ntitle: "Key"\ntype: reference\nstatus: active\nai_priority: high\ntags: [test]\ncreated: 2026-10-07\nupdated: 2026-10-07\n---\n'
            note.write_text(header + 'ghp_' + '\u115f' + 'x' * 36, encoding='utf-8')
            subprocess.run(['git', 'add', '--', '20-knowledge/key.md'], cwd=vault, check=True)
            note.write_text(header + 'Benign working copy.', encoding='utf-8')
            warnings = []
            errors = validate_staged(vault, validate_config({}), warning_diagnostics=warnings)
            self.assertEqual(errors, [])
            self.assertIn('egress-secret', {row['code'] for row in warnings})

    def test_unresolved_verification_cannot_be_promoted_with_any_certificate(self):
        from vault_warning_policy import build_promotion_certificate, validate_promotion
        certificate = build_promotion_certificate()
        result = validate_promotion({'levels': {'provenance-unresolved': 'fatal'},
                                    'certificate': certificate},
                                   certificate['checker_sha256'], certificate['corpus_sha256'])
        self.assertFalse(result['valid'])


class HardeningHookIntegrationTests(unittest.TestCase):
    def test_checkpoint_manifest_executes_both_native_events_silently(self):
        shell = shutil.which('sh')
        if not shell:
            candidate = Path('C:/Program Files/Git/bin/sh.exe')
            if candidate.is_file():
                shell = str(candidate)
        if not shell:
            self.skipTest('POSIX hook shell unavailable')
        manifest = json.loads((ROOT / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
        for event, matcher in [('PreCompact', 'manual|auto'), ('SessionEnd', '.*')]:
            with self.subTest(event=event), tempfile.TemporaryDirectory(prefix='vault integration ') as temporary:
                vault = Path(temporary)
                (vault / '00-meta').mkdir()
                (vault / '00-meta/vault-config.json').write_text(json.dumps({'deny_zones': ['private']}), encoding='utf-8')
                note = vault / '00-meta/hot.md'
                note.write_bytes(b'Host-owned narrative.\r\n')
                before = hashlib.sha256(note.read_bytes()).hexdigest()
                groups = manifest[event]
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0]['matcher'], matcher)
                hooks = groups[0]['hooks']
                self.assertEqual(len(hooks), 1)
                hook = hooks[0]
                self.assertNotIn('async', hook)
                self.assertLessEqual(hook['timeout'], 3)
                environment = os.environ.copy()
                environment['CLAUDE_PLUGIN_ROOT'] = ROOT.as_posix()
                environment['CLAUDE_PROJECT_DIR'] = vault.as_posix()
                payload = {'hook_event_name': event, 'cwd': str(vault), 'session_id': 'synthetic-session'}
                if event == 'PreCompact':
                    payload['trigger'] = 'auto'
                else:
                    payload['reason'] = 'other'
                run = subprocess.run([shell, '-c', hook['command']], cwd=vault,
                    input=json.dumps(payload).encode(), env=environment, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=3)
                self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b'', b''))
                self.assertEqual(hashlib.sha256(note.read_bytes()).hexdigest(), before)
                self.assertTrue(list((vault / '00-meta/.agentic-vault/runtime').glob('checkpoint-*.json')))


if __name__ == '__main__':
    unittest.main()
