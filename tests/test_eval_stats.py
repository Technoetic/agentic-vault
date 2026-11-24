"""Unit tests for paired bootstrap CIs and deterministic 2-fold cross-fitting in scripts/eval_stats.py."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
STATS_PATH = REPO_ROOT / "scripts" / "eval_stats.py"


def _load_stats():
    spec = importlib.util.spec_from_file_location("agentic_vault_eval_stats_test", STATS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PairedBootstrapCITests(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = _load_stats()

    def test_flat_and_stratified_bootstrap_are_deterministic_and_bounded(self) -> None:
        baseline = [0.2, 0.3, 0.4, 0.5, 0.1, 0.2, 0.3, 0.4]
        improved = [0.6, 0.7, 0.8, 0.9, 0.3, 0.4, 0.5, 0.6]
        strata = ["en:s1", "en:s1", "en:s1", "en:s1", "ko:s2", "ko:s2", "ko:s2", "ko:s2"]

        first = self.stats.paired_bootstrap_ci(baseline, improved, strata=strata, resamples=500, seed=1707)
        second = self.stats.paired_bootstrap_ci(baseline, improved, strata=strata, resamples=500, seed=1707)

        self.assertEqual(first, second)
        self.assertEqual(first["paired_query_count"], 8)
        self.assertAlmostEqual(first["mean_delta"], 0.3, places=12)
        self.assertLessEqual(first["ci_low"], first["mean_delta"])
        self.assertGreaterEqual(first["ci_high"], first["mean_delta"])
        self.assertGreater(first["ci_low"], 0.0)

        strat = first["stratified_macro"]
        self.assertEqual(strat["stratum_count"], 2)
        self.assertEqual(strat["strata"], {"en:s1": 4, "ko:s2": 4})
        self.assertAlmostEqual(strat["observed_macro_delta"], 0.3, places=12)
        self.assertLessEqual(strat["ci_low"], strat["observed_macro_delta"])
        self.assertGreaterEqual(strat["ci_high"], strat["observed_macro_delta"])

    def test_stratified_macro_weights_imbalanced_strata_equally(self) -> None:
        # Large stratum delta=+0.1 (6 rows), small stratum delta=+0.9 (2 rows)
        baseline = [0.0] * 8
        improved = [0.1] * 6 + [0.9] * 2
        strata = ["big"] * 6 + ["small"] * 2

        result = self.stats.paired_bootstrap_ci(baseline, improved, strata=strata, resamples=300, seed=42)
        self.assertAlmostEqual(result["mean_delta"], 0.3, places=12)
        self.assertAlmostEqual(result["stratified_macro"]["observed_macro_delta"], 0.5, places=12)
        self.assertAlmostEqual(result["stratified_macro"]["ci_low"], 0.5, places=12)
        self.assertAlmostEqual(result["stratified_macro"]["ci_high"], 0.5, places=12)

    def test_empty_pairs_returns_none_estimates(self) -> None:
        result = self.stats.paired_bootstrap_ci([], [], strata=[])
        self.assertEqual(result["paired_query_count"], 0)
        self.assertIsNone(result["mean_delta"])
        self.assertIsNone(result["ci_low"])
        self.assertIsNone(result["ci_high"])
        self.assertIsNone(result["stratified_macro"])

    def test_validation_rejects_bad_inputs(self) -> None:
        with self.assertRaises(ValueError):
            self.stats.paired_bootstrap_ci([0.1], [0.2, 0.3])
        with self.assertRaises(ValueError):
            self.stats.paired_bootstrap_ci([0.1], [0.2], resamples=50)
        with self.assertRaises(ValueError):
            self.stats.paired_bootstrap_ci([0.1], [0.2], confidence=0.5)
        with self.assertRaises(ValueError):
            self.stats.paired_bootstrap_ci([0.1], [0.2], seed=True)
        with self.assertRaises(ValueError):
            self.stats.paired_bootstrap_ci([0.1, 0.2], [0.3, 0.4], strata=["ok", ""])
        with self.assertRaises(ValueError):
            self.stats.paired_bootstrap_ci([math.nan], [0.1])


class CrossFitTwoFoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = _load_stats()

    def test_out_of_fold_isolation_and_determinism(self) -> None:
        items = [{"id": f"q-{idx}", "x": float(idx)} for idx in range(9)]
        seen_train_sets: list[set[str]] = []

        def fit_fn(train_rows):
            ids = {row["id"] for row in train_rows}
            seen_train_sets.append(ids)
            mean_x = sum(row["x"] for row in train_rows) / len(train_rows)
            return {"train_ids": ids, "mean_x": mean_x}

        def score_fn(model, row):
            self.assertNotIn(row["id"], model["train_ids"])
            return abs(row["x"] - model["mean_x"])

        first = self.stats.cross_fit_two_fold(items, fit_fn, score_fn, seed=1707)
        second = self.stats.cross_fit_two_fold(items, fit_fn, score_fn, seed=1707)

        self.assertEqual(first, second)
        self.assertEqual(first["item_count"], 9)
        self.assertEqual(first["fold_sizes"], [5, 4])
        self.assertEqual([row["id"] for row in first["per_item"]], [row["id"] for row in items])
        self.assertEqual(len(seen_train_sets[0] & seen_train_sets[1]), 0)
        self.assertEqual(seen_train_sets[0] | seen_train_sets[1], {row["id"] for row in items})

    def test_validation_rejects_invalid_items_and_scores(self) -> None:
        with self.assertRaises(ValueError):
            self.stats.cross_fit_two_fold([{"id": "only-one"}], lambda rows: None, lambda m, r: 1.0)
        with self.assertRaises(ValueError):
            self.stats.cross_fit_two_fold(
                [{"id": "dup"}, {"id": "dup"}],
                lambda rows: None,
                lambda m, r: 1.0,
            )
        with self.assertRaises(ValueError):
            self.stats.cross_fit_two_fold(
                [{"id": "a"}, {"id": "b"}],
                lambda rows: None,
                lambda m, r: float("nan"),
            )
        with self.assertRaises(ValueError):
            self.stats.cross_fit_two_fold(
                [{"id": "a"}, {"id": "b"}],
                lambda rows: None,
                lambda m, r: True,
            )


if __name__ == "__main__":
    unittest.main()
