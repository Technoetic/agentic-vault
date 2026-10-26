from __future__ import annotations

import hashlib
import io
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    ssot = importlib.import_module('vault_ssot')
except ModuleNotFoundError:
    ssot = None

FIELDS = ['subject', 'relation', 'value', 'valid_from', 'valid_until', 'recorded', 'source', 'superseded_by', 'status']
GOLDEN = Path(__file__).resolve().parent / 'fixtures' / 'v0221_default_outputs.json'
SUPPORT_GOLDEN = Path(__file__).resolve().parent / 'fixtures' / 'v0300_check_support_default.json'
SUPPORT_CODES = {'ssot-source-unsupported', 'ssot-source-unresolved'}
TABLE_ONLY_KEYS = ('rows_supported_ledger_table_only', 'ledger_table_only')
PERIOD_KEYS = ('normalized_periods', 'period_convention')


def ledger(rows, fields=FIELDS, newline='\n'):
    return newline.join(['| ' + ' | '.join(fields) + ' |',
                         '| ' + ' | '.join(['---'] * len(fields)) + ' |',
                         *['| ' + ' | '.join(row) + ' |' for row in rows]]) + newline


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LedgerParsingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')

    def row(self, value='12', start='2025-01-01', until='', source='[[Evidence]]', status='confirmed', successor=''):
        return ['Atlas', 'retention', value, start, until, '2026-10-07', source, successor, status]

    def test_disjoint_history_is_not_a_contradiction(self):
        text = ledger([self.row('12', until='2026-01-01'), self.row('24', start='2026-01-01')])
        self.assertEqual(ssot.check_ledger(text), [])
        parsed = ssot.parse_ledger(text)
        self.assertEqual(parsed['tables'][0]['schema_version'], 2)
        self.assertEqual(len(parsed['tables'][0]['rows']), 2)

    def test_real_overlapping_contradictions_warn_and_same_value_does_not(self):
        text = ledger([self.row('12', until='2026-07-01'), self.row('24', start='2026-01-01')])
        warnings = ssot.check_ledger(text, path='notes/facts.md')
        conflicts = [w for w in warnings if w['code'] == 'ssot-temporal-conflict']
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['severity'], 'warning')
        self.assertEqual(conflicts[0]['path'], 'notes/facts.md')
        self.assertEqual(ssot.check_ledger(ledger([self.row(), self.row()])), [])

    def test_unknown_bounds_do_not_invent_a_temporal_contradiction(self):
        warnings = ssot.check_ledger(ledger([self.row('12', start=''), self.row('24', start='')]))
        self.assertNotIn('ssot-temporal-conflict', [w['code'] for w in warnings])
        self.assertIn('ssot-unknown-validity', [w['code'] for w in warnings])

    def test_different_open_ended_values_warn_even_when_lower_dates_are_unknown(self):
        warnings = ssot.check_ledger(ledger([self.row('12', start=''), self.row('24', start='')]))
        self.assertIn('ssot-open-ended-conflict', [w['code'] for w in warnings])
        self.assertNotIn('ssot-temporal-conflict', [w['code'] for w in warnings])

    def test_brief_valid_to_alias_is_accepted_as_valid_until(self):
        headers = FIELDS.copy()
        headers[4] = 'valid_to'
        text = ledger([self.row('12', until='2026-01-01'), self.row('24', start='2026-01-01')], fields=headers)
        self.assertEqual(ssot.check_ledger(text), [])
        self.assertEqual(ssot.parse_ledger(text)['tables'][0]['rows'][0]['fields']['valid_until'], '2026-01-01')

    def test_invalid_or_reversed_dates_warn_without_inferring_values(self):
        for start, until, recorded in [('2026-02-30', '', '2026-10-07'),
                                       ('2026-07-01', '2026-07-01', '2026-10-07'),
                                       ('2026-07-02', '2026-07-01', '2026-10-07'),
                                       ('2026-07-01', '', 'tomorrow')]:
            row = self.row(start=start, until=until)
            row[5] = recorded
            with self.subTest(row=row):
                warnings = ssot.check_ledger(ledger([row]))
                self.assertTrue(any(w['code'] == 'ssot-invalid-date' for w in warnings))

    def test_confirmed_source_required_with_explicit_legacy_statuses(self):
        for state in ['confirmed', '확정', '✅확정']:
            with self.subTest(state=state):
                warnings = ssot.check_ledger(ledger([self.row(source='', status=state)]))
                self.assertIn('ssot-confirmed-source-missing', [w['code'] for w in warnings])
        self.assertNotIn('ssot-confirmed-source-missing', [w['code'] for w in ssot.check_ledger(ledger([self.row(source='', status='pending')]))])

    def test_superseded_metadata_does_not_erase_actual_temporal_conflicts(self):
        rows = [self.row('12', successor='fact-2'), self.row('24', start='2026-01-01')]
        self.assertIn('ssot-temporal-conflict', [w['code'] for w in ssot.check_ledger(ledger(rows))])

    def test_invalid_recorded_date_does_not_hide_known_overlapping_validity(self):
        row = self.row('12')
        row[5] = 'unknown date'
        warnings = ssot.check_ledger(ledger([row, self.row('24')]))
        self.assertIn('ssot-invalid-date', [w['code'] for w in warnings])
        self.assertIn('ssot-temporal-conflict', [w['code'] for w in warnings])

    def test_confirmed_section_is_explicit_status_when_column_is_absent(self):
        fields = FIELDS[:-1]
        row = self.row(source='')[:-1]
        warnings = ssot.check_ledger('## ✅확정\n\n' + ledger([row], fields=fields))
        self.assertIn('ssot-confirmed-source-missing', [w['code'] for w in warnings])
        warnings = ssot.check_ledger('## ⚠️충돌\n\n' + ledger([row], fields=fields))
        self.assertNotIn('ssot-confirmed-source-missing', [w['code'] for w in warnings])

    def test_malformed_parsed_input_and_path_reject_with_safe_error(self):
        for value in [{}, {'version': 1, 'tables': 'not an array', 'diagnostics': []},
                      {'version': 1, 'tables': [{'rows': [{}]}], 'diagnostics': []}]:
            with self.subTest(value=value):
                with self.assertRaises(ssot.SSOTError):
                    ssot.check_ledger(value)
        with self.assertRaises(ssot.SSOTError):
            ssot.check_ledger('', path=['wrong'])

    def test_mutated_section_status_and_other_parsed_types_fail_with_safe_error(self):
        original = ledger([self.row()[:-1]], fields=FIELDS[:-1])
        for section_status in [['confirmed'], {'status': 'confirmed'}, None, True, 7, 'forged', 'x' * 4097]:
            parsed = ssot.parse_ledger(original)
            parsed['tables'][0]['section_status'] = section_status
            with self.subTest(section_status=section_status):
                with self.assertRaisesRegex(ssot.SSOTError, 'invalid_parsed_ledger'):
                    ssot.check_ledger(parsed)
        for key, value in [('missing_fields', [None]), ('duplicate_fields', [[]]),
                           ('schema_version', True), ('rows', [{'line': 3, 'fields': []}])]:
            parsed = ssot.parse_ledger(original)
            parsed['tables'][0][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ssot.SSOTError):
                    ssot.check_ledger(parsed)
        parsed = ssot.parse_ledger(original)
        del parsed['tables'][0]['section_status']
        self.assertEqual(ssot.check_ledger(parsed), [])
        parsed['tables'][0]['section_status'] = 'confirmed'
        parsed['tables'][0]['rows'][0]['fields']['source'] = ''
        self.assertIn('ssot-confirmed-source-missing', [w['code'] for w in ssot.check_ledger(parsed)])

    def test_different_subject_or_relation_does_not_conflict(self):
        row = self.row('24')
        row[0] = 'Birch'
        self.assertEqual(ssot.check_ledger(ledger([self.row(), row])), [])
        row[0], row[1] = 'Atlas', 'price'
        self.assertEqual(ssot.check_ledger(ledger([self.row(), row])), [])

    def test_legacy_korean_and_english_alias_headers_and_escaped_pipes(self):
        headers = ['주체', '항목', '값', '시작일', '종료일', '기록일', '출처', '대체 항목', '상태']
        row = self.row(value=r'first\|second', source=r'[[Evidence\|alias]]')
        parsed = ssot.parse_ledger(ledger([row], fields=headers))
        result = parsed['tables'][0]['rows'][0]['fields']
        self.assertEqual(result['value'], 'first|second')
        self.assertEqual(result['source'], '[[Evidence|alias]]')
        english = ['entity', 'predicate', 'fact', 'from', 'until', 'recorded_at', 'evidence', 'superseded', 'state']
        self.assertEqual(ssot.parse_ledger(ledger([self.row()], fields=english))['tables'][0]['rows'][0]['fields']['relation'], 'retention')

    def test_fenced_and_indented_examples_are_not_ledgers(self):
        example = ledger([self.row()])
        text = '```md\n' + example + '```\n\n' + ''.join('    ' + line for line in example.splitlines(keepends=True))
        self.assertEqual(ssot.parse_ledger(text)['tables'], [])

    def test_duplicate_aliases_and_malformed_rows_are_visible(self):
        text = ledger([['Atlas', 'r', 'v', 'extra']], fields=['subject', 'relation', 'value'])
        self.assertIn('ssot-invalid-row', [w['code'] for w in ssot.check_ledger(text)])
        text = ledger([['Atlas', 'r', 'v', 'v']], fields=['subject', 'relation', 'value', '값'])
        self.assertIn('ssot-duplicate-column', [w['code'] for w in ssot.check_ledger(text)])

    def test_input_rows_cells_and_columns_are_bounded(self):
        with self.assertRaises(ssot.SSOTError):
            ssot.parse_ledger('x' * (256 * 1024 + 1))
        with self.assertRaises(ssot.SSOTError):
            ssot.parse_ledger(ledger([self.row()] * 257))
        row = self.row('x' * 4097)
        with self.assertRaises(ssot.SSOTError):
            ssot.parse_ledger(ledger([row]))

    def test_template_has_v2_schema_and_no_invented_facts(self):
        template = Path(__file__).resolve().parents[1] / 'assets/templates/ssot-ledger.md'
        parsed = ssot.parse_ledger(template.read_text(encoding='utf-8'))
        self.assertEqual(len(parsed['tables']), 1)
        self.assertEqual(parsed['tables'][0]['schema_version'], 2)
        self.assertEqual(parsed['tables'][0]['rows'], [])
        self.assertEqual(ssot.check_ledger(parsed), [])


class LedgerMigrationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write('00-meta/vault-config.json', json.dumps({'deny_zones': ['private'], 'exclude_dirs': ['excluded']}).encode())
        self.original = ('---\r\ntitle: "Facts"\r\nupdated: 2026-01-01\r\n---\r\nHuman text 한글\r\n\r\n' +
                         ledger([['Atlas', 'retention', r'12\|24', '[[Evidence]]', 'confirmed', 'keep  exact']],
                                ['주체', '항목', '값', '출처', '상태', 'Original Extra'], '\r\n') +
                         '\r\nKeep this prose  unchanged\n').encode()
        self.path = self.write('notes/facts.md', self.original)

    def write(self, relative, data):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def test_prepare_is_read_only_and_missing_dates_stay_empty(self):
        before = sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob('*'))
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        self.assertEqual(proposal['status'], 'prepared')
        self.assertEqual(proposal['base_sha256'], sha(self.original))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(before, sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob('*')))
        self.assertEqual(proposal['authority'], 'proposal_only_not_approval_or_truth')
        for field in ['valid_from', 'valid_until', 'recorded', 'superseded_by']:
            self.assertEqual(proposal['preserved_rows'][0]['fields'][field], '')

    def test_apply_requires_explicit_boolean_approval(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        for approval in [False, 'yes', 1, None]:
            with self.subTest(approval=approval):
                with self.assertRaisesRegex(ssot.SSOTError, 'approval_required'):
                    ssot.apply_migration(self.vault, proposal, approve=approval)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_apply_preserves_original_values_extra_columns_and_unrelated_bytes(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        result = ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertEqual(result['status'], 'applied')
        final = self.path.read_bytes()
        self.assertTrue(final.startswith(self.original[:self.original.index(b'|')]))
        self.assertTrue(final.endswith(b'\r\nKeep this prose  unchanged\n'))
        self.assertIn(b'12\\|24', final)
        self.assertIn(b'keep  exact', final)
        self.assertIn(b'Original Extra', final)
        self.assertNotIn(b'|\n', final)
        parsed = ssot.parse_ledger(final.decode())
        self.assertEqual(parsed['tables'][0]['schema_version'], 2)
        self.assertEqual(parsed['tables'][0]['rows'][0]['fields']['value'], '12|24')
        self.assertEqual(result['authority'], 'migration_accounting_not_fact_verification')

    def test_already_v2_is_unchanged_and_idempotent(self):
        ssot.apply_migration(self.vault, ssot.prepare_migration(self.vault, 'notes/facts.md'), approve=True)
        current = self.path.read_bytes()
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        self.assertEqual(proposal['status'], 'unchanged')
        self.assertEqual(proposal['edits'], [])
        self.assertEqual(ssot.apply_migration(self.vault, proposal, approve=True)['status'], 'unchanged')
        self.assertEqual(self.path.read_bytes(), current)

    def test_stale_base_blocks_write(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        self.path.write_bytes(self.original + b'Other writer\n')
        with self.assertRaisesRegex(ssot.SSOTError, 'stale_base'):
            ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertTrue(self.path.read_bytes().endswith(b'Other writer\n'))

    def test_changed_configuration_blocks_write(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        self.write('00-meta/vault-config.json', b'{"deny_zones": ["private", "notes"], "exclude_dirs": []}')
        with self.assertRaises(ssot.SSOTError):
            ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_deny_policy_change_during_final_prepare_read_blocks_publication(self):
        read_note = ssot.state._Policy.read_note
        calls = []
        changed = {'active': False}
        def changing_read(policy, relative):
            calls.append(relative)
            if len(calls) == 2:
                self.write('00-meta/vault-config.json', b'{"deny_zones": ["private", "notes"], "exclude_dirs": []}')
                changed['active'] = True
            return read_note(policy, relative)
        with mock.patch.object(ssot.state._Policy, 'read_note', changing_read):
            with self.assertRaisesRegex(ssot.SSOTError, 'configuration_changed'):
                ssot.prepare_migration(self.vault, 'notes/facts.md')
        self.assertTrue(changed['active'])
        self.assertEqual(calls, ['notes/facts.md', 'notes/facts.md'])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_deny_policy_change_during_final_cli_check_read_is_safe_and_silent(self):
        read_note = ssot.state._Policy.read_note
        calls = []
        def changing_read(policy, relative):
            calls.append(relative)
            if len(calls) == 2:
                self.write('00-meta/vault-config.json', b'{"deny_zones": ["private", "notes"], "exclude_dirs": []}')
            return read_note(policy, relative)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(ssot.state._Policy, 'read_note', changing_read), \
             mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
            result = ssot.main(['--vault', str(self.vault), 'check', '--path', 'notes/facts.md'])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(json.loads(stderr.getvalue()), {'error': 'ssot_operation_unavailable'})
        self.assertEqual(calls, ['notes/facts.md', 'notes/facts.md'])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_forged_or_tampered_proposal_is_not_an_arbitrary_edit_primitive(self):
        for mutation in ['replacement', 'candidate_sha256', 'base_sha256', 'path', 'extra']:
            proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
            if mutation == 'replacement':
                proposal['edits'][0]['replacement'] = 'Delete original value'
            else:
                proposal[mutation] = 'forged'
            with self.subTest(mutation=mutation):
                with self.assertRaises(ssot.SSOTError):
                    ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_bool_integer_equivalence_does_not_accept_changed_proposal_metadata(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        proposal['version'] = True
        with self.assertRaisesRegex(ssot.SSOTError, 'proposal_changed'):
            ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_legacy_aliases_with_canonical_fields_migrate_without_renaming_status(self):
        fields = ['entity', 'predicate', 'fact', 'from', 'until', 'recorded_at', 'evidence', 'superseded', '상태']
        content = ledger([['Atlas', 'retention', '12', '2025-01-01', '', '2026-10-07', '[[Evidence]]', '', 'confirmed']], fields=fields)
        self.path.write_bytes(content.encode())
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        ssot.apply_migration(self.vault, proposal, approve=True)
        current = self.path.read_text(encoding='utf-8')
        self.assertIn('| subject | relation | value |', current)
        self.assertIn('| 상태 |', current)
        self.assertEqual(current.splitlines()[-1], content.splitlines()[-1])

    def test_fence_sibling_tables_and_no_outer_pipes_preserve_every_other_byte(self):
        content = ('before\n```md\n' + ledger([['Example', 'r', 'v']], fields=['subject', 'relation', 'value']) +
                   '```\n\nsubject | relation | value\n--- | --- | ---\nAtlas | r | value\n\n' +
                   '| Name | Note |\n| --- | --- |\n| other | untouched |\nend\n')
        self.path.write_bytes(content.encode())
        ssot.apply_migration(self.vault, ssot.prepare_migration(self.vault, 'notes/facts.md'), approve=True)
        current = self.path.read_text(encoding='utf-8')
        self.assertTrue(current.startswith(content[:content.index('subject | relation')]))
        self.assertTrue(current.endswith(content[content.index('\n\n| Name |'):]))
        self.assertEqual(ssot.parse_ledger(current)['tables'][0]['rows'][0]['fields']['value'], 'value')

    def test_config_generation_change_at_patch_entry_blocks_write(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        patch = ssot.state.patch_note
        def racing_patch(*args, **kwargs):
            self.write('00-meta/vault-config.json', b'{"deny_zones": ["private"], "exclude_dirs": [], "extension": "new generation"}')
            return patch(*args, **kwargs)
        with mock.patch.object(ssot.state, 'patch_note', racing_patch):
            with self.assertRaisesRegex(ssot.SSOTError, 'configuration_changed'):
                ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_nonledger_and_duplicate_columns_refuse_migration(self):
        for content in ['Human prose only\n', ledger([['a', 'r', 'v', 'x']], ['subject', 'relation', 'value', '값'])]:
            self.path.write_text(content, encoding='utf-8')
            with self.assertRaises(ssot.SSOTError):
                ssot.prepare_migration(self.vault, 'notes/facts.md')

    def test_deny_exclude_runtime_nonmarkdown_and_hardlink_are_rejected(self):
        for relative in ['private/facts.md', 'excluded/facts.md', '../outside.md',
                         '00-meta/.agentic-vault/runtime/facts.md', 'notes/facts.json']:
            with self.subTest(path=relative):
                with self.assertRaises(ssot.SSOTError):
                    ssot.prepare_migration(self.vault, relative)
        linked = self.vault / 'notes/link.md'
        os.link(self.path, linked)
        with self.assertRaises(ssot.SSOTError):
            ssot.prepare_migration(self.vault, 'notes/facts.md')

    def test_race_immediately_before_partial_replace_does_not_clobber_other_writer(self):
        proposal = ssot.prepare_migration(self.vault, 'notes/facts.md')
        atomic = ssot.state._atomic
        def racing_atomic(target, data, before_replace, **kwargs):
            self.path.write_bytes(self.original + b'Raced writer\n')
            return atomic(target, data, before_replace, **kwargs)
        with mock.patch.object(ssot.state, '_atomic', racing_atomic):
            with self.assertRaisesRegex(ssot.SSOTError, 'stale_base'):
                ssot.apply_migration(self.vault, proposal, approve=True)
        self.assertTrue(self.path.read_bytes().endswith(b'Raced writer\n'))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_junction_target_is_rejected_without_symlink_privilege(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'facts.md').write_bytes(self.original)
        junction = self.vault / 'junction'
        try:
            make_junction(junction, outside)
        except OSError:
            self.skipTest('junction creation unavailable')
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        with self.assertRaises(ssot.SSOTError):
            ssot.prepare_migration(self.vault, 'junction/facts.md')
        self.assertEqual((outside / 'facts.md').read_bytes(), self.original)

    def test_cli_prepare_and_apply_are_real_bounded_command_paths(self):
        script = str(SCRIPTS / 'vault_ssot.py')
        prepare = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'prepare', '--path', 'notes/facts.md'], capture_output=True)
        self.assertEqual(prepare.returncode, 0, prepare.stderr.decode())
        proposal = json.loads(prepare.stdout)
        self.assertEqual(self.path.read_bytes(), self.original)
        rejected = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'apply'], input=prepare.stdout, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        applied = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'apply', '--approve'], input=json.dumps(proposal).encode(), capture_output=True)
        self.assertEqual(applied.returncode, 0, applied.stderr.decode())
        self.assertEqual(json.loads(applied.stdout)['status'], 'applied')


class SourceSupportTests(unittest.TestCase):
    """Warning-only check-support: cited note body versus ledger value literal."""

    EVIDENCE = '---\ntitle: Evidence\nretention: 99\n---\n보존 기간은 12개월이다. [[Atlas|아틀라스]] 대표, **승인** 완료.\n'

    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write('00-meta/vault-config.json', '{"deny_zones": ["private"], "exclude_dirs": ["excluded"]}')
        self.evidence = self.write('notes/Evidence.md', self.EVIDENCE)

    def write(self, relative, text):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode('utf-8'))
        return target

    def support(self, rows, preamble=''):
        """Rows are (value, source) pairs; without a preamble the first row is line 3."""
        self.write('notes/facts.md', preamble + ledger(
            [['Atlas', 'retention', value, '2025-01-01', '', '2026-10-07', source, '', 'confirmed'] for value, source in rows]))
        return ssot.check_support(self.vault, 'notes/facts.md')

    @staticmethod
    def found(result):
        return [(w['line'], w['code'], w['details'].get('reason')) for w in result['warnings']]

    def test_supported_value_binds_the_cited_revision_without_warnings(self):
        result = self.support([('12개월', '[[Evidence]]')])
        self.assertEqual(result['warnings'], [])
        self.assertEqual((result['rows_checked'], result['rows_supported'], result['rows_skipped']), (1, 1, 0))
        data = self.evidence.read_bytes()
        self.assertEqual(result['cited_notes'], [{'path': 'notes/Evidence.md', 'sha256': sha(data), 'bytes': len(data)}])
        self.assertEqual(result['source_sha256'], sha((self.vault / 'notes/facts.md').read_bytes()))
        self.assertEqual(result['incomplete'], [])
        self.assertEqual(result['authority'], 'warning_only_not_fact_verification')

    def test_unsupported_value_warns_without_echoing_note_content(self):
        result = self.support([('24개월', '[[Evidence]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unsupported', None)])
        self.assertEqual(result['warnings'][0]['severity'], 'warning')
        self.assertEqual(result['warnings'][0]['details'], {'cited': ['notes/Evidence.md']})
        self.assertEqual(result['rows_unsupported'], 1)
        self.assertNotIn('보존', json.dumps(result, ensure_ascii=False))

    def test_number_literals_need_boundaries_and_frontmatter_is_not_body(self):
        for value, supported in [('12', 1), ('12개', 1), ('2', 0), ('1', 0), ('112개월', 0), ('99', 0)]:
            with self.subTest(value=value):
                self.assertEqual(self.support([(value, '[[Evidence]]')])['rows_supported'], supported)

    def test_documented_number_examples_are_other_literals(self):
        self.write('notes/Numbers.md', '수량 125개, 금액 1,212원, 비율 12.5%.\n')
        for value in ['12', '21', '2']:
            with self.subTest(value=value):
                self.assertEqual(self.support([(value, '[[Numbers]]')])['rows_unsupported'], 1)
        for value in ['125', '1,212', '12.5', '12.5%']:
            with self.subTest(value=value):
                self.assertEqual(self.support([(value, '[[Numbers]]')])['rows_supported'], 1)

    def test_normalized_markup_and_alias_forms_are_compared(self):
        for value in ['１２개월', 'ATLAS', '[[Atlas]] 대표', '아틀라스 대표', '**승인** 완료', '승인 완료', '`12개월`']:
            with self.subTest(value=value):
                result = self.support([(value, '[[Evidence]]')])
                self.assertEqual((result['rows_supported'], result['warnings']), (1, []))

    def test_missing_note_is_unresolved(self):
        result = self.support([('12개월', '[[Missing]]')])
        self.assertEqual(result['warnings'][0]['details'], {'reference': 'Missing', 'reason': 'not_found'})
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'not_found')])
        self.assertEqual((result['rows_checked'], result['rows_unresolved'], result['cited_notes']), (1, 1, []))

    def test_exact_filename_is_case_sensitive_but_unicode_normalized(self):
        self.write('notes/' + unicodedata.normalize('NFD', '근거노트') + '.md', '보존 12개월\n')
        result = self.support([('12개월', '[[evidence]]'), ('12개월', '[[' + unicodedata.normalize('NFC', '근거노트') + ']]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'not_found')])
        self.assertEqual(result['rows_supported'], 1)

    def test_ambiguous_duplicate_stems_abstain_and_qualified_paths_select(self):
        self.write('a/Dup.md', '12개월\n')
        self.write('b/Dup.md', '24개월\n')
        result = self.support([('12개월', '[[Dup]]'), ('12개월', '[[a/Dup]]'), ('24개월', '[[b/Dup.md]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'ambiguous')])
        self.assertEqual(result['warnings'][0]['details'], {'reference': 'Dup', 'reason': 'ambiguous',
                                                            'candidate_count': 2, 'candidates': ['a/Dup.md', 'b/Dup.md']})
        self.assertEqual(result['rows_supported'], 2)

    def test_deny_and_exclude_zone_sources_are_never_listed_read_or_described(self):
        self.write('private/Secret.md', '12개월\n')
        self.write('excluded/Hidden.md', '12개월\n')
        touched = []
        real_scandir, real_lstat, real_read = os.scandir, os.lstat, ssot.stable_read

        def scandir(path):
            touched.append(path)
            return real_scandir(path)

        def lstat(path, *args, **kwargs):
            touched.append(path)
            return real_lstat(path, *args, **kwargs)

        def stable_read(resolver, limit, contents=False):
            touched.append(resolver())
            return real_read(resolver, limit, contents=contents)
        rows = [('12개월', '[[Secret]]'), ('12개월', '[[Hidden]]'), ('12개월', '[[private/Secret]]'),
                ('12개월', '[[excluded/Hidden]]'), ('12개월', '`private/Secret.md`')]
        with mock.patch.object(ssot.os, 'scandir', scandir), mock.patch.object(ssot.os, 'lstat', lstat), \
                mock.patch.object(ssot, 'stable_read', stable_read):
            result = self.support(rows)
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'not_found'),
                                              (4, 'ssot-source-unresolved', 'not_found'),
                                              (5, 'ssot-source-unresolved', 'policy'),
                                              (6, 'ssot-source-unresolved', 'policy'),
                                              (7, 'ssot-source-unresolved', 'policy')])
        self.assertTrue(touched)
        # Compare inside the vault only: macOS temporary roots live under /private.
        roots = (self.vault.resolve(), self.vault.absolute())
        for path in touched:
            candidate = Path(os.fspath(path))
            inside = next((candidate.relative_to(root).parts for root in roots
                           if candidate == root or root in candidate.parents), ())
            self.assertFalse({part.casefold() for part in inside} & {'private', 'excluded'}, path)
        self.assertNotIn('candidates', json.dumps(result))
        self.assertEqual(result['cited_notes'], [])

    def test_oversize_note_is_unresolved_and_never_partially_read(self):
        self.write('notes/Big.md', '12개월 ' + 'x' * ssot.MAX_BYTES)
        result = self.support([('12개월', '[[Big]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'size_limit')])
        self.assertEqual(result['cited_notes'], [])

    def test_hardlinked_note_is_unsafe_and_not_read(self):
        try:
            os.link(self.evidence, self.vault / 'notes' / 'Alias.md')
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('hardlink creation unavailable')
        result = self.support([('12개월', '[[Evidence]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'unsafe_file')])
        self.assertEqual(result['cited_notes'], [])

    def test_symlinked_note_is_a_policy_rejection(self):
        try:
            os.symlink(self.evidence, self.vault / 'notes' / 'Linked.md')
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('symlink creation unavailable')
        result = self.support([('12개월', '[[Linked]]'), ('12개월', '[[notes/Linked]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'policy'),
                                              (4, 'ssot-source-unresolved', 'policy')])

    def test_vault_paths_code_spans_and_whole_cell_paths_resolve(self):
        result = self.support([('12개월', '[[notes/Evidence]]'), ('12개월', '`notes/Evidence.md`'),
                               ('12개월', 'notes/Evidence.md'), ('12개월', '[[./Evidence]]'),
                               ('12개월', 'Evidence.md'), ('12개월', '[[Evidence.md#근거\\|별칭]]')])
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['rows_supported'], 6)

    def test_invalid_and_non_note_references(self):
        result = self.support([('12개월', '[[report.pdf]]'), ('12개월', 'https://example.com/a.md'),
                               ('12개월', '회사 홈페이지'), ('12개월', 'see notes/Evidence.md'),
                               ('12개월', '`C:/notes/Evidence.md`'), ('12개월', '[[../../Outside]]'),
                               ('12개월', '[[a:b]]'), ('12개월', '[[]]')])
        self.assertEqual(self.found(result), [(8, 'ssot-source-unresolved', 'invalid_reference'),
                                              (9, 'ssot-source-unresolved', 'invalid_reference')])
        self.assertEqual((result['rows_checked'], result['rows_skipped']), (2, 6))

    def test_self_reference_compares_only_outside_fact_tables(self):
        result = self.support([('77일', '[[#근거]]'), ('88일', '[[#근거]]'), ('77일', '[[facts]]')],
                              preamble='## 근거\n세부 값 77일.\n\n')
        self.assertEqual(self.found(result), [(7, 'ssot-source-unsupported', None)])
        self.assertEqual(result['warnings'][0]['details'], {'cited': ['notes/facts.md']})
        self.assertEqual(result['rows_supported'], 2)
        ledger_bytes = (self.vault / 'notes/facts.md').read_bytes()
        self.assertEqual(result['cited_notes'], [{'path': 'notes/facts.md', 'sha256': sha(ledger_bytes),
                                                  'bytes': len(ledger_bytes)}])

    def test_one_supporting_note_is_enough_and_broken_citations_still_warn(self):
        result = self.support([('12개월', '[[Evidence]], [[Missing]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'not_found')])
        self.assertEqual((result['rows_checked'], result['rows_supported'], result['rows_unresolved']), (1, 1, 0))

    def test_rows_without_value_or_source_keep_check_treatment(self):
        text = ledger([['Atlas', 'retention', '12', '2025-01-01', '', '2026-10-07', '', '', 'confirmed'],
                       ['Atlas', 'retention', '', '2025-01-01', '', '2026-10-07', '[[Missing]]', '', 'pending']])
        self.write('notes/facts.md', text)
        result = ssot.check_support(self.vault, 'notes/facts.md')
        self.assertEqual(result['warnings'], [])
        self.assertEqual((result['rows_checked'], result['rows_skipped']), (0, 2))
        codes = [w['code'] for w in ssot.check_ledger(text)]
        self.assertIn('ssot-confirmed-source-missing', codes)
        self.assertIn('ssot-empty-fact', codes)
        self.assertFalse(SUPPORT_CODES & set(codes))

    def test_duplicate_columns_and_tables_without_source_are_skipped(self):
        text = (ledger([['Atlas', 'retention', '12']], fields=['subject', 'relation', 'value']) + '\n' +
                ledger([['Atlas', 'retention', '12', '[[Missing]]', '[[Other]]']],
                       fields=['subject', 'relation', 'value', 'source', '출처']))
        self.write('notes/facts.md', text)
        result = ssot.check_support(self.vault, 'notes/facts.md')
        self.assertEqual((result['warnings'], result['rows_checked'], result['rows_skipped']), ([], 0, 2))

    def test_inventory_limit_reports_incomplete_instead_of_not_found(self):
        self.write('zz/one.md', 'x\n')
        self.write('zz/two.md', 'y\n')
        # Entries: 3 at the root, 1 in 00-meta, 2 in notes, 2 in zz. Stop inside zz.
        with mock.patch.object(ssot, 'MAX_INVENTORY_ENTRIES', 7):
            result = self.support([('12개월', '[[Evidence]]'), ('12개월', '[[notes/Evidence]]'), ('12개월', '[[Missing]]')])
        self.assertEqual(self.found(result), [(3, 'ssot-source-unresolved', 'inventory_incomplete'),
                                              (5, 'ssot-source-unresolved', 'inventory_incomplete')])
        self.assertEqual(result['rows_supported'], 1)
        self.assertEqual(result['incomplete'], ['inventory_incomplete'])

    def test_cited_note_budget_marks_the_result_incomplete(self):
        self.write('notes/Other.md', '12개월\n')
        with mock.patch.object(ssot, 'MAX_SUPPORT_NOTES', 1):
            result = self.support([('12개월', '[[Evidence]]'), ('12개월', '[[Other]]'), ('12개월', '[[Evidence]]')])
        self.assertEqual(self.found(result), [(4, 'ssot-source-unresolved', 'source_limit')])
        self.assertEqual(result['incomplete'], ['source_limit'])
        self.assertEqual(result['rows_supported'], 2)

    def test_reference_limit_marks_the_result_incomplete(self):
        source = ', '.join(f'[[Missing{index}]]' for index in range(ssot.MAX_REFERENCES + 2))
        result = self.support([('12개월', source)])
        self.assertEqual(len(result['warnings']), ssot.MAX_REFERENCES)
        self.assertEqual(result['incomplete'], ['reference_limit'])

    def test_cited_note_change_during_check_is_not_reported_as_current(self):
        real = ssot._cited_forms

        def changing(policy, relative, run):
            outcome = real(policy, relative, run)
            self.write(relative, 'changed: 12개월 and a longer body\n')
            return outcome
        with mock.patch.object(ssot, '_cited_forms', changing):
            with self.assertRaisesRegex(ssot.SSOTError, 'stale_source'):
                self.support([('12개월', '[[Evidence]]')])

    def test_policy_change_during_check_fails_closed(self):
        real = ssot._inventory

        def changing(policy):
            inventory = real(policy)
            self.write('00-meta/vault-config.json', '{"deny_zones": ["private", "notes"], "exclude_dirs": []}')
            return inventory
        with mock.patch.object(ssot, '_inventory', changing):
            with self.assertRaisesRegex(ssot.SSOTError, 'configuration_changed'):
                self.support([('12개월', '[[Evidence]]')])

    def test_issue_ids_are_stable_across_runs(self):
        rows = [('24개월', '[[Evidence]]'), ('12개월', '[[Missing]]')]
        first, second = self.support(rows), self.support(rows)
        self.assertEqual([w['issue_id'] for w in first['warnings']], [w['issue_id'] for w in second['warnings']])
        self.assertEqual(len({w['issue_id'] for w in first['warnings']}), 2)

    def test_reference_extraction_is_conservative(self):
        cases = {'[[A]], [[B#h|alias]] and ![[C]]': [('A', True), ('B', True), ('C', True)],
                 '[[#근거]]': [('', True)],
                 '[[]] [[ |x]]': [],
                 '`notes/a b.md` [[A]]': [('notes/a b.md', False), ('A', True)],
                 '`[[Inside]]`': [],
                 'notes/a.md': [('notes/a.md', False)],
                 'see notes/a.md': [],
                 '[[A]] and [[A|again]]': [('A', True)],
                 'https://example.com/page': []}
        for cell, expected in cases.items():
            with self.subTest(cell=cell):
                self.assertEqual(ssot._references(cell), expected)

    def test_cli_check_support_exit_zero_with_warnings_and_unchanged_error_contract(self):
        self.support([('24개월', '[[Evidence]]')])
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
            code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', 'notes/facts.md'])
        self.assertEqual((code, stderr.getvalue()), (0, ''))
        self.assertEqual([w['code'] for w in json.loads(stdout.getvalue())['warnings']], ['ssot-source-unsupported'])
        self.write('private/facts.md', 'x\n')
        self.write('excluded/facts.md', 'x\n')
        for relative in ['private/facts.md', 'excluded/facts.md', '../outside.md', 'notes/absent.md', 'notes/facts.json']:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(path=relative), mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
                code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', relative])
                self.assertEqual(code, 1)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(json.loads(stderr.getvalue()), {'error': 'ssot_operation_unavailable'})
        script = str(SCRIPTS / 'vault_ssot.py')
        completed = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'check-support',
                                    '--path', 'notes/facts.md'], capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout)['rows_unsupported'], 1)


class LedgerTableOnlySupportTests(unittest.TestCase):
    """check-support support found only inside other cited ledgers' fact tables."""

    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.write('00-meta/vault-config.json', '{"deny_zones": ["private"], "exclude_dirs": ["excluded"]}')

    def write(self, relative, text, bom=False):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8'))
        return target

    @staticmethod
    def rows(*pairs):
        """A v2 ledger of (value, source) rows; without a preamble the first row is line 3."""
        return ledger([['Atlas', 'retention', value, '2025-01-01', '', '2026-10-07', source, '', 'confirmed']
                       for value, source in pairs])

    def check(self, path='notes/facts.md', **options):
        return ssot.check_support(self.vault, path, **options)

    def test_mutual_ledgers_without_prose_mark_each_other_table_only(self):
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]]')))
        self.write('notes/Other.md', self.rows(('24개월', '[[facts]]')))
        for path, via in (('notes/facts.md', 'notes/Other.md'), ('notes/Other.md', 'notes/facts.md')):
            with self.subTest(path=path):
                result = self.check(path)
                self.assertEqual((result['warnings'], result['rows_supported'], result['rows_unsupported']), ([], 1, 0))
                self.assertEqual(result['rows_supported_ledger_table_only'], 1)
                self.assertEqual(result['ledger_table_only'], [{'line': 3, 'via': [via]}])

    def test_cited_frontmatter_is_decided_before_fact_tables_are_removed(self):
        # Without frontmatter, cutting the table leaves a '---' line on top; it must
        # not open a frontmatter block over the prose below it.
        prose = '메모: 36개월 기준은 내규 3조.\n---\n끝.\n'
        long_table = self.rows(('24개월', '[[facts]]'), *[(f'{n}일', '') for n in range(70)])
        layouts = {'table on line 1': self.rows(('24개월', '[[facts]]')) + '---\n' + prose,
                   'rule above a long table': '---\n' + long_table + prose}
        self.write('notes/facts.md', self.rows(('36개월', '[[Other]]'), ('24개월', '[[Other]]')))
        for name, text in layouts.items():
            with self.subTest(layout=name):
                self.write('notes/Other.md', text)
                default, flagged = self.check(), self.check(primary_text_only=True)
                # 36개월 is only in the prose; the control 24개월 is only in the table.
                self.assertEqual((default['warnings'], default['rows_supported']), ([], 2))
                for result in (default, flagged):
                    self.assertEqual(result['ledger_table_only'], [{'line': 4, 'via': ['notes/Other.md']}])
                self.assertEqual([(w['line'], w['details']) for w in flagged['warnings']],
                                 [(4, {'cited': ['notes/Other.md'], 'reason': 'ledger_table_only'})])
                self.assertEqual((flagged['rows_supported'], flagged['rows_unsupported']), (1, 1))
                self.assertEqual(flagged['ledger_tables_unseparated'], [])
        # Documented limit: a self-citation keeps the v0.30.0 order (tables cut first),
        # so the same layout hides that prose unless a blank line follows the table.
        for gap, found in (('', [(3, 'ssot-source-unsupported', None)]), ('\n', [])):
            with self.subTest(self_gap=gap):
                self.write('notes/Self.md', self.rows(('36개월', '[[#근거]]')) + gap + '---\n' + prose)
                result = self.check('notes/Self.md')
                self.assertEqual([(w['line'], w['code'], w['details'].get('reason')) for w in result['warnings']], found)

    def test_cited_notes_parse_ledger_refuses_are_named_only_with_primary_text_only(self):
        # parse_ledger splits every header-candidate line, prose and other tables
        # included, so one refusal leaves the whole cited note without tables.
        table, long = self.rows(('24개월', '[[facts]]')), '가' * 1400
        wide = ledger([['x'] * 40], fields=[f'c{n}' for n in range(40)])
        many_rows = self.rows(('24개월', '[[facts]]'), *[(f'{n}일', '') for n in range(ssot.MAX_ROWS)])
        cases = [  # (case, cited note, the reason parse_ledger refuses its body with, or None)
            ('long pipe prose line', table + '\n메모: ' + long + ' | 참고\n다음 줄\n', 'cell_limit'),
            ('40-column auxiliary table', table + '\n' + wide, 'column_limit'),
            ('33-pipe prose line', table + '\n' + 'a' + ' | a' * 33 + '\n다음 줄\n', 'column_limit'),
            ('nine fact tables', '\n'.join([table] * (ssot.MAX_TABLES + 1)), 'table_limit'),
            ('257 fact rows', many_rows, 'row_limit'),
            ('NUL', table + 'x\x00y\n', 'invalid_text'),
            ('short pipe prose line', table + '\n메모: 짧은 | 줄\n다음 줄\n', None),
            ('pipe in a code span', table + '\n메모: `' + long + ' | 참고`\n다음 줄\n', None),
            ('escaped pipe', table + '\n메모: ' + long + ' \\| 참고\n다음 줄\n', None),
            ('pipe in a wiki alias', table + '\n메모: [[Target|' + long + ']]\n다음 줄\n', None),
            ('indented line', table + '\n    메모: ' + long + ' | 참고\n다음 줄\n', None),
            ('last line', table + '\n메모: ' + long + ' | 참고\n', None),
            ('fenced line', table + '\n```\n메모: ' + long + ' | 참고\n다음 줄\n```\n', None),
            ('frontmatter', '---\nnote: ' + long + ' | 참고\nnul: x\x00y\n---\n' + table, None)]
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]]')))
        for case, text, reason in cases:
            with self.subTest(case=case):
                self.write('notes/Other.md', text)
                default, flagged = self.check(), self.check(primary_text_only=True)
                self.assertNotIn('ledger_tables_unseparated', default)
                named = flagged.pop('ledger_tables_unseparated')
                if reason:
                    self.assertEqual(named, [{'path': 'notes/Other.md', 'reason': reason}])
                    # The opt-in rule cannot apply, so the value in the table still counts.
                    self.assertEqual(flagged, default)
                    self.assertEqual((default['rows_supported'], default['ledger_table_only']), (1, []))
                else:
                    self.assertEqual(named, [])
                    self.assertEqual(default['ledger_table_only'], [{'line': 3, 'via': ['notes/Other.md']}])
                    self.assertEqual([(w['line'], w['details'].get('reason')) for w in flagged['warnings']],
                                     [(3, 'ledger_table_only')])
        # Read in citation order (Wide first); listed in path order.
        self.write('notes/Other.md', cases[0][1])
        self.write('notes/Wide.md', cases[1][1])
        self.write('notes/facts.md', self.rows(('24개월', '[[Wide]], [[Other]]')))
        both = [{'path': 'notes/Other.md', 'reason': 'cell_limit'}, {'path': 'notes/Wide.md', 'reason': 'column_limit'}]
        for extra, named in (([], None), (['--primary-text-only'], both)):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(extra=extra), mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
                code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', 'notes/facts.md', *extra])
                self.assertEqual((code, stderr.getvalue()), (0, ''))
                self.assertEqual(json.loads(stdout.getvalue()).get('ledger_tables_unseparated'), named)

    def test_each_check_shows_only_its_own_link_of_a_longer_chain(self):
        # A -> B -> C -> A, each value only in the next ledger's fact table. There is
        # no graph walk: each check lists its own link and nothing further.
        links = (('A', '1개월', 'B', '3개월'), ('B', '2개월', 'C', '1개월'), ('C', '3개월', 'A', '2개월'))
        for name, value, cited, held in links:
            self.write(f'notes/{name}.md', self.rows((value, f'[[{cited}]]'), (held, '')))
        for name, _, cited, _ in links:
            with self.subTest(ledger=name):
                default = self.check(f'notes/{name}.md')
                flagged = self.check(f'notes/{name}.md', primary_text_only=True)
                self.assertEqual(default['ledger_table_only'], [{'line': 3, 'via': [f'notes/{cited}.md']}])
                self.assertEqual((default['rows_supported'], flagged['rows_unsupported']), (1, 1))

    def test_prose_outside_the_table_and_plain_notes_are_ordinary_support(self):
        self.write('notes/Other.md', '# Other\n\n' + self.rows(('24개월', ''), ('36개월', '')) + '\n메모: 24개월 기준.\n')
        self.write('notes/Plain.md', '기준은 36개월이다.\n')
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]]'), ('36개월', '[[Other]]'),
                                               ('36개월', '[[Other]], [[Plain]]'), ('36개월', '[[Plain]]')))
        result = self.check()
        self.assertEqual((result['warnings'], result['rows_supported']), ([], 4))
        self.assertEqual(result['ledger_table_only'], [{'line': 4, 'via': ['notes/Other.md']}])
        self.assertEqual(result['rows_supported_ledger_table_only'], 1)

    def test_via_names_only_the_ledgers_whose_tables_hold_the_value(self):
        self.write('notes/Other.md', self.rows(('24개월', '[[facts]]')))
        self.write('notes/Third.md', self.rows(('36개월', '[[facts]]')))
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]], [[Third]]'), ('36개월', '[[Third]], [[Other]]')))
        result = self.check()
        self.assertEqual(result['ledger_table_only'], [{'line': 3, 'via': ['notes/Other.md']},
                                                       {'line': 4, 'via': ['notes/Third.md']}])

    def test_self_citation_is_never_listed_and_other_ledgers_still_are(self):
        self.write('notes/Other.md', self.rows(('88일', '[[facts]]')))
        self.write('notes/facts.md', '## 근거\n세부 값 77일.\n\n' + self.rows(('77일', '[[#근거]]'), ('88일', '[[#근거]], [[Other]]')))
        result = self.check()
        self.assertEqual((result['warnings'], result['rows_supported']), ([], 2))
        self.assertEqual(result['ledger_table_only'], [{'line': 7, 'via': ['notes/Other.md']}])

    def test_bom_prefixed_cited_ledger_is_sliced_on_the_decoded_text(self):
        # No blank line around the table: slicing the raw bytes (BOM included)
        # with parse_ledger offsets would cut the heading and keep table bytes.
        lines = ['---', 'title: Bom', '---', '# 근거 대장', '| 주체 | 항목 | 값 | 출처 |', '| --- | --- | --- | --- |',
                 '| Atlas | retention | 60개월 | [[facts]] |', '기간 메모: 84개월', '']
        self.write('notes/facts.md', self.rows(('60개월', '[[Bom]]'), ('84개월', '[[Bom]]'), ('근거 대장', '[[Bom]]')))
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=newline):
                cited = self.write('notes/Bom.md', newline.join(lines), bom=True)
                result = self.check()
                self.assertEqual((result['warnings'], result['rows_supported']), ([], 3))
                self.assertEqual(result['ledger_table_only'], [{'line': 3, 'via': ['notes/Bom.md']}])
                data = cited.read_bytes()
                self.assertEqual(result['cited_notes'], [{'path': 'notes/Bom.md', 'sha256': sha(data), 'bytes': len(data)}])

    def test_unrelated_or_fenced_tables_do_not_make_a_ledger(self):
        self.write('notes/Table.md', '| 이름 | 수량 |\n| --- | --- |\n| 부품 | 72개월 |\n')
        self.write('notes/Example.md', '```\n| subject | relation | value |\n| --- | --- | --- |\n| Atlas | retention | 72개월 |\n```\n')
        self.write('notes/facts.md', self.rows(('72개월', '[[Table]]'), ('72개월', '[[Example]]')))
        result = self.check()
        self.assertEqual((result['warnings'], result['rows_supported']), ([], 2))
        self.assertEqual((result['rows_supported_ledger_table_only'], result['ledger_table_only']), (0, []))

    def test_unparseable_cited_ledger_counts_as_having_no_tables(self):
        table = '| subject | relation | value |\n| --- | --- | --- |\n| Atlas | retention | 108개월 |\n'
        notes = {'Many': '\n'.join([table] * (ssot.MAX_TABLES + 1)), 'Binary': table + 'x\x00y\n'}
        for name, text in notes.items():
            self.write(f'notes/{name}.md', text)
        for text, code in ((notes['Many'], 'table_limit'), (notes['Binary'], 'invalid_text')):
            with self.subTest(code=code), self.assertRaisesRegex(ssot.SSOTError, code):
                ssot.parse_ledger(text)
        self.write('notes/facts.md', self.rows(('108개월', '[[Many]]'), ('108개월', '[[Binary]]')))
        for options in ({}, {'primary_text_only': True}):
            with self.subTest(options=options):
                result = self.check(**options)
                self.assertEqual((result['warnings'], result['rows_supported']), ([], 2))
                self.assertEqual((result['rows_supported_ledger_table_only'], result['ledger_table_only']), (0, []))

    def test_each_cited_note_is_read_once_within_unchanged_budgets(self):
        self.write('notes/Other.md', self.rows(('24개월', '[[facts]]'), ('36개월', '[[facts]]')))
        self.write('notes/Evidence.md', '보존 기간은 12개월이다.\n')
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]]'), ('36개월', '[[Other]]'),
                                               ('12개월', '[[Other]], [[Evidence]]')))
        real, reads = ssot.stable_read, []

        def counting(resolver, size, contents=False):
            reads.append(resolver().name)
            return real(resolver, size, contents=contents)
        for limit in (ssot.MAX_SUPPORT_NOTES, 1):
            results = []
            for options in ({}, {'primary_text_only': True}):
                reads.clear()
                with self.subTest(limit=limit, options=options), \
                        mock.patch.object(ssot, 'stable_read', counting), \
                        mock.patch.object(ssot, 'MAX_SUPPORT_NOTES', limit):
                    results.append(self.check(**options))
                    self.assertEqual(sorted(reads), ['Evidence.md', 'Other.md'] if limit > 1 else ['Other.md'])
            default, flagged = results
            for key in ('cited_notes', 'incomplete', 'rows_checked', 'rows_unresolved', *TABLE_ONLY_KEYS):
                with self.subTest(limit=limit, key=key):
                    self.assertEqual(flagged[key], default[key])
            self.assertEqual(default['incomplete'], [] if limit > 1 else ['source_limit'])
            self.assertEqual(default['ledger_table_only'], [{'line': 3, 'via': ['notes/Other.md']},
                                                            {'line': 4, 'via': ['notes/Other.md']}])

    def test_primary_text_only_moves_only_table_only_rows_to_unsupported(self):
        self.write('notes/Other.md', self.rows(('24개월', '[[facts]]')) + '\n메모: 36개월.\n')
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]]'), ('36개월', '[[Other]]'),
                                               ('48개월', '[[Other]]'), ('24개월', '[[Missing]], [[Other]]')))
        default, flagged = self.check(), self.check(primary_text_only=True)
        self.assertEqual(default['ledger_table_only'], [{'line': 3, 'via': ['notes/Other.md']},
                                                        {'line': 6, 'via': ['notes/Other.md']}])
        self.assertEqual((default['rows_supported'], default['rows_unsupported']), (3, 1))
        self.assertEqual((flagged['rows_supported'], flagged['rows_unsupported']), (1, 3))
        for key in ('path', 'source_sha256', 'rows_checked', 'rows_unresolved', 'rows_skipped', 'cited_notes',
                    'incomplete', 'method', 'authority', *TABLE_ONLY_KEYS):
            with self.subTest(key=key):
                self.assertEqual(flagged[key], default[key])
        # Existing warnings keep their ids; the flag only adds the moved rows.
        self.assertEqual([w for w in flagged['warnings'] if w in default['warnings']], default['warnings'])
        added = [(w['line'], w['code'], w['details']) for w in flagged['warnings'] if w not in default['warnings']]
        self.assertEqual(added, [(3, 'ssot-source-unsupported', {'cited': ['notes/Other.md'], 'reason': 'ledger_table_only'}),
                                 (6, 'ssot-source-unsupported', {'cited': ['notes/Other.md'], 'reason': 'ledger_table_only'})])

    def test_annotation_and_warnings_carry_lines_and_paths_only(self):
        self.write('notes/Other.md', self.rows(('비밀값 24개월', '[[facts]]')))
        self.write('notes/facts.md', self.rows(('비밀값 24개월', '[[Other]]')))
        for options in ({}, {'primary_text_only': True}):
            with self.subTest(options=options):
                result = self.check(**options)
                self.assertEqual([set(entry) for entry in result['ledger_table_only']], [{'line', 'via'}])
                dumped = json.dumps(result, ensure_ascii=False)
                for literal in ('비밀값', '24개월', 'Atlas', 'retention'):
                    self.assertNotIn(literal, dumped)

    def test_primary_text_only_requires_a_boolean(self):
        for value in (1, 'yes', None):
            with self.subTest(value=value), self.assertRaisesRegex(ssot.SSOTError, 'invalid_opt_in'):
                self.check(primary_text_only=value)

    def test_cli_flag_is_opt_in_and_exit_codes_are_unchanged(self):
        self.write('notes/Other.md', self.rows(('24개월', '[[facts]]')))
        self.write('notes/facts.md', self.rows(('24개월', '[[Other]]')))
        outputs = {}
        for extra in ([], ['--primary-text-only']):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(extra=extra), mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
                code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', 'notes/facts.md', *extra])
                self.assertEqual((code, stderr.getvalue()), (0, ''))
            outputs[bool(extra)] = json.loads(stdout.getvalue())
        self.assertEqual((outputs[False]['rows_supported'], outputs[False]['warnings']), (1, []))
        self.assertEqual(outputs[True]['rows_unsupported'], 1)
        self.assertEqual(outputs[True]['warnings'][0]['details'], {'cited': ['notes/Other.md'], 'reason': 'ledger_table_only'})
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
            code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', 'notes/absent.md', '--primary-text-only'])
        self.assertEqual((code, stdout.getvalue(), json.loads(stderr.getvalue())),
                         (1, '', {'error': 'ssot_operation_unavailable'}))
        script = str(SCRIPTS / 'vault_ssot.py')
        completed = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'check-support',
                                    '--path', 'notes/facts.md', '--primary-text-only'], capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout)['ledger_table_only'], [{'line': 3, 'via': ['notes/Other.md']}])

    def test_default_output_equals_v0300_golden_plus_the_annotation(self):
        """Default CLI output captured from v0.30.0 (452c5c0) before the annotation existed."""
        golden = json.loads(SUPPORT_GOLDEN.read_text(encoding='utf-8'))
        expected = {'A': [(8, ['ledgers/B.md']), (12, ['ledgers/C.md']), (15, ['ledgers/Legacy.md']),
                          (18, ['ledgers/B.md']), (19, ['ledgers/B.md']), (20, ['ledgers/B.md', 'ledgers/Legacy.md'])],
                    'B': [(line, ['ledgers/A.md']) for line in range(8, 13)],
                    'C': [(7, ['ledgers/A.md'])]}
        self.assertEqual(sorted(golden['cases']), sorted(expected))
        for relative, text in golden['files'].items():
            self.write(relative, text, bom=relative in golden['bom'])
        for name, case in sorted(golden['cases'].items()):
            outputs = {}
            for extra in ([], ['--primary-text-only']):
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
                    code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', case['path'], *extra])
                with self.subTest(case=name, extra=extra):
                    self.assertEqual((code, stderr.getvalue()), (case['exit'], case['stderr']))
                outputs[bool(extra)] = json.loads(stdout.getvalue())
            default, flagged = outputs[False], outputs[True]
            listed = [{'line': line, 'via': via} for line, via in expected[name]]
            with self.subTest(case=name):
                self.assertEqual({key: default.pop(key) for key in TABLE_ONLY_KEYS},
                                 {'rows_supported_ledger_table_only': len(listed), 'ledger_table_only': listed})
                # Byte-for-byte the v0.30.0 stdout once the two additive fields are removed.
                self.assertEqual(ssot._encode(default).decode() + '\n', case['stdout'])
                self.assertEqual({key: flagged.pop(key) for key in TABLE_ONLY_KEYS},
                                 {'rows_supported_ledger_table_only': len(listed), 'ledger_table_only': listed})
                self.assertEqual((flagged['rows_supported'], flagged['rows_unsupported']),
                                 (default['rows_supported'] - len(listed), default['rows_unsupported'] + len(listed)))
                kept = [w for w in flagged['warnings'] if w in default['warnings']]
                added = [(w['line'], w['code'], w['details'].get('reason')) for w in flagged['warnings']
                         if w not in default['warnings']]
                self.assertEqual(kept, default['warnings'])
                self.assertEqual(added, [(line, 'ssot-source-unsupported', 'ledger_table_only') for line, _ in expected[name]])


