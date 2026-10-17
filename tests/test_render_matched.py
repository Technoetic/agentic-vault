"""Render-matched temporal control arm (arXiv 2607.16019).

The arm keeps the advanced retrieval machinery, match fields and temporal
annotations but disables as-of/supersession exclusion, so the apparent gain can
be split into display_effect and mechanism_residual. Default behavior of the
retriever and both evaluators must stay unchanged without the opt-in flag.
"""
from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
ADVANCED_FIXTURE = ROOT / "tests" / "fixtures" / "recall_advanced"
TIMING_FIELDS = {"elapsed_ms", "total_elapsed_ms", "mean_elapsed_ms", "retrieval_elapsed_ms"}


def _load(name: str, path: Path, *, script_dir: Path | None = None):
    if script_dir is not None:
        sys.path.insert(0, str(script_dir))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if script_dir is not None:
            sys.path.remove(str(script_dir))


retrieval = _load("render_matched_retrieval", SCRIPTS / "vault_retrieval.py", script_dir=SCRIPTS)
recall_evaluator = _load("render_matched_recall_evaluator", ROOT / "scripts" / "evaluate_recall.py")
korean_evaluator = _load("render_matched_korean_evaluator", ROOT / "scripts" / "evaluate_korean_memory.py")


def without_timing(value):
    """Wall-clock fields differ between any two runs; everything else must match."""
    if isinstance(value, dict):
        return {key: without_timing(item) for key, item in value.items() if key not in TIMING_FIELDS}
    if isinstance(value, list):
        return [without_timing(item) for item in value]
    return value


def canonical(value) -> str:
    return json.dumps(without_timing(value), ensure_ascii=False, sort_keys=True)


