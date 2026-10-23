"""Opt-in lower bounds for the share of answerable queries whose every expected
source was retrieved (--success-bounds).

The recall evaluator reports 100% headlines (for example 56/56) without a
lower bound, and --rate-bounds keeps its existing three failure rates. The new
flag adds one-sided lower bounds computed from the same headline rows as each
summary. Without the flag every report, the CLI output and the exit code stay
as before.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import itertools
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ADVANCED_FIXTURE = ROOT / "tests" / "fixtures" / "recall_advanced"
HARD_FIXTURE = ROOT / "tests" / "fixtures" / "recall_hard"
SUCCESS_KEYS = {"success_lower_bounds", "success_lower_bound_definitions"}
GATES = ["--max-no-answer-false-positive-rate", "0", "--max-forbidden-exposure-rate", "0",
         "--max-stale-top-1-rate", "0"]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stats = _load("success_bounds_eval_stats", ROOT / "scripts" / "eval_stats.py")
evaluator = _load("success_bounds_recall_evaluator", ROOT / "scripts" / "evaluate_recall.py")


def without_success(value):
    if isinstance(value, dict):
        return {key: without_success(item) for key, item in value.items() if key not in SUCCESS_KEYS}
    if isinstance(value, list):
        return [without_success(item) for item in value]
    return value


def headline(report: dict) -> list:
    return [row for row in report["per_query"] if row.get("inspected") is not True]


def run_main(argv):
    """main() with a frozen clock so two runs print byte-comparable JSON."""
    counter = itertools.count()
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(evaluator.time, "perf_counter", new=lambda: next(counter) * 0.001), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = evaluator.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class SuccessBoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.single = evaluator.evaluate(ADVANCED_FIXTURE, advanced=True)
        cls.comparison = evaluator.compare(ADVANCED_FIXTURE)

    def check_summary(self, summary: dict, rows: list, confidence: float = 0.95):
        answerable = [row for row in rows if row["expectation"] == "answerable"]
        bounds = summary["success_lower_bounds"]
        if not answerable:
            self.assertEqual(bounds, {})
            return
        found = sum(row["source_recall_at_k"] == 1.0 for row in answerable)
        record = bounds["all_expected_found_rate"]
        self.assertEqual((record["numerator"], record["denominator"]), (found, len(answerable)))
        self.assertEqual(record["denominator"], summary["answerable_query_count"])
        self.assertEqual(record["observed_rate"], found / len(answerable))
        miss = stats.rate_upper_bounds(len(answerable) - found, len(answerable), confidence=confidence)
        self.assertEqual(record["wilson_lower"], 1 - miss["wilson_upper"] if found else 0.0)
        self.assertEqual(record["clopper_pearson_lower"], 1 - miss["clopper_pearson_upper"] if found else 0.0)
        self.assertEqual(record["confidence"], confidence)

    def check_arm(self, report: dict, confidence: float = 0.95):
        rows = headline(report)
        self.check_summary(report, rows, confidence)
        for language, summary in report["by_language"].items():
            with self.subTest(language=language):
                self.check_summary(summary, [row for row in rows if row["language"] == language], confidence)
        for scenario, summary in report["by_scenario"].items():
            with self.subTest(scenario=scenario):
                self.check_summary(summary, [row for row in rows if row["scenario"] == scenario], confidence)

    def test_every_summary_gains_lower_bounds_from_its_own_rows(self):
        original = copy.deepcopy(self.single)
        extended = evaluator.with_success_bounds(self.single)
        self.assertEqual(self.single, original, "input report is not mutated")
        self.assertEqual(without_success(extended), self.single)
        self.assertEqual(evaluator.with_success_bounds(extended), extended, "a second pass replaces, never duplicates")
        self.check_arm(extended)
        keys = list(extended)
        self.assertEqual(keys[keys.index("stale_sources") + 1], "success_lower_bounds")
        self.assertEqual(keys[keys.index("interpretation") + 1], "success_lower_bound_definitions")
        definitions = extended["success_lower_bound_definitions"]
        self.assertEqual((definitions["confidence"], definitions["side"]), (0.95, "one-sided lower"))
        self.assertIn("not a lower bound of recall_at_k", definitions["definition"])
        self.assertIn("never gates", definitions["interpretation"])
        relabeled = evaluator.with_success_bounds(extended, confidence=0.9)
        self.check_arm(relabeled, confidence=0.9)

    def test_all_found_uses_the_closed_form(self):
        record = evaluator.with_success_bounds(self.single)["success_lower_bounds"]["all_expected_found_rate"]
        if record["numerator"] != record["denominator"]:
            self.skipTest("fixture has a missed expected source")
        n = record["denominator"]
        self.assertAlmostEqual(record["clopper_pearson_lower"], 0.05 ** (1 / n), places=12)
        self.assertLess(record["clopper_pearson_lower"], 1.0)

    def test_rate_bounds_and_success_bounds_compose(self):
        rated = evaluator.with_rate_bounds(self.single)
        both = evaluator.with_success_bounds(rated)
        self.assertEqual(without_success(both), rated)
        keys = list(both)
        self.assertEqual(keys[keys.index("rate_upper_bounds") + 1], "success_lower_bounds")
        self.assertEqual(keys[keys.index("rate_upper_bound_definitions") + 1], "success_lower_bound_definitions")
        self.check_arm(both)

    def test_compare_arms_gain_bounds_and_other_sections_stay(self):
        extended = evaluator.with_success_bounds(self.comparison)
        self.assertEqual(without_success(extended), self.comparison)
        for arm in ("baseline", "improved"):
            with self.subTest(arm=arm):
                self.check_arm(extended[arm])
        self.assertEqual(list(extended).count("success_lower_bound_definitions"), 1)
        self.assertEqual(extended["paired_randomization"], self.comparison["paired_randomization"])

    def test_inspected_queries_stay_out_of_the_bounds(self):
        report = copy.deepcopy(self.single)
        target = next(row for row in report["per_query"] if row["expectation"] == "answerable")
        target["inspected"] = True
        rows = headline(report)
        report.update(evaluator._summary(rows))
        report["by_language"] = {key: evaluator._summary([row for row in rows if row["language"] == key])
                                 for key in sorted({row["language"] for row in rows})}
        report["by_scenario"] = {key: evaluator._summary([row for row in rows if row["scenario"] == key])
                                 for key in sorted({row["scenario"] for row in rows})}
        extended = evaluator.with_success_bounds(report)
        self.check_arm(extended)
        record = extended["success_lower_bounds"]["all_expected_found_rate"]
        self.assertEqual(record["denominator"], self.single["answerable_query_count"] - 1)

    def test_none_found_gives_a_zero_lower_bound(self):
        report = copy.deepcopy(self.single)
        for row in report["per_query"]:
            if row["expectation"] == "answerable":
                row["source_recall_at_k"] = 0.0
        record = evaluator.with_success_bounds(report)["success_lower_bounds"]["all_expected_found_rate"]
        self.assertEqual((record["numerator"], record["wilson_lower"], record["clopper_pearson_lower"],
                          record["miss_rate_method"]), (0, 0.0, 0.0, "all_events"))

    def test_redacted_run_with_every_compare_arm_adds_only_counts(self):
        argv = ["--fixture", str(HARD_FIXTURE), "--compare", "--render-matched", "--expand-query", "--query-mapping",
                "00-meta/query-map.txt", "--top-k", "3", "--max-tokens", "1500", "--redact"]
        plain_code, plain_out, _ = run_main(argv)
        flag_code, flag_out, _ = run_main([*argv, "--success-bounds"])
        self.assertEqual(flag_code, plain_code)
        self.assertEqual(json.dumps(without_success(json.loads(flag_out)), ensure_ascii=False, indent=2) + "\n",
                         plain_out)
        flagged = json.loads(flag_out)
        for arm in ("baseline", "expansion_ablation", "improved", "render_matched"):
            with self.subTest(arm=arm):
                self.check_arm(flagged[arm])
        record = flagged["improved"]["success_lower_bounds"]["all_expected_found_rate"]
        self.assertEqual(set(record), {"numerator", "denominator", "observed_rate", "confidence", "wilson_lower",
                                       "clopper_pearson_lower", "miss_rate_method"})
        self.assertIn(record["miss_rate_method"],
                      {"zero_event_closed_form", "all_events", "binomial_cdf_bisection"})

    def test_population_mismatch_and_invalid_input_fail_closed(self):
        tampered = copy.deepcopy(self.single)
        tampered["answerable_query_count"] += 1
        with self.assertRaises(ValueError):
            evaluator.with_success_bounds(tampered)
        for invalid in ([], {}, {"baseline": {}}):
            with self.subTest(report=invalid), self.assertRaises(ValueError):
                evaluator.with_success_bounds(invalid)
        for confidence in (0.4, 1, True):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                evaluator.with_success_bounds(self.single, confidence=confidence)

    def test_cli_flag_is_additive_and_never_changes_gates(self):
        argv = ["--fixture", str(ADVANCED_FIXTURE), "--compare", *GATES]
        plain_code, plain_out, plain_err = run_main(argv)
        flag_code, flag_out, flag_err = run_main([*argv, "--success-bounds"])
        self.assertEqual((plain_code, flag_code), (0, 0))
        self.assertEqual(json.dumps(without_success(json.loads(flag_out)), ensure_ascii=False, indent=2) + "\n",
                         plain_out)
        self.assertEqual((plain_err, flag_err), ("", ""))
        flagged = json.loads(flag_out)
        self.assertIn("success_lower_bounds", flagged["improved"])
        self.assertEqual(flagged["improved"]["optional_gates"], json.loads(plain_out)["improved"]["optional_gates"])

    def test_split_run_bounds_follow_the_split(self):
        argv = ["--fixture", str(HARD_FIXTURE), "--compare", "--expand-query", "--query-mapping",
                "00-meta/query-map.txt", "--top-k", "3", "--max-tokens", "1500", "--split", "heldout"]
        plain_code, plain_out, _ = run_main(argv)
        flag_code, flag_out, _ = run_main([*argv, "--success-bounds"])
        self.assertEqual(flag_code, plain_code)
        self.assertEqual(json.dumps(without_success(json.loads(flag_out)), ensure_ascii=False, indent=2) + "\n",
                         plain_out)
        improved = json.loads(flag_out)["improved"]
        record = improved["success_lower_bounds"]["all_expected_found_rate"]
        self.assertEqual(record["denominator"], improved["answerable_query_count"])
        self.check_arm(improved)


if __name__ == "__main__":
    unittest.main()
