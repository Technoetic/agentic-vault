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


class HolmBonferroniTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = _load_stats()

    def test_step_down_monotonicity_and_order_preservation(self) -> None:
        # Classic Holm (1979) example: sorted p = [0.005, 0.015, 0.02, 0.04]
        # multipliers = [4, 3, 2, 1] -> raw scaled = [0.02, 0.045, 0.04, 0.04]
        # step-down cummax -> [0.02, 0.045, 0.045, 0.045]
        result = self.stats.holm_bonferroni_adjust([0.04, 0.005, 0.02, 0.015], alpha=0.05)
        self.assertEqual(result["hypothesis_count"], 4)
        self.assertEqual(result["rejected_count"], 4)
        self.assertEqual(result["unadjusted_rejected_count"], 4)
        adj = [item["adjusted_p"] for item in result["items"]]
        self.assertAlmostEqual(adj[1], 0.02, places=12)
        self.assertAlmostEqual(adj[3], 0.045, places=12)
        self.assertAlmostEqual(adj[2], 0.045, places=12)
        self.assertAlmostEqual(adj[0], 0.045, places=12)

    def test_dict_input_controls_fwer_against_unadjusted_rejections(self) -> None:
        result = self.stats.holm_bonferroni_adjust(
            {"m1": 0.01, "m2": 0.03, "m3": 0.04, "m4": 0.50},
            alpha=0.05,
        )
        self.assertEqual(result["unadjusted_rejected_count"], 3)
        self.assertEqual(result["rejected_count"], 1)
        by_label = {item["label"]: item for item in result["items"]}
        self.assertTrue(by_label["m1"]["rejected"])
        self.assertAlmostEqual(by_label["m1"]["adjusted_p"], 0.04, places=12)
        self.assertFalse(by_label["m2"]["rejected"])
        self.assertAlmostEqual(by_label["m2"]["adjusted_p"], 0.09, places=12)
        self.assertFalse(by_label["m3"]["rejected"])
        self.assertAlmostEqual(by_label["m3"]["adjusted_p"], 0.09, places=12)
        self.assertAlmostEqual(by_label["m4"]["adjusted_p"], 0.50, places=12)

    def test_validation_rejects_bad_inputs(self) -> None:
        for bad in ([], {}, [-0.01], [1.01], [math.nan], [math.inf], [True], {"": 0.05}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.stats.holm_bonferroni_adjust(bad)
        for bad_alpha in (0.0, 1.0, -0.05, math.nan, True, "0.05"):
            with self.subTest(alpha=bad_alpha), self.assertRaises(ValueError):
                self.stats.holm_bonferroni_adjust([0.01], alpha=bad_alpha)

    def test_clamping_at_one_ties_and_schema(self) -> None:
        clamped = self.stats.holm_bonferroni_adjust([0.6, 0.7], alpha=0.05)
        self.assertEqual(clamped["schema"], "agentic-vault/holm-bonferroni/v1")
        self.assertEqual([item["adjusted_p"] for item in clamped["items"]], [1.0, 1.0])
        self.assertEqual(clamped["rejected_count"], 0)

        tied = self.stats.holm_bonferroni_adjust([0.02, 0.02, 0.5], alpha=0.05)
        # step 0: 3*0.02=0.06; step 1: max(0.06, 2*0.02)=0.06; step 2: max(0.06, 1*0.5)=0.5
        self.assertAlmostEqual(tied["items"][0]["adjusted_p"], 0.06, places=12)
        self.assertAlmostEqual(tied["items"][1]["adjusted_p"], 0.06, places=12)
        self.assertAlmostEqual(tied["items"][2]["adjusted_p"], 0.5, places=12)
        self.assertEqual(tied["rejected_count"], 0)


class PpiPlusPlusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = _load_stats()

    def test_strong_proxy_narrows_ci_and_matches_closed_form(self) -> None:
        # High correlation proxy f(x) = y + constant bias (+0.2) + tiny noise
        gold_y = [float(i % 5) for i in range(40)]
        gold_f = [y + 0.2 + (0.05 if i % 2 == 0 else -0.05) for i, y in enumerate(gold_y)]
        unlab_f = [float(i % 5) + 0.2 for i in range(400)]

        res = self.stats.ppi_pp_mean_ci(gold_y, gold_f, unlab_f, confidence=0.95)
        self.assertEqual(res["schema"], "agentic-vault/ppi-pp-mean/v1")
        self.assertFalse(res["small_sample_warning"])
        self.assertAlmostEqual(res["point_estimate"], 2.0, places=2)
        self.assertGreater(res["lambda_hat"], 0.85)
        self.assertLess(res["ci_width_ratio"], 0.45)
        self.assertLess(res["ci_low"], res["point_estimate"])
        self.assertGreater(res["ci_high"], res["point_estimate"])
        self.assertEqual(res["caveat"], "asymptotic_normal_ci_descriptive_only")

    def test_constant_proxy_falls_back_to_classical_interval(self) -> None:
        gold_y = [0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
        gold_f = [0.5] * 6
        unlab_f = [0.5] * 50

        res = self.stats.ppi_pp_mean_ci(gold_y, gold_f, unlab_f)
        self.assertTrue(res["small_sample_warning"])
        self.assertEqual(res["lambda_hat"], 0.0)
        self.assertAlmostEqual(res["point_estimate"], res["classical_mean"], places=12)
        self.assertAlmostEqual(res["ci_low"], res["classical_ci_low"], places=12)
        self.assertAlmostEqual(res["ci_high"], res["classical_ci_high"], places=12)
        self.assertAlmostEqual(res["ci_width_ratio"], 1.0, places=12)

    def test_uncorrelated_varying_proxy_sets_lambda_zero_and_extreme_overflow_raises_value_error(self) -> None:
        # Varying proxy with exact zero sample covariance with gold_y
        gold_y = [0.0, 0.0, 1.0, 1.0] * 10
        gold_f = [0.0, 1.0, 0.0, 1.0] * 10
        unlab_f = [0.0, 1.0] * 100

        res = self.stats.ppi_pp_mean_ci(gold_y, gold_f, unlab_f)
        self.assertAlmostEqual(res["lambda_hat"], 0.0, places=12)
        self.assertAlmostEqual(res["point_estimate"], res["classical_mean"], places=12)
        self.assertAlmostEqual(res["ci_width_ratio"], 1.0, places=12)

        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([1e200, -1e200], [1.0, 2.0], [1.0, 2.0])

    def test_fixed_standard_ppi_lambda_and_validation(self) -> None:
        gold_y = [1.0, 2.0, 3.0, 4.0]
        gold_f = [1.5, 2.5, 3.5, 4.5]
        unlab_f = [1.5, 2.5, 3.5, 4.5, 2.5, 3.5]

        res = self.stats.ppi_pp_mean_ci(gold_y, gold_f, unlab_f, power_tuning=False)
        self.assertEqual(res["lambda_hat"], 1.0)
        # Rectifier y - f is constant -0.5 -> point estimate = mean(unlab_f) - 0.5 = 2.5
        self.assertAlmostEqual(res["point_estimate"], 2.5, places=12)

        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([1.0], [1.0], [1.0, 2.0])
        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([1.0, 2.0], [1.0], [1.0, 2.0])
        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([1.0, 2.0], [1.0, 2.0], [1.0])
        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([True, False], [1.0, 0.0], [1.0, 0.0])
        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([1.0, 2.0], [1.0, 2.0], [1.0, math.nan])
        with self.assertRaises(ValueError):
            self.stats.ppi_pp_mean_ci([1.0, 2.0], [1.0, 2.0], [1.0, 2.0], power_tuning=True, lam=0.5)


if __name__ == "__main__":
    unittest.main()
