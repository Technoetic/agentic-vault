"""Synthetic, offline regression tests at the actual Jev/Jarvis boundaries."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
judge = importlib.import_module('vault_judge')
bridge = importlib.import_module('jarvis_bridge')


class EgressFixture:
    """Original fixture data, not company context or a live transport."""
    def __init__(self, root, *, revoked=True, bom=False):
        self.vault = Path(root)
        self.config = {'deny_zones': ['private'], 'exclude_dirs': [],
                       'hot_note': '00-meta/hot.md', 'handoff_note': '00-meta/handoff.md',
                       'log_note': '00-meta/log.md', 'hot_max_tokens': 1000,
                       'handoff_max_tokens': 1000,
                       'memory_revocation_file': '00-meta/revocations.json'}
        self.registry = {'schema_version': 1, 'nodes': [], 'derivations': [],
                         'revoked': ['source'] if revoked else []}
        for ident, relative in [('source', '20-knowledge/source.md'),
                                ('hot', self.config['hot_note']),
                                ('handoff', self.config['handoff_note']),
                                ('activity', self.config['log_note'])]:
            raw = ('# Synthetic context\r\nWITHDRAWN_' + ident +
                   '\r\nBENIGN_' + ident + '\r\n').encode('utf-8')
            if bom:
                raw = b'\xef\xbb\xbf' + raw
            self.write(relative, raw)
            self.registry['nodes'].append({'id': ident, 'path': relative,
                'start_line': 2, 'end_line': 2, 'sha256': hashlib.sha256(raw).hexdigest()})
            if ident != 'source':
                self.registry['derivations'].append({'parent': 'source', 'child': ident})
        self.save()

    def write(self, relative, value):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))
        return target

    def save(self):
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        self.write('00-meta/revocations.json', json.dumps(self.registry))

    def request(self, excerpt='BENIGN_source'):
        return {'schema_version': 1, 'task': 'support',
                'sources': [{'path': '20-knowledge/source.md', 'excerpt': excerpt}],
                'questions': [{'id': 'q', 'instructions': 'Is the selected fixture supported?',
                    'choices': {'yes': 'Supported', 'unknown': 'Insufficient'}, 'abstain': 'unknown'}]}


class RevocationEgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = EgressFixture(self.temp.name, bom=True)
        self.vault = self.fixture.vault

    def judge_run(self, excerpt='BENIGN_source', during=None):
        def answer(*_args, **_kwargs):
            if during:
                during()
            return {'answers': {'q': {'choice': 'yes', 'confidence': .99}}, 'usage': {}}
        with mock.patch.object(judge, 'request_judgments', side_effect=answer) as send:
            result = judge.run(self.vault, self.fixture.request(excerpt),
                               allow_network=True, api_key='synthetic-placeholder')
        return result, send

    def jarvis_run(self, before=None, during=None):
        def build(*_args):
            if before:
                before()
            return ['synthetic-never-launched']
        def answer(*_args, **_kwargs):
            if during:
                during()
            return subprocess.CompletedProcess([], 0, 'synthetic answer', '')
        with mock.patch.object(bridge, 'resolve_claude_executable', return_value='synthetic'), \
                mock.patch.object(bridge, 'build_claude_command', side_effect=build), \
                mock.patch.object(bridge, 'child_process_env', return_value={}), \
                mock.patch.object(bridge, 'run_with_stdin', side_effect=answer) as send:
            result = bridge.generate_claude(self.vault,
                {'claude_cmd': 'synthetic', 'qa_timeout_sec': 1, '_include_activity': True}, 'BENIGN')
        return result, send

    def mutate(self, kind):
        if kind == 'registry':
            self.fixture.write('00-meta/revocations.json', '{}')
        elif kind == 'config':
            changed = {**self.fixture.config, 'exclude_dirs': ['20-knowledge']}
            self.fixture.write('00-meta/vault-config.json', json.dumps(changed))
        elif kind == 'source':
            self.fixture.write('20-knowledge/source.md', '# Changed\nBENIGN_source\n')
        elif kind == 'direct':
            self.fixture.write('00-meta/hot.md', '# Changed\nBENIGN_hot\n')

    def test_jev_revoked_excerpt_never_calls_transport(self):
        result, send = self.judge_run('WITHDRAWN_source')
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error'], 'excerpt_not_found')
        send.assert_not_called()

    def test_jev_bom_crlf_projection_keeps_benign_and_original_raw_hash(self):
        result, send = self.judge_run()
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(result['sources'][0]['sha256'], self.fixture.registry['nodes'][0]['sha256'])
        self.assertEqual(result['sources'][0]['line_start'], 3)
        self.assertEqual(send.call_args.args[0], {'evidence': ['BENIGN_source']})

    def test_jev_changes_between_prepare_and_transport_fail_closed(self):
        original = judge._prepare
        for kind in ('registry', 'config', 'source'):
            with self.subTest(kind=kind):
                self.fixture = EgressFixture(self.vault, bom=True)
                def prepare(*args, **kwargs):
                    prepared = original(*args, **kwargs)
                    self.mutate(kind)
                    return prepared
                with mock.patch.object(judge, '_prepare', side_effect=prepare):
                    result, send = self.judge_run()
                self.assertEqual(result['status'], 'unverified')
                send.assert_not_called()

    def test_jev_changes_during_transport_withhold_answer(self):
        for kind in ('registry', 'config', 'source'):
            with self.subTest(kind=kind):
                self.fixture = EgressFixture(self.vault, bom=True)
                result, send = self.judge_run(during=lambda: self.mutate(kind))
                self.assertEqual(send.call_count, 1)
                self.assertEqual(result['status'], 'unverified')
                self.assertNotIn('answers', result)

    def test_jarvis_direct_hot_handoff_activity_declared_children_are_masked(self):
        result, send = self.jarvis_run()
        self.assertTrue(result.ok, result.text)
        payload = send.call_args.kwargs['input']
        self.assertNotIn('WITHDRAWN_', payload)
        for ident in ('hot', 'handoff', 'activity'):
            self.assertIn('BENIGN_' + ident, payload)

    def test_jarvis_filters_complete_source_before_prefix(self):
        raw = b'\xef\xbb\xbf# Synthetic\r\nWITHDRAWN_hot\r\nBENIGN_hot\r\n' + b'z' * 80000
        self.fixture.write('00-meta/hot.md', raw)
        self.fixture.registry['nodes'][1]['sha256'] = hashlib.sha256(raw).hexdigest()
        self.fixture.save()
        result, send = self.jarvis_run()
        self.assertTrue(result.ok, result.text)
        self.assertNotIn('WITHDRAWN_', send.call_args.kwargs['input'])
        self.assertIn('BENIGN_hot', send.call_args.kwargs['input'])
        self.assertIn('context_truncated', json.loads(send.call_args.kwargs['input'])['retrieval']['omissions'])

    def test_jarvis_changes_between_prepare_and_dispatch_refuse_child(self):
        for kind in ('registry', 'config', 'source', 'direct'):
            with self.subTest(kind=kind):
                self.fixture = EgressFixture(self.vault, bom=True)
                result, send = self.jarvis_run(before=lambda: self.mutate(kind))
                self.assertFalse(result.ok)
                send.assert_not_called()

    def test_jarvis_changes_during_child_withhold_response(self):
        for kind in ('registry', 'config', 'source', 'direct'):
            with self.subTest(kind=kind):
                self.fixture = EgressFixture(self.vault, bom=True)
                result, send = self.jarvis_run(during=lambda: self.mutate(kind))
                self.assertEqual(send.call_count, 1)
                self.assertFalse(result.ok)
                self.assertNotIn('synthetic answer', result.text)

    def test_jarvis_policy_captured_before_recall_cannot_be_replaced_after_recall(self):
        recall_module = importlib.import_module('vault_recall')
        original = recall_module.recall
        def recall_then_disable(*args, **kwargs):
            result = original(*args, **kwargs)
            self.fixture.config.pop('memory_revocation_file')
            self.fixture.save()
            return result
        with mock.patch.object(recall_module, 'recall', side_effect=recall_then_disable):
            result, send = self.jarvis_run()
        self.assertFalse(result.ok)
        self.assertEqual(send.call_count, 0)

    def test_jarvis_disabled_to_enabled_before_dispatch_refuses_child(self):
        for disabled in ('absent', 'empty'):
            with self.subTest(disabled=disabled):
                self.fixture = EgressFixture(self.vault, bom=True)
                if disabled == 'absent':
                    self.fixture.config.pop('memory_revocation_file')
                else:
                    self.fixture.config['memory_revocation_file'] = ''
                self.fixture.save()
                def enable():
                    self.fixture.config['memory_revocation_file'] = '00-meta/revocations.json'
                    self.fixture.save()
                result, send = self.jarvis_run(before=enable)
                self.assertFalse(result.ok)
                self.assertEqual(send.call_count, 0)

    def test_jarvis_disabled_to_enabled_during_dispatch_withholds_response(self):
        self.fixture.config.pop('memory_revocation_file')
        self.fixture.save()
        def enable():
            self.fixture.config['memory_revocation_file'] = '00-meta/revocations.json'
            self.fixture.save()
        result, send = self.jarvis_run(during=enable)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(result.ok)
        self.assertNotIn('synthetic answer', result.text)

    def test_jarvis_not_vault_to_vault_before_dispatch_refuses_child(self):
        (self.vault / '00-meta/vault-config.json').unlink()
        result, send = self.jarvis_run(before=self.fixture.save)
        self.assertFalse(result.ok)
        self.assertEqual(send.call_count, 0)

    def test_jarvis_not_vault_to_vault_during_dispatch_withholds_response(self):
        (self.vault / '00-meta/vault-config.json').unlink()
        result, send = self.jarvis_run(during=self.fixture.save)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(result.ok)
        self.assertNotIn('synthetic answer', result.text)

    def test_configured_malformed_or_stale_sources_never_use_unfiltered_fallback(self):
        for kind in ('registry', 'direct'):
            with self.subTest(kind=kind):
                self.fixture = EgressFixture(self.vault, bom=True)
                self.mutate(kind)
                result, send = self.jarvis_run()
                self.assertFalse(result.ok)
                send.assert_not_called()
                self.assertNotIn('WITHDRAWN_', result.text)

    def test_disabled_configuration_preserves_literal_default_prompt(self):
        del self.fixture.config['memory_revocation_file']
        self.fixture.save()
        # A malformed, unconfigured registry must have no effect.
        self.fixture.write('00-meta/revocations.json', '{}')
        expected = bridge._generation_prompt(self.vault, {'_include_activity': True}, 'BENIGN')
        result, send = self.jarvis_run()
        self.assertTrue(result.ok)
        self.assertEqual(send.call_args.kwargs['input'], expected)
        self.assertIn('WITHDRAWN_hot', expected)
        result, send = self.judge_run('WITHDRAWN_source')
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(send.call_args.args[0]['evidence'], ['WITHDRAWN_source'])


class LifecycleEvaluationTests(unittest.TestCase):
    def evaluator(self):
        spec = importlib.util.spec_from_file_location('revocation_egress_eval', ROOT / 'scripts/evaluate_revocation_egress.py')
        self.assertTrue(spec.origin and Path(spec.origin).exists(), 'lifecycle evaluator is missing')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_or_invalid_observations_are_not_safe_useful_success(self):
        evaluate = self.evaluator()
        self.assertFalse(evaluate.assess_observations([])['safe_useful'])
        self.assertFalse(evaluate.assess_observations([{'outcome': 'unavailable'}])['safe_useful'])
        unknown = {'outcome': 'observed', 'consumer': 'unknown_reader',
                   'poison_visible': False, 'benign_visible': True, 'output_sha256': 'a' * 64}
        self.assertFalse(evaluate.assess_observations([unknown])['safe_useful'])
        partial = {**unknown, 'consumer': 'vault_recall.recall'}
        self.assertFalse(evaluate.assess_observations([partial])['safe_useful'])

    def test_actual_consumer_lifecycle_keeps_benign_withdraws_declared_children(self):
        report = self.evaluator().evaluate_revocation_egress()
        self.assertEqual(report['status'], 'passed', report)
        self.assertTrue(report['assessment']['safe_useful'])
        self.assertGreater(report['assessment']['observed_count'], 0)
        self.assertTrue(report['stale_envelope_refused'])
        self.assertTrue(report['previous_simulated_send_retained'])
        self.assertTrue(report['undeclared_copy_visible_outside_scope'])

    def test_unfiltered_negative_control_is_detected(self):
        report = self.evaluator().evaluate_revocation_egress(negative_control=True)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['assessment']['safe_useful'])
        self.assertGreater(report['assessment']['residual_count'], 0)


if __name__ == '__main__':
    unittest.main()
