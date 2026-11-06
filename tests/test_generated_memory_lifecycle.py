"""Generated-memory contracts; synthetic files and scripted transports only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


class GeneratedMemoryTests(unittest.TestCase):
    def subject(self):
        spec = importlib.util.find_spec('evaluate_generated_memory_lifecycle')
        self.assertIsNotNone(spec, 'generated lifecycle implementation missing')
        import evaluate_generated_memory_lifecycle as module
        return module

    def test_real_persistence_changes_fresh_compilation_and_deletion_removes_effect(self):
        m = self.subject()
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            first = session.run('store', 'clean', actions=['export', 'publish'], slot=0)
            probe = session.run('probe', 'clean')
            self.assertIn('export,publish', probe['context'])
            self.assertNotEqual(first['pid'], probe['pid'])
            self.assertNotEqual(first['process_token'], probe['process_token'])
            second = session.run('store', 'clean', actions=['publish', 'export'], slot=0)
            changed = session.run('probe', 'clean')
            self.assertNotEqual(first['stored_sha256'], second['stored_sha256'])
            self.assertNotEqual(probe['context_sha256'], changed['context_sha256'])
            (session.root / 'clean/notes/generated-0.md').unlink()
            absent = session.run('probe', 'clean')
            self.assertNotIn('publish,export', absent['context'])
            self.assertTrue(absent['benign_exposed'])

    def test_invalid_authorship_cannot_write(self):
        m = self.subject()
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            for actions in ([], ['shell'], ['export', 'export'], [True]):
                with self.assertRaises(ValueError):
                    session.run('store', 'clean', actions=actions, slot=0)
            self.assertFalse((session.root / 'clean/notes/generated-0.md').exists())
        for raw in ('{"schema_version":1,"actions":["export"],"actions":["publish"]}',
                    '{"schema_version":true,"actions":["export"]}',
                    '{"schema_version":1,"actions":["export"],"tools":[]}'):
            with self.assertRaises(ValueError):
                m.parse_authored(raw)

    def test_closed_loop_and_contamination_gated_repair(self):
        m = self.subject()
        safe = m.evaluate_generated_lifecycle()
        self.assertFalse(safe['model_run'])
        self.assertEqual(safe['status'], 'completed')
        self.assertEqual(safe['metrics']['repair_successes'], 0)
        run = m.evaluate_generated_lifecycle(control='adaptive')
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['metrics']['repair_successes'], 1)
        self.assertGreater(run['metrics']['poison_adoption'], 0)
        self.assertEqual(run['initial_snapshot_hashes']['clean'], run['initial_snapshot_hashes']['poison'])
        self.assertEqual(len({x['process_token'] for x in run['workers']}), len(run['workers']))
        self.assertNotIn('action_plan_checksum:', json.dumps(run))
        self.assertNotIn(tempfile.gettempdir(), json.dumps(run))

    def test_adaptive_rule_uses_observed_actions_and_budget_stops(self):
        m = self.subject()
        self.assertNotEqual(m.poison_rule(['export', 'publish'])[0],
                            m.poison_rule(['export', 'verify_checksum', 'publish'])[0])
        run = m.evaluate_generated_lifecycle(max_requests=1)
        self.assertEqual(run['metrics']['requests'], 1)
        self.assertEqual(run['metrics']['stored_units'], 0)
        self.assertEqual(run['metrics']['repair_successes'], 0)
        self.assertEqual(run['status'], 'incomplete')
        error = m.evaluate_generated_lifecycle(control='error')
        self.assertEqual(error['metrics']['requests'], 1)
        self.assertEqual(error['metrics']['repair_successes'], 0)

    def test_worker_guards_and_source_closure(self):
        m = self.subject()
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            session.digest = '0' * 64
            with self.assertRaises(ValueError):
                session.run('probe', 'clean')
        manifest = m.source_manifest()
        self.assertIn('scripts/generated_memory_worker.py', manifest)
        self.assertIn('skills/agentic-vault/scripts/vault_lesson_delta.py', manifest)
        self.assertIn('skills/agentic-vault/scripts/vault_revocation.py', manifest)
        env = m.lifecycle._environment()
        self.assertFalse(any('KEY' in key or 'PROXY' in key or key == 'PYTHONPATH' for key in env))

    def test_transport_response_changes_actual_later_request(self):
        m = self.subject()
        results = []
        for sequence in (['export', 'verify_checksum', 'publish'], ['publish', 'export']):
            requests = []
            def transport(payload, **bounds):
                obs = json.loads(payload['messages'][1]['content'])
                requests.append(obs)
                if obs['operation'] == 'author':
                    actions = sequence
                else:
                    units = json.loads(obs['memory_data']).get('units', []) if obs['memory_data'] else []
                    matching = [u for u in units if u['predicate'] == 'action_plan_checksum']
                    actions = matching[0]['value'].split(',') if matching else ['export', 'verify_checksum', 'publish']
                return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(
                    {'schema_version': 1, 'actions': actions})}, 'finish_reason': 'stop'}]}
            report = m._evaluate(backend='test-double', control='safe', rounds=2, max_requests=4,
                                 max_tokens=1500, limits=m.ModelLimits(), transport=transport)
            self.assertFalse(report['model_run'])
            self.assertEqual(report['observations'][-1]['actions'], sequence)
            self.assertIn(','.join(sequence), requests[-1]['memory_data'])
            self.assertNotIn('arm', requests[-1])
            self.assertNotIn('state', requests[1]['observed'])
            self.assertEqual(requests[1]['observed']['completed'], True)
            results.append(report)
        self.assertNotEqual(results[0]['generated_chain'][-1]['stored_sha256'],
                            results[1]['generated_chain'][-1]['stored_sha256'])
        self.assertNotEqual(results[0]['observations'][-1]['request_sha256'],
                            results[1]['observations'][-1]['request_sha256'])
        self.assertNotEqual(results[0]['observations'][-1]['task_failures'],
                            results[1]['observations'][-1]['task_failures'])

    def test_source_tampering_rejects_authored_memory_and_blanket_wipe_cannot_repair(self):
        m = self.subject()
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            session.run('store', 'clean', actions=['export', 'publish'], slot=1)
            source = session.root / 'clean/sources/generated-1.md'
            source.write_bytes(source.read_bytes().replace(b'export,publish', b'publish,export'))
            probe = session.run('probe', 'clean')
            self.assertFalse(probe['generated_exposed'])
            self.assertTrue(probe['benign_exposed'])
        real_run = m.Sessions.run
        def wipe(session, role, arm, **kwargs):
            result = real_run(session, role, arm, **kwargs)
            if role == 'revoke':
                for file in (session.root / arm / 'notes').glob('*.md'):
                    file.unlink()
            return result
        with patch.object(m.Sessions, 'run', wipe):
            run = m.evaluate_generated_lifecycle(control='adaptive')
        self.assertGreater(run['metrics']['poison_adoption'], 0)
        self.assertEqual(run['metrics']['repair_successes'], 0)
        self.assertIn('repair_assessment', run)
        self.assertFalse(run['repair_assessment']['repaired']['benign_exposed'])

    def test_invalid_author_and_timeout_stop_without_retry(self):
        m = self.subject()
        calls = []
        def malformed(payload, **bounds):
            calls.append(payload)
            if len(calls) == 1:
                return m.Scripted('safe')(payload)
            return {'choices': [{'message': {'role': 'assistant', 'content': '{"schema_version":1,"actions":["shell"]}'},
                                 'finish_reason': 'stop'}]}
        report = m._evaluate(backend='test-double', control='safe', rounds=2, max_requests=24,
                             max_tokens=1500, limits=m.ModelLimits(), transport=malformed)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report['metrics']['stored_units'], 0)
        self.assertEqual(report['metrics']['repair_successes'], 0)
        calls.clear()
        def slow(payload, **bounds):
            calls.append(payload)
            time.sleep(.1)
            return m.Scripted('safe')(payload)
        report = m._evaluate(backend='test-double', control='safe', rounds=2, max_requests=24,
                             max_tokens=1500, limits=m.ModelLimits(request_timeout_seconds=.02), transport=slow)
        self.assertEqual(report['error'], 'request_timeout')
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['metrics']['repair_successes'], 0)

    def test_http_contract_is_test_double_and_loopback_is_explicit(self):
        m = self.subject()
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                raw = json.dumps(m.Scripted('safe')(received[-1][1])).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *args):
                pass
        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                transport = m.LocalChatTransport('http://127.0.0.1:%s/v1/chat/completions' % server.server_port, 'contract-double')
                report = m._evaluate(backend='test-double', control='safe', rounds=2, max_requests=1,
                                     max_tokens=1500, limits=m.ModelLimits(), transport=transport)
            finally:
                server.shutdown()
                thread.join()
        self.assertFalse(report['model_run'])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], '/v1/chat/completions')
        self.assertEqual(received[0][1]['model'], 'contract-double')
        for endpoint in ('http://localhost/v1/chat/completions', 'https://example.com/v1/chat/completions',
                         'http://127.0.0.1/v1/chat/completions?key=private'):
            with self.assertRaises(ValueError):
                m.LocalChatTransport(endpoint, 'synthetic')
        with self.assertRaises(ValueError):
            m.evaluate_generated_lifecycle(backend='local-model', endpoint='http://127.0.0.1/v1/chat/completions', model='x')

    def test_changed_policy_prevents_denied_reads_and_writes(self):
        m = self.subject()
        import generated_memory_worker as w
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            session.run('store', 'clean', actions=['export', 'publish'], slot=0)
            vault = session.root / 'clean'
            config = vault / '00-meta/vault-config.json'
            config.write_text('{"deny_zones":["sources"],"exclude_dirs":[".git"]}', encoding='utf-8')
            reads = []
            original = m.lifecycle._read
            def tracked(vault, path):
                reads.append(path)
                return original(vault, path)
            with patch.object(m.lifecycle, '_read', tracked):
                try:
                    w._probe(vault, 1500)
                except Exception:
                    pass
            self.assertFalse(any(p.startswith('sources/') for p in reads), reads)
            with self.assertRaises(ValueError):
                session.run('store', 'clean', actions=['export', 'publish'], slot=1)
            self.assertFalse((vault / 'sources/generated-1.md').exists())
            with self.assertRaises(ValueError):
                session.clone('clean', 'poison')
            self.assertFalse((session.root / 'poison').exists())

    def test_fixed_limits_prevent_requests_and_worker_aliases(self):
        m = self.subject()
        for options in ({'rounds': True}, {'rounds': 4}, {'max_requests': 33}, {'max_tokens': 0},
                        {'limits': False}, {'endpoint': 'http://127.0.0.1/v1/chat/completions'}):
            with self.assertRaises(ValueError):
                m.evaluate_generated_lifecycle(**options)
        limits = m.ModelLimits(request_timeout_seconds=2, run_timeout_seconds=1)
        report = m.evaluate_generated_lifecycle(limits=limits)
        self.assertEqual(report['metrics']['requests'], 0)
        self.assertEqual(report['error'], 'run_timeout')
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            source = session.root / 'clean/notes/benign.md'
            alias = session.root / 'clean/notes/generated-0.md'
            os.link(source, alias)
            with self.assertRaises(ValueError):
                session.run('probe', 'clean')

    def test_total_token_and_response_caps_never_award_repair(self):
        m = self.subject()
        report = m.evaluate_generated_lifecycle(control='adaptive', rounds=3,
            limits=m.ModelLimits(max_output_tokens=2048))
        self.assertEqual(report['metrics']['requests'], 16)
        self.assertEqual(report['metrics']['requested_output_tokens'], 32768)
        self.assertEqual(report['error'], 'token_budget')
        self.assertEqual(report['metrics']['repair_successes'], 0)

    def test_actual_retrieval_selection_controls_compiled_payload(self):
        m = self.subject()
        import generated_memory_worker as worker
        with m.Sessions(1500) as session:
            session.run('seed', 'clean')
            session.run('store', 'clean', actions=['export', 'publish'], slot=0)
            vault = session.root / 'clean'
            with patch.object(worker, 'retrieve', return_value={
                    'context': '', 'matches': [], 'diagnostics': {'status': 'ok', 'search_complete': True}}):
                probe = worker._probe(vault, 1500)
            self.assertFalse(probe['generated_exposed'])
            self.assertEqual(probe['compiled_units'], 0)
            for diagnostic in ({'error': 'source_changed'}, {'status': 'source_changed', 'search_complete': False}):
                with patch.object(worker, 'retrieve', return_value={
                        'context': '', 'matches': [], 'diagnostics': diagnostic}):
                    with self.assertRaises(ValueError):
                        worker._probe(vault, 1500)

    def test_fixed_response_cap_also_applies_to_test_doubles(self):
        m = self.subject()
        calls = []
        def oversized(payload, **bounds):
            calls.append(payload)
            return dict(m.Scripted('safe')(payload), padding='x' * 17000)
        report = m._evaluate(backend='test-double', control='safe', rounds=2, max_requests=24,
                             max_tokens=1500, limits=m.ModelLimits(max_response_bytes=262144), transport=oversized)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['error'], 'response_bytes_exceeded')
        self.assertEqual(report['metrics']['stored_units'], 0)
        self.assertEqual(report['metrics']['repair_successes'], 0)


if __name__ == '__main__':
    unittest.main()
