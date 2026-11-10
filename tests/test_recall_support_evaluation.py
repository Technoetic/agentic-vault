"""Original synthetic fixtures exercise real retrieval, never a mocked retriever."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_recall_support.py"


class RecallSupportEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location("recall_support_eval", SCRIPT)
            cls.ev = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.ev)

    def setUp(self):
        self.assertIsNotNone(self.ev, "real recall support evaluator is missing")

    def rows(self, report):
        return {row["case_id"]: row for row in report["results"]}

    def test_complete_and_distributed_controls_have_actual_emitted_evidence(self):
        rows = self.rows(self.ev.evaluate())
        for name, ids in (("complete", ["release", "condition"]),
                          ("distributed_chain", ["identity", "condition", "release"]),
                          ("shuffled_chain", ["identity", "condition", "release"])):
            with self.subTest(name=name):
                row = rows[name]
                self.assertEqual(row["assessment"]["observed_requirement_ids"], ids)
                self.assertEqual(row["assessment"]["missing_requirement_ids"], [])
                self.assertTrue(row["claims_support"])
                self.assertGreaterEqual(row["retrieval"]["context_matches"], len(ids))

    def test_retrieval_match_cannot_stand_in_for_hidden_or_wrong_source_evidence(self):
        rows = self.rows(self.ev.evaluate())
        for name in ("lexical_distractor", "outside_snippet", "tiny_context"):
            with self.subTest(name=name):
                row = rows[name]
                self.assertGreater(row["retrieval"]["match_count"], 0)
                self.assertEqual(row["assessment"]["observed_requirement_ids"], [])
                self.assertFalse(row["claims_support"])
        self.assertEqual(rows["outside_snippet"]["source_requirement_ids"], ["release"])
        self.assertEqual(rows["tiny_context"]["retrieval"]["context_matches"], 0)
        self.assertFalse(rows["tiny_context"]["assessment"]["output_complete"])

    def test_missing_condition_and_blocker_remain_distinct(self):
        rows = self.rows(self.ev.evaluate())
        self.assertEqual(rows["missing_condition"]["assessment"]["missing_requirement_ids"], ["condition"])
        self.assertEqual(rows["missing_condition"]["assessment"]["observed_blocker_ids"], [])
        self.assertEqual(rows["blocker"]["assessment"]["missing_requirement_ids"], [])
        self.assertEqual(rows["blocker"]["assessment"]["observed_blocker_ids"], ["hold"])
        self.assertFalse(rows["blocker"]["claims_support"])
        self.assertEqual(rows["no_match"]["retrieval"]["match_count"], 0)
        self.assertTrue(rows["no_match"]["assessment"]["scope_complete"])

    def test_same_line_snippet_loss_and_render_truncation_are_measured_separately(self):
        rows = self.rows(self.ev.evaluate())
        self.assertIn("outside_snippet_tail", rows)
        self.assertIn("truncated_context", rows)
        snippet = rows["outside_snippet_tail"]
        rendered = rows["truncated_context"]
        self.assertEqual(snippet["source_requirement_ids"], ["release"])
        self.assertEqual(snippet["retrieval"]["context_truncated"], 0)
        self.assertTrue(snippet["assessment"]["output_complete"])
        self.assertEqual(snippet["assessment"]["missing_requirement_ids"], ["release"])
        self.assertEqual(rendered["retrieval"]["context_truncated"], 1)
        self.assertEqual(rendered["retrieval"]["context_omitted"], 0)
        self.assertFalse(rendered["assessment"]["output_complete"])
        self.assertEqual(rendered["assessment"]["missing_requirement_ids"], ["release"])

    def test_boundary_recipes_contain_multibyte_text_at_authored_positions(self):
        cases = {c["id"]: c for c in self.ev.load_pack()["cases"]}
        for position, line in (("start", 1), ("middle", 2), ("end", 2)):
            raw = self.ev._file_bytes(cases[f"utf8_524288_{position}"]["files"][0])
            text = raw.decode("utf-8")
            self.assertEqual(len(raw), 524288)
            self.assertLess(len(text), 200000)
            self.assertEqual(text.splitlines()[line - 1], "aurora boundary permit is violet.")

    def test_utf8_boundary_start_middle_end_is_exact_bytes_not_characters(self):
        rows = self.rows(self.ev.evaluate())
        for size, readable in ((524287, True), (524288, True), (524289, False)):
            for position in ("start", "middle", "end"):
                name = f"utf8_{size}_{position}"
                row = rows[name]
                with self.subTest(name=name):
                    self.assertEqual(row["files"][0]["bytes"], size)
                    self.assertEqual(row["assessment"]["observed_requirement_ids"], ["release"] if readable else [])
                    self.assertEqual(row["assessment"]["scope_complete"], readable)
                    self.assertEqual(row["retrieval"]["skipped_oversized"], 0 if readable else 1)
                    self.assertEqual(row["source_requirement_ids"], ["release"])

    def test_naive_any_match_control_demonstrably_gives_false_assurance(self):
        good, naive = self.ev.evaluate(), self.ev.evaluate(control="naive_any_match")
        self.assertTrue(good["passed"])
        self.assertEqual(good["summary"]["false_assurance_count"], 0)
        self.assertLess(good["summary"]["evidence_completeness"], 1)
        self.assertGreater(good["summary"]["source_present_but_unobserved_requirements"], 0)
        self.assertFalse(naive["passed"])
        self.assertGreaterEqual(naive["summary"]["false_assurance_count"], 5)
        self.assertGreater(naive["summary"]["false_assurance_rate"], 0)
        self.assertEqual(self.rows(good)["blocker"]["assessment"], self.rows(naive)["blocker"]["assessment"])

    def test_reports_are_deterministic_bound_to_case_labels_and_file_bytes(self):
        first, second = self.ev.evaluate(), self.ev.evaluate()
        self.assertEqual(first, second)
        hashes = [row["binding"]["case_sha256"] for row in first["results"]]
        self.assertEqual(len(hashes), len(set(hashes)))
        self.assertEqual(first["provenance"]["model_requests"], 0)
        self.assertEqual(first["provenance"]["retriever"], "vault_recall.recall")
        for row in first["results"]:
            for key in ("case_sha256", "expected_sha256", "contract_sha256", "context_sha256"):
                self.assertRegex(row["binding"][key], r"^[a-f0-9]{64}$")
            self.assertTrue(all(len(item["sha256"]) == 64 for item in row["files"]))

    def test_unsafe_paths_unknown_fields_duplicate_ids_and_unbounded_values_fail_closed(self):
        pack = self.ev.load_pack()
        changes = [lambda p: p.update(schema_version=True),
                   lambda p: p.update(unexpected="field"),
                   lambda p: p["cases"].append(copy.deepcopy(p["cases"][0])),
                   lambda p: p["cases"][0].update(max_tokens=True),
                   lambda p: p["cases"][0].update(max_tokens=100001),
                   lambda p: p["cases"][0].update(query="x" * 1001),
                   lambda p: p["cases"][0].update(limit=51),
                   lambda p: p["cases"][0]["files"][0].update(text="x" * 600000),
                   lambda p: p["cases"][0]["files"].append(copy.deepcopy(p["cases"][0]["files"][0])),
                   lambda p: p["labels"].pop("complete")]
        for path in ("../escape.md", "/tmp/escape.md", "C:/escape.md", "//host/x.md",
                     "folder\\escape.md", "file:stream.md", "NUL.md", "00-meta/vault-config.json"):
            changes.append(lambda p, path=path: p["cases"][0]["files"][0].update(path=path))
        for change in changes:
            changed = copy.deepcopy(pack); change(changed)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ev.evaluate(changed)

    def test_expected_labels_are_independent_and_can_expose_a_regression(self):
        pack = self.ev.load_pack()
        pack["labels"]["complete"]["observed_requirement_ids"] = ["release"]
        report = self.ev.evaluate(pack)
        self.assertFalse(report["passed"])
        self.assertFalse(self.rows(report)["complete"]["expectation_matches"])
        self.assertEqual(self.rows(report)["complete"]["assessment"]["observed_requirement_ids"], ["release", "condition"])

    def test_cli_json_default_and_naive_control_have_meaningful_exit_codes(self):
        for args, code in (([], 0), (["--format", "json"], 0), (["--control", "naive_any_match"], 1)):
            run = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                                 text=True, encoding="utf-8")
            self.assertEqual(run.returncode, code, run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(report["passed"], code == 0)
        private = "private-untrusted-" + "x" * 2000
        for args in (["--vault", private], ["--control", private], ["--format", private]):
            run = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertNotIn("private-untrusted", run.stderr)
            self.assertLess(len(run.stderr), 200)


if __name__ == "__main__":
    unittest.main()
