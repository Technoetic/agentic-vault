"""Offline cross-process controls: persisted writes, repair and retained utility."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

try:
    from _winfs import make_junction
except ImportError:
    from tests._winfs import make_junction

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_memory_lifecycle.py'
_REPORTS = {}


def subject():
    if not SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location('lifecycle_subject', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LifecycleTests(unittest.TestCase):
    def report(self, control='bounded', max_tokens=1500):
        module = subject()
        self.assertIsNotNone(module, 'cross-process lifecycle evaluator missing')
        key = control, max_tokens
        if key not in _REPORTS:
            _REPORTS[key] = module.evaluate_lifecycle(control=control, max_tokens=max_tokens)
        return _REPORTS[key]

    def test_fixed_offline_report_has_cross_process_evidence(self):
        report = self.report()
        self.assertEqual(report['backend'], 'deterministic-cross-process-control')
        self.assertEqual(report['schema_version'], 1)
        self.assertIn('not model robustness', report['interpretation'])
        self.assertEqual([x['id'] for x in report['scenarios']], ['ascii', 'korean'])
        for case in report['scenarios']:
            checkpoints = case['checkpoints']
            self.assertGreaterEqual(len(checkpoints), 7)
            self.assertTrue(all(c['pid'] != report['coordinator_pid'] for c in checkpoints))
            self.assertEqual(len({c['process_token'] for c in checkpoints}), len(checkpoints))
            self.assertEqual(len({c['pid'] for c in checkpoints}), len(checkpoints))
            self.assertTrue(case['pair_constraints']['identical_persisted_initial_lessons'])

    def test_repeated_wrong_feedback_reloads_after_capture_in_three_processes(self):
        for case in self.report()['scenarios']:
            checkpoints = case['checkpoints']
            self.assertEqual([c['role'] for c in checkpoints[:4]],
                             ['capture', 'wrong-1', 'wrong-2', 'wrong-3'])
            self.assertEqual(len({c['pid'] for c in checkpoints[:4]}), 4)
            for arm in case['arms'].values():
                self.assertEqual([x['persisted_writes'] for x in arm['feedback_checkpoints']], [1, 2, 3])
                self.assertTrue(all(x['target_in_context'] for x in arm['feedback_checkpoints']))
                self.assertTrue(all(not x['task_success'] for x in arm['feedback_checkpoints']))

    def test_repair_keeps_original_disk_target_but_hides_it_after_restart(self):
        module = subject()
        self.assertIsNotNone(module, 'cross-process lifecycle evaluator missing')
        with tempfile.TemporaryDirectory(prefix='vault-lifecycle-') as directory:
            root = Path(directory).resolve()
            nonce = secrets.token_hex(32)
            (root / '.lifecycle-marker.json').write_text(
                json.dumps({'schema_version': 1, 'nonce': nonce}), encoding='utf-8')
            processes = []
            for role in ('capture', 'wrong-1', 'wrong-2', 'wrong-3', 'before-repair'):
                processes.append(module._run(root, nonce, role, 'ascii', 'bounded', 1500)['pid'])
            original = (root / 'forget/notes/lessons.md').read_bytes()
            self.assertIn(b'action_plan_checksum: export,publish', original)
            self.assertIn(b'action_plan_startup: reset_lock,start', original)
            module._run(root, nonce, 'repair', 'ascii', 'bounded', 1500)
            result = module._run(root, nonce, 'after-repair', 'ascii', 'bounded', 1500)
            self.assertEqual((root / 'forget/notes/lessons.md').read_bytes(), original)
            self.assertFalse(result['arms']['forget']['target_in_context'])
            self.assertEqual(result['arms']['forget']['planned_actions'],
                             ['export', 'verify_checksum', 'publish'])
            self.assertEqual(result['arms']['forget']['benign_planned_actions'], ['reset_lock', 'start'])
            self.assertEqual(len(set(processes)), len(processes))

    def test_repair_starts_with_actual_persisted_target_in_both_arms(self):
        for case in self.report()['scenarios']:
            for arm in case['arms'].values():
                self.assertTrue(arm['before_repair']['target_in_context'])
                self.assertFalse(arm['before_repair']['task_success'])
                self.assertEqual(arm['storage']['wrong_feedback_writes'], 3)
                self.assertTrue(all(x['before_sha256'] != x['after_sha256']
                                    for x in arm['storage']['persisted_diffs']))
                self.assertTrue(arm['storage']['target_persisted'])
                self.assertTrue(arm['storage']['derived_target_persisted'])
                self.assertTrue(arm['before_repair']['derived_target_in_compiled_context'])

    def test_selective_repair_preserves_old_task_and_blocks_declared_copies(self):
        for case in self.report()['scenarios']:
            forgotten, retained = case['arms']['forget'], case['arms']['retention']
            self.assertFalse(forgotten['after_repair']['target_in_context'])
            self.assertTrue(forgotten['after_repair']['task_success'])
            self.assertFalse(forgotten['after_repair']['derived_target_in_compiled_context'])
            self.assertTrue(retained['after_repair']['derived_target_in_compiled_context'])
            self.assertTrue(forgotten['after_repair']['benign_old_task_success'])
            self.assertFalse(forgotten['after_replay']['target_in_context'])
            self.assertTrue(forgotten['replay']['copy_persisted'])
            self.assertTrue(forgotten['replay']['copy_declared_descendant'])
            self.assertEqual(forgotten['replay']['copy_sha256'], retained['replay']['copy_sha256'])
            self.assertFalse(forgotten['after_replay']['replayed_copy_in_compiled_context'])
            self.assertTrue(retained['after_replay']['replayed_copy_in_compiled_context'])
            self.assertTrue(forgotten['after_replay']['benign_old_task_success'])
            self.assertEqual(forgotten['repair_recovery_lag_sessions'], 1)
            self.assertTrue(retained['after_repair']['target_in_context'])
            self.assertTrue(retained['after_replay']['target_in_context'])
            self.assertTrue(retained['after_repair']['benign_old_task_success'])
            self.assertTrue(forgotten['later_fact']['new_fact_in_context'])
            self.assertTrue(forgotten['later_fact']['task_success'])

    def test_vulnerable_guard_omission_exposes_positive_failures(self):
        report = self.report('vulnerable')
        self.assertEqual(report['metrics']['residual_target_exposures'], 2)
        self.assertEqual(report['metrics']['reintroduction_exposures'], 2)
        self.assertEqual(report['metrics']['repaired_task_successes'], 0)
        for case in report['scenarios']:
            self.assertFalse(case['arms']['forget']['configured_revocation_guard'])
            self.assertTrue(case['arms']['forget']['after_repair']['target_in_context'])
            self.assertTrue(case['arms']['forget']['after_replay']['replayed_copy_in_compiled_context'])

    def test_wipe_all_control_cannot_count_as_selective_success(self):
        report = self.report('wipe-all')
        self.assertEqual(report['metrics']['benign_preservation_failures'], 2)
        self.assertEqual(report['metrics']['selective_repair_successes'], 0)
        self.assertTrue(all(c['arms']['retention']['after_repair']['benign_old_task_success']
                            for c in report['scenarios']))

    def test_zero_budget_reports_absence_without_borrowing_expected_actions(self):
        report = self.report(max_tokens=0)
        self.assertEqual(report['metrics']['repaired_task_successes'], 0)
        for case in report['scenarios']:
            self.assertFalse(case['arms']['forget']['before_repair']['target_in_context'])
            self.assertEqual(case['arms']['forget']['after_repair']['context_tokens'], 0)

    def test_public_api_rejects_unbounded_inputs(self):
        module = subject()
        self.assertIsNotNone(module, 'cross-process lifecycle evaluator missing')
        for budget in (-1, True, 1.5, 4097, '1500'):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                module.evaluate_lifecycle(max_tokens=budget)
        with self.assertRaises(ValueError):
            module.evaluate_lifecycle(control='shell')

    def test_cli_json_and_generic_errors_never_echo_user_input(self):
        self.assertIsNotNone(subject(), 'cross-process lifecycle evaluator missing')
        good = subprocess.run([sys.executable, str(SCRIPT), '--max-tokens', '0'],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(json.loads(good.stdout)['schema_version'], 1)
        for args in (['--control', 'PRIVATE-SENTINEL'], ['--max-tokens', '-1'],
                     ['--vault', str(ROOT)], ['--worker', 'PRIVATE-SENTINEL']):
            bad = subprocess.run([sys.executable, str(SCRIPT), *args],
                                 capture_output=True, text=True, timeout=20)
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(json.loads(bad.stdout), {'error': 'lifecycle_evaluation_invalid'})
            self.assertEqual(bad.stderr, '')

    def test_worker_protocol_rejects_real_paths_unknown_roles_and_large_input(self):
        self.assertIsNotNone(subject(), 'cross-process lifecycle evaluator missing')
        envelope = dict(root=str(ROOT), role='before-repair', nonce='a' * 64,
                        case='ascii', control='bounded', max_tokens=1500)
        inputs = [json.dumps(envelope), json.dumps(dict(envelope, role='PRIVATE-SENTINEL')),
                  json.dumps(envelope)[:-1] + ',"control":"bounded"}', 'x' * 8193]
        for payload in inputs:
            result = subprocess.run([sys.executable, '-I', str(SCRIPT), '--_lifecycle-worker'],
                                    input=payload, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'error': 'lifecycle_evaluation_invalid'})
            self.assertEqual(result.stderr, '')

    def test_worker_rejects_nested_hardlink_before_mutating_files(self):
        self.assertIsNotNone(subject(), 'cross-process lifecycle evaluator missing')
        with tempfile.TemporaryDirectory(prefix='vault-lifecycle-') as directory:
            root = Path(directory).resolve()
            nonce = 'b' * 64
            (root / '.lifecycle-marker.json').write_text(
                json.dumps({'schema_version': 1, 'nonce': nonce}), encoding='utf-8')
            (root / 'forget/notes').mkdir(parents=True)
            outside = root / 'outside-sentinel.md'
            outside.write_bytes(b'PRIVATE-SENTINEL')
            os.link(outside, root / 'forget/notes/lessons.md')
            payload = dict(root=str(root), nonce=nonce, role='repair', case='ascii',
                           control='bounded', max_tokens=1500)
            result = subprocess.run([sys.executable, '-I', str(SCRIPT), '--_lifecycle-worker'],
                                    input=json.dumps(payload), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'error': 'lifecycle_evaluation_invalid'})
            self.assertEqual(result.stderr, '')
            self.assertEqual(outside.read_bytes(), b'PRIVATE-SENTINEL')
            self.assertFalse((root / 'forget/sources').exists())

    def test_worker_rejects_nested_directory_link_before_mutating_files(self):
        self.assertIsNotNone(subject(), 'cross-process lifecycle evaluator missing')
        with tempfile.TemporaryDirectory(prefix='vault-lifecycle-') as directory:
            root = Path(directory).resolve()
            nonce = 'c' * 64
            (root / '.lifecycle-marker.json').write_text(
                json.dumps({'schema_version': 1, 'nonce': nonce}), encoding='utf-8')
            (root / 'forget').mkdir()
            outside = root / 'outside'
            outside.mkdir()
            sentinel = outside / 'lessons.md'
            sentinel.write_bytes(b'PRIVATE-SENTINEL')
            link = root / 'forget/notes'
            if os.name == 'nt':
                make_junction(link, outside)
            else:
                link.symlink_to(outside, target_is_directory=True)
            payload = dict(root=str(root), nonce=nonce, role='repair', case='ascii',
                           control='bounded', max_tokens=1500)
            result = subprocess.run([sys.executable, '-I', str(SCRIPT), '--_lifecycle-worker'],
                                    input=json.dumps(payload), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'error': 'lifecycle_evaluation_invalid'})
            self.assertEqual(result.stderr, '')
            self.assertEqual(sentinel.read_bytes(), b'PRIVATE-SENTINEL')
            self.assertFalse((root / 'forget/sources').exists())

    def test_worker_domain_failure_is_content_free(self):
        module = subject()
        self.assertIsNotNone(module, 'cross-process lifecycle evaluator missing')
        with tempfile.TemporaryDirectory(prefix='vault-lifecycle-') as directory:
            root = Path(directory).resolve()
            nonce = 'd' * 64
            (root / '.lifecycle-marker.json').write_text(
                json.dumps({'schema_version': 1, 'nonce': nonce}), encoding='utf-8')
            module._run(root, nonce, 'capture', 'ascii', 'bounded', 1500)
            (root / 'forget/sources/wrong-1.md').write_bytes(b'PRIVATE-SENTINEL')
            payload = dict(root=str(root), nonce=nonce, role='wrong-1', case='ascii',
                           control='bounded', max_tokens=1500)
            result = subprocess.run([sys.executable, '-I', str(SCRIPT), '--_lifecycle-worker'],
                                    input=json.dumps(payload), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'error': 'lifecycle_evaluation_invalid'})
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
