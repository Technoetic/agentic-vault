from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    operations = importlib.import_module('vault_operations')
except ModuleNotFoundError:
    operations = None


OPERATION_CHILD = """
import json, pathlib, re, sys, time
sys.path.insert(0, sys.argv[1]); import vault_operations as op
ready, release, effect = map(pathlib.Path, sys.argv[3:6])
ready.write_text('ready')
deadline = time.monotonic() + 10
while not release.exists():
    if time.monotonic() > deadline: raise RuntimeError('barrier timeout')
    time.sleep(.01)
try:
    result = op.begin(sys.argv[2], 'one-intent', 'fixed-action', {'count': 1}, wait_seconds=2)
except op.OperationError as error:
    cause = error.__cause__
    if (str(error) == 'operation_unavailable'
            and isinstance(cause, op.StateError) and str(cause) == 'lock_busy'):
        print(json.dumps({'ok': False, 'error': 'lock_busy'}))
    else:
        chain = []
        while error is not None:
            code = str(error) if re.fullmatch(r'[a-z_]{1,80}', str(error)) else None
            chain.append({'type': type(error).__name__, 'code': code,
                          'winerror': getattr(error, 'winerror', None)})
            error = error.__cause__
        print(json.dumps({'unexpected_error': chain})); sys.exit(2)
else:
    if result['disposition'] == 'claimed':
        with effect.open('a') as output: output.write('effect\\n')
    print(json.dumps({'ok': True, 'result': result}))
"""


class VaultOperationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(operations, 'scoped operation receipts implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write('00-meta/vault-config.json', json.dumps({
            'deny_zones': ['private'], 'exclude_dirs': ['excluded'],
            'handoff_note': '', 'hot_note': '', 'log_note': '',
        }))

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return path

    def begin(self, **kwargs):
        return operations.begin(self.vault, 'intent-001', 'send-message',
                                {'count': 1, 'nested': {'b': 2, 'a': 1}}, **kwargs)

    def receipt(self, operation_id):
        return self.vault / operations.RUNTIME_DIR / ('operation-' + operation_id + '.json')

    def run_operation_children(self, *, count=2):
        release = Path(self.tmp.name) / 'release'
        effect = Path(self.tmp.name) / 'effects'
        ready = [Path(self.tmp.name) / ('ready-' + str(i)) for i in range(count)]
        children = [subprocess.Popen([sys.executable, '-X', 'utf8', '-c', OPERATION_CHILD,
                    str(SCRIPTS), str(self.vault), str(marker), str(release), str(effect)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
                    for marker in ready]
        def cleanup():
            for child in children:
                if child.poll() is None:
                    child.kill()
            for child in children:
                child.communicate(timeout=5)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 10
        while not all(p.exists() for p in ready):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        release.write_text('go')
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            self.assertEqual(stderr, '')
            results.append(json.loads(stdout))
        return results, effect

    def cli(self, *args):
        return subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'vault_operations.py'),
                               '--vault', str(self.vault), *args], capture_output=True,
                              text=True, encoding='utf-8', timeout=15)

    def test_claim_is_durable_before_caller_effect_and_pending_blocks(self):
        first = self.begin()
        self.assertEqual(first['disposition'], 'claimed')
        self.assertRegex(first['owner_token'], r'^[a-f0-9]{32}$')
        disk = json.loads(self.receipt(first['id']).read_text())
        self.assertEqual(disk['state'], 'pending')
        self.assertEqual(disk['owner_token'], first['owner_token'])
        second = self.begin()
        self.assertEqual(second['disposition'], 'blocked')
        self.assertNotIn('owner_token', second)
        self.assertEqual(second['receipt_sha256'], first['receipt_sha256'])

    def test_order_insensitive_parameters_same_scope_and_completed_reuse(self):
        first = self.begin()
        equivalent = {'nested': {'a': 1, 'b': 2}, 'count': 1}
        second = operations.begin(self.vault, 'intent-001', 'send-message', equivalent)
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(second['parameters_sha256'], first['parameters_sha256'])
        done = operations.finish(self.vault, first['id'], first['owner_token'],
                                 {'http_status': 202, 'effect_count': 1})
        self.assertEqual(done['state'], 'succeeded')
        third = operations.begin(self.vault, 'intent-001', 'send-message', equivalent)
        self.assertEqual(third['disposition'], 'reused')
        self.assertEqual(third['result'], {'http_status': 202, 'effect_count': 1})
        self.assertNotIn('owner_token', third)

    def test_same_intent_changed_parameters_conflicts_but_fresh_intent_can_claim(self):
        first = self.begin()
        with self.assertRaisesRegex(operations.OperationError, '^intent_conflict$'):
            operations.begin(self.vault, 'intent-001', 'send-message', {'count': 2})
        fresh = operations.begin(self.vault, 'intent-002', 'send-message', {'count': 2})
        self.assertEqual(fresh['disposition'], 'claimed')
        self.assertNotEqual(fresh['id'], first['id'])

    def test_expired_claim_becomes_uncertain_and_old_owner_cannot_finish(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with mock.patch.object(operations, '_now', return_value=now.isoformat()), \
             mock.patch.object(operations, 'time') as clock:
            clock.time.return_value = now.timestamp()
            first = self.begin(ttl_seconds=.2)
            clock.time.return_value = now.timestamp() + .25
            second = self.begin()
            self.assertEqual(second['state'], 'uncertain')
            self.assertEqual(second['disposition'], 'blocked')
            self.assertNotIn('owner_token', second)
            with self.assertRaisesRegex(operations.OperationError, '^owner_fence_changed$'):
                operations.finish(self.vault, first['id'], first['owner_token'], {})
            self.assertEqual(operations.inspect(self.vault, first['id'])['state'], 'uncertain')

    def test_finish_expiry_persists_uncertain_before_reporting_failure(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with mock.patch.object(operations, '_now', return_value=now.isoformat()), \
             mock.patch.object(operations, 'time') as clock:
            clock.time.return_value = now.timestamp()
            first = self.begin(ttl_seconds=.2)
            clock.time.return_value = now.timestamp() + .25
            with self.assertRaisesRegex(operations.OperationError, '^operation_uncertain$'):
                operations.finish(self.vault, first['id'], first['owner_token'], {})
            self.assertEqual(operations.inspect(self.vault, first['id'])['state'], 'uncertain')
            self.assertEqual(self.begin()['disposition'], 'blocked')

    def test_unknown_outcome_stays_blocked_without_expiry(self):
        first = self.begin()
        unknown = operations.mark_uncertain(self.vault, first['id'], first['owner_token'])
        self.assertEqual(unknown['state'], 'uncertain')
        self.assertEqual(self.begin()['disposition'], 'blocked')
        with self.assertRaisesRegex(operations.OperationError, '^owner_fence_changed$'):
            operations.finish(self.vault, first['id'], first['owner_token'], {})

    def test_reconciliation_closes_without_authorizing_replay(self):
        first = self.begin()
        unknown = operations.mark_uncertain(self.vault, first['id'], first['owner_token'])
        with self.assertRaisesRegex(operations.OperationError, '^stale_receipt$'):
            operations.reconcile(self.vault, first['id'], first['receipt_sha256'],
                                 outcome='not-executed')
        closed = operations.reconcile(self.vault, first['id'], unknown['receipt_sha256'],
                                      outcome='not-executed')
        self.assertEqual(closed['state'], 'not-executed')
        replay = self.begin()
        self.assertEqual(replay['disposition'], 'closed')
        self.assertNotIn('owner_token', replay)
        with self.assertRaises(operations.OperationError):
            operations.reconcile(self.vault, first['id'], closed['receipt_sha256'],
                                 outcome='succeeded', result={})

    def test_reconciled_success_is_reusable_with_metadata_only(self):
        first = self.begin()
        unknown = operations.mark_uncertain(self.vault, first['id'], first['owner_token'])
        done = operations.reconcile(self.vault, first['id'], unknown['receipt_sha256'],
                                    outcome='succeeded', result={'remote_id_sha256': 'f' * 64})
        self.assertEqual(done['state'], 'succeeded')
        self.assertEqual(self.begin()['disposition'], 'reused')

    def test_parameters_and_names_are_not_stored_and_secret_labels_rejected(self):
        sensitive_plain = 'caller-private-context-without-secret-shape'
        first = operations.begin(self.vault, 'intent-private-label', 'fixed-action',
                                 {'body': sensitive_plain})
        raw = self.receipt(first['id']).read_text()
        self.assertNotIn(sensitive_plain, raw)
        self.assertNotIn('intent-private-label', raw)
        self.assertNotIn('fixed-action', raw)
        for secret in ({'api_key': 'tiny'}, {'password': 'hidden-value'},
                       {'payload': 'Bearer sk-' + 'x' * 24},
                       {'authorization': 'Bearer hidden-value'}):
            with self.subTest(secret_field=next(iter(secret))):
                with self.assertRaisesRegex(operations.OperationError, '^sensitive_input$'):
                    operations.begin(self.vault, 'different-intent', 'fixed-action', secret)

    def test_result_rejects_raw_reply_secret_unknown_fields_and_bad_numbers(self):
        first = self.begin()
        for result in ({'reply': 'remote raw response'}, {'api_key': 'value'},
                       {'http_status': 200, 'body': 'raw'}, {'effect_count': -1},
                       {'effect_count': True}, {'response_bytes': float('nan')},
                       {'evidence_sha256': 'nope'}, {'http_status': 999}):
            with self.subTest(result=result):
                with self.assertRaises(operations.OperationError):
                    operations.finish(self.vault, first['id'], first['owner_token'], result)
        self.assertEqual(operations.inspect(self.vault, first['id'])['state'], 'pending')

    def test_json_bounds_nonfinite_deep_oversize_cycles_and_duplicate_keys(self):
        deep = {}
        for _ in range(20):
            deep = {'a': deep}
        cycle = {}; cycle['a'] = cycle
        for params in ({'x': float('inf')}, {'x': 'a' * 40000}, deep, cycle,
                       {'x': object()}, {1: 'not-string'}, {'x': 1 << 1000}):
            with self.subTest(kind=type(params).__name__):
                with self.assertRaises(operations.OperationError):
                    operations.begin(self.vault, 'intent-001', 'fixed-action', params)
        for raw in ('{"intent":"i","intent":"j","action":"a","parameters":{}}',
                    '{"intent":"i","action":"a","parameters":{"n":NaN}}',
                    '{"intent":"i","action":"a","parameters":{"n":1e9999}}'):
            self.write('notes/request.json', raw)
            run = self.cli('begin', '--input', 'notes/request.json')
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(run.stdout, '')
            self.assertNotIn(raw, run.stderr)

    def test_cli_full_lifecycle_and_redacted_failure(self):
        self.write('notes/begin.json', json.dumps({'intent': 'cli-001', 'action': 'fixed-action',
                                                  'parameters': {'count': 1}}))
        run = self.cli('begin', '--input', 'notes/begin.json')
        self.assertEqual(run.returncode, 0, run.stderr)
        first = json.loads(run.stdout)
        self.write('notes/finish.json', json.dumps({'id': first['id'],
                    'owner_token': first['owner_token'], 'result': {'effect_count': 1}}))
        done = self.cli('finish', '--input', 'notes/finish.json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['state'], 'succeeded')
        inspection = self.cli('inspect', '--id', first['id'])
        self.assertEqual(inspection.returncode, 0, inspection.stderr)
        self.assertNotIn(first['owner_token'], inspection.stdout)
        self.write('notes/bad.json', '{"intent":"api_key=supersecretvalue","action":"x","parameters":{}}')
        bad = self.cli('begin', '--input', 'notes/bad.json')
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn('supersecretvalue', bad.stderr + bad.stdout)

    def test_argparse_failures_do_not_echo_unknown_or_invalid_arguments(self):
        sentinel = 'sk-' + 'fakesecretvalue' * 3
        for args in (('begin', '--input', 'notes/input.json', '--unknown', sentinel),
                     ('inspect', '--id', 'f' * 64, '--ttl-seconds', sentinel),
                     ('unrecognized-' + sentinel,)):
            with self.subTest(args=args[0]):
                run = self.cli(*args)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, '')
                self.assertNotIn(sentinel, run.stderr)
                self.assertEqual(run.stderr.strip(), '{"error":"invalid_arguments"}')

    def test_invalid_ttl_input_does_not_echo_sensitive_value(self):
        sentinel = 'sk-' + 'fakesecretvalue' * 3
        self.write('notes/input.json', json.dumps({'intent': 'new-intent', 'action': 'x',
                    'parameters': {}, 'ttl_seconds': sentinel}))
        run = self.cli('begin', '--input', 'notes/input.json')
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn(sentinel, run.stdout + run.stderr)

    def test_result_nonstring_keys_and_extreme_ttl_raise_content_free_api_error(self):
        first = self.begin()
        with self.assertRaises(operations.OperationError):
            operations.finish(self.vault, first['id'], first['owner_token'], {1: 2})
        for kwargs in ({'ttl_seconds': 1 << 10000}, {'wait_seconds': 1 << 10000}):
            with self.assertRaises(operations.OperationError):
                self.begin(**kwargs)

    def test_config_drift_blocks_finish_reuse_and_reports_inspection_stale(self):
        first = self.begin()
        config = self.vault / '00-meta/vault-config.json'
        config.write_text(config.read_text() + '\n')
        with self.assertRaisesRegex(operations.OperationError, '^configuration_changed$'):
            operations.finish(self.vault, first['id'], first['owner_token'], {})
        with self.assertRaisesRegex(operations.OperationError, '^configuration_changed$'):
            self.begin()
        self.assertFalse(operations.inspect(self.vault, first['id'])['current'])

    def test_invalid_receipt_is_not_reclaimed(self):
        first = self.begin()
        path = self.receipt(first['id'])
        raw = json.loads(path.read_text())
        raw['state'] = 'invalid'
        path.write_text(json.dumps(raw))
        with self.assertRaises(operations.OperationError):
            self.begin()
        self.assertEqual(json.loads(path.read_text())['state'], 'invalid')

    def test_denied_excluded_reserved_and_linked_inputs_fail_before_read(self):
        raw = json.dumps({'intent': 'cli-001', 'action': 'x', 'parameters': {}})
        for relative in ('private/request.json', 'excluded/request.json',
                         operations.RUNTIME_DIR + '/request.json'):
            self.write(relative, raw)
            with mock.patch.object(operations, 'stable_read', wraps=operations.stable_read) as reader:
                with self.assertRaises(operations.OperationError):
                    operations.load_input(self.vault, relative)
                self.assertFalse(any(str(relative) in str(call) for call in reader.call_args_list))
        source = self.write('notes/input.json', raw)
        os.link(source, self.vault / 'notes/linked.json')
        with self.assertRaises(operations.OperationError):
            operations.load_input(self.vault, 'notes/input.json')

    def test_runtime_hardlink_prevents_replacement(self):
        first = self.begin()
        path = self.receipt(first['id'])
        copy = self.vault / 'receipt-copy.json'
        os.link(path, copy)
        before = path.read_bytes()
        with self.assertRaises(operations.OperationError):
            operations.finish(self.vault, first['id'], first['owner_token'], {})
        self.assertEqual(copy.read_bytes(), before)

    def test_runtime_symlink_or_junction_rejected(self):
        parent = self.vault / '00-meta/.agentic-vault'
        parent.mkdir(parents=True)
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        link = parent / 'runtime'
        if os.name == 'nt':
            run = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                 capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 0)
        else:
            link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(operations.OperationError):
            self.begin()
        self.assertEqual(list(outside.iterdir()), [])

    def test_two_processes_allow_bounded_refusal_and_at_most_one_caller_effect(self):
        results, effect = self.run_operation_children()
        observed = [result['result'] for result in results if result['ok']]
        claims = [result for result in observed if result['disposition'] == 'claimed']
        self.assertLessEqual(len(claims), 1, results)
        self.assertEqual(effect.read_text().splitlines() if effect.exists() else [],
                         ['effect'] * len(claims))
        for result in results:
            if not result['ok']:
                self.assertEqual(result, {'ok': False, 'error': 'lock_busy'})
                self.assertNotIn('owner_token', result)
            elif result['result']['disposition'] != 'claimed':
                self.assertEqual(result['result']['disposition'], 'blocked')
                self.assertNotIn('owner_token', result['result'])
        receipts = list((self.vault / operations.RUNTIME_DIR).glob('operation-*.json'))
        self.assertLessEqual(len(receipts), 1)
        if observed:
            self.assertEqual(len(receipts), 1)
            self.assertEqual(len({result['id'] for result in observed}), 1)
        if receipts:
            stored = json.loads(receipts[0].read_text())
            self.assertEqual(receipts[0], self.receipt(stored['id']))
            self.assertEqual(stored['state'], 'pending')
            self.assertEqual([event['event'] for event in stored['events']], ['claimed'])
            self.assertEqual(stored['intent_sha256'], hashlib.sha256(b'"one-intent"').hexdigest())
            self.assertEqual(stored['action_sha256'], hashlib.sha256(b'"fixed-action"').hexdigest())
            self.assertEqual(stored['parameters_sha256'],
                             hashlib.sha256(b'{"count":1}').hexdigest())
            for result in observed:
                self.assertEqual(result['id'], stored['id'])
            if claims:
                self.assertEqual(claims[0]['owner_token'], stored['owner_token'])

    def test_held_operation_lease_refuses_without_effect_then_reuses_pending_receipt(self):
        first = operations.begin(self.vault, 'one-intent', 'fixed-action', {'count': 1})
        path = self.receipt(first['id'])
        original = path.read_bytes()
        with operations.advisory_lock(self.vault, 'operation:' + first['id'], wait_seconds=1):
            results, effect = self.run_operation_children(count=1)
            self.assertEqual(results, [{'ok': False, 'error': 'lock_busy'}])
            self.assertFalse(effect.exists())
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(path.parent.glob('operation-*.json'))), 1)
        retry = operations.begin(self.vault, 'one-intent', 'fixed-action', {'count': 1})
        self.assertEqual(retry['disposition'], 'blocked')
        self.assertEqual(retry['id'], first['id'])
        self.assertNotIn('owner_token', retry)
        self.assertEqual(path.read_bytes(), original)

    def test_subprocess_crash_after_effect_before_finish_never_reexecutes(self):
        effect = Path(self.tmp.name) / 'effect'
        script = """
import pathlib, sys, os
from datetime import datetime, timezone
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1]); import vault_operations as op
now = datetime(2026, 1, 1, tzinfo=timezone.utc)
# Hold only the receipt clock while real claim writes and locks run.
op._now = lambda: now.isoformat()
op.time = SimpleNamespace(time=lambda: now.timestamp())
claim = op.begin(sys.argv[2], 'intent-001', 'send-message',
                 {'count': 1, 'nested': {'b': 2, 'a': 1}}, ttl_seconds=.08)
if claim['disposition'] == 'claimed': pathlib.Path(sys.argv[3]).write_text('remote effect')
os._exit(23)
"""
        child = subprocess.run([sys.executable, '-c', script, str(SCRIPTS), str(self.vault),
                                str(effect)], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 23)
        self.assertEqual(effect.read_text(), 'remote effect')
        expired = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=.1)
        with mock.patch.object(operations, '_now', return_value=expired.isoformat()), \
             mock.patch.object(operations, 'time') as clock:
            clock.time.return_value = expired.timestamp()
            retry = self.begin()
            self.assertEqual(retry['disposition'], 'blocked')
            self.assertEqual(retry['state'], 'uncertain')
            self.assertNotIn('owner_token', retry)
        self.assertEqual(effect.read_text(), 'remote effect')

    def test_policy_change_at_final_replace_boundary_preserves_pending_receipt(self):
        first = self.begin()
        path = self.receipt(first['id'])
        before = path.read_bytes()
        original = operations._atomic
        def mutate(path, data, before_replace, **kwargs):
            config = self.vault / '00-meta/vault-config.json'
            config.write_text(config.read_text() + '\n')
            return original(path, data, before_replace, **kwargs)
        with mock.patch.object(operations, '_atomic', side_effect=mutate):
            with self.assertRaises(operations.OperationError):
                operations.finish(self.vault, first['id'], first['owner_token'], {})
        self.assertEqual(path.read_bytes(), before)

    def test_owner_expiry_during_finish_write_cannot_commit_success(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Expire the owner during staging, independently of runner scheduling.
        # Replace this module's clock only; advisory-lock clocks remain real.
        with mock.patch.object(operations, '_now', return_value=now.isoformat()), \
             mock.patch.object(operations, 'time') as clock:
            clock.time.return_value = now.timestamp()
            first = self.begin(ttl_seconds=.2)
            before = self.receipt(first['id']).read_bytes()
            original = operations._atomic
            def expire(path, data, before_replace, **kwargs):
                clock.time.return_value = now.timestamp() + .25
                return original(path, data, before_replace, **kwargs)
            with mock.patch.object(operations, '_atomic', side_effect=expire):
                with self.assertRaisesRegex(operations.OperationError, '^operation_uncertain$'):
                    operations.finish(self.vault, first['id'], first['owner_token'], {})
            self.assertEqual(self.receipt(first['id']).read_bytes(), before)
            replay = self.begin()
            self.assertEqual(replay['state'], 'uncertain')
            self.assertEqual(replay['disposition'], 'blocked')

    def test_cli_mark_uncertain_and_reconcile_never_returns_owner(self):
        first = self.begin()
        self.write('notes/uncertain.json', json.dumps({'id': first['id'],
                   'owner_token': first['owner_token']}))
        unknown = self.cli('mark-uncertain', '--input', 'notes/uncertain.json')
        self.assertEqual(unknown.returncode, 0, unknown.stderr)
        view = json.loads(unknown.stdout)
        self.assertNotIn('owner_token', view)
        self.write('notes/reconcile.json', json.dumps({'id': first['id'],
                   'expected_sha256': view['receipt_sha256'], 'outcome': 'not-executed'}))
        closed = self.cli('reconcile', '--input', 'notes/reconcile.json')
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(json.loads(closed.stdout)['state'], 'not-executed')
        self.assertNotIn('owner_token', json.loads(closed.stdout))


if __name__ == '__main__':
    unittest.main()
