"""Strict observation scoring, using hand-authored outcomes rather than cue labels."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_lesson_cue_effects.py"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def fixture():
    oracle = {"id": "independent-check", "expected_first_actions": ["inspect"],
              "acceptable_final_outcomes": ["verified"], "interruption_expected": False}
    bindings = {"task_sha256": "a" * 64, "prompt_sha256": "b" * 64,
                "model_config_sha256": "c" * 64, "host_config_sha256": "d" * 64,
                "lesson_source_sha256": "e" * 64, "compiled_lesson_sha256": "f" * 64,
                "oracle_sha256": digest(oracle)}
    scenario = {"id": "task-one", "bindings": bindings, "oracle": oracle}
    trials = []
    for condition, action, elapsed in (("baseline", "change", 120),
                                       ("hint", "inspect", 100), ("guard", "inspect", 140)):
        trials.append({"id": condition + "-run", "scenario_id": "task-one", "pair_id": "pair-one",
                       "condition": condition, "provenance": "caller_reported_actual_host",
                       "bindings": dict(bindings),
                       "cue": None if condition == "baseline" else
                           {"kind": condition, "shown_at_ms": 10, "decision_index": 1},
                       "actions": [{"action_id": action, "decision_index": 1,
                                    "attempt_index": 1, "at_ms": 20}],
                       "final_task_check": {"oracle_sha256": digest(oracle),
                                            "outcome_id": "verified", "checked_at_ms": 90},
                       "interrupted": False, "elapsed_ms": elapsed,
                       "usage": {"input_tokens": 100, "output_tokens": 30},
                       "teacher_label": "good"})
    return {"schema_version": 1, "scenarios": [scenario], "trials": trials}


class LessonCueEffectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location("lesson_cue_eval", SCRIPT)
            cls.ev = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.ev)

    def setUp(self):
        self.assertIsNotNone(self.ev, "lesson observation scorer has not been implemented")

    def report(self, pack=None):
        return self.ev.evaluate_trials(fixture() if pack is None else pack)

    def test_independent_oracle_catches_wrong_first_action_even_when_final_check_passes(self):
        report = self.report()
        baseline = report["trials"][0]
        self.assertEqual(baseline["metrics"]["first_action_correct"], False)
        self.assertEqual(baseline["metrics"]["final_task_correct"], True)
        comparison = report["pairs"][0]["comparisons"]["hint"]
        self.assertEqual(comparison["deltas"]["first_action_correct"], 1)
        self.assertEqual(comparison["deltas"]["final_task_correct"], 0)
        self.assertEqual(comparison["deltas"]["elapsed_ms"], -20)
        self.assertFalse(report["authenticity_verified"])

    def test_teacher_label_has_no_effect_and_wrong_final_outcome_moves_score(self):
        pack = fixture()
        pack["trials"][1]["teacher_label"] = "bad"
        self.assertEqual(self.report(pack)["trials"][1]["metrics"]["first_action_correct"], True)
        pack["trials"][1]["final_task_check"]["outcome_id"] = "unverified"
        self.assertEqual(self.report(pack)["pairs"][0]["comparisons"]["hint"]
                         ["deltas"]["final_task_correct"], -1)

    def test_missing_observations_remain_null_and_have_zero_sample_size(self):
        pack = fixture()
        for trial in pack["trials"]:
            for key in ("actions", "final_task_check", "interrupted", "elapsed_ms", "usage"):
                trial[key] = None
        report = self.report(pack)
        self.assertEqual(report["trials"][0]["observation_state"], "missing")
        self.assertTrue(all(value is None for value in report["trials"][0]["metrics"].values()))
        row = report["summary"]["caller_reported_actual_host"]["hint"]["before_first_decision"]
        self.assertEqual(row["first_action_correct"], {"n": 0, "mean_delta": None})
        self.assertIsNone(report["pairs"][0]["comparisons"]["hint"]["deltas"]["retry_count"])

    def test_omitted_metrics_are_unknown_and_partial_usage_does_not_zero_fill(self):
        pack = fixture()
        del pack["trials"][1]["final_task_check"]
        pack["trials"][1]["usage"] = {"input_tokens": None, "output_tokens": 10}
        report = self.report(pack)
        self.assertEqual(report["trials"][1]["observation_state"], "partial")
        changes = report["pairs"][0]["comparisons"]["hint"]["deltas"]
        self.assertIsNone(changes["final_task_correct"])
        self.assertIsNone(changes["input_tokens"])
        self.assertEqual(changes["output_tokens"], -20)

    def test_stale_hashes_are_distinct_and_excluded_without_losing_observations(self):
        for field in ("lesson_source_sha256", "compiled_lesson_sha256"):
            pack = fixture()
            pack["trials"][1]["bindings"][field] = "0" * 64
            report = self.report(pack)
            self.assertEqual(report["trials"][1]["status"], "stale")
            self.assertEqual(report["trials"][1]["metrics"]["first_action_correct"], True)
            self.assertEqual(report["pairs"][0]["status"], "contains_stale")
            self.assertTrue(all(v is None for v in report["pairs"][0]
                                ["comparisons"]["hint"]["deltas"].values()))

    def test_prompt_task_model_host_and_oracle_confounds_are_excluded(self):
        for field in ("task_sha256", "prompt_sha256", "model_config_sha256", "host_config_sha256",
                      "oracle_sha256"):
            pack = fixture()
            pack["trials"][1]["bindings"][field] = "0" * 64
            report = self.report(pack)
            self.assertEqual(report["trials"][1]["status"], "confounded", field)
            self.assertEqual(report["pairs"][0]["status"], "confounded", field)

    def test_unpaired_duplicate_and_mixed_provenance_never_supply_deltas(self):
        for mutation, status in (("missing", "unpaired"), ("duplicate", "confounded"),
                                 ("mixed", "confounded")):
            pack = fixture()
            if mutation == "missing":
                pack["trials"].pop()
            elif mutation == "duplicate":
                extra = copy.deepcopy(pack["trials"][1])
                extra["id"] = "another-hint"
                pack["trials"].append(extra)
            else:
                pack["trials"][1]["provenance"] = "scripted"
            report = self.report(pack)
            self.assertEqual(report["pairs"][0]["status"], status)
            self.assertIsNone(report["pairs"][0]["comparisons"]["hint"]["deltas"]
                              ["first_action_correct"])

    def test_late_cue_cannot_credit_first_decision_but_can_measure_final_outcome(self):
        pack = fixture()
        pack["trials"][1]["cue"] = {"kind": "hint", "shown_at_ms": 30, "decision_index": 2}
        pack["trials"][1]["actions"].append({"action_id": "inspect", "decision_index": 2,
                                            "attempt_index": 1, "at_ms": 40})
        report = self.report(pack)
        comparison = report["pairs"][0]["comparisons"]["hint"]
        self.assertEqual(comparison["timing"], "later_decision")
        self.assertIsNone(comparison["deltas"]["first_action_correct"])
        self.assertEqual(comparison["deltas"]["final_task_correct"], 0)

    def test_missing_cue_never_credits_injection_and_inconsistent_timing_is_invalid(self):
        pack = fixture()
        pack["trials"][1]["cue"] = None
        report = self.report(pack)
        self.assertEqual(report["pairs"][0]["comparisons"]["hint"]["timing"], "missing_cue")
        self.assertTrue(all(v is None for v in report["pairs"][0]
                            ["comparisons"]["hint"]["deltas"].values()))
        pack["trials"][1]["cue"] = {"kind": "hint", "shown_at_ms": 30, "decision_index": 1}
        self.assertEqual(self.report(pack)["trials"][1]["status"], "invalid")

    def test_action_attempts_false_interruptions_and_null_empty_sequence(self):
        pack = fixture()
        pack["trials"][1]["actions"].append({"action_id": "inspect", "decision_index": 2,
                                            "attempt_index": 2, "at_ms": 40})
        pack["trials"][1]["interrupted"] = True
        report = self.report(pack)
        metrics = report["trials"][1]["metrics"]
        self.assertEqual(metrics["retry_count"], 1)
        self.assertEqual(metrics["false_interruption"], True)
        pack["trials"][1]["actions"] = []
        self.assertIsNone(self.report(pack)["trials"][1]["metrics"]["first_action_correct"])

    def test_malformed_trial_is_distinct_from_missing_and_report_does_not_echo_action_text(self):
        pack = fixture()
        pack["trials"][1]["actions"][0]["attempt_index"] = 0
        report = self.report(pack)
        self.assertEqual(report["trials"][1]["status"], "invalid")
        self.assertEqual(report["pairs"][0]["status"], "contains_invalid")
        pack["trials"][1]["actions"] = [{"action_id": "PRIVATE_ACTION_PAYLOAD", "decision_index": 1,
                                            "attempt_index": 1, "at_ms": 20}]
        self.assertNotIn("PRIVATE_ACTION_PAYLOAD", json.dumps(self.report(pack)))

    def test_oracle_tampering_and_unsupported_schema_are_rejected(self):
        pack = fixture()
        pack["scenarios"][0]["oracle"]["acceptable_final_outcomes"] = ["wrong"]
        with self.assertRaisesRegex(ValueError, "^invalid_scenario$"):
            self.report(pack)
        pack = fixture()
        pack["schema_version"] = True
        with self.assertRaises(ValueError):
            self.report(pack)

    def test_direct_input_limits_reject_cycles_deep_oversized_and_nonfinite_values(self):
        bad_values = [float("nan"), float("inf"), "x" * 5000, 10 ** 1000, {"x": object()}]
        deep = {}
        cursor = deep
        for _ in range(30):
            cursor["x"] = {}
            cursor = cursor["x"]
        bad_values.append(deep)
        cycle = []
        cycle.append(cycle)
        bad_values.append(cycle)
        for value in bad_values:
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                self.report(value)

    def test_json_loader_rejects_duplicates_nonfinite_deep_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}',
                        '[' * 40 + '0' + ']' * 40, ' ' * (1024 * 1024 + 1), '{bad'):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.ev.load_trials(path)
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(self.ev.load_trials(path), fixture())

    def test_unsafe_paths_rejected_before_access_and_errors_are_content_free(self):
        for path in ("//host/share/private.json", r"\\?\C:\private.json", "NUL", "CON.json",
                     "some.json:stream", "https://example.test/private", "secret?.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "^unsafe_input_path$"):
                self.ev.load_trials(path)
        run = subprocess.run([sys.executable, str(SCRIPT), "--input", "PRIVATE_MISSING.json"],
                             text=True, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("PRIVATE_MISSING", run.stdout + run.stderr)
        self.assertNotIn("Traceback", run.stdout + run.stderr)

    def test_non_regular_and_symlink_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.ev.load_trials(directory)
            target = Path(directory) / "data.json"
            target.write_text(json.dumps(fixture()), encoding="utf-8")
            link = Path(directory) / "link.json"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                return
            with self.assertRaisesRegex(ValueError, "^unsafe_input_path$"):
                self.ev.load_trials(link)

    def test_hardlinked_input_and_unicode_control_keys_are_rejected_with_fixed_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "data.json"
            target.write_text(json.dumps(fixture()), encoding="utf-8")
            linked = Path(directory) / "linked.json"
            os.link(target, linked)
            with self.assertRaisesRegex(ValueError, "^unsafe_input_path$"):
                self.ev.load_trials(linked)
        for key in ("PRIVATE\nKEY", "PRIVATE\ud800KEY"):
            with self.assertRaisesRegex(ValueError, "^invalid_input$"):
                self.report({key: None})

    def test_record_hashes_audit_bindings_without_echoing_raw_observations(self):
        pack = fixture()
        report = self.report(pack)
        self.assertEqual(report["input_sha256"], digest(pack))
        self.assertEqual(report["trials"][1]["record_sha256"], digest(pack["trials"][1]))
        self.assertEqual(report["trials"][1]["bindings"], pack["trials"][1]["bindings"])

    def test_unknown_fields_invalid_attempt_order_and_stale_final_oracle_do_not_count(self):
        for mutation in ("unknown", "decision", "attempt", "final"):
            pack = fixture()
            trial = pack["trials"][1]
            if mutation == "unknown":
                trial["unexpected"] = "ignored?"
            elif mutation == "decision":
                trial["actions"][0]["decision_index"] = 2
            elif mutation == "attempt":
                trial["actions"][0]["attempt_index"] = 2
            else:
                trial["final_task_check"]["oracle_sha256"] = "0" * 64
            report = self.report(pack)
            self.assertEqual(report["trials"][1]["status"],
                             "stale" if mutation == "final" else "invalid")
            self.assertIsNone(report["pairs"][0]["comparisons"]["hint"]
                              ["deltas"]["final_task_correct"])

    def test_demo_and_cli_are_deterministic_and_explicitly_scripted(self):
        report = self.ev.evaluate_trials(self.ev.demo_pack())
        self.assertEqual(report["provenance_counts"]["caller_reported_actual_host"], 0)
        self.assertGreater(report["provenance_counts"]["scripted"], 0)
        self.assertEqual(report, self.ev.evaluate_trials(self.ev.demo_pack()))
        run = subprocess.run([sys.executable, str(SCRIPT), "--json"], capture_output=True,
                             text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), report)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path), "--json"],
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["provenance_counts"]
                             ["caller_reported_actual_host"], 3)

    def test_default_hint_models_next_decision_delivery_without_first_action_credit(self):
        report = self.ev.evaluate_trials(self.ev.demo_pack())
        for pair in report["pairs"]:
            self.assertIsNone(pair["comparisons"]["hint"]["deltas"]["first_action_correct"])

    def test_cue_after_final_outcome_and_invented_future_decision_cannot_supply_delta(self):
        for mutation in ("after_final", "future_index", "skipped_index", "wrong_interval"):
            pack = fixture()
            trial = pack["trials"][1]
            trial["cue"] = {"kind": "hint", "shown_at_ms": 30, "decision_index": 2}
            if mutation == "after_final":
                trial["cue"]["shown_at_ms"] = 95
            elif mutation == "future_index":
                trial["cue"]["decision_index"] = 999
            elif mutation == "skipped_index":
                trial["actions"].append({"action_id": "inspect", "decision_index": 3,
                                         "attempt_index": 1, "at_ms": 40})
                trial["cue"]["decision_index"] = 3
            else:
                trial["actions"].extend([
                    {"action_id": "inspect", "decision_index": 2, "attempt_index": 1, "at_ms": 40},
                    {"action_id": "inspect", "decision_index": 3, "attempt_index": 1, "at_ms": 60}])
                trial["cue"] = {"kind": "hint", "shown_at_ms": 30, "decision_index": 3}
            report = self.report(pack)
            self.assertEqual(report["trials"][1]["status"], "invalid", mutation)
            self.assertIsNone(report["pairs"][0]["comparisons"]["hint"]
                              ["deltas"]["final_task_correct"], mutation)

    def test_unobserved_action_timeline_cannot_establish_cue_availability(self):
        pack = fixture()
        pack["trials"][1]["actions"] = None
        report = self.report(pack)
        self.assertEqual(report["pairs"][0]["comparisons"]["hint"]["timing"], "unknown_timeline")
        self.assertTrue(all(value is None for value in report["pairs"][0]
                            ["comparisons"]["hint"]["deltas"].values()))

    def test_explicit_empty_input_path_refuses_without_running_demo(self):
        run = subprocess.run([sys.executable, str(SCRIPT), "--input=", "--json"],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("unsafe_input_path", run.stderr)


if __name__ == "__main__":
    unittest.main()
