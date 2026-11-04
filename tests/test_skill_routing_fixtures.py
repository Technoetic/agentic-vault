"""Original synthetic routing controls: coverage, provenance labels and outcomes.

These are scripted grader checks, not model or installed-host observations.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "skill_routing"
FROZEN_CATALOG_SHA256 = "00b950a42742491970c08698d7655b405fee0282b1180210409ee4481364d92f"
FROZEN_SUITE_SHA256 = "fe794941403561633e2dc8857e8d483d43e184d892e0b72f04220457d2097d69"
sys.path.insert(0, str(ROOT / "scripts"))


def _load_contract():
    spec = importlib.util.spec_from_file_location(
        "skill_routing_fixture_contract", ROOT / "scripts" / "skill_routing_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _canonical_digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SkillRoutingFixturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = _load_contract()
        cls.catalog = _read("catalog.json")
        cls.suite = _read("cases.json")
        cls.passing = _read("control-pass.json")
        cls.faulty = _read("control-fail.json")

    def test_frozen_catalog_uses_synthetic_logical_sources(self):
        self.assertEqual(self.catalog["source_kind"], "synthetic")
        skills = self.catalog["skills"]
        self.assertEqual(len(skills), 8)
        for skill in skills:
            with self.subTest(skill=skill["id"]):
                source_record = {key: skill[key] for key in ("id", "name", "description")}
                self.assertEqual(skill["source_sha256"], _canonical_digest(source_record))
                self.assertTrue(skill["source_path"].startswith("synthetic/"))
        webapps = [skill["id"] for skill in skills if skill["name"] == "webapp"]
        self.assertEqual(webapps, ["harness14:webapp", "decoy:webapp"])

    def test_case_coverage_is_bilingual_and_declared(self):
        cases = self.suite["cases"]
        self.assertEqual(len(cases), 32)
        languages = {case["context"]["language"] for case in cases}
        self.assertEqual(languages, {"ko", "en"})
        for language in languages:
            self.assertGreaterEqual(sum(case["context"]["language"] == language
                                        for case in cases), 12)
        korean = [case for case in cases if case["context"]["language"] == "ko"]
        self.assertTrue(all(re.search(r"[가-힣]", case["prompt"]) for case in korean))
        self.assertEqual({case["context"]["in_vault"] for case in cases}, {True, False})
        self.assertEqual({case["context"]["jev_first"] for case in cases}, {True, False})
        self.assertTrue({"read_only", "implementation"}.issubset(
            {case["context"]["request_mode"] for case in cases}))
        coverage = set().union(*(set(case["context"]["coverage"]) for case in cases))
        self.assertTrue({"negative", "ambiguous", "vault", "non_vault", "read_only",
                         "implementation", "jev_authorized", "composition", "forbidden",
                         "ordering", "neutral_helper", "alternative_entrypoint",
                         "repeat", "unavailable", "missing", "error", "unexpected"}
                        .issubset(coverage))
        self.assertGreaterEqual(sum(not case["expect"]["required"] for case in cases), 6)
        self.assertGreaterEqual(sum(len(case["expect"]["required"]) > 1 for case in cases), 10)
        self.assertTrue(any(case["expect"]["before"] for case in cases))
        self.assertTrue(any(case["expect"]["allowed"] for case in cases))

    def test_both_controls_are_bound_to_complete_frozen_documents(self):
        self.assertEqual(_canonical_digest(self.catalog), FROZEN_CATALOG_SHA256)
        self.assertEqual(_canonical_digest(self.suite), FROZEN_SUITE_SHA256)
        for trace in (self.passing, self.faulty):
            with self.subTest(trace=trace["source_kind"]):
                self.assertEqual(trace["suite_sha256"], _canonical_digest(self.suite))
                self.assertEqual(trace["catalog_sha256"], _canonical_digest(self.catalog))
                self.assertEqual(trace["suite_sha256"], self.contract.digest_document(self.suite))
                self.assertEqual(trace["catalog_sha256"], self.contract.digest_document(self.catalog))
                self.assertEqual(trace["source_kind"], "scripted_control")
                self.assertEqual(trace["observation_kind"], "skill_invocation")
                self.assertEqual(trace["runtime"], {
                    "host": "scripted-control", "version": "1", "model": "none"})

    def test_passing_control_scores_all_32_cases(self):
        report = self.contract.evaluate(self.catalog, self.suite, self.passing)
        self.assertEqual(report["summary"], {
            "cases": 32, "scored": 32, "unmeasured": 0, "passed": 32, "failed": 0})
        self.assertTrue(all(case["status"] == "pass" for case in report["cases"]))
        self.assertEqual(report["source_kind"], "scripted_control")
        self.assertFalse(report["model_run_performed"])

    def test_faulty_control_has_known_failures_and_three_unmeasured_cases(self):
        report = self.contract.evaluate(self.catalog, self.suite, self.faulty)
        self.assertEqual(report["summary"], {
            "cases": 32, "scored": 29, "unmeasured": 3, "passed": 15, "failed": 14})
        cases = {case["id"]: case for case in report["cases"]}
        expected_failures = {
            "en_plain_summary", "ko_vault_recall", "en_jev_direct_inline",
            "en_webapp_review", "ko_vault_webapp_implementation", "en_helper_before_av",
            "en_forbidden_publish_review", "ko_no_harness_auto", "en_owned_name_selection",
            "en_complete_empty_negative", "ko_missing_composition", "en_wrong_entrypoint",
            "ko_reverse_order", "ko_jev_vault_review",
        }
        self.assertEqual({key for key, case in cases.items() if case["status"] == "fail"},
                         expected_failures)
        self.assertEqual({key for key, case in cases.items() if case["status"] == "unmeasured"},
                         {"en_unavailable_observation", "ko_unobserved_negative", "en_error_observation"})
        self.assertIn("helper:implementation", cases["ko_vault_recall"]["forbidden"])
        self.assertIn("helper:review", cases["en_webapp_review"]["missing_required"])
        self.assertIn("av:webapp-principles", cases["ko_missing_composition"]["missing_required"])
        self.assertTrue(cases["ko_reverse_order"]["order_violations"])
        self.assertTrue(cases["en_wrong_entrypoint"]["entrypoint_violations"])
        self.assertIn("unknown:ghost", cases["ko_jev_vault_review"]["unexpected"])
        self.assertFalse(report["model_run_performed"])

    def test_neutral_helpers_alternative_entries_repeats_and_empty_negatives_pass(self):
        report = self.contract.evaluate(self.catalog, self.suite, self.passing)
        cases = {case["id"]: case for case in report["cases"]}
        for case_id in ("en_helper_before_av", "ko_helper_between_composition",
                        "ko_alternative_entrypoints", "en_repeat_valid",
                        "en_complete_empty_negative"):
            self.assertEqual(cases[case_id]["status"], "pass", case_id)
        source = {row["case_id"]: row for row in self.passing["observations"]}
        self.assertEqual(source["en_complete_empty_negative"]["invoked"], [])
        self.assertEqual(source["en_helper_before_av"]["invoked"][0], "helper:orientation")


if __name__ == "__main__":
    unittest.main()