class PeriodNormalizationSupportTests(unittest.TestCase):
    """check-support --normalize-periods: a separate per-row normalized_match from period intervals."""

    MEETING = ('---\ntitle: Meeting\n---\n회의는 2026. 10. 7.에 열렸다. 3Q26 출하, 출시는 2026.08.15.\n'
               '점검 2026.10.01. 당기 실적과 올해 3분기 계획.\n')
    VIA = ['notes/Meeting.md']

    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.write('00-meta/vault-config.json', '{"deny_zones": ["private"], "exclude_dirs": ["excluded"]}')
        self.write('notes/Meeting.md', self.MEETING)

    def write(self, relative, text, bom=False):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8'))
        return target

    @staticmethod
    def rows(*pairs):
        """A v2 ledger of (value, source) rows; without a preamble the first row is line 3."""
        return ledger([['Atlas', 'launch', value, '2025-01-01', '', '2026-10-07', source, '', 'confirmed']
                       for value, source in pairs])

    def check(self, path='notes/facts.md', **options):
        return ssot.check_support(self.vault, path, **options)

    def run_cli(self, *extra, path='notes/facts.md'):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
            code = ssot.main(['--vault', str(self.vault), 'check-support', '--path', path, *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    @staticmethod
    def without_periods(result):
        return {key: value for key, value in result.items() if key not in PERIOD_KEYS}

    def test_each_compared_row_gets_a_separate_normalized_match(self):
        self.write('notes/facts.md', self.rows(
            ('2026-10-07', '[[Meeting]]'), ('2026년 3분기', '[[Meeting]]'), ('2026년 4분기', '[[Meeting]]'),
            ('2026년 2분기', '[[Meeting]]'), ('FY2026', '[[Meeting]]'), ('당기 출하', '[[Meeting]]'),
            ('24개월', '[[Meeting]]'), ('3Q26', '[[Meeting]]'), ('2026년 3분기', '[[Missing]]'), ('2026년 3분기', '')))
        default, flagged = self.check(), self.check(normalize_periods=True)
        self.assertEqual(flagged['normalized_periods'], [
            {'line': 3, 'normalized_match': 'equal', 'via': self.VIA},
            {'line': 4, 'normalized_match': 'equal', 'via': self.VIA},
            {'line': 5, 'normalized_match': 'contained', 'via': self.VIA},
            {'line': 6, 'normalized_match': 'none'},
            {'line': 7, 'normalized_match': 'abstain', 'reason': 'fiscal_start_undeclared'},
            {'line': 8, 'normalized_match': 'abstain', 'reason': 'relative_needs_anchor'},
            {'line': 10, 'normalized_match': 'equal', 'via': self.VIA}])
        self.assertEqual(flagged['period_convention'],
                         {'fiscal_start': None, 'scope': importlib.import_module('vault_period').SCOPE})
        # Literal support is unchanged: only 3Q26 is present as written.
        self.assertEqual((default['rows_checked'], default['rows_supported'], default['rows_unsupported'],
                          default['rows_unresolved'], default['rows_skipped']), (9, 1, 7, 1, 1))
        self.assertEqual(ssot._encode(self.without_periods(flagged)), ssot._encode(default))

    def test_flag_adds_only_its_two_fields_under_every_option(self):
        self.write('notes/Other.md', self.rows(('2026.03.05', '[[facts]]')) + '\n메모: 2026년 3분기 출하.\n')
        self.write('notes/facts.md', self.rows(('2026-03-05', '[[Other]]'), ('2026년 3분기', '[[Other]], [[Meeting]]'),
                                               ('2026년 1분기', '[[Missing]], [[Meeting]]'), ('FY2026', '[[Meeting]]')))
        for base in ({}, {'primary_text_only': True}):
            default = self.check(**base)
            self.assertFalse(set(PERIOD_KEYS) & set(default))
            for extra in ({'normalize_periods': True}, {'normalize_periods': True, 'fiscal_start': 1},
                          {'normalize_periods': True, 'fiscal_start': 4}):
                with self.subTest(base=base, extra=extra):
                    flagged = self.check(**base, **extra)
                    self.assertEqual(ssot._encode(self.without_periods(flagged)), ssot._encode(default))
                    self.assertEqual([row['line'] for row in flagged['normalized_periods']], [3, 4, 5, 6])

    def test_fiscal_start_resolves_fiscal_years_and_holds_calendar_quarters(self):
        self.write('notes/facts.md', self.rows(('FY2026', '[[Meeting]]'), ('2026년 3분기', '[[Meeting]]'),
                                               ('2026-10-07', '[[Meeting]]')))
        april = self.check(normalize_periods=True, fiscal_start=4)
        self.assertEqual(april['normalized_periods'], [
            {'line': 3, 'normalized_match': 'contained', 'via': self.VIA},
            {'line': 4, 'normalized_match': 'abstain', 'reason': 'quarter_basis_ambiguous'},
            {'line': 5, 'normalized_match': 'equal', 'via': self.VIA}])
        self.assertEqual(april['period_convention']['fiscal_start'], 4)
        january = self.check(normalize_periods=True, fiscal_start=1)
        self.assertEqual([row['normalized_match'] for row in january['normalized_periods']],
                         ['contained', 'equal', 'equal'])
        december = self.check(normalize_periods=True, fiscal_start=12)
        self.assertEqual(december['normalized_periods'][0], {'line': 3, 'normalized_match': 'none'})

    def test_primary_text_only_keeps_other_ledgers_fact_tables_out(self):
        self.write('notes/Other.md', self.rows(('2026.03.05', '[[facts]]')) + '\n메모: 2026년 3분기 출하.\n')
        self.write('notes/facts.md', self.rows(('2026-03-05', '[[Other]]'), ('2026년 3분기', '[[Other]]')))
        via = ['notes/Other.md']
        self.assertEqual(self.check(normalize_periods=True)['normalized_periods'], [
            {'line': 3, 'normalized_match': 'equal', 'via': via, 'via_ledger_table_only': via},
            {'line': 4, 'normalized_match': 'equal', 'via': via}])
        self.assertEqual(self.check(normalize_periods=True, primary_text_only=True)['normalized_periods'], [
            {'line': 3, 'normalized_match': 'none'},
            {'line': 4, 'normalized_match': 'equal', 'via': via}])

    def test_validity_cells_stay_strict_dates(self):
        rows = [['Atlas', 'launch', '2026년 3분기', start, until, '2026-10-07', '[[Meeting]]', '', 'confirmed']
                for start, until in (('2026.07.01', ''), ('2026년 3분기', ''), ('2026-07-01', '2026년 4분기'))]
        text = ledger(rows)
        self.write('notes/facts.md', text)
        invalid = [(w['line'], w['details']) for w in ssot.check_ledger(text) if w['code'] == 'ssot-invalid-date']
        self.assertEqual(invalid, [(3, {'field': 'valid_from'}), (4, {'field': 'valid_from'}),
                                   (5, {'field': 'valid_until'})])
        flagged = self.check(normalize_periods=True, fiscal_start=1)
        self.assertEqual(flagged['warnings'], self.check()['warnings'])
        self.assertEqual([row['normalized_match'] for row in flagged['normalized_periods']], ['equal'] * 3)

    def test_entries_carry_lines_paths_and_reasons_only(self):
        self.write('notes/Secret.md', '비밀값 2026. 10. 7. 회의, 3Q26 출하\n')
        self.write('notes/facts.md', self.rows(('비밀값 2026-10-07', '[[Secret]]'), ('비밀값 2026년 3분기', '[[Secret]]'),
                                               ('비밀값 당기', '[[Secret]]')))
        for fiscal_start in (None, 4):
            with self.subTest(fiscal_start=fiscal_start):
                result = self.check(normalize_periods=True, fiscal_start=fiscal_start)
                self.assertEqual(len(result['normalized_periods']), 3)
                self.assertLessEqual({key for entry in result['normalized_periods'] for key in entry},
                                     {'line', 'normalized_match', 'via', 'reason'})
                dumped = json.dumps(result, ensure_ascii=False)
                for literal in ('비밀값', '2026-10-07', '2026.10.07', '2026-35', '2026-07-01', '분기', '당기', 'Atlas'):
                    self.assertNotIn(literal, dumped)

    def test_opt_in_arguments_are_validated_before_reading(self):
        self.write('notes/facts.md', self.rows(('2026년 3분기', '[[Meeting]]')))
        invalid = [dict(normalize_periods=value) for value in (1, 'yes', None)]
        invalid += [dict(fiscal_start=4), dict(fiscal_start=4, normalize_periods=False)]
        invalid += [dict(normalize_periods=True, fiscal_start=value) for value in (0, 13, '04', True, 4.0)]
        for options in invalid:
            with self.subTest(options=options), \
                    mock.patch.object(ssot.state, '_Policy', side_effect=AssertionError('read before validation')), \
                    self.assertRaisesRegex(ssot.SSOTError, '^invalid_opt_in$'):
                self.check(**options)

    def test_cli_flags_are_opt_in_and_exit_codes_are_unchanged(self):
        self.write('notes/facts.md', self.rows(('2026-10-07', '[[Meeting]]'), ('FY2026', '[[Meeting]]')))
        code, stdout, stderr = self.run_cli()
        self.assertEqual((code, stderr), (0, ''))
        self.assertFalse(set(PERIOD_KEYS) & set(json.loads(stdout)))
        fiscal = {None: {'line': 4, 'normalized_match': 'abstain', 'reason': 'fiscal_start_undeclared'},
                  4: {'line': 4, 'normalized_match': 'contained', 'via': self.VIA},
                  12: {'line': 4, 'normalized_match': 'none'}}
        for extra, fiscal_start in ((['--normalize-periods'], None), (['--normalize-periods', '--fiscal-start', '04'], 4),
                                    (['--fiscal-start', '4', '--normalize-periods'], 4),
                                    (['--normalize-periods', '--fiscal-start', '12'], 12)):
            with self.subTest(extra=extra):
                code, out, err = self.run_cli(*extra)
                self.assertEqual((code, err), (0, ''))
                result = json.loads(out)
                self.assertEqual(result['period_convention']['fiscal_start'], fiscal_start)
                self.assertEqual(result['normalized_periods'],
                                 [{'line': 3, 'normalized_match': 'equal', 'via': self.VIA}, fiscal[fiscal_start]])
                self.assertEqual(ssot._encode(self.without_periods(result)).decode() + '\n', stdout)
        for extra in (['--fiscal-start', '04'], ['--normalize-periods', '--fiscal-start', '13'],
                      ['--normalize-periods', '--fiscal-start', '00'], ['--normalize-periods', '--fiscal-start', 'April']):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_cli(*extra)[:2], (1, ''))
                self.assertEqual(json.loads(self.run_cli(*extra)[2]), {'error': 'ssot_operation_unavailable'})
        completed = subprocess.run([sys.executable, str(SCRIPTS / 'vault_ssot.py'), '--vault', str(self.vault),
                                    'check-support', '--path', 'notes/facts.md', '--normalize-periods',
                                    '--fiscal-start', '04'], capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout)['normalized_periods'][1], fiscal[4])

    def test_the_period_module_is_loaded_only_on_request(self):
        self.write('notes/facts.md', self.rows(('2026년 3분기', '[[Meeting]]')))
        script = ('import sys\nsys.path.insert(0, sys.argv[1])\nimport vault_ssot\n'
                  "vault_ssot.check_support(sys.argv[2], 'notes/facts.md', primary_text_only=True)\n"
                  "print('vault_period' in sys.modules)\n"
                  "vault_ssot.check_support(sys.argv[2], 'notes/facts.md', normalize_periods=True)\n"
                  "print('vault_period' in sys.modules)\n")
        completed = subprocess.run([sys.executable, '-c', script, str(SCRIPTS), str(self.vault)],
                                   capture_output=True, text=True, encoding='utf-8')
        self.assertEqual((completed.returncode, completed.stdout.split()), (0, ['False', 'True']), completed.stderr)

    def test_v0300_golden_cases_keep_their_output_with_the_flag(self):
        """Durations only: no row names a period, and every other field stays byte-identical."""
        golden = json.loads(SUPPORT_GOLDEN.read_text(encoding='utf-8'))
        for relative, text in golden['files'].items():
            self.write(relative, text, bom=relative in golden['bom'])
        for name, case in sorted(golden['cases'].items()):
            for base in ([], ['--primary-text-only']):
                code, default, stderr = self.run_cli(*base, path=case['path'])
                self.assertEqual((code, stderr), (case['exit'], case['stderr']))
                if not base:
                    plain = json.loads(default)
                    for key in TABLE_ONLY_KEYS:
                        plain.pop(key)
                    self.assertEqual(ssot._encode(plain).decode() + '\n', case['stdout'])
                for extra in (['--normalize-periods'], ['--normalize-periods', '--fiscal-start', '04']):
                    with self.subTest(case=name, base=base, extra=extra):
                        code, flagged, stderr = self.run_cli(*base, *extra, path=case['path'])
                        self.assertEqual((code, stderr), (case['exit'], case['stderr']))
                        result = json.loads(flagged)
                        self.assertEqual(result['normalized_periods'], [])
                        self.assertEqual(ssot._encode(self.without_periods(result)).decode() + '\n', default)

    def test_qualified_periods_and_partial_ranges_abstain_end_to_end(self):
        """Never the whole period or one end: 말 with a particle, 초, Q before FY, a yearless range end."""
        self.write('notes/Plan.md', '2026년 3분기 완공 예정. FY2026 실적 요약. 2026.10.07 출발.\n')
        self.write('notes/facts.md', self.rows(
            ('2026년 3분기 말에 완공', '[[Plan]]'), ('2026.10.07~10.09 출장', '[[Plan]]'), ('Q3 FY2026', '[[Plan]]'),
            ('2026년 3분기 초', '[[Plan]]'), ('2026.10.07~2026.10.09 출장', '[[Plan]]'), ('FY2026 Q3', '[[Plan]]')))
        via = ['notes/Plan.md']
        none = {'normalized_match': 'none'}
        partial = {'normalized_match': 'abstain', 'reason': 'partial_range'}
        qualifier = {'normalized_match': 'abstain', 'reason': 'period_qualifier'}
        expected = {
            None: [none, partial, qualifier, qualifier, none,
                   {'normalized_match': 'abstain', 'reason': 'fiscal_start_undeclared'}],
            1: [none, partial, qualifier, qualifier, none, {'normalized_match': 'equal', 'via': via}],
            4: [{'normalized_match': 'abstain', 'reason': 'quarter_basis_ambiguous'}, partial, qualifier, qualifier,
                none, {'normalized_match': 'contained', 'via': via}],
        }
        default = self.check()
        for fiscal_start, rows in expected.items():
            with self.subTest(fiscal_start=fiscal_start):
                flagged = self.check(normalize_periods=True, fiscal_start=fiscal_start)
                self.assertEqual(flagged['normalized_periods'], [dict(line=line, **row) for line, row in enumerate(rows, 3)])
                self.assertEqual(ssot._encode(self.without_periods(flagged)), ssot._encode(default))
        code, out, err = self.run_cli('--normalize-periods', '--fiscal-start', '04')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['normalized_periods'][:3], [dict(line=3, **expected[4][0]),
                                                                     dict(line=4, **partial), dict(line=5, **qualifier)])

    def test_a_period_only_in_another_ledgers_fact_table_is_marked(self):
        """One fact row (valid_from, valid_until, recorded) and no prose, against a ledger that also has prose."""
        fact = ledger([['Beta', 'size', '42', '2025-01-01', '2026-01-01', '2026-10-07', '[[X]]', '', 'confirmed']])
        self.write('notes/Other.md', fact)
        self.write('notes/Prose.md', fact + '\n착수일은 2026.10.07이다.\n')
        self.write('notes/facts.md', self.rows(
            ('2025년 1월 1일', '[[Other]]'), ('2026.10.07', '[[Other]]'), ('2026.01.01', '[[Other]]'),
            ('2026년 4분기', '[[Other]]'), ('2026.10.07', '[[Prose]]'), ('2026.10.07', '[[Other]], [[Meeting]]'),
            ('2026.10.07', '[[Other]], [[Prose]]')))
        other, prose = ['notes/Other.md'], ['notes/Prose.md']
        flagged = self.check(normalize_periods=True)
        self.assertEqual(flagged['normalized_periods'], [
            {'line': 3, 'normalized_match': 'equal', 'via': other, 'via_ledger_table_only': other},
            {'line': 4, 'normalized_match': 'equal', 'via': other, 'via_ledger_table_only': other},
            {'line': 5, 'normalized_match': 'equal', 'via': other, 'via_ledger_table_only': other},
            {'line': 6, 'normalized_match': 'contained', 'via': other, 'via_ledger_table_only': other},
            {'line': 7, 'normalized_match': 'equal', 'via': prose},
            {'line': 8, 'normalized_match': 'equal', 'via': self.VIA + other, 'via_ledger_table_only': other},
            {'line': 9, 'normalized_match': 'equal', 'via': other + prose, 'via_ledger_table_only': other}])
        # ledger_table_only lists none of them: no value is written as in Other's table, and the
        # two literal supports (lines 7 and 9) come from Prose's prose.
        self.assertEqual((flagged['ledger_table_only'], flagged['rows_supported']), ([], 2))
        primary = self.check(normalize_periods=True, primary_text_only=True)
        self.assertEqual(primary['normalized_periods'], [
            {'line': 3, 'normalized_match': 'none'}, {'line': 4, 'normalized_match': 'none'},
            {'line': 5, 'normalized_match': 'none'}, {'line': 6, 'normalized_match': 'none'},
            {'line': 7, 'normalized_match': 'equal', 'via': prose},
            {'line': 8, 'normalized_match': 'equal', 'via': self.VIA},
            {'line': 9, 'normalized_match': 'equal', 'via': prose}])
        for base, result in (({}, flagged), ({'primary_text_only': True}, primary)):
            with self.subTest(base=base):
                self.assertEqual(ssot._encode(self.without_periods(result)), ssot._encode(self.check(**base)))
        code, out, err = self.run_cli('--normalize-periods')
        self.assertEqual((code, err, json.loads(out)['normalized_periods']), (0, '', flagged['normalized_periods']))
        dumped = json.dumps(flagged['normalized_periods'], ensure_ascii=False)
        for literal in ('2025-01-01', '2026-10-07', '2026.10.07', 'Beta', '42'):
            self.assertNotIn(literal, dumped)

    def test_a_cited_body_past_the_period_text_limit_is_left_out_and_the_exit_code_kept(self):
        """NFKC expands U+FDFA to 18 characters, so a 256 KiB note passes vault_period.MAX_TEXT_CHARS."""
        period = importlib.import_module('vault_period')
        note = '2026년 3분기 ' + 'ﷺ' * 85_333
        self.assertLessEqual(len(note.encode('utf-8')), ssot.MAX_BYTES)
        self.assertGreater(len(ssot._literal(note)), period.MAX_TEXT_CHARS)
        self.write('notes/Long.md', note)
        self.write('notes/facts.md', self.rows(('2026년 3분기', '[[Long]]'), ('2026년 3분기', '[[Long]], [[Meeting]]')))
        code, default, stderr = self.run_cli()
        self.assertEqual((code, stderr, json.loads(default)['rows_supported']), (0, '', 2))
        code, flagged, stderr = self.run_cli('--normalize-periods')
        self.assertEqual((code, stderr), (0, ''))
        result = json.loads(flagged)
        self.assertEqual(result['normalized_periods'], [
            {'line': 3, 'normalized_match': 'none', 'incomplete': ['period_text_limit']},
            {'line': 4, 'normalized_match': 'equal', 'via': self.VIA, 'incomplete': ['period_text_limit']}])
        self.assertEqual(ssot._encode(self.without_periods(result)).decode() + '\n', default)

    def test_narrowing_words_after_a_period_abstain_end_to_end(self):
        """후반, 마지막 날, 종료일 or 첫 달 narrow the period: the value abstains, and a body using one is not indexed."""
        self.write('notes/Plan.md', '신제품은 2026년 하반기 양산 목표. 3공장은 2026년 3분기 완공 예정. '
                                    '2공장은 2026년 4분기 끝까지 점검.\n')
        self.write('notes/facts.md', self.rows(
            ('2026년 하반기 후반 양산', '[[Plan]]'), ('2026년 3분기 마지막 날 완공', '[[Plan]]'),
            ('2026년 3분기 종료일 완공', '[[Plan]]'), ('2026년 3분기의 첫 달 완공', '[[Plan]]'),
            ('2026년 3분기 중 완공', '[[Plan]]'), ('2026년 3분기 말에 완공', '[[Plan]]'), ('2026년 4분기 점검', '[[Plan]]')))
        qualifier = {'normalized_match': 'abstain', 'reason': 'period_qualifier'}
        ambiguous = {'normalized_match': 'abstain', 'reason': 'quarter_basis_ambiguous'}
        none = {'normalized_match': 'none'}
        expected = {None: [qualifier] * 4 + [{'normalized_match': 'equal', 'via': ['notes/Plan.md']}, none, none],
                    4: [qualifier] * 4 + [ambiguous] * 3}
        default = self.check()
        for fiscal_start, rows in expected.items():
            with self.subTest(fiscal_start=fiscal_start):
                flagged = self.check(normalize_periods=True, fiscal_start=fiscal_start)
                self.assertEqual(flagged['normalized_periods'], [dict(line=line, **row) for line, row in enumerate(rows, 3)])
                self.assertEqual(ssot._encode(self.without_periods(flagged)), ssot._encode(default))
        code, out, err = self.run_cli('--normalize-periods')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['normalized_periods'][0], dict(line=3, **qualifier))

    def test_a_multi_interval_value_resting_on_ledger_tables_is_marked(self):
        """A full range or two dates: each ledger whose fact tables the result needs is named, alone or together."""
        def fact(subject, start, until=''):
            return ledger([[subject, 'size', '7', start, until, '2026-10-07', '[[X]]', '', 'confirmed']])
        # L's prose names 2026.03.31 and its fact table 2026-01-01; M is only a table; R has nine tables (refused).
        self.write('notes/L.md', '# L\n\n메모: 2026.03.31 종료.\n\n' + fact('Beta', '2026-01-01'))
        self.write('notes/M.md', fact('Gamma', '2026-01-01', '2026-07-01'))
        self.write('notes/Plain.md', '2026.01.01 착수.\n')
        self.write('notes/R.md', '# R\n\n' + '\n\n'.join(fact('R%d' % number, '2026-02-02') for number in range(9)))
        self.write('notes/facts.md', self.rows(
            ('2026.01.01~2026.03.31', '[[L]]'), ('2026.01.01, 2026.03.31', '[[L]]'),
            ('2026.01.01, 2026.03.31', '[[L]], [[M]]'), ('2026.03.31', '[[L]]'),
            ('2026.01.01~2026.03.31', '[[L]], [[Plain]]'), ('2026.02.02', '[[R]]')))
        other, table, plain, refused = ['notes/L.md'], ['notes/M.md'], ['notes/Plain.md'], ['notes/R.md']
        flagged = self.check(normalize_periods=True)
        self.assertEqual(flagged['normalized_periods'], [
            {'line': 3, 'normalized_match': 'equal', 'via': other, 'via_ledger_table_only': other},
            {'line': 4, 'normalized_match': 'equal', 'via': other, 'via_ledger_table_only': other},
            {'line': 5, 'normalized_match': 'equal', 'via': other + table, 'via_ledger_table_only': other + table},
            {'line': 6, 'normalized_match': 'equal', 'via': other},
            {'line': 7, 'normalized_match': 'equal', 'via': other + plain},
            # parse_ledger refuses R: its tables cannot be left out, so it is compared whole and never named.
            {'line': 8, 'normalized_match': 'equal', 'via': refused}])
        primary = self.check(normalize_periods=True, primary_text_only=True)
        self.assertEqual(primary['normalized_periods'], [
            {'line': 3, 'normalized_match': 'none'}, {'line': 4, 'normalized_match': 'none'},
            {'line': 5, 'normalized_match': 'none'}, {'line': 6, 'normalized_match': 'equal', 'via': other},
            {'line': 7, 'normalized_match': 'equal', 'via': other + plain},
            {'line': 8, 'normalized_match': 'equal', 'via': refused}])
        self.assertEqual(primary['ledger_tables_unseparated'], [{'path': 'notes/R.md', 'reason': 'table_limit'}])
        # A named path is a ledger with separable tables that the primary comparison drops or answers more weakly.
        rank = {'equal': 0, 'contained': 1, 'none': 2}
        for entry, alone in zip(flagged['normalized_periods'], primary['normalized_periods']):
            weaker = rank[alone['normalized_match']] > rank[entry['normalized_match']]
            with self.subTest(line=entry['line']):
                self.assertEqual(entry.get('via_ledger_table_only', []), [
                    path for path in entry['via'] if path in ('notes/L.md', 'notes/M.md')
                    and (weaker or path not in alone.get('via', []))])
        for base in ({}, {'primary_text_only': True}):
            with self.subTest(base=base):
                self.assertEqual(ssot._encode(self.without_periods(self.check(normalize_periods=True, **base))),
                                 ssot._encode(self.check(**base)))

    def test_markup_in_a_cited_body_never_brings_the_whole_period_back(self):
        """Body indexes read only the rendered forms, as the value side reads value_forms[-1]."""
        bodies = {'Bold': '3공장은 **2026년 3분기** 초 착수.', 'Link': '3공장은 [[2026년 3분기]] 초 착수.',
                  'Code': '3공장은 `2026년 3분기` 초 착수.', 'Periodic': '[[2026-Q3]] 초 착수.',
                  'Narrow': '**2026년 3분기** 후반 착수.', 'Alias': '[[Plan|2026년 3분기]] 마지막 날 착수.',
                  'Count': '**2026년 3분기** 연속 흑자.', 'EndBold': '3공장은 **2026년 3분기** 말에 착수.',
                  'EndLink': '3공장은 [[2026년 3분기]] 말에 착수.', 'Plain': '3공장은 2026년 3분기 착수.',
                  'BoldOnly': '3공장은 **2026년 3분기** 착수.', 'AliasOnly': '[[Q3 계획|2026년 3분기]] 착수.'}
        for name, text in bodies.items():
            self.write(f'notes/{name}.md', text + '\n')
        self.write('notes/facts.md', self.rows(*[('2026년 3분기 착수', f'[[{name}]]') for name in bodies]))
        none, end = {'normalized_match': 'none'}, 'contained'
        expected = [none] * 7 + [{'normalized_match': end, 'via': ['notes/EndBold.md']},
                                 {'normalized_match': end, 'via': ['notes/EndLink.md']}]
        expected += [{'normalized_match': 'equal', 'via': [f'notes/{name}.md']} for name in ('Plain', 'BoldOnly', 'AliasOnly')]
        flagged = self.check(normalize_periods=True)
        self.assertEqual(flagged['normalized_periods'], [dict(line=line, **row) for line, row in enumerate(expected, 3)])
        self.assertEqual(ssot._encode(self.without_periods(flagged)), ssot._encode(self.check()))
        # A Q mark in bold before a fiscal year is read as a mark too.
        self.write('notes/Fiscal.md', 'Q3 **FY2026** 실적.\n')
        self.write('notes/fiscal-facts.md', self.rows(('FY2026', '[[Fiscal]]')))
        self.assertEqual(self.check('notes/fiscal-facts.md', normalize_periods=True, fiscal_start=4)['normalized_periods'],
                         [{'line': 3, 'normalized_match': 'none'}])

    def test_unread_marks_list_items_and_range_forms_abstain_end_to_end(self):
        """Never the whole fiscal year, one list item or one range end; fully written forms still compare."""
        self.write('notes/Plan.md', 'FY2026 실적 요약. 2026년 1분기 실적. 점검은 2026.03.05에 했다. 2026.10.07 출발. '
                                    '계약은 2026.12.31 종료. 2027년 3분기 납품. 2026년 3분기 완공 예정.\n')
        qualifier = {'normalized_match': 'abstain', 'reason': 'period_qualifier'}
        multiple = {'normalized_match': 'abstain', 'reason': 'multiple_periods'}
        partial = {'normalized_match': 'abstain', 'reason': 'partial_range'}
        rows = [(value, qualifier) for value in ('FY2026/Q3 실적', 'FY2026, Q3 실적', 'Q3/FY2026 실적', 'FY2026_Q3',
                                                 'FY2026 Q 3 실적', 'FY2026 Q5', '2026년 3분기 2주 차 완공',
                                                 '2026년 3분기' + chr(0x200B) + '말에 완공')]
        rows += [(value, multiple) for value in ('2026Q1·Q2 실적', '2026Q1, Q2 실적', '점검 2026.03.05, 09.05',
                                                 '점검 2026년 3월 5일과 9월 5일')]
        rows += [(value, partial) for value in ('출장 2026.10.07에서 10.09까지', '출장 2026.10.07(수요일)~10.09(금요일)',
                                                '출장 2026.10.07\N{HORIZONTAL BAR}10.09', '계약 2026년 10월~2026.12.31',
                                                '계약 2026년 10월부터 2026년 12월 31일까지', '2026년~2027년 3분기 납품')]
        via = ['notes/Plan.md']
        rows += [('FY2026 Q3 실적', {'normalized_match': 'equal', 'via': via}), ('계약 2026.10.01~2026.12.31', {'normalized_match': 'none'}),
                 ('2026년 1분기 실적', {'normalized_match': 'equal', 'via': via}),
                 ('매출 1500 Q3 대비', {'normalized_match': 'abstain', 'reason': 'no_year'})]
        self.write('notes/facts.md', self.rows(*[(value, '[[Plan]]') for value, _ in rows]))
        flagged = self.check(normalize_periods=True, fiscal_start=1)
        self.assertEqual(flagged['normalized_periods'], [dict(line=line, **row) for line, (_, row) in enumerate(rows, 3)])
        self.assertEqual(ssot._encode(self.without_periods(flagged)), ssot._encode(self.check()))
        code, out, err = self.run_cli('--normalize-periods', '--fiscal-start', '1')
        self.assertEqual((code, err, json.loads(out)['normalized_periods']), (0, '', flagged['normalized_periods']))

    def test_a_damaged_period_module_is_a_content_free_error(self):
        """A vault_period that fails to import: SSOTError('period_normalizer_unavailable'), and no traceback from the CLI."""
        import shutil
        self.write('notes/facts.md', self.rows(('2026년 3분기', '[[Meeting]]')))
        copy = Path(self.tmp.name) / 'scripts'
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns('__pycache__'))
        (copy / 'vault_period.py').write_text('def broken(:\n', encoding='utf-8')
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')

        def run(*arguments):
            return subprocess.run([sys.executable, '-X', 'utf8', *arguments], capture_output=True, text=True,
                                  encoding='utf-8', env=environment)
        command = [str(copy / 'vault_ssot.py'), '--vault', str(self.vault), 'check-support', '--path', 'notes/facts.md']
        completed = run(*command, '--normalize-periods')
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr),
                         (1, '', '{"error":"ssot_operation_unavailable"}\n'))
        # The default path never imports it.
        completed = run(*command)
        self.assertEqual((completed.returncode, completed.stderr, json.loads(completed.stdout)['rows_checked']), (0, '', 1))
        script = ('import sys\nsys.path.insert(0, sys.argv[1])\nimport vault_ssot\ntry:\n'
                  "    vault_ssot.check_support(sys.argv[2], 'notes/facts.md', normalize_periods=True)\n"
                  'except vault_ssot.SSOTError as exc:\n    print(exc)\n')
        completed = run('-c', script, str(copy), str(self.vault))
        self.assertEqual((completed.returncode, completed.stdout, completed.stderr),
                         (0, 'period_normalizer_unavailable\n', ''))


