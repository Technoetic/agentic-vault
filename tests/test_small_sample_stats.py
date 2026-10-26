"""Exact paired sign-flip p-values and small-sample rate upper bounds (opt-in).

scripts/eval_stats.py holds the statistics. evaluate_recall.py uses them only
behind --exact-paired and --rate-bounds; without the flags every report, the
CLI output and the exit code stay as before and the module is never loaded.
The exact hypergeometric miss bound is used only by scripts/real_vault_audit.py;
the functions that existed in v0.31.0 keep their values (pinned below).
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import inspect
import io
import itertools
import json
import math
import random
import time
import unittest
from fractions import Fraction
from pathlib import Path
from statistics import NormalDist
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ADVANCED_FIXTURE = ROOT / "tests" / "fixtures" / "recall_advanced"
TIMING_FIELDS = {"elapsed_ms", "total_elapsed_ms", "mean_elapsed_ms", "retrieval_elapsed_ms"}
BOUND_KEYS = {"rate_upper_bounds", "rate_upper_bound_definitions"}
LEGACY_PAIRED_KEYS = {"method", "seed", "permutations", "paired_query_count", "mean_delta", "p_value"}
EXACT_PAIRED_KEYS = {"method", "paired_query_count", "nonzero_pair_count", "mean_delta", "p_value",
                     "minimum_attainable_p_value"}
ALPHA = 1 - 0.95
GATES = ["--max-no-answer-false-positive-rate", "0", "--max-forbidden-exposure-rate", "0",
         "--max-stale-top-1-rate", "0"]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stats = _load("small_sample_stats_under_test", ROOT / "scripts" / "eval_stats.py")
evaluator = _load("small_sample_recall_evaluator", ROOT / "scripts" / "evaluate_recall.py")


def exact(deltas):
    return stats.exact_paired_sign_flip([0.0] * len(deltas), list(deltas))


def sum_left_to_right(values):
    """Plain float addition in order. Built-in sum() adds floats with compensation from
    Python 3.12, so tie-breaking float noise would depend on the interpreter."""
    total = 0.0
    for value in values:
        total += value
    return total


def brute_force_p(deltas, tolerance=1e-12):
    """Independent definition: all 2^n sign patterns (zeros included), summed left to right."""
    threshold = abs(sum_left_to_right(deltas)) - tolerance
    patterns = list(itertools.product((1, -1), repeat=len(deltas)))
    return sum(abs(sum_left_to_right(sign * value for sign, value in zip(signs, deltas))) >= threshold
               for signs in patterns) / len(patterns)


def binomial_cdf(events, trials, probability):
    """Direct float evaluation from math.comb, independent of eval_stats."""
    return sum(math.comb(trials, i) * probability ** i * (1 - probability) ** (trials - i)
               for i in range(events + 1))


def exact_binomial_cdf(events, trials, probability: float) -> Fraction:
    p = Fraction(probability)
    return sum(math.comb(trials, i) * p ** i * (1 - p) ** (trials - i) for i in range(events + 1))


def coverage(trials, rate, bounds):
    """Exact probability that bounds[X] >= rate for X ~ Binomial(trials, rate)."""
    return sum(math.comb(trials, x) * rate ** x * (1 - rate) ** (trials - x)
               for x in range(trials + 1) if bounds[x] >= rate)


def without_timing(value):
    if isinstance(value, dict):
        return {key: without_timing(item) for key, item in value.items() if key not in TIMING_FIELDS}
    if isinstance(value, list):
        return [without_timing(item) for item in value]
    return value


def canonical(value) -> str:
    return json.dumps(without_timing(value), ensure_ascii=False, sort_keys=True)


def without_bounds(value):
    if isinstance(value, dict):
        return {key: without_bounds(item) for key, item in value.items() if key not in BOUND_KEYS}
    if isinstance(value, list):
        return [without_bounds(item) for item in value]
    return value


def summaries(report: dict):
    """Yield (label, summary) for an evaluate() report: overall, by_language, by_scenario."""
    yield "overall", report
    for group in ("by_language", "by_scenario"):
        for key, value in report[group].items():
            yield f"{group}.{key}", value


def run_main(argv):
    """main() with a frozen clock so two runs print byte-comparable JSON."""
    counter = itertools.count()
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(evaluator.time, "perf_counter", new=lambda: next(counter) * 0.001), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = evaluator.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def hypergeometric_cdf(population, total, sample, misses) -> Fraction:
    """P(X <= misses) for X ~ Hypergeometric(population, total, sample) as a Fraction, from math.comb."""
    return Fraction(sum(math.comb(total, i) * math.comb(population - total, sample - i) for i in range(misses + 1)),
                    math.comb(population, sample))


def hypergeometric_pmf(population, total, sample, misses) -> Fraction:
    return Fraction(math.comb(total, misses) * math.comb(population - total, sample - misses),
                    math.comb(population, sample))


def stable(value):
    """Floats at 10 significant digits: last-bit libm differences between CI platforms stay out."""
    if isinstance(value, dict):
        return {key: stable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [stable(item) for item in value]
    if isinstance(value, float):
        return format(value, ".10g")
    return value


def existing_outputs(module):
    """Outputs of every function eval_stats.py had in v0.31.0 on a fixed grid."""
    rows = []
    for confidence in (0.9, 0.95, 0.99):
        for trials in (1, 2, 4, 8, 21, 56, 300):
            for events in sorted({0, 1, trials // 2, trials - 1, trials}):
                rows.append(["rate_upper_bounds", events, trials, confidence,
                             module.rate_upper_bounds(events, trials, confidence=confidence)])
        rows.append(["confidence_alpha", confidence, module.confidence_alpha(confidence)])
    for baseline, improved in (([0.0] * 4, [0.1, 0.2, 0.3, -0.3]), ([0.25, 0.5, 0.0], [0.75, 0.5, 0.5]),
                               ([], []), ([0, 0, 0], [1, 1, -0.5])):
        rows.append(["exact_paired_sign_flip", baseline, improved,
                     module.exact_paired_sign_flip(baseline, improved)])
    rows.append(["paired_differences", module.paired_differences([0.25, 0.5], [0.75, 0.25])])
    rows.append(["minimum_attainable_p_value", [module.minimum_attainable_p_value(k) for k in range(6)]])
    rows.append(["constants", module.EXACT_PAIRED_MAX_NONZERO, module.TIE_TOLERANCE, module.EXACT_PAIRED_METHOD])
    return stable(rows)


# existing_outputs() of v0.31.0 (21c4b47) as compact sorted JSON, captured before the
# hypergeometric bound was added (Windows, Python 3.12.10).
V0310_EXISTING_OUTPUTS_SHA256 = "8a32eee6101093ab8c399922f55103f4a9dcc38d630d8e601a7bc9383bf2a891"
# Parameter names, kinds and defaults of the v0.31.0 public functions.
V0310_SIGNATURES = {
    "clopper_pearson_upper_bound": [("events", "POSITIONAL_OR_KEYWORD", None),
                                    ("trials", "POSITIONAL_OR_KEYWORD", None),
                                    ("confidence", "KEYWORD_ONLY", 0.95)],
    "confidence_alpha": [("confidence", "POSITIONAL_OR_KEYWORD", None)],
    "exact_paired_sign_flip": [("baseline", "POSITIONAL_OR_KEYWORD", None),
                               ("improved", "POSITIONAL_OR_KEYWORD", None)],
    "minimum_attainable_p_value": [("nonzero_pairs", "POSITIONAL_OR_KEYWORD", None)],
    "paired_differences": [("baseline", "POSITIONAL_OR_KEYWORD", None), ("improved", "POSITIONAL_OR_KEYWORD", None)],
    "rate_upper_bounds": [("events", "POSITIONAL_OR_KEYWORD", None), ("trials", "POSITIONAL_OR_KEYWORD", None),
                          ("confidence", "KEYWORD_ONLY", 0.95)],
    "wilson_upper_bound": [("events", "POSITIONAL_OR_KEYWORD", None), ("trials", "POSITIONAL_OR_KEYWORD", None),
                           ("confidence", "KEYWORD_ONLY", 0.95)],
}


class ExactPairedSignFlipTests(unittest.TestCase):
    def test_hand_checkable_p_values(self):
        # (deltas, p, non-zero pairs, minimum attainable p)
        cases = [
            ([0, 0, 0], 1.0, 0, 1.0),
            ([1], 1.0, 1, 1.0),
            ([1, 1], 0.5, 2, 0.5),
            ([0.25, 0, 0.25, 0], 0.5, 2, 0.5),
            ([1, -1], 1.0, 2, 0.5),
            ([1, 1, -0.5], 0.5, 3, 0.25),
            ([0.1, 0.2, 0.3], 0.25, 3, 0.25),
            ([1, 1, 1, 1], 0.125, 4, 0.125),
        ]
        for deltas, p_value, nonzero, floor in cases:
            with self.subTest(deltas=deltas):
                result = exact(deltas)
                self.assertEqual(set(result), EXACT_PAIRED_KEYS)
                self.assertEqual(result["p_value"], p_value)
                self.assertEqual(result["nonzero_pair_count"], nonzero)
                self.assertEqual(result["minimum_attainable_p_value"], floor)
                self.assertEqual(result["paired_query_count"], len(deltas))
                self.assertEqual(result["mean_delta"], sum(deltas) / len(deltas))
                self.assertGreaterEqual(result["p_value"], result["minimum_attainable_p_value"])

    def test_no_pairs_reports_no_p_value_like_monte_carlo(self):
        result = stats.exact_paired_sign_flip([], [])
        self.assertEqual((result["p_value"], result["mean_delta"], result["nonzero_pair_count"]), (None, None, 0))
        self.assertEqual(result["minimum_attainable_p_value"], 1.0)
        self.assertIsNone(evaluator.paired_randomization([], [])["p_value"])

    def test_ties_use_the_monte_carlo_tolerance(self):
        # Mathematically 12 of 16 patterns tie or beat |0.3|, six of them as exact ties.
        # Summed strictly left to right, float noise breaks four of those ties on every Python.
        deltas = [0.1, 0.2, 0.3, -0.3]
        self.assertEqual(exact(deltas)["p_value"], 0.75)
        self.assertEqual(brute_force_p(deltas), 0.75)
        self.assertEqual(brute_force_p(deltas, tolerance=0.0), 0.5)
        self.assertEqual(exact([0.3, -0.1, -0.2, 0.5])["p_value"], 0.625)

    def test_matches_brute_force_enumeration(self):
        generator = random.Random(20261019)
        for case in range(300):
            size = generator.randint(1, 11)
            if case % 2:
                deltas = [generator.choice((-1, 1)) * generator.randint(1, 8) / 8 for _ in range(size)]
            else:
                deltas = [generator.uniform(-1, 1) for _ in range(size)]
            deltas += [0.0] * generator.randint(0, 2)
            generator.shuffle(deltas)
            with self.subTest(case=case):
                self.assertEqual(exact(deltas)["p_value"], brute_force_p(deltas))

    def test_agrees_with_monte_carlo(self):
        baseline = [0.25, 0.5, 0.0, 1.0, 0.75, 0.5, 0.25, 0.0, 1.0, 0.5, 0.75, 0.25, 0.5, 0.0, 1.0, 0.5]
        improved = [0.75, 0.5, 0.5, 1.0, 1.0, 0.25, 0.75, 0.5, 0.875, 1.0, 0.75, 0.5, 1.0, 0.25, 1.0, 0.625]
        result = stats.exact_paired_sign_flip(baseline, improved)
        sampled = evaluator.paired_randomization(baseline, improved)
        self.assertEqual(result["nonzero_pair_count"], 12)
        self.assertEqual(result["mean_delta"], sampled["mean_delta"])
        self.assertLessEqual(abs(result["p_value"] - sampled["p_value"]), 0.02)
        # Two non-zero pairs among n: Monte Carlo moves with zero positions; exact stays 0.5.
        for size in (10, 14):
            for positions in ((0, 1), (3, 7), (size - 2, size - 1)):
                improved = [-1.0 if index in positions else 0.0 for index in range(size)]
                with self.subTest(size=size, positions=positions):
                    sampled = evaluator.paired_randomization([0.0] * size, improved)["p_value"]
                    self.assertEqual(stats.exact_paired_sign_flip([0.0] * size, improved)["p_value"], 0.5)
                    self.assertLessEqual(abs(sampled - 0.5), 0.02)

    def test_direction_and_zero_pairs_do_not_change_p(self):
        baseline, improved = [0.2, 0.5, 0.9, 0.4, 0.3], [0.6, 0.5, 0.7, 1.0, 0.8]
        forward = stats.exact_paired_sign_flip(baseline, improved)
        backward = stats.exact_paired_sign_flip(improved, baseline)
        self.assertEqual(forward["p_value"], backward["p_value"])
        self.assertAlmostEqual(forward["mean_delta"], -backward["mean_delta"], places=15)
        padded = stats.exact_paired_sign_flip([*baseline, 0.5, 0.5], [*improved, 0.5, 0.5])
        self.assertEqual(padded["p_value"], forward["p_value"])
        self.assertEqual(padded["nonzero_pair_count"], forward["nonzero_pair_count"])
        self.assertEqual(padded["paired_query_count"], 7)

    def test_enumeration_limit_is_twenty_non_zero_pairs(self):
        self.assertEqual(stats.EXACT_PAIRED_MAX_NONZERO, 20)
        result = stats.exact_paired_sign_flip([0] * 23, [1] * 20 + [0] * 3)
        self.assertEqual(result["nonzero_pair_count"], 20)
        self.assertEqual(result["p_value"], 2.0 ** -19)
        self.assertEqual(result["minimum_attainable_p_value"], 2.0 ** -19)
        with self.assertRaisesRegex(ValueError, "at most 20 non-zero pairs"):
            stats.exact_paired_sign_flip([0] * 21, [1] * 21)
        self.assertEqual([stats.minimum_attainable_p_value(k) for k in range(4)], [1.0, 1.0, 0.5, 0.25])
        for value in (-1, True, 1.0, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                stats.minimum_attainable_p_value(value)

    def test_input_validation_matches_monte_carlo(self):
        invalid = [([0], [1, 2]), (0, [1]), ([0], "1"), ([True], [1]), ([0.0], [float("nan")]),
                   ([float("inf")], [0.0]), (["1"], [1]), ([None], [1])]
        for baseline, improved in invalid:
            with self.subTest(baseline=baseline, improved=improved):
                with self.assertRaises(ValueError) as sampled:
                    evaluator.paired_randomization(baseline, improved)
                with self.assertRaises(ValueError) as enumerated:
                    stats.exact_paired_sign_flip(baseline, improved)
                self.assertEqual(str(enumerated.exception), str(sampled.exception))
        self.assertEqual(stats.exact_paired_sign_flip((0, 1), (1, 1))["p_value"], 1.0)


class RateUpperBoundTests(unittest.TestCase):
    Z = NormalDist().inv_cdf(0.95)

    def test_zero_events_use_the_exact_closed_form(self):
        bounds = stats.rate_upper_bounds(0, 21)
        self.assertAlmostEqual(bounds["clopper_pearson_upper"], 1 - 0.05 ** (1 / 21), places=12)
        self.assertEqual(round(bounds["clopper_pearson_upper"], 4), 0.1329)
        self.assertEqual(bounds["clopper_pearson_method"], "zero_event_closed_form")
        self.assertAlmostEqual(bounds["wilson_upper"], self.Z ** 2 / (21 + self.Z ** 2), places=12)
        self.assertEqual(round(bounds["wilson_upper"], 4), 0.1141)
        self.assertEqual((bounds["numerator"], bounds["denominator"], bounds["observed_rate"]), (0, 21, 0.0))
        self.assertEqual(bounds["confidence"], 0.95)
        for trials in (1, 2, 4, 10, 21, 100, 1000):
            with self.subTest(trials=trials):
                closed = stats.clopper_pearson_upper_bound(0, trials)
                self.assertAlmostEqual(closed, 1 - ALPHA ** (1 / trials), places=12)
                self.assertAlmostEqual(stats._clopper_pearson_bisection(0, trials, ALPHA), closed, places=12)

    def test_clopper_pearson_solves_the_binomial_tail(self):
        for events, trials in ((1, 10), (2, 8), (5, 20), (3, 50), (7, 30), (20, 21)):
            with self.subTest(events=events, trials=trials):
                bound = stats.clopper_pearson_upper_bound(events, trials)
                self.assertEqual(stats.rate_upper_bounds(events, trials)["clopper_pearson_method"],
                                 "binomial_cdf_bisection")
                self.assertLess(abs(binomial_cdf(events, trials, bound) - ALPHA), 1e-9)
                self.assertLess(abs(float(exact_binomial_cdf(events, trials, bound)) - ALPHA), 1e-12)
                self.assertGreater(binomial_cdf(events, trials, bound * (1 - 1e-6)), ALPHA)
                self.assertGreater(bound, events / trials)

    def test_large_trials_use_float_log_coefficients(self):
        # math.lgamma replaces math.comb, whose integers reach thousands of digits at n = 20,000.
        trials = 20_000
        coefficients = stats._log_binomial_coefficients(trials, trials)
        self.assertEqual((coefficients[0], coefficients[-1]), (0.0, 0.0))
        for i in (1, 2, 10, 999, 1000, 6667, 10_000, 19_999):
            with self.subTest(i=i):
                exact = math.log(math.comb(trials, i))
                self.assertLess(abs(coefficients[i] - exact), 1e-9)
                self.assertLess(abs(coefficients[i] - coefficients[trials - i]), 1e-9)

        def tail(events, probability):
            """P(X <= events) in log space with exact math.comb coefficients."""
            log_p, log_q = math.log(probability), math.log1p(-probability)
            return math.fsum(math.exp(math.log(math.comb(trials, i)) + i * log_p + (trials - i) * log_q)
                             for i in range(events + 1))

        for events in (1, 100, 1000):
            with self.subTest(events=events):
                bound = stats.clopper_pearson_upper_bound(events, trials)
                self.assertLess(abs(tail(events, bound) - ALPHA), 1e-9)
                self.assertGreater(tail(events, bound * (1 - 1e-6)), ALPHA)
                self.assertGreater(bound, events / trials)

    def test_wilson_is_the_upper_root_of_the_score_quadratic(self):
        z2 = self.Z ** 2
        for events, trials in ((0, 5), (1, 10), (2, 8), (5, 20), (19, 21), (3, 50)):
            rate = events / trials
            linear, square = 2 * trials * rate + z2, trials + z2
            root = (linear + math.sqrt(linear * linear - 4 * square * trials * rate * rate)) / (2 * square)
            with self.subTest(events=events, trials=trials):
                self.assertAlmostEqual(stats.wilson_upper_bound(events, trials), root, places=12)

    def test_all_events_and_monotonicity(self):
        for trials in (1, 8, 21):
            bounds = stats.rate_upper_bounds(trials, trials)
            self.assertEqual((bounds["wilson_upper"], bounds["clopper_pearson_upper"]), (1.0, 1.0))
            self.assertEqual(bounds["clopper_pearson_method"], "all_events")
        for function in (stats.wilson_upper_bound, stats.clopper_pearson_upper_bound):
            with self.subTest(function=function.__name__):
                rising = [function(events, 12) for events in range(13)]
                self.assertEqual(rising, sorted(set(rising)))
                falling = [function(0, trials) for trials in (2, 4, 10, 21, 100)]
                self.assertEqual(falling, sorted(set(falling), reverse=True))

    def test_coverage_values_documented_for_twenty_one_trials(self):
        wilson = [stats.wilson_upper_bound(events, 21) for events in range(22)]
        clopper_pearson = [stats.clopper_pearson_upper_bound(events, 21) for events in range(22)]
        self.assertEqual(round(coverage(21, 0.12, wilson), 3), 0.932)
        self.assertEqual(round(coverage(21, 0.2, wilson), 3), 0.942)
        self.assertEqual(round(coverage(21, 0.12, clopper_pearson), 3), 1.0)
        self.assertEqual(round(coverage(21, 0.2, clopper_pearson), 3), 0.991)
        # Exact construction: at least 95% coverage at every rate on this grid.
        self.assertGreaterEqual(min(coverage(21, step / 200, clopper_pearson) for step in range(1, 200)),
                                0.95 - 1e-12)
        self.assertAlmostEqual(1 - 0.05 ** (1 / 22), 0.1273, places=4)  # uniform-prior posterior, 0 of 21

    def test_input_validation(self):
        functions = (stats.rate_upper_bounds, stats.wilson_upper_bound, stats.clopper_pearson_upper_bound)
        for events, trials in ((-1, 5), (6, 5), (0, 0), (True, 5), (1, 5.0), (1.0, 5), ("1", 5), (None, 5)):
            for function in functions:
                with self.subTest(function=function.__name__, events=events, trials=trials), \
                        self.assertRaises(ValueError):
                    function(events, trials)
        for confidence in (0.5, 1.0, 0.0, 1, True, "0.95", 1.5, -0.95, float("nan")):
            for function in functions:
                with self.subTest(function=function.__name__, confidence=confidence), \
                        self.assertRaises(ValueError):
                    function(1, 5, confidence=confidence)
        self.assertEqual(stats.confidence_alpha(0.95), ALPHA)
        self.assertGreater(stats.clopper_pearson_upper_bound(1, 5, confidence=0.99),
                           stats.clopper_pearson_upper_bound(1, 5, confidence=0.9))


class HypergeometricUpperBoundTests(unittest.TestCase):
    """Exact finite-population bound on misses (Callaghan and Mueller-Hansen 2020, inverted)."""

    def test_bisection_equals_a_linear_scan_of_an_independent_fraction_cdf(self):
        # Every N <= 30 at 95% (N <= 16 at 90% and 99%): the largest K whose CDF reaches alpha.
        cases, rises, mismatches = 0, [], []
        for confidence, alpha, largest in ((0.95, Fraction(1, 20), 30), (0.9, Fraction(1, 10), 16),
                                           (0.99, Fraction(1, 100), 16)):
            for population in range(1, largest + 1):
                for sample in range(population + 1):
                    table = [[hypergeometric_cdf(population, total, sample, x) for x in range(sample + 1)]
                             for total in range(population + 1)]
                    for x in range(sample + 1):
                        column = [row[x] for row in table]
                        # The bisection relies on the CDF never rising with K.
                        if any(a < b for a, b in zip(column, column[1:])):
                            rises.append((population, sample, x))
                        expected = max(total for total, value in enumerate(column) if value >= alpha)
                        actual = stats.hypergeometric_upper_bound(x, sample, population,
                                                                  confidence=confidence)["upper_misses"]
                        cases += 1
                        if actual != expected:
                            mismatches.append((confidence, population, sample, x, actual, expected))
        self.assertEqual(cases, 7391)
        self.assertEqual(rises, [])
        self.assertEqual(mismatches, [])

    def test_exact_coverage_is_at_least_the_confidence_for_sixty_items(self):
        # P(bound >= K) summed exactly over x for every true K = 0..60, k = 15.
        population, sample, minimum = 60, 15, {}
        for confidence, floor in ((0.95, Fraction(19, 20)), (0.9, Fraction(9, 10))):
            bounds = [stats.hypergeometric_upper_bound(x, sample, population, confidence=confidence)["upper_misses"]
                      for x in range(sample + 1)]
            minimum[confidence] = min(sum(hypergeometric_pmf(population, total, sample, x)
                                          for x in range(sample + 1) if bounds[x] >= total)
                                      for total in range(population + 1))
            with self.subTest(confidence=confidence):
                self.assertGreaterEqual(minimum[confidence], floor)
        self.assertEqual(round(float(minimum[0.95]), 4), 0.9503)  # the documented exact minimum

    def test_documented_values_and_the_binomial_comparison(self):
        cases = {(0, 20, 500): 68, (0, 40, 500): 34, (1, 20, 500): 106, (0, 40, 100): 5,
                 (0, 40, 20_000): 1441, (3, 400, 1_000_000): 19_266}
        for (misses, sample, population), upper in cases.items():
            with self.subTest(misses=misses, sample=sample, population=population):
                result = stats.hypergeometric_upper_bound(misses, sample, population)
                self.assertEqual(result, {"misses": misses, "sample": sample, "population": population,
                                          "confidence": 0.95, "upper_misses": upper,
                                          "upper_miss_share": upper / population,
                                          "method": "hypergeometric_exact_inversion; integer bisection over K"})
        self.assertEqual(stats.hypergeometric_upper_bound(0, 20, 500)["upper_miss_share"], 0.136)
        # The binomial (with-replacement) Clopper-Pearson bound times N is looser.
        for (sample, population), binomial in (((20, 500), 69.55), ((40, 500), 36.08), ((40, 100), 7.22)):
            with self.subTest(sample=sample, population=population):
                scaled = stats.clopper_pearson_upper_bound(0, sample) * population
                self.assertEqual(round(scaled, 2), binomial)
                self.assertLess(stats.hypergeometric_upper_bound(0, sample, population)["upper_misses"], scaled)

    def test_edge_cases_need_no_special_handling(self):
        for population in (1, 2, 7, 50):
            with self.subTest(population=population):
                # Every sampled item missed: nothing excludes N.
                self.assertEqual([stats.hypergeometric_upper_bound(k, k, population)["upper_misses"]
                                  for k in range(population + 1)], [population] * (population + 1))
                # A census counts the misses exactly.
                self.assertEqual([stats.hypergeometric_upper_bound(x, population, population)["upper_misses"]
                                  for x in range(population + 1)], list(range(population + 1)))
        self.assertEqual(stats.hypergeometric_upper_bound(10, 10, 50)["upper_misses"], 50)
        self.assertEqual(stats.hypergeometric_upper_bound(7, 50, 50)["upper_misses"], 7)
        self.assertEqual(stats.hypergeometric_upper_bound(0, 50, 50)["upper_misses"], 0)
        self.assertEqual(stats.hypergeometric_upper_bound(0, 0, 10)["upper_misses"], 10)
        # The bound only grows with x and with confidence, and shrinks as k grows.
        rising = [stats.hypergeometric_upper_bound(x, 20, 500)["upper_misses"] for x in range(21)]
        self.assertEqual(rising, sorted(set(rising)))
        falling = [stats.hypergeometric_upper_bound(0, k, 500)["upper_misses"] for k in (5, 10, 20, 40, 80, 160)]
        self.assertEqual(falling, sorted(set(falling), reverse=True))
        self.assertGreater(stats.hypergeometric_upper_bound(1, 20, 500, confidence=0.99)["upper_misses"],
                           stats.hypergeometric_upper_bound(1, 20, 500, confidence=0.9)["upper_misses"])

    def test_large_populations_use_exact_integers_quickly(self):
        started = time.perf_counter()
        self.assertEqual(stats.hypergeometric_upper_bound(3, 400, 1_000_000)["upper_misses"], 19_266)
        self.assertEqual(stats.hypergeometric_upper_bound(100, 5_000, 1_000_000)["upper_misses"], 23_564)
        self.assertLess(time.perf_counter() - started, 5.0)

    def test_alpha_is_exact_from_the_decimal_confidence(self):
        self.assertEqual(stats.exact_alpha(0.95), Fraction(1, 20))
        self.assertEqual(stats.exact_alpha(0.9), Fraction(1, 10))
        self.assertEqual(stats.exact_alpha(0.99), Fraction(1, 100))
        self.assertNotEqual(1 - Fraction(0.95), Fraction(1, 20))  # why the float is not used directly
        for confidence in (0.5, 1.0, 0.0, 1, True, "0.95", 1.5, -0.95, float("nan")):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    stats.exact_alpha(confidence)
                with self.assertRaises(ValueError):
                    stats.hypergeometric_upper_bound(0, 20, 500, confidence=confidence)

    def test_input_validation(self):
        for misses, sample, population in ((-1, 5, 10), (6, 5, 10), (0, 11, 10), (0, 0, 0), (0, -1, 10),
                                           (True, 5, 10), (0, 5.0, 10), (0, 5, 10.0), ("0", 5, 10), (None, 5, 10)):
            with self.subTest(misses=misses, sample=sample, population=population), self.assertRaises(ValueError):
                stats.hypergeometric_upper_bound(misses, sample, population)


class ExactPairedEvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain = evaluator.compare_render_matched(ADVANCED_FIXTURE)
        cls.exact = evaluator.compare_render_matched(ADVANCED_FIXTURE, exact_paired=True)

    def test_default_paths_never_load_the_statistics_module(self):
        with mock.patch.object(evaluator, "_load_eval_stats", side_effect=AssertionError("loaded")):
            default = evaluator.compare(ADVANCED_FIXTURE)
            explicit = evaluator.compare(ADVANCED_FIXTURE, exact_paired=False)
            for argv in ([], ["--fixture", str(ADVANCED_FIXTURE), "--compare", *GATES],
                         ["--fixture", str(ADVANCED_FIXTURE), "--compare", "--render-matched"]):
                with self.subTest(argv=argv):
                    self.assertEqual(run_main(argv)[0], 0)
        self.assertEqual(canonical(default), canonical(explicit))
        self.assertEqual(canonical(default), canonical({k: v for k, v in self.plain.items()
                                                        if k not in ("render_matched", "temporal_decomposition")}))
        for metric, record in default["paired_randomization"].items():
            with self.subTest(metric=metric):
                self.assertEqual(set(record), LEGACY_PAIRED_KEYS)
                values = [(a[metric], b[metric]) for a, b in zip(default["baseline"]["per_query"],
                                                                 default["improved"]["per_query"])
                          if a[metric] is not None and b[metric] is not None]
                self.assertEqual(record, evaluator.paired_randomization([v[0] for v in values],
                                                                        [v[1] for v in values]))

    def test_default_paired_test_is_the_monte_carlo_function(self):
        generator = random.Random(5)
        for _ in range(20):
            size = generator.randint(0, 30)
            baseline = [generator.random() for _ in range(size)]
            improved = [generator.choice((value, generator.random())) for value in baseline]
            seed, permutations = generator.randint(0, 9999), generator.randint(1, 500)
            self.assertEqual(evaluator.paired_test(baseline, improved, seed=seed, permutations=permutations),
                             evaluator.paired_randomization(baseline, improved, seed=seed,
                                                            permutations=permutations))
        self.assertEqual(evaluator.paired_randomization([0.25, 0.5] * 4, [1.0, 0.875] * 4)["p_value"],
                         evaluator.paired_test([0.25, 0.5] * 4, [1.0, 0.875] * 4)["p_value"])

    def test_exact_records_name_method_k_and_minimum(self):
        report = self.exact
        self.assertEqual(canonical({k: v for k, v in report.items() if k not in ("paired_randomization",
                                                                                 "temporal_decomposition")}),
                         canonical({k: v for k, v in self.plain.items() if k not in ("paired_randomization",
                                                                                     "temporal_decomposition")}))
        for metric, record in report["paired_randomization"].items():
            values = [(a[metric], b[metric]) for a, b in zip(report["baseline"]["per_query"],
                                                             report["improved"]["per_query"])
                      if a[metric] is not None and b[metric] is not None]
            nonzero = sum(a != b for a, b in values)
            with self.subTest(metric=metric):
                self.assertEqual(set(record), EXACT_PAIRED_KEYS)
                self.assertEqual(record["method"], stats.EXACT_PAIRED_METHOD)
                self.assertEqual(record["nonzero_pair_count"], nonzero)
                self.assertEqual(record["minimum_attainable_p_value"], 2.0 ** (1 - nonzero) if nonzero else 1.0)
                self.assertEqual(record, stats.exact_paired_sign_flip([v[0] for v in values], [v[1] for v in values]))
                self.assertEqual(record["mean_delta"], self.plain["paired_randomization"][metric]["mean_delta"])

    def test_more_than_twenty_non_zero_pairs_keep_the_seeded_monte_carlo_p(self):
        baseline, improved = [0.0] * 26, [0.5 if index % 3 else -0.25 for index in range(24)] + [0.0, 0.0]
        result = evaluator.paired_test(baseline, improved, exact=True)
        sampled = evaluator.paired_randomization(baseline, improved)
        self.assertEqual(result, {**sampled, "method": sampled["method"] + " (more than 20 non-zero pairs)",
                                  "nonzero_pair_count": 24, "minimum_attainable_p_value": 2.0 ** -23})
        twenty = evaluator.paired_test(baseline[:20] + [0.0] * 6, improved[:20] + [0.0] * 6, exact=True)
        self.assertEqual(twenty["method"], stats.EXACT_PAIRED_METHOD)
        for options in ({"exact": "yes"}, {"exact": True, "seed": 1.5}, {"exact": True, "permutations": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                evaluator.paired_test([0.0], [1.0], **options)
        for value in ("yes", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluator.compare(ADVANCED_FIXTURE, exact_paired=value)

    def test_exact_decomposition_keeps_the_existing_floor_fields(self):
        plain, exact_run = self.plain["temporal_decomposition"], self.exact["temporal_decomposition"]
        self.assertEqual(plain["method"], "render-matched control arm (arXiv 2607.16019): advanced retrieval with "
                                          "temporal_filter='annotate_only'; paired sign randomization per metric")
        self.assertTrue(exact_run["method"].startswith(plain["method"] + "; exact enumeration"))
        self.assertEqual(exact_run["decision"], plain["decision"])
        self.assertEqual(exact_run["warnings"], plain["warnings"])
        tests = ("apparent_gain", "display_effect", "mechanism_residual")
        for name, row in exact_run["metrics"].items():
            residual = row["mechanism_residual"]
            with self.subTest(metric=name):
                self.assertEqual({k: v for k, v in row.items() if k not in tests},
                                 {k: v for k, v in plain["metrics"][name].items() if k not in tests})
                self.assertEqual(residual["nonzero_pair_count"], row["mechanism_nonzero_pair_count"])
                self.assertEqual(residual["minimum_attainable_p_value"], row["mechanism_minimum_attainable_p_value"])
                for test in tests:
                    self.assertEqual(row[test]["method"], stats.EXACT_PAIRED_METHOD)
                    self.assertLessEqual(abs(row[test]["p_value"] - plain["metrics"][name][test]["p_value"]), 0.02)
        residual_p = {name: row["mechanism_residual"]["p_value"] for name, row in exact_run["metrics"].items()}
        self.assertEqual(residual_p, {"ndcg_at_5": 0.5, "source_recall_at_k": 1.0, "stale_top_1": 0.125,
                                      "no_answer_false_positive": 0.5, "stale_exposure": 0.125})

    def test_cli_exact_paired_requires_compare_and_keeps_the_exit_code(self):
        code, stdout, stderr = run_main(["--fixture", str(ADVANCED_FIXTURE), "--exact-paired"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("--exact-paired requires --compare", stderr)
        plain = run_main(["--fixture", str(ADVANCED_FIXTURE), "--compare", *GATES])
        flagged = run_main(["--fixture", str(ADVANCED_FIXTURE), "--compare", *GATES, "--exact-paired"])
        self.assertEqual((plain[0], flagged[0]), (0, 0))
        self.assertEqual((plain[2], flagged[2]), ("", ""))
        plain_report, flagged_report = json.loads(plain[1]), json.loads(flagged[1])
        self.assertEqual({k: v for k, v in flagged_report.items() if k != "paired_randomization"},
                         {k: v for k, v in plain_report.items() if k != "paired_randomization"})
        self.assertTrue(all(record["method"] == stats.EXACT_PAIRED_METHOD
                            for record in flagged_report["paired_randomization"].values()))


class RateBoundEvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.single = evaluator.evaluate(ADVANCED_FIXTURE, advanced=True)
        cls.comparison = evaluator.compare_render_matched(ADVANCED_FIXTURE)

    def assert_summary_bounds(self, report):
        omitted = 0
        for label, summary in summaries(report):
            keys = list(summary)
            self.assertEqual(keys[keys.index("stale_sources") + 1], "rate_upper_bounds", label)
            for name, group, numerator, denominator in evaluator.RATE_BOUND_FIELDS:
                counts = summary[group]
                with self.subTest(summary=label, rate=name):
                    if counts[denominator]:
                        self.assertEqual(summary["rate_upper_bounds"][name],
                                         stats.rate_upper_bounds(counts[numerator], counts[denominator]))
                    else:
                        self.assertNotIn(name, summary["rate_upper_bounds"])
                        omitted += 1
        return omitted

    def test_every_summary_gains_bounds_for_existing_rates_only(self):
        original = copy.deepcopy(self.single)
        extended = evaluator.with_rate_bounds(self.single)
        self.assertEqual(self.single, original, "input report is not mutated")
        self.assertEqual(without_bounds(extended), self.single)
        self.assertEqual(evaluator.with_rate_bounds(extended), extended, "a second pass replaces, never duplicates")
        relabeled = evaluator.with_rate_bounds(extended, confidence=0.9)
        self.assertEqual(relabeled["rate_upper_bounds"]["stale_top_1_rate"]["confidence"], 0.9)
        self.assertEqual(list(relabeled), list(extended))
        self.assertGreater(self.assert_summary_bounds(extended), 0, "some summaries lack a denominator")
        keys = list(extended)
        self.assertEqual(keys[keys.index("interpretation") + 1], "rate_upper_bound_definitions")
        definitions = extended["rate_upper_bound_definitions"]
        self.assertEqual((definitions["confidence"], definitions["side"]), (0.95, "one-sided upper"))
        self.assertIn("never gates", definitions["interpretation"])
        overall = extended["rate_upper_bounds"]
        self.assertEqual({name: (row["numerator"], row["denominator"]) for name, row in overall.items()},
                         {"no_answer_false_positive_rate": (0, 2), "forbidden_exposure_rate": (0, 2),
                          "stale_top_1_rate": (0, 4)})
        self.assertAlmostEqual(overall["stale_top_1_rate"]["clopper_pearson_upper"], 1 - ALPHA ** (1 / 4), places=12)

    def test_compare_arms_all_gain_bounds_and_other_sections_stay(self):
        extended = evaluator.with_rate_bounds(self.comparison)
        self.assertEqual(without_bounds(extended), self.comparison)
        for arm in ("baseline", "improved", "render_matched"):
            with self.subTest(arm=arm):
                self.assert_summary_bounds(extended[arm])
        self.assertEqual(extended["temporal_decomposition"], self.comparison["temporal_decomposition"])
        self.assertEqual(extended["paired_randomization"], self.comparison["paired_randomization"])
        self.assertEqual(list(extended).count("rate_upper_bound_definitions"), 1)
        # --expand-query adds an expansion_ablation arm; it gains bounds like the others.
        expanded = {"benchmark": "paired retrieval comparison", "interpretation": "synthetic",
                    "baseline": self.single, "improved": self.single, "expansion_ablation": self.single}
        extended = evaluator.with_rate_bounds(expanded)
        self.assertEqual(without_bounds(extended), expanded)
        for arm in ("baseline", "expansion_ablation", "improved"):
            with self.subTest(arm=arm):
                self.assert_summary_bounds(extended[arm])
        for invalid in ([], {}, {"baseline": {}}):
            with self.subTest(report=invalid), self.assertRaises(ValueError):
                evaluator.with_rate_bounds(invalid)
        for confidence in (0.4, 1, True):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                evaluator.with_rate_bounds(self.single, confidence=confidence)

    def test_cli_bounds_are_additive_and_never_change_gates(self):
        argv = ["--fixture", str(ADVANCED_FIXTURE), "--compare", *GATES]
        plain_code, plain_out, plain_err = run_main(argv)
        flag_code, flag_out, flag_err = run_main([*argv, "--rate-bounds"])
        self.assertEqual((plain_code, flag_code), (0, 0))
        # Byte equality once the added keys are removed: no existing value or key order changed.
        self.assertEqual(json.dumps(without_bounds(json.loads(flag_out)), ensure_ascii=False, indent=2) + "\n",
                         plain_out)
        self.assertEqual(plain_err, "")
        notes = [line for line in flag_err.splitlines() if line.startswith("rate-bounds note (non-blocking): ")]
        self.assertEqual(len(notes), 3, flag_err)
        self.assertIn("stale_top_1_rate passed at 0/4 (maximum 0), but its one-sided 95% upper bound is 0.5271", flag_err)
        self.assertIn("no_answer_false_positive_rate passed at 0/2", flag_err)
        flagged = json.loads(flag_out)
        self.assertEqual(flagged["improved"]["optional_gates"], json.loads(plain_out)["improved"]["optional_gates"])
        self.assertIn("rate_upper_bounds", flagged["baseline"])

    def test_cli_failed_gate_keeps_its_exit_code_and_gets_no_note(self):
        argv = ["--fixture", str(ADVANCED_FIXTURE), "--max-stale-top-1-rate", "0"]
        plain_code, plain_out, _ = run_main(argv)
        flag_code, flag_out, flag_err = run_main([*argv, "--rate-bounds"])
        self.assertEqual((plain_code, flag_code), (1, 1))
        self.assertFalse(json.loads(flag_out)["optional_gates"]["stale_top_1_rate"]["passed"])
        self.assertNotIn("rate-bounds note", flag_err)
        self.assertEqual(json.dumps(without_bounds(json.loads(flag_out)), ensure_ascii=False, indent=2) + "\n",
                         plain_out)

    def test_split_runs_take_exact_pairs_and_bounds_on_the_split_only(self):
        # --split (merged from the gold-set branch) selects the queries; the statistics follow them.
        hard = ROOT / "tests" / "fixtures" / "recall_hard"
        argv = ["--fixture", str(hard), "--compare", "--expand-query", "--query-mapping", "00-meta/query-map.txt",
                "--top-k", "3", "--max-tokens", "1500", "--max-no-answer-false-positive-rate", "0.25",
                "--max-stale-top-1-rate", "0", "--split", "heldout"]
        plain_code, plain_out, plain_err = run_main(argv)
        flag_code, flag_out, flag_err = run_main([*argv, "--exact-paired", "--rate-bounds"])
        self.assertEqual(flag_code, plain_code)
        plain, flagged = json.loads(plain_out), json.loads(flag_out)
        # Without the added keys and the paired records the reports are identical.
        self.assertEqual({k: v for k, v in without_bounds(flagged).items() if k != "paired_randomization"},
                         {k: v for k, v in plain.items() if k != "paired_randomization"})
        improved = flagged["improved"]
        self.assertEqual(improved["split"]["name"], "heldout")
        self.assertEqual(improved["query_count"], improved["split"]["evaluated_query_count"])
        bound = improved["rate_upper_bounds"]["no_answer_false_positive_rate"]
        self.assertEqual((bound["numerator"], bound["denominator"]),
                         (improved["no_answer"]["false_positive_query_count"], improved["no_answer"]["query_count"]))
        for metric, record in flagged["paired_randomization"].items():
            with self.subTest(metric=metric):
                self.assertEqual(record["mean_delta"], plain["paired_randomization"][metric]["mean_delta"])
                self.assertLessEqual(record["paired_query_count"], improved["query_count"])
                if record["nonzero_pair_count"] <= stats.EXACT_PAIRED_MAX_NONZERO:
                    self.assertEqual(record["method"], stats.EXACT_PAIRED_METHOD)
        # The split note stays first; rate-bound notes only add lines.
        self.assertTrue(flag_err.startswith(plain_err), flag_err)
        self.assertIn("recall evaluation split: heldout n=32", flag_err)


class ExistingOutputsUnchangedTests(unittest.TestCase):
    """Adding the hypergeometric bound leaves every earlier output alone."""

    def test_v0310_functions_return_the_same_values(self):
        text = json.dumps(existing_outputs(stats), sort_keys=True, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), V0310_EXISTING_OUTPUTS_SHA256)

    def test_v0310_signatures_are_unchanged(self):
        for name, expected in V0310_SIGNATURES.items():
            with self.subTest(function=name):
                parameters = inspect.signature(getattr(stats, name)).parameters.values()
                self.assertEqual([(p.name, p.kind.name, None if p.default is inspect.Parameter.empty else p.default)
                                  for p in parameters], expected)

    def test_recall_reports_never_call_the_hypergeometric_bound(self):
        flagged = ["--fixture", str(ADVANCED_FIXTURE), "--compare", *GATES, "--exact-paired", "--rate-bounds"]
        expected = [run_main([]), run_main(flagged)]
        loaded = evaluator._load_eval_stats()
        with mock.patch.object(loaded, "hypergeometric_upper_bound", create=True,
                               side_effect=AssertionError("called")), \
                mock.patch.object(loaded, "exact_alpha", create=True, side_effect=AssertionError("called")), \
                mock.patch.object(evaluator, "_load_eval_stats", wraps=evaluator._load_eval_stats) as loads:
            self.assertEqual(run_main([]), expected[0])
            self.assertEqual(loads.call_count, 0, "the default run never loads eval_stats")
            self.assertEqual(run_main(flagged), expected[1])
        self.assertEqual(expected[0][0], 0)
        self.assertEqual(expected[1][0], 0)


if __name__ == "__main__":
    unittest.main()
