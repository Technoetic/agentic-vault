"""Synthetic, read-only authored record probes and their policy boundaries."""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
quality = importlib.import_module('vault_compile_quality')
proposals = importlib.import_module('vault_proposals')
try:
    relations = importlib.import_module('vault_relation_probes')
except ModuleNotFoundError as exc:
    if exc.name != 'vault_relation_probes':
        raise
    relations = None

ORIGINAL = '[[Item A]] quantity is 10.\n[[Item B]] quantity is 20.\n'
SWAPPED = '[[Item A]] quantity is 20.\n[[Item B]] quantity is 10.\n'
PROBES = [{'id': 'qty_a', 'anchor': '[[Item A]]', 'scope': 'line'},
          {'id': 'qty_b', 'anchor': '[[Item B]]', 'scope': 'line'}]


class RelationRecordTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(relations, 'authored record probe module is not implemented')

    def test_quantity_swaps_need_review_despite_complete_legacy_counts(self):
        self.assertEqual(quality.check_preservation(ORIGINAL, SWAPPED)['status'], 'complete')
        result = relations.check_relation_records(ORIGINAL, SWAPPED, PROBES)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual([row['reason'] for row in result['relation_rows']], ['record_changed'] * 2)
        self.assertEqual([row['preserved'] for row in result['relation_rows']], [False, False])

    def test_appended_negation_changes_the_whole_selected_record(self):
        result = relations.check_relation_records('[[Policy]] approval required.',
                                                  '[[Policy]] approval required. Not required.',
                                                  [{'id': 'approval', 'anchor': '[[Policy]]', 'scope': 'line'}])
        self.assertEqual(result['relation_rows'][0]['reason'], 'record_changed')

    def test_paragraph_observes_exception_removal_but_line_is_narrower(self):
        original = '[[Policy]] access forbidden.\nExcept emergency access.\n\nOther record.'
        candidate = '[[Policy]] access forbidden.\n\nOther record.'
        for scope, expected in [('line', 'complete'), ('paragraph', 'needs_review')]:
            with self.subTest(scope=scope):
                result = relations.check_relation_records(original, candidate,
                    [{'id': 'exception', 'anchor': '[[Policy]]', 'scope': scope}])
                self.assertEqual(result['status'], expected)

    def test_copy_elsewhere_cannot_mask_changed_record(self):
        result = relations.check_relation_records(ORIGINAL, SWAPPED + '\n' + ORIGINAL, PROBES)
        self.assertEqual([row['reason'] for row in result['relation_rows']], ['candidate_ambiguous'] * 2)
        self.assertEqual([row['candidate_count'] for row in result['relation_rows']], [2, 2])
        self.assertTrue(all(row['candidate_sha256'] is None for row in result['relation_rows']))

    def test_record_order_and_repeated_anchor_in_one_record_are_allowed(self):
        source = 'Anchor Anchor value 10.\nOther value 20.\n'
        result = relations.check_relation_records(source, 'Other value 20.\nAnchor Anchor value 10.\n',
                                                  [{'id': 'a', 'anchor': 'Anchor', 'scope': 'line'}])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['relation_rows'][0]['original_count'], 1)

    def test_source_locator_must_bind_exactly_one_body_record(self):
        for source in ('unrelated', 'Anchor one\nAnchor two', '\ufeff---\ntitle: Anchor\n---\nOther'):
            with self.subTest(source=source), self.assertRaises(relations.RelationProbeError):
                relations.check_relation_records(source, source, [{'id': 'a', 'anchor': 'Anchor', 'scope': 'line'}])

    def test_candidate_missing_is_observation_and_frontmatter_is_excluded(self):
        source = '\ufeff---\ntitle: Anchor\n---\nAnchor one\n'
        result = relations.check_relation_records(source, '---\ntitle: Anchor\n---\nOther\n',
                                                  [{'id': 'a', 'anchor': 'Anchor', 'scope': 'line'}])
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['relation_rows'][0]['reason'], 'candidate_missing')
        self.assertEqual(result['relation_rows'][0]['candidate_count'], 0)

    def test_nfc_and_line_endings_are_equivalent_case_and_compatibility_are_not(self):
        probe = [{'id': 'a', 'anchor': 'Caf\u00e9', 'scope': 'paragraph'}]
        result = relations.check_relation_records('Caf\u00e9 1 Pa\r\nexception\r\n',
                                                  'Cafe\u0301 1 Pa\nexception\n', probe)
        self.assertEqual(result['status'], 'complete')
        for source, candidate in [('A 1 Pa', 'A 1 pA'), ('A 2\u00b2', 'A 22'), ('A 10', 'A 10 '),
                                  ('  A 10', 'A 10')]:
            with self.subTest(source=source):
                result = relations.check_relation_records(source, candidate,
                                                          [{'id': 'a', 'anchor': 'A', 'scope': 'line'}])
                self.assertEqual(result['status'], 'needs_review')

    def test_literal_anchor_and_output_do_not_disclose_records_or_locator_text(self):
        source = '[.*] synthetic-private-record 10'
        result = relations.check_relation_records(source, source,
                                                  [{'id': 'literal', 'anchor': '[.*]', 'scope': 'line'}])
        row = result['relation_rows'][0]
        self.assertEqual(row['original_sha256'], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(row['original_locator'], {'start_line': 1, 'end_line': 1})
        self.assertNotIn(source, json.dumps(result))
        self.assertNotIn('[.*]', json.dumps(result))
        missing = relations.check_relation_records(source, 'unrelated',
                                                   [{'id': 'literal', 'anchor': '[.*]', 'scope': 'line'}])
        self.assertEqual(missing['relation_rows'][0]['reason'], 'candidate_missing')

    def test_probe_shapes_limits_and_text_bounds_fail_closed(self):
        base = {'id': 'a', 'anchor': 'Anchor', 'scope': 'line'}
        invalid = [None, [], True, {}, [True], [base, base],
                   [{**base, 'scope': 'sentence'}], [{**base, 'extra': 1}],
                   [{**base, 'id': True}], [{**base, 'id': 'a' * 65}],
                   [{**base, 'anchor': ''}], [{**base, 'anchor': ' ' * 3}],
                   [{**base, 'anchor': 'a' * 257}], [{**base, 'anchor': 'A\nB'}],
                   [{**base, 'anchor': 'A\x00B'}], [{**base, 'anchor': 'A\u2028B'}],
                   [{**base, 'id': str(i)} for i in range(65)]]
        for probes in invalid:
            with self.subTest(probes=probes), self.assertRaises(relations.RelationProbeError):
                relations.check_relation_records('Anchor', 'Anchor', probes)
        for text in (None, '\ud800', 'x' * (256 * 1024 + 1)):
            with self.subTest(text_type=type(text).__name__), self.assertRaises(relations.RelationProbeError):
                relations.check_relation_records(text, 'Anchor', [base])
        accepted = [{'id': str(i), 'anchor': 'A' * 256, 'scope': 'line'} for i in range(64)]
        self.assertEqual(len(relations.check_relation_records('A' * 256, 'A' * 256, accepted)['relation_rows']), 64)


class RelationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name).resolve()
        self.write('00-meta/vault-config.json', json.dumps({'required_keys': [], 'enums': {}, 'deny_zones': ['private']}))
        self.source = self.write('10-inbox/source.md', ORIGINAL)
        self.target = self.write('20-knowledge/result.md', SWAPPED)

    def write(self, relative, text):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return {'path': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def compile_probes(self):
        return [{**probe, 'source_path': self.source['path'], 'target_path': self.target['path']} for probe in PROBES]

    def run_cli(self, script, *args, request=None):
        done = subprocess.run([sys.executable, str(SCRIPTS / script), '--vault', str(self.vault), *args],
                              input=json.dumps(request) if request is not None else None,
                              capture_output=True, encoding='utf-8')
        return done.returncode, json.loads(done.stdout)

    def test_preservation_opt_in_adds_review_without_changing_default_bytes(self):
        legacy = quality.check_preservation(ORIGINAL, SWAPPED)
        self.assertEqual(json.dumps(legacy), json.dumps(quality.check_preservation(ORIGINAL, SWAPPED, relation_probes=None)))
        self.assertNotIn('relation_rows', legacy)
        result = quality.check_preservation(ORIGINAL, SWAPPED, relation_probes=PROBES)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['review_reasons'], ['relation_record_changed'])
        with self.assertRaises(quality.QualityError):
            quality.check_preservation(ORIGINAL, SWAPPED, relation_probes=[])

    def test_authored_record_is_preservation_evidence_without_legacy_literals(self):
        original = '[.*] approval is required.'
        probes = [{'id': 'selected', 'anchor': '[.*]', 'scope': 'line'}]
        legacy = quality.check_preservation(original, original)
        self.assertEqual((legacy['status'], legacy['review_reasons']),
                         ('needs_review', ['no_selected_literal_facts']))
        unchanged = quality.check_preservation(original, original, relation_probes=probes)
        self.assertEqual((unchanged['status'], unchanged['review_reasons'], unchanged['facts_checked']),
                         ('complete', [], 0))
        changed = quality.check_preservation(original, original + ' Not required.', relation_probes=probes)
        self.assertEqual((changed['status'], changed['review_reasons']),
                         ('needs_review', ['relation_record_changed']))

    def test_authored_record_evidence_keeps_other_preservation_review_reasons(self):
        original = '[.*] approval is required.'
        probes = [{'id': 'selected', 'anchor': '[.*]', 'scope': 'line'}]
        result = quality.check_preservation(original, original, expected_removals=['approval'],
                                            expected_additions=['new phrase'], relation_probes=probes)
        self.assertEqual((result['status'], result['review_reasons']),
                         ('needs_review', ['declared_removal_not_observed', 'expected_addition_missing']))
        result = quality.check_preservation(original + '\nCount 10', original + '\nCount 20', relation_probes=probes)
        self.assertEqual((result['status'], result['review_reasons']), ('needs_review', ['unexpected_removals']))

    def test_authored_record_is_compile_evidence_without_legacy_literals(self):
        original = '[.*] approval is required.'
        source = self.write('10-inbox/source.md', original)
        target = self.write('20-knowledge/result.md', original)
        probes = [{'id': 'selected', 'anchor': '[.*]', 'scope': 'line',
                   'source_path': source['path'], 'target_path': target['path']}]
        legacy = quality.check_compile(self.vault, [source], [target], [])
        self.assertEqual((legacy['status'], legacy['review_reason']), ('needs_review', 'no_selected_literal_facts'))
        unchanged = quality.check_compile(self.vault, [source], [target], [], relation_probes=probes)
        self.assertEqual((unchanged['status'], unchanged['review_reason'], unchanged['facts_checked']),
                         ('complete', None, 0))
        target = self.write('20-knowledge/result.md', original + ' Not required.')
        changed = quality.check_compile(self.vault, [source], [target], [], relation_probes=probes)
        self.assertEqual((changed['status'], changed['review_reason']), ('needs_review', 'relation_record_changed'))
        source = self.write('10-inbox/source.md', original + '\nCount 10')
        target = self.write('20-knowledge/result.md', original + '\nCount 20')
        missing = quality.check_compile(self.vault, [source], [target], [], relation_probes=probes)
        self.assertEqual((missing['status'], missing['review_reason']), ('needs_review', 'missing_literal_facts'))

    def test_compile_selected_roles_and_optional_request_field(self):
        args = (self.vault, [self.source], [self.target], [])
        legacy = quality.check_compile(*args)
        self.assertEqual(json.dumps(legacy), json.dumps(quality.check_compile(*args, relation_probes=None)))
        self.assertEqual(legacy['status'], 'complete')
        result = quality.check_compile(*args, relation_probes=self.compile_probes())
        self.assertEqual((result['status'], result['review_reason']), ('needs_review', 'relation_record_changed'))
        self.assertEqual(len(result['relation_rows']), 2)
        code, observed = self.run_cli('vault_compile_quality.py', request={
            'sources': [self.source], 'targets': [self.target], 'required_claims': [],
            'relation_probes': self.compile_probes()})
        self.assertEqual((code, observed['status']), (1, 'needs_review'))
        for field, path in [('source_path', 'private/secret.md'), ('source_path', self.target['path']),
                            ('target_path', self.source['path']), ('target_path', 'unselected.md')]:
            probes = self.compile_probes()
            probes[0][field] = path
            with self.subTest(field=field, path=path), self.assertRaises(quality.QualityError):
                quality.check_compile(*args, relation_probes=probes)

    def test_compile_rechecks_current_source_and_policy_after_probe_analysis(self):
        self.assertIsNotNone(relations, 'authored record probe module is not implemented')
        real = relations.check_relation_records
        for change in ('source', 'policy'):
            with self.subTest(change=change):
                self.setUp()
                def alter(original, candidate, probes):
                    result = real(original, candidate, probes)
                    path = self.vault / (self.source['path'] if change == 'source' else '00-meta/vault-config.json')
                    path.write_bytes(path.read_bytes() + b'\n')
                    return result
                with patch.object(relations, 'check_relation_records', side_effect=alter), self.assertRaises(quality.QualityError):
                    quality.check_compile(self.vault, [self.source], [self.target], [], relation_probes=self.compile_probes())

    def test_proposal_sidecar_is_read_only_and_keeps_explicit_apply_approval(self):
        self.write('CLAUDE.md', ORIGINAL)
        self.write('00-meta/candidate.md', SWAPPED)
        self.write('00-meta/probes.json', json.dumps({'schema_version': 1, 'probes': PROBES}))
        code, created = self.run_cli('vault_proposals.py', 'propose', '--target', 'CLAUDE.md', '--candidate',
                                     '00-meta/candidate.md', '--lesson', 'L-001', '--summary', 'Synthetic quantity review')
        self.assertEqual(code, 0, created)
        snapshot = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()}
        code, result = self.run_cli('vault_proposals.py', 'preserve-check', created['id'], '--relation-probes', '00-meta/probes.json')
        self.assertEqual((code, result.get('status')), (1, 'needs_review'))
        self.assertEqual(result['review_reasons'], ['relation_record_changed'])
        self.assertEqual(snapshot, {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()})
        code, refused = self.run_cli('vault_proposals.py', 'apply', created['id'])
        self.assertEqual((code, refused), (1, {'error': 'explicit_approval_required'}))

    def test_sidecar_path_schema_bounds_and_change_are_rejected(self):
        self.assertTrue(callable(getattr(proposals, 'read_relation_manifest', None)), 'sidecar reader is not implemented')
        store = proposals.Store(self.vault)
        for relative in ('../outside.json', 'private/probes.json', '00-meta/proposals/probes.json',
                         '00-meta/evidence/probes.json', '00-meta/.agentic-vault/runtime/probes.json',
                         '00-meta/probes.md'):
            with self.subTest(relative=relative), self.assertRaises((proposals.ProposalError, ValueError)):
                proposals.read_relation_manifest(store, relative)
        for value in ({'schema_version': True, 'probes': PROBES}, {'schema_version': 1, 'probes': []},
                      {'schema_version': 1, 'probes': PROBES, 'unknown': 1}):
            self.write('00-meta/probes.json', json.dumps(value))
            with self.subTest(value=value), self.assertRaises(proposals.ProposalError):
                proposals.read_relation_manifest(store, '00-meta/probes.json')
        self.write('00-meta/probes.json', ' ' * (64 * 1024 + 1))
        with self.assertRaises(proposals.ProposalError):
            proposals.read_relation_manifest(store, '00-meta/probes.json')
        for raw in ('{"schema_version":1,"schema_version":1,"probes":[]}',
                    '{"schema_version":NaN,"probes":[]}', '{"schema_version":Infinity,"probes":[]}'):
            self.write('00-meta/probes.json', raw)
            with self.subTest(raw=raw), self.assertRaises(proposals.ProposalError):
                proposals.read_relation_manifest(store, '00-meta/probes.json')
        self.write('00-meta/probes.json', json.dumps({'schema_version': 1, 'probes': PROBES}))
        os.link(self.vault / '00-meta/probes.json', self.vault / '00-meta/linked.json')
        with self.assertRaises(proposals.ProposalError):
            proposals.read_relation_manifest(store, '00-meta/linked.json')

    def test_proposal_opt_in_rechecks_manifest_receipt_and_policy_after_analysis(self):
        real = proposals.preserve_check
        for change, expected in [('manifest', 'relation_manifest_changed'), ('receipt', 'proposal_changed'),
                                 ('policy', 'configuration_changed')]:
            with self.subTest(change=change):
                self.setUp()
                self.write('CLAUDE.md', ORIGINAL)
                self.write('00-meta/candidate.md', SWAPPED)
                self.write('00-meta/probes.json', json.dumps({'schema_version': 1, 'probes': PROBES}))
                store = proposals.Store(self.vault)
                with store.lock():
                    created = proposals.propose(store, SimpleNamespace(target='CLAUDE.md', candidate='00-meta/candidate.md',
                                                                       lesson='L-001', summary='Synthetic review'))
                def alter(receipt, claims, removals, additions, **kwargs):
                    result = real(receipt, claims, removals, additions, **kwargs)
                    if change == 'receipt':
                        current = store.load(created['id'])
                        current['status'] = 'rejected'
                        current['events'].append(proposals.event('rejected', reason='Synthetic external edit'))
                        store.save(current)
                    else:
                        path = self.vault / ('00-meta/probes.json' if change == 'manifest' else '00-meta/vault-config.json')
                        path.write_bytes(path.read_bytes() + b'\n')
                    return result
                output = io.StringIO()
                with patch.object(proposals, 'preserve_check', side_effect=alter), redirect_stdout(output):
                    code = proposals.main(['--vault', str(self.vault), 'preserve-check', created['id'],
                                           '--relation-probes', '00-meta/probes.json'])
                self.assertEqual((code, json.loads(output.getvalue())), (1, {'error': expected}))

    def test_compile_unknown_field_and_empty_probes_are_errors(self):
        request = {'sources': [self.source], 'targets': [self.target], 'required_claims': []}
        for extra in ({'relation_probes': []}, {'relation_probe': PROBES}, {'relation_probes': True}):
            with self.subTest(extra=extra):
                code, result = self.run_cli('vault_compile_quality.py', request={**request, **extra})
                self.assertEqual((code, result['status'], result['authority']), (2, 'error', 'none'))


if __name__ == '__main__':
    unittest.main()
