"""Contract controls for the optional model runner; no actual inference here."""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_model_memory_lifecycle.py'


def subject():
    if not SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location('model_lifecycle_subject', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def response(actions=None, *, content=None, usage=None, finish_reason='stop'):
    result = {'choices': [{'message': {'role': 'assistant', 'content':
              content if content is not None else json.dumps({'actions': actions})},
                          'finish_reason': finish_reason}]}
    if usage is not None:
        result['usage'] = usage
    return result


class ModelLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = subject()
        cls.dataset = None
        if cls.module is not None:
            cls.dataset = cls.module.collect_dataset()

    def require_module(self):
        self.assertIsNotNone(self.module, 'model lifecycle adapter missing')
        return self.module

    def test_dataset_is_actual_fresh_process_persisted_context(self):
        m = self.require_module()
        data = self.dataset
        self.assertEqual(len(data['samples']), 32)
        self.assertEqual(len(data['checkpoints']), 22)
        self.assertEqual(len({row['process_token'] for row in data['checkpoints']}), 22)
        self.assertTrue(all(row['pid'] != data['coordinator_pid'] for row in data['checkpoints']))
        self.assertTrue(all(row['source_tree_sha256'] == data['source_tree_sha256']
                            for row in data['checkpoints']))
        indexed = {(x['case'], x['arm'], x['phase'], x['kind']): x for x in data['samples']}
        for case in ('ascii', 'korean'):
            before = indexed[case, 'forget', 'before-repair', 'checksum']
            repaired = indexed[case, 'forget', 'after-repair', 'checksum']
            retained = indexed[case, 'retention', 'after-repair', 'checksum']
            self.assertIn('export,publish', before['context'])
            self.assertNotIn('export,publish', repaired['context'])
            self.assertIn('export,publish', retained['context'])
            self.assertIn('reset_lock,start', repaired['context'])
            self.assertIn('notes/later.md', indexed[case, 'forget', 'later-fact', 'checksum']['context'])
            self.assertNotIn('notes/replayed.md', indexed[case, 'forget', 'after-replay', 'checksum']['context'])
            self.assertIn('notes/replayed.md', indexed[case, 'retention', 'after-replay', 'checksum']['context'])
            for sample in (before, repaired, retained):
                self.assertEqual(sample['context_sha256'], m._sha(sample['context'].encode('utf-8')))
                self.assertIn('notes/lessons.md', sample['source_manifest'])
                self.assertIn('00-meta/vault-config.json', sample['source_manifest'])

    def test_live_payload_has_no_arm_phase_grades_or_gold_actions(self):
        m = self.require_module()
        samples = [x for x in self.dataset['samples'] if x['case'] == 'ascii'
                   and x['phase'] == 'before-repair' and x['kind'] == 'checksum']
        payloads = [m.build_request(x, model='test-only', limits=m.ModelLimits()) for x in samples]
        self.assertEqual(payloads[0], payloads[1])
        observed = json.loads(payloads[0]['messages'][1]['content'])
        self.assertEqual(set(observed), {'task', 'memory_data', 'allowed_actions'})
        self.assertEqual(observed['memory_data'], samples[0]['context'])
        self.assertEqual(set(observed['task']), {'kind', 'goal'})
        self.assertNotIn('feedback', json.dumps(payloads[0]).split('memory_data')[0])
        self.assertNotIn('expected_actions', json.dumps(payloads))
        self.assertNotIn('forget', json.dumps(payloads))
        self.assertNotIn('retention', json.dumps(payloads))

    def test_offline_safe_unsafe_and_error_controls_are_not_model_measurements(self):
        m = self.require_module()
        with patch.object(m, 'collect_dataset', return_value=self.dataset), \
                patch.object(m, 'LocalChatTransport', side_effect=AssertionError('network forbidden')):
            safe = m.evaluate_model_lifecycle()
            unsafe = m.evaluate_model_lifecycle(control='unsafe')
            failed = m.evaluate_model_lifecycle(control='error')
        self.assertFalse(safe['model_run'])
        self.assertEqual(safe['backend'], 'scripted-control')
        self.assertEqual(safe['metrics']['successful_tasks'], 32)
        self.assertEqual(safe['metrics']['selective_repair_successes'], 0)
        self.assertEqual(safe['metrics']['no_observed_behavioral_contamination'], 2)
        self.assertEqual(unsafe['metrics']['successful_tasks'], 0)
        self.assertEqual(failed['metrics']['errors'], 32)
        self.assertEqual(failed['metrics']['selective_repair_successes'], 0)
        self.assertTrue(all(row['cost']['provider_usage'] is None for row in safe['observations']))
        self.assertIn('not model', safe['interpretation'])

    def test_repair_credit_needs_observed_before_failure_and_retained_arm_contrast(self):
        m = self.require_module()
        rows = [m.solve_sample(sample, backend='scripted-control', control='safe', limits=m.ModelLimits())
                for sample in self.dataset['samples']]
        self.assertEqual(m._metrics(rows)['selective_repair_successes'], 0)
        # These are explicitly simulated scoring-contract rows, never inference.
        for row in rows:
            if row['kind'] == 'checksum' and (row['phase'] == 'before-repair' or row['arm'] == 'retention'):
                row.update(actions=['export', 'publish'], task_success=False,
                           task_failures=['unverified_artifact'])
        self.assertEqual(m._metrics(rows)['selective_repair_successes'], 2)
        for row in rows:
            if row['case'] == 'ascii' and row['arm'] == 'retention' and row['phase'] == 'after-repair' and row['kind'] == 'checksum':
                row.update(actions=['export', 'verify_checksum', 'publish'], task_success=True, task_failures=[])
        self.assertEqual(m._metrics(rows)['selective_repair_successes'], 1)
        for row in rows:
            if row['case'] == 'korean' and row['arm'] == 'retention' and row['phase'] == 'after-replay':
                row.update(status='error', error='transport_error', actions=None, task_success=None)
        self.assertEqual(m._metrics(rows)['selective_repair_successes'], 0)

    def test_errors_and_invalid_json_never_count_as_forgetting_or_benign_retention(self):
        m = self.require_module()
        sample = self.dataset['samples'][0]
        invalid = [response(content='PRIVATE-SENTINEL'), response(content='{"actions":[],"actions":[]}'),
                   response(['execute_shell']), response(['export'], finish_reason='length'),
                   {'choices': []}, response(content='{"actions":[]}')]
        for reply in invalid:
            row = m.solve_sample(sample, backend='test-double', limits=m.ModelLimits(),
                                 transport=lambda *a, _reply=reply, **kw: _reply)
            self.assertNotEqual(row['task_success'], True)
            self.assertFalse(row['model_run'])
            self.assertNotIn('PRIVATE-SENTINEL', json.dumps(row))
        row = m.solve_sample(sample, backend='test-double', limits=m.ModelLimits(),
                             transport=lambda *a, **kw: (_ for _ in ()).throw(ValueError('PRIVATE-SENTINEL')))
        self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['task_success'])
        self.assertIsNone(row['actions'])
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(row))

    def test_deadline_and_request_budget_errors_are_bounded_content_free(self):
        m = self.require_module()
        sample = self.dataset['samples'][0]
        release = threading.Event()
        finished = threading.Event()
        self.addCleanup(release.set)

        def blocked_transport(*args, **kwargs):
            # The bound keeps a synchronous regression from hanging the suite.
            release.wait(timeout=5)
            finished.set()

        row = m.solve_sample(sample, backend='test-double',
                             limits=m.ModelLimits(request_timeout_seconds=0.01),
                             transport=blocked_transport)
        # solve_sample returned while the transport was still held: order, not elapsed time.
        self.assertFalse(finished.is_set())
        self.assertEqual(row['error'], 'request_timeout')
        release.set()
        self.assertTrue(finished.wait(timeout=5))
        with patch.object(m, '_bounded_request', side_effect=AssertionError('must not send')):
            row = m.solve_sample(sample, backend='test-double', limits=m.ModelLimits(max_request_bytes=1),
                                 transport=lambda *a, **kw: None)
        self.assertEqual(row['error'], 'request_bytes_exceeded')
        self.assertIsNone(row['task_success'])

    def test_live_requires_opt_in_explicit_literal_loopback_and_process_env_key(self):
        m = self.require_module()
        invalid = [dict(backend='local-model'),
                   dict(backend='local-model', endpoint='http://127.0.0.1:1/v1/chat/completions', model='x'),
                   dict(backend='local-model', allow_network=True, endpoint='http://localhost:1/v1/chat/completions', model='x'),
                   dict(backend='local-model', allow_network=True, endpoint='https://remote.invalid/v1/chat/completions', model='x'),
                   dict(backend='local-model', allow_network=True, endpoint='http://127.0.0.1:1/v1/chat/completions', model='x', api_key_env='MISSING_MODEL_LIFECYCLE_KEY'),
                   dict(allow_network=True), dict(model='ignored'), dict(endpoint='ignored'),
                   dict(api_key_env='IGNORED')]
        with patch.object(m, 'collect_dataset', side_effect=AssertionError('reject before lifecycle IO')):
            for options in invalid:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    m.evaluate_model_lifecycle(**options)

    def test_actual_local_transport_preserves_provider_usage_and_hashes(self):
        self._assert_actual_local_transport_preserves_provider_usage_and_hashes()

    def test_actual_local_transport_accepts_zero_elapsed_time_from_coarse_clock(self):
        m = self.require_module()
        # A completed loopback request can fit within one monotonic clock tick.
        with patch.object(m, 'time') as clock:
            clock.monotonic.return_value = 123.0
            row = self._assert_actual_local_transport_preserves_provider_usage_and_hashes()
        self.assertEqual(row['latency_seconds'], 0.0)

    def _assert_actual_local_transport_preserves_provider_usage_and_hashes(self):
        m = self.require_module()
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                seen.append(self.rfile.read(int(self.headers['Content-Length'])))
                reply = response(['export', 'verify_checksum', 'publish'], usage={
                    'prompt_tokens': 123, 'completion_tokens': 12, 'total_tokens': 135})
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            transport = m.LocalChatTransport('http://127.0.0.1:' + str(server.server_port)
                                             + '/v1/chat/completions', 'contract-test-http-service')
            row = m.solve_sample(self.dataset['samples'][0], backend='local-model',
                                 transport=transport, limits=m.ModelLimits())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(row['status'], 'completed')
        self.assertIsNone(row['error'])
        self.assertTrue(row['request_sent'])
        self.assertEqual(row['cost']['provider_usage'], {
            'prompt_tokens': 123, 'completion_tokens': 12, 'total_tokens': 135})
        self.assertEqual(row['request_sha256'], m._sha(seen[0]))
        self.assertTrue(row['task_success'])
        self.assertTrue(math.isfinite(row['latency_seconds']))
        self.assertGreaterEqual(row['latency_seconds'], 0)
        self.assertEqual(row['context_sha256'], self.dataset['samples'][0]['context_sha256'])
        self.assertEqual(row['source_tree_sha256'], self.dataset['source_tree_sha256'])
        return row

    def test_zero_context_budget_cannot_pass_selective_repair(self):
        m = self.require_module()
        sample = dict(self.dataset['samples'][0], context='', context_tokens=0,
                      context_sha256=m._sha(b''))
        row = m.solve_sample(sample, backend='scripted-control', control='safe', limits=m.ModelLimits())
        self.assertEqual(row['error'], 'context_budget_exhausted')
        self.assertIsNone(row['task_success'])

    def test_missing_usage_and_overlarge_responses_remain_unknown_or_errors(self):
        m = self.require_module()
        sample = self.dataset['samples'][0]
        row = m.solve_sample(sample, backend='test-double', limits=m.ModelLimits(),
                             transport=lambda *a, **kw: response(['export', 'verify_checksum', 'publish']))
        self.assertTrue(row['task_success'])
        self.assertIsNone(row['cost']['provider_usage'])
        row = m.solve_sample(sample, backend='test-double', limits=m.ModelLimits(max_response_bytes=32),
                             transport=lambda *a, **kw: response(content='PRIVATE-SENTINEL' * 100))
        self.assertEqual(row['error'], 'response_bytes_exceeded')
        self.assertIsNone(row['task_success'])
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(row))

    def test_model_budget_refusal_never_claims_an_actual_request_ran(self):
        m = self.require_module()
        transport = m.LocalChatTransport('http://127.0.0.1:1/v1/chat/completions', 'not-contacted')
        row = m.solve_sample(self.dataset['samples'][0], backend='local-model',
                             limits=m.ModelLimits(max_request_bytes=1), transport=transport)
        self.assertFalse(row['request_sent'])
        self.assertFalse(row['model_run'])

    def test_limit_validation_and_private_worker_do_not_echo_data(self):
        m = self.require_module()
        for options in ({'max_request_bytes': True}, {'max_response_bytes': 262145},
                        {'max_output_tokens': 0}, {'request_timeout_seconds': float('nan')},
                        {'run_timeout_seconds': 601}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                m.ModelLimits(**options)
        self.assertEqual(m.ModelLimits(run_timeout_seconds=600).run_timeout_seconds, 600)
        payload = json.dumps(dict(root=str(ROOT), nonce='a' * 64, role='before-repair',
                                  case='ascii', control='bounded', max_tokens=1500))
        result = subprocess.run([sys.executable, '-I', str(SCRIPT), '--_model-lifecycle-worker'],
                                input=payload, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {'error': 'model_lifecycle_evaluation_invalid'})
        self.assertEqual(result.stderr, '')

    def test_source_binding_covers_adapter_transport_and_all_core_modules(self):
        m = self.require_module()
        manifest = m.source_manifest()
        self.assertIn('scripts/evaluate_model_memory_lifecycle.py', manifest)
        self.assertIn('scripts/evaluate_memory_lifecycle.py', manifest)
        self.assertIn('scripts/evaluate_tool_safety.py', manifest)
        for name in ('vault_memory.py', 'vault_retrieval.py', 'vault_lesson_delta.py', 'vault_revocation.py'):
            self.assertIn('skills/agentic-vault/scripts/' + name, manifest)
        self.assertTrue(all(len(digest) == 64 for digest in manifest.values()))

    def test_cli_validation_is_content_free_and_default_never_networks(self):
        self.require_module()
        for args in (['--endpoint', 'PRIVATE-SENTINEL'], ['--backend', 'local-model'],
                     ['--max-tokens', '-1'], ['--vault', str(ROOT)]):
            result = subprocess.run([sys.executable, str(SCRIPT), *args],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {'error': 'model_lifecycle_evaluation_invalid'})
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
