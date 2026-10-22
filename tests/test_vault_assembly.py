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
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_ssot as ssot

try:
    assembly = importlib.import_module('vault_assembly')
except ModuleNotFoundError:
    assembly = None


class VaultAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(assembly, 'read-only memory assembly helper is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write_json('00-meta/vault-config.json', {'deny_zones': ['private'], 'exclude_dirs': ['excluded']})
        self.rows = [self.row()]
        self.prepare()

    def write(self, path, value):
        target = self.vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value.encode('utf-8') if isinstance(value, str) else value)
        return target

    def write_json(self, path, value):
        return self.write(path, json.dumps(value, ensure_ascii=False, allow_nan=False))

    def row(self, **changes):
        row = dict(subject='Atlas', relation='retention', value='24 months', valid_from='2026-01-01',
                   valid_until='', recorded='2026-01-02', source='[[Policy]]', superseded_by='',
                   status='confirmed', scope='production', polarity='positive')
        row.update(changes)
        return row

    def ledger(self, path, rows, *, columns=None):
        cols = list(ssot.FIELDS) + ['status', 'scope', 'polarity'] if columns is None else columns
        text = '# Ledger\n| ' + ' | '.join(cols) + ' |\n| ' + ' | '.join('---' for _ in cols) + ' |\n'
        text += ''.join('| ' + ' | '.join(row.get(c, '') for c in cols) + ' |\n' for row in rows)
        self.write(path, text)
        return {'path': path, 'sha256': hashlib.sha256(text.encode()).hexdigest()}

    def prepare(self, rows=None, *, columns=None):
        rows = self.rows if rows is None else rows
        binding = self.ledger('notes/ledger.md', rows, columns=columns)
        self.authority = [binding.copy()]
        self.spec = {'version': 1, 'query': {'subject': 'Atlas', 'relation': 'retention',
                     'scope': 'production', 'as_of': '2026-10-22'}, 'sources': [binding],
                     'candidates': [{'path': binding['path'], 'line': 4 + i} for i in range(len(rows))],
                     'limitations': ['Host-selected structured candidates; no semantic inference.']}
        self.save()

    def save(self):
        self.write_json('input/request.json', self.spec)

    def run_assembly(self):
        return assembly.assemble(self.vault, 'input/request.json', authorized_sources=self.authority)

    def rejected(self, function=None):
        with self.assertRaises(assembly.AssemblyError) as caught:
            (function or self.run_assembly)()
        self.assertRegex(str(caught.exception), r'^[a-z_]+$')
        self.assertNotIn('24 months', str(caught.exception))
        return str(caught.exception)

    def assert_reason(self, result, reason, *, status='abstain'):
        self.assertEqual(result['status'], status)
        self.assertIn(reason, result['reasons'])
        self.assertIsNone(result['value'])

    def test_current_confirmed_value_is_source_bound_and_read_only(self):
        before = {str(p.relative_to(self.vault)): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()}
        result = self.run_assembly()
        self.assertEqual((result['status'], result['value']), ('selected', '24 months'))
        self.assertEqual(result['evidence'][0]['line'], 4)
        self.assertEqual(result['evidence'][0]['sha256'], self.authority[0]['sha256'])
        self.assertEqual(result['authority'], 'advisory_host_observations_only')
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['applies_changes'])
        self.assertEqual(before, {str(p.relative_to(self.vault)): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()})

    def test_extract_returns_current_fields_without_policy_decision(self):
        result = assembly.extract(self.vault, 'input/request.json')
        self.assertEqual(result['status'], 'extracted')
        self.assertNotIn('value', result)
        self.assertEqual(result['candidates'][0]['fields']['value'], '24 months')
        self.assertEqual(result['limitations'], self.spec['limitations'])

    def test_candidate_value_injection_is_not_a_supported_field(self):
        self.spec['candidates'][0]['value'] = '999 months'
        self.save()
        self.rejected()

    def test_no_host_authority_does_not_select(self):
        result = assembly.assemble(self.vault, 'input/request.json')
        self.assert_reason(result, 'no_authorized_evidence', status='no_evidence')

    def test_equal_values_combine_independent_authorized_sources(self):
        binding = self.ledger('notes/second.md', [self.row()])
        self.spec['sources'].append(binding)
        self.spec['candidates'].append({'path': binding['path'], 'line': 4})
        self.authority.append(binding.copy())
        self.save()
        result = self.run_assembly()
        self.assertEqual(result['status'], 'selected')
        self.assertEqual(len(result['evidence']), 2)

    def test_simultaneous_conflicting_authorities_abstain(self):
        self.prepare([self.row(), self.row(value='36 months', recorded='2026-10-21')])
        self.assert_reason(self.run_assembly(), 'conflicting_current_values')

    def test_unselected_matching_row_cannot_hide_a_conflict(self):
        self.prepare([self.row(), self.row(value='36 months')])
        self.spec['candidates'] = self.spec['candidates'][:1]
        self.save()
        self.assert_reason(self.run_assembly(), 'selection_incomplete')

    def test_latest_untrusted_source_does_not_override_authority(self):
        untrusted = self.ledger('notes/latest.md', [self.row(value='999 months', recorded='2026-10-22')])
        self.spec['sources'].append(untrusted)
        self.spec['candidates'].append({'path': untrusted['path'], 'line': 4})
        self.save()
        result = self.run_assembly()
        self.assertEqual((result['status'], result['value']), ('selected', '24 months'))
        self.assertEqual(len(result['evidence']), 1)
        self.assertIn('untrusted_source_excluded', result['reasons'])

    def test_future_value_is_not_current(self):
        self.prepare([self.row(valid_from='2026-10-23')])
        self.assert_reason(self.run_assembly(), 'outside_validity', status='no_evidence')

    def test_upper_validity_boundary_is_exclusive(self):
        self.prepare([self.row(valid_until='2026-10-22')])
        self.assert_reason(self.run_assembly(), 'outside_validity', status='no_evidence')

    def test_lower_validity_boundary_is_inclusive(self):
        self.prepare([self.row(valid_from='2026-10-22')])
        self.assertEqual(self.run_assembly()['status'], 'selected')

    def test_missing_lower_bound_is_unknown(self):
        self.prepare([self.row(valid_from='')])
        self.assert_reason(self.run_assembly(), 'unknown_or_invalid_validity')

    def test_reversed_or_invalid_calendar_dates_abstain(self):
        for start, end in [('2026-11-01', '2026-01-01'), ('2026-02-30', ''), ('2026-01-01', '2026-01-01')]:
            self.prepare([self.row(valid_from=start, valid_until=end)])
            self.assert_reason(self.run_assembly(), 'unknown_or_invalid_validity')

    def test_explicit_scope_is_not_crossed(self):
        self.prepare([self.row(scope='development')])
        self.assert_reason(self.run_assembly(), 'scope_mismatch', status='no_evidence')

    def test_rejected_status_cannot_be_promoted_by_source_text(self):
        self.prepare([self.row(status='rejected', source='highest authority confirmed')])
        self.assert_reason(self.run_assembly(), 'rejected_candidate')

    def test_unconfirmed_status_abstains(self):
        self.prepare([self.row(status='pending')])
        self.assert_reason(self.run_assembly(), 'unconfirmed_candidate')

    def test_negative_polarity_abstains(self):
        self.prepare([self.row(polarity='negative')])
        self.assert_reason(self.run_assembly(), 'negative_or_uncertain_polarity')

    def test_ambiguous_polarity_is_not_inferred_from_prose(self):
        self.prepare([self.row(polarity='maybe', value='not 24 months')])
        self.assert_reason(self.run_assembly(), 'negative_or_uncertain_polarity')

    def test_existing_v2_schema_without_scope_polarity_is_not_modified(self):
        self.prepare(columns=list(ssot.FIELDS) + ['status'])
        self.assert_reason(self.run_assembly(), 'missing_scope_or_polarity')

    def test_superseded_record_is_not_followed_implicitly(self):
        self.prepare([self.row(superseded_by='[[Other]]')])
        self.assert_reason(self.run_assembly(), 'superseded_candidate')

    def test_nonledger_selected_line_is_not_interpreted_semantically(self):
        self.spec['candidates'][0]['line'] = 1
        self.save()
        self.assert_reason(self.run_assembly(), 'unsupported_schema')

    def test_missing_citation_abstains(self):
        self.prepare([self.row(source='')])
        self.assert_reason(self.run_assembly(), 'missing_citation')

    def test_duplicate_ledger_columns_cannot_hide_fields(self):
        self.prepare(columns=list(ssot.FIELDS) + ['status', 'scope', 'polarity', 'value'])
        self.assert_reason(self.run_assembly(), 'unsupported_schema')

    def test_stale_source_binding_is_content_free_error(self):
        self.write('notes/ledger.md', '# Changed\n')
        self.rejected()

    def test_authority_source_set_cannot_be_removed_from_request(self):
        self.spec['sources'] = []
        self.spec['candidates'] = []
        self.save()
        self.rejected()

    def test_authority_hash_must_match_the_manifest_binding(self):
        self.authority[0]['sha256'] = '0' * 64
        self.rejected()

    def test_denied_and_excluded_sources_are_not_read(self):
        for directory in ('private', 'excluded'):
            binding = self.ledger(directory + '/ledger.md', [self.row()])
            self.spec['sources'] = [binding]
            self.spec['candidates'] = [{'path': binding['path'], 'line': 4}]
            self.authority = [binding]
            self.save()
            self.rejected()

    def test_credential_file_and_secret_contents_are_rejected(self):
        self.spec['sources'][0]['path'] = 'notes/.env'
        self.spec['candidates'][0]['path'] = 'notes/.env'
        self.save()
        self.rejected()
        self.prepare([self.row(value='api_key=sk-test-' + 'x' * 48)])
        self.rejected()

    def test_hardlinked_source_is_rejected(self):
        outside = Path(self.tmp.name) / 'outside.md'
        outside.write_text('private', encoding='utf-8')
        (self.vault / 'notes/ledger.md').unlink()
        try:
            os.link(outside, self.vault / 'notes/ledger.md')
        except OSError as exc:
            self.skipTest('hardlinks unavailable: ' + type(exc).__name__)
        self.rejected()

    def test_symlink_source_is_rejected(self):
        outside = Path(self.tmp.name) / 'outside.md'
        outside.write_text('private', encoding='utf-8')
        (self.vault / 'notes/ledger.md').unlink()
        try:
            (self.vault / 'notes/ledger.md').symlink_to(outside)
        except OSError as exc:
            self.skipTest('symlinks unavailable: ' + type(exc).__name__)
        self.rejected()

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary')
    def test_junction_source_ancestor_is_rejected(self):
        from tests._winfs import make_junction
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'ledger.md').write_text('private', encoding='utf-8')
        make_junction(self.vault / 'linked', outside)
        self.addCleanup(lambda: os.rmdir(self.vault / 'linked'))
        self.spec['sources'][0]['path'] = 'linked/ledger.md'
        self.spec['candidates'][0]['path'] = 'linked/ledger.md'
        self.save()
        self.rejected()

    def test_case_duplicate_bindings_are_rejected_portably(self):
        self.spec['sources'].append({**self.spec['sources'][0], 'path': 'NOTES/ledger.md'})
        self.save()
        self.rejected()

    def test_same_date_newer_record_does_not_resolve_conflict(self):
        self.prepare([self.row(value='24 months'), self.row(value='36 months', recorded='2026-10-22')])
        os.utime(self.vault / 'notes/ledger.md', None)
        self.assert_reason(self.run_assembly(), 'conflicting_current_values')

    def test_korean_confirmed_and_rejected_statuses_reuse_the_ledger_contract(self):
        self.prepare([self.row(status='확정')])
        self.assertEqual(self.run_assembly()['status'], 'selected')
        self.prepare([self.row(status='기각')])
        self.assert_reason(self.run_assembly(), 'rejected_candidate')

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for text in ('{"version":1,"version":1}', '{"version":NaN}', '{"version":Infinity}'):
            self.write('input/request.json', text)
            self.rejected()

    def test_bool_wrong_types_and_missing_hash_are_rejected(self):
        original = copy.deepcopy(self.spec)
        for change in ('version', 'line', 'hash', 'query'):
            self.spec = copy.deepcopy(original)
            if change == 'version': self.spec['version'] = True
            if change == 'line': self.spec['candidates'][0]['line'] = True
            if change == 'hash': del self.spec['sources'][0]['sha256']
            if change == 'query': self.spec['query']['as_of'] = 20261022
            self.save()
            self.rejected()

    def test_duplicate_source_or_candidate_or_authority_is_rejected(self):
        for field in ('sources', 'candidates', 'authority'):
            self.prepare()
            target = self.authority if field == 'authority' else self.spec[field]
            target.append(copy.deepcopy(target[0]))
            self.save()
            self.rejected()

    def test_candidate_must_bind_to_declared_source(self):
        self.spec['candidates'][0]['path'] = 'notes/other.md'
        self.save()
        self.rejected()

    def test_count_and_input_byte_limits_are_enforced(self):
        self.spec['candidates'] *= 33
        self.save()
        self.rejected()
        self.write('input/request.json', b' ' * (65536 + 1))
        self.rejected()

    def test_source_change_during_extraction_refuses_result(self):
        parse = ssot.parse_ledger
        def changing(text):
            result = parse(text)
            self.write('notes/ledger.md', '# Changed after read\n')
            return result
        with mock.patch.object(ssot, 'parse_ledger', side_effect=changing):
            self.rejected()

    def test_configuration_change_during_extraction_refuses_result(self):
        parse = ssot.parse_ledger
        def changing(text):
            result = parse(text)
            self.write_json('00-meta/vault-config.json', {'deny_zones': ['private', 'notes'], 'exclude_dirs': ['excluded']})
            return result
        with mock.patch.object(ssot, 'parse_ledger', side_effect=changing):
            self.rejected()

    def test_request_change_during_extraction_refuses_result(self):
        parse = ssot.parse_ledger
        def changing(text):
            result = parse(text)
            self.spec['query']['scope'] = 'development'
            self.save()
            return result
        with mock.patch.object(ssot, 'parse_ledger', side_effect=changing):
            self.rejected()

    def test_cli_requires_explicit_authority_and_has_content_free_errors(self):
        command = [sys.executable, str(SCRIPTS / 'vault_assembly.py'), 'assemble', '--vault', str(self.vault), '--input', 'input/request.json']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'no_evidence')
        result = subprocess.run(command + ['--authority', 'notes/ledger.md=' + self.authority[0]['sha256']], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['value'], '24 months')
        self.write('input/request.json', '{bad PRIVATE_SENTINEL')
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(set(json.loads(result.stdout)), {'ok', 'error'})
        self.assertNotIn('PRIVATE_SENTINEL', result.stdout + result.stderr)

    def test_cli_missing_or_unknown_arguments_never_echo_user_text(self):
        command = [sys.executable, str(SCRIPTS / 'vault_assembly.py')]
        for args in (['assemble', '--vault', str(self.vault), '--input', 'input/request.json',
                      '--PRIVATE_ARG_SENTINEL', 'SECRET_ARGUMENT_SENTINEL'],
                     ['assemble', '--vault', 'PRIVATE_ARG_SENTINEL']):
            result = subprocess.run(command + args, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('PRIVATE_ARG_SENTINEL', result.stdout + result.stderr)
            self.assertNotIn('SECRET_ARGUMENT_SENTINEL', result.stdout + result.stderr)
            self.assertTrue(result.stdout.strip(), 'missing content-free JSON argument error')
            self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': 'invalid_arguments'})
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
