from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hard_evaluator', ROOT / 'scripts/evaluate_recall.py')
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


class HardEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Path(self.tmp.name)
        vault = self.fixture / 'vault'
        (vault / '00-meta').mkdir(parents=True)
        (vault / '00-meta/vault-config.json').write_text('{}', encoding='utf-8')
        (vault / 'note.md').write_text('# Checklist\nChecklist requires locking before inspection.\n', encoding='utf-8')
        self.queries = [{'id': str(i), 'language': 'ko' if i % 2 else 'en', 'scenario': 'short', 'query': 'Checklist', 'expected_paths': ['note.md'], 'required_claims': ['locking before inspection'], 'relevance': {'note.md': 3}} for i in range(12)]
        self.save()

    def save(self):
        (self.fixture / 'queries.json').write_text(json.dumps({'queries': self.queries}), encoding='utf-8')

    def test_context_claim_coverage_requires_actual_returned_text_and_denominators(self):
        report = evaluator.evaluate(self.fixture, top_k=5)
        self.assertEqual(report['top_k'], 5)
        self.assertEqual(report['ndcg_at_5'], 1)
        self.assertEqual(report['required_claim_query_count'], 12)
        self.assertEqual(report['returned_context_claim_coverage'], 0)
        zero = evaluator.evaluate(self.fixture, max_tokens=0, top_k=3)
        self.assertEqual(zero['recall_at_3'], 1)
        self.assertEqual(zero['returned_context_claim_coverage'], 0)
        self.assertEqual(zero['by_language']['ko']['required_claim_count'], 6)

    def test_labels_are_never_passed_to_retriever(self):
        module = evaluator._load_recall_module()
        original = module.recall
        calls = []
        def checked(vault, query, **options):
            calls.append(options)
            return original(vault, query, **options)
        with patch.object(evaluator, '_load_recall_module', return_value=module), patch.object(module, 'recall', side_effect=checked):
            evaluator.evaluate(self.fixture, top_k=5)
        self.assertEqual(len(calls), 12)
        self.assertTrue(all(set(c) == {'limit', 'max_tokens'} for c in calls))

    def test_graded_ndcg_and_randomization_are_real_deterministic_and_two_sided(self):
        self.assertTrue(hasattr(evaluator, 'paired_randomization'), 'stdlib paired randomization must exist')
        stats = evaluator.paired_randomization([0]*12, [1]*12, seed=1707, permutations=2048)
        reverse = evaluator.paired_randomization([1]*12, [0]*12, seed=1707, permutations=2048)
        self.assertEqual(stats, evaluator.paired_randomization([0]*12, [1]*12, seed=1707, permutations=2048))
        self.assertEqual(stats['p_value'], reverse['p_value'])
        self.assertLess(stats['p_value'], .01)
        self.assertEqual(evaluator.paired_randomization([1]*4, [1]*4)['p_value'], 1)
        self.assertAlmostEqual(evaluator._ndcg(['weak.md', 'best.md'], {'best.md':3, 'weak.md':1}), (1 + 7 / evaluator.math.log2(3)) / (7 + 1 / evaluator.math.log2(3)))

    def test_invalid_top_k_claims_relevance_and_permutations_are_rejected(self):
        for value in (0, 4, True):
            with self.assertRaises(ValueError):
                evaluator.evaluate(self.fixture, top_k=value)
        for changes in ({'required_claims': ['']}, {'required_claims': 'claim'}, {'relevance': {'../unsafe.md': 3}}, {'relevance': {'note.md': True}}, {'relevance': {'note.md': 4}}):
            original = dict(self.queries[0])
            self.queries[0].update(changes)
            self.save()
            with self.assertRaises(ValueError):
                evaluator._load_queries(self.fixture)
            self.queries[0] = original
        with self.assertRaises(ValueError):
            evaluator.paired_randomization([0], [1], permutations=0)

    def test_hard_fixture_has_32_each_language_six_scenarios_and_expansion_ablation(self):
        fixture = ROOT / 'tests/fixtures/recall_hard'
        self.assertTrue((fixture / 'queries.json').is_file(), 'independent hard labels must be checked in')
        queries = evaluator._load_queries(fixture)
        for language in ('ko', 'en'):
            rows = [q for q in queries if q['language'] == language]
            self.assertGreaterEqual(len(rows), 32)
            self.assertEqual({q['scenario'] for q in rows}, {'particles', 'short', 'mixed', 'spacing', 'update', 'abstention'})
        comparison = evaluator.compare(fixture, max_tokens=300, top_k=5, expand_query=True, query_mapping='00-meta/query-map.txt')
        self.assertEqual(comparison['baseline']['costs']['max_context_tokens'], 300)
        self.assertEqual(comparison['expansion_ablation']['costs']['max_context_tokens'], 300)
        self.assertEqual(comparison['improved']['costs']['max_context_tokens'], 300)
        self.assertEqual(comparison['paired_randomization']['returned_context_claim_coverage']['seed'], 1707)
        self.assertGreater(comparison['improved']['returned_context_claim_coverage'], comparison['expansion_ablation']['returned_context_claim_coverage'])


if __name__ == '__main__':
    unittest.main()
