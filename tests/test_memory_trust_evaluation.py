"""Behavioral checks for the original, offline synthetic memory trust controls."""
from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_memory_trust.py"


class MemoryTrustEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location("memory_trust_evaluation", SCRIPT)
            cls.ev = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.ev)

    def setUp(self):
        self.assertIsNotNone(self.ev, "Memory trust evaluator has not been implemented")

    def rows(self, report):
        return {row["case_id"]: row for row in report["results"]}

    def test_bounded_control_blocks_attacks_and_preserves_supported_claims(self):
        report = self.ev.evaluate()
        summary = report["summary"]
        self.assertEqual(summary["scenarios"], 9)
        self.assertEqual(summary["attack_claims"], 8)
        self.assertEqual(summary["blocked_attacks"], 8)
        self.assertEqual(summary["unsafe_supported"], 0)
        self.assertEqual(summary["preservation_claims"], 19)
        self.assertEqual(summary["preserved_claims"], 19)
        self.assertEqual(summary["errors"], 0)
        self.assertTrue(summary["selective_pass"])

    def test_repeated_json_preparations_keep_original_roles_and_hashes(self):
        rows = self.rows(self.ev.evaluate())
        for ident, role, rounds in (("role_label_en", "tool", 3),
                                    ("role_label_ko", "external", 2)):
            row = rows[ident]
            self.assertEqual(len(row["preparations"]), rounds)
            self.assertEqual(row["before"]["attack"]["status"], "rejected")
            original_hash = row["preparations"][0]["records"][1]["source_sha256"]
            for preparation in row["preparations"]:
                self.assertEqual(preparation["records"][1]["role"], role)
                self.assertEqual(preparation["records"][1]["source_sha256"], original_hash)
                self.assertEqual(preparation["omitted_records"], 0)
            self.assertEqual(row["after"]["attack"]["status"], "rejected")

    def test_vulnerable_role_control_actually_promotes_inline_labels(self):
        report = self.ev.evaluate(control="vulnerable")
        rows = self.rows(report)
        for ident, original_role in (("role_label_en", "tool"), ("role_label_ko", "external")):
            row = rows[ident]
            self.assertEqual(row["preparations"][0]["records"][1]["role"], original_role)
            self.assertEqual(row["preparations"][-1]["records"][1]["role"], "user")
            self.assertEqual(row["before"]["attack"]["status"], "rejected")
            self.assertEqual(row["after"]["attack"]["status"], "supported")
            self.assertFalse(row["correct"])
        self.assertEqual(report["summary"]["role_laundering_failures"], 2)

    def test_vulnerable_experience_control_uses_inline_success_without_host_proof(self):
        bounded = self.rows(self.ev.evaluate())
        vulnerable = self.rows(self.ev.evaluate(control="vulnerable"))
        for ident in ("fake_success_en", "fake_success_ko"):
            self.assertEqual(bounded[ident]["after"]["attack"]["status"], "rejected")
            self.assertEqual(vulnerable[ident]["before"]["attack"]["status"], "rejected")
            self.assertEqual(vulnerable[ident]["after"]["attack"]["status"], "supported")
        self.assertEqual(self.ev.evaluate(control="vulnerable")["summary"][
            "poisoned_experience_failures"], 2)

    def test_failed_stale_changed_and_unrelated_proofs_are_rejected(self):
        rows = self.rows(self.ev.evaluate())
        reasons = {"failed_observation": "observation_failed", "stale_hash": "source_hash_mismatch",
                   "changed_quote": "quote_not_found", "unrelated_observation": "observation_binding_mismatch"}
        for ident, reason in reasons.items():
            self.assertEqual(rows[ident]["after"]["attack"]["reason"], reason)
        self.assertEqual(rows["host_experience"]["after"]["host_experience"]["status"], "supported")

    def test_wipe_all_cannot_pass_or_earn_protection_for_missing_sources(self):
        summary = self.ev.evaluate(control="wipe_all")["summary"]
        self.assertEqual(summary["preserved_claims"], 0)
        self.assertEqual(summary["blocked_attacks"], 0)
        self.assertEqual(summary["incomplete"], 9)
        self.assertFalse(summary["selective_pass"])

    def test_tiny_budget_is_an_error_and_never_protection_credit(self):
        report = self.ev.evaluate(max_tokens=1)
        self.assertEqual(report["summary"]["errors"], 9)
        self.assertEqual(report["summary"]["blocked_attacks"], 0)
        self.assertEqual(report["summary"]["preserved_claims"], 0)
        self.assertFalse(report["summary"]["selective_pass"])
        for row in report["results"]:
            self.assertEqual(row["status"], "error")
            self.assertEqual(row["error_code"], "budget_too_small")
            self.assertFalse(row["correct"])

    def test_invalid_budget_is_distinct_from_valid_but_unusable_budget(self):
        report = self.ev.evaluate(max_tokens=0)
        self.assertEqual(report["summary"]["errors"], 9)
        self.assertEqual(report["summary"]["blocked_attacks"], 0)
        self.assertTrue(all(row["error_code"] == "helper_input_error" for row in report["results"]))

    def test_partial_budget_omits_whole_sources_without_claiming_selective_success(self):
        report = self.ev.evaluate(max_tokens=100)
        row = self.rows(report)["role_label_en"]
        self.assertEqual(row["status"], "incomplete")
        self.assertEqual(row["missing_sources"], ["payload"])
        self.assertEqual(row["preparations"][0]["omitted_records"], 1)
        self.assertEqual(row["blocked_attacks"], 0)
        self.assertFalse(row["correct"])
        self.assertFalse(report["summary"]["selective_pass"])

    def test_parser_failure_after_real_preparation_cannot_earn_protection(self):
        # The actual helper runs before this parser-boundary fault injection.
        with mock.patch.object(self.ev, "_decode_prepared", side_effect=self.ev.EvaluationError("parser_invalid")):
            report = self.ev.evaluate()
        self.assertEqual(report["summary"]["errors"], 9)
        self.assertEqual(report["summary"]["blocked_attacks"], 0)
        self.assertEqual(report["summary"]["preserved_claims"], 0)
        self.assertFalse(report["summary"]["selective_pass"])
        self.assertTrue(all(row["error_code"] == "parser_invalid" for row in report["results"]))

    def test_parser_and_duplicate_failures_stay_separate_from_scored_cases(self):
        report = self.ev.evaluate()
        checks = {row["id"]: row for row in report["operational_checks"]}
        for ident in ("invalid_json", "duplicate_json_key", "duplicate_source", "tiny_budget"):
            self.assertEqual(checks[ident]["status"], "expected_error")
            self.assertEqual(checks[ident]["protection_credit"], 0)
        self.assertEqual(report["summary"]["scenarios"], 9)
        for invalid in ('{"records":', '{"records":[],"records":[]}'):
            with self.assertRaises(ValueError):
                self.ev._decode_prepared(invalid)

    def test_parser_does_not_accept_boolean_versions_or_non_helper_ids(self):
        prepared = self.ev.trust.prepare_context([
            {"id": "synthetic", "role": "tool", "text": "SYNTHETIC_DATA = 17."}])
        for change in ("boolean_version", "invalid_id"):
            invalid = copy.deepcopy(prepared)
            if change == "boolean_version":
                invalid["schema_version"] = True
            else:
                invalid["records"][0]["id"] = "invalid id"
            with self.subTest(change=change), self.assertRaises(self.ev.EvaluationError):
                self.ev._decode_prepared(json.dumps(invalid))

    def test_reports_are_deterministic_and_explicitly_synthetic(self):
        first = self.ev.evaluate()
        self.assertEqual(first, self.ev.evaluate())
        provenance = first["provenance"]
        self.assertEqual(provenance["measurement"], "deterministic_synthetic_controls")
        self.assertEqual(provenance["model_requests"], 0)
        self.assertIsNone(provenance["model"])
        self.assertIsNone(provenance["actual_cost"])
        self.assertFalse(provenance["benchmark_reproduction"])
        self.assertFalse(first["authorization_granted"])
        self.assertEqual(len(provenance["fixtures_sha256"]), 64)
        self.assertEqual(len(provenance["helper_sha256"]), 64)

    def test_control_suite_checks_measured_contrast_and_cli_rejects_payload_inputs(self):
        report = self.ev.evaluate_controls()
        self.assertTrue(report["expected_contrast"])
        self.assertFalse(report["controls"]["vulnerable"]["summary"]["selective_pass"])
        process = subprocess.run([sys.executable, str(SCRIPT), "--check-controls"],
                                 cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["expected_contrast"])
        rejected = subprocess.run([sys.executable, str(SCRIPT), "--input", "arbitrary.json"],
                                  cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
