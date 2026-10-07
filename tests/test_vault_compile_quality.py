import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
MODULE = SCRIPTS / 'vault_compile_quality.py'


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


if __name__ == '__main__':
    unittest.main()
