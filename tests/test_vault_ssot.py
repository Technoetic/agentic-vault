from __future__ import annotations

import hashlib
import io
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    ssot = importlib.import_module('vault_ssot')
except ModuleNotFoundError:
    ssot = None

FIELDS = ['subject', 'relation', 'value', 'valid_from', 'valid_until', 'recorded', 'source', 'superseded_by', 'status']
GOLDEN = Path(__file__).resolve().parent / 'fixtures' / 'v0221_default_outputs.json'
SUPPORT_CODES = {'ssot-source-unsupported', 'ssot-source-unresolved'}


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
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True, text=True)
        if result.returncode:
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
