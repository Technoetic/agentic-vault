"""Opt-in, content-free support-row and physical-line binding diagnostics."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/agentic-vault/scripts'))
ssot = importlib.import_module('vault_ssot')
sys.path.insert(0, str(ROOT / 'scripts'))
audit = importlib.import_module('real_vault_audit')
FIELDS = ['subject', 'relation', 'value', 'source']


def ledger(rows):
    return '| ' + ' | '.join(FIELDS) + ' |\n| --- | --- | --- | --- |\n' + ''.join(
        '| ' + ' | '.join(row) + ' |\n' for row in rows)


class SupportRowDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name)
        self.write('00-meta/vault-config.json', '{"deny_zones":["private"]}')
        self.write('notes/Evidence.md', 'Atlas retains 12 months.\n')
        self.rows = [['Atlas', 'retention', '12', '[[Evidence]]']]
        self.set_rows(self.rows)

    def write(self, path, text):
        target = self.vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode('utf-8'))
        return target

    def set_rows(self, rows, prefix=''):
        return self.write('notes/facts.md', prefix + ledger(rows))

    def check(self, **options):
        return ssot.check_support(self.vault, 'notes/facts.md', **options)

    def test_export_covers_all_row_states_and_preserves_exact_default(self):
        self.set_rows(self.rows + [['Atlas', 'r', '99', '[[Evidence]], [[Missing]]'],
                                  ['Atlas', 'r', '12', '[[Missing]]'],
                                  ['Atlas', 'r', '', '[[Evidence]]'],
                                  ['Atlas', 'r', '12', 'https://example.org']])
        baseline = self.check()
        result = self.check(row_references=True, bind_subject=True)
        self.assertEqual(baseline, {k: v for k, v in result.items() if k not in
                                   ('row_references', 'audit_population', 'binding_observations', 'binding_warnings')})
        self.assertEqual(baseline, self.check(row_references=False, bind_subject=False))
        self.assertEqual([r['status'] for r in result['row_references']],
                         ['supported', 'unsupported', 'unresolved', 'skipped', 'skipped'])
        self.assertEqual([r['line'] for r in result['row_references']], [3, 4, 5, 6, 7])
        self.assertEqual(result['row_references'][0], dict(path='notes/facts.md', line=3,
                         status='supported', reason='literal_present', via=['notes/Evidence.md']))
        self.assertNotIn('retention', json.dumps(result['row_references']))
        population = audit.population_from(result['audit_population'])
        self.assertEqual(len(population['items']), 5)
        self.assertEqual(population['source_sha256'], baseline['source_sha256'])

    def test_wrong_subject_warns_separately_without_changing_support(self):
        self.write('notes/Evidence.md', 'Birch retains 12 months.\n')
        result = self.check(bind_subject=True)
        self.assertEqual((result['rows_supported'], result['warnings']), (1, []))
        observation = result['binding_observations'][0]
        self.assertEqual((observation['status'], observation['reason']),
                         ('subject_absent', 'subject_not_on_value_line'))
        self.assertEqual(result['binding_warnings'][0]['code'], 'ssot-subject-unbound')
        self.assertEqual(observation['via'], [dict(path='notes/Evidence.md', line=1)])
        self.assertNotIn('Birch', json.dumps(result))

    def test_subject_and_value_must_share_original_physical_line(self):
        for text, status in [('Atlas\n12 months\n', 'subject_absent'),
                             ('Atlas retains **12** months\n', 'bound'),
                             ('---\nsubject: Atlas\n---\n12 months\n', 'subject_absent'),
                             ('| Atlas | 12 |\n', 'bound'),
                             ('Atlas 112\nBirch 12\n', 'subject_absent')]:
            with self.subTest(text=text):
                self.write('notes/Evidence.md', text)
                self.assertEqual(self.check(bind_subject=True)['binding_observations'][0]['status'], status)

    def test_explicit_subject_aliases_nfkc_and_numeric_boundaries(self):
        for subject, text, status in [('[[Atlas\\|A]]', 'A **12**\n', 'bound'),
                                      ('Atlas', '[[Atlas|A]] 12\n', 'bound'),
                                      ('ＡＴＬＡＳ', '`atlas` 12\n', 'bound'),
                                      ('Atlas', 'Atlas2 12\n', 'subject_absent'),
                                      ('12', '112 12.5\nBirch 12\n', 'bound')]:
            with self.subTest(subject=subject, text=text):
                self.set_rows([[subject, 'r', '12', '[[Evidence]]']])
                self.write('notes/Evidence.md', text)
                self.assertEqual(self.check(bind_subject=True)['binding_observations'][0]['status'], status)

    def test_empty_subject_and_missing_or_failed_sources_are_unassessed(self):
        for subject, source, reason in [('', '[[Evidence]]', 'subject_missing'),
                                        ('Atlas', '[[Evidence]], [[Missing]]', 'source_incomplete'),
                                        ('Atlas', '[[private/Denied]]', 'literal_not_supported')]:
            with self.subTest(subject=subject, source=source):
                self.set_rows([[subject, 'r', '12', source]])
                self.write('notes/Evidence.md', 'Birch 12\n')
                observation = self.check(bind_subject=True)['binding_observations'][0]
                self.assertEqual((observation['status'], observation['reason']), ('unassessed', reason))
        self.set_rows(self.rows)
        self.write('notes/Evidence.md', 'Atlas 12\x00\n')
        result = self.check(bind_subject=True)
        self.assertEqual(result['binding_observations'][0]['status'], 'unassessed')
        self.assertEqual(result['binding_warnings'], [])

    def test_bom_crlf_evidence_keeps_original_line_numbers(self):
        self.write('notes/Evidence.md', '\ufeff---\r\ntitle: Atlas\r\n---\r\n\r\nAtlas **12**\r\n')
        observation = self.check(bind_subject=True)['binding_observations'][0]
        self.assertEqual(observation['status'], 'bound')
        self.assertEqual(observation['via'], [dict(path='notes/Evidence.md', line=5)])

    def test_fact_tables_and_self_citations_never_supply_binding(self):
        self.write('notes/Evidence.md', ledger(self.rows) + '\nBirch 12\n')
        for primary in (False, True):
            result = self.check(bind_subject=True, primary_text_only=primary)
            self.assertEqual(result['binding_observations'][0]['status'], 'subject_absent')
        self.set_rows([['Atlas', 'r', '12', '[[#source]]']], 'Birch 12\n\n')
        self.assertEqual(self.check(bind_subject=True)['binding_observations'][0]['status'], 'subject_absent')
        self.set_rows([['Atlas', 'r', '12', '[[#source]]']], 'Atlas 12\n\n')
        observation = self.check(bind_subject=True)['binding_observations'][0]
        self.assertEqual(observation['via'], [dict(path='notes/facts.md', line=1)])

    def test_removed_table_does_not_join_original_lines(self):
        self.write('notes/Evidence.md', 'Atlas\n\n' + ledger(self.rows) + '\n12\n')
        self.assertEqual(self.check(bind_subject=True)['binding_observations'][0]['status'], 'subject_absent')

    def test_source_limits_and_parse_refusals_stay_unassessed(self):
        for patch, text in [({'MAX_SUPPORT_NOTES': 0}, 'Atlas 12\n'),
                            ({'MAX_SUPPORT_BYTES': 1}, 'Atlas 12\n'),
                            ({'MAX_ROWS': 1}, ledger(self.rows * 2) + 'Atlas 12\n')]:
            with self.subTest(patch=patch):
                self.write('notes/Evidence.md', text)
                with mock.patch.multiple(ssot, **patch):
                    result = self.check(bind_subject=True)
                self.assertEqual(result['binding_observations'][0]['status'], 'unassessed')
                self.assertEqual(result['binding_warnings'], [])

    def test_cited_source_read_once_and_default_does_not_cache_raw_text(self):
        original = ssot.stable_read
        reads = []
        def counting(resolve, *args, **kwargs):
            path = resolve()
            if path.name == 'Evidence.md':
                reads.append(path)
            return original(lambda: path, *args, **kwargs)
        with mock.patch.object(ssot, 'stable_read', counting):
            self.check(bind_subject=True, row_references=True)
        self.assertEqual(len(reads), 1)
        seen = []
        original_forms = ssot._cited_forms
        def capture(policy, relative, run):
            result = original_forms(policy, relative, run)
            seen.append('binding_raw' in run)
            return result
        with mock.patch.object(ssot, '_cited_forms', capture):
            self.check()
        self.assertEqual(seen, [False])

    def test_stale_source_and_policy_generation_are_still_refused(self):
        original = ssot._cited_forms
        for change, error in [('source', 'stale_source'), ('policy', 'configuration_changed')]:
            with self.subTest(change=change):
                self.write('00-meta/vault-config.json', '{"deny_zones":["private"]}')
                self.write('notes/Evidence.md', 'Atlas 12\n')
                def racing(*args):
                    result = original(*args)
                    if change == 'source':
                        self.write('notes/Evidence.md', 'Atlas 12 changed\n')
                    else:
                        self.write('00-meta/vault-config.json', '{"deny_zones":["notes"]}')
                    return result
                with mock.patch.object(ssot, '_cited_forms', racing):
                    with self.assertRaisesRegex(ssot.SSOTError, error):
                        self.check(bind_subject=True, row_references=True)

    def test_cli_flags_and_audit_draw_roundtrip(self):
        script = ROOT / 'skills/agentic-vault/scripts/vault_ssot.py'
        command = [sys.executable, str(script), '--vault', str(self.vault), 'check-support',
                   '--path', 'notes/facts.md']
        default = subprocess.run(command, capture_output=True, check=True)
        flagged = subprocess.run(command + ['--row-references', '--bind-subject'], capture_output=True, check=True)
        result = json.loads(flagged.stdout)
        for key, value in json.loads(default.stdout).items():
            self.assertEqual(result[key], value)
        population = self.write('population.json', json.dumps(result['audit_population']))
        sample = self.vault / 'sample.json'
        run = subprocess.run([sys.executable, str(ROOT / 'scripts/real_vault_audit.py'), 'draw',
                              '--population', str(population), '--out', str(sample), '--k', '1', '--seed', 'abcd'],
                             capture_output=True)
        self.assertEqual(run.returncode, 0, (run.stdout, run.stderr))

    def test_options_require_real_bools_before_source_read(self):
        for option in ('row_references', 'bind_subject'):
            for value in (1, None, 'true'):
                with self.subTest(option=option, value=value):
                    with self.assertRaisesRegex(ssot.SSOTError, 'invalid_opt_in'):
                        self.check(**{option: value})

    def test_each_option_alone_preserves_serialized_default_fields(self):
        for base in ({}, {'primary_text_only': True}, {'normalize_periods': True},
                     {'normalize_periods': True, 'primary_text_only': True}):
            baseline = self.check(**base)
            for options in ({'row_references': True}, {'bind_subject': True},
                            {'row_references': True, 'bind_subject': True}):
                with self.subTest(base=base, options=options):
                    result = self.check(**base, **options)
                    old = {key: value for key, value in result.items() if key in baseline}
                    self.assertEqual(ssot._encode(old), ssot._encode(baseline))

    def test_mixed_refusals_and_reference_limit_do_not_certify_subject_absence(self):
        self.write('notes/Evidence.md', 'Birch 12\n')
        sources = ['[[Evidence]], [[private/Denied]]', '[[Evidence]], [[Large]]',
                   '[[Evidence]], [[Malformed]]', '[[Evidence]], [[Unclosed]]']
        self.write('notes/Large.md', 'x' * (256 * 1024 + 1))
        self.write('notes/Malformed.md', '| subject | relation | value |\n| --- | --- | --- |\n| Atlas | 12 |\n')
        self.write('notes/Unclosed.md', '---\nAtlas 12\n')
        for source in sources:
            with self.subTest(source=source):
                self.set_rows([['Atlas', 'r', '12', source]])
                result = self.check(bind_subject=True)
                self.assertEqual(result['rows_supported'], 1)
                self.assertEqual(result['binding_observations'][0]['status'], 'unassessed')
                self.assertEqual(result['binding_warnings'], [])
        self.set_rows([['Atlas', 'r', '12', '[[Evidence]], [[Missing]]']])
        with mock.patch.object(ssot, 'MAX_REFERENCES', 1):
            result = self.check(bind_subject=True)
        self.assertEqual(result['binding_observations'][0]['status'], 'unassessed')
        self.assertEqual(result['incomplete'], ['reference_limit'])

    def test_read_failure_does_not_turn_mixed_source_into_subject_absence(self):
        self.write('notes/Evidence.md', 'Birch 12\n')
        self.write('notes/Unreadable.md', 'Atlas 12\n')
        self.set_rows([['Atlas', 'r', '12', '[[Evidence]], [[Unreadable]]']])
        original = ssot.stable_read
        def unreadable(resolve, *args, **kwargs):
            target = resolve()
            if target.name == 'Unreadable.md':
                raise OSError('private body must not leak')
            return original(lambda: target, *args, **kwargs)
        with mock.patch.object(ssot, 'stable_read', unreadable):
            result = self.check(bind_subject=True)
        self.assertEqual(result['binding_observations'][0]['status'], 'unassessed')
        self.assertNotIn('private body', json.dumps(result))

    def test_binding_scan_finds_late_bound_line_with_bounded_metadata(self):
        self.write('notes/Evidence.md', 'Birch 12\n' * 100 + 'Atlas 12\n' * 100)
        observation = self.check(bind_subject=True)['binding_observations'][0]
        self.assertEqual(observation['status'], 'bound')
        self.assertEqual(observation['via'][0], dict(path='notes/Evidence.md', line=101))
        self.assertLessEqual(len(observation['via']), 32)

    def test_fact_table_only_and_split_value_are_not_binding_absence(self):
        for text in [ledger(self.rows), 'Atlas 12\nmonths\n']:
            with self.subTest(text=text):
                self.write('notes/Evidence.md', text)
                self.set_rows([['Atlas', 'r', '12' if text.startswith('|') else '12 months', '[[Evidence]]']])
                result = self.check(bind_subject=True)
                self.assertEqual(result['rows_supported'], 1)
                self.assertEqual(result['binding_observations'][0]['status'], 'unassessed')
                self.assertEqual(result['binding_warnings'], [])

    def test_markup_only_subject_never_becomes_a_binding(self):
        for subject in ('**', '__', '[[#anchor]]'):
            with self.subTest(subject=subject):
                self.set_rows([[subject, 'r', '12', '[[Evidence]]']])
                self.write('notes/Evidence.md', subject + ' 12\n')
                observation = self.check(bind_subject=True)['binding_observations'][0]
                self.assertEqual((observation['status'], observation['reason']), ('unassessed', 'subject_missing'))

    def test_marker_only_subjects_are_unassessed_with_formatted_or_plain_values(self):
        for subject in ('*', '_', '***', '___', '*_', '**__**', '`**`', '***___***'):
            for value in ('12', '**12**', '__12__'):
                with self.subTest(subject=subject, value=value):
                    self.set_rows([[subject, 'r', '12', '[[Evidence]]']])
                    self.write('notes/Evidence.md', 'Birch retains ' + value + ' months.\n')
                    baseline = self.check()
                    result = self.check(bind_subject=True)
                    observation = result['binding_observations'][0]
                    self.assertEqual((observation['status'], observation['reason'], observation['via']),
                                     ('unassessed', 'subject_missing', []))
                    self.assertEqual(result['binding_warnings'], [])
                    self.assertEqual(baseline, {k: v for k, v in result.items() if k in baseline})

    def test_substantive_alias_does_not_allow_marker_target_to_bind(self):
        self.set_rows([['[[***\\|Atlas]]', 'r', '12', '[[Evidence]]']])
        self.write('notes/Evidence.md', 'Birch retains **12** months.\n')
        observation = self.check(bind_subject=True)['binding_observations'][0]
        self.assertEqual(observation['status'], 'subject_absent')
        self.write('notes/Evidence.md', 'ATLAS retains **12** months.\n')
        self.assertEqual(self.check(bind_subject=True)['binding_observations'][0]['status'], 'bound')


if __name__ == '__main__':
    unittest.main()