# --- Oracles: the v0.31.0 body helpers (vault_ssot.py:387-398, :586-616 and the
# self-citation call at :702 of 21c4b47), kept only here so that the tests can
# show where the current helpers part from them. ---
def v0310_note_body(text):
    lines = text.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((i for i, line in enumerate(lines[1:65], 1) if line.strip() == '---'), None)
        if end is not None:
            return '\n'.join(lines[end + 1:])
    return text


def v0310_body_forms(text):
    return ssot._forms(re.sub(r'\\([\\|])', r'\1', v0310_note_body(text)), aliases=True)


def v0310_without_tables(raw, parsed):
    pieces, cursor = [], 0
    for table in parsed['tables']:
        pieces.append(raw[cursor:table['start']])
        cursor = table['end']
    pieces.append(raw[cursor:])
    return b''.join(pieces).decode('utf-8')


def v0310_separate_tables(text):
    body = v0310_note_body(text)
    try:
        parsed = ssot.parse_ledger(body)
    except ssot.SSOTError as exc:
        return None, str(exc)
    if not parsed['tables']:
        return None, None
    return ssot._forms(re.sub(r'\\([\\|])', r'\1', v0310_without_tables(body.encode('utf-8'), parsed)),
                       aliases=True), None


def v0310_self_forms(raw, parsed):
    return v0310_body_forms(v0310_without_tables(raw, parsed))


