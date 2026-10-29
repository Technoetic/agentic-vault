"""Opt-in conformal abstention threshold for the recall evaluator (--abstention-calibration conformal).

The dev side of the committed split calibrates a refusal threshold on one
retrieval signal and the heldout side tests it: split conformal p-values after
Bates et al. (arXiv 2104.08279) and a Learn then Test version (arXiv
2110.01052). The engine keeps abstention: host_decision. Without the flag every
report, the CLI output and the exit code stay as before.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import importlib.util
import io
import itertools
import json
import math
import random
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RECALL_HARD = ROOT / "tests" / "fixtures" / "recall_hard"
LOOSE = ["--min-recall-at-3", "0", "--min-mrr", "0"]
SECTION = "conformal_abstention"
TWINS = "gold_removal_twins"
FLAG = ["--abstention-calibration", "conformal"]
KEY = b"CONFORMALKEYMARK:" + bytes(range(40, 80))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator = _load("conformal_abstention_recall_evaluator", ROOT / "scripts" / "evaluate_recall.py")


def run_main(argv):
    """main() with a frozen clock so two runs print byte-comparable JSON."""
    counter = itertools.count()
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(evaluator.time, "perf_counter", new=lambda: next(counter) * 0.001), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = evaluator.main([str(value) for value in argv])
    return code, stdout.getvalue(), stderr.getvalue()


def without_section(value):
    if isinstance(value, dict):
        return {key: without_section(item) for key, item in value.items() if key != SECTION}
    if isinstance(value, list):
        return [without_section(item) for item in value]
    return value


def all_keys(value) -> set:
    if isinstance(value, dict):
        return set(value).union(*(all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value))
    return set()


def hmac_hex(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def bates_p_value(calibration, signal) -> Fraction:
    """Eq. (3) of Bates et al.: (1 + #{calibration <= signal}) / (n + 1), exactly."""
    return Fraction(1 + sum(value <= signal for value in calibration), len(calibration) + 1)


def binomial_cdf_exact(n: int, x: int, alpha: Fraction) -> Fraction:
    return sum(Fraction(math.comb(n, i)) * alpha ** i * (1 - alpha) ** (n - i) for i in range(x + 1))


def counts(section: dict) -> tuple:
    """(n, dev empty, k, threshold, ties, heldout empty, heldout threshold refusals, twin empty, twin threshold)."""
    calibration, heldout = section["calibration"], section["heldout"]
    return (calibration["n"], calibration["empty_result_count"], calibration["k"], calibration["threshold"],
            calibration["ties_at_threshold"], heldout["empty_result_count"], heldout["threshold_refusal_count"],
            heldout["twins"]["empty_result_refusal_count"], heldout["twins"]["threshold_refusal_count"])


class ThresholdRuleTests(unittest.TestCase):
    def test_k_is_floor_alpha_times_n_plus_one_without_float_rounding(self):
        # Boundaries of the spec: alpha = 0.05 needs n >= 19 for k = 1; n = 18 has no threshold.
        self.assertEqual(evaluator.conformal_threshold(list(range(18, 0, -1)), 0.05), (0, None))
        self.assertEqual(evaluator.conformal_threshold(list(range(19, 0, -1)), 0.05), (1, 1))
        self.assertEqual(evaluator.conformal_threshold(list(range(28)), 0.05), (1, 0))
        self.assertEqual(evaluator.conformal_threshold(list(range(8)), 0.1), (0, None))
        self.assertEqual(evaluator.conformal_threshold(list(range(9)), 0.1), (1, 0))
        self.assertEqual(evaluator.conformal_threshold(list(range(28)), 0.1), (2, 1))
        self.assertEqual(evaluator.conformal_threshold([], 0.5), (0, None))
        # 0.29 * 100 is 28.999999999999996 in floats; alpha is read from its decimal text.
        self.assertEqual(math.floor(0.29 * 100), 28)
        self.assertEqual(evaluator.conformal_threshold(list(range(99)), 0.29), (29, 28))
        for alpha in (0.0, 1.0, 1, -0.1, float("nan"), "0.05"):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                evaluator.conformal_threshold([1, 2, 3], alpha)

    def test_ties_at_the_threshold_answer_and_lower_signals_are_refused(self):
        calibration = [7, 7, 7] + list(range(8, 24))  # n = 19, k = 1: the threshold is 7 with three ties
        k, threshold = evaluator.conformal_threshold(calibration, 0.05)
        self.assertEqual((k, threshold), (1, 7))
        rows = [("candidates", 7), ("candidates", 6.999), ("candidates", 30), ("empty", None), ("error", None)]
        twins = [("candidates", 7.0), ("candidates", 1), ("empty", None), ("error", None)]
        heldout = evaluator._conformal_heldout(rows, twins, threshold)
        self.assertEqual((heldout["empty_result_count"], heldout["threshold_refusal_count"],
                          heldout["false_refusal_count"], heldout["error_count"], heldout["answerable_query_count"]),
                         (1, 1, 2, 1, 5))
        self.assertEqual(heldout["twins"], {"twin_count": 4, "empty_result_refusal_count": 1,
                                            "threshold_refusal_count": 1, "refusal_count": 2,
                                            "refusal_recall": 0.5, "twin_error_count": 1})
        # Without a threshold only empty results refuse.
        none = evaluator._conformal_heldout(rows, twins, None)
        self.assertEqual((none["threshold_refusal_count"], none["twins"]["threshold_refusal_count"]), (0, 0))

    def test_p_value_rule_and_threshold_rule_decide_alike(self):
        generator = random.Random(1707)
        alphas = (0.01, 0.05, 0.1, 0.2, 0.29, 0.5, 0.9)
        checked = 0
        for _ in range(400):
            n = generator.randrange(0, 61)
            spread = generator.choice((2, 5, 50))
            calibration = [generator.randrange(spread) if generator.random() < 0.7 else generator.random() * spread
                           for _ in range(n)]
            probes = [*calibration, -1, spread + 1, *(generator.random() * spread for _ in range(5))]
            for alpha in alphas:
                _, threshold = evaluator.conformal_threshold(calibration, alpha)
                for signal in probes:
                    by_p = bates_p_value(calibration, signal) <= Fraction(repr(alpha))
                    by_threshold = threshold is not None and signal < threshold
                    self.assertEqual(by_p, by_threshold, (n, alpha, signal))
                    checked += 1
        self.assertGreater(checked, 50000)

    def test_learn_then_test_runs_a_fixed_sequence_from_the_no_refusal_end(self):
        calibration = [50, 50, 50, *range(51, 76)]  # n = 28, no empty result
        result = evaluator.learn_then_test(calibration, 0, 0.05, 0.1)
        # The no-refusal end already has p = 0.95^28 = 0.238 > 0.1: no threshold (the paper abstains).
        self.assertEqual((result["n"], result["result"], result["threshold"]), (28, "none", None))
        self.assertEqual(len(result["tested"]), 1)
        self.assertAlmostEqual(result["tested"][0]["p_value"], 0.95 ** 28, places=12)
        self.assertAlmostEqual(0.95 ** 28, 0.2378, places=4)
        # alpha = 0.1: p = 0.9^28 = 0.052 passes at delta = 0.1, the next threshold refuses the three ties and fails.
        result = evaluator.learn_then_test(calibration, 0, 0.1, 0.1)
        self.assertEqual((result["result"], result["threshold"]), ("threshold", 50))
        self.assertEqual([(step["threshold"], step["false_refusal_count"], step["passed"])
                          for step in result["tested"]], [(50, 0, True), (51, 3, False)])
        self.assertAlmostEqual(result["tested"][0]["p_value"], 0.9 ** 28, places=12)
        for step in result["tested"]:
            self.assertAlmostEqual(step["p_value"], float(binomial_cdf_exact(28, step["false_refusal_count"],
                                                                             Fraction(1, 10))), places=12)
        self.assertEqual(evaluator.learn_then_test(calibration, 0, 0.1, 0.05)["result"], "none")
        # Empty results count as false refusals in this risk: 7 of 28 already exceed alpha.
        result = evaluator.learn_then_test(calibration[:21], 7, 0.1, 0.1)
        self.assertEqual((result["n"], result["empty_result_count"], result["result"]), (28, 7, "none"))
        self.assertEqual(result["tested"][0]["false_refusal_count"], 7)
        self.assertEqual(evaluator.learn_then_test([], 0, 0.05, 0.1),
                         {"n": 0, "empty_result_count": 0, "tested": [], "threshold": None, "result": "none"})

    def test_simes_sequence_matches_the_paper(self):
        # Smallest CCV p-value with k = n/2: 1 - delta^(2/n) = 0.152 at n = 28, delta = 0.1.
        self.assertAlmostEqual(evaluator._simes_upper_bound(28, 14, 0.1, 0), 1 - 0.1 ** (1 / 14), places=12)
        self.assertAlmostEqual(evaluator._simes_upper_bound(28, 14, 0.1, 0), 0.1517, places=4)
        n, k, delta = 21, 10, 0.1
        bounds = [evaluator._simes_upper_bound(n, k, delta, below) for below in range(n + 1)]
        for below, bound in enumerate(bounds):
            remaining = n - below  # b_(n+1-i) with i = remaining
            product = math.prod(remaining - m for m in range(k)) / math.prod(n - m for m in range(k))
            expected = 1 - delta ** (1 / k) * product ** (1 / k) if remaining >= k else 1.0
            self.assertAlmostEqual(bound, expected, places=12)
        self.assertEqual(bounds, sorted(bounds))
        self.assertEqual(bounds[n - k + 1:], [1.0] * k)
        # The report states the k the code uses: at n = 1, floor(n / 2) would be 0.
        definition = " ".join(evaluator.CONFORMAL_DEFINITIONS["calibration_conditional"].split())
        self.assertIn("(k = simes_k = max(1, floor(n / 2)))", definition)

    def test_each_bound_point_covers_the_thresholds_up_to_its_own(self):
        # Proposition 3 bounds refusing below t by b_(#(calibration < t) + 1): a point's upper_bound covers every t
        # above the previous point up to and including its own, and a t just above a point also refuses its ties.
        delta = 0.1

        def valid(values, threshold, simes_k):
            return evaluator._simes_upper_bound(len(values), simes_k, delta, sum(value < threshold for value in values))

        def covering(points, threshold):
            covers = [point["upper_bound"] for point in points if threshold <= point["threshold"]]
            return covers[0] if covers else 1.0

        calibration = [50, 50, 50, 51, 51, 51, 51, *range(55, 76)]  # n = 28: three ties, then four
        points = evaluator._simultaneous_fpr_bound(sorted(calibration), 14, delta)
        self.assertEqual([(point["threshold"], point["calibration_below"]) for point in points[:3]],
                         [(50, 0), (51, 3), (55, 7)])
        # 50.5 refuses the three signals tied at 50: b_4 = 0.2749 of the point at 51, not 0.1517 of the point at 50.
        between = valid(calibration, 50.5, 14)
        self.assertAlmostEqual(between, 1 - 0.1 ** (1 / 14) * (14 * 13 * 12 / (28 * 27 * 26)) ** (1 / 14), places=12)
        self.assertAlmostEqual(between, 0.2749, places=4)
        self.assertEqual(between, points[1]["upper_bound"])
        self.assertGreater(between, points[0]["upper_bound"])
        self.assertEqual((valid(calibration, 49, 14), valid(calibration, 76, 14)), (points[0]["upper_bound"], 1.0))
        generator = random.Random(1707)
        checked = 0
        for _ in range(300):
            size = generator.randrange(1, 61)
            spread = generator.choice((2, 5, 50))
            values = [generator.randrange(spread) if generator.random() < 0.7 else generator.random() * spread
                      for _ in range(size)]
            simes_k = max(1, size // 2)
            drawn = evaluator._simultaneous_fpr_bound(sorted(values), simes_k, delta)
            distinct = sorted(set(values))
            self.assertEqual([point["threshold"] for point in drawn], distinct)
            probes = [-1, spread + 1, *distinct, *((low + high) / 2 for low, high in zip(distinct, distinct[1:]))]
            for threshold in probes:
                self.assertEqual(valid(values, threshold, simes_k), covering(drawn, threshold), (size, threshold))
                checked += 1
        self.assertGreater(checked, 5000)
        definition = " ".join(evaluator.CONFORMAL_DEFINITIONS["calibration_conditional"].split())
        self.assertIn("covers every threshold above the previous point up to and including its own", definition)

    def test_signals_read_the_fixed_score_and_never_the_rrf_sum(self):
        legacy = [{"path": "a.md", "score": 17}, {"path": "b.md", "score": 23}]
        self.assertEqual(evaluator._conformal_signal_value(legacy, "lexical"), 23)
        self.assertIsNone(evaluator._conformal_signal_value(legacy, "bm25"))
        advanced = [{"path": "a.md", "score": 0.032, "scores": {"lexical": 12, "bm25": 0.0, "bm25_hangul2": 3.5}},
                    {"path": "b.md", "score": 0.016, "scores": {"lexical": 30, "bm25": 2.25, "bm25_hangul2": 1.5}}]
        self.assertEqual([evaluator._conformal_signal_value(advanced, name) for name in evaluator.CONFORMAL_SIGNALS],
                         [30, 2.25, 3.5])
        self.assertIsNone(evaluator._conformal_signal_value([], "lexical"))
        self.assertIsNone(evaluator._conformal_signal_value([{"scores": {"lexical": float("nan")}}], "lexical"))
        rows = [{"retrieval_options": options} for options in (
            {}, {"backend": "hybrid", "lexical_ngrams": "hangul2"}, {"backend": "bm25"},
            {"backend": "bm25", "lexical_ngrams": "hangul2"}, {"backend": "other"}, {"backend": ["bm25"]})]
        self.assertEqual([evaluator._conformal_kind({"method": "advanced"}, row) for row in rows],
                         ["lexical", "hybrid", "bm25", "bm25+hangul2", "invalid", "invalid"])
        self.assertEqual(evaluator._conformal_kind({"method": "legacy"}, rows[2]), "legacy")


TOPIC = "20-knowledge/Topic {:02d}.md"
WEAK = "20-knowledge/Misc.md"
NEAR = "20-knowledge/Topic 21 notes.md"


def topic_query(index: int, **extra) -> dict:
    return {"id": f"CALID{index:02d}", "language": "en", "scenario": f"CALSCEN{index % 2}",
            "query": f"caltopic{index:02d}", "expected_paths": [TOPIC.format(index)],
            "required_claims": [f"gear notes for caltopic{index:02d}"], **extra}


class SyntheticCase(unittest.TestCase):
    """19 dev queries that each return one title match of the same score (all ties), and a heldout side with a
    tie, a weaker body-only match and a twin that falls back to a weaker near-duplicate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.fixture = self.root / "fixture"
        self.vault = self.fixture / "vault"
        self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["private"], "exclude_dirs": ["scratch"]}))
        for index in range(22):
            self.write(TOPIC.format(index), f"# CALTOPIC{index:02d} manual\nGear notes for CALTOPIC{index:02d}.\n")
        self.write(WEAK, "# Misc\nThe weakword appears here once.\n")
        self.write(NEAR, "# Notes\nSee caltopic21 in passing.\n")
        self.write("private/Secret.md", "# CALTOPIC00 secret\nDENYTOKEN gear notes for caltopic00.\n")
        self.write("scratch/Draft.md", "# CALTOPIC00 draft\nDENYTOKEN gear notes for caltopic00.\n")
        self.queries = [topic_query(index) for index in range(19)]
        self.queries += [topic_query(19), {"id": "CALID20", "language": "en", "scenario": "CALSCEN0",
                                           "query": "weakword", "expected_paths": [WEAK]},
                         topic_query(21)]
        self.queries += [{"id": f"CALID{index}", "language": "en", "scenario": "CALSCEN1",
                          "query": "zebra harmonica", "expectation": "no_answer", "expected_paths": []}
                         for index in (22, 23)]
        self.split = {"seed": 7, "dev": [f"CALID{index:02d}" for index in (*range(19), 22)],
                      "heldout": ["CALID19", "CALID20", "CALID21", "CALID23"]}
        self.save()

    def write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def save(self) -> None:
        (self.fixture / "queries.json").write_text(json.dumps({"queries": self.queries}, ensure_ascii=False),
                                                   encoding="utf-8")
        (self.fixture / "split.json").write_text(json.dumps(self.split), encoding="utf-8")

    def calibrate(self, *extra):
        code, stdout, stderr = run_main(["--fixture", self.fixture, *LOOSE, *FLAG, *extra])
        return code, (json.loads(stdout) if stdout else None), stderr


class SyntheticCalibrationTests(SyntheticCase):
    def test_threshold_ties_and_heldout_refusals(self):
        recall_module = evaluator._load_recall_module()
        strong = recall_module.recall(self.vault, "caltopic19", limit=3)["matches"]
        weak = recall_module.recall(self.vault, "weakword", limit=3)["matches"]
        near = recall_module.recall(self.vault, "caltopic21", limit=3)["matches"]
        self.assertEqual([match["path"] for match in near], [TOPIC.format(21), NEAR])
        self.assertLess(weak[0]["score"], strong[0]["score"])
        self.assertLess(near[1]["score"], strong[0]["score"])
        code, report, stderr = self.calibrate()
        self.assertEqual((code, stderr), (0, ""))
        section = report[SECTION]
        self.assertEqual((section["status"], section["signal"], section["rerun_mismatch_count"]),
                         ("threshold", "lexical", 0))
        # n = 19 at alpha = 0.05: k = 1 and the threshold is the shared score, tied 19 times.
        self.assertEqual(counts(section), (19, 0, 1, strong[0]["score"], 19, 0, 1, 2, 1))
        calibration = section["calibration"]
        self.assertEqual((calibration["answerable_query_count"], calibration["error_count"],
                          calibration["distinct_signal_count"]), (19, 0, 1))
        self.assertAlmostEqual(calibration["marginal_refusal_bound"], 1 / 20)
        self.assertAlmostEqual(calibration["realized_rate_above_budget_probability"], 0.95 ** 19, places=12)
        heldout = section["heldout"]
        # The tie (CALID19) and the strong match with a weak runner-up (CALID21) answer; the body-only match is refused.
        self.assertEqual((heldout["answerable_query_count"], heldout["false_refusal_count"]), (3, 1))
        self.assertAlmostEqual(heldout["false_refusal_rate"], 1 / 3)
        # Twins: two come back empty; the CALID21 twin returns only the weaker near-duplicate.
        self.assertEqual(heldout["twins"]["refusal_count"], 3)
        conditional = section["calibration_conditional"]
        self.assertEqual(conditional["learn_then_test"]["result"], "none")
        self.assertAlmostEqual(conditional["learn_then_test"]["tested"][0]["p_value"], 0.95 ** 19, places=12)
        self.assertEqual(conditional["simes_k"], 9)
        self.assertAlmostEqual(conditional["simes_ccv_minimum_p_value"], 1 - 0.1 ** (1 / 9), places=12)
        self.assertEqual([(point["calibration_below"], point["upper_bound"])
                          for point in conditional["simultaneous_fpr_bound"]],
                         [(0, conditional["simes_ccv_minimum_p_value"])])
        self.assertEqual(section["split"]["query_count_by_split"], {"dev": 20, "heldout": 4, "unassigned": 0})
        self.assertEqual((section["split"]["seed"], section["split"]["listed_ids_not_in_queries"]), (7, 0))
        self.assertEqual(section["split"]["definition_canonical_sha256"], evaluator._digest(self.split))
        self.assertEqual(section["costs"]["retrieval_calls"], 19 + 3 + 3)

    def test_eighteen_calibration_queries_leave_no_threshold(self):
        # An inspected dev query leaves the population: n = 18 < 1/alpha - 1, so k = 0.
        self.queries[18]["inspected"] = True
        self.save()
        code, report, stderr = self.calibrate()
        self.assertEqual(code, 0, stderr)
        section = report[SECTION]
        self.assertEqual(section["status"], "no_threshold")
        self.assertEqual(counts(section), (18, 0, 0, None, None, 0, 0, 2, 0))
        self.assertEqual(section["calibration"]["marginal_refusal_bound"], 0.0)
        self.assertIsNone(section["calibration"]["realized_rate_above_budget_probability"])
        # The same 18 values give a threshold at a larger budget: k = floor(0.1 * 19) = 1.
        code, report, _ = self.calibrate("--false-refusal-budget", "0.1")
        self.assertEqual(code, 0)
        self.assertEqual(report[SECTION]["calibration"]["k"], 1)
        self.assertEqual(report[SECTION]["false_refusal_budget"], 0.1)

    def test_unassigned_queries_take_no_part(self):
        self.split["dev"].remove("CALID00")
        self.save()
        _, report, _ = self.calibrate()
        section = report[SECTION]
        self.assertEqual(section["split"]["query_count_by_split"], {"dev": 19, "heldout": 4, "unassigned": 1})
        self.assertEqual((section["calibration"]["n"], section["status"]), (18, "no_threshold"))

    def test_split_file_without_split_and_the_gold_set_mode(self):
        elsewhere = self.root / "splits" / "calibration.json"
        elsewhere.parent.mkdir()
        elsewhere.write_text(json.dumps(self.split), encoding="utf-8")
        (self.fixture / "split.json").unlink()
        code, report, stderr = self.calibrate("--split-file", elsewhere)
        self.assertEqual(code, 0, stderr)
        expected = counts(report[SECTION])
        goldset = ["--vault", self.vault, "--goldset", self.fixture / "queries.json", *LOOSE, *FLAG]
        code, stdout, stderr = run_main(goldset)
        self.assertEqual((code, stdout, stderr.strip()), (2, "", "--abstention-calibration with --goldset requires "
                                                                  "--split-file"))
        code, stdout, stderr = run_main([*goldset, "--split-file", elsewhere])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(counts(json.loads(stdout)[SECTION]), expected)
        self.assertIn("goldset", json.loads(stdout))

    def test_missing_split_file_fails_before_any_retrieval(self):
        (self.fixture / "split.json").unlink()
        with mock.patch.object(evaluator, "evaluate", side_effect=AssertionError("retrieval ran")):
            code, stdout, stderr = run_main(["--fixture", self.fixture, *FLAG])
        self.assertEqual((code, stdout, stderr.strip()),
                         (2, "", "recall evaluation unavailable: split file is not a readable file"))

    def test_a_split_that_selects_no_query_exits_2_before_any_retrieval(self):
        self.split = {"seed": 7, "dev": ["NOTLISTED1"], "heldout": ["NOTLISTED2", "NOTLISTED3"]}
        self.save()
        message = "recall evaluation unavailable: split dev and heldout select no queries"
        goldset = ["--vault", self.vault, "--goldset", self.fixture / "queries.json",
                   "--split-file", self.fixture / "split.json"]
        with mock.patch.object(evaluator, "evaluate", side_effect=AssertionError("retrieval ran")):
            for argv in (["--fixture", self.fixture], goldset):
                with self.subTest(argv=argv):
                    code, stdout, stderr = run_main([*argv, *LOOSE, *FLAG])
                    self.assertEqual((code, stdout, stderr.strip()), (2, "", message))
            # The same rejection as --split, which keeps one side of the same file.
            code, stdout, stderr = run_main(["--fixture", self.fixture, *LOOSE, "--split", "dev"])
            self.assertEqual((code, stdout, stderr.strip()),
                             (2, "", "recall evaluation unavailable: split dev selects no queries"))
        report = evaluator.evaluate(self.fixture)
        with mock.patch.object(evaluator, "_twin_note_scan", side_effect=AssertionError("vault read")), \
                self.assertRaises(evaluator.CalibrationError) as caught:
            evaluator.with_conformal_abstention(report, self.fixture, self.split)
        self.assertEqual(str(caught.exception), "split dev and heldout select no queries")

    def test_a_split_without_answerable_dev_or_heldout_queries_only_adds_a_note(self):
        original = copy.deepcopy(self.split)
        cases = (
            # Only the no_answer query is on the dev side, so nothing calibrates.
            ({**original, "dev": ["CALID22"]}, (0, 3), "no_threshold"),
            # Only the no_answer query is on the heldout side, so nothing is tested.
            ({**original, "heldout": ["CALID23"]}, (19, 0), "threshold"),
        )
        for split, (dev, heldout), status in cases:
            with self.subTest(dev=dev, heldout=heldout):
                self.split = split
                self.save()
                note = (f"conformal abstention note (non-blocking): the split file leaves {dev} answerable dev "
                        f"queries to calibrate on and {heldout} answerable heldout queries to test on (inspected and "
                        "no_answer queries take no part)\n")
                plain_code, plain_out, plain_err = run_main(["--fixture", self.fixture, *LOOSE])
                code, stdout, stderr = run_main(["--fixture", self.fixture, *LOOSE, *FLAG])
                self.assertEqual((code, stderr), (plain_code, plain_err + note))
                self.assertEqual(code, 0)
                flagged = json.loads(stdout)
                self.assertEqual(json.dumps(without_section(flagged), ensure_ascii=False, indent=2) + "\n",
                                 plain_out)
                section = flagged[SECTION]
                self.assertEqual((section["calibration"]["answerable_query_count"],
                                  section["heldout"]["answerable_query_count"], section["status"]),
                                 (dev, heldout, status))
        # Every arm evaluates the same queries, so a compare run prints the note once.
        code, _, stderr = self.calibrate("--compare")
        self.assertEqual((code, stderr), (0, note))

    def test_vault_snapshot_digest_covers_listed_notes_only(self):
        _, first, _ = self.calibrate()
        snapshot = first[SECTION]["vault_snapshot"]
        self.assertEqual((snapshot["listed_note_count"], snapshot["hashed_note_count"], snapshot["complete"],
                          snapshot["omissions"]), (24, 24, True, []))
        _, again, _ = self.calibrate()
        self.assertEqual(again[SECTION]["vault_snapshot"], snapshot)
        # A deny zone or an excluded directory is never listed or read: changing them changes nothing.
        self.write("private/Secret.md", "# changed\nDENYTOKEN again.\n")
        self.write("scratch/Draft.md", "# changed\nDENYTOKEN again.\n")
        _, report, _ = self.calibrate()
        self.assertEqual(report[SECTION]["vault_snapshot"], snapshot)
        self.assertNotIn("DENYTOKEN", json.dumps(report))
        # A byte that never reaches a score still marks another snapshot.
        self.write(WEAK, "# Misc\nThe weakword appears here once. \n")
        _, report, _ = self.calibrate()
        self.assertNotEqual(report[SECTION]["vault_snapshot"]["vault_snapshot_sha256"],
                            snapshot["vault_snapshot_sha256"])
        self.assertEqual(counts(report[SECTION]), counts(first[SECTION]))

    def test_every_compare_arm_gets_its_own_section_and_one_snapshot(self):
        code, report, stderr = self.calibrate("--compare", "--backend", "bm25")
        self.assertEqual(code, 0, stderr)
        self.assertNotIn(SECTION, report)
        sections = {arm: report[arm][SECTION] for arm in ("baseline", "improved")}
        self.assertEqual({arm: section["signal"] for arm, section in sections.items()},
                         {"baseline": "lexical", "improved": "bm25"})
        self.assertEqual(sections["baseline"]["vault_snapshot"], sections["improved"]["vault_snapshot"])
        self.assertEqual(list(report["improved"])[-2:], [SECTION, "optional_gates"])
        self.assertEqual(list(report["baseline"])[-1], SECTION)

    def test_input_report_is_not_mutated_and_inputs_are_checked(self):
        report = evaluator.evaluate(self.fixture)
        original = copy.deepcopy(report)
        extended = evaluator.with_conformal_abstention(report, self.fixture, self.split)
        self.assertEqual(report, original)
        self.assertEqual(without_section(extended), report)
        self.assertEqual(extended[SECTION]["calibration"]["n"], 19)
        for invalid in ([], {}, {"baseline": {}}):
            with self.subTest(report=invalid), self.assertRaises(ValueError):
                evaluator.with_conformal_abstention(invalid, self.fixture, self.split)
        for options in ({"false_refusal_budget": 0.0}, {"false_refusal_budget": 1}, {"delta": 1.0},
                        {"delta": float("nan")}, {"signal": "rrf"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                evaluator.with_conformal_abstention(report, self.fixture, self.split, **options)
        for split in ({"dev": ["CALID00"]}, {"dev": ["CALID00"], "heldout": ["CALID00"]},
                      {"dev": [1], "heldout": []}, []):
            with self.subTest(split=split), self.assertRaises(ValueError):
                evaluator.with_conformal_abstention(report, self.fixture, split)
        # delta is a library parameter: a smaller delta never passes more thresholds.
        loose = evaluator.with_conformal_abstention(report, self.fixture, self.split, false_refusal_budget=0.5,
                                                    delta=0.5)[SECTION]["calibration_conditional"]
        self.assertEqual(loose["learn_then_test"]["result"], "threshold")


def arm_signal(method: str, *options: dict, signal=None, name=None) -> str:
    rows = [{"retrieval_options": dict(value)} for value in options]
    return evaluator._conformal_arm_signal(name, {"method": method}, rows, signal)


class SignalChoiceTests(unittest.TestCase):
    def test_defaults_follow_the_row_kind(self):
        hangul2 = {"backend": "bm25", "lexical_ngrams": "hangul2"}
        self.assertEqual(arm_signal("legacy", {}), "lexical")
        self.assertEqual(arm_signal("advanced", {}, {"backend": "lexical", "lexical_ngrams": "hangul2"}), "lexical")
        self.assertEqual(arm_signal("render_matched", {"backend": "hybrid"},
                                    {"backend": "hybrid", "lexical_ngrams": "hangul2"}), "lexical")
        self.assertEqual(arm_signal("advanced", {"backend": "bm25"}), "bm25")
        # Candidates found only by the bigram rank have a word bm25 of 0, so that arm defaults to its own score.
        self.assertEqual(arm_signal("advanced", hangul2), "bm25_hangul2")
        self.assertEqual(arm_signal("advanced", hangul2, signal="bm25"), "bm25")
        self.assertEqual(arm_signal("advanced", signal="bm25"), "bm25")  # no row to calibrate or test

    def test_a_named_signal_must_fit_every_row_kind(self):
        cases = (
            (("legacy", {}), "bm25", "baseline",
             "--abstention-signal bm25 does not fit the baseline arm: legacy rows allow lexical"),
            (("advanced", {}), "bm25_hangul2", None,
             "--abstention-signal bm25_hangul2 does not fit the evaluated arm: lexical rows allow lexical"),
            (("advanced", {"backend": "hybrid"}), "bm25", "improved",
             "--abstention-signal bm25 does not fit the improved arm: hybrid rows allow lexical"),
            (("advanced", {"backend": "bm25"}), "lexical", None,
             "--abstention-signal lexical does not fit the evaluated arm: bm25 rows allow bm25"),
            (("advanced", {"backend": "bm25", "lexical_ngrams": "hangul2"}), "lexical", None,
             "--abstention-signal lexical does not fit the evaluated arm: bm25+hangul2 rows allow bm25_hangul2 or "
             "bm25"),
        )
        for (method, options), signal, name, message in cases:
            with self.subTest(signal=signal, method=method), self.assertRaises(evaluator.CalibrationError) as caught:
                arm_signal(method, options, signal=signal, name=name)
            self.assertEqual(str(caught.exception), message)
        self.assertTrue(issubclass(evaluator.CalibrationError, evaluator.GoldSetError))

    def test_one_arm_cannot_mix_signal_kinds(self):
        with self.assertRaises(evaluator.CalibrationError) as caught:
            arm_signal("advanced", {}, {"backend": "bm25"}, {"backend": "hybrid"})
        self.assertEqual(str(caught.exception), "--abstention-calibration needs one signal per arm, but the evaluated "
                                                "arm has bm25 and hybrid and lexical rows; give --backend")
        # lexical and hybrid rows share their default, so they mix.
        self.assertEqual(arm_signal("advanced", {}, {"backend": "hybrid"}), "lexical")
        with self.assertRaises(evaluator.CalibrationError) as caught:
            arm_signal("advanced", {"backend": "other"})
        self.assertEqual(str(caught.exception),
                         "the evaluated arm has rows with an invalid backend option, which no signal fits")


class SignalValidationTests(SyntheticCase):
    def test_a_named_signal_that_does_not_fit_exits_2(self):
        code, stdout, stderr = run_main(["--fixture", self.fixture, *LOOSE, *FLAG, "--compare", "--backend", "bm25",
                                         "--abstention-signal", "bm25"])
        self.assertEqual((code, stdout, stderr.strip()),
                         (2, "", "recall evaluation unavailable: --abstention-signal bm25 does not fit the baseline "
                                 "arm: legacy rows allow lexical"))

    def test_per_query_backends_decide_the_kind_unless_the_command_line_sets_one(self):
        self.queries[3]["backend"] = "bm25"
        self.save()
        code, stdout, stderr = run_main(["--fixture", self.fixture, *LOOSE, *FLAG, "--advanced"])
        self.assertEqual((code, stdout, stderr.strip()),
                         (2, "", "recall evaluation unavailable: --abstention-calibration needs one signal per arm, "
                                 "but the evaluated arm has bm25 and lexical rows; give --backend"))
        # A command-line backend overrides every row, so one signal fits again.
        code, report, stderr = self.calibrate("--advanced", "--backend", "bm25")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(report[SECTION]["signal"], "bm25")

    def test_an_invalid_per_query_backend_stays_an_evaluation_error(self):
        # retrieve() rejects the backend (status invalid_options) in the evaluation and in every second call, so the
        # rows choose no signal: the run reports them and exits 1 as without the flag, plus the section.
        self.queries[3]["backend"] = "BM25"  # dev
        self.queries[19]["backend"] = "BM25"  # heldout
        self.save()
        argv = ["--fixture", self.fixture, *LOOSE, "--advanced"]
        plain_code, plain_out, plain_err = run_main(argv)
        code, stdout, stderr = run_main([*argv, *FLAG])
        self.assertEqual(plain_code, 1)
        self.assertEqual([row["status"] for row in json.loads(plain_out)["evaluation_errors"]],
                         ["invalid_options", "invalid_options"])
        self.assertEqual(code, plain_code)
        flagged = json.loads(stdout)
        self.assertEqual(json.dumps(without_section(flagged), ensure_ascii=False, indent=2) + "\n", plain_out)
        self.assertEqual(stderr, plain_err + "conformal abstention note (non-blocking): 1 of 3 heldout twins could "
                                             "not be measured and are not counted as refusals\n")
        section = flagged[SECTION]
        self.assertEqual((section["signal"], section["status"], section["rerun_mismatch_count"]),
                         ("lexical", "no_threshold", 0))
        calibration, heldout = section["calibration"], section["heldout"]
        self.assertEqual((calibration["answerable_query_count"], calibration["error_count"], calibration["n"]),
                         (19, 1, 18))
        self.assertEqual((heldout["answerable_query_count"], heldout["error_count"],
                          heldout["twins"]["twin_error_count"]), (3, 1, 1))

    def test_option_errors_exit_2_before_retrieval(self):
        fixture = ["--fixture", self.fixture]
        cases = (
            ([*FLAG, "--split", "dev"], "--abstention-calibration cannot be combined with --split or --derive-split"),
            ([*FLAG, "--derive-split"], "--abstention-calibration cannot be combined with --split or --derive-split"),
            (["--false-refusal-budget", "0.1"],
             "--false-refusal-budget and --abstention-signal require --abstention-calibration"),
            (["--abstention-signal", "lexical"],
             "--false-refusal-budget and --abstention-signal require --abstention-calibration"),
            (["--split-file", self.fixture / "split.json"], "--split-file requires --split or --derive-split"),
        )
        with mock.patch.object(evaluator, "evaluate", side_effect=AssertionError("retrieval ran")):
            for argv, message in cases:
                with self.subTest(argv=argv):
                    code, stdout, stderr = run_main([*fixture, *argv])
                    self.assertEqual((code, stdout, stderr.strip()), (2, "", message))
            for budget in ("0", "1", "-0.1", "1.5", "nan", "inf"):
                with self.subTest(budget=budget):
                    code, stdout, stderr = run_main([*fixture, *FLAG, "--false-refusal-budget", budget])
                    self.assertEqual((code, stdout, stderr.strip()),
                                     (2, "", "--false-refusal-budget must be strictly between 0 and 1"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            evaluator._parser().parse_args(["--abstention-calibration", "marginal"])

    def test_flag_only_appends_a_section_to_every_arm(self):
        # Four arms, the twin section and both bound options; the improved arm also carries the optional gates.
        argv = ["--fixture", self.fixture, "--compare", "--expand-query", "--render-matched", "--gold-removal-twins",
                "--rate-bounds", "--success-bounds", "--max-no-answer-false-positive-rate", "0"]
        plain_code, plain_out, plain_err = run_main(argv)
        code, stdout, stderr = run_main([*argv, *FLAG])
        # The plain run's own notes (render-matched warnings, a rate-bounds note) come out unchanged.
        self.assertEqual((code, stderr), (plain_code, plain_err))
        self.assertEqual(code, 0)
        self.assertIn("rate-bounds note (non-blocking)", stderr)
        self.assertNotIn("conformal abstention note", stderr)
        flagged = json.loads(stdout)
        self.assertEqual(json.dumps(without_section(flagged), ensure_ascii=False, indent=2) + "\n", plain_out)
        self.assertNotIn(SECTION, flagged)
        for arm in ("baseline", "expansion_ablation", "improved", "render_matched"):
            with self.subTest(arm=arm):
                tail = [TWINS, SECTION, "optional_gates"] if arm == "improved" else [TWINS, SECTION]
                self.assertEqual(list(flagged[arm])[-len(tail):], tail)
                self.assertEqual(flagged[arm][SECTION]["calibration"]["n"], 19)
        self.assertEqual(flagged["improved"]["optional_gates"], json.loads(plain_out)["improved"]["optional_gates"])

    def test_gates_and_exit_code_never_read_the_section(self):
        # No query carries a stale label, so the stale top-1 gate has nothing to observe and fails (exit 1).
        argv = ["--fixture", self.fixture, "--max-stale-top-1-rate", "0"]
        plain_code, plain_out, plain_err = run_main(argv)
        code, stdout, stderr = run_main([*argv, *FLAG])
        self.assertEqual(plain_code, 1)
        self.assertEqual((code, stderr), (plain_code, plain_err))
        self.assertEqual(json.dumps(without_section(json.loads(stdout)), ensure_ascii=False, indent=2) + "\n",
                         plain_out)

    def test_a_second_call_that_differs_withholds_the_threshold(self):
        real = evaluator._conformal_rerun

        def drifted(recall_module, vault, arm, row, removed=None):
            result = real(recall_module, vault, arm, row, removed)
            if removed is None and row["id"] == "CALID05":
                result = {**result, "matches": []}
            return result

        plain_code, _, plain_err = run_main(["--fixture", self.fixture, *LOOSE])
        with mock.patch.object(evaluator, "_conformal_rerun", side_effect=drifted):
            code, report, stderr = self.calibrate()
        self.assertEqual(code, plain_code)
        section = report[SECTION]
        self.assertEqual((section["status"], section["rerun_mismatch_count"]), ("rerun_mismatch", 1))
        calibration = section["calibration"]
        self.assertEqual((calibration["n"], calibration["k"], calibration["threshold"],
                          calibration["marginal_refusal_bound"]), (19, None, None, None))
        self.assertEqual(section["calibration_conditional"]["learn_then_test"], None)
        self.assertEqual(section["heldout"]["threshold_refusal_count"], 0)
        self.assertEqual(stderr, plain_err + "conformal abstention note (non-blocking): 1 second retrieval calls did "
                                             "not reproduce the evaluated rows (did the vault change during the run?), "
                                             "so no threshold is reported\n")

    def test_twins_that_cannot_be_measured_are_never_refusals(self):
        real_scan = evaluator._twin_note_scan

        def incomplete(*args, **kwargs):
            scan = real_scan(*args, **kwargs)
            scan["summary"].update(search_complete=False, omissions=["total_byte_limit"])
            return scan

        with mock.patch.object(evaluator, "_twin_note_scan", side_effect=incomplete):
            code, report, stderr = self.calibrate()
        self.assertEqual(code, 0)
        twins = report[SECTION]["heldout"]["twins"]
        # CALID20 has no required claims, so its twin is still measured.
        self.assertEqual((twins["twin_error_count"], twins["refusal_count"]), (2, 1))
        self.assertFalse(report[SECTION]["vault_snapshot"]["complete"])
        self.assertIn("conformal abstention note (non-blocking): 2 of 3 heldout twins could not be measured", stderr)


class RedactionTests(SyntheticCase):
    MARKERS = ("CALID", "CALSCEN", "caltopic", "CALTOPIC", "gear notes", ".md", "weakword", "Topic")

    def setUp(self):
        super().setUp()
        self.key_file = self.root / "keys" / "key.bin"
        self.key_file.parent.mkdir()
        self.key_file.write_bytes(KEY)

    def test_section_holds_no_row_id_text_or_path(self):
        _, plain, _ = self.calibrate("--gold-removal-twins")
        text = json.dumps(plain[SECTION], ensure_ascii=False)
        for marker in self.MARKERS:
            self.assertNotIn(marker, text)
        self.assertIn("CALID", json.dumps(plain[TWINS]))  # the twin section does carry row ids

    def test_keyed_modes_hash_the_fingerprints_and_name_them(self):
        _, plain, _ = self.calibrate()
        code, keyed, stderr = self.calibrate("--redact-key-file", self.key_file)
        self.assertEqual(code, 0, stderr)
        section, plain_section = keyed[SECTION], plain[SECTION]
        self.assertEqual(section["vault_snapshot"]["vault_snapshot_sha256"],
                         hmac_hex(KEY, plain_section["vault_snapshot"]["vault_snapshot_sha256"]))
        self.assertEqual(section["split"]["definition_canonical_sha256"],
                         hmac_hex(KEY, plain_section["split"]["definition_canonical_sha256"]))
        self.assertIn("vault_snapshot_sha256", evaluator._FINGERPRINT_FIELDS)
        for name in ("calibration", "calibration_conditional", "heldout", "definitions", "status", "signal"):
            self.assertEqual(section[name], plain_section[name], name)
        replaced = keyed["redaction"]["replaced"]
        self.assertEqual(replaced, [*evaluator.KEYED_REDACTION_DESCRIPTION["replaced"],
                                    evaluator._KEYED_CONFORMAL_REPLACED])
        self.assertIn("vault_snapshot_sha256", replaced[-1])
        # With the twin section too, each section adds its own entry in pipeline order.
        _, both, _ = self.calibrate("--redact-ephemeral", "--gold-removal-twins")
        self.assertEqual(both["redaction"]["replaced"][-2:],
                         [evaluator._KEYED_TWIN_REPLACED, evaluator._KEYED_CONFORMAL_REPLACED])
        # Without the section the description stays the constant one.
        code, stdout, _ = run_main(["--fixture", self.fixture, *LOOSE, "--redact-key-file", self.key_file])
        self.assertEqual(json.loads(stdout)["redaction"], evaluator.KEYED_REDACTION_DESCRIPTION)

    def test_unkeyed_redaction_leaves_the_section_as_it_is(self):
        _, plain, _ = self.calibrate()
        code, redacted, stderr = self.calibrate("--redact")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(redacted[SECTION], plain[SECTION])
        self.assertEqual(redacted["redaction"], evaluator.REDACTION_DESCRIPTION)

    def test_token_row_order_leaves_the_section_alone(self):
        argv = ["--redact-key-file", self.key_file, "--gold-removal-twins"]
        _, gold, _ = self.calibrate(*argv)
        code, token, stderr = self.calibrate(*argv, "--redact-row-order", "token")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(token[SECTION], gold[SECTION])
        self.assertEqual(token["row_order"], "sorted_by_keyed_id_token")


class DefaultOutputTests(unittest.TestCase):
    def test_options_are_off_by_default_and_add_nothing(self):
        args = evaluator._parser().parse_args([])
        self.assertEqual((args.abstention_calibration, args.false_refusal_budget, args.abstention_signal),
                         (None, None, None))
        code, stdout, stderr = run_main([])
        self.assertEqual((code, stderr), (0, ""))
        self.assertNotIn(SECTION, all_keys(json.loads(stdout)))

    def test_help_names_the_limits(self):
        flat = " ".join(evaluator._parser().format_help().split())
        for phrase in ("--abstention-calibration {conformal}", "--false-refusal-budget ALPHA",
                       "--abstention-signal {lexical,bm25,bm25_hangul2}", "Marginal guarantee only",
                       "empty results are outside it", "valid for this vault snapshot only",
                       "never an engine default", "gates and the exit code never read it; it exits 2 when the split "
                       "file is missing, invalid or selects no query, or when the rows of an arm need different "
                       "signals or do not allow the given --abstention-signal",
                       "default: lexical for legacy, lexical and hybrid arms, bm25_hangul2 for bm25 arms with "
                       "--lexical-ngrams hangul2 and bm25 for other bm25 arms"):
            self.assertIn(phrase, flat)


class RecallHardCalibrationTests(unittest.TestCase):
    """Measured on recall_hard (dev 28 / heldout 28 answerable, split seed 1707) with the CI compare options
    (--compare --expand-query --query-mapping 00-meta/query-map.txt, top 3, 1,500 tokens); the 2026-10-30 table
    of docs/memory-evaluation.md."""

    @classmethod
    def setUpClass(cls):
        cls.split = evaluator.load_split(RECALL_HARD / "split.json")
        compared = evaluator.compare(RECALL_HARD, max_tokens=1500, top_k=3, expand_query=True,
                                     query_mapping="00-meta/query-map.txt")
        cls.twins = evaluator.with_gold_removal_twins(compared, RECALL_HARD)
        cls.report = evaluator.with_conformal_abstention(cls.twins, RECALL_HARD, cls.split)

    def test_sections_follow_the_twins_of_every_arm(self):
        self.assertEqual(without_section(self.report), self.twins)
        for arm in ("baseline", "expansion_ablation", "improved"):
            with self.subTest(arm=arm):
                self.assertEqual(list(self.report[arm])[-2:], [TWINS, SECTION])
        self.assertNotIn(SECTION, self.report)

    def test_ci_arms_are_pinned(self):
        observed = {arm: counts(self.report[arm][SECTION]) for arm in ("baseline", "expansion_ablation", "improved")}
        self.assertEqual(observed, {"baseline": (21, 7, 1, 17, 3, 6, 0, 13, 2),
                                    "expansion_ablation": (21, 7, 1, 17, 3, 6, 0, 13, 2),
                                    "improved": (28, 0, 1, 50, 3, 0, 0, 13, 10)})
        for arm in ("baseline", "expansion_ablation", "improved"):
            section = self.report[arm][SECTION]
            with self.subTest(arm=arm):
                self.assertEqual((section["signal"], section["status"], section["rerun_mismatch_count"]),
                                 ("lexical", "threshold", 0))
                self.assertEqual(section["calibration_conditional"]["learn_then_test"]["result"], "none")
                self.assertEqual(section["split"]["query_count_by_split"], {"dev": 32, "heldout": 32,
                                                                           "unassigned": 0})
                self.assertEqual(section["split"]["seed"], 1707)
                self.assertEqual(section["heldout"]["twins"]["twin_error_count"], 0)

    def test_n_28_at_alpha_005_gives_the_documented_numbers(self):
        section = self.report["improved"][SECTION]
        calibration, conditional = section["calibration"], section["calibration_conditional"]
        self.assertAlmostEqual(calibration["marginal_refusal_bound"], 1 / 29)
        self.assertAlmostEqual(calibration["realized_rate_above_budget_probability"], 0.95 ** 28, places=12)
        self.assertAlmostEqual(calibration["realized_rate_above_budget_probability"], 0.238, places=3)
        ltt = conditional["learn_then_test"]
        self.assertEqual([(step["threshold"], step["false_refusal_count"]) for step in ltt["tested"]], [(50, 0)])
        self.assertAlmostEqual(ltt["tested"][0]["p_value"], 0.95 ** 28, places=12)
        self.assertAlmostEqual(conditional["simes_ccv_minimum_p_value"], 1 - 0.1 ** (1 / 14), places=12)
        # The legacy arm's dev empty results alone (7 of 28) exceed alpha, so Learn then Test finds nothing.
        baseline = self.report["baseline"][SECTION]["calibration_conditional"]["learn_then_test"]
        self.assertEqual((baseline["n"], baseline["empty_result_count"], baseline["tested"][0]["false_refusal_count"]),
                         (28, 7, 7))

    def test_empty_results_and_twins_match_the_twin_section(self):
        heldout = set(json.loads((RECALL_HARD / "split.json").read_text(encoding="utf-8"))["heldout"])
        for arm in ("baseline", "expansion_ablation", "improved"):
            with self.subTest(arm=arm):
                twins, section = self.report[arm][TWINS], self.report[arm][SECTION]
                self.assertEqual(section["calibration"]["empty_result_count"]
                                 + section["heldout"]["empty_result_count"],
                                 twins["operating_point"]["answerable_false_refusal_count"])
                self.assertEqual(section["heldout"]["twins"]["empty_result_refusal_count"],
                                 sum(row["outcome"] == "refused_empty_complete" for row in twins["rows"]
                                     if row["id"] in heldout))
                self.assertEqual(section["heldout"]["twins"]["twin_count"],
                                 sum(row["id"] in heldout for row in twins["rows"]))
                self.assertEqual(section["vault_snapshot"], self.report["baseline"][SECTION]["vault_snapshot"])

    def test_larger_budget_lets_learn_then_test_pass_the_smallest_threshold_only(self):
        section = evaluator.with_conformal_abstention(self.twins["improved"], RECALL_HARD, self.split,
                                                      false_refusal_budget=0.1)[SECTION]
        self.assertEqual(counts(section), (28, 0, 2, 50, 3, 0, 0, 13, 10))
        ltt = section["calibration_conditional"]["learn_then_test"]
        self.assertEqual((ltt["result"], ltt["threshold"]), ("threshold", 50))
        self.assertAlmostEqual(ltt["tested"][0]["p_value"], 0.9 ** 28, places=12)
        self.assertEqual([step["passed"] for step in ltt["tested"]], [True, False])
        self.assertAlmostEqual(section["calibration"]["realized_rate_above_budget_probability"],
                               0.9 ** 28 + 28 * 0.1 * 0.9 ** 27, places=12)
        # At delta = 0.05 the same p = 0.052 fails (ThresholdRuleTests runs that sequence).
        self.assertGreater(ltt["tested"][0]["p_value"], 0.05)


class RecallHardBackendTests(unittest.TestCase):
    """The other backends of the 2026-10-30 table in docs/memory-evaluation.md (single advanced arm, top 3)."""

    @classmethod
    def setUpClass(cls):
        cls.split = evaluator.load_split(RECALL_HARD / "split.json")

    def section(self, report, signal=None):
        return evaluator.with_conformal_abstention(report, RECALL_HARD, self.split, signal=signal)[SECTION]

    def test_bm25_has_too_few_calibration_queries(self):
        section = self.section(evaluator.evaluate(RECALL_HARD, backend="bm25"))
        self.assertEqual((section["signal"], section["status"]), ("bm25", "no_threshold"))
        self.assertEqual(counts(section), (17, 11, 0, None, None, 12, 0, 18, 0))

    def test_hybrid_uses_the_lexical_score(self):
        section = self.section(evaluator.evaluate(RECALL_HARD, backend="hybrid"))
        self.assertEqual(section["signal"], "lexical")
        self.assertEqual(counts(section), (21, 7, 1, 17, 3, 6, 0, 13, 2))

    def test_hangul_bigram_rank_defaults_to_its_own_score(self):
        report = evaluator.evaluate(RECALL_HARD, backend="bm25", lexical_ngrams="hangul2")
        section = self.section(report)
        self.assertEqual(section["signal"], "bm25_hangul2")
        observed = counts(section)
        self.assertEqual(observed[:3] + observed[4:], (22, 6, 1, 1, 7, 0, 15, 0))
        self.assertAlmostEqual(observed[3], 1.9402, places=4)
        # The word BM25 score is 0 for candidates found only by the bigram rank: the threshold falls to 0.
        self.assertEqual(counts(self.section(report, signal="bm25")), (22, 6, 1, 0.0, 5, 7, 0, 15, 0))

    def test_hybrid_with_the_hangul_bigram_rank_keeps_the_lexical_score_and_degenerates(self):
        report = evaluator.evaluate(RECALL_HARD, backend="hybrid", lexical_ngrams="hangul2")
        section = self.section(report)
        self.assertEqual((section["signal"], section["status"]), ("lexical", "threshold"))
        # Candidates found only by the bigram rank have a lexical score of 0: t = 0 with 5 ties refuses nothing.
        self.assertEqual(counts(section), (26, 2, 1, 0, 5, 1, 0, 10, 0))
        with self.assertRaises(evaluator.CalibrationError) as caught:
            self.section(report, signal="bm25_hangul2")
        self.assertEqual(str(caught.exception),
                         "--abstention-signal bm25_hangul2 does not fit the evaluated arm: hybrid rows allow lexical")


if __name__ == "__main__":
    unittest.main()