class AnnotateOnlyRetrievalTests(unittest.TestCase):
    AS_OF = "2025-06-01"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["90-assets"], "exclude_dirs": ["scratch"]}))
        self.write("20-knowledge/old.md", '---\nvalid_from: 2024-01-01\nsuperseded_by: "[[new]]"\n---\n# Rollback\nrollback OLD [[plain]]\n')
        self.write("20-knowledge/new.md", "---\nvalid_from: 2025-01-01\n---\nrollback NEW\n")
        self.write("20-knowledge/expired.md", "---\nvalid_until: 2025-01-01\n---\nrollback EXPIRED\n")
        self.write("20-knowledge/future.md", "---\nvalid_from: 2026-01-01\n---\nrollback FUTURE\n")
        self.write("20-knowledge/invalid.md", "---\nvalid_from: tomorrow\n---\nrollback INVALID\n")
        self.write("20-knowledge/plain.md", "rollback PLAIN\n")

    def write(self, rel: str, text: str) -> None:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_retrieval(self, **options):
        return retrieval.retrieve(self.vault, "rollback", **{"limit": 10, **options})

    def test_explicit_default_filter_is_byte_identical_and_adds_no_fields(self):
        for options in ({}, {"as_of": self.AS_OF}, {"as_of": self.AS_OF, "backend": "hybrid"},
                        {"backend": "bm25"}, {"expand_links": 1}, {"as_of": self.AS_OF, "expand_links": 2}):
            with self.subTest(options=options):
                default = self.run_retrieval(**options)
                explicit = self.run_retrieval(**options, temporal_filter="exclude")
                self.assertEqual(json.dumps(default, sort_keys=True), json.dumps(explicit, sort_keys=True))
                self.assertNotIn("temporal_filter", default["diagnostics"])
                self.assertNotIn("temporal_unfiltered", default["diagnostics"])
        self.assertEqual(self.run_retrieval(), retrieval._legacy.recall(self.vault, "rollback", 10))

    def test_annotate_only_keeps_rejected_sources_with_identical_annotations_and_fields(self):
        excluded = self.run_retrieval(as_of=self.AS_OF, backend="hybrid")
        annotated = self.run_retrieval(as_of=self.AS_OF, backend="hybrid", temporal_filter="annotate_only")
        self.assertEqual({m["path"] for m in excluded["matches"]}, {"20-knowledge/new.md", "20-knowledge/plain.md"})
        statuses = {m["path"]: m["temporal"]["status"] for m in annotated["matches"]}
        self.assertEqual(statuses, {
            "20-knowledge/old.md": "superseded", "20-knowledge/expired.md": "expired",
            "20-knowledge/future.md": "not_yet_valid", "20-knowledge/invalid.md": "invalid",
            "20-knowledge/new.md": "within_explicit_bounds", "20-knowledge/plain.md": "unknown"})
        # The same annotation is computed in both arms; only exclusion differs.
        self.assertEqual(excluded["diagnostics"]["temporal"], annotated["diagnostics"]["temporal"])
        by_path = {row["path"]: row for row in excluded["diagnostics"]["temporal"]}
        for match in annotated["matches"]:
            self.assertEqual({"path": match["path"], **match["temporal"]}, by_path[match["path"]])
        self.assertEqual(excluded["diagnostics"]["temporal_rejected"], 4)
        self.assertEqual(annotated["diagnostics"]["temporal_rejected"], 0)
        self.assertEqual(annotated["diagnostics"]["temporal_unfiltered"], 4)
        self.assertEqual(annotated["diagnostics"]["temporal_filter"], "annotate_only")
        self.assertEqual({frozenset(m) for m in annotated["matches"]}, {frozenset(m) for m in excluded["matches"]})
        old = next(m for m in annotated["matches"] if m["path"] == "20-knowledge/old.md")
        self.assertEqual(old["temporal"]["supersession_source"]["path"], "20-knowledge/new.md")
        # Same renderer: label plus snippet, no temporal text is added to context.
        self.assertIn("[Source: 20-knowledge/old.md:", annotated["context"])
        self.assertNotIn("[Source: 20-knowledge/old.md:", excluded["context"])
        self.assertIn("rollback EXPIRED", annotated["context"])
        self.assertNotIn("rollback EXPIRED", excluded["context"])
        self.assertEqual(annotated["context"], retrieval._legacy._render_context(
            annotated["matches"], 1500, retrieval._legacy._diagnostics()))

    def test_annotate_only_reaches_graph_and_candidates_like_an_unfiltered_corpus(self):
        # The target never matches the query, so only a resolved link can return it.
        self.write("20-knowledge/seed.md", "rollback seed [[retired-target]]\n")
        self.write("20-knowledge/retired-target.md", "---\nvalid_until: 2025-01-01\n---\nLINK_ONLY_EVIDENCE\n")
        excluded = self.run_retrieval(as_of=self.AS_OF, expand_links=1)
        annotated = self.run_retrieval(as_of=self.AS_OF, expand_links=1, temporal_filter="annotate_only")
        self.assertNotIn("20-knowledge/retired-target.md", [m["path"] for m in excluded["matches"]])
        self.assertGreaterEqual(excluded["diagnostics"]["graph_unresolved"], 1)
        target = next(m for m in annotated["matches"] if m["path"] == "20-knowledge/retired-target.md")
        self.assertEqual(target["graph"][0]["origin_path"], "20-knowledge/seed.md")
        self.assertEqual(target["temporal"]["status"], "expired")
        self.assertIn("LINK_ONLY_EVIDENCE", annotated["context"])
        external = [{"path": "20-knowledge/future.md", "rank": 1}]
        self.assertEqual(self.run_retrieval(as_of=self.AS_OF, external_candidates=external)["diagnostics"]["external_rejected"], 1)
        self.assertEqual(self.run_retrieval(as_of=self.AS_OF, external_candidates=external,
                                            temporal_filter="annotate_only")["diagnostics"]["external_rejected"], 0)

    def test_annotate_only_without_as_of_changes_nothing_but_the_control_marker(self):
        # As with lexical_ngrams, the legacy fast path needs every option at its default, so an
        # explicit annotate_only alone selects the advanced output: same paths and context.
        legacy = self.run_retrieval()
        alone = self.run_retrieval(temporal_filter="annotate_only")
        self.assertNotIn("mode", legacy["diagnostics"])
        self.assertEqual(alone["diagnostics"]["mode"], "advanced")
        self.assertEqual([m["path"] for m in alone["matches"]], [m["path"] for m in legacy["matches"]])
        self.assertEqual(alone["context"], legacy["context"])
        for options in ({}, {"backend": "hybrid"}, {"expand_links": 1}, {"backend": "bm25"}):
            with self.subTest(options=options):
                # An explicit empty deny-scope list yields the same search's advanced output.
                default = self.run_retrieval(**(options or {"excluded_sources": []}))
                annotated = self.run_retrieval(**options, temporal_filter="annotate_only")
                self.assertEqual(annotated["diagnostics"].pop("temporal_filter"), "annotate_only")
                self.assertEqual(annotated["diagnostics"].pop("temporal_unfiltered"), 0)
                self.assertEqual(annotated, default)

    def test_invalid_temporal_filter_fails_before_note_reads(self):
        class Filter(str):
            pass

        for value in ("drop", "", "EXCLUDE", None, True, 0, ["exclude"], Filter("exclude"), Filter("annotate_only")):
            for options in ({}, {"as_of": self.AS_OF}):
                with self.subTest(value=value, options=options):
                    result = self.run_retrieval(**options, temporal_filter=value)
                    self.assertEqual(result["diagnostics"]["status"], "invalid_options")
                    self.assertEqual(result["diagnostics"]["files_read"], 0)
                    self.assertEqual(result["matches"], [])
                    self.assertNotIn("rollback", json.dumps(result))


