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
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    ssot = importlib.import_module('vault_ssot')
except ModuleNotFoundError:
    ssot = None

FIELDS = ['subject', 'relation', 'value', 'valid_from', 'valid_until', 'recorded', 'source', 'superseded_by', 'status']


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
        def racing_atomic(target, data, before_replace):
            self.path.write_bytes(self.original + b'Raced writer\n')
            return atomic(target, data, before_replace)
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


if __name__ == '__main__':
    unittest.main()
