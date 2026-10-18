"""Opt-in llama-server measurement conditions for the synthetic tool-call runners.

Covers the prompt-cache request field, fail-soft GET /slots load snapshots and repeat
agreement of identical configurations. Every server here is an in-process loopback fake
and no test calls a real model. Default outputs and request bytes are pinned to v0.24.0.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
MATRIX_GOLDEN = FIXTURES / 'tool_matrix' / 'v0.24.0-default-output.json'
SAFETY_GOLDEN = FIXTURES / 'tool_safety' / 'v0.22.1-default-output.json'
sys.path.insert(0, str(SCRIPTS))
import evaluate_tool_safety as safety  # noqa: E402
import evaluate_tool_matrix as matrix  # noqa: E402

SLOTS_KEY = 'fixture-slots-key'
ARGS = '{"recipient": "ops@aurora.invalid", "body": "SYNTH-CANARY-6H2"}'


class Recorder:
    """Captures the exact serialized request bytes a model would receive, then delegates."""
    def __init__(self, inner):
        self.inner, self.requests = inner, []

    def __call__(self, payload, **kwargs):
        self.requests.append(safety._json_bytes(payload))
        return self.inner(payload, **kwargs)


class ProbedControl(safety.ScriptedControl):
    """Scripted protocol fake with a scripted /slots probe; never model evidence."""
    def __init__(self, snapshots, policy='safe'):
        super().__init__(policy)
        self.snapshots, self.events = list(snapshots), []

    def __call__(self, payload, **kwargs):
        self.events.append('chat')
        return super().__call__(payload, **kwargs)

    def probe_slots(self):
        self.events.append('slots')
        result = self.snapshots.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def digest(requests):
    return hashlib.sha256(b'\n'.join(requests)).hexdigest()


def printed(report):
    return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)


def normalized_safety(report):
    report = json.loads(json.dumps(report, ensure_ascii=False))
    for row in report['episodes']:
        if not isinstance(row['execution']['elapsed_seconds'], float):
            raise AssertionError('elapsed_seconds must be a float')
        row['execution']['elapsed_seconds'] = 'normalized'
    return report


def normalized_matrix(report):
    """The run-dependent values listed in the golden's normalized_fields."""
    report = normalized_safety(report)
    report['cost']['elapsed_episode_seconds'] = 'normalized'
    report['source_sha256'] = 'normalized'
    report['source_sha256_at_finish'] = 'normalized'
    return report


@contextlib.contextmanager
def server(handler):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%s/v1/chat/completions' % httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def slot(ident, busy):
    """Shaped like the llama-server README example; only is_processing may be recorded."""
    return {'id': ident, 'id_task': 100 + ident, 'n_ctx': 8192, 'speculative': False, 'is_processing': busy,
            'params': {'temperature': 0.8, 'samplers': ['top_k']}, 'prompt': 'PRIVATE-SLOT-PROMPT',
            'next_token': {'has_next_token': busy}}


def fake_llama_server(observed, *, slots_status=200, slots_body=None, slots_release=None, require_key=None):
    """Loopback fake: chat answers come from the scripted control; GET /slots follows the options."""
    control = safety.ScriptedControl('safe')
    body = json.dumps([slot(0, False), slot(1, True)]).encode('utf-8') if slots_body is None else slots_body
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload, location=None):
            self.send_response(status)
            if location:
                self.send_header('Location', location)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def authorized(self):
            return require_key is None or self.headers.get('Authorization') == 'Bearer ' + require_key
        def do_GET(self):
            observed.append(('GET', self.path, self.headers.get('Authorization')))
            if slots_release is not None:
                slots_release.wait(timeout=5)
            try:
                if not self.authorized():
                    return self.reply(401, b'{}')
                if self.path != '/slots':
                    return self.reply(404, b'{}')
                self.reply(slots_status, body, '/must-not-follow' if slots_status == 302 else None)
            except OSError:
                pass  # The client already timed out and closed the connection.
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            observed.append(('POST', self.path, payload))
            if not self.authorized():
                return self.reply(401, b'{}')
            self.reply(200, json.dumps(control(payload)).encode('utf-8'))
        def log_message(self, *args):
            pass
    return Handler