def _row(query_id, *, ndcg=None, recall=None, stale=None, exposed=None, no_answer=None):
    """Minimal evaluate() per_query row carrying only fields the decomposition reads."""
    expectation = "no_answer" if no_answer is not None else "answerable"
    return {"id": query_id, "expectation": expectation, "ndcg_at_5": ndcg, "source_recall_at_k": recall,
            "stale_paths": ["s.md"] if stale is not None else [],
            "top_1_stale": bool(stale), "stale_retrieved_paths": ["s.md"] if exposed else [],
            "retrieved_paths": ["x.md"] if no_answer else []}


def _arm(rows, errors=()):
    return {"per_query": rows, "evaluation_errors": list(errors)}


class DecompositionArithmeticTests(unittest.TestCase):
    def arms(self, legacy, render, advanced, *, stale=((0, 0, 0),), no_answer=((0, 0, 0),), exposure=None):
        """Columns: per-arm values for 8 answerable rows, then stale and no-answer rows."""
        arms = ([], [], [])
        for index, values in enumerate(zip(legacy, render, advanced)):
            for arm, value in zip(arms, values):
                arm.append(_row(f"a{index}", ndcg=value, recall=1.0))
        for index, values in enumerate(stale):
            seen = exposure[index] if exposure else values
            for arm, value, shown in zip(arms, values, seen):
                arm.append(_row(f"s{index}", ndcg=1.0, recall=1.0, stale=value, exposed=shown))
        for index, values in enumerate(no_answer):
            for arm, value in zip(arms, values):
                arm.append(_row(f"n{index}", no_answer=value))
        return [_arm(rows) for rows in arms]

    def test_display_plus_residual_equals_apparent_and_p_values_are_the_paired_test(self):
        legacy, render, advanced = self.arms([0.25, 0.5] * 4, [0.5, 0.75] * 4, [1.0, 0.875] * 4)
        result = recall_evaluator.temporal_decomposition(legacy, render, advanced)
        ndcg = result["metrics"]["ndcg_at_5"]
        self.assertEqual(ndcg["paired_query_count"], 9)
        self.assertAlmostEqual(ndcg["display_effect"]["mean_delta"] + ndcg["mechanism_residual"]["mean_delta"],
                               ndcg["apparent_gain"]["mean_delta"], places=12)
        self.assertLess(ndcg["decomposition_error"], 1e-12)
        values = [[row["ndcg_at_5"] for row in arm["per_query"] if row["ndcg_at_5"] is not None]
                  for arm in (legacy, render, advanced)]
        self.assertEqual(ndcg["mechanism_residual"], recall_evaluator.paired_randomization(values[1], values[2]))
        self.assertEqual(ndcg["display_effect"], recall_evaluator.paired_randomization(values[0], values[1]))
        self.assertEqual(ndcg["apparent_gain"], recall_evaluator.paired_randomization(values[0], values[2]))
        self.assertAlmostEqual(ndcg["means"]["render_matched"], sum(values[1]) / 9)
        self.assertEqual(ndcg["mechanism_nonzero_pair_count"], 8)
        self.assertAlmostEqual(ndcg["mechanism_minimum_attainable_p_value"], 2 ** -7)
        self.assertTrue(ndcg["mechanism_residual_distinguishable"])
        self.assertEqual(ndcg["mechanism_direction"], "beneficial")
        self.assertEqual(result["decision"]["outcome"], "mechanism_residual_detected")
        self.assertEqual(result["decision"]["beneficial_distinguishable_metrics"], ["ndcg_at_5"])

    def test_zero_or_power_limited_residual_defers_richer_ledger_typing(self):
        legacy, render, advanced = self.arms([0.25] * 8, [1.0] * 8, [1.0] * 8,
                                             stale=((1, 1, 0), (1, 1, 0)), no_answer=((1, 1, 0),))
        result = recall_evaluator.temporal_decomposition(legacy, render, advanced)
        ndcg, stale = result["metrics"]["ndcg_at_5"], result["metrics"]["stale_top_1"]
        self.assertEqual(ndcg["mechanism_residual"]["mean_delta"], 0)
        self.assertEqual(ndcg["mechanism_residual"]["p_value"], 1)
        self.assertEqual(ndcg["mechanism_nonzero_pair_count"], 0)
        self.assertFalse(ndcg["mechanism_power_limited"])
        self.assertLess(ndcg["display_effect"]["p_value"], 0.05)
        self.assertEqual(stale["paired_query_count"], 2)
        self.assertEqual(stale["means"], {"legacy": 1.0, "render_matched": 1.0, "advanced": 0.0})
        self.assertEqual(stale["mechanism_residual"]["mean_delta"], -1.0)
        self.assertFalse(stale["higher_is_better"])
        self.assertEqual(stale["mechanism_direction"], "beneficial")
        self.assertEqual(stale["mechanism_minimum_attainable_p_value"], 0.5)
        self.assertTrue(stale["mechanism_power_limited"])
        self.assertFalse(stale["mechanism_residual_distinguishable"])
        decision = result["decision"]
        self.assertEqual(decision["outcome"], "defer_richer_ledger_typing")
        # One nonzero pair can never beat p=1; two can never beat p=0.5.
        self.assertEqual(decision["power_limited_metrics"], ["stale_top_1", "no_answer_false_positive"])
        self.assertEqual(result["metrics"]["no_answer_false_positive"]["mechanism_minimum_attainable_p_value"], 1.0)
        self.assertIn("no_answer_false_positive", [name for name, row in result["metrics"].items()
                                                   if row["paired_query_count"] == 1])
        self.assertTrue(any(w.startswith("stale_top_1: mechanism residual cannot reach") for w in result["warnings"]))

    def test_harmful_and_descriptive_residuals_never_trigger_adoption(self):
        legacy, render, advanced = self.arms([0.5] * 8, [1.0] * 8, [0.25] * 8)
        harmful = recall_evaluator.temporal_decomposition(legacy, render, advanced)
        self.assertEqual(harmful["decision"]["harmful_distinguishable_metrics"], ["ndcg_at_5"])
        self.assertEqual(harmful["decision"]["outcome"], "defer_richer_ledger_typing")
        self.assertIn("ndcg_at_5: mechanism residual is distinguishable but harmful", harmful["warnings"])
        stale = tuple((0, 0, 0) for _ in range(8))
        exposure = tuple((1, 1, 0) for _ in range(8))
        legacy, render, advanced = self.arms([1.0] * 8, [1.0] * 8, [1.0] * 8, stale=stale, exposure=exposure)
        descriptive = recall_evaluator.temporal_decomposition(legacy, render, advanced)
        self.assertFalse(descriptive["metrics"]["stale_exposure"]["decision_metric"])
        self.assertTrue(descriptive["metrics"]["stale_exposure"]["mechanism_residual_distinguishable"])
        self.assertEqual(descriptive["decision"]["descriptive_beneficial_distinguishable_metrics"], ["stale_exposure"])
        self.assertEqual(descriptive["decision"]["outcome"], "defer_richer_ledger_typing")
        self.assertNotIn("stale_exposure", descriptive["decision"]["decision_metrics"])

    def test_inputs_must_pair_identical_queries_and_alpha_is_bounded(self):
        legacy, render, advanced = self.arms([0.5] * 8, [0.5] * 8, [0.5] * 8)
        shuffled = copy.deepcopy(render)
        shuffled["per_query"].reverse()
        with self.assertRaises(ValueError):
            recall_evaluator.temporal_decomposition(legacy, shuffled, advanced)
        for alpha in (0, 1, -0.1, True, "0.05"):
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                recall_evaluator.temporal_decomposition(legacy, render, advanced, alpha=alpha)
        empty = recall_evaluator.temporal_decomposition(_arm([]), _arm([]), _arm([]))
        self.assertTrue(all(row["paired_query_count"] == 0 and row["decomposition_error"] is None
                            for row in empty["metrics"].values()))
        self.assertEqual(empty["decision"]["outcome"], "defer_richer_ledger_typing")
        errors = recall_evaluator.temporal_decomposition(legacy, _arm(render["per_query"], [{"id": "a0"}]), advanced)
        self.assertIn("render_matched arm: 1 incomplete or invalid searches", errors["warnings"])


class RenderMatchedRecallEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = recall_evaluator.compare_render_matched(ADVANCED_FIXTURE)

    def test_arm_reuses_advanced_options_and_annotations_without_exclusion(self):
        arm, improved = self.report["render_matched"], self.report["improved"]
        self.assertEqual(arm["method"], "render_matched")
        self.assertEqual(improved["method"], "advanced")
        self.assertEqual([row["id"] for row in arm["per_query"]], [row["id"] for row in improved["per_query"]])
        for row, base in zip(arm["per_query"], improved["per_query"]):
            with self.subTest(query=row["id"]):
                self.assertEqual(row["retrieval_options"], {**base["retrieval_options"], "temporal_filter": "annotate_only"})
                self.assertEqual(row["diagnostics"]["temporal"], base["diagnostics"]["temporal"])
                self.assertEqual(row["diagnostics"]["temporal_rejected"], 0)
        rows = {row["id"]: row for row in arm["per_query"]}
        self.assertEqual(rows["en-current"]["retrieved_paths"][0], "20-knowledge/phoenix-old.md")
        self.assertEqual(rows["en-retired"]["retrieved_paths"], ["20-knowledge/retired.md"])
        self.assertEqual(next(row for row in improved["per_query"] if row["id"] == "en-retired")["retrieved_paths"], [])

    def test_decomposition_means_match_arm_summaries(self):
        metrics = self.report["temporal_decomposition"]["metrics"]
        arms = {"legacy": self.report["baseline"], "render_matched": self.report["render_matched"],
                "advanced": self.report["improved"]}
        for name, arm in arms.items():
            with self.subTest(arm=name):
                self.assertAlmostEqual(metrics["ndcg_at_5"]["means"][name], arm["ndcg_at_5"], places=12)
                self.assertAlmostEqual(metrics["source_recall_at_k"]["means"][name], arm["recall_at_k"], places=12)
                self.assertAlmostEqual(metrics["stale_top_1"]["means"][name], arm["stale_sources"]["top_1_rate"], places=12)
                self.assertAlmostEqual(metrics["stale_exposure"]["means"][name], arm["stale_sources"]["exposure_rate"], places=12)
                self.assertAlmostEqual(metrics["no_answer_false_positive"]["means"][name],
                                       arm["no_answer"]["false_positive_rate"], places=12)
        self.assertTrue(all(row["decomposition_error"] < 1e-12 for row in metrics.values()))
        decision = self.report["temporal_decomposition"]["decision"]
        self.assertEqual(decision["outcome"], "defer_richer_ledger_typing")
        self.assertEqual(decision["power_limited_metrics"], ["ndcg_at_5", "stale_top_1", "no_answer_false_positive"])

    def test_without_the_flag_reports_are_unchanged(self):
        plain = recall_evaluator.compare(ADVANCED_FIXTURE)
        self.assertEqual(set(plain), {"benchmark", "interpretation", "baseline", "improved",
                                      "paired_randomization", "paired_comparison"})
        flagged = {key: value for key, value in self.report.items()
                   if key not in ("render_matched", "temporal_decomposition")}
        self.assertEqual(canonical(flagged), canonical(plain))
        self.assertEqual(canonical(recall_evaluator.evaluate(ADVANCED_FIXTURE, advanced=True, render_matched=False)),
                         canonical(recall_evaluator.evaluate(ADVANCED_FIXTURE, advanced=True)))
        original = copy.deepcopy(plain)
        extended = recall_evaluator.with_render_matched(plain, ADVANCED_FIXTURE)
        self.assertEqual(plain, original, "existing report is not mutated")
        self.assertEqual(set(extended) - set(plain), {"render_matched", "temporal_decomposition"})

    def test_cli_requires_compare_and_never_changes_the_gated_exit_code(self):
        gates = ["--max-no-answer-false-positive-rate", "0", "--max-forbidden-exposure-rate", "0",
                 "--max-stale-top-1-rate", "0"]
        for extra in (["--render-matched"], ["--render-matched", "--advanced"]):
            stderr = io.StringIO()
            with self.subTest(extra=extra), redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                self.assertEqual(recall_evaluator.main(["--fixture", str(ADVANCED_FIXTURE), *extra]), 2)
            self.assertIn("--render-matched requires --compare", stderr.getvalue())
        outputs = {}
        for flag in ([], ["--render-matched"]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = recall_evaluator.main(["--fixture", str(ADVANCED_FIXTURE), "--compare", *gates, *flag])
            outputs[bool(flag)] = (code, json.loads(stdout.getvalue()), stderr.getvalue())
        (plain_code, plain, plain_err), (flag_code, flagged, flag_err) = outputs[False], outputs[True]
        # The control arm itself would fail the stale gate (top-1 rate 1.0); gates still read improved only.
        self.assertEqual(flagged["render_matched"]["stale_sources"]["top_1_rate"], 1.0)
        self.assertEqual((plain_code, flag_code), (0, 0))
        self.assertEqual(plain_err, "")
        self.assertIn("render-matched warning (non-blocking): stale_top_1", flag_err)
        self.assertNotIn("render_matched", plain)
        self.assertEqual(canonical({k: v for k, v in flagged.items() if k not in ("render_matched", "temporal_decomposition")}),
                         canonical(plain))

    def test_invalid_programmatic_options_are_rejected(self):
        for value in ("yes", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recall_evaluator.evaluate(ADVANCED_FIXTURE, render_matched=value)
        with self.assertRaises(ValueError):
            recall_evaluator.with_render_matched({"improved": {}}, ADVANCED_FIXTURE)


class RenderMatchedKoreanEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default = korean_evaluator.evaluate()
        cls.flagged = korean_evaluator.evaluate(render_matched=True)

    def test_default_report_is_unchanged_and_flag_only_adds_its_section(self):
        self.assertNotIn("render_matched", self.default)
        rest = {key: value for key, value in self.flagged.items() if key != "render_matched"}
        self.assertEqual(canonical(rest), canonical(self.default))

    def test_evidence_only_arm_is_inert_because_no_as_of_or_rendered_annotation_exists(self):
        section = self.flagged["render_matched"]
        self.assertEqual(section["scope"], "evidence_only")
        self.assertFalse(section["answer_metrics_compared"])
        for phrase in ("never rendered", "no as_of", "deny scope", "evidence hash"):
            self.assertIn(phrase, section["answer_metrics_reason"])
        self.assertEqual((section["case_count"], section["identical_evidence_case_count"]), (50, 50))
        self.assertEqual(section["temporal_rejected_total_default"], 0)
        self.assertEqual(section["temporal_unfiltered_total_render_matched"], 0)
        counts = {name: row["mechanism_residual"]["paired_query_count"] for name, row in section["metrics"].items()}
        self.assertEqual(counts, {"expected_source_recall": 30, "withdrawn_source_exposure": 50, "evidence_complete": 50})
        for name, row in section["metrics"].items():
            with self.subTest(metric=name):
                self.assertEqual(row["mechanism_residual"]["mean_delta"], 0)
                self.assertEqual(row["mechanism_residual"]["p_value"], 1)
        self.assertEqual(section["warnings"], [])
        paths = {row["id"]: row["evidence"]["retrieved_paths"] for row in self.default["per_case"]}
        self.assertTrue(all(row["retrieved_paths"] == paths[row["id"]] for row in section["per_case"]))

    def test_answers_are_produced_once_per_case_and_never_rescored(self):
        payloads = []

        def observer(payload):
            payloads.append(set(payload))
            return korean_evaluator.synthetic_answer(payload)

        report = korean_evaluator.evaluate(answerer=observer, render_matched=True)
        self.assertEqual(len(payloads), 50)
        self.assertEqual(payloads[0], {"question", "options", "evidence"})
        self.assertEqual(report["summary"]["answer_correct_count"], 50)
        self.assertEqual(report["cost"]["model_request_count"], 0)
        with self.assertRaises(ValueError):
            korean_evaluator.evaluate(render_matched="yes")

    def test_cli_flag_is_observational(self):
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "evaluate_korean_memory.py"), "--render-matched"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["render_matched"]["identical_evidence_case_count"], 50)
        self.assertEqual(report["cost"]["model_request_count"], 0)
        self.assertNotIn("render-matched warning", run.stderr)


if __name__ == "__main__":
    unittest.main()
