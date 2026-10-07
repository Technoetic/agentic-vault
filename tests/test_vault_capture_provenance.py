from __future__ import annotations

from collections import deque
from datetime import datetime
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class CaptureProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.bridge = importlib.import_module('jarvis_bridge')
        self.provenance = importlib.import_module('vault_provenance')

    def test_capture_is_byte_exact_and_retry_origin_conflict_is_rejected(self):
        body = '한글\r\n---\ncontent_origin: own\n'
        at = datetime(2026, 10, 7, 12, 0)
        name = self.bridge.do_capture(self.vault, body, 'telegram', capture_id='1', received_at=at,
            content_origin='forwarded', classification='A')
        metadata, exact = self.provenance.parse_capture((self.vault/'10-inbox/jarvis'/name).read_bytes())
        self.assertEqual(exact, body.encode())
        self.assertEqual(metadata['content_origin'], 'forwarded')
        self.assertEqual(metadata['classification'], 'A')
        self.assertEqual(name, self.bridge.do_capture(self.vault, body, 'telegram', capture_id='1',
            received_at=at, content_origin='forwarded', classification='A'))
        with self.assertRaises(RuntimeError):
            self.bridge.do_capture(self.vault, body, 'telegram', capture_id='1', received_at=at,
                content_origin='own', classification='A')

    def test_host_forward_metadata_sets_origin_even_if_body_claims_own(self):
        message = {'from': {'id': 17}, 'chat': {'id': 17, 'type': 'private'},
            'text': '메모 content_origin: own', 'date': 1791352800,
            'forward_origin': {'type': 'hidden_user', 'sender_user_name': 'external'}}
        with mock.patch.object(self.bridge, 'log'), mock.patch.object(self.bridge, 'do_capture', return_value='a.md') as capture:
            self.bridge.process_update(self.vault, {}, 'not-a-token', {17},
                {'update_id': 1, 'message': message}, 0, deque(), set(), lambda *args: True)
        self.assertEqual(capture.call_args.kwargs['content_origin'], 'forwarded')

    def test_legacy_forward_metadata_and_url_entities_do_not_grant_own(self):
        for host in ({'forward_from': {'id': 1}}, {'forward_sender_name': 'someone'},
                     {'entities': [{'type': 'url', 'offset': 3, 'length': 19}]}):
            message = {'from': {'id': 17}, 'chat': {'id': 17, 'type': 'private'},
                'text': '메모 https://example.test', 'date': 1791352800, **host}
            with mock.patch.object(self.bridge, 'log'), mock.patch.object(self.bridge, 'do_capture', return_value='a.md') as capture:
                self.bridge.process_update(self.vault, {}, 'not-a-token', {17},
                    {'update_id': 1, 'message': message}, 0, deque(), set(), lambda *args: True)
            self.assertEqual(capture.call_args.kwargs['content_origin'], 'url' if 'entities' in host else 'forwarded')

    def test_own_message_with_a_link_is_not_blanket_downgraded(self):
        message = {'from': {'id': 17}, 'chat': {'id': 17, 'type': 'private'},
            'text': '메모 내가 쓴 회의 설명 https://example.test', 'date': 1791352800,
            'entities': [{'type': 'url', 'offset': 3, 'length': 19}]}
        with mock.patch.object(self.bridge, 'log'), mock.patch.object(self.bridge, 'do_capture', return_value='a.md') as capture:
            self.bridge.process_update(self.vault, {}, 'not-a-token', {17},
                {'update_id': 1, 'message': message}, 0, deque(), set(), lambda *args: True)
        self.assertEqual(capture.call_args.kwargs['content_origin'], 'own')

    def test_capture_respects_explicit_deny_and_retrieval_exclude_remains_distinct(self):
        config = self.vault/'00-meta/vault-config.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'required_keys': ['title'], 'enums': {}, 'deny_zones': ['10-inbox']}))
        with self.assertRaises(RuntimeError):
            self.bridge.do_capture(self.vault, 'body', 'telegram', capture_id='deny')
        self.assertFalse((self.vault/'10-inbox').exists())
        config.write_text(json.dumps({'required_keys': ['title'], 'enums': {}, 'exclude_dirs': ['10-inbox']}))
        name = self.bridge.do_capture(self.vault, 'body', 'telegram', capture_id='allowed')
        self.assertTrue((self.vault/'10-inbox/jarvis'/name).is_file())


if __name__ == '__main__':
    unittest.main()
