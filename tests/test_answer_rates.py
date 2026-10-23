"""Opt-in answer rates for the synthetic Korean memory evaluation (--answer-rates).

Response rate, the wrong-among-answered (silent error) rate, accuracy on the
cases that should be answered and on the cases that should be abstained, the
always-answer ceiling and one-sided upper bounds. Without the flag the report
is unchanged.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANSWERABLE = ("current", "historical", "conditional")
SHOULD_ABSTAIN = ("no_answer", "withdrawn")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator = _load("answer_rates_korean_memory", ROOT / "scripts" / "evaluate_korean_memory.py")
stats = _load("answer_rates_eval_stats", ROOT / "scripts" / "eval_stats.py")


def without_timing(report: dict) -> dict:
    report = copy.deepcopy(report)
    for row in report["per_case"]:
        row["execution"].pop("retrieval_elapsed_ms", None)
    return report


def counts(rate: dict) -> tuple:
    return rate["numerator"], rate["denominator"]


def fraction(rate: dict) -> Fraction:
    return Fraction(rate["numerator"], rate["denominator"])


def always_answer(payload: dict) -> dict:
    """An external answerer that never abstains, including on cases with no answer."""
    return {"status": "answer", "assertions": [{**payload["options"], "value": "항상 답함", "source": "always.md"}]}


class AnswerRateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain = evaluator.evaluate()
        cls.good = evaluator.evaluate(answer_rates=True)
        cls.wrong = evaluator.evaluate(control="wrong", answer_rates=True)
        cls.always = evaluator.evaluate(answerer=always_answer, answer_rates=True)

    def test_flag_is_additive_and_sits_after_the_summary(self):
        self.assertNotIn("answer_rates", self.plain)
        extended = without_timing(self.good)
        rates = extended.pop("answer_rates")
        self.assertEqual(extended, without_timing(self.plain))
        keys = list(self.good)
        self.assertEqual(keys[keys.index("summary") + 1], "answer_rates")
        self.assertIn("never gates", rates["definitions"]["interpretation"])
        self.assertIn("1 - accuracy among answered", rates["definitions"]["silent_error_rate"])

    def test_good_control_answers_only_the_answerable_cases(self):
        rates = self.good["answer_rates"]
        self.assertEqual(rates["case_count"], 50)
        self.assertEqual(counts(rates["response_rate"]), (30, 50))
        self.assertEqual(counts(rates["silent_error_rate"]), (0, 30))
        self.assertEqual(counts(rates["answerable_accuracy"]), (30, 30))
        self.assertEqual(counts(rates["should_abstain_accuracy"]), (20, 20))
        self.assertEqual(counts(rates["always_answer_ceiling"]), (30, 50))
        self.assertEqual(rates["silent_error_breakdown"],
                         {"answered_should_abstain_count": 0, "answered_wrong_on_answerable_count": 0})
        self.assertEqual((rates["abstention_count"], rates["unassessed_abstention_count"],
                          rates["execution_error_count"]), (20, 0, 0))

    def test_wrong_control_is_hidden_by_the_mixed_accuracy(self):
        rates = self.wrong["answer_rates"]
        self.assertEqual(counts(rates["silent_error_rate"]), (30, 30))
        self.assertEqual(rates["silent_error_rate"]["rate"], 1.0)
        self.assertEqual(counts(rates["answerable_accuracy"]), (0, 30))
        self.assertEqual(counts(rates["should_abstain_accuracy"]), (20, 20))
        self.assertEqual(rates["silent_error_breakdown"],
                         {"answered_should_abstain_count": 0, "answered_wrong_on_answerable_count": 30})
        # The mixed accuracy alone reads 0.4 although every answer is wrong.
        self.assertEqual(self.wrong["summary"]["answer_accuracy_all_cases"], 0.4)

    def test_always_answering_turns_should_abstain_cases_into_silent_errors(self):
        rates = self.always["answer_rates"]
        self.assertEqual(counts(rates["response_rate"]), (50, 50))
        self.assertEqual(rates["abstention_count"], 0)
        self.assertEqual(rates["silent_error_breakdown"]["answered_should_abstain_count"], 20)
        self.assertEqual(counts(rates["should_abstain_accuracy"]), (0, 20))
        ceiling = fraction(rates["always_answer_ceiling"])
        self.assertLessEqual(Fraction(self.always["summary"]["answer_correct_count"], 50), ceiling)

    def test_mixed_accuracy_decomposes_exactly_into_the_two_populations(self):
        reports = [self.good, self.wrong, self.always,
                   evaluator.evaluate(control="stale", answer_rates=True),
                   evaluator.evaluate(control="unconditional", answer_rates=True)]
        for report in reports:
            rates = report["answer_rates"]
            with self.subTest(control=report["control"], mode=report["mode"]):
                ceiling = fraction(rates["always_answer_ceiling"])
                recomposed = (ceiling * fraction(rates["answerable_accuracy"])
                              + (1 - ceiling) * fraction(rates["should_abstain_accuracy"]))
                self.assertEqual(recomposed, Fraction(report["summary"]["answer_correct_count"], report["case_count"]))
                wrong, answered = counts(rates["silent_error_rate"])
                breakdown = rates["silent_error_breakdown"]
                self.assertEqual(wrong, breakdown["answered_should_abstain_count"]
                                 + breakdown["answered_wrong_on_answerable_count"])
                self.assertEqual(answered, rates["response_rate"]["numerator"])

    def test_scenarios_and_upper_bounds(self):
        rates = self.wrong["answer_rates"]
        self.assertEqual(list(rates["scenario_rates"]), sorted((*ANSWERABLE, *SHOULD_ABSTAIN)))
        for scenario in ANSWERABLE:
            row = rates["scenario_rates"][scenario]
            self.assertEqual(counts(row["accuracy"]), (0, 10))
            self.assertEqual(counts(row["silent_error_rate"]), (10, 10))
        for scenario in SHOULD_ABSTAIN:
            row = rates["scenario_rates"][scenario]
            self.assertEqual(counts(row["accuracy"]), (10, 10))
            self.assertEqual(counts(row["silent_error_rate"]), (0, 0))
            self.assertIsNone(row["silent_error_rate"]["rate"])
        self.assertEqual(rates["silent_error_rate"]["upper_bounds"], stats.rate_upper_bounds(30, 30))
        self.assertEqual(self.good["answer_rates"]["silent_error_rate"]["upper_bounds"],
                         stats.rate_upper_bounds(0, 30))
        self.assertIsNone(rates["scenario_rates"]["no_answer"]["silent_error_rate"]["upper_bounds"])

    def test_unassessed_abstentions_errors_and_replay_keep_the_identity(self):
        calls = iter(range(10 ** 6))

        def flaky(payload):
            if next(calls) % 3 == 0:
                raise RuntimeError("synthetic failure")
            return evaluator.synthetic_answer(payload)

        observations = {row["id"]: row["observation"] for row in self.good["per_case"]}
        partial = dict(list(observations.items())[7:])
        reports = {"unassessed": evaluator.evaluate(max_tokens=0, answer_rates=True),
                   "errors": evaluator.evaluate(answerer=flaky, answer_rates=True),
                   "replay": evaluator.evaluate(observations=partial, answer_rates=True)}
        for name, report in reports.items():
            rates = report["answer_rates"]
            with self.subTest(path=name):
                ceiling = fraction(rates["always_answer_ceiling"])
                recomposed = (ceiling * fraction(rates["answerable_accuracy"])
                              + (1 - ceiling) * fraction(rates["should_abstain_accuracy"]))
                self.assertEqual(recomposed, Fraction(report["summary"]["answer_correct_count"], report["case_count"]))
                self.assertEqual(rates["response_rate"]["numerator"] + rates["abstention_count"]
                                 + rates["execution_error_count"], rates["case_count"])
        unassessed = reports["unassessed"]["answer_rates"]
        self.assertEqual((unassessed["unassessed_abstention_count"], counts(unassessed["response_rate"])), (50, (0, 50)))
        self.assertEqual(counts(unassessed["should_abstain_accuracy"]), (0, 20))
        self.assertIsNone(unassessed["silent_error_rate"]["upper_bounds"])
        self.assertGreater(reports["errors"]["answer_rates"]["execution_error_count"], 0)
        self.assertEqual((reports["replay"]["mode"], reports["replay"]["answer_rates"]["execution_error_count"]),
                         ("observation_replay", 7))

    def test_invalid_flag_is_rejected(self):
        for value in ("yes", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluator.evaluate(answer_rates=value)

    def test_cli_flag_is_additive_and_keeps_the_exit_code(self):
        plain_out, flag_out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(plain_out):
            plain_code = evaluator.main(["--control", "wrong"])
        with contextlib.redirect_stdout(flag_out):
            flag_code = evaluator.main(["--control", "wrong", "--answer-rates"])
        self.assertEqual(plain_code, flag_code)
        plain, flagged = json.loads(plain_out.getvalue()), json.loads(flag_out.getvalue())
        self.assertNotIn("answer_rates", plain)
        self.assertEqual(counts(flagged["answer_rates"]["silent_error_rate"]), (30, 30))

    def test_cli_exit_code_and_exported_observations_do_not_depend_on_the_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, codes = [Path(temporary) / "plain.json", Path(temporary) / "flag.json"], []
            for extra, path in (([], paths[0]), (["--answer-rates"], paths[1])):
                with contextlib.redirect_stdout(io.StringIO()):
                    codes.append(evaluator.main(["--max-tokens", "120", "--write-observations", str(path), *extra]))
            self.assertEqual(codes, [1, 1])
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())


if __name__ == "__main__":
    unittest.main()
