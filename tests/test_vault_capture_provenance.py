from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_provenance as provenance


class CaptureProvenanceTests(unittest.TestCase):
    def test_archived_captures_can_be_read_and_rendered_byte_for_byte(self):
        fixtures = json.loads((SCRIPTS / 'resources/lint-benign-v1.json').read_text(encoding='utf-8'))
        cases = {case['id']: case['notes'] for case in fixtures['cases']}
        for case_id, origin in (('capture-own-unmodified', 'own'), ('capture-forwarded-unprivileged', 'forwarded')):
            with self.subTest(case_id=case_id):
                [original] = cases[case_id].values()
                data = original.encode('utf-8')
                metadata, body = provenance.parse_capture(data)
                self.assertEqual(metadata['content_origin'], origin)
                self.assertEqual(metadata['title'], 'Jarvis capture')
                restored = provenance.render_capture(
                    body.decode('utf-8'), captured_via=metadata['captured_via'], content_origin=origin,
                    classification=metadata['classification'], captured_at=datetime.fromisoformat(metadata['captured_at']))
                self.assertEqual(restored, data)

    def test_explicit_deny_and_retrieval_exclusion_remain_distinct_for_legacy_capture_path(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            config = vault / '00-meta/vault-config.json'
            config.parent.mkdir()
            settings = {'required_keys': ['title'], 'enums': {}}
            config.write_text(json.dumps({**settings, 'deny_zones': ['10-inbox']}), encoding='utf-8')
            with self.assertRaisesRegex(provenance.ProvenanceError, 'capture_destination_denied_or_unsafe'):
                provenance.capture_policy(vault)
            config.write_text(json.dumps({**settings, 'exclude_dirs': ['10-inbox']}), encoding='utf-8')
            self.assertIsNotNone(provenance.capture_policy(vault))
            self.assertFalse((vault / '10-inbox').exists())


if __name__ == '__main__':
    unittest.main()
