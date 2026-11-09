import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / 'scripts/evaluate_relation_preservation.py'


def module():
    spec = importlib.util.spec_from_file_location('relation_evaluation_under_test', EVALUATOR)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class PairedRelationEvaluationTests(unittest.TestCase):
    def test_same_cases_have_authored_labels_and_a_discriminating_negative_control(self):
        m = module()
        result = m.evaluate()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['case_count'], 16)
        self.assertEqual(result['arms']['record_probes']['correct_cases'], 16)
        self.assertGreater(result['arms']['literal']['missed_changes'], 0)
        self.assertGreater(result['paired']['record_only_matches'], 0)
        broken = m.evaluate(record_arm='literal-control')
        self.assertEqual(broken['status'], 'needs_review')
        self.assertGreater(broken['arms']['record_probes']['missed_changes'], 0)
        self.assertEqual(broken['arms']['record_probes'], broken['arms']['literal'])

    def test_source_and_pack_are_bound_and_output_does_not_repeat_input_text(self):
        m = module()
        result = m.evaluate()
        self.assertEqual(len(result['pack_sha256']), 64)
        self.assertEqual(len(result['source_sha256']), 3)
        self.assertTrue(all(len(x) == 64 for x in result['source_sha256'].values()))
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('[[Sample A]]', text)
        self.assertIn('not_semantic_truth', text)
        self.assertNotIn('original', result['cases'][0])
        self.assertNotIn('candidate', result['cases'][0])

    def test_authored_label_is_not_inferred_from_checker_output(self):
        m = module()
        pack, _ = m.load_pack()
        altered = copy.deepcopy(pack)
        altered['cases'][1]['expected_preserved'] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pack.json'
            path.write_text(json.dumps(altered), encoding='utf-8')
            result = m.evaluate(path)
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['arms']['record_probes']['correct_cases'], 15)

    def test_bad_pack_missing_cases_types_duplicates_and_unknown_fields_are_rejected(self):
        m = module()
        pack, _ = m.load_pack()
        bad = []
        for change in ('empty', 'duplicate', 'bool_version', 'str_label', 'unknown'):
            item = copy.deepcopy(pack)
            if change == 'empty': item['cases'] = []
            elif change == 'duplicate': item['cases'][1]['id'] = item['cases'][0]['id']
            elif change == 'bool_version': item['schema_version'] = True
            elif change == 'str_label': item['cases'][0]['expected_preserved'] = 'true'
            else: item['cases'][0]['extra'] = 'unexpected'
            bad.append(item)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pack.json'
            for item in bad:
                path.write_text(json.dumps(item), encoding='utf-8')
                with self.subTest(item=item):
                    with self.assertRaises(ValueError): m.evaluate(path)

    def test_cli_negative_control_has_nonzero_exit_and_no_network_requirement(self):
        result = subprocess.run([sys.executable, str(EVALUATOR), '--record-arm', 'literal-control'],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['status'], 'needs_review')
        self.assertIn('no_model', payload['limitations'])


if __name__ == '__main__':
    unittest.main()
