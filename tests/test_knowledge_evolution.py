import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

class EvolutionTests(unittest.TestCase):
    def test_current_truth_precedes_history_and_missing_is_unverified(self):
        from evaluate_knowledge_evolution import classify_claim
        self.assertEqual(classify_claim('color', 'blue', {'color': 'blue'}, [{'color': 'red'}]), 'supported')
        self.assertEqual(classify_claim('color', 'red', {'color': 'blue'}, [{'color': 'red'}]), 'stale')
        self.assertEqual(classify_claim('color', 'green', {'color': 'blue'}, [{'color': 'red'}]), 'invented')
        self.assertEqual(classify_claim('color', 'red', {}, [{'color': 'red'}]), 'unverified')

class ReplayTests(unittest.TestCase):
    def test_replay_detects_wrong_expected_classification_and_impact(self):
        import copy
        from evaluate_knowledge_evolution import load_fixture, evaluate, DEFAULT
        fixture = load_fixture(DEFAULT)
        result = evaluate(fixture)
        self.assertTrue(result['matches'])
        self.assertEqual(result['pooled']['denominator'], 6)
        self.assertIsNone(result['checkpoints'][-1]['supported_rate'])
        wrong = copy.deepcopy(fixture)
        wrong[1]['claims'][0]['expected'] = 'supported'
        self.assertFalse(evaluate(wrong)['matches'])
        wrong = copy.deepcopy(fixture)
        wrong[1]['expected_source'] = 'current'
        self.assertFalse(evaluate(wrong)['matches'])

    def test_cli_malformed_and_mismatch_exit_codes(self):
        import json, subprocess, tempfile
        from evaluate_knowledge_evolution import DEFAULT
        script = Path(__file__).resolve().parents[1] / 'scripts/evaluate_knowledge_evolution.py'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.json'
            path.write_text('{', encoding='utf-8')
            self.assertEqual(subprocess.run([sys.executable, str(script), str(path)], capture_output=True).returncode, 2)
            obj = json.loads(DEFAULT.read_text(encoding='utf-8-sig'))
            obj['checkpoints'][1]['claims'][0]['expected'] = 'supported'
            path.write_text(json.dumps(obj), encoding='utf-8')
            self.assertEqual(subprocess.run([sys.executable, str(script), str(path)], capture_output=True).returncode, 1)

    def test_cli_export_failure_is_json_unavailable(self):
        import json, subprocess, tempfile
        from evaluate_knowledge_evolution import DEFAULT
        script = Path(__file__).resolve().parents[1] / 'scripts/evaluate_knowledge_evolution.py'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.json'
            obj = json.loads(DEFAULT.read_text(encoding='utf-8-sig'))
            obj['checkpoints'][0]['owner'] = '---\nunterminated: true\n'
            path.write_text(json.dumps(obj), encoding='utf-8')
            result = subprocess.run([sys.executable, str(script), str(path)], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('error', json.loads(result.stdout))
            self.assertEqual(result.stderr, b'')
