from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
MODULE_PATH = SCRIPT_DIR / "vault_recall.py"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "recall" / "vault"
EVALUATOR_PATH = REPO_ROOT / "scripts" / "evaluate_recall.py"


def _load_recall_module():
    if not MODULE_PATH.is_file():
        return None
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        spec = importlib.util.spec_from_file_location("agentic_vault_recall", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load recall module: {MODULE_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT_DIR))


recall_module = _load_recall_module()


class RecallModulePresenceTests(unittest.TestCase):
    def test_recall_module_exists(self) -> None:
        self.assertIsNotNone(recall_module, "vault_recall.py is missing")

    def test_checked_in_bilingual_benchmark_meets_threshold(self) -> None:
        run = subprocess.run(
            [sys.executable, str(EVALUATOR_PATH)],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertGreaterEqual(report["query_count"], 12)
        self.assertGreaterEqual(report["recall_at_3"], 0.85)
        self.assertGreaterEqual(report["mrr"], 0.75)
        self.assertEqual(report["failed_queries"], [])

    def test_evaluator_computes_multilabel_recall_and_honors_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "recall"
            shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "recall", fixture)
            query_path = fixture / "queries.json"
            corpus = json.loads(query_path.read_text(encoding="utf-8"))
            corpus["queries"][0]["expected_paths"].append("20-knowledge/missing-second-source.md")
            corpus["queries"][1]["expected_paths"] = ["20-knowledge/missing-only-source.md"]
            query_path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")

            permissive = subprocess.run(
                [
                    sys.executable, str(EVALUATOR_PATH), "--fixture", str(fixture),
                    "--min-recall-at-3", "0.90", "--min-mrr", "0.90",
                ],
                cwd=REPO_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            strict = subprocess.run(
                [
                    sys.executable, str(EVALUATOR_PATH), "--fixture", str(fixture),
                    "--min-recall-at-3", "0.95", "--min-mrr", "0.95",
                ],
                cwd=REPO_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

        self.assertEqual(permissive.returncode, 0, permissive.stderr)
        report = json.loads(permissive.stdout)
        self.assertAlmostEqual(report["recall_at_3"], 14.5 / 16)
        self.assertAlmostEqual(report["mrr"], 15 / 16)
        self.assertEqual(len(report["failed_queries"]), 2)
        self.assertEqual(report["evaluation_errors"], [])
        self.assertEqual(strict.returncode, 1)

    def test_fixture_has_competing_candidates_and_reversed_ranking_fails_evaluation(self) -> None:
        spec = importlib.util.spec_from_file_location("recall_evaluator_test", EVALUATOR_PATH)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        fixture = FIXTURE_ROOT.parent
        for item in evaluator._load_queries(fixture):
            with self.subTest(query=item["id"]):
                result = recall_module.recall(FIXTURE_ROOT, item["query"], limit=50, max_tokens=0)
                self.assertGreaterEqual(len(result["matches"]), 4)

        original_recall = recall_module.recall

        def reversed_recall(vault, query, limit=3, max_tokens=0):
            result = original_recall(vault, query, limit=50, max_tokens=max_tokens)
            result["matches"] = list(reversed(result["matches"]))[:limit]
            return result

        output = io.StringIO()
        with mock.patch.object(evaluator, "_load_recall_module", return_value=recall_module), mock.patch.object(
            recall_module, "recall", side_effect=reversed_recall
        ), redirect_stdout(output):
            exit_code = evaluator.main(["--fixture", str(fixture)])

        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertLess(report["recall_at_3"], 0.85)
        self.assertLess(report["mrr"], 0.75)
        self.assertEqual(report["evaluation_errors"], [])


@unittest.skipIf(recall_module is None, "recall module is not implemented yet")
class VaultRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        self.write_config()

    def write(self, relpath: str, text: str) -> Path:
        path = self.vault / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, relpath: str, content: bytes) -> Path:
        path = self.vault / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def write_config(self, **overrides: object) -> None:
        config: dict[str, object] = {
            "deny_zones": ["20-knowledge/_archive", "private"],
            "exclude_dirs": [".git", "node_modules", "scratch"],
        }
        config.update(overrides)
        self.write(
            "00-meta/vault-config.json",
            json.dumps(config, ensure_ascii=False),
        )

    @contextmanager
    def module_limits(self, **values: int):
        previous = {name: getattr(recall_module, name) for name in values}
        try:
            for name, value in values.items():
                setattr(recall_module, name, value)
            yield
        finally:
            for name, value in previous.items():
                setattr(recall_module, name, value)

    def test_ranks_english_source_and_attributes_actual_line(self) -> None:
        self.write(
            "20-knowledge/rollback.md",
            "---\ntitle: Deployment rollback\n---\n# Runbook\nUse a canary deployment and keep a rollback command ready.\n",
        )
        self.write(
            "20-knowledge/other.md",
            "---\ntitle: Deployment checklist\n---\nConfirm monitoring before release.\n",
        )

        result = recall_module.recall(self.vault, "deployment rollback", limit=3, max_tokens=300)

        self.assertEqual(result["matches"][0]["path"], "20-knowledge/rollback.md")
        self.assertEqual(result["matches"][0]["line"], 2)
        self.assertIn("[Source: 20-knowledge/rollback.md:2]", result["context"])
        self.assertIn("title: Deployment rollback", result["context"])

    def test_unicode_ranking_finds_korean_body_evidence(self) -> None:
        self.write("20-knowledge/incident.md", "# 운영 안내\n장애 대응은 먼저 영향을 격리하고 복구 절차를 실행한다.\n")
        self.write("20-knowledge/meeting.md", "# 회의\n다음 분기 운영 예산을 논의한다.\n")

        result = recall_module.recall(self.vault, "장애 대응 복구", limit=2, max_tokens=200)

        self.assertEqual(result["matches"][0]["path"], "20-knowledge/incident.md")
        self.assertEqual(result["matches"][0]["line"], 2)
        self.assertIn("장애 대응은 먼저 영향을 격리하고 복구 절차", result["context"])

    def test_equal_scores_are_ordered_by_normalized_relative_path(self) -> None:
        self.write("20-knowledge/zeta.md", "# Shared\nexact tie phrase\n")
        self.write("20-knowledge/Alpha.md", "# Shared\nexact tie phrase\n")

        first = recall_module.recall(self.vault, "exact tie phrase", limit=5)
        second = recall_module.recall(self.vault, "exact tie phrase", limit=5)

        expected = ["20-knowledge/Alpha.md", "20-knowledge/zeta.md"]
        self.assertEqual([m["path"] for m in first["matches"]], expected)
        self.assertEqual(first, second)

    def test_zero_matches_is_certain_only_after_complete_scan(self) -> None:
        self.write("20-knowledge/known.md", "# Known\nalpha beta gamma\n")

        result = recall_module.recall(self.vault, "unfindable-term")

        self.assertEqual(result["matches"], [])
        self.assertEqual(result["context"], "")
        self.assertTrue(result["diagnostics"]["search_complete"])
        self.assertEqual(result["diagnostics"]["omissions"], [])

    def test_deny_and_exclude_directories_never_contribute_content(self) -> None:
        self.write("20-knowledge/allowed.md", "# Allowed\nneedle public evidence\n")
        self.write("20-knowledge/_archive/secret.md", "# Secret\nneedle DENIED_MARKER\n")
        self.write("nested/scratch/secret.md", "# Scratch\nneedle EXCLUDED_MARKER\n")
        self.write("nested/private/secret.md", "# Private\nneedle PRIVATE_MARKER\n")

        result = recall_module.recall(self.vault, "needle", limit=10)

        self.assertEqual([m["path"] for m in result["matches"]], ["20-knowledge/allowed.md"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("DENIED_MARKER", serialized)
        self.assertNotIn("EXCLUDED_MARKER", serialized)
        self.assertNotIn("PRIVATE_MARKER", serialized)
        self.assertGreaterEqual(result["diagnostics"]["skipped_denied"], 2)
        self.assertGreaterEqual(result["diagnostics"]["skipped_excluded"], 1)

    def test_symlink_escape_is_skipped_without_reading_outside_marker(self) -> None:
        outside = Path(self._tmp.name) / "outside.md"
        outside.write_text("needle OUTSIDE_MARKER", encoding="utf-8")
        link = self.vault / "20-knowledge" / "escape.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"file symlinks unavailable: {exc}")

        result = recall_module.recall(self.vault, "needle", limit=5)

        self.assertNotIn("OUTSIDE_MARKER", json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["matches"], [])
        self.assertGreaterEqual(result["diagnostics"]["skipped_unsafe"], 1)
        self.assertFalse(result["diagnostics"]["search_complete"])

    def test_oversized_file_is_not_partially_read_and_is_reported(self) -> None:
        self.write_bytes("20-knowledge/large.md", b"needle " + b"x" * 200)

        with self.module_limits(MAX_FILE_BYTES=64):
            result = recall_module.recall(self.vault, "needle")

        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["skipped_oversized"], 1)
        self.assertFalse(result["diagnostics"]["search_complete"])
        self.assertIn("file_byte_limit", result["diagnostics"]["omissions"])

    def test_file_and_total_byte_limits_stop_scan_and_report_uncertainty(self) -> None:
        self.write("20-knowledge/a.md", "# A\nfirst evidence\n")
        self.write("20-knowledge/b.md", "# B\nneedle beyond file cap\n")
        with self.module_limits(MAX_FILES=1):
            file_limited = recall_module.recall(self.vault, "needle")

        self.assertEqual(file_limited["matches"], [])
        self.assertEqual(file_limited["diagnostics"]["omitted_file_limit"], 1)
        self.assertIn("file_count_limit", file_limited["diagnostics"]["omissions"])

        with self.module_limits(MAX_TOTAL_READ_BYTES=18):
            byte_limited = recall_module.recall(self.vault, "needle")

        self.assertFalse(byte_limited["diagnostics"]["search_complete"])
        self.assertGreaterEqual(byte_limited["diagnostics"]["omitted_total_byte_limit"], 1)
        self.assertIn("total_byte_limit", byte_limited["diagnostics"]["omissions"])

    def test_context_never_exceeds_complete_estimated_token_budget(self) -> None:
        self.write(
            "20-knowledge/budget.md",
            "# Budget\nrollback " + "복구 절차를 안전하게 실행한다. " * 30 + "\n",
        )

        result = recall_module.recall(self.vault, "rollback 복구", limit=5, max_tokens=28)

        self.assertLessEqual(recall_module.estimate_tokens(result["context"]), 28)
        self.assertTrue(result["context"].startswith("[Source: 20-knowledge/budget.md:2]"))
        self.assertEqual(result["diagnostics"]["context_matches"], 1)
        self.assertGreater(result["diagnostics"]["context_truncated"], 0)

        disabled = recall_module.recall(self.vault, "rollback", max_tokens=0)
        self.assertEqual(disabled["context"], "")
        self.assertEqual(disabled["diagnostics"]["context_matches"], 0)

    def test_tight_context_budget_omits_block_when_truncation_marker_cannot_fit(self) -> None:
        self.write("a.md", "needle evidence that must be visibly truncated\n")

        result = recall_module.recall(self.vault, "needle", max_tokens=5)

        self.assertLessEqual(recall_module.estimate_tokens(result["context"]), 5)
        self.assertTrue(not result["context"] or result["context"].endswith("..."))

    def test_invalid_query_options_and_config_fail_without_scanning_notes(self) -> None:
        self.write("20-knowledge/secret.md", "# Secret\nDO_NOT_SURFACE\n")

        for query, limit, max_tokens, status in (
            ("   ", 5, 100, "invalid_query"),
            ("hello", 0, 100, "invalid_options"),
            ("hello", True, 100, "invalid_options"),
            ("hello", 5, -1, "invalid_options"),
        ):
            with self.subTest(query=query, limit=limit, max_tokens=max_tokens):
                result = recall_module.recall(self.vault, query, limit=limit, max_tokens=max_tokens)
                self.assertEqual(result["diagnostics"]["status"], status)
                self.assertEqual(result["diagnostics"]["files_read"], 0)
                self.assertNotIn("DO_NOT_SURFACE", json.dumps(result))

        self.write("00-meta/vault-config.json", "{broken")
        result = recall_module.recall(self.vault, "secret")
        self.assertEqual(result["diagnostics"]["status"], "invalid_config")
        self.assertEqual(result["diagnostics"]["files_read"], 0)
        self.assertNotIn("DO_NOT_SURFACE", json.dumps(result))

    def test_deeply_nested_config_is_a_diagnostic_not_a_crash(self) -> None:
        nested = "[" * 1_200 + "0" + "]" * 1_200
        self.write("00-meta/vault-config.json", '{"extra":' + nested + "}")

        result = recall_module.recall(self.vault, "anything")

        self.assertEqual(result["diagnostics"]["status"], "invalid_config")
        self.assertEqual(result["matches"], [])

    @unittest.skipIf(os.name == "nt", "POSIX FIFO behavior")
    def test_config_fifo_is_rejected_without_blocking(self) -> None:
        config_path = self.vault / "00-meta" / "vault-config.json"
        config_path.unlink()
        os.mkfifo(config_path)

        try:
            run = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--vault", str(self.vault), "--query", "needle"],
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=1,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("recall blocked while opening a config FIFO")
        self.assertEqual(run.returncode, 2)
        self.assertIn("invalid_config", run.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX FIFO behavior")
    def test_markdown_fifo_is_skipped_without_blocking(self) -> None:
        fifo = self.vault / "20-knowledge" / "pipe.md"
        fifo.parent.mkdir(parents=True)
        os.mkfifo(fifo)

        try:
            run = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--vault", str(self.vault), "--query", "needle", "--format", "json"],
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=1,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("recall blocked while opening a Markdown FIFO")
        result = json.loads(run.stdout)
        self.assertGreaterEqual(result["diagnostics"]["skipped_unsafe"], 1)
        self.assertFalse(result["diagnostics"]["search_complete"])

    def test_text_cli_reports_invalid_and_incomplete_searches_on_stderr(self) -> None:
        self.write("00-meta/vault-config.json", "{broken")
        invalid = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--vault", str(self.vault), "--query", "needle"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("invalid_config", invalid.stderr)
        self.assertEqual(invalid.stdout, "")

        self.write_config()
        self.write_bytes("20-knowledge/large.md", b"needle " + b"x" * 200)
        command = (
            "import importlib.util, pathlib, sys; "
            f"sys.path.insert(0, {str(SCRIPT_DIR)!r}); "
            f"s=importlib.util.spec_from_file_location('recall_cli', {str(MODULE_PATH)!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "m.MAX_FILE_BYTES=64; "
            f"sys.argv=['vault_recall.py','--vault',{str(self.vault)!r},'--query','needle']; "
            "raise SystemExit(m.main())"
        )
        incomplete = subprocess.run(
            [sys.executable, "-c", command],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(incomplete.returncode, 0)
        self.assertIn("incomplete", incomplete.stderr.casefold())
        self.assertIn("file_byte_limit", incomplete.stderr)

    def test_symlink_vault_root_is_rejected(self) -> None:
        linked_root = Path(self._tmp.name) / "linked-vault"
        try:
            linked_root.symlink_to(self.vault, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        result = recall_module.recall(linked_root, "anything")

        self.assertEqual(result["diagnostics"]["status"], "invalid_vault")
        self.assertEqual(result["matches"], [])

    def test_source_changes_are_reflected_without_a_stale_index(self) -> None:
        source = self.write("20-knowledge/live.md", "# Live\nphoenix rollback procedure\n")
        first = recall_module.recall(self.vault, "phoenix")
        self.assertEqual(first["matches"][0]["path"], "20-knowledge/live.md")

        source.write_text("# Live\nretired procedure\n", encoding="utf-8")
        second = recall_module.recall(self.vault, "phoenix")

        self.assertEqual(second["matches"], [])

    def test_filename_fallback_is_not_fabricated_as_line_evidence(self) -> None:
        self.write("20-knowledge/needle-only-in-filename.md", "unrelated body text\n")

        result = recall_module.recall(self.vault, "needle filename")

        self.assertEqual(result["matches"], [])
        self.assertEqual(result["context"], "")

    def test_long_line_snippet_contains_the_lexical_evidence(self) -> None:
        self.write(
            "20-knowledge/long-line.md",
            "prefix" + " " * 700 + "needle target evidence\n",
        )

        result = recall_module.recall(self.vault, "needle target", max_tokens=500)

        self.assertEqual(result["matches"][0]["line"], 1)
        self.assertIn("needle target", result["matches"][0]["snippet"])
        self.assertIn("needle target", result["context"])

    def test_long_line_snippet_survives_nonuniform_unicode_normalization(self) -> None:
        self.write(
            "20-knowledge/normalized-line.md",
            "\ufb00" * 300 + "needle" + "x" * 500 + "\n",
        )

        result = recall_module.recall(self.vault, "needle", max_tokens=500)

        self.assertIn("needle", result["matches"][0]["snippet"])

    def test_file_growth_between_checks_is_reported_as_incomplete(self) -> None:
        note = self.write("20-knowledge/growing.md", "needle\n")
        original = recall_module._read_markdown
        changed = False

        def grow_then_read(*args, **kwargs):
            nonlocal changed
            if not changed:
                note.write_text("needle " + "x" * 200, encoding="utf-8")
                changed = True
            return original(*args, **kwargs)

        with self.module_limits(MAX_TOTAL_READ_BYTES=64), mock.patch.object(
            recall_module, "_read_markdown", side_effect=grow_then_read
        ):
            result = recall_module.recall(self.vault, "needle")

        self.assertEqual(result["matches"], [])
        self.assertFalse(result["diagnostics"]["search_complete"])
        self.assertIn("total_byte_limit", result["diagnostics"]["omissions"])

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_existing_junction_cannot_read_outside_vault(self) -> None:
        source_dir = self.vault / "20-knowledge"
        outside_dir = Path(self._tmp.name) / "outside"
        outside_dir.mkdir()
        (outside_dir / "a.md").write_text("# Outside\nneedle OUTSIDE_MARKER\n", encoding="utf-8")
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(source_dir), str(outside_dir)],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        try:
            result = recall_module.recall(self.vault, "needle")
        finally:
            os.rmdir(source_dir)

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("OUTSIDE_MARKER", serialized)
        self.assertEqual(result["matches"], [])
        self.assertFalse(result["diagnostics"]["search_complete"])
        self.assertIn("unsafe_path", result["diagnostics"]["omissions"])

    def test_cli_text_and_json_formats_match_public_result(self) -> None:
        self.write("20-knowledge/cli.md", "# CLI\ncommand palette recall\n")
        base = [sys.executable, str(MODULE_PATH), "--vault", str(self.vault), "--query", "command palette"]

        text_run = subprocess.run(base, text=True, encoding="utf-8", capture_output=True, check=False)
        json_run = subprocess.run(base + ["--format", "json"], text=True, encoding="utf-8", capture_output=True, check=False)

        self.assertEqual(text_run.returncode, 0, text_run.stderr)
        self.assertEqual(json_run.returncode, 0, json_run.stderr)
        self.assertEqual(text_run.stdout.rstrip("\n"), json.loads(json_run.stdout)["context"])
        self.assertIn("[Source: 20-knowledge/cli.md:2]", text_run.stdout)


if __name__ == "__main__":
    unittest.main()
