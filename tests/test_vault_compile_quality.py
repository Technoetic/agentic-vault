import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
MODULE = SCRIPTS / 'vault_compile_quality.py'
GOLDEN = Path(__file__).resolve().parent / 'fixtures' / 'v0221_default_outputs.json'
REVERSE_FIELDS = {'target_literals_checked', 'unsupported_target_literals', 'unsupported_by_kind',
                  'fabrication_candidate_rate', 'fabrication_scope'}


def load_module():
    spec = importlib.util.spec_from_file_location('compile_quality_test_module', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompileQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        (self.vault / '00-meta').mkdir()
        (self.vault / '00-meta/vault-config.json').write_text(json.dumps({'deny_zones': ['private'], 'exclude_dirs': ['10-inbox']}), encoding='utf-8')
        spec = importlib.util.spec_from_file_location('compile_quality_test_module', MODULE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def write(self, path, text):
        target = self.vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        return {'path': path, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

    def test_preserves_required_literals_and_binds_originals_before_quarantine(self):
        source = self.write('10-inbox/source.md', 'Name: Atlas\nDate: 2026-10-07\nAmount: 125,000 KRW\nApproval remains pending.\n')
        target = self.write('20-knowledge/result.md', 'Atlas: 125,000 KRW on 2026-10-07. Approval remains pending.\n')
        result = self.module.check_compile(self.vault, [source], [target], ['Approval remains pending.'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['missing_facts'], [])
        self.assertTrue(result['originals_current'])
        self.assertEqual(result['semantic_authority'], 'advisory_unverified')

    def test_lost_number_date_name_and_explicit_claim_are_visible(self):
        source = self.write('10-inbox/source.md', 'Name: Atlas\nDate: 2026-10-07\nAmount: 125,000 KRW\nApproval remains pending.\n')
        target = self.write('20-knowledge/result.md', 'An event occurred.\n')
        result = self.module.check_compile(self.vault, [source], [target], ['Approval remains pending.'])
        self.assertEqual(result['status'], 'needs_review')
        self.assertGreaterEqual(len(result['missing_facts']), 4)
        self.assertEqual({r['kind'] for r in result['missing_facts']}, {'number', 'date', 'name', 'claim'})

    def test_source_drift_blocks_a_successful_coverage_report(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12.\n')
        self.write('10-inbox/source.md', 'Value: 13.\n')
        with self.assertRaisesRegex(self.module.QualityError, 'source_changed'):
            self.module.check_compile(self.vault, [source], [target], [])

    def test_target_drift_is_not_bound_as_the_compiled_artifact(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12.\n')
        self.write('20-knowledge/result.md', 'Value: 0.\n')
        with self.assertRaisesRegex(self.module.QualityError, 'target_changed'):
            self.module.check_compile(self.vault, [source], [target], [])

    def test_denied_source_is_never_read_even_if_all_facts_would_match(self):
        source = self.write('private/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12.\n')
        with self.assertRaises(self.module.QualityError):
            self.module.check_compile(self.vault, [source], [target], [])

    def test_runtime_and_nonmarkdown_paths_are_not_compile_inputs(self):
        for path in ('00-meta/.agentic-vault/runtime/fake.md', '20-knowledge/config.env'):
            with self.subTest(path=path):
                source = self.write(path, 'Value: 12.\n')
                target = self.write('20-knowledge/result.md', 'Value: 12.\n')
                with self.assertRaises(self.module.QualityError):
                    self.module.check_compile(self.vault, [source], [target], [])

    def test_forged_required_claim_not_in_sources_is_rejected(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12. Forged conclusion.\n')
        with self.assertRaisesRegex(self.module.QualityError, 'claim_not_in_source'):
            self.module.check_compile(self.vault, [source], [target], ['Forged conclusion.'])

    def test_duplicate_or_unbounded_sources_are_not_silently_merged(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12.\n')
        with self.assertRaises(self.module.QualityError):
            self.module.check_compile(self.vault, [source, source], [target], [])
        large = self.write('10-inbox/large.md', 'x' * (1024 * 1024))
        with self.assertRaises(self.module.QualityError):
            self.module.check_compile(self.vault, [large], [target], [])

    def test_korean_number_units_are_checked_as_required_facts(self):
        source = self.write('10-inbox/source.md', '수량: 12개\n금액: 350만원\n')
        target = self.write('20-knowledge/result.md', '수량과 금액을 확인했다.\n')
        result = self.module.check_compile(self.vault, [source], [target], [])
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual({r['literal'] for r in result['missing_facts']}, {'12개', '350만원'})

    def test_numeric_prefix_does_not_satisfy_required_quantity(self):
        source = self.write('10-inbox/source.md', '수량: 12개\n')
        target = self.write('20-knowledge/result.md', '수량: 125개\n')
        result = self.module.check_compile(self.vault, [source], [target], [])
        self.assertEqual(result['status'], 'needs_review')

    def test_date_with_an_extra_digit_is_not_the_original_date(self):
        source = self.write('10-inbox/source.md', 'Date: 2026-10-07\n')
        for changed in ('2026-10-070', '12026-10-07'):
            with self.subTest(changed=changed):
                target = self.write('20-knowledge/result.md', 'Date: ' + changed + '\n')
                result = self.module.check_compile(self.vault, [source], [target], [])
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['missing_facts'][0]['literal'], '2026-10-07')

    def test_korean_name_must_not_be_a_prefix_of_a_different_name(self):
        source = self.write('10-inbox/source.md', 'Name: 홍길동\n')
        for changed in ('홍길동철', '김홍길동', '홍길동은'):
            with self.subTest(changed=changed):
                target = self.write('20-knowledge/result.md', 'Name: ' + changed + '\n')
                result = self.module.check_compile(self.vault, [source], [target], [])
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['missing_facts'][0]['literal'], '홍길동')
        target = self.write('20-knowledge/result.md', '담당자: 홍길동.\n')
        self.assertEqual(self.module.check_compile(self.vault, [source], [target], [])['status'], 'complete')

    def test_repeated_dates_have_linear_match_boundary_work_near_file_cap(self):
        # Instrument regex match boundary access rather than impose a flaky
        # wall-clock threshold. Stop excessive work early on the old scanner.
        for count in (2000, 22000):
            text = '2026-10-07 ' * count
            calls = [0]
            limit = 8 * count + 128
            class CountedMatch:
                def __init__(self, match):
                    self.match = match
                def start(self):
                    calls[0] += 1
                    if calls[0] > limit:
                        raise AssertionError('date/number boundary work exceeded linear budget')
                    return self.match.start()
                def end(self):
                    calls[0] += 1
                    if calls[0] > limit:
                        raise AssertionError('date/number boundary work exceeded linear budget')
                    return self.match.end()
                def group(self, *args):
                    return self.match.group(*args)
            class CountedRegex:
                def __init__(self, regex):
                    self.regex = regex
                def finditer(self, value):
                    return (CountedMatch(match) for match in self.regex.finditer(value))
            with self.subTest(bytes=len(text.encode('utf-8'))):
                with patch.object(self.module, 'DATE_RE', CountedRegex(self.module.DATE_RE)), \
                        patch.object(self.module, 'NUMBER_RE', CountedRegex(self.module.NUMBER_RE)):
                    facts = self.module._fact_rows([{'path': 'source.md', 'text': text}])
                self.assertEqual(facts, {('date', '2026-10-07'): {'source.md'}})
                self.assertLessEqual(calls[0], limit)

    def test_empty_fact_set_needs_host_review(self):
        source = self.write('10-inbox/source.md', 'An unstructured narrative.\n')
        target = self.write('20-knowledge/result.md', 'A different narrative.\n')
        result = self.module.check_compile(self.vault, [source], [target], [])
        self.assertEqual(result['status'], 'needs_review')
        self.assertIsNone(result['literal_coverage'])
        self.assertEqual(result['review_reason'], 'no_selected_literal_facts')

    def test_wiki_heading_target_name_is_checked(self):
        source = self.write('10-inbox/source.md', 'The owner is [[Atlas#Responsibilities|project lead]].\n')
        target = self.write('20-knowledge/result.md', 'The owner remains unspecified.\n')
        result = self.module.check_compile(self.vault, [source], [target], [])
        self.assertIn('atlas', {r['literal'] for r in result['missing_facts']})

    def test_source_changes_during_analysis_never_get_current_originals_receipt(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12.\n')
        real = self.module._fact_rows
        def drifting(rows):
            result = real(rows)
            if rows[0]['path'] == source['path']:
                self.write(source['path'], 'Value: 99.\n')
            return result
        with patch.object(self.module, '_fact_rows', side_effect=drifting):
            with self.assertRaisesRegex(self.module.QualityError, 'source_changed'):
                self.module.check_compile(self.vault, [source], [target], [])

    def test_host_claims_count_towards_combined_fact_limit(self):
        source = self.write('10-inbox/source.md', 'Value: 12. Host claim.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12. Host claim.\n')
        with patch.object(self.module, 'MAX_FACTS', 1):
            with self.assertRaisesRegex(self.module.QualityError, 'fact_limit'):
                self.module.check_compile(self.vault, [source], [target], ['Host claim.'])

    def test_final_selected_reads_do_not_hide_a_policy_replacement(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12.\n')
        real = self.module._bindings
        calls = []
        def drifting(store, rows, role, budget):
            result = real(store, rows, role, budget)
            calls.append(role)
            if len(calls) == 4:
                (self.vault / '00-meta/vault-config.json').write_text(
                    json.dumps({'deny_zones': ['private', '10-inbox']}), encoding='utf-8')
            return result
        with patch.object(self.module, '_bindings', side_effect=drifting):
            with self.assertRaisesRegex(self.module.QualityError, 'policy_changed'):
                self.module.check_compile(self.vault, [source], [target], [])


class ReverseLiteralTests(unittest.TestCase):
    """Opt-in precision direction: target literals that no selected source contains."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        (self.vault / '00-meta').mkdir()
        (self.vault / '00-meta/vault-config.json').write_bytes(b'{"deny_zones": ["private"], "exclude_dirs": ["10-inbox"]}')
        self.module = load_module()

    def write(self, path, text):
        target = self.vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode('utf-8')
        target.write_bytes(data)
        return {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}

    def check(self, source_text, target_text, claims=()):
        source = self.write('10-inbox/source.md', source_text)
        target = self.write('20-knowledge/result.md', target_text)
        return self.module.check_compile(self.vault, [source], [target], list(claims), report_unsupported=True)

    def test_unsupported_number_date_and_name_are_listed_without_changing_status(self):
        result = self.check('Name: Atlas\nDate: 2026-10-07\nAmount: 125,000 KRW\n',
                            'Name: Atlas\nName: Borealis\nAtlas: 125,000 KRW and 999 units on 2026-10-07 and 2027-01-01.\n')
        self.assertEqual(result['status'], 'complete')
        self.assertIsNone(result['review_reason'])
        self.assertEqual(result['missing_facts'], [])
        self.assertEqual(result['unsupported_target_literals'], [
            {'kind': 'date', 'literal': '2027-01-01', 'target_paths': ['20-knowledge/result.md']},
            {'kind': 'name', 'literal': 'borealis', 'target_paths': ['20-knowledge/result.md']},
            {'kind': 'number', 'literal': '999', 'target_paths': ['20-knowledge/result.md']}])
        self.assertEqual(result['target_literals_checked'], 6)
        self.assertEqual(result['unsupported_by_kind'], {'date': 1, 'name': 1, 'number': 1})
        self.assertEqual(result['fabrication_candidate_rate'], 0.5)
        self.assertIn('a literal is not truth', result['fabrication_scope'])

    def test_supported_target_literals_have_zero_candidate_rate(self):
        result = self.check('Name: Atlas\nDate: 2026-10-07\nAmount: 125,000 KRW\n',
                            'Name: Atlas\nAtlas: 125,000 KRW on 2026-10-07.\n')
        self.assertEqual(result['unsupported_target_literals'], [])
        self.assertEqual(result['target_literals_checked'], 3)
        self.assertEqual(result['fabrication_candidate_rate'], 0.0)

    def test_target_without_literals_has_no_rate_and_keeps_review_reason(self):
        result = self.check('An unstructured narrative.\n', 'A different narrative.\n')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['review_reason'], 'no_selected_literal_facts')
        self.assertEqual(result['target_literals_checked'], 0)
        self.assertEqual(result['unsupported_target_literals'], [])
        self.assertIsNone(result['fabrication_candidate_rate'])

    def test_empty_target_list_is_still_an_input_error(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        with self.assertRaisesRegex(self.module.QualityError, 'invalid_target_list'):
            self.module.check_compile(self.vault, [source], [], [], report_unsupported=True)

    def test_reverse_names_use_the_forward_word_boundaries(self):
        self.assertEqual(self.check('Atlases are maps.\n', 'Name: Atlas\n')['unsupported_target_literals'],
                         [{'kind': 'name', 'literal': 'atlas', 'target_paths': ['20-knowledge/result.md']}])
        self.assertEqual(self.check('담당자: 홍길동.\n', 'Name: 홍길동\n')['unsupported_target_literals'], [])
        # Documented false alarm: an attached Korean particle is another literal.
        self.assertEqual(self.check('홍길동은 승인했다.\n', 'Name: 홍길동\n')['unsupported_by_kind']['name'], 1)

    def test_format_and_unit_variants_are_documented_false_alarms(self):
        result = self.check('Amount: 125000 KRW\nDate: 2026.10.07\n', 'Amount: 125,000 KRW on 2026-10-07.\n')
        self.assertEqual({row['literal'] for row in result['unsupported_target_literals']}, {'125,000', '2026-10-07'})

    def test_frontmatter_dates_and_host_claims_are_not_target_literals(self):
        result = self.check('Value: 12. Host claim.\n', '---\nupdated: 2026-10-17\n---\nValue: 12. Host claim.\n',
                            ['Host claim.'])
        self.assertEqual(result['target_literals_checked'], 1)
        self.assertEqual(result['unsupported_target_literals'], [])

    def test_existing_fact_limit_is_reused_for_both_directions(self):
        source = self.write('10-inbox/source.md', 'Value: 12.\n')
        target = self.write('20-knowledge/result.md', 'Value: 12 and 13.\n')
        for flag in (False, True):
            with self.subTest(report_unsupported=flag), patch.object(self.module, 'MAX_FACTS', 1):
                with self.assertRaisesRegex(self.module.QualityError, 'fact_limit'):
                    self.module.check_compile(self.vault, [source], [target], [], report_unsupported=flag)

    def test_cli_flag_adds_fields_without_changing_exit_codes(self):
        script = str(MODULE)
        cases = (('Value: 12.\n', 'Value: 12 and 999.\n', 0, 'complete'),
                 ('Value: 12.\n', 'Value: 999.\n', 1, 'needs_review'))
        for source_text, target_text, code, status in cases:
            source = self.write('10-inbox/source.md', source_text)
            target = self.write('20-knowledge/result.md', target_text)
            request = json.dumps({'sources': [source], 'targets': [target], 'required_claims': []}).encode()
            with self.subTest(status=status):
                plain = subprocess.run([sys.executable, script, '--vault', str(self.vault)],
                                       input=request, capture_output=True)
                opted = subprocess.run([sys.executable, script, '--vault', str(self.vault), '--report-unsupported'],
                                       input=request, capture_output=True)
                self.assertEqual((plain.returncode, opted.returncode), (code, code), opted.stderr.decode())
                default, extended = json.loads(plain.stdout), json.loads(opted.stdout)
                self.assertEqual(extended['status'], status)
                self.assertFalse(REVERSE_FIELDS & set(default))
                self.assertEqual({key: extended[key] for key in default}, default)
                self.assertEqual(extended['unsupported_target_literals'],
                                 [{'kind': 'number', 'literal': '999', 'target_paths': ['20-knowledge/result.md']}])


class DefaultOutputRegressionTests(unittest.TestCase):
    """Golden outputs captured from v0.22.1 (63b4cc9) before the opt-in reverse check."""

    def setUp(self):
        self.golden = json.loads(GOLDEN.read_text(encoding='utf-8'))['compile']
        self.module = load_module()

    def build(self, tmp, case):
        vault = Path(tmp)
        bindings = []
        for relative, text in (('00-meta/vault-config.json', self.golden['config']),
                               ('10-inbox/source.md', case['source']),
                               ('20-knowledge/result.md', case['target'])):
            target = vault / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode('utf-8'))
            bindings.append({'path': relative, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
        return vault, bindings[1], bindings[2]

    def test_default_api_output_equals_v0221_golden(self):
        self.assertEqual(len(self.golden['cases']), 5)
        for name, case in sorted(self.golden['cases'].items()):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                vault, source, target = self.build(tmp, case)
                self.assertEqual(self.module.check_compile(vault, [source], [target], case['required_claims']),
                                 case['api'])
                self.assertEqual(self.module.check_compile(vault, [source], [target], case['required_claims'],
                                                           report_unsupported=False), case['api'])

    def test_default_cli_output_is_byte_identical_to_v0221_golden(self):
        for name, case in sorted(self.golden['cases'].items()):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                vault, source, target = self.build(tmp, case)
                request = json.dumps({'sources': [source], 'targets': [target],
                                      'required_claims': case['required_claims']}).encode('utf-8')
                stdin = type('Stdin', (), {'buffer': io.BytesIO(request)})()
                stdout = io.StringIO()
                with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
                    code = self.module.main(['--vault', str(vault)])
                self.assertEqual(code, case['cli']['exit'])
                self.assertEqual(stdout.getvalue(), case['cli']['stdout'])

    def test_opt_in_keeps_every_existing_field_and_only_adds_reverse_fields(self):
        for name, case in sorted(self.golden['cases'].items()):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                vault, source, target = self.build(tmp, case)
                result = self.module.check_compile(vault, [source], [target], case['required_claims'],
                                                   report_unsupported=True)
                self.assertEqual({key: result[key] for key in case['api']}, case['api'])
                self.assertEqual(set(result) - set(case['api']), REVERSE_FIELDS)

    def test_frontmatter_wiki_golden_reports_only_the_extra_number(self):
        case = self.golden['cases']['frontmatter_wiki']
        with tempfile.TemporaryDirectory() as tmp:
            vault, source, target = self.build(tmp, case)
            result = self.module.check_compile(vault, [source], [target], [], report_unsupported=True)
        self.assertEqual(result['unsupported_target_literals'],
                         [{'kind': 'number', 'literal': '7', 'target_paths': ['20-knowledge/result.md']}])
        self.assertEqual(result['fabrication_candidate_rate'], 0.25)


if __name__ == '__main__':
    unittest.main()
