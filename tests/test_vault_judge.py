from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    judge = importlib.import_module('vault_judge') if (SCRIPTS / 'vault_judge.py').exists() else None
finally:
    sys.path.remove(str(SCRIPTS))


class VaultJudgeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(judge, 'vault judge implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        (self.vault / '00-meta').mkdir()
        self.config = self.vault / '00-meta/vault-config.json'
        self.config.write_text(json.dumps({'vault_name': 'Test', 'exclude_dirs': ['private']}), encoding='utf-8')
        (self.vault / '20-knowledge').mkdir()
        self.source = self.vault / '20-knowledge/public.md'
        self.source.write_bytes('# Public fixture\r\nCSV export is supported.\r\nUnselected detail.\r\n'.encode())
        self.request = {
            'schema_version': 1, 'task': 'support',
            'sources': [{'path': '20-knowledge/public.md', 'excerpt': 'CSV export is supported.'}],
            'questions': [{'id': 'export', 'instructions': 'Does the evidence support CSV export?',
                           'choices': {'yes': 'Explicitly supported', 'no': 'Explicitly contradicted',
                                       'unknown': 'Insufficient evidence'}, 'abstain': 'unknown'}],
        }
        self.calls = []

    def transport(self, payload, api_key, timeout):
        self.calls.append(json.loads(payload))
        return self.reply()

    def reply(self, choice='yes', confidence=0.95):
        return json.dumps({'model': 'jev-1.13.0', 'answers': {'export': {
            'type': 'choice', 'choice': choice, 'confidence': confidence,
            'probabilities': {key: (0.9 if key == choice else 0.05)
                              for key in ('yes', 'no', 'unknown')}}},
            'usage': {'input_tokens': 12, 'output_tokens': 3}}).encode()

    def run_judge(self, **kwargs):
        return judge.run(self.vault, self.request, allow_network=True,
                         api_key='synthetic-test-token', transport=self.transport, **kwargs)

    def test_prepare_is_offline_and_returns_hashes_and_source_lines(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network')):
            result = judge.prepare(self.vault, self.request)
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['sources'][0]['line_start'], 2)
        self.assertEqual(result['sources'][0]['line_end'], 2)
        self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertNotIn('CSV export is supported.', json.dumps(result))

    def test_call_sends_only_selected_evidence_and_returns_advisory_choices(self):
        result = self.run_judge()
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(result['role'], 'advisory')
        self.assertEqual(result['answers']['export']['choice'], 'yes')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['state'], {'evidence': ['CSV export is supported.']})
        outgoing = json.dumps(self.calls[0])
        self.assertNotIn('public.md', outgoing)
        self.assertNotIn('Unselected detail', outgoing)
        self.assertEqual(result['sources'][0]['path'], '20-knowledge/public.md')

    def test_denied_or_excluded_sources_never_reach_network(self):
        for relative in ('10-inbox/_processed/secret.md', 'private/secret.md',
                         '20-knowledge/.git/secret.md', '20-knowledge/public.txt'):
            path = self.vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('CSV export is supported.', encoding='utf-8')
        for relative in ('10-inbox/_processed/secret.md', 'private/secret.md', '../outside.md',
                         '20-knowledge/.git/secret.md', '20-knowledge/public.txt'):
            with self.subTest(relative=relative):
                self.request['sources'][0]['path'] = relative
                self.assertEqual(self.run_judge()['status'], 'unverified')
        self.assertEqual(self.calls, [])

    def test_excerpt_must_be_bound_to_actual_source(self):
        self.request['sources'][0]['excerpt'] = 'CSV export is prohibited.'
        self.assertEqual(self.run_judge()['error'], 'excerpt_not_found')
        self.assertEqual(self.calls, [])

    def test_crlf_binding_keeps_line_numbers(self):
        self.request['sources'][0]['excerpt'] = '# Public fixture\nCSV export is supported.'
        result = judge.prepare(self.vault, self.request)
        self.assertEqual((result['sources'][0]['line_start'], result['sources'][0]['line_end']), (1, 2))

    def test_trailing_newline_does_not_cite_the_next_line(self):
        self.request['sources'][0]['excerpt'] = 'CSV export is supported.\n'
        result = judge.prepare(self.vault, self.request)
        self.assertEqual(result['sources'][0]['line_end'], 2)

    def test_prepared_request_hash_matches_actual_wire_bytes(self):
        observed = []
        def capture(payload, *args):
            observed.append(hashlib.sha256(payload).hexdigest())
            return self.reply()
        result = judge.run(self.vault, self.request, allow_network=True,
                           api_key='synthetic-test-token', transport=capture)
        self.assertEqual(result['request_sha256'], observed[0])

    def test_empty_recall_returns_review_without_calling_provider(self):
        self.request['sources'] = []
        result = self.run_judge()
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['reason'], 'no_evidence')
        self.assertEqual(self.calls, [])

    def test_deterministic_tasks_route_offline(self):
        for task in ('arithmetic', 'file_exists', 'date_comparison', 'permission', 'generation'):
            self.request['task'] = task
            result = self.run_judge()
            self.assertEqual(result['status'], 'not_applicable')
        self.assertEqual(self.calls, [])

    def test_network_flag_is_required_before_source_read(self):
        with patch.object(judge, 'Store', side_effect=AssertionError('read before consent')):
            result = judge.run(self.vault, self.request, allow_network=False,
                               api_key='synthetic-test-token', transport=self.transport)
        self.assertEqual(result['error'], 'network_not_authorized')
        self.assertEqual(self.calls, [])

    def test_missing_key_is_reported_without_call(self):
        with patch.dict(os.environ, {}, clear=True):
            result = judge.run(self.vault, self.request, allow_network=True, transport=self.transport)
        self.assertEqual(result['error'], 'missing_api_key')
        self.assertEqual(self.calls, [])

    def test_abstention_and_low_confidence_require_review(self):
        for choice, confidence in [('unknown', .99), ('yes', .4)]:
            result = judge.run(self.vault, self.request, allow_network=True,
                               api_key='synthetic-test-token',
                               transport=lambda *args: self.reply(choice, confidence))
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(result['answers']['export']['choice'], choice)

    def test_source_changes_during_call_discard_answers(self):
        def change(*args):
            self.source.write_text('CSV is no longer supported.', encoding='utf-8')
            return self.reply()
        result = judge.run(self.vault, self.request, allow_network=True,
                           api_key='synthetic-test-token', transport=change)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['error'], 'source_changed')
        self.assertNotIn('answers', result)

    def test_policy_changes_during_call_discard_answers(self):
        def change(*args):
            self.config.write_text(json.dumps({'deny_zones': ['20-knowledge']}), encoding='utf-8')
            return self.reply()
        result = judge.run(self.vault, self.request, allow_network=True,
                           api_key='synthetic-test-token', transport=change)
        self.assertEqual(result['status'], 'unverified')
        self.assertNotIn('answers', result)

    def test_request_mutation_by_transport_cannot_change_abstention(self):
        def change(*args):
            self.request['questions'][0]['abstain'] = 'yes'
            return self.reply()
        result = judge.run(self.vault, self.request, allow_network=True,
                           api_key='synthetic-test-token', transport=change)
        self.assertEqual(result['status'], 'reviewed')

    def test_known_credentials_are_rejected_without_echo(self):
        fake = 'apikey_' + 'a' * 32 + '_' + 'b' * 64
        self.request['questions'][0]['instructions'] = 'Check this ' + fake
        result = self.run_judge()
        self.assertEqual(result['error'], 'sensitive_input')
        self.assertNotIn(fake, json.dumps(result))
        self.assertEqual(self.calls, [])

    def test_unknown_keys_and_invalid_question_contracts_never_call(self):
        bad_requests = []
        for modify in (
            lambda x: x.update(unexpected=True),
            lambda x: x.update(schema_version=True),
            lambda x: x.update(min_confidence=float('nan')),
            lambda x: x.update(min_confidence=True),
            lambda x: x['questions'][0].update(abstain='missing'),
            lambda x: x['questions'].append(copy.deepcopy(x['questions'][0])),
            lambda x: x['questions'][0].update(choices={'yes': 'a', 'unknown': ''}),
            lambda x: x['sources'][0].update(excerpt=''),
        ):
            data = copy.deepcopy(self.request)
            modify(data)
            bad_requests.append(data)
        for data in bad_requests:
            result = judge.run(self.vault, data, allow_network=True,
                               api_key='synthetic-test-token', transport=self.transport)
            self.assertEqual(result['status'], 'unverified')
        self.assertEqual(self.calls, [])

    def test_hardlinked_evidence_is_rejected(self):
        target = self.vault / '20-knowledge/link.md'
        try:
            os.link(self.source, target)
        except OSError as exc:
            self.skipTest(str(exc))
        self.assertEqual(self.run_judge()['status'], 'unverified')
        self.assertEqual(self.calls, [])

    @unittest.skipUnless(os.name == 'nt', 'Windows short path aliases')
    def test_short_path_alias_is_excluded_before_reading_content(self):
        import ctypes
        folder = self.vault / 'private-review-material'
        folder.mkdir()
        secret = folder / 'secret.md'
        secret.write_text('CSV export is supported.', encoding='utf-8')
        self.config.write_text(json.dumps({'exclude_dirs': ['private-review-material']}), encoding='utf-8')
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(secret), buffer, len(buffer)):
            self.skipTest('8.3 path unavailable')
        short_parts = Path(buffer.value).parts
        relative = '/'.join(short_parts[-2:])
        if relative == 'private-review-material/secret.md':
            self.skipTest('8.3 name creation disabled')
        self.request['sources'][0]['path'] = relative
        reads = []
        original = judge.stable_read
        def observe(resolver, *args, **kwargs):
            path = resolver()
            if path == secret:
                reads.append(path)
            return original(resolver, *args, **kwargs)
        with patch.object(judge, 'stable_read', side_effect=observe):
            result = self.run_judge()
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(reads, [])
        self.assertEqual(self.calls, [])

    def test_multilingual_categories_supported(self):
        self.request['questions'][0].update(choices={'긍정': '긍정적 표현', '부정': '부정적 표현',
                                                   '판단불가': '근거 부족'}, abstain='판단불가')
        result = judge.prepare(self.vault, self.request)
        self.assertEqual(result['status'], 'prepared')

    def cli(self, args, content):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        env.pop('TYPESAFE_API_KEY', None)
        return subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'vault_judge.py'),
                               '--vault', str(self.vault), *args], input=content,
                              capture_output=True, env=env, timeout=10)

    def test_cli_prepare_utf8_and_exit_status(self):
        result = self.cli(['prepare'], json.dumps(self.request).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'prepared')

    def test_cli_rejects_duplicate_json_keys_oversize_and_invalid_utf8(self):
        for content in (b'{"schema_version":1,"schema_version":1}', b' ' * 65537, b'\xff'):
            result = self.cli(['prepare'], content)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['status'], 'unverified')
            self.assertNotIn(b'Traceback', result.stderr)

    def test_cli_rejects_duplicate_or_unknown_flags(self):
        for flags in (['run', '--allow-network', '--allow-network'], ['prepare', '--allow-network'],
                      ['prepare', '--unknown'], ['prepare', '--vault', str(self.vault)]):
            result = self.cli(flags, json.dumps(self.request).encode())
            self.assertEqual(result.returncode, 2)

    def test_cli_without_network_flag_never_needs_input(self):
        result = self.cli(['run'], b'')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['error'], 'network_not_authorized')


if __name__ == '__main__':
    unittest.main()
