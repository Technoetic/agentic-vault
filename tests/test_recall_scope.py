"""Opt-in read budget, read priority and read-scope labels of vault recall.

Every vault here is synthetic. The checks are structural: they never depend on
the clock, on elapsed time or on the operating system's directory order.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
recall = importlib.import_module("vault_recall")
retrieval = importlib.import_module("vault_retrieval")
links = importlib.import_module("vault_links")


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("recall_scope_evaluator", ROOT / "scripts" / "evaluate_recall.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator = _load_evaluator()
MIB = 1024 * 1024
NEW_KEYS = ("omitted_paths_sample", "scope_state", "answer_state")


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.configure()

    def configure(self, block=None, **extra):
        config = {"required_keys": ["title"], "enums": {}, "deny_zones": ["90-assets", "10-inbox/_processed"],
                  "exclude_dirs": ["node_modules"], **extra}
        if block is not None:
            config["recall"] = block
        self.write("00-meta/vault-config.json", json.dumps(config))

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def fill(self, count, *, folder="10-early", body="needle evidence\n", pad=0):
        for index in range(count):
            self.write(f"{folder}/note-{index:03d}.md", body + "x" * pad + "\n")

    def paths(self, result):
        return [match["path"] for match in result["matches"]]


class DefaultConstantsTests(VaultCase):
    def test_constants_stay_at_the_documented_defaults(self):
        self.assertEqual(recall.MAX_TOTAL_READ_BYTES, 16 * MIB)
        self.assertEqual(recall.MAX_FILE_BYTES, 512 * 1024)
        self.assertEqual(links.MAX_BINDING_BYTES, 16 * MIB)

    def test_without_the_setting_limits_keep_their_shape_and_values(self):
        self.write("20-knowledge/a.md", "needle\n")
        for result in (recall.recall(self.vault, "needle"), recall.recall(self.vault, "needle", backend="bm25")):
            with self.subTest(mode=result["diagnostics"].get("mode", "legacy")):
                self.assertEqual(result["diagnostics"]["limits"], {
                    "directory_entries": recall.MAX_DIRECTORY_ENTRIES, "files": recall.MAX_FILES,
                    "file_bytes": recall.MAX_FILE_BYTES, "total_read_bytes": 16 * MIB})

    def test_module_constant_remains_the_default_source(self):
        self.fill(4, pad=100)
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 150):
            result = recall.recall(self.vault, "needle")
        self.assertEqual(result["diagnostics"]["limits"]["total_read_bytes"], 150)
        self.assertIn("total_byte_limit", result["diagnostics"]["omissions"])


class BudgetSettingTests(VaultCase):
    def test_configured_budget_replaces_the_default_in_lexical_recall(self):
        self.fill(6, pad=100)
        self.configure({"max_total_read_bytes": 1 * MIB})
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 150):
            result = recall.recall(self.vault, "needle", limit=6)
        diagnostics = result["diagnostics"]
        self.assertEqual(diagnostics["limits"]["total_read_bytes"], 1 * MIB)
        self.assertTrue(diagnostics["search_complete"])
        self.assertEqual(diagnostics["files_read"], 6)
        self.assertEqual(diagnostics["omitted_total_byte_limit"], 0)
        self.assertEqual(len(result["matches"]), 6)

    def test_retrieval_uses_the_same_value_for_reading_and_for_rechecking(self):
        self.fill(6, pad=100)
        self.configure({"max_total_read_bytes": 1 * MIB})
        for backend in ("bm25", "hybrid"):
            with self.subTest(backend=backend), mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 650):
                result = recall.recall(self.vault, "needle", limit=6, backend=backend)
                diagnostics = result["diagnostics"]
                self.assertEqual(diagnostics["limits"]["total_read_bytes"], 1 * MIB)
                self.assertEqual(diagnostics["omissions"], [])
                self.assertEqual(len(result["matches"]), 6)

    def test_query_expansion_mapping_budget_uses_the_same_value(self):
        self.fill(3, pad=100)
        self.write("00-meta/mapping.txt", "needle => thread\n")
        self.configure({"max_total_read_bytes": 1 * MIB})
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 330):
            result = recall.recall(self.vault, "needle", expand_query=True, query_mapping="00-meta/mapping.txt")
        self.assertEqual(result["diagnostics"]["status"], "ok")
        self.assertGreater(result["diagnostics"]["query_expansion"]["mapping_bytes_read"], 0)

    def test_standalone_query_expansion_honours_the_configured_budget(self):
        expansion = importlib.import_module("vault_query_expansion")
        self.fill(3, pad=100)
        self.write("00-meta/mapping.txt", "needle => thread\n")
        self.configure({"max_total_read_bytes": 1 * MIB})
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 330):
            result = expansion.expand_query(self.vault, "needle", enabled=True, mapping="00-meta/mapping.txt")
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["mapping_bytes_read"], 0)

    def test_budget_can_also_be_lowered_and_stays_a_hard_stop(self):
        self.fill(12, pad=100 * 1024)
        self.configure({"max_total_read_bytes": recall.MAX_FILE_BYTES})
        result = recall.recall(self.vault, "needle", limit=12)
        diagnostics = result["diagnostics"]
        self.assertLessEqual(diagnostics["bytes_read"], recall.MAX_FILE_BYTES)
        self.assertIn("total_byte_limit", diagnostics["omissions"])

    def test_file_limit_is_not_raised_with_the_budget(self):
        self.write("20-knowledge/big.md", "needle " + "x" * (recall.MAX_FILE_BYTES + 1))
        self.configure({"max_total_read_bytes": 64 * MIB})
        result = recall.recall(self.vault, "needle")
        diagnostics = result["diagnostics"]
        self.assertEqual(diagnostics["limits"]["file_bytes"], recall.MAX_FILE_BYTES)
        self.assertEqual(result["matches"], [])
        self.assertEqual(diagnostics["skipped_oversized"], 1)
        self.assertIn("file_byte_limit", diagnostics["omissions"])

    def test_invalid_settings_fail_with_a_fixed_code_in_every_mode(self):
        self.write("20-knowledge/secret-name.md", "needle\n")
        invalid = {
            "block_not_object": ["x"], "unknown_key": {"max_total_read_byte": 64 * MIB},
            "budget_text": {"max_total_read_bytes": "64"}, "budget_bool": {"max_total_read_bytes": True},
            "budget_float": {"max_total_read_bytes": 67108864.0}, "budget_zero": {"max_total_read_bytes": 0},
            "budget_negative": {"max_total_read_bytes": -1},
            "budget_below_one_file": {"max_total_read_bytes": recall.MAX_FILE_BYTES - 1},
            "budget_above_ceiling": {"max_total_read_bytes": recall.MAX_CONFIGURED_TOTAL_READ_BYTES + 1},
            "budget_absurd": {"max_total_read_bytes": 10 ** 30},
            "priority_not_list": {"priority_paths": "20-knowledge"},
            "priority_not_text": {"priority_paths": [7]}, "priority_empty": {"priority_paths": [""]},
            "priority_absolute": {"priority_paths": ["/etc"]}, "priority_drive": {"priority_paths": ["C:/x"]},
            "priority_parent": {"priority_paths": ["../outside"]},
            "priority_too_many": {"priority_paths": [f"a{index}" for index in range(recall.MAX_PRIORITY_PATHS + 1)]},
            "priority_too_long": {"priority_paths": ["a" * (recall.MAX_PRIORITY_PATH_CHARS + 1)]},
            "priority_denied": {"priority_paths": ["10-inbox/_processed/secret.md"]},
            "priority_denied_anywhere": {"priority_paths": ["20-knowledge/90-assets"]},
            "priority_excluded": {"priority_paths": ["node_modules"]},
            "priority_git": {"priority_paths": [".git/config"]},
        }
        for label, block in invalid.items():
            self.configure(block)
            for options in ({}, {"backend": "bm25"}):
                with self.subTest(label=label, options=options):
                    result = recall.recall(self.vault, "needle", **options)
                    diagnostics = result["diagnostics"]
                    self.assertEqual(diagnostics["status"], "invalid_config")
                    self.assertIn("recall_config_invalid", diagnostics["omissions"])
                    self.assertEqual(result["matches"], [])
                    self.assertEqual(diagnostics["omitted_paths_sample"], [])
                    self.assertEqual(diagnostics["answer_state"], "not_enough_scope")
                    self.assertNotIn("secret", json.dumps(diagnostics))


class ScopeLabelTests(VaultCase):
    def test_complete_search_with_results(self):
        self.write("20-knowledge/a.md", "needle\n")
        diagnostics = recall.recall(self.vault, "needle")["diagnostics"]
        self.assertTrue(diagnostics["search_complete"])
        self.assertEqual((diagnostics["scope_state"], diagnostics["answer_state"]), ("complete", "matches"))
        self.assertEqual(diagnostics["omitted_paths_sample"], [])

    def test_complete_search_without_results_is_the_same_fact_as_complete_and_empty(self):
        self.write("20-knowledge/a.md", "other\n")
        result = recall.recall(self.vault, "needle")
        self.assertEqual(result["matches"], [])
        self.assertTrue(result["diagnostics"]["search_complete"])
        self.assertEqual(result["diagnostics"]["answer_state"], "no_match_in_scope")

    def test_partial_search_with_results(self):
        self.write("20-knowledge/a.md", "needle\n" + "x" * 80 + "\n")
        self.write("20-knowledge/z.md", "needle\n" + "x" * 80 + "\n")
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 100):
            diagnostics = recall.recall(self.vault, "needle")["diagnostics"]
        self.assertFalse(diagnostics["search_complete"])
        self.assertEqual((diagnostics["scope_state"], diagnostics["answer_state"]), ("partial", "partial_matches"))
        self.assertEqual(diagnostics["omitted_paths_sample"], ["20-knowledge/z.md"])

    def test_partial_search_without_results_is_not_a_negative_answer(self):
        self.write("20-knowledge/a.md", "other\n" + "x" * 80 + "\n")
        self.write("20-knowledge/z.md", "needle\n" + "x" * 80 + "\n")
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 100):
            result = recall.recall(self.vault, "needle")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["scope_state"], "partial")
        self.assertEqual(result["diagnostics"]["answer_state"], "not_enough_scope")

    def test_labels_match_between_legacy_and_advanced_paths(self):
        self.write("20-knowledge/a.md", "needle\n")
        for backend in ("bm25", "hybrid"):
            with self.subTest(backend=backend):
                diagnostics = recall.recall(self.vault, "needle", backend=backend)["diagnostics"]
                self.assertEqual((diagnostics["scope_state"], diagnostics["answer_state"]), ("complete", "matches"))

    def test_early_failures_still_carry_the_labels(self):
        for result in (recall.recall(self.vault, "!!!"), recall.recall(self.vault, "!!!", backend="bm25"),
                       recall.recall(self.vault, "needle", limit=0)):
            diagnostics = result["diagnostics"]
            self.assertEqual((diagnostics["scope_state"], diagnostics["answer_state"]), ("partial", "not_enough_scope"))
            self.assertEqual(diagnostics["omitted_paths_sample"], [])

    def test_sample_is_bounded_ordered_and_reaches_the_back_of_the_list(self):
        self.fill(60, folder="20-knowledge", pad=200)
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 2000):
            result = recall.recall(self.vault, "needle")
        diagnostics = result["diagnostics"]
        sample = diagnostics["omitted_paths_sample"]
        self.assertEqual(len(sample), 10)
        self.assertEqual(sample, sorted(sample))
        self.assertEqual(len(set(sample)), 10)
        self.assertEqual(sample[-1], "20-knowledge/note-059.md")
        read = diagnostics["files_read"]
        self.assertEqual(sample[0], f"20-knowledge/note-{read:03d}.md")
        numbers = [int(path[-6:-3]) for path in sample]
        steps = [after - before for before, after in zip(numbers, numbers[1:])]
        self.assertLessEqual(max(steps) - min(steps), 1)

    def test_fewer_omissions_than_the_cap_are_listed_whole(self):
        self.fill(5, folder="20-knowledge", pad=50)
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 90):
            sample = recall.recall(self.vault, "needle")["diagnostics"]["omitted_paths_sample"]
        self.assertEqual(sample, [f"20-knowledge/note-{index:03d}.md" for index in range(1, 5)])

    def test_file_count_cut_notes_are_sampled(self):
        self.fill(3, folder="20-knowledge")
        for options in ({}, {"backend": "bm25"}):
            with self.subTest(options=options), mock.patch.object(recall, "MAX_FILES", 1):
                sample = recall.recall(self.vault, "needle", **options)["diagnostics"]["omitted_paths_sample"]
                self.assertEqual(sample, ["20-knowledge/note-001.md", "20-knowledge/note-002.md"])

    def test_oversized_notes_are_sampled_next_to_their_named_list(self):
        self.write("20-knowledge/big.md", "needle " + "x" * (recall.MAX_FILE_BYTES + 1))
        diagnostics = recall.recall(self.vault, "other")["diagnostics"]
        self.assertEqual([item["path"] for item in diagnostics["oversized_files"]], ["20-knowledge/big.md"])
        self.assertEqual(diagnostics["omitted_paths_sample"], ["20-knowledge/big.md"])
        self.assertEqual(diagnostics["answer_state"], "not_enough_scope")

    def test_denied_and_excluded_notes_are_not_omissions(self):
        self.write("20-knowledge/a.md", "needle\n")
        self.write("90-assets/hidden.md", "needle\n")
        self.write("10-inbox/_processed/old.md", "needle\n")
        self.write("node_modules/pkg/readme.md", "needle\n")
        self.write("archive/extra.md", "needle\n")
        for options in ({}, {"backend": "bm25"}, {"excluded_sources": ("archive",)}):
            with self.subTest(options=options):
                result = recall.recall(self.vault, "needle", **options)
                diagnostics = result["diagnostics"]
                self.assertTrue(diagnostics["search_complete"])
                self.assertEqual(diagnostics["scope_state"], "complete")
                self.assertEqual(diagnostics["omitted_paths_sample"], [])
                self.assertGreaterEqual(diagnostics["skipped_denied"], 2)
                self.assertNotIn("90-assets/hidden.md", self.paths(result))

    def test_verification_budget_drop_names_the_dropped_note(self):
        self.write("20-knowledge/a.md", "needle one " + "x" * 50 + "\n")
        self.write("20-knowledge/b.md", "needle two " + "x" * 50 + "\n")
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 140):
            result = recall.recall(self.vault, "needle", backend="bm25")
        diagnostics = result["diagnostics"]
        self.assertIn("source_verification_byte_limit", diagnostics["omissions"])
        self.assertEqual(result["matches"], [])
        self.assertEqual(diagnostics["omitted_paths_sample"], ["20-knowledge/a.md", "20-knowledge/b.md"])
        self.assertEqual(diagnostics["answer_state"], "not_enough_scope")

    def test_policy_change_publishes_no_sample(self):
        self.fill(3, folder="20-knowledge", pad=100)
        with mock.patch.object(recall, "MAX_TOTAL_READ_BYTES", 150), \
                mock.patch.object(retrieval, "_same_policy_stamp", return_value=False):
            diagnostics = recall.recall(self.vault, "needle", backend="bm25")["diagnostics"]
        self.assertEqual(diagnostics["status"], "policy_changed")
        self.assertEqual(diagnostics["omitted_paths_sample"], [])

    def test_labels_never_change_matches_context_or_ranking(self):
        self.fill(4, folder="20-knowledge")
        self.write("20-knowledge/best.md", "# needle evidence\nneedle evidence\n")
        for options in ({}, {"backend": "bm25"}, {"backend": "hybrid"}):
            with self.subTest(options=options):
                result = recall.recall(self.vault, "needle evidence", **options)
                self.assertEqual(self.paths(result)[0], "20-knowledge/best.md")
                for key in NEW_KEYS:
                    self.assertIn(key, result["diagnostics"])
                    self.assertNotIn(key, json.dumps(result["matches"]))
                    self.assertNotIn(key, result["context"])


class PriorityTests(VaultCase):
    # Same query terms, same score, and past the budget in path order.
    def fill_front(self):
        self.fill(30, folder="10-early", pad=20 * 1024)
        self.write("90-zz/placeholder.txt", "not a note\n")
        self.write("zz-projects/core/decisions.md", "needle evidence\n" + "x" * 20 * 1024 + "\n")

    def test_last_path_is_lost_by_default_and_read_first_when_prioritised(self):
        self.fill_front()
        target = "zz-projects/core/decisions.md"
        self.configure({"max_total_read_bytes": recall.MAX_FILE_BYTES})
        before = recall.recall(self.vault, "needle", limit=5)
        self.assertNotIn(target, self.paths(before))
        self.assertIn(target, before["diagnostics"]["omitted_paths_sample"])

        self.configure({"max_total_read_bytes": recall.MAX_FILE_BYTES, "priority_paths": [target]})
        after = recall.recall(self.vault, "needle", limit=5)
        self.assertEqual(self.paths(after)[0], target)
        self.assertNotIn(target, after["diagnostics"]["omitted_paths_sample"])
        self.assertEqual(after["diagnostics"]["limits"]["priority_paths"], 1)
        self.assertEqual(after["diagnostics"]["scope_state"], "partial")

    def test_priority_path_keeps_its_place_in_every_backend(self):
        self.fill_front()
        target = "zz-projects/core/decisions.md"
        self.configure({"max_total_read_bytes": 2 * recall.MAX_FILE_BYTES, "priority_paths": [target]})
        for backend in ("lexical", "bm25"):
            with self.subTest(backend=backend):
                result = recall.recall(self.vault, "needle", backend=backend, as_of="2026-01-01")
                self.assertEqual(self.paths(result)[0], target)
        # Fused scores already differ by path order here, so the note keeps that place.
        result = recall.recall(self.vault, "needle", backend="hybrid", as_of="2026-01-01")
        self.assertNotEqual(self.paths(result)[0], target)

    def test_priority_only_breaks_ties(self):
        self.write("10-early/best.md", "# needle evidence\nneedle evidence\n")
        self.write("20-late/tied.md", "needle evidence\n")
        self.write("30-late/other.md", "needle evidence\n")
        self.configure({"priority_paths": ["30-late/other.md"]})
        self.assertEqual(self.paths(recall.recall(self.vault, "needle evidence")),
                         ["10-early/best.md", "30-late/other.md", "20-late/tied.md"])

    def spread(self):
        """Tied in the lexical channel, spread out by note length in the BM25 channel."""
        self.write("20-knowledge/a/a.md", "atlas ledger\n" + "filler words here are many " * 60 + "\n")
        self.write("20-knowledge/b/x.md", "atlas ledger\n")
        self.write("20-knowledge/c/p.md", "atlas ledger\n" + "other padding text " * 10 + "\n")

    def assert_only_ties_moved(self, plain, prioritised):
        """The same notes with the same scores, in descending order; only equal scores may swap."""
        self.assertEqual({match["path"]: match["score"] for match in prioritised["matches"]},
                         {match["path"]: match["score"] for match in plain["matches"]})
        scores = [match["score"] for match in prioritised["matches"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(len(scores), 1)

    def test_priority_never_changes_a_score_on_any_route(self):
        self.spread()
        routes = {"lexical": {"backend": "lexical"}, "lexical_advanced": {"backend": "lexical", "as_of": "2026-01-01"},
                  "bm25": {"backend": "bm25"}, "hybrid": {"backend": "hybrid"},
                  "lexical_ngrams": {"backend": "lexical", "lexical_ngrams": "hangul2"},
                  "bm25_ngrams": {"backend": "bm25", "lexical_ngrams": "hangul2"},
                  "hybrid_ngrams": {"backend": "hybrid", "lexical_ngrams": "hangul2"},
                  "external": {"backend": "bm25", "external_candidates": ["20-knowledge/c/p.md"]},
                  "links": {"backend": "hybrid", "expand_links": 1},
                  "expanded": {"backend": "hybrid", "expand_query": True}}
        for name, options in routes.items():
            for entry in ("20-knowledge/a", "20-knowledge/b", "20-knowledge/c"):
                with self.subTest(route=name, priority=entry):
                    self.configure()
                    plain = recall.recall(self.vault, "atlas ledger", limit=5, **options)
                    self.configure({"priority_paths": [entry]})
                    prioritised = recall.recall(self.vault, "atlas ledger", limit=5, **options)
                    self.assertEqual(prioritised["diagnostics"]["status"], "ok")
                    self.assert_only_ties_moved(plain, prioritised)

    def test_lexical_prefers_the_priority_among_equal_scores(self):
        self.spread()
        self.configure({"priority_paths": ["20-knowledge/c"]})
        result = recall.recall(self.vault, "atlas ledger", backend="lexical", limit=5)
        self.assertEqual(self.paths(result), ["20-knowledge/c/p.md", "20-knowledge/a/a.md", "20-knowledge/b/x.md"])

    def test_bm25_keeps_a_higher_score_ahead_of_the_priority(self):
        self.spread()
        self.configure({"priority_paths": ["20-knowledge/a"]})
        result = recall.recall(self.vault, "atlas ledger", backend="bm25", limit=5)
        self.assertEqual(self.paths(result), ["20-knowledge/b/x.md", "20-knowledge/c/p.md", "20-knowledge/a/a.md"])

    def test_hybrid_keeps_a_higher_fused_score_ahead_of_the_priority(self):
        self.spread()
        self.configure({"priority_paths": ["20-knowledge/c"]})
        result = recall.recall(self.vault, "atlas ledger", backend="hybrid", limit=5)
        self.assertEqual(self.paths(result), ["20-knowledge/b/x.md", "20-knowledge/a/a.md", "20-knowledge/c/p.md"])
        self.assertEqual(result["matches"][2]["ranks"], {"lexical": 3, "bm25": 2, "external": None})

    def test_hybrid_prefers_the_priority_among_equal_fused_scores(self):
        # The first note wins the lexical channel and the second the BM25 channel, so both
        # fuse to 1/61 + 1/62: a real tie at the final step.
        self.write("10-early/a.md", "# needle evidence\n" + "filler words here " * 30 + "\n")
        self.write("20-late/b.md", "needle evidence\n")
        plain = recall.recall(self.vault, "needle evidence", backend="hybrid")
        self.assertEqual(self.paths(plain), ["10-early/a.md", "20-late/b.md"])
        self.assertEqual(plain["matches"][0]["score"], plain["matches"][1]["score"])
        self.configure({"priority_paths": ["20-late"]})
        result = recall.recall(self.vault, "needle evidence", backend="hybrid")
        self.assertEqual(self.paths(result), ["20-late/b.md", "10-early/a.md"])

    def test_identical_notes_tie_in_bm25_but_hybrid_fuses_the_path_order(self):
        self.write("10-early/a.md", "needle evidence\n")
        self.write("20-late/b.md", "needle evidence\n")
        self.configure({"priority_paths": ["20-late"]})
        self.assertEqual(self.paths(recall.recall(self.vault, "needle", backend="bm25")),
                         ["20-late/b.md", "10-early/a.md"])
        # Channel ranks are the same with and without the setting, and they already differ here.
        hybrid = recall.recall(self.vault, "needle", backend="hybrid")
        self.assertEqual(self.paths(hybrid), ["10-early/a.md", "20-late/b.md"])
        self.assertGreater(hybrid["matches"][0]["score"], hybrid["matches"][1]["score"])

    def test_priority_does_not_choose_the_graph_seeds(self):
        self.write("10-early/a.md", "needle evidence [[first.md]] plain plain\n")
        self.write("20-late/b.md", "needle evidence [[second.md]] [[third.md]]\n")
        self.write("30-other/first.md", "unrelated\n")
        self.write("30-other/second.md", "unrelated\n")
        self.write("30-other/third.md", "unrelated\n")
        options = {"backend": "bm25", "expand_links": 1, "limit": 1}
        plain = recall.recall(self.vault, "needle", **options)["diagnostics"]["graph_links_considered"]
        self.configure({"priority_paths": ["20-late"]})
        result = recall.recall(self.vault, "needle", **options)
        self.assertEqual(result["diagnostics"]["graph_links_considered"], plain)
        self.assertEqual(self.paths(result), ["20-late/b.md"])

    def test_folders_listed_order_and_letter_case(self):
        self.write("10-early/a.md", "needle\n")
        self.write("20-Late/b.md", "needle\n")
        self.write("30-last/deep/c.md", "needle\n")
        self.configure({"priority_paths": ["30-LAST", "20-late\\b.md"]})
        self.assertEqual(self.paths(recall.recall(self.vault, "needle")),
                         ["30-last/deep/c.md", "20-Late/b.md", "10-early/a.md"])

    def test_entry_is_a_path_prefix_not_a_name_anywhere(self):
        self.write("10-early/core/a.md", "needle\n")
        self.write("core/b.md", "needle\n")
        self.write("corehouse/c.md", "needle\n")
        self.configure({"priority_paths": ["core"]})
        self.assertEqual(self.paths(recall.recall(self.vault, "needle")),
                         ["core/b.md", "10-early/core/a.md", "corehouse/c.md"])

    def test_priority_survives_the_file_count_cut(self):
        self.write("10-early/a.md", "needle\n")
        self.write("20-late/b.md", "needle\n")
        self.configure({"priority_paths": ["20-late/b.md"]})
        for options in ({}, {"backend": "bm25"}):
            with self.subTest(options=options), mock.patch.object(recall, "MAX_FILES", 1):
                result = recall.recall(self.vault, "needle", **options)
                self.assertEqual(self.paths(result), ["20-late/b.md"])
                self.assertEqual(result["diagnostics"]["omitted_paths_sample"], ["10-early/a.md"])

    def test_priority_never_widens_deny_or_exclusion(self):
        self.write("20-late/open.md", "needle\n")
        self.write("20-late/90-assets/hidden.md", "needle\n")
        self.write("20-late/node_modules/hidden.md", "needle\n")
        self.write("20-late/private/hidden.md", "needle\n")
        self.configure({"priority_paths": ["20-late"]})
        result = recall.recall(self.vault, "needle", excluded_sources=("20-late/private",))
        self.assertEqual(self.paths(result), ["20-late/open.md"])
        self.assertEqual(result["diagnostics"]["omitted_paths_sample"], [])

    def test_missing_priority_path_changes_nothing(self):
        self.write("10-early/a.md", "needle\n")
        self.configure({"priority_paths": ["20-late/gone.md"]})
        result = recall.recall(self.vault, "needle")
        self.assertEqual(self.paths(result), ["10-early/a.md"])
        self.assertEqual(result["diagnostics"]["status"], "ok")

    def test_ranking_note_names_the_tie_break_only_when_configured(self):
        self.write("10-early/a.md", "needle\n")
        plain = recall.recall(self.vault, "needle", backend="bm25")["diagnostics"]["ranking"]
        self.assertNotIn("priority", plain)
        self.configure({"priority_paths": ["10-early"]})
        configured = recall.recall(self.vault, "needle", backend="bm25")["diagnostics"]["ranking"]
        self.assertTrue(configured.startswith(plain))
        self.assertIn("priority_paths", configured)

    def test_empty_settings_give_byte_identical_output(self):
        self.fill(4, folder="20-knowledge", pad=30)
        self.write("30-late/z.md", "needle evidence\n")
        for options in ({}, {"backend": "bm25"}, {"backend": "hybrid", "expand_links": 1}):
            with self.subTest(options=options):
                self.configure()
                absent = json.dumps(recall.recall(self.vault, "needle", **options), ensure_ascii=False)
                self.configure({})
                empty = json.dumps(recall.recall(self.vault, "needle", **options), ensure_ascii=False)
                self.configure({"priority_paths": [], "max_total_read_bytes": recall.MAX_TOTAL_READ_BYTES})
                explicit = json.dumps(recall.recall(self.vault, "needle", **options), ensure_ascii=False)
                self.assertEqual(absent, empty)
                self.assertEqual(absent, explicit)


class EvaluatorScopeTests(VaultCase):
    def scan(self, **options):
        module = evaluator._load_recall_module()
        digests = {}
        return evaluator._twin_note_scan(module, self.vault, read_texts=True, raw_digests=digests, **options), digests

    def test_note_scan_uses_the_configured_budget_and_order(self):
        self.fill(5, folder="20-knowledge", pad=100)
        self.write("30-late/z.md", "needle\n")
        self.configure({"max_total_read_bytes": 1 * MIB, "priority_paths": ["30-late"]})
        module = evaluator._load_recall_module()
        with mock.patch.object(module, "MAX_TOTAL_READ_BYTES", 300):
            scan = evaluator._twin_note_scan(module, self.vault, read_texts=True)
        self.assertEqual(scan["listing"][0], "30-late/z.md")
        self.assertTrue(scan["summary"]["search_complete"])
        self.assertEqual(scan["summary"]["notes_read"], 6)

    def test_default_snapshot_digest_keeps_its_definition(self):
        self.fill(2, folder="20-knowledge")
        scan, digests = self.scan()
        self.assertEqual(evaluator._vault_snapshot_digest(scan, digests),
                         evaluator._digest([[path, digests.get(path)] for path in scan["listing"]]))
        self.assertEqual(evaluator._recall_setting_fields(scan), {})

    def test_read_settings_change_the_snapshot_digest(self):
        self.fill(2, folder="20-knowledge")
        seen = {}
        for label, block in (("default", None), ("budget", {"max_total_read_bytes": 32 * MIB}),
                             ("wider", {"max_total_read_bytes": 64 * MIB}),
                             ("priority", {"priority_paths": ["20-knowledge/note-001.md"]})):
            self.configure(block)
            scan, digests = self.scan()
            seen[label] = evaluator._vault_snapshot_digest(scan, digests)
        self.assertEqual(len(set(seen.values())), 4)

    def test_snapshot_prints_only_values_and_counts_not_priority_paths(self):
        self.fill(2, folder="20-knowledge")
        self.configure({"max_total_read_bytes": 32 * MIB, "priority_paths": ["20-knowledge/note-001.md"]})
        scan, _digests = self.scan()
        fields = evaluator._recall_setting_fields(scan)
        self.assertEqual(fields, {"total_read_bytes": 32 * MIB, "priority_path_count": 1})
        self.assertNotIn("note-001", json.dumps(fields))

    def test_keyed_reports_hash_the_omitted_paths(self):
        self.write("20-knowledge/big.md", "needle " + "x" * (recall.MAX_FILE_BYTES + 1))
        diagnostics = recall.recall(self.vault, "other")["diagnostics"]
        self.assertEqual(diagnostics["omitted_paths_sample"], ["20-knowledge/big.md"])
        hashed = evaluator._keyed_paths({"diagnostics": diagnostics}, lambda value: "TOKEN")["diagnostics"]
        self.assertEqual(hashed["omitted_paths_sample"], ["TOKEN"])

    def test_keyed_reports_hash_the_skipped_worktree_paths(self):
        hashed = evaluator._keyed_paths({"diagnostics": {"skipped_worktrees": ["people/copy", "people/other"]}},
                                        lambda value: "TOKEN")["diagnostics"]
        self.assertEqual(hashed["skipped_worktrees"], ["TOKEN", "TOKEN"])

    def test_goldset_listing_cut_follows_the_same_order(self):
        for index in range(12):
            self.write(f"10-early/note-{index:02d}.md", "needle\n")
        self.write("20-late/key.md", "needle\n")
        self.configure({"priority_paths": ["20-late"]})
        goldset = self.write("gold.json", json.dumps({"queries": [
            {"id": f"q{index}", "language": "en", "scenario": "direct", "query": "needle",
             "expected_paths": ["20-late/key.md"]}
            for index in range(12)]}))
        module = evaluator._load_recall_module()
        with mock.patch.object(evaluator, "_load_recall_module", return_value=module), \
                mock.patch.object(module, "MAX_FILES", 5):
            prepared = evaluator.load_goldset(self.vault, goldset)
        self.assertEqual(prepared.sections["goldset"]["label_match_counts"]["path"], 12)


class KeyedReportPathTests(unittest.TestCase):
    """A folder name stays out of a keyed report whichever diagnostic would carry it."""

    MARK = "zqprivatefolder"

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name).resolve()
        cls.vault = root / "vault"
        shutil.copytree(ROOT / "tests" / "fixtures" / "recall" / "vault", cls.vault)
        mark = cls.MARK
        notes = {f"{mark}1/big.md": "needle " + "x" * (recall.MAX_FILE_BYTES + 1),
                 f"{mark}2/old.md": f'---\nvalid_until: 2020-01-01\nsuperseded_by: "[[{mark} new]]"\n---\nneedle\n',
                 f"{mark}3/{mark} new.md": "---\nchecked_at: 2026-01-01\n---\nneedle\n",
                 f"{mark}4/one.md": f"---\naliases: [{mark}alias]\n---\nneedle\n",
                 f"{mark}4/two.md": f"---\naliases: [{mark}alias]\n---\nneedle\n"}
        for relative, text in notes.items():
            path = cls.vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
        for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "notes"),
                     ("worktree", "add", "-q", f"{mark}5/a copy", "-b", "copy")):
            subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                            "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                           cwd=cls.vault, capture_output=True, timeout=60, check=True)
        cls.key = root / "report.key"
        cls.key.write_bytes(b"k" * 32)

    def report(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            evaluator.main(["--vault", str(self.vault), "--goldset",
                            str(ROOT / "tests" / "fixtures" / "recall" / "queries.json"), *arguments])
        return output.getvalue()

    def test_the_report_names_the_worktree_only_by_token(self):
        for name, arguments in (("key_file", ("--redact-key-file", str(self.key))),
                                ("ephemeral", ("--redact-ephemeral",)),
                                ("advanced", ("--redact-ephemeral", "--advanced", "--backend", "hybrid",
                                              "--expand-query", "--expand-links", "1"))):
            with self.subTest(mode=name):
                text = self.report(*arguments)
                self.assertIn('"skipped_worktrees"', text)
                self.assertNotIn(self.MARK, text)

    def test_the_plain_report_keeps_the_path(self):
        self.assertIn(f"{self.MARK}5/a copy", self.report())


class LinkBudgetTests(VaultCase):
    def test_binding_budget_covers_a_full_review_at_the_unchanged_file_limit(self):
        # Largest source, the reserve at the last proposal, and every recheck.
        needed = recall.MAX_FILE_BYTES + (links.MAX_PROPOSALS + 1) * recall.MAX_FILE_BYTES
        self.assertLessEqual(needed, links.MAX_BINDING_BYTES)

    def test_raising_the_read_budget_keeps_ten_proposals_and_the_binding_budget(self):
        self.write("20-knowledge/source.md", "# Source\nbody\n")
        for index in range(12):
            self.write(f"20-knowledge/target-{index:02d}.md", "memory storage\n" + "x" * 40 * 1024 + "\n")
        self.configure({"max_total_read_bytes": 64 * MIB})
        result = links.propose_links(self.vault, "20-knowledge/source.md", "memory storage", limit=10, max_tokens=20000)
        diagnostics = result["diagnostics"]
        self.assertEqual(len(result["proposals"]), 10)
        self.assertTrue(diagnostics["search_complete"])
        self.assertEqual(diagnostics["limits"]["binding_bytes"], 16 * MIB)
        self.assertEqual(diagnostics["limits"]["file_bytes"], recall.MAX_FILE_BYTES)
        self.assertEqual(diagnostics["recall"]["limits"]["total_read_bytes"], 64 * MIB)

    def test_invalid_recall_settings_stop_link_proposals_with_the_fixed_code(self):
        self.write("20-knowledge/source.md", "# Source\nbody\n")
        self.configure({"max_total_read_bytes": "many"})
        result = links.propose_links(self.vault, "20-knowledge/source.md", "memory storage")
        self.assertEqual(result["diagnostics"]["status"], "invalid_config")
        self.assertEqual(result["proposals"], [])


class DocumentationTests(unittest.TestCase):
    def test_recall_command_names_the_new_diagnostics(self):
        text = (ROOT / "commands" / "vault-recall.md").read_text(encoding="utf-8")
        for name in ("omitted_paths_sample", "answer_state", "not_enough_scope"):
            self.assertIn(name, text)

    def test_docs_state_the_setting_and_that_the_label_is_derived(self):
        scope = (ROOT / "docs" / "recall-scope.md").read_text(encoding="utf-8")
        for name in ("recall.max_total_read_bytes", "recall.priority_paths", "answer_state",
                     "derived label", "c864f15af264", "a5a933bc043e"):
            self.assertIn(name, scope)
        self.assertIn("recall-scope.md", (ROOT / "docs" / "memory-patterns.md").read_text(encoding="utf-8"))
        evaluation = (ROOT / "docs" / "memory-evaluation.md").read_text(encoding="utf-8")
        self.assertIn("recall.max_total_read_bytes", evaluation)


if __name__ == "__main__":
    unittest.main()