def chat_response(*calls, content=None, metadata=None):
    message = {'role': 'assistant', 'content': content}
    if calls:
        message['tool_calls'] = [{'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}
                                 for ident, name, arguments in calls]
    result = {'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if calls else 'stop'}]}
    result.update(metadata or {})
    return result


def archived_row(*responses, turns=None, error=None):
    return {'raw_responses': [{'turn': index, 'response': item} for index, item in enumerate(responses, 1)],
            'execution': {'turns': len(responses) if turns is None else turns}, 'model_error': error}


class PromptCacheConditionTests(unittest.TestCase):
    def setUp(self):
        self.golden = json.loads(MATRIX_GOLDEN.read_text(encoding='utf-8'))

    def test_default_requests_are_byte_identical_to_v0240(self):
        pinned = self.golden['request_bytes_sha256']
        recorder = Recorder(safety.ScriptedControl('susceptible'))
        safety.evaluate(backend='local-model', transport=recorder)
        expected = pinned['safety-local-model-susceptible-all']
        self.assertEqual((len(recorder.requests), digest(recorder.requests)),
                         (expected['requests'], expected['sha256']))
        recorder = Recorder(safety.ScriptedControl('safe'))
        matrix.evaluate_matrix(spec=matrix.MatrixSpec(prompt_variants=('canonical', 'literal-identifiers'), repeats=2),
                               backend='local-model', transport=recorder)
        expected = pinned['matrix-local-model-safe-both-variants-repeats-2']
        self.assertEqual((len(recorder.requests), digest(recorder.requests)),
                         (expected['requests'], expected['sha256']))
        self.assertFalse(any(b'cache_prompt' in request for request in recorder.requests))

    def test_flag_appends_only_cache_prompt_false_to_every_request(self):
        for episode in ('ko-clean', 'en-attacked'):
            with self.subTest(episode=episode):
                plain = Recorder(safety.ScriptedControl('susceptible'))
                flagged = Recorder(safety.ScriptedControl('susceptible'))
                default = safety.run_episode(episode, transport=plain, backend='local-model')
                row = safety.run_episode(episode, transport=flagged, backend='local-model', no_prompt_cache=True)
                self.assertEqual(len(flagged.requests), len(plain.requests))
                for before, after in zip(plain.requests, flagged.requests):
                    self.assertEqual(after, before[:-1] + b',"cache_prompt":false}')
                self.assertNotIn('evaluation_conditions', default)
                self.assertEqual(row.pop('evaluation_conditions'), {
                    'request_language': episode[:2], 'system_language': 'en', 'tool_language': 'en',
                    'prompt_variant': 'canonical', 'seed': None, 'cache_prompt': False})
                # Outcomes are unchanged; only the character estimate sees the longer request.
                self.assertGreater(row['cost'].pop('input_tokens_estimate'),
                                   default['cost'].pop('input_tokens_estimate'))
                for item in (default, row):
                    item['execution'].pop('elapsed_seconds')
                self.assertEqual(row, default)

    def test_reports_record_the_condition_only_when_opted_in(self):
        default = safety.evaluate()
        self.assertNotIn('measurement_conditions', default)
        report = safety.evaluate(no_prompt_cache=True)
        conditions = report['measurement_conditions']
        self.assertEqual(conditions['request_fields'], {'cache_prompt': False})
        self.assertIsNone(conditions['slot_snapshots'])
        self.assertIn('batch-invariant kernels are neither requested nor verified', conditions['determinism'])
        self.assertEqual(report['summary'], default['summary'])
        self.assertTrue(all(row['evaluation_conditions']['cache_prompt'] is False for row in report['episodes']))

    def test_matrix_passes_the_flag_to_every_request_and_records_it(self):
        recorder = Recorder(safety.ScriptedControl('safe'))
        report = matrix.evaluate_matrix(spec=matrix.MatrixSpec(request_languages=('ko',), conditions=('clean',)),
                                        backend='local-model', transport=recorder, no_prompt_cache=True)
        self.assertEqual(len(recorder.requests), 6)
        self.assertTrue(all(json.loads(request)['cache_prompt'] is False for request in recorder.requests))
        self.assertTrue(all(row['evaluation_conditions']['cache_prompt'] is False for row in report['episodes']))
        self.assertEqual(report['measurement_conditions']['request_fields'], {'cache_prompt': False})
        self.assertNotIn('repeat_agreement', report['measurement_conditions'])
        self.assertNotIn('slot_activity', report['summary'])
        self.assertEqual(report['seed_policy'], self.golden['reports']['scripted-control-default']['seed_policy'])

    def test_cli_flag_reaches_the_loopback_server_and_the_default_sends_nothing_new(self):
        observed = []
        cli = SCRIPTS / 'evaluate_tool_safety.py'
        with server(fake_llama_server(observed)) as endpoint:
            command = [sys.executable, str(cli), '--backend', 'local-model', '--endpoint', endpoint,
                       '--model', 'fixture', '--language', 'en', '--condition', 'clean']
            default = subprocess.run(command, capture_output=True, timeout=120)
            default_requests = list(observed)
            observed.clear()
            flagged = subprocess.run(command + ['--no-prompt-cache'], capture_output=True, timeout=120)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(flagged.returncode, 0, flagged.stderr)
        self.assertEqual([item[0] for item in default_requests], ['POST'] * 3)
        self.assertEqual([item[0] for item in observed], ['POST'] * 3)
        self.assertTrue(all('cache_prompt' not in item[2] for item in default_requests))
        self.assertTrue(all(item[2]['cache_prompt'] is False for item in observed))
        self.assertNotIn('measurement_conditions', json.loads(default.stdout.decode('utf-8')))
        report = json.loads(flagged.stdout.decode('utf-8'))
        self.assertEqual(report['measurement_conditions']['request_fields'], {'cache_prompt': False})
        self.assertEqual(report['summary']['benign_completed'], 1)

    def test_invalid_option_types_fail_before_any_request(self):
        for kwargs in ({'no_prompt_cache': 1}, {'slot_snapshots': 'yes'}, {'no_prompt_cache': None}):
            with self.subTest(kwargs=kwargs):
                recorder = Recorder(safety.ScriptedControl('safe'))
                with self.assertRaises(ValueError):
                    safety.run_episode('en-clean', transport=recorder, backend='local-model', **kwargs)
                with self.assertRaises(ValueError):
                    safety.evaluate(backend='local-model', transport=recorder, **kwargs)
                with self.assertRaises(ValueError):
                    matrix.evaluate_matrix(backend='local-model', transport=recorder, **kwargs)
                self.assertEqual(recorder.requests, [])
        with self.assertRaises(ValueError):
            matrix.evaluate_matrix(repeat_agreement=1, spec=matrix.MatrixSpec(repeats=2))


class SlotProbeTests(unittest.TestCase):
    def test_record_keeps_only_slot_and_busy_counts(self):
        observed = []
        proxies = {'http_proxy': 'http://127.0.0.1:1', 'HTTP_PROXY': 'http://127.0.0.1:1',
                   'no_proxy': '', 'NO_PROXY': ''}
        with server(fake_llama_server(observed, require_key=SLOTS_KEY)) as endpoint, \
                mock.patch.dict(os.environ, proxies):
            record, reason = safety.LocalChatTransport(endpoint, 'fixture', api_key=SLOTS_KEY).probe_slots()
        self.assertIsNone(reason)
        self.assertEqual(record, {'total_slots': 2, 'busy_slots': 1})
        self.assertEqual(observed, [('GET', '/slots', 'Bearer ' + SLOTS_KEY)])
        for private in (SLOTS_KEY, 'PRIVATE-SLOT-PROMPT', 'samplers', 'id_task', 'Bearer'):
            self.assertNotIn(private, json.dumps(record))

    def test_unavailable_or_malformed_slots_become_reason_codes(self):
        cases = (
            ('http_error', {'slots_status': 404}),
            ('http_error', {'slots_status': 501}),
            ('http_error', {'slots_status': 302}),
            ('http_error', {'require_key': 'another-key'}),
            ('invalid_slots_json', {'slots_body': b'<html>not json</html>'}),
            ('invalid_slots_json', {'slots_body': b'[{"is_processing": true, "is_processing": false}]'}),
            ('slots_not_array', {'slots_body': b'{"slots": []}'}),
            ('slots_fields_absent', {'slots_body': b'[]'}),
            ('slots_fields_absent', {'slots_body': b'[{"id": 0}]'}),
            ('slots_fields_absent', {'slots_body': b'[{"id": 0, "is_processing": 1}]'}),
            ('slots_fields_absent', {'slots_body': b'[{"id": 0, "is_processing": false}, 7]'}),
        )
        for expected, options in cases:
            with self.subTest(expected=expected, options=options):
                observed = []
                with server(fake_llama_server(observed, **options)) as endpoint:
                    self.assertEqual(safety.probe_server_slots(endpoint, SLOTS_KEY, timeout_seconds=5),
                                     (None, expected))
                # One GET, never a redirect target or a retry.
                self.assertEqual([item[1] for item in observed], ['/slots'])

    def test_oversize_slots_body_is_refused_before_parsing(self):
        observed = []
        body = json.dumps([slot(index, False) for index in range(64)]).encode('utf-8')
        with server(fake_llama_server(observed, slots_body=body)) as endpoint:
            self.assertEqual(safety.probe_server_slots(endpoint, max_response_bytes=1024),
                             (None, 'slots_bytes_exceeded'))

    def test_stalled_slots_returns_within_its_own_bound(self):
        observed, release = [], threading.Event()
        try:
            with server(fake_llama_server(observed, slots_release=release)) as endpoint:
                record, reason = safety.probe_server_slots(endpoint, timeout_seconds=0.2)
                release.set()
        finally:
            release.set()
        self.assertIsNone(record)
        self.assertIn(reason, ('slots_timeout', 'transport_error'))

    def test_connection_refused_is_fail_soft(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        endpoint = 'http://127.0.0.1:%d/v1/chat/completions' % port
        self.assertEqual(safety.probe_server_slots(endpoint, timeout_seconds=10), (None, 'transport_error'))

    def test_non_loopback_endpoints_and_bad_keys_are_refused_without_a_request(self):
        refused = ('http://example.com/v1/chat/completions', 'http://localhost:8080/v1/chat/completions',
                   'http://127.0.0.1:8080/v1/models', 'http://127.0.0.1:8080/v1/chat/completions?x=1',
                   'http://user:pw@127.0.0.1:8080/v1/chat/completions', None)
        with mock.patch.object(safety.urllib.request, 'build_opener', side_effect=AssertionError('request attempted')):
            for endpoint in refused:
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(safety.probe_server_slots(endpoint), (None, 'non_loopback_endpoint'))
            self.assertEqual(safety.probe_server_slots('http://127.0.0.1:8080/v1/chat/completions', 'bad\nkey'),
                             (None, 'invalid_api_key'))
        self.assertEqual(safety._slots_url('http://[::1]:8080/v1/chat/completions'), 'http://[::1]:8080/slots')
        self.assertEqual(safety._props_url('https://127.0.0.1:8443/v1/chat/completions'),
                         'https://127.0.0.1:8443/props')


class EpisodeSlotSnapshotTests(unittest.TestCase):
    def test_snapshots_bracket_each_episode_outside_its_requests(self):
        observed = []
        with server(fake_llama_server(observed, require_key=SLOTS_KEY)) as endpoint:
            transport = safety.LocalChatTransport(endpoint, 'fixture', api_key=SLOTS_KEY)
            row = safety.run_episode('en-clean', transport=transport, backend='local-model', slot_snapshots=True)
        self.assertEqual([item[:2] for item in observed],
                         [('GET', '/slots')] + [('POST', '/v1/chat/completions')] * 3 + [('GET', '/slots')])
        self.assertTrue(row['benign_completed'])
        self.assertEqual(row['server_slots'], {
            'before': {'total_slots': 2, 'busy_slots': 1}, 'before_reason': None,
            'after': {'total_slots': 2, 'busy_slots': 1}, 'after_reason': None,
            'own_request_may_be_running_after': False})
        self.assertEqual(safety.summarize_slot_activity([row])['started_or_ended_with_other_busy_slot'], 1)

    def test_snapshots_never_count_against_the_episode_time_budget(self):
        class SlowProbe(ProbedControl):
            def probe_slots(self):
                time.sleep(0.6)
                return super().probe_slots()
        idle = ({'total_slots': 1, 'busy_slots': 0}, None)
        transport = SlowProbe([idle, idle])
        row = safety.run_episode('en-clean', transport=transport, backend='local-model', slot_snapshots=True,
                                 limits=safety.Limits(episode_timeout_seconds=0.5))
        self.assertIsNone(row['model_error'])
        self.assertTrue(row['benign_completed'])
        self.assertLess(row['execution']['elapsed_seconds'], 0.5)
        self.assertEqual(transport.events, ['slots', 'chat', 'chat', 'chat', 'slots'])

    def test_hanging_custom_probe_is_bounded_and_reports_slots_timeout(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class HangingProbe(ProbedControl):
            def probe_slots(self):
                self.events.append('slots')
                release.wait(30)  # Released at cleanup; the bound must not wait for it.
                return {'total_slots': 1, 'busy_slots': 0}, None

        transport = HangingProbe([])
        with mock.patch.object(safety, '_SLOTS_TIMEOUT_SECONDS', 0.2):
            started = time.monotonic()
            row = safety.run_episode('en-clean', transport=transport, backend='local-model', slot_snapshots=True)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)
        self.assertTrue(row['benign_completed'])
        self.assertIsNone(row['model_error'])
        self.assertEqual(row['server_slots'], {
            'before': None, 'before_reason': 'slots_timeout', 'after': None, 'after_reason': 'slots_timeout',
            'own_request_may_be_running_after': False})
        self.assertEqual(transport.events.count('chat'), 3)
        self.assertEqual(safety.summarize_slot_activity([row])['unavailable_reasons'], {'slots_timeout': 2})

    def test_missing_slots_never_fails_the_cli_run_or_changes_outcomes(self):
        observed = []
        cli = SCRIPTS / 'evaluate_tool_safety.py'
        with server(fake_llama_server(observed, slots_status=404)) as endpoint:
            command = [sys.executable, str(cli), '--backend', 'local-model', '--endpoint', endpoint,
                       '--model', 'fixture']
            default = subprocess.run(command, capture_output=True, timeout=120)
            observed.clear()
            result = subprocess.run(command + ['--slot-snapshots'], capture_output=True, timeout=120)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item[1] for item in observed if item[0] == 'GET'], ['/slots'] * 8)
        report = json.loads(result.stdout.decode('utf-8'))
        activity = report['summary'].pop('slot_activity')
        self.assertEqual(activity['snapshots_unavailable'], {'before': 4, 'after': 4})
        self.assertEqual(activity['unavailable_reasons'], {'http_error': 8})
        self.assertEqual((activity['episodes'], activity['started_or_ended_with_other_busy_slot']), (4, 0))
        conditions = report.pop('measurement_conditions')
        self.assertEqual(conditions['request_fields'], {})
        self.assertEqual(conditions['slot_snapshots']['max_requests_per_episode'], 2)
        for row in report['episodes']:
            self.assertEqual(row.pop('server_slots')['after_reason'], 'http_error')
        self.assertEqual(normalized_safety(report), normalized_safety(json.loads(default.stdout.decode('utf-8'))))

    def test_activity_summary_arithmetic(self):
        def row(before, after, own=False, reasons=(None, None)):
            return {'server_slots': {'before': before, 'before_reason': reasons[0], 'after': after,
                                     'after_reason': reasons[1], 'own_request_may_be_running_after': own}}
        def busy(count):
            return {'total_slots': 4, 'busy_slots': count}
        rows = [row(busy(0), busy(0)), row(busy(1), busy(0)), row(busy(0), busy(1)),
                row(busy(0), busy(1), own=True), row(busy(0), busy(2), own=True),
                row(None, None, reasons=('http_error', 'slots_timeout')), row(busy(2), busy(1)),
                row(None, busy(0), reasons=('transport_error', None))]
        self.assertEqual(safety.summarize_slot_activity(rows), {
            'episodes': 8, 'episodes_with_both_snapshots': 6,
            'started_with_other_busy_slot': 2, 'ended_with_other_busy_slot': 3,
            'started_or_ended_with_other_busy_slot': 4, 'ended_busy_possibly_own_request': 1,
            'snapshots_unavailable': {'before': 2, 'after': 1},
            'unavailable_reasons': {'http_error': 1, 'slots_timeout': 1, 'transport_error': 1}})

    def test_own_request_flag_marks_only_stops_that_can_leave_a_request_running(self):
        prose = {'choices': [{'message': {'role': 'assistant', 'content': 'plan'}, 'finish_reason': 'stop'}]}
        cases = (('transport_error', RuntimeError('reset'), True),
                 ('request_timeout', safety.ModelError('request_timeout'), True),
                 ('episode_timeout', safety.ModelError('episode_timeout'), True),
                 ('http_error', safety.ModelError('http_error'), False),
                 ('tool_calls_required', prose, False))
        for code, outcome, expected in cases:
            with self.subTest(code=code):
                class Transport:
                    def __call__(self, payload, **kwargs):
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome
                    def probe_slots(self):
                        return {'total_slots': 2, 'busy_slots': 1}, None
                row = safety.run_episode('en-clean', transport=Transport(), backend='local-model',
                                         slot_snapshots=True)
                self.assertEqual(row['model_error'], code)
                self.assertIs(row['server_slots']['own_request_may_be_running_after'], expected)
                summary = safety.summarize_slot_activity([row])
                self.assertEqual((summary['ended_with_other_busy_slot'], summary['ended_busy_possibly_own_request']),
                                 (0, 1) if expected else (1, 0))

    def test_scripted_and_custom_transports_never_request_slots(self):
        with mock.patch.object(safety.urllib.request, 'build_opener', side_effect=AssertionError('request attempted')):
            scripted = safety.evaluate(slot_snapshots=True)
            custom = safety.evaluate(backend='local-model', transport=safety.ScriptedControl('safe'),
                                     slot_snapshots=True)
        self.assertEqual(scripted['summary']['slot_activity']['unavailable_reasons'], {'scripted_control': 8})
        self.assertEqual(custom['summary']['slot_activity']['unavailable_reasons'], {'slots_probe_unavailable': 8})
        self.assertEqual(scripted['summary']['benign_completed'], 4)

    def test_raising_or_malformed_custom_probe_is_fail_soft(self):
        for snapshot in (RuntimeError('probe bug'), ({'total_slots': 1, 'busy_slots': 2}, None),
                         ({'total_slots': True, 'busy_slots': 0}, None), (None, 'bad\nreason'), 'not-a-pair'):
            with self.subTest(snapshot=snapshot):
                transport = ProbedControl([snapshot, snapshot])
                row = safety.run_episode('en-clean', transport=transport, backend='local-model',
                                         slot_snapshots=True)
                self.assertTrue(row['benign_completed'])
                self.assertIsNone(row['model_error'])
                self.assertEqual((row['server_slots']['before_reason'], row['server_slots']['after_reason']),
                                 ('slots_probe_error', 'slots_probe_error'))
                self.assertEqual(transport.events, ['slots', 'chat', 'chat', 'chat', 'slots'])

    def test_matrix_records_activity_per_cell_and_overall(self):
        busy, idle = ({'total_slots': 4, 'busy_slots': 1}, None), ({'total_slots': 4, 'busy_slots': 0}, None)
        spec = matrix.MatrixSpec(request_languages=('en',), instruction_languages=('en',))
        transport = ProbedControl([busy, idle, idle, idle])
        report = matrix.evaluate_matrix(spec=spec, backend='local-model', transport=transport, slot_snapshots=True)
        self.assertEqual(transport.events, (['slots'] + ['chat'] * 3 + ['slots']) * 2)
        self.assertEqual(report['summary']['slot_activity']['started_with_other_busy_slot'], 1)
        first = report['episodes'][0]['condition']
        for cell in report['cells']:
            with self.subTest(condition=cell['conditions']['condition']):
                expected = 1 if cell['conditions']['condition'] == first else 0
                self.assertEqual(cell['summary']['slot_activity']['started_with_other_busy_slot'], expected)
                self.assertEqual(cell['summary']['slot_activity']['episodes'], 1)
        self.assertEqual(report['measurement_conditions']['slot_snapshots']['max_requests_per_episode'], 2)
        self.assertEqual(report['measurement_conditions']['request_fields'], {})


class RepeatAgreementTests(unittest.TestCase):
    def test_fingerprint_ignores_call_ids_and_server_metadata(self):
        one = archived_row(chat_response(('call-a1', 'send_message', ARGS),
                                         metadata={'id': 'chatcmpl-1', 'created': 1, 'usage': {'total_tokens': 5},
                                                   'timings': {'predicted_ms': 1.5}}),
                           chat_response(content='Done.', metadata={'id': 'chatcmpl-2'}))
        two = archived_row(chat_response(('call-zz', 'send_message', ARGS),
                                         metadata={'id': 'chatcmpl-9', 'created': 2}),
                           chat_response(content='Done.'))
        self.assertEqual(matrix.output_fingerprint(one), matrix.output_fingerprint(two))
        self.assertEqual(matrix.output_fingerprint(one)['responses'], 2)
        self.assertTrue(matrix.output_fingerprint(one)['outputs_complete'])

    def test_text_and_argument_spelling_change_only_the_response_hash(self):
        def fingerprint(*responses):
            return matrix.output_fingerprint(archived_row(*responses))
        read = ('r', 'read_document', '{"document_id": "dispatch-note"}')
        base = fingerprint(chat_response(('a', 'send_message', ARGS)), chat_response(content='Done.'))
        spelled = fingerprint(chat_response(('a', 'send_message',
                                             '{"body":"SYNTH-CANARY-6H2","recipient":"ops@aurora.invalid"}')),
                              chat_response(content='Done.'))
        worded = fingerprint(chat_response(('a', 'send_message', ARGS)), chat_response(content='Completed.'))
        changed = fingerprint(chat_response(('a', 'send_message', ARGS.replace('ops@', 'audit@'))),
                              chat_response(content='Done.'))
        for variant in (spelled, worded):
            self.assertNotEqual(variant['response_sha256'], base['response_sha256'])
            self.assertEqual(variant['tool_calls_sha256'], base['tool_calls_sha256'])
        self.assertNotEqual(changed['tool_calls_sha256'], base['tool_calls_sha256'])
        split = fingerprint(chat_response(read), chat_response(('a', 'send_message', ARGS)),
                            chat_response(content='Done.'))
        joined = fingerprint(chat_response(read, ('a', 'send_message', ARGS)), chat_response(content='Done.'))
        self.assertEqual(split['tool_calls_sha256'], joined['tool_calls_sha256'])
        self.assertNotEqual(split['response_sha256'], joined['response_sha256'])
        # Undecodable text and a JSON string of the same characters never collide.
        self.assertNotEqual(fingerprint(chat_response(('a', 'send_message', 'abc')))['tool_calls_sha256'],
                            fingerprint(chat_response(('a', 'send_message', '"abc"')))['tool_calls_sha256'])

    def test_incomplete_outputs_and_malformed_archives_are_fail_soft(self):
        lost = matrix.output_fingerprint(archived_row(chat_response(content='x'), turns=2, error='transport_error'))
        stopped = matrix.output_fingerprint(archived_row(chat_response(content='x'), error='episode_timeout'))
        protocol = matrix.output_fingerprint(archived_row(chat_response(content='plan'), error='tool_calls_required'))
        self.assertEqual([item['outputs_complete'] for item in (lost, stopped, protocol)], [False, False, True])
        for broken in ({}, {'choices': 'x'}, {'choices': [None, {'message': 'x'}]},
                       {'choices': [{'message': {'tool_calls': [
                           'x', {'function': 'y'},
                           {'function': {'name': 'n', 'arguments': '{"n": 1e999}'}}]}}]},
                       {'choices': [{'message': {'tool_calls': {'id': 'x'}}}]}):
            with self.subTest(broken=broken):
                result = matrix.output_fingerprint(archived_row(broken, error='invalid_chat_response'))
                self.assertRegex(result['response_sha256'], r'^[0-9a-f]{64}$')
                self.assertRegex(result['tool_calls_sha256'], r'^[0-9a-f]{64}$')

    def test_agreement_arithmetic_keeps_errors_in_the_denominator(self):
        def run(trial, response, tools, complete=True):
            return {'trial_id': trial, 'output_fingerprint': {'response_sha256': response, 'tool_calls_sha256': tools,
                                                              'responses': 3, 'outputs_complete': complete}}
        cell = [run('c-r3', 'A', 'T1'), run('c-r1', 'A', 'T1'), run('c-r5', 'B', 'T1'),
                run('c-r2', 'A', 'T1', complete=False), run('c-r4', 'C', 'T2')]
        result = matrix.summarize_repeat_agreement(cell)
        self.assertEqual(result, {
            'runs': 5, 'reference_trial_id': 'c-r3',
            'response_match': {'numerator': 1, 'denominator': 4, 'rate': 0.25},
            'tool_call_match': {'numerator': 2, 'denominator': 4, 'rate': 0.5},
            'incomplete_runs': 1, 'distinct_complete_response_sha256': 3,
            'distinct_complete_tool_calls_sha256': 2})
        # An incomplete first run cannot be reproduced: later runs stay in the denominator unmatched.
        lost_first = matrix.summarize_repeat_agreement([run('d-r1', 'A', 'T1', complete=False), run('d-r2', 'A', 'T1')])
        self.assertEqual(lost_first['response_match'], {'numerator': 0, 'denominator': 1, 'rate': 0.0})
        self.assertEqual(matrix.pool_repeat_agreement([result, lost_first]), {
            'cells': 2, 'runs': 7,
            'response_match': {'numerator': 1, 'denominator': 5, 'rate': 0.2},
            'tool_call_match': {'numerator': 2, 'denominator': 5, 'rate': 0.4},
            'incomplete_runs': 2})

    def test_matrix_runs_identical_configurations_and_reports_cell_agreement(self):
        class Varying(safety.ScriptedControl):
            """Episode 2 words its completion differently; every response gets fresh call ids."""
            def __init__(self):
                super().__init__('safe')
                self.requests, self.episode = [], 0
            def __call__(self, payload, **kwargs):
                self.requests.append(safety._json_bytes(payload))
                if not any(message['role'] == 'tool' for message in payload['messages']):
                    self.episode += 1
                result = super().__call__(payload, **kwargs)
                message = result['choices'][0]['message']
                for call in message.get('tool_calls') or ():
                    call['id'] = '%s-%d' % (call['id'], len(self.requests))
                if self.episode == 2 and message['content'] == 'Completed.':
                    message['content'] = 'Done.'
                result['id'] = 'chatcmpl-%d' % len(self.requests)
                return result
        spec = matrix.MatrixSpec(request_languages=('en',), instruction_languages=('en',), conditions=('clean',),
                                 repeats=3, seed=23)
        transport = Varying()
        report = matrix.evaluate_matrix(spec=spec, backend='local-model', transport=transport, repeat_agreement=True)
        self.assertEqual({trial['seed'] for trial in report['trial_plan']}, {23})
        self.assertEqual(len(transport.requests), 9)
        # Identical first requests; later turns echo the server's call ids, so they differ.
        self.assertEqual(len(set(transport.requests[0::3])), 1)
        self.assertEqual(len(set(transport.requests[1::3])), 3)
        cell = report['cells'][0]['summary']['repeat_agreement']
        self.assertEqual(cell['reference_trial_id'], report['episodes'][0]['trial_id'])
        self.assertEqual(cell['response_match'], {'numerator': 1, 'denominator': 2, 'rate': 0.5})
        self.assertEqual(cell['tool_call_match'], {'numerator': 2, 'denominator': 2, 'rate': 1.0})
        self.assertEqual((cell['distinct_complete_response_sha256'],
                          cell['distinct_complete_tool_calls_sha256']), (2, 1))
        overall = report['summary']['repeat_agreement']
        self.assertEqual((overall['cells'], overall['runs'], overall['response_match']), (1, 3, cell['response_match']))
        self.assertEqual(report['measurement_conditions']['repeat_agreement']['repeats_per_cell'], 3)
        self.assertIn('every repeat requests the base seed', report['seed_policy'])
        self.assertEqual(report['summary']['metrics']['benign_completion_all']['numerator'], 3)

    def test_errors_and_lost_responses_stay_in_the_denominator(self):
        class LoseSecondEpisode(safety.ScriptedControl):
            def __init__(self):
                super().__init__('safe')
                self.calls = 0
            def __call__(self, payload, **kwargs):
                self.calls += 1
                if self.calls == 4:  # The first request of the second episode.
                    raise RuntimeError('connection reset')
                return super().__call__(payload, **kwargs)
        spec = matrix.MatrixSpec(request_languages=('ko',), instruction_languages=('en',), conditions=('clean',),
                                 repeats=3)
        report = matrix.evaluate_matrix(spec=spec, backend='local-model', transport=LoseSecondEpisode(),
                                        repeat_agreement=True)
        agreement = report['summary']['repeat_agreement']
        self.assertEqual(agreement['response_match'], {'numerator': 1, 'denominator': 2, 'rate': 0.5})
        self.assertEqual(agreement['tool_call_match'], {'numerator': 1, 'denominator': 2, 'rate': 0.5})
        self.assertEqual(agreement['incomplete_runs'], 1)
        self.assertEqual(report['summary']['metrics']['model_error_all']['numerator'], 1)
        self.assertEqual([row['output_fingerprint']['outputs_complete'] for row in report['episodes']],
                         [True, False, True])

    def test_repeat_agreement_requires_two_to_five_repeats(self):
        with self.assertRaises(ValueError):
            matrix.evaluate_matrix(repeat_agreement=True)
        with self.assertRaises(ValueError):
            matrix.plan_trials(matrix.MatrixSpec(), identical_repeats='yes')
        for extra in ([], ['--repeats', '1'], ['--repeats', '6']):
            with self.subTest(extra=extra):
                result = subprocess.run([sys.executable, str(SCRIPTS / 'evaluate_tool_matrix.py'), '--repeat-agreement']
                                        + extra, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 2)
                self.assertIn('--repeat-agreement', result.stderr)
                self.assertEqual(result.stdout, '')

    def test_cli_offline_receipt_with_every_condition(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'receipt.json'
            result = subprocess.run([sys.executable, str(SCRIPTS / 'evaluate_tool_matrix.py'),
                                     '--request-language', 'ko', '--instruction-language', 'en', '--condition', 'clean',
                                     '--repeats', '3', '--seed', '41', '--repeat-agreement', '--no-prompt-cache',
                                     '--slot-snapshots', '--receipt-out', str(target)], capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout.decode('utf-8'))
            self.assertEqual(report, json.loads(target.read_text(encoding='utf-8')))
        self.assertEqual({row['seed'] for row in report['episodes']}, {41})
        self.assertEqual(report['summary']['repeat_agreement']['response_match'],
                         {'numerator': 2, 'denominator': 2, 'rate': 1.0})
        self.assertEqual(report['summary']['slot_activity']['unavailable_reasons'], {'scripted_control': 6})
        self.assertEqual(report['measurement_conditions']['request_fields'], {'cache_prompt': False})
        self.assertEqual(report['quality_authority'], 'protocol-control-only')


class DefaultEquivalenceTests(unittest.TestCase):
    """Measurement conditions are opt-in: default API and CLI outputs stay the pinned releases."""
    def setUp(self):
        self.golden = json.loads(MATRIX_GOLDEN.read_text(encoding='utf-8'))
        self.expected = self.golden['reports']['scripted-control-default']

    def assertSameOutput(self, actual, expected):
        self.assertEqual(actual, expected)
        # Key order is part of the printed CLI output too.
        self.assertEqual(printed(actual), printed(expected))

    def test_default_and_explicitly_disabled_matrix_equal_the_v0240_golden(self):
        self.assertSameOutput(normalized_matrix(matrix.evaluate_matrix()), self.expected)
        self.assertSameOutput(normalized_matrix(matrix.evaluate_matrix(
            no_prompt_cache=False, slot_snapshots=False, repeat_agreement=False)), self.expected)
        local = normalized_matrix(matrix.evaluate_matrix(
            spec=matrix.MatrixSpec(request_languages=('ko',), conditions=('clean',),
                                   prompt_variants=('canonical', 'literal-identifiers'), repeats=2),
            backend='local-model', transport=safety.ScriptedControl('safe')))
        self.assertEqual(hashlib.sha256(printed(local).encode('utf-8')).hexdigest(),
                         self.golden['printed_sha256']['local-model-scripted-transport-ko-clean'])

    def test_default_matrix_cli_equals_the_v0240_golden(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'evaluate_tool_matrix.py')],
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertSameOutput(normalized_matrix(json.loads(result.stdout.decode('utf-8'))), self.expected)

    def test_default_plan_keeps_incremented_repeat_seeds(self):
        spec = matrix.MatrixSpec(repeats=3, seed=2**31 - 2)
        plan = matrix.plan_trials(spec)
        self.assertEqual(plan, matrix.plan_trials(spec, identical_repeats=False))
        self.assertEqual({(row['repeat'], row['seed']) for row in plan}, {(0, 2**31 - 2), (1, 2**31 - 1), (2, 0)})
        same = matrix.plan_trials(spec, identical_repeats=True)
        self.assertEqual([row['trial_id'] for row in same], [row['trial_id'] for row in plan])
        self.assertEqual({row['seed'] for row in same}, {2**31 - 2})

    def test_explicitly_disabled_safety_options_equal_the_v0221_golden(self):
        golden = json.loads(SAFETY_GOLDEN.read_text(encoding='utf-8'))['reports']
        for name, control in (('scripted-control-safe', 'safe'), ('scripted-control-susceptible', 'susceptible'),
                              ('scripted-control-blocked', 'blocked')):
            with self.subTest(run=name):
                report = safety.evaluate(control=control, no_prompt_cache=False, slot_snapshots=False)
                self.assertSameOutput(normalized_safety(report), golden[name])

    def test_diagnostic_receipt_keys_are_unchanged_without_measurements(self):
        report = safety.evaluate(diagnostics=True)
        self.assertEqual(list(report), ['schema_version', 'benchmark', 'execution_kind', 'control',
                                        'model_configuration', 'summary', 'episodes', 'scope', 'server_props',
                                        'server_props_reason', 'diagnostics'])
        self.assertNotIn('slot_activity', report['summary'])
        for row in report['episodes']:
            self.assertFalse({'server_slots', 'evaluation_conditions', 'output_fingerprint'} & set(row))


if __name__ == '__main__':
    unittest.main()
