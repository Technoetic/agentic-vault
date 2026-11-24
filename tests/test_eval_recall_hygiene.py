"""Unit tests for scripts/eval_recall_hygiene.py and opt-in evaluate_recall.py hygiene flags."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
HYGIENE_PATH = REPO_ROOT / "scripts" / "eval_recall_hygiene.py"
EVAL_PATH = REPO_ROOT / "scripts" / "evaluate_recall.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recall"
HARD_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "recall_hard"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SplitQueryLeakageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load(HYGIENE_PATH, "eval_recall_hygiene_test")

    def test_detects_exact_and_near_duplicate_en_and_ko_queries(self) -> None:
        dev = [
            {"id": "d1", "query": "What is the backup retention policy for production snapshots?"},
            {"id": "d2", "query": "배포 후 캐시 무효화와 열린 탭 자동 새로고침 규칙"},
            {"id": "d3", "query": "Unrelated topic about database migration locks"},
        ]
        heldout = [
            {"id": "h1", "query": "What is the backup retention policy for production snapshots?!"},
            {"id": "h2", "query": "배포 후 캐시 무효화 및 열린 탭 자동 새로고침 기준"},
            {"id": "h3", "query": "Completely separate query on holiday schedule"},
        ]
        report = self.mod.audit_split_query_leakage(dev, heldout, jaccard_threshold=0.60)
        self.assertEqual(report["schema"], self.mod.SCHEMA_LEAKAGE)
        self.assertFalse(report["passed"])
        self.assertEqual(report["exact_match_pair_count"], 1)
        self.assertGreaterEqual(report["total_flagged_pairs"], 2)
        pairs = {(row["dev_id"], row["heldout_id"]): row for row in report["pairs"]}
        self.assertIn(("d1", "h1"), pairs)
        self.assertEqual(pairs[("d1", "h1")]["match_kind"], "exact_normalized")
        self.assertIn(("d2", "h2"), pairs)
        self.assertEqual(pairs[("d2", "h2")]["match_kind"], "near_duplicate_jaccard")

    def test_clean_disjoint_split_passes(self) -> None:
        dev = [{"id": "d1", "query": "alpha beta gamma"}, {"id": "d2", "query": "???"}]
        heldout = [{"id": "h1", "query": "delta epsilon zeta"}, {"id": "h2", "query": "!!!"}]
        report = self.mod.audit_split_query_leakage(dev, heldout, jaccard_threshold=0.60)
        self.assertTrue(report["passed"])
        self.assertEqual(report["total_flagged_pairs"], 0)

    def test_checked_in_fixture_split_runs_via_cli(self) -> None:
        proc = subprocess.run(
            [
                sys.executable, str(HYGIENE_PATH), "leakage",
                "--queries", str(HARD_FIXTURE / "queries.json"),
                "--split", str(HARD_FIXTURE / "split.json"),
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schema"], self.mod.SCHEMA_LEAKAGE)
        self.assertGreater(payload["dev_count"], 0)
        self.assertGreater(payload["heldout_count"], 0)


class LayoutInvarianceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load(HYGIENE_PATH, "eval_recall_hygiene_layout_test")

    def test_perturb_markdown_layout_preserves_fences_and_multiline_frontmatter(self) -> None:
        source = (
            "---\n"
            "title: Layout Test\n"
            "type: concept\n"
            "status: active\n"
            "summary: |\n"
            "  Para 1\n"
            "\n"
            "  Para 2\n"
            "flow_tags: [\n"
            "  alpha,\n"
            "  beta\n"
            "]\n"
            "tags:\n"
            "  - alpha\n"
            "  - beta\n"
            "updated: 2026-11-24\n"
            "---\n\n"
            "# Main Heading\n"
            "Paragraph body.\n"
            "```markdown\n"
            "# Not a heading inside code fence\n"
            "```\n"
            "## Second Heading\n"
            "More body.\n"
        )
        perturbed = self.mod.perturb_markdown_layout(source, seed=1707)
        self.assertIn("summary: |\n  Para 1\n\n  Para 2\n", perturbed)
        self.assertIn("flow_tags: [\n  alpha,\n  beta\n]\n", perturbed)
        self.assertIn("tags:\n  - alpha\n  - beta", perturbed)
        self.assertIn("```markdown\n# Not a heading inside code fence\n```", perturbed)
        self.assertIn("title: Layout Test", perturbed)
        self.assertIn("status: active", perturbed)
        self.assertIn("updated: 2026-11-24", perturbed)
        self.assertEqual(perturbed, self.mod.perturb_markdown_layout(source, seed=1707))

    def test_probe_layout_invariance_does_not_modify_source_vault(self) -> None:
        vault = FIXTURE / "vault"
        queries = json.loads((FIXTURE / "queries.json").read_text(encoding="utf-8"))["queries"][:6]
        before = {
            p.relative_to(vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(vault.rglob("*")) if p.is_file()
        }
        report = self.mod.probe_layout_invariance(vault, queries, backend="bm25", top_k=3, seed=1707)
        after = {
            p.relative_to(vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(vault.rglob("*")) if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(report["schema"], self.mod.SCHEMA_LAYOUT)
        self.assertEqual(report["error_count"], 0)
        self.assertGreater(report["notes_perturbed"], 0)
        self.assertGreaterEqual(report["top1_exact_agreement"], 0.0)
        self.assertLessEqual(report["top1_exact_agreement"], 1.0)


class EvaluateRecallIntegrationTests(unittest.TestCase):
    def test_default_compare_output_unchanged_without_opt_in_flags(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(EVAL_PATH), "--compare", "--backend", "bm25"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertNotIn("paired_bootstrap", data)
        self.assertNotIn("split_query_leakage", data)
        self.assertNotIn("layout_invariance", data)

    def test_opt_in_bootstrap_leakage_layout_and_redaction(self) -> None:
        proc = subprocess.run(
            [
                sys.executable, str(EVAL_PATH), "--fixture", str(HARD_FIXTURE),
                "--compare", "--backend", "bm25",
                "--bootstrap-ci", "--check-split-leakage", "--layout-invariance",
                "--redact-ephemeral", "--redact-row-order", "token",
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("paired_bootstrap", data)
        self.assertIn("source_recall_at_k", data["paired_bootstrap"])
        self.assertIn("stratified_macro", data["paired_bootstrap"]["source_recall_at_k"])
        self.assertIn("split_query_leakage", data)
        self.assertEqual(data["split_query_leakage"]["schema"], "agentic-vault/query-leakage-audit/v1")
        self.assertIn("layout_invariance", data)
        self.assertEqual(data["layout_invariance"]["schema"], "agentic-vault/layout-invariance-probe/v1")
        self.assertEqual(data["layout_invariance"]["error_count"], 0)
        # Ensure stratum labels, leakage IDs, and drifted queries are redacted and token-sorted
        strata_keys = list(data["paired_bootstrap"]["source_recall_at_k"]["stratified_macro"]["strata"])
        self.assertTrue(all(len(k) == 64 for k in strata_keys))
        self.assertEqual(strata_keys, sorted(strata_keys))
        leaked_ids = data["split_query_leakage"]["leaked_heldout_ids"]
        self.assertTrue(all(len(qid) == 64 for qid in leaked_ids))
        self.assertEqual(leaked_ids, sorted(leaked_ids))
        pairs = data["split_query_leakage"]["pairs"]
        for pair in pairs:
            self.assertEqual(len(pair["dev_id"]), 64)
            self.assertEqual(len(pair["heldout_id"]), 64)
        self.assertEqual(pairs, sorted(pairs, key=lambda p: (-p["max_jaccard"], p["heldout_id"], p["dev_id"])))
        drifted_ids = [row["id"] for row in data["layout_invariance"]["drifted_queries"]]
        self.assertEqual(drifted_ids, sorted(drifted_ids))

    def test_bootstrap_ci_requires_compare(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(EVAL_PATH), "--bootstrap-ci"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--bootstrap-ci requires --compare", proc.stderr)


if __name__ == "__main__":
    unittest.main()
