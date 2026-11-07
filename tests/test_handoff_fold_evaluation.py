from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_handoff_fold.py'
FIXTURE = ROOT / 'tests/fixtures/handoff_fold/pack.json'
KINDS = ('recall', 'artifact', 'continuation', 'decision', 'constraint')


def load():
    specification = importlib.util.spec_from_file_location('evaluate_handoff_fold', SCRIPT)
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.temp = tempfile.TemporaryDirectory(prefix='av-fold-eval-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def pack(self, **changes):
        value = json.loads(FIXTURE.read_text(encoding='utf-8'))
        value.update(changes)
        path = self.root / 'pack.json'
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), *args], capture_output=True,
                              text=True, encoding='utf-8')

    def test_good_fold_reaches_every_probe(self):
        report = self.module.evaluate()
        self.assertTrue(report['passed'])
        self.assertEqual(report['synthetic'], True)
        self.assertEqual(report['lost_probes'], [])
        self.assertEqual(set(report['probes']), set(KINDS))
        for kind in KINDS:
            row = report['probes'][kind]
            self.assertGreater(row['total'], 0)
            self.assertEqual(row['total'], row['injected'] + row['handoff'] + row['one_hop'] + row['lost'])
            self.assertEqual(row['lost'], 0)

    def test_tiers_tell_the_routes_apart(self):
        tiers = {row['id']: row['tier'] for row in self.module.evaluate()['probe_rows']}
        self.assertEqual(tiers['artifact-replay'], 'injected')
        self.assertEqual(tiers['constraint-open-nested'], 'handoff')
        self.assertEqual(tiers['constraint-mirror'], 'one_hop')

    def test_sections_are_checked_one_by_one(self):
        sections = self.module.evaluate()['sections']
        self.assertEqual(sections['folded'], 4)
        self.assertEqual(sections['preserved'], 4)
        self.assertEqual(sections['with_loss'], 0)
        self.assertEqual(sections['skipped'], 0)

    def test_truncation_control_fails(self):
        report = self.module.evaluate(control='truncate')
        self.assertFalse(report['passed'])
        self.assertIn('constraint-mirror', report['lost_probes'])
        self.assertGreater(report['sections']['with_loss'], 0)

    def test_broken_link_control_fails_only_past_the_handoff(self):
        report = self.module.evaluate(control='unlinked')
        self.assertFalse(report['passed'])
        self.assertIn('constraint-window', report['lost_probes'])
        self.assertNotIn('constraint-open-nested', report['lost_probes'])
        self.assertEqual(report['sections']['preserved'], 0)

    def test_report_holds_identifiers_and_counts_only(self):
        text = json.dumps(self.module.evaluate(control='truncate'), ensure_ascii=False)
        self.assertNotIn('미러 저장소', text)

    def test_limits_are_stated(self):
        limitations = self.module.evaluate()['limitations']
        self.assertIn('literal_reachability_is_a_loss_alarm_not_a_functional_probe', limitations)
        self.assertIn('no_model_scored_probes', limitations)

    def test_a_probe_absent_before_the_fold_is_invalid(self):
        path = self.pack(probes=[{'id': 'x', 'kind': 'recall', 'expected': 'not in the text'}])
        with self.assertRaises(ValueError):
            self.module.evaluate(path)

    def test_a_fold_with_nothing_to_do_is_invalid_for_a_pack(self):
        with self.assertRaises(ValueError):
            self.module.evaluate(self.pack(settings={'threshold_bytes': 99999999, 'protect_sections': 2,
                                                     'window_bytes': 10}))

    def test_cli_exit_codes(self):
        passed = self.run_cli()
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertTrue(json.loads(passed.stdout)['passed'])
        self.assertEqual(self.run_cli('--control', 'truncate').returncode, 1)
        self.assertEqual(self.run_cli('--control', 'unlinked').returncode, 1)

    def test_invalid_input_exits_two_with_a_fixed_code(self):
        bad = self.pack(schema_version=2)
        refused = self.run_cli('--fixture', str(bad))
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stderr), {'error': 'invalid_input'})
        self.assertNotIn(str(self.root), refused.stderr)
        self.assertEqual(self.run_cli('--fixture', str(self.root / 'absent.json')).returncode, 2)
        self.assertEqual(self.run_cli('--control', 'other').returncode, 2)


class VaultModeTests(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.fold = sys.modules['vault_handoff_fold']
        self.temp = tempfile.TemporaryDirectory(prefix='av-fold-eval-')
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.pack = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.handoff = self.vault / '50-projects/Lantern/Lantern handoff.md'
        self.handoff.parent.mkdir(parents=True)
        self.raw = '\n'.join(self.pack['handoff']).encode('utf-8')
        self.handoff.write_bytes(self.raw)
        (self.vault / '00-meta').mkdir()
        (self.vault / '00-meta/vault-config.json').write_text(
            json.dumps({'handoff_note': '50-projects/Lantern/Lantern handoff.md'}), encoding='utf-8')
        local = {key: self.pack[key] for key in ('schema_version', 'settings', 'probes')}
        (self.vault / '00-meta/probes.json').write_text(json.dumps({**local, 'synthetic': False},
                                                                   ensure_ascii=False), encoding='utf-8')

    def tree(self):
        return {path.relative_to(self.vault).as_posix(): path.read_bytes()
                for path in sorted(self.vault.rglob('*')) if path.is_file()}

    def test_preview_of_a_real_vault_reads_only(self):
        before = self.tree()
        report = self.module.evaluate_vault(self.vault, '00-meta/probes.json')
        self.assertEqual(self.tree(), before)
        self.assertTrue(report['passed'])
        self.assertEqual(report['synthetic'], False)
        self.assertEqual(report['sections']['folded'], 4)

    def test_after_a_fold_the_existing_archive_is_followed(self):
        settings = self.pack['settings']
        import hashlib
        self.fold.apply(self.vault, expected_sha256=hashlib.sha256(self.raw).hexdigest(),
                        threshold_bytes=settings['threshold_bytes'], protect_sections=settings['protect_sections'],
                        folded_at='2031-04-01T00:00:00Z')
        report = self.module.evaluate_vault(self.vault, '00-meta/probes.json')
        self.assertTrue(report['passed'])
        self.assertEqual(report['sections']['folded'], 0)
        tiers = {row['id']: row['tier'] for row in report['probe_rows']}
        self.assertEqual(tiers['constraint-mirror'], 'one_hop')

    def test_cli_vault_mode(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--vault', str(self.vault),
                                 '--probes', '00-meta/probes.json'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_control_is_refused_in_vault_mode(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--vault', str(self.vault),
                                 '--probes', '00-meta/probes.json', '--control', 'truncate'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)

    def test_denied_probe_path_is_refused(self):
        (self.vault / '90-assets').mkdir()
        (self.vault / '90-assets/probes.json').write_text('{}', encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--vault', str(self.vault),
                                 '--probes', '90-assets/probes.json'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {'error': 'invalid_input'})


if __name__ == '__main__':
    unittest.main()
