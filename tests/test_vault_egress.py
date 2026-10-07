from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class EgressTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('vault_egress')

    def test_fake_secret_all_fillers_and_selectors_warn_without_value_leak(self):
        secret = 'sk-' + 'A'*24
        for splitter in ('', '\u115f', '\u1160', '\u3164', '\uffa0', '\u200b', '\ufe0f', '\U000e0100'):
            text = secret[:5] + splitter + secret[5:]
            findings = self.module.scan_text(text, '20-knowledge/example.md')
            self.assertEqual([row['code'] for row in findings], ['egress-secret'])
            self.assertTrue(all(row['severity'] == 'warning' for row in findings))
            self.assertNotIn(secret, json.dumps(findings))
            self.assertEqual(text, secret[:5] + splitter + secret[5:])

    def test_korean_ids_warn_but_valid_dates_phones_and_business_numbers_do_not(self):
        for number in ('900101-1234567', '９００１０１－１２３４５６７', '900\u3164101-1234567'):
            with self.subTest(number=number):
                findings = self.module.scan_text(number)
                self.assertEqual([row['code'] for row in findings], ['egress-korean-id'])
                self.assertNotIn('1234567', json.dumps(findings))
        for text in ('2026-10-07', '010-1234-5678', '123-45-67890', '900132-1234567',
                     '900101-9234567', '일반 한글 회의 노트, 카페 café 🌱️', 'password policy document'):
            with self.subTest(text=text):
                self.assertEqual(self.module.scan_text(text), [])

    def test_codes_deduplicate_and_lines_refer_to_original_input(self):
        rows = self.module.scan_text('ordinary\n'+'sk-'+'B'*24+'\n'+'sk-'+'C'*24+'\n900101-1234567')
        self.assertEqual([(r['code'], r['line']) for r in rows], [('egress-secret', 2), ('egress-korean-id', 4)])

    def test_diagnostics_redact_sensitive_paths_and_keep_stable_issue_ids(self):
        secret = 'sk-' + 'E' * 24
        identifier = '900101-1234567'
        for value in (secret, identifier, secret[:5] + '\u3164' + secret[5:],
                      '９００１０１－１２３４５６７', '900\u200b101-1234567'):
            path = '20-knowledge/' + value + '.md'
            with self.subTest(path_kind='synthetic_sensitive_path'):
                rows = self.module.scan_text(secret + '\n' + identifier, path)
                serialized = json.dumps(rows, ensure_ascii=False)
                for forbidden in (secret, identifier, value):
                    self.assertNotIn(forbidden, serialized)
                self.assertEqual({row['path'] for row in rows}, {'<redacted-sensitive-path>'})
                self.assertEqual(rows, self.module.scan_text(secret + '\n' + identifier, path))
                self.assertEqual([row['line'] for row in rows], [1, 2])
        safe = self.module.scan_text(secret, '20-knowledge/ordinary.md')
        self.assertEqual(safe[0]['path'], '20-knowledge/ordinary.md')

    def test_incomplete_diagnostics_also_redact_sensitive_paths(self):
        path = '20-knowledge/' + 'sk-' + 'F' * 24 + '.md'
        for body in ('x' * (256 * 1024 + 1), '\ud800'):
            with self.subTest(body_kind='oversized_or_invalid_utf8'):
                rows = self.module.scan_text(body, path)
                self.assertEqual(rows[0]['code'], 'egress-incomplete')
                self.assertNotIn('F' * 24, json.dumps(rows))
                self.assertEqual(rows[0]['path'], '<redacted-sensitive-path>')

    def test_inventory_filters_deny_excludes_and_reserved_runtime_before_scanning(self):
        text = 'sk-' + 'D'*24
        notes = {'90-assets/secret.md': text, '00-meta/.agentic-vault/runtime/a.md': text,
                 '20-knowledge/ignore.md': text, '20-knowledge/allowed.md': text}
        config = {'deny_zones': ['90-assets'], 'exclude_dirs': ['ignore.md']}
        rows = self.module.analyze_notes(notes, config)
        self.assertEqual([r['path'] for r in rows], ['20-knowledge/allowed.md'])

    def test_excessive_input_reports_incomplete_without_claiming_safety(self):
        rows = self.module.scan_text('x'*(256*1024+1))
        self.assertEqual(rows[0]['code'], 'egress-incomplete')
        self.assertEqual(rows[0]['severity'], 'warning')
        optional = self.module.optional_scanners()
        self.assertEqual(optional['status'], 'not_invoked')
        self.assertFalse(optional['required'])

    def test_maximum_single_line_is_bounded_and_invalid_policy_is_explicit(self):
        rows = self.module.scan_text('x' * (256*1024))
        self.assertEqual(rows, [])
        rows = self.module.analyze_notes({'a.md': 'safe'}, {'deny_zones': [None]})
        self.assertEqual(rows[0]['code'], 'egress-incomplete')

    def test_multiline_credentials_get_a_whole_original_warning_without_values(self):
        for label, value in [('password', 'abcdefgh'), ('api_key', 'abcdefgh'),
                             ('authorization', 'Bearer abcdefgh')]:
            for separator in ('\n', '\r\n', '\u2028'):
                for splitter in ('', '\u3164', '\u115f', '\u200b', '\ufe0f'):
                    text = label[:2] + splitter + label[2:] + ':' + separator + value
                    with self.subTest(label=label, separator=repr(separator), splitter=repr(splitter)):
                        rows = self.module.scan_text(text, '20-knowledge/example.md')
                        self.assertEqual([(row['code'], row['line']) for row in rows], [('egress-secret', 0)])
                        self.assertNotIn('abcdefgh', json.dumps(rows))
                        self.assertEqual(rows[0]['severity'], 'warning')

    def test_inventory_scans_multiline_originals_with_same_pure_warning(self):
        notes = {'20-knowledge/example.md': 'password:\nabcdefgh'}
        rows = self.module.analyze_notes(notes, {'deny_zones': [], 'exclude_dirs': []})
        self.assertEqual([(row['code'], row['path'], row['line']) for row in rows],
                         [('egress-secret', '20-knowledge/example.md', 0)])

    def test_whole_original_fallback_does_not_duplicate_a_line_local_category(self):
        secret = 'sk-' + 'H' * 24
        text = 'password:\nabcdefgh\n' + secret + '\n900101-1234567'
        rows = self.module.scan_text(text)
        self.assertEqual([(row['code'], row['line']) for row in rows],
                         [('egress-secret', 3), ('egress-korean-id', 4)])

    def test_harmless_multiline_text_does_not_get_credential_warnings(self):
        self.assertEqual(self.module.scan_text(
            'Password policy discussion.\nAPI key rotation guidance.\r\nAuthorization overview.'), [])


if __name__ == '__main__':
    unittest.main()
