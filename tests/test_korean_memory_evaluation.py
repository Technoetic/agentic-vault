from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_korean_memory.py"
FIXTURE = ROOT / "tests" / "fixtures" / "korean_memory"
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location("korean_memory_evaluation_tests", SCRIPT)
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
else:
    evaluator = None


class KoreanMemoryEvaluationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(evaluator, "additive Korean memory evaluator is not implemented")
        return evaluator

    def report(self, **kwargs):
        return self.api().evaluate(**kwargs)

    def copied_fixture(self):
        self.api()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "synthetic"
        shutil.copytree(FIXTURE, path)
        return path

    def test_pack_adds_fifty_korean_cases_across_five_business_scenarios(self):
        report = self.report()
        self.assertEqual(report["case_count"], 50)
        self.assertEqual(report["by_scenario"], {
            "current": 10, "historical": 10, "conditional": 10,
            "no_answer": 10, "withdrawn": 10,
        })
        self.assertEqual(report["mode"], "synthetic_control")
        self.assertEqual(report["summary"]["answer_correct_count"], 50)
        self.assertEqual(report["summary"]["evidence_complete_count"], 50)
        self.assertEqual(report["summary"]["evidence_expected_source_hit_count"], 30)
        self.assertEqual(report["summary"]["certified_abstention_count"], 20)
        self.assertTrue(all(any("가" <= letter <= "힣" for letter in row["question"])
                            for row in report["per_case"]))

    def test_correct_evidence_does_not_make_wrong_answer_correct(self):
        report = self.report(control="wrong")
        current = report["per_case"][0]
        self.assertTrue(current["evidence"]["expected_source_hit"])
        self.assertFalse(current["answer"]["correct"])
        self.assertEqual(report["summary"]["answer_correct_count"], 20)

    def test_answerer_receives_question_public_options_and_returned_evidence_only(self):
        payloads = []

        def observer(payload):
            payloads.append(copy.deepcopy(payload))
            return self.api().synthetic_answer(payload)

        report = self.report(answerer=observer)
        self.assertEqual(report["summary"]["answer_correct_count"], 50)
        self.assertEqual(set(payloads[0]), {"question", "options", "evidence"})
        self.assertEqual(set(payloads[0]["options"]), {"entity", "attribute", "scope", "condition"})
        text = json.dumps(payloads, ensure_ascii=False)
        for secret in ("expected_assertions", "expected_paths", "allowed_assertions",
                       "forbidden_current_assertions", "withdrawn_paths", "grader", "case_id"):
            self.assertNotIn(secret, text)

    def test_synthetic_answer_depends_on_context_instead_of_reference_answers(self):
        api = self.api()
        payload = {
            "question": "가상회사 시험등대의 납기일은?",
            "options": {"entity": "시험등대", "attribute": "납기일", "scope": "current", "condition": None},
            "evidence": "[Source: 20-knowledge/sample.md]\nMEMORY_FACT " + json.dumps({
                "entity": "시험등대", "attribute": "납기일", "value": "2099-01-07",
                "scope": "current", "condition": None,
            }, ensure_ascii=False),
        }
        response = api.synthetic_answer(payload)
        self.assertEqual(response["assertions"][0]["value"], "2099-01-07")
        payload["evidence"] = ""
        self.assertEqual(api.synthetic_answer(payload), {"status": "abstain", "assertions": []})

    def test_historical_old_fact_is_allowed_but_current_old_fact_is_stale(self):
        api = self.api()
        report = self.report()
        row = report["per_case"][0]
        pack = api.load_pack()
        label = pack["labels"][row["id"]]
        response = copy.deepcopy(row["response"])
        historical = copy.deepcopy(label["allowed_assertions"][0])
        response["assertions"].append(historical)
        grade = api.grade_response(response, label, evidence_complete=True,
                                   evidence_paths=[assertion["source"] for assertion in response["assertions"]])
        self.assertTrue(grade["correct"])
        self.assertEqual(grade["stale_current_assertion_count"], 0)
        response["assertions"][-1]["scope"] = "current"
        grade = api.grade_response(response, label, evidence_complete=True,
                                   evidence_paths=[assertion["source"] for assertion in response["assertions"]])
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["stale_current_assertion_count"], 1)

    def test_conditional_fact_cannot_be_promoted_to_unconditional_fact(self):
        report = self.report(control="unconditional")
        rows = [row for row in report["per_case"] if row["scenario"] == "conditional"]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["evidence"]["expected_source_hit"] for row in rows))
        self.assertTrue(all(row["answer"]["correct"] is False for row in rows))

    def test_invalid_response_is_error_and_never_successful_abstention(self):
        report = self.report(answerer=lambda payload: {"status": "abstain", "assertions": [], "prose": "모름"})
        self.assertEqual(report["summary"]["answer_assessed_count"], 0)
        self.assertEqual(report["summary"]["execution_error_count"], 50)
        self.assertEqual(report["summary"]["certified_abstention_count"], 0)
        self.assertIsNone(report["summary"]["answer_accuracy_assessed"])
        self.assertEqual(report["summary"]["answer_accuracy_all_cases"], 0.0)
        self.assertTrue(all(row["response"] is None for row in report["per_case"]))

    def test_incomplete_retrieval_cannot_certify_abstention(self):
        fixture = self.copied_fixture()
        extra = fixture / "vault" / "20-knowledge" / "oversized.md"
        extra.write_text("모의 자료 " + "x" * 600_000, encoding="utf-8")
        report = self.report(fixture=fixture)
        self.assertEqual(report["summary"]["evidence_complete_count"], 0)
        self.assertEqual(report["summary"]["certified_abstention_count"], 0)
        self.assertEqual(report["summary"]["retrieval_error_count"], 50)
        self.assertTrue(all(row["answer"]["correct"] is None for row in report["per_case"]
                            if row["scenario"] in ("no_answer", "withdrawn")))

    def test_missing_observations_are_errors_including_no_answer_cases(self):
        report = self.report(observations={})
        self.assertEqual(report["mode"], "observation_replay")
        self.assertEqual(report["summary"]["execution_error_count"], 50)
        self.assertEqual(report["summary"]["certified_abstention_count"], 0)

    def test_replay_is_bound_to_question_evidence_corpus_and_configuration(self):
        baseline = self.report()
        observations = {row["id"]: row["observation"] for row in baseline["per_case"]}
        replay = self.report(observations=observations)
        self.assertEqual(replay["summary"]["answer_correct_count"], 50)
        for key in ("question_sha256", "evidence_sha256", "corpus_sha256", "config_sha256"):
            with self.subTest(key=key):
                changed = copy.deepcopy(observations)
                changed[baseline["per_case"][0]["id"]]["binding"][key] = "0" * 64
                report = self.report(observations=changed)
                self.assertEqual(report["summary"]["execution_error_count"], 1)
                self.assertEqual(report["summary"]["answer_assessed_count"], 49)

    def test_changed_source_and_added_withdrawal_invalidate_saved_replay(self):
        api = self.api()
        fixture = self.copied_fixture()
        baseline = self.report(fixture=fixture)
        observations = {row["id"]: row["observation"] for row in baseline["per_case"]}
        source = fixture / "vault" / baseline["per_case"][0]["evidence"]["retrieved_paths"][0]
        source.write_text(source.read_text(encoding="utf-8") + "\n추가 확인 메모\n", encoding="utf-8")
        self.assertEqual(self.report(fixture=fixture, observations=observations)["summary"]["execution_error_count"], 50)
        fresh = self.report()
        fresh_observations = {row["id"]: row["observation"] for row in fresh["per_case"]}
        path = api.load_pack()["labels"][fresh["per_case"][0]["id"]]["expected_paths"][0]
        withdrawn = self.report(observations=fresh_observations, withdrawn_sources=[path])
        self.assertEqual(withdrawn["summary"]["execution_error_count"], 50)

    def test_withdrawn_source_claim_fails_even_when_value_matches_other_labels(self):
        api = self.api()
        label = {
            "expected_paths": [], "expected_assertions": [], "allowed_assertions": [],
            "forbidden_current_assertions": [], "withdrawn_paths": ["20-knowledge/retracted.md"],
        }
        response = {"status": "answer", "assertions": [{
            "entity": "시험등대", "attribute": "승인자", "value": "노을", "scope": "historical",
            "condition": None, "source": "20-knowledge/retracted.md",
        }]}
        grade = api.grade_response(response, label, evidence_complete=True, evidence_paths=[])
        self.assertFalse(grade["correct"])
        self.assertEqual(grade["withdrawn_source_assertion_count"], 1)

    def test_no_model_usage_or_billing_is_invented_for_controls(self):
        report = self.report()
        self.assertIsNone(report["cost"]["actual_model_usage"])
        self.assertIsNone(report["cost"]["actual_model_cost"])
        self.assertGreater(report["cost"]["estimated_context_tokens"], 0)
        self.assertEqual(report["cost"]["model_request_count"], 0)
        self.assertTrue(all(row["cost"]["actual_model_usage"] is None for row in report["per_case"]))

    def test_replay_preserves_observed_usage_without_treating_estimates_as_billing(self):
        baseline = self.report()
        observations = {row["id"]: row["observation"] for row in baseline["per_case"]}
        item = observations[baseline["per_case"][0]["id"]]
        item.update(origin="actual_model", model="explicit-observed-model",
                    usage={"input_tokens": 23, "output_tokens": 7},
                    actual_cost={"amount": 0.001, "currency": "USD"})
        report = self.report(observations=observations)
        self.assertEqual(report["per_case"][0]["cost"]["actual_model_usage"], {"input_tokens": 23, "output_tokens": 7})
        self.assertEqual(report["cost"]["observed_usage_case_count"], 1)
        self.assertIsNone(report["cost"]["actual_model_usage"])
        self.assertEqual(report["cost"]["model_request_count"], 0)

    def test_response_and_replay_validation_reject_unbounded_or_nonfinite_fields(self):
        api = self.api()
        baseline = self.report()
        for response in ({"status": "abstain", "assertions": [{}]},
                         {"status": "answer", "assertions": []},
                         {"status": "answer", "assertions": [{}] * 33}, "답변"):
            with self.subTest(response=response):
                self.assertRaises(ValueError, api.validate_response, response)
        observations = {row["id"]: row["observation"] for row in baseline["per_case"]}
        observations[baseline["per_case"][0]["id"]]["usage"] = {"input_tokens": -1, "output_tokens": 3}
        self.assertEqual(self.report(observations=observations)["summary"]["execution_error_count"], 1)

    def test_budget_omitted_context_never_certifies_no_answer(self):
        report = self.report(max_tokens=0)
        self.assertEqual(report["summary"]["certified_abstention_count"], 0)
        self.assertEqual(report["summary"]["answer_assessed_count"], 0)
        self.assertEqual(report["summary"]["retrieval_error_count"], 50)

    def test_changed_policy_bytes_invalidate_replay_even_when_evidence_is_identical(self):
        fixture = self.copied_fixture()
        baseline = self.report(fixture=fixture)
        observations = {row["id"]: row["observation"] for row in baseline["per_case"]}
        policy = fixture / "vault/00-meta/vault-config.json"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        report = self.report(fixture=fixture, observations=observations)
        self.assertEqual(report["summary"]["execution_error_count"], 50)
        self.assertEqual(report["per_case"][0]["evidence"]["context"], baseline["per_case"][0]["evidence"]["context"])

    def test_stale_control_claims_old_values_as_current_and_fails_ten_cases(self):
        report = self.report(control="stale")
        self.assertEqual(report["summary"]["stale_current_assertion_count"], 10)
        self.assertEqual(report["summary"]["answer_correct_count"], 40)

    def test_cli_default_is_offline_synthetic_and_replay_is_explicit(self):
        api = self.api()
        result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "synthetic_control")
        self.assertEqual(report["cost"]["model_request_count"], 0)
        self.assertIsNone(report["cost"]["actual_model_usage"])


if __name__ == "__main__":
    unittest.main()