V0310_BODY_HELPERS = {'_body_forms': v0310_body_forms, '_separate_tables': v0310_separate_tables,
                      '_self_forms': v0310_self_forms}
LINE_MARKS = ('\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x85', '\N{LINE SEPARATOR}', '\N{PARAGRAPH SEPARATOR}')


class BodyEdgeSupportTests(unittest.TestCase):
    """check-support bodies at the three edges v0.31.0 read differently.

    The text on either side of a removed fact table stays apart, a cited note
    breaks lines where parse_ledger does (CR, LF, CRLF) with or without
    frontmatter, and a self-cited ledger drops a leading BOM before its
    frontmatter is decided. Away from these edges every output equals the
    v0.31.0 helpers.
    """

    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.write('00-meta/vault-config.json', '{"deny_zones": ["private"], "exclude_dirs": ["excluded"]}')

    def write(self, relative, text, bom=False):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8'))
        return target

    @staticmethod
    def rows(*pairs):
        """A v2 ledger of (value, source) rows; without a preamble the first row is line 3."""
        return ledger([['Atlas', 'retention', value, '2025-01-01', '', '2026-10-07', source, '', 'confirmed']
                       for value, source in pairs])

    def check(self, path='notes/facts.md', **options):
        return ssot.check_support(self.vault, path, **options)

    @staticmethod
    def found(result):
        return [(w['line'], w['code'], w['details'].get('reason')) for w in result['warnings']]

    def test_text_on_either_side_of_a_removed_table_is_never_read_as_one_value(self):
        # v0.31.0 joined the two sides of a cut, so 2026년 3분기 and 12 34 (each only in a
        # table) were also found outside the tables: never table-only, never unsupported.
        note = ('기준 연도 2026년\n' + self.rows(('2026년 3분기', '[[facts]]')) + '3분기 계획은 별도. 보존 12\n'
                + self.rows(('12 34', '')) + '34일 이후 점검.\n')
        self.write('notes/facts.md', self.rows(
            ('2026년 3분기', '[[Other]]'), ('12 34', '[[Other]]'), ('기준 연도 2026년', '[[Other]]'),
            ('3분기 계획은 별도', '[[Other]]'), ('보존 12', '[[Other]]'), ('34일 이후 점검', '[[Other]]')))
        listed = [{'line': 3, 'via': ['notes/Other.md']}, {'line': 4, 'via': ['notes/Other.md']}]
        for front in ('', '---\ntitle: Other\n---\n'):
            for newline in ('\n', '\r\n', '\r'):
                with self.subTest(frontmatter=bool(front), newline=repr(newline)):
                    self.write('notes/Other.md', (front + note).replace('\n', newline))
                    default, flagged = self.check(), self.check(primary_text_only=True)
                    # Text right above and right below a cut is still found on its own.
                    self.assertEqual((default['warnings'], default['rows_supported']), ([], 6))
                    self.assertEqual((default['ledger_table_only'], flagged['ledger_table_only']), (listed, listed))
                    self.assertEqual(self.found(flagged), [(3, 'ssot-source-unsupported', 'ledger_table_only'),
                                                           (4, 'ssot-source-unsupported', 'ledger_table_only')])

    def test_periods_and_their_qualifiers_are_never_read_across_a_removed_table(self):
        # v0.31.0 read 2026년 above a table and 3분기 below it as 2026년 3분기, and a quarter above
        # a table with 말에 below it as the quarter's last day (which then marked End).
        self.write('notes/Year.md', '기준 연도 2026년\n' + self.rows(('24개월', '[[facts]]')) + '3분기 계획은 별도.\n')
        self.write('notes/End.md', '완공 목표 2026년 3분기\n' + self.rows(('36개월', '[[facts]]')) + '말에 재점검.\n')
        self.write('notes/facts.md', self.rows(('2026년 3분기', '[[Year]]'), ('2026년 3분기', '[[End]]')))
        expected = [{'line': 3, 'normalized_match': 'none'},
                    {'line': 4, 'normalized_match': 'equal', 'via': ['notes/End.md']}]
        for options in ({}, {'primary_text_only': True}):
            with self.subTest(options=options):
                self.assertEqual(self.check(normalize_periods=True, **options)['normalized_periods'], expected)

    def test_a_self_citation_never_joins_the_text_around_its_own_tables(self):
        self.write('notes/facts.md', '기준 연도 2026년\n' + self.rows(
            ('2026년 3분기', '[[#근거]]'), ('기준 연도 2026년', '[[#근거]]'), ('3분기 계획은 별도', '[[#근거]]'))
            + '3분기 계획은 별도.\n')
        result = self.check(normalize_periods=True)
        self.assertEqual(self.found(result), [(4, 'ssot-source-unsupported', None)])
        self.assertEqual(result['warnings'][0]['details'], {'cited': ['notes/facts.md']})
        self.assertEqual(result['normalized_periods'][0], {'line': 4, 'normalized_match': 'none'})
        # The marks never move the frontmatter boundary, which the v0.30.0 order decides once the
        # tables are cut: a closing '---' right above a table still closes the block, and '---'
        # right above and right below a table still reads as an empty block.
        self.write('notes/Tight.md', '---\ntitle: 36개월\n---\n'
                   + self.rows(('36개월', '[[#근거]]'), ('48개월', '[[#근거]]')) + '메모: 48개월.\n')
        self.assertEqual(self.found(self.check('notes/Tight.md')), [(6, 'ssot-source-unsupported', None)])
        self.write('notes/Rule.md', '---\n' + self.rows(('72개월', '[[#근거]]')) + '---\na: 72개월\n---\n본문\n')
        self.assertEqual(self.found(self.check('notes/Rule.md')), [])

    def test_a_ledger_supporting_a_period_through_its_own_prose_is_not_marked_after_a_cut(self):
        # The documented v0.31.0 case: L1's text around its table read as 2026년 3분기 once the
        # tables were left out, so the result grew stronger and L2 dropped out of via.
        self.write('notes/L1.md', '기준 연도 2026년\n' + self.rows(('24개월', '[[facts]]')) + '3분기 계획은 별도.\n')
        self.write('notes/L2.md', self.rows(('36개월', '[[facts]]')) + '\n점검 2026.09.15 완료.\n')
        self.write('notes/facts.md', self.rows(('2026년 3분기', '[[L1]], [[L2]]')))
        for options in ({}, {'primary_text_only': True}):
            with self.subTest(options=options):
                self.assertEqual(self.check(normalize_periods=True, **options)['normalized_periods'],
                                 [{'line': 3, 'normalized_match': 'contained', 'via': ['notes/L2.md']}])
        # Documented limit: the table's own first line can still hold a qualifier of the period
        # above it (a header without a leading pipe that starts with 말). Leaving the table out
        # then lifts it, the joint comparison grows stronger and L2 is marked again.
        self.write('notes/L1.md', '기준 연도 2026년 3분기\n말 | subject | relation | value |\n| --- | --- | --- | --- |\n'
                                  '| x | Atlas | retention | 24개월 |\n')
        self.assertEqual(self.check(normalize_periods=True)['normalized_periods'], [
            {'line': 3, 'normalized_match': 'contained', 'via': ['notes/L1.md', 'notes/L2.md'],
             'via_ledger_table_only': ['notes/L2.md']}])
        self.assertEqual(self.check(normalize_periods=True, primary_text_only=True)['normalized_periods'],
                         [{'line': 3, 'normalized_match': 'equal', 'via': ['notes/L1.md']}])

    def test_a_bom_prefixed_ledger_leaves_its_frontmatter_out_of_a_self_citation(self):
        # v0.31.0 decoded the ledger with its BOM, so the first line was not '---' and the
        # frontmatter (title 36개월, period 2026년 3분기) supported its self-citations.
        text = ('---\ntitle: 36개월\nperiod: 2026년 3분기\n---\n## 근거\n세부 값 48개월.\n\n'
                + self.rows(('36개월', '[[#근거]]'), ('48개월', '[[#근거]]'), ('2026년 3분기', '[[#근거]]')))
        for newline in ('\n', '\r\n'):
            for bom in (False, True):
                with self.subTest(newline=repr(newline), bom=bom):
                    self.write('notes/facts.md', text.replace('\n', newline), bom=bom)
                    result = self.check(normalize_periods=True)
                    self.assertEqual(self.found(result), [(10, 'ssot-source-unsupported', None),
                                                          (12, 'ssot-source-unsupported', None)])
                    self.assertEqual(result['normalized_periods'], [{'line': 12, 'normalized_match': 'none'}])

    def test_a_cited_table_reads_alike_with_or_without_frontmatter(self):
        # parse_ledger breaks lines at CR and LF only. Below frontmatter v0.31.0 rejoined the
        # body on str.splitlines, which also breaks at each of these marks.
        def layouts(mark):
            header = [field + mark if field == 'relation' else field for field in FIELDS]
            cell = ledger([['Atlas', 'retention', '24개월', '2025-01-01', '', '2026-10-07', '[[facts]]', '', 'confirmed']],
                          fields=header)
            pipe = self.rows(('12개월', '[[facts]]'), ('36개월', '[[facts]]')).split('\n')
            pipe[2] += mark
            return {'mark in a header cell': ('24개월', cell, True),
                    'mark after a closing pipe': ('36개월', '\n'.join(pipe), True),
                    'table-shaped line': ('48개월', '| subject | relation | value |' + mark + '| --- | --- | --- |'
                                          + mark + '| Atlas | retention | 48개월 |\n', False)}
        for mark in LINE_MARKS:
            for name, (value, text, table) in layouts(mark).items():
                with self.subTest(mark=hex(ord(mark)), layout=name):
                    self.write('notes/Front.md', '---\ntitle: F\n---\n' + text)
                    self.write('notes/Bare.md', text)
                    self.write('notes/facts.md', self.rows((value, '[[Front]]'), (value, '[[Bare]]')))
                    default, flagged = self.check(), self.check(primary_text_only=True)
                    listed = [{'line': 3, 'via': ['notes/Front.md']}, {'line': 4, 'via': ['notes/Bare.md']}]
                    self.assertEqual(default['ledger_table_only'], listed if table else [])
                    self.assertEqual((default['rows_supported'], flagged['rows_supported']), (2, 0 if table else 2))
                    self.assertEqual(flagged['ledger_tables_unseparated'], [])

    def test_frontmatter_lines_and_links_break_where_parse_ledger_breaks(self):
        ls = '\N{LINE SEPARATOR}'
        # A first line '---' that runs on past U+2028 is one line and opens no frontmatter.
        self.write('notes/Opener.md', '---' + ls + 'title: x\n본문 72개월\n---\n끝\n')
        # U+2028 breaks no longer count toward the 64-line frontmatter limit, so this block closes.
        self.write('notes/Window.md', '---\n' + ('x' + ls) * 70 + 'title: 84개월\n---\n본문\n')
        # A wiki link that holds U+2028 renders below frontmatter as it does without one.
        link = '메모 [[Plan' + ls + 'A|보존 60개월]] 확인.\n'
        self.write('notes/Link.md', '---\ntitle: L\n---\n' + link)
        self.write('notes/BareLink.md', link)
        self.write('notes/facts.md', self.rows(('72개월', '[[Opener]]'), ('84개월', '[[Window]]'),
                                               ('메모 보존 60개월 확인', '[[Link]]'),
                                               ('메모 보존 60개월 확인', '[[BareLink]]')))
        result = self.check()
        self.assertEqual((self.found(result), result['rows_supported']), ([(4, 'ssot-source-unsupported', None)], 3))
        # A self-citation decides its frontmatter on the same lines.
        self.write('notes/Self.md', '---' + ls + 'x\n세부 값 77일.\n---\n\n' + self.rows(('77일', '[[#근거]]')))
        self.assertEqual(self.found(self.check('notes/Self.md')), [])

    def corpus(self):
        """Write ledgers that cite fact-table notes and ledgers that cite themselves; return their paths.

        Values and periods sit right above and right below fact tables but never span a cut,
        line-break marks other than CR and LF sit only in prose below a frontmatter block, and
        no BOM-prefixed ledger cites its own frontmatter.
        """
        ls, nel = '\N{LINE SEPARATOR}', '\x85'
        layouts = (
            lambda k, table, second: table,
            lambda k, table, second: (f'메모 {k}2개월 기준. 2026년 3분기 착수.\n{table}'
                                      f'점검 2026.08.15 완료, {k}3개월 유지.\n'),
            lambda k, table, second: f'# 근거\n\n메모 {k}2개월.\n\n{table}\n{k}3개월 유지.\n',
            lambda k, table, second: f'메모 {k}2개월.\n{table}중간 {k}3개월.\n{second}끝.',
            lambda k, table, second: f'메모 {k}2개월{ls}다음 줄.\n{table}{k}3개월{nel}유지.\n',
        )
        paths, number = [], 0
        for newline, name in (('\n', 'lf'), ('\r\n', 'crlf'), ('\r', 'cr')):
            facts = []
            for layout in layouts:
                for front in (False, True):
                    for bom in (False, True):
                        number += 1
                        k = number
                        body = layout(k, self.rows((f'{k}1개월', '[[facts]]'), (f'{k}6개월', '')),
                                      self.rows((f'{k}7개월', '')))
                        head = f'---\ntitle: N{k}\nkeep: {k}4개월\n---\n' if front else ''
                        self.write(f'corpus/N{k}.md', (head + body).replace('\n', newline), bom=bom)
                        # 2026.10.07 is only in the tables' recorded cells (2026-10-07).
                        facts += [(value, f'[[N{k}]]') for value in (
                            f'{k}1개월', f'{k}2개월', f'{k}3개월', f'{k}4개월', f'{k}5개월', f'{k}7개월',
                            '2026년 3분기', '2026.08.15', '2026.10.07')]
            self.write(f'corpus/facts-{name}.md', self.rows(*facts))
            paths.append(f'corpus/facts-{name}.md')
            own = self.rows(*[(value, '[[#근거]]') for value in ('66일', '77일', '88일', '99일', '2026년 3분기')])
            selves = {
                'Tight': (f'메모 77일.\n{own}99일 유지. 2026년 3분기 착수.\n', False),
                'Front': (f'---\ntitle: S\nkeep: 66일\n---\n메모 77일.\n{own}99일 유지.\n', False),
                'FrontTable': (f'---\ntitle: S\nkeep: 66일\n---\n{own}메모 77일.\n', False),
                'Quirk': (f'{own}---\n메모: 77일 기준.\n---\n끝 99일.\n', False),
                'Rule': (f'---\n{own}---\na: 77일\n---\n본문 99일\n', False),
                'Marks': (f'---\ntitle: S\n---\n메모 77일{ls}다음.\n{own}99일{nel}유지.\n', False),
                'Bom': (f'메모 77일.\n{own}99일 유지.\n', True),
                'BomFront': (f'---\ntitle: S\n---\n메모 77일.\n{own}99일 유지.\n', True),
            }
            for label, (text, bom) in selves.items():
                self.write(f'corpus/Self{label}-{name}.md', text.replace('\n', newline), bom=bom)
                paths.append(f'corpus/Self{label}-{name}.md')
        return paths

    def test_outputs_equal_the_v0310_helpers_away_from_these_edges(self):
        golden = json.loads(SUPPORT_GOLDEN.read_text(encoding='utf-8'))
        for relative, text in golden['files'].items():
            self.write(relative, text, bom=relative in golden['bom'])
        paths = ['ledgers/A.md', 'ledgers/B.md', 'ledgers/C.md', *self.corpus()]
        options = ({}, {'primary_text_only': True}, {'normalize_periods': True},
                   {'normalize_periods': True, 'primary_text_only': True},
                   {'normalize_periods': True, 'fiscal_start': 4})
        listed = marked = 0
        for path in paths:
            for option in options:
                with self.subTest(path=path, options=option):
                    current = self.check(path, **option)
                    with mock.patch.multiple(ssot, **V0310_BODY_HELPERS):
                        former = self.check(path, **option)
                    self.assertEqual(ssot._encode(current), ssot._encode(former))
                    listed += len(current['ledger_table_only'])
                    marked += sum('via_ledger_table_only' in row for row in current.get('normalized_periods', ()))
        # The corpus reaches the table-only listing and the period marking.
        self.assertGreater(listed, 0)
        self.assertGreater(marked, 0)
        # The oracles do differ at an edge: the cut of the first test above.
        self.write('notes/Other.md', '기준 연도 2026년\n' + self.rows(('2026년 3분기', '[[edge]]')) + '3분기 계획은 별도.\n')
        self.write('notes/edge.md', self.rows(('2026년 3분기', '[[Other]]')))
        with mock.patch.multiple(ssot, **V0310_BODY_HELPERS):
            self.assertEqual(self.check('notes/edge.md')['ledger_table_only'], [])
        self.assertEqual(self.check('notes/edge.md')['ledger_table_only'], [{'line': 3, 'via': ['notes/Other.md']}])


