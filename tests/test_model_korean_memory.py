import copy
import importlib.util
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.find_spec('evaluate_model_korean_memory')
if SPEC:
    import evaluate_model_korean_memory as runner


class FreshKoreanTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SPEC, 'fresh Korean model evaluator is missing')

    def test_paired_requests_use_public_payload_and_independent_completions(self):
        dataset = runner.collect_dataset()
        self.assertEqual(len(dataset['samples']), 100)
        first, second = dataset['samples'][:2]
        self.assertEqual(first['case'], second['case'])
        self.assertEqual({first['arm'], second['arm']}, {'default', 'annotate-only'})
        request = runner.build_request(first, limits=runner.ModelLimits())
        payload = json.loads(request['messages'][1]['content'])
        self.assertEqual(set(payload), {'question', 'options', 'evidence'})
        self.assertEqual(payload['question'], first['payload']['question'])
        report = runner.evaluate_model_korean_memory()
        self.assertEqual(len(report['rows']), 100)
        self.assertEqual(report['summary']['completed'], 100)
        self.assertEqual(len(report['receipt']['rows']), 100)

    def test_receipt_rejects_swapped_arms_and_changed_labels(self):
        dataset = runner.collect_dataset()
        report = runner.evaluate_model_korean_memory()
        receipt = report['receipt']
        self.assertEqual(len(runner.replay_dataset(dataset, receipt)['rows']), 100)
        swapped = copy.deepcopy(receipt)
        swapped['rows'][0], swapped['rows'][1] = swapped['rows'][1], swapped['rows'][0]
        with self.assertRaises(ValueError):
            runner.replay_dataset(dataset, swapped)
        changed = copy.deepcopy(dataset)
        changed['samples'][0]['label']['expected_paths'] = ['changed.md']
        with self.assertRaises(ValueError):
            runner.replay_dataset(changed, receipt)
        stale = copy.deepcopy(dataset)
        stale['source_manifest'][next(iter(stale['source_manifest']))] = '0' * 64
        with self.assertRaises(ValueError):
            runner.replay_dataset(stale, receipt)
        changed_contract = copy.deepcopy(receipt)
        changed_contract['contract']['model'] = 'other-model'
        with self.assertRaises(ValueError):
            runner.replay_dataset(dataset, changed_contract)
        bad_row = copy.deepcopy(receipt)
        bad_row['rows'][0].update(status='error', error='transport_error', response=None)
        with self.assertRaises(ValueError):
            runner.replay_dataset(dataset, bad_row)

    def test_invalid_completion_is_error_not_abstention(self):
        dataset = runner.collect_dataset()
        class Invalid:
            def __call__(self, payload, **bounds):
                return {'choices': [{'message': {'role': 'assistant', 'content': '{"status":"abstain"} trailing'}, 'finish_reason': 'stop'}]}
        row = runner.solve_sample(dataset['samples'][0], backend='test-double', transport=Invalid())
        self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['grade'])
        self.assertNotIn('trailing', json.dumps(row))

    def test_loopback_sends_two_fresh_requests_per_case_and_retains_invalid_usage(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))) )
                body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'invalid private prose'},
                                               'finish_reason': 'stop'}],
                                   'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = runner.evaluate_model_korean_memory(backend='local-model', allow_network=True,
                endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions', model='fixture-model',
                limits=runner.ModelLimits(request_timeout_seconds=1, run_timeout_seconds=120))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(len(received), 100)
        self.assertEqual(report['summary']['sent'], 100)
        self.assertEqual(report['summary']['errors'], 100)
        self.assertEqual(report['rows'][0]['provider_usage']['total_tokens'], 4)
        self.assertIsNone(report['rows'][0]['grade'])
        self.assertNotIn('invalid private prose', json.dumps(report))
        self.assertEqual(set(json.loads(received[0]['messages'][1]['content'])), {'question', 'options', 'evidence'})
        self.assertEqual(received[0], received[1])

    def test_parser_rejects_duplicate_keys_and_truncated_finish(self):
        sample = runner.collect_dataset()['samples'][0]
        class Reply:
            def __init__(self, content, finish='stop'):
                self.content, self.finish = content, finish
            def __call__(self, payload, **bounds):
                return {'choices': [{'message': {'role': 'assistant', 'content': self.content},
                                     'finish_reason': self.finish}]}
        valid = runner.solve_sample(sample, backend='test-double', transport=Reply('{"status":"abstain","assertions":[]}'))
        self.assertEqual(valid['status'], 'completed')
        self.assertIsNotNone(valid['grade'])
        too_small = runner.solve_sample(sample, backend='test-double', transport=Reply('ignored'),
                                        limits=runner.ModelLimits(max_request_bytes=1))
        self.assertEqual(too_small['error'], 'request_bytes_exceeded')
        self.assertFalse(too_small['request_sent'])
        for reply in (Reply('{"status":"abstain","status":"abstain","assertions":[]}'),
                      Reply('{"status":"abstain","assertions":[]}', 'length')):
            row = runner.solve_sample(sample, backend='test-double', transport=reply)
            self.assertEqual(row['status'], 'error')
            self.assertIsNone(row['grade'])

    def test_timeout_stops_remaining_requests_without_abstentions(self):
        received = []
        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                received.append(1)
                time.sleep(0.5)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = runner.evaluate_model_korean_memory(backend='local-model', allow_network=True,
                endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions', model='fixture-model',
                limits=runner.ModelLimits(request_timeout_seconds=0.1, run_timeout_seconds=30))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(len(received), 1)
        self.assertIn(report['rows'][0]['error'], ('request_timeout', 'transport_error'))
        self.assertTrue(all(row['error'] == 'run_stopped' and row['grade'] is None for row in report['rows'][1:]))
        self.assertEqual(report['summary']['sent'], 1)

    def test_review_legacy_function_call_is_rejected(self):
        sample = runner.collect_dataset()['samples'][0]
        class LegacyCall:
            def __call__(self, payload, **bounds):
                return {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': '{"status":"abstain","assertions":[]}',
                    'function_call': {'name': 'do_bad', 'arguments': '{}'}}}]}
        row = runner.solve_sample(sample, backend='test-double', transport=LegacyCall())
        self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['grade'])

    def test_review_replay_rejects_impossible_local_execution_after_rehash(self):
        dataset = runner.collect_dataset()
        receipt = runner.evaluate_model_korean_memory()['receipt']
        receipt['origin'] = 'local-model'
        receipt['contract']['model'] = 'fixture-model'
        receipt['contract']['endpoint'] = 'http://127.0.0.1:1/v1/chat/completions'
        for sample, row in zip(dataset['samples'], receipt['rows']):
            row['request_sent'] = True
            row['request_sha256'] = runner._hash(runner.build_request(sample, model='fixture-model',
                                                       limits=runner.ModelLimits()))
        def check(mutator):
            forged = copy.deepcopy(receipt)
            mutator(forged)
            limits = runner.ModelLimits(**forged['contract']['limits'])
            for sample, row in zip(dataset['samples'], forged['rows']):
                row['request_sha256'] = runner._hash(runner.build_request(sample, model='fixture-model', limits=limits))
            forged['receipt_sha256'] = runner._hash({k: v for k, v in forged.items() if k != 'receipt_sha256'})
            with self.assertRaises(ValueError):
                runner.replay_dataset(dataset, forged)
        cases = {
            'output usage exceeds budget': lambda r: r['rows'][0].update(provider_usage={'prompt_tokens': 1, 'completion_tokens': 129, 'total_tokens': 130}),
            'request exceeds bytes': lambda r: r['contract']['limits'].update(max_request_bytes=1),
            'response exceeds bytes': lambda r: r['contract']['limits'].update(max_response_bytes=1),
            'sends after timeout': lambda r: r['rows'][0].update(status='error', error='request_timeout', response=None),
            'invalid answer without request': lambda r: r['rows'][0].update(status='error', error='invalid_answer', response=None, request_sent=False),
        }
        for name, mutator in cases.items():
            with self.subTest(name=name):
                check(mutator)

    def test_review2_replay_rejects_scripted_oversize_and_impossible_initial_deadline(self):
        dataset = runner.collect_dataset()
        original = runner.evaluate_model_korean_memory()['receipt']
        def rebind(receipt):
            limits = runner.ModelLimits(**receipt['contract']['limits'])
            for sample, row in zip(dataset['samples'], receipt['rows']):
                row['request_sha256'] = runner._hash(runner.build_request(
                    sample, model=receipt['contract']['model'], limits=limits))
            receipt['receipt_sha256'] = runner._hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
        scripted = copy.deepcopy(original)
        scripted['contract']['limits']['max_request_bytes'] = 1
        rebind(scripted)
        with self.subTest(case='scripted request budget'):
            with self.assertRaises(ValueError):
                runner.replay_dataset(dataset, scripted)
        local = copy.deepcopy(original)
        local['origin'] = 'local-model'
        local['contract'].update(model='fixture-model', endpoint='http://127.0.0.1:1/v1/chat/completions')
        local['contract']['limits'].update(run_timeout_seconds=0.5, request_timeout_seconds=1)
        for row in local['rows']:
            row['request_sent'] = True
        rebind(local)
        with self.subTest(case='local initial reserve'):
            with self.assertRaises(ValueError):
                runner.replay_dataset(dataset, local)

    def test_review2_actual_zero_reserve_and_scripted_oversize_replay(self):
        tiny = runner.ModelLimits(max_request_bytes=1)
        timed = runner.ModelLimits(run_timeout_seconds=0.5, request_timeout_seconds=1)
        for limits, expected in ((tiny, 'request_bytes_exceeded'), (timed, 'run_timeout')):
            with self.subTest(error=expected):
                report = runner.evaluate_model_korean_memory(limits=limits)
                self.assertTrue(all(row['error'] == expected for row in report['rows']))
                self.assertEqual(runner.replay_dataset(runner.collect_dataset(), report['receipt'])['summary'], report['summary'])

    def test_review2_actual_local_zero_reserve_replay(self):
        limits = runner.ModelLimits(run_timeout_seconds=0.5, request_timeout_seconds=1)
        report = runner.evaluate_model_korean_memory(backend='local-model', allow_network=True,
            endpoint='http://127.0.0.1:1/v1/chat/completions', model='fixture-model', limits=limits)
        self.assertEqual(report['summary']['sent'], 0)
        self.assertTrue(all(row['error'] == 'run_timeout' for row in report['rows']))
        replay = runner.replay_dataset(runner.collect_dataset(), report['receipt'])
        self.assertEqual(replay['summary'], report['summary'])

    def test_local_model_requires_explicit_loopback_opt_in(self):
        with self.assertRaises(ValueError):
            runner.evaluate_model_korean_memory(backend='local-model', endpoint='http://example.com/v1/chat/completions', model='x')
        with self.assertRaises(ValueError):
            runner.evaluate_model_korean_memory(backend='local-model', allow_network=True, endpoint='http://localhost/v1/chat/completions', model='x')


if __name__ == '__main__':
    unittest.main()
