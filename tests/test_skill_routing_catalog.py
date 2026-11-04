"""Behavioral contracts for explicit, non-executing skill catalog collection."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from skill_routing_catalog import build_catalog, analyze_catalog
from skill_routing_contract import RoutingError


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def skill(self, folder='one', name='vault', description='Use for vault work.'):
        path = self.root / folder / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('---\nname: ' + name + '\ndescription: ' + description + '\n---\n\nNever executed.\n').encode('utf-8')
        path.write_bytes(raw)
        return path, raw

    def test_owned_identity_and_exact_source_hash(self):
        path, raw = self.skill()
        result = build_catalog([('av', self.root)])
        self.assertEqual(result['source_kind'], 'manifest_scan')
        self.assertEqual(result['skills'], [{'id': 'av:vault', 'name': 'vault',
            'description': 'Use for vault work.', 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'source_path': 'av/one/SKILL.md'}])
        self.assertTrue(path.exists())

    def test_quoted_descriptions_and_korean(self):
        self.skill(description="'Don''t modify 볼트 without a request.'")
        self.assertEqual(build_catalog([('av', self.root)])['skills'][0]['description'],
                         "Don't modify 볼트 without a request.")

    def test_double_quotes_json_subset(self):
        self.skill(description='"Use a \\"quoted\\" description."'.replace('\\\\', '\\'))
        self.assertEqual(build_catalog([('av', self.root)])['skills'][0]['description'],
                         'Use a "quoted" description.')

    def test_same_name_other_owner_is_advisory_not_invalid(self):
        self.skill()
        catalog = build_catalog([('av', self.root), ('other', self.root)])
        report = analyze_catalog(catalog)
        self.assertEqual(report['same_names'], [{'name': 'vault', 'skills': ['av:vault', 'other:vault']}])
        self.assertFalse(report['model_run_performed'])

    def test_duplicate_owned_identity_rejected(self):
        self.skill('one'); self.skill('two')
        with self.assertRaises(RoutingError):
            build_catalog([('av', self.root)])

    def test_multiline_description_not_silently_truncated(self):
        self.skill(description='>\n  line one\n  line two')
        with self.assertRaises(RoutingError):
            build_catalog([('av', self.root)])

    def test_ambiguous_plain_scalar_rejected(self):
        self.skill(description='true')
        with self.assertRaises(RoutingError):
            build_catalog([('av', self.root)])

    def test_comment_numeric_and_yaml_indicator_scalars_rejected(self):
        for raw in ['# comment', '123', '1.5', '0x2A', '1e3', '- item',
                    '? mapping', '@reserved', '.nan', '2001-01-01',
                    ']invalid', '}invalid', '-', '?', ':', 'Use for vault:']:
            with self.subTest(raw=raw):
                self.skill(description=raw)
                with self.assertRaises(RoutingError):
                    build_catalog([('av', self.root)])

    def test_unterminated_frontmatter_rejected(self):
        p, _ = self.skill(); p.write_text('---\nname: vault\ndescription: x\n', encoding='utf-8')
        with self.assertRaises(RoutingError):
            build_catalog([('av', self.root)])

    def test_duplicate_manifest_field_rejected(self):
        p, raw = self.skill(); p.write_bytes(raw.replace(b'name: vault', b'name: vault\nname: other'))
        with self.assertRaises(RoutingError):
            build_catalog([('av', self.root)])

    def test_unsupported_unknown_metadata_is_not_accepted_as_valid_yaml(self):
        p, raw = self.skill()
        p.write_bytes(raw.replace(b'\n---\n\n', b'\nmetadata: {unterminated\n---\n\n'))
        with self.assertRaises(RoutingError):build_catalog([('av', self.root)])

    def test_unreadable_subtree_is_not_a_successful_partial_catalog(self):
        self.skill('good', 'good'); self.skill('blocked', 'blocked')
        scan = os.scandir
        def denied(path):
            if Path(path).name == 'blocked':raise PermissionError('blocked')
            return scan(path)
        with patch('skill_routing_catalog.os.scandir', side_effect=denied):
            with self.assertRaises(RoutingError):build_catalog([('av', self.root)])

    def test_empty_and_wrong_owner_rejected(self):
        with self.assertRaises(RoutingError):build_catalog([('av', self.root)])
        self.skill()
        with self.assertRaises(RoutingError):build_catalog([('../av', self.root)])

    def test_real_repository_manifests_collect_without_body_execution(self):
        repository = Path(__file__).resolve().parents[1]
        report = build_catalog([('agentic-vault', repository / 'skills')])
        self.assertEqual({r['id'] for r in report['skills']},
                         {'agentic-vault:agentic-vault', 'agentic-vault:webapp-principles'})

    def test_advisory_similar_words_do_not_claim_collision(self):
        self.skill('one', 'one', 'Use for vault source recall')
        self.skill('two', 'two', 'Use for vault source backup')
        report = analyze_catalog(build_catalog([('av', self.root)]))
        self.assertTrue(report['overlap_candidates'])
        self.assertIn('not routing evidence', report['scope'])


if __name__ == '__main__':
    unittest.main()