class DefaultCliRegressionTests(unittest.TestCase):
    """Golden check/prepare outputs captured from v0.22.1 (63b4cc9) before check-support."""

    def setUp(self):
        self.assertIsNotNone(ssot, 'temporal ledger implementation is missing')

    def test_check_and_prepare_outputs_equal_v0221_golden(self):
        golden = json.loads(GOLDEN.read_text(encoding='utf-8'))['ssot']
        self.assertEqual(sorted(golden['cases']), ['check_legacy', 'check_v2', 'prepare_legacy', 'prepare_v2'])
        for name, case in sorted(golden['cases'].items()):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                vault = Path(tmp) / 'vault'
                for relative, text in (('00-meta/vault-config.json', golden['config']),
                                       ('notes/facts.md', case['ledger']), ('notes/Evidence.md', golden['evidence'])):
                    target = vault / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(text.encode('utf-8'))
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch('sys.stdout', stdout), mock.patch('sys.stderr', stderr):
                    code = ssot.main(['--vault', str(vault), case['command'], '--path', 'notes/facts.md'])
                self.assertEqual((code, stdout.getvalue(), stderr.getvalue()),
                                 (case['exit'], case['stdout'], case['stderr']))

    def test_support_codes_stay_outside_lint_and_promotion(self):
        lint = importlib.import_module('vault_lint_extensions')
        self.assertFalse(SUPPORT_CODES & lint.WARNING_CODES)
        self.assertFalse(SUPPORT_CODES & lint.PROMOTABLE_CODES)


if __name__ == '__main__':
    unittest.main()
