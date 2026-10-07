from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
MODULE = SCRIPTS / "vault_retrieval.py"
FIXTURE = ROOT / "tests" / "fixtures" / "recall_advanced" / "vault"


def load_module():
    if not MODULE.is_file():
        return None
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("advanced_retrieval_test", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


retrieval = load_module()


class AdvancedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(retrieval, "advanced source retrieval is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        self.write("00-meta/vault-config.json", json.dumps({
            "deny_zones": ["20-knowledge/_archive", "90-assets", ".obsidian"],
            "exclude_dirs": ["scratch"],
        }))

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_retrieval(self, query="rollback", **kwargs):
        return retrieval.retrieve(self.vault, query, **kwargs)

    def paths(self, result):
        return [match["path"] for match in result["matches"]]

    def test_default_result_equals_legacy_without_added_fields(self):
        self.write("20-knowledge/a.md", "# Rollback\nrollback procedure\n")
        self.assertEqual(self.run_retrieval(), retrieval._legacy.recall(self.vault, "rollback"))

    def test_temporal_half_open_interval_selects_current_and_historical_sources(self):
        self.write("20-knowledge/old.md", "---\nvalid_from: 2024-01-01\nvalid_until: 2025-01-01\n---\n# Rollback\nrollback OLD\n")
        self.write("20-knowledge/new.md", "---\nvalid_from: 2025-01-01\n---\nrollback NEW\n")
        self.assertEqual(self.paths(self.run_retrieval(as_of="2024-12-31")), ["20-knowledge/old.md"])
        self.assertEqual(self.paths(self.run_retrieval(as_of="2025-01-01")), ["20-knowledge/new.md"])

    def test_temporal_timezone_normalizes_boundary(self):
        self.write("20-knowledge/a.md", "---\nvalid_until: 2025-01-01T00:00:00Z\n---\nrollback\n")
        self.assertEqual(self.paths(self.run_retrieval(as_of="2025-01-01T09:00:00+09:00")), [])
        self.assertEqual(self.paths(self.run_retrieval(as_of="2024-12-31T23:59:59Z")), ["20-knowledge/a.md"])

    def test_timezone_overflow_is_rejected_for_options_and_metadata(self):
        self.write("20-knowledge/a.md", "rollback\n")
        for offset in ("+00:60", "+01:99", "-01:99", "+24:00"):
            with self.subTest(offset=offset):
                result = self.run_retrieval(as_of="2025-01-01T00:00:00" + offset)
                self.assertEqual(result["diagnostics"]["status"], "invalid_options")
                self.assertEqual(result["diagnostics"]["files_read"], 0)
                self.write("20-knowledge/a.md", "---\nvalid_from: 2025-01-01T00:00:00" + offset + "\n---\nrollback\n")
                metadata = self.run_retrieval(as_of="2026-01-01")
                self.assertEqual(metadata["matches"], [])
                self.assertEqual(metadata["diagnostics"]["temporal"][0]["status"], "invalid")

    def test_malformed_duplicate_and_ambiguous_temporal_metadata_fail_closed(self):
        values = ["valid_from: tomorrow", "valid_from: 2025-01-01\nvalid_from: 2024-01-01", "valid_from: 2025-01-01T12:00:00", "valid_from: 2026-01-01\nvalid_until: 2025-01-01", "checked_at: [2025-01-01]", "valid_from:\n  nested: 2025-01-01"]
        for index, value in enumerate(values):
            self.write(f"20-knowledge/{index}.md", f"---\n{value}\n---\nrollback\n")
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["temporal_rejected"], 6)
        self.assertTrue(all(row["status"] == "invalid" for row in result["diagnostics"]["temporal"]))

    def test_unclosed_frontmatter_temporal_mode_fails_closed(self):
        self.write("20-knowledge/a.md", "---\nvalid_from: 2024-01-01\nrollback\n")
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["temporal"][0]["status"], "invalid")

    def test_quoted_or_folded_temporal_keys_are_ambiguous_and_fail_closed(self):
        for index, key in enumerate(('"valid_until"', "'valid_until'", "VALID_UNTIL")):
            self.write(f"20-knowledge/{index}.md", f"---\n{key}: 2024-01-01\n---\nrollback EXPIRED\n")
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["temporal_rejected"], 3)

    def test_whitespace_keys_and_yaml_merges_cannot_hide_temporal_bounds(self):
        for index, metadata in enumerate(("valid_until : 2024-01-01", "<<: *inherited_bounds", "{valid_until: 2024-01-01}")):
            self.write(f"20-knowledge/{index}.md", f"---\n{metadata}\n---\nrollback EXPIRED\n")
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(result["matches"], [])
        self.assertTrue(all(row["status"] == "invalid" for row in result["diagnostics"]["temporal"]))

    def test_supersession_metadata_cannot_unbound_output(self):
        self.write("20-knowledge/a.md", "---\nsuperseded_by: " + "x" * 5000 + "\n---\nrollback\n")
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["temporal"][0]["status"], "invalid")
        self.assertLess(len(json.dumps(result)), 5000)

    def test_graph_expansion_requires_complete_literal_filename_index(self):
        self.write("20-knowledge/seed.md", "rollback [[target]]\n")
        self.write("20-knowledge/target.md", "TARGET_ONLY\n")
        original = retrieval._legacy._markdown_paths
        def limited_walk(*args, **kwargs):
            paths = original(*args, **kwargs)
            retrieval._legacy._omit(args[-1], "directory_entry_limit")
            return paths
        with mock.patch.object(retrieval._legacy, "_markdown_paths", side_effect=limited_walk):
            result = self.run_retrieval(expand_links=2)
        self.assertEqual(self.paths(result), ["20-knowledge/seed.md"])
        self.assertIn("graph_filename_index_incomplete", result["diagnostics"]["omissions"])

    def test_external_ranks_do_not_fabricate_evidence_for_empty_source(self):
        self.write("20-knowledge/empty.md", "")
        result = self.run_retrieval("unmatched", external_candidates=[{"path": "20-knowledge/empty.md", "rank": 1}])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["external_rejected"], 1)

    def test_unknown_currentness_is_visible_and_checked_at_is_not_valid_until(self):
        self.write("20-knowledge/unknown.md", "rollback UNKNOWN\n")
        self.write("20-knowledge/checked.md", "---\nchecked_at: 2024-01-01\n---\nrollback CHECKED\n")
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(set(self.paths(result)), {"20-knowledge/unknown.md", "20-knowledge/checked.md"})
        self.assertTrue(all(match["temporal"]["currentness"] == "unknown" for match in result["matches"]))
        checked = next(m for m in result["matches"] if m["path"].endswith("checked.md"))
        self.assertEqual(checked["temporal"]["checked_at"], "2024-01-01")

    def test_supersession_uses_successor_explicit_start_and_preserves_history(self):
        self.write("20-knowledge/old.md", '---\nvalid_from: 2024-01-01\nsuperseded_by: "[[new]]"\n---\n# Rollback\nrollback OLD\n')
        self.write("20-knowledge/new.md", "---\nvalid_from: 2025-01-01\n---\nrollback NEW\n")
        self.assertEqual(self.paths(self.run_retrieval(as_of="2024-12-31")), ["20-knowledge/old.md"])
        self.assertEqual(self.paths(self.run_retrieval(as_of="2025-01-01")), ["20-knowledge/new.md"])

    def test_unresolved_supersession_does_not_guess_from_mtime(self):
        self.write("20-knowledge/old.md", '---\nsuperseded_by: "[[missing]]"\n---\nrollback\n')
        result = self.run_retrieval(as_of="2025-01-01")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["temporal"][0]["status"], "supersession_unknown")

    def test_invalid_advanced_options_fail_before_note_reads(self):
        self.write("20-knowledge/a.md", "rollback PRIVATE\n")
        for options in ({"as_of": "yesterday"}, {"as_of": "2025-01-01T12:00:00"}, {"expand_links": 3}, {"expand_links": True}, {"backend": "model"}, {"excluded_sources": "20-knowledge"}, {"excluded_sources": ["../secret"]}, {"external_candidates": [{"path": "a.md", "rank": True}]}):
            with self.subTest(options=options):
                result = self.run_retrieval(**options)
                self.assertEqual(result["diagnostics"]["status"], "invalid_options")
                self.assertEqual(result["diagnostics"]["files_read"], 0)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_graph_expands_two_hops_with_literal_passage_proof_and_cycles_bounded(self):
        self.write("20-knowledge/seed.md", "# Rollback\nrollback procedure uses [[Bridge|alias]].\n")
        self.write("20-knowledge/Bridge.md", "# Bridge\nSee [[최종]] for recovery.\n")
        self.write("20-knowledge/최종.md", "# Final\nRESTORE_PROOF and [[seed]].\n")
        self.assertEqual(self.paths(self.run_retrieval(expand_links=0)), ["20-knowledge/seed.md"])
        self.assertEqual(self.paths(self.run_retrieval(expand_links=1)), ["20-knowledge/seed.md", "20-knowledge/Bridge.md"])
        result = self.run_retrieval(expand_links=2)
        self.assertEqual(self.paths(result), ["20-knowledge/seed.md", "20-knowledge/Bridge.md", "20-knowledge/최종.md"])
        final = result["matches"][2]
        self.assertEqual(final["graph"][0]["depth"], 2)
        self.assertEqual(final["graph"][0]["seed_path"], "20-knowledge/seed.md")
        self.assertEqual(final["graph"][0]["origin_path"], "20-knowledge/Bridge.md")
        self.assertIn("[[최종]]", final["graph"][0]["origin_quote"])
        self.assertIn("RESTORE_PROOF", result["context"])

    def test_graph_does_not_guess_duplicate_filename_or_alias(self):
        self.write("20-knowledge/seed.md", "rollback [[Same]] [[Alias]] [[folder/Safe]]\n")
        self.write("20-knowledge/a/Same.md", "DUPLICATE_A\n")
        self.write("20-knowledge/b/Same.md", "DUPLICATE_B\n")
        self.write("20-knowledge/Safe.md", "---\naliases: [Alias]\n---\nNO_ALIAS\n")
        result = self.run_retrieval(expand_links=2)
        self.assertEqual(self.paths(result), ["20-knowledge/seed.md"])
        self.assertGreaterEqual(result["diagnostics"]["graph_ambiguous"], 1)

    def test_graph_respects_temporal_and_extra_exclusions(self):
        self.write("20-knowledge/seed.md", "rollback [[expired]] [[hidden]]\n")
        self.write("20-knowledge/expired.md", "---\nvalid_until: 2025-01-01\n---\nEXPIRED\n")
        self.write("20-knowledge/hidden.md", "HIDDEN\n")
        result = self.run_retrieval(as_of="2025-01-01", expand_links=2, excluded_sources=["20-knowledge/hidden.md"])
        self.assertEqual(self.paths(result), ["20-knowledge/seed.md"])
        self.assertNotIn("EXPIRED", result["context"])
        self.assertNotIn("HIDDEN", result["context"])

    def test_bm25_uses_document_frequency_and_not_filename_or_substring_hits(self):
        self.write("20-knowledge/rare.md", "orchid common common\n")
        self.write("20-knowledge/common.md", "common common common\n")
        self.write("20-knowledge/orchid.md", "orchidlike unrelated\n")
        result = self.run_retrieval("orchid common", backend="bm25")
        self.assertEqual(result["matches"][0]["path"], "20-knowledge/rare.md")
        self.assertNotIn("20-knowledge/orchid.md", self.paths(self.run_retrieval("orchid", backend="bm25")))
        self.assertGreater(result["matches"][0]["scores"]["bm25"], 0)

    def test_bm25_passage_quote_contains_exact_token_evidence(self):
        self.write("20-knowledge/a.md", "# Orchidlike\nActual orchid evidence.\n")
        match = self.run_retrieval("orchid", backend="bm25")["matches"][0]
        self.assertEqual(match["line"], 2)
        self.assertEqual(match["source"]["quote"], "Actual orchid evidence.")

    def test_hybrid_explains_reciprocal_rank_contributions(self):
        self.write("20-knowledge/a.md", "# Rollback\nrollback recovery\n")
        self.write("20-knowledge/b.md", "rollback alternative\n")
        result = self.run_retrieval(backend="hybrid")
        first = result["matches"][0]
        self.assertAlmostEqual(first["scores"]["rrf_lexical"], 1 / 61)
        self.assertAlmostEqual(first["scores"]["rrf_bm25"], 1 / 61)
        self.assertAlmostEqual(first["score"], 2 / 61)
        self.assertEqual(first["review_status"], "needs_review")

    def test_external_candidates_are_paths_only_reread_and_bound_to_sources(self):
        path = self.write("20-knowledge/live.md", "# Live\nACTUAL_EVIDENCE\n")
        result = self.run_retrieval("unmatched", external_candidates=[{"path": "20-knowledge/live.md", "rank": 1, "snippet": "FORGED", "approved": True}])
        match = result["matches"][0]
        self.assertIn("ACTUAL_EVIDENCE", result["context"])
        self.assertNotIn("FORGED", json.dumps(result))
        self.assertEqual(match["source"]["quote"], "ACTUAL_EVIDENCE")
        self.assertEqual(match["source"]["path"], "20-knowledge/live.md")
        import hashlib
        self.assertEqual(match["source"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(match["review_status"], "needs_review")
        self.assertEqual(result["diagnostics"]["external_provider_status"], "candidates_only_unverified")

    def test_external_candidate_cannot_relax_policy_or_reserved_git(self):
        for rel in ("20-knowledge/_archive/a.md", "90-assets/a.md", "scratch/a.md", ".git/a.md", "20-knowledge/private.md"):
            self.write(rel, "SECRET_MARKER\n")
        self.write("20-knowledge/safe.md", "SAFE\n")
        candidates = [{"path": p, "rank": i + 1} for i, p in enumerate(["../outside.md", ".git/a.md", "20-knowledge/_archive/a.md", "90-assets/a.md", "scratch/a.md", "20-knowledge/private.md", "20-knowledge/safe.md"])]
        result = self.run_retrieval("unmatched", external_candidates=candidates, excluded_sources=["20-knowledge/private.md"])
        self.assertEqual(self.paths(result), ["20-knowledge/safe.md"])
        self.assertNotIn("SECRET_MARKER", json.dumps(result))
        self.assertGreaterEqual(result["diagnostics"]["external_rejected"], 6)

    def test_external_candidates_and_context_are_bounded(self):
        self.write("20-knowledge/a.md", "rollback " + "x" * 900)
        self.assertEqual(self.run_retrieval(external_candidates=[{"path": "20-knowledge/a.md", "rank": n + 1} for n in range(51)])["diagnostics"]["status"], "invalid_options")
        result = self.run_retrieval(backend="hybrid", max_tokens=15)
        self.assertLessEqual(retrieval._legacy.estimate_tokens(result["context"]), 15)
        self.assertTrue(result["diagnostics"]["context_truncated"] or result["diagnostics"]["context_omitted"])

    def test_metadata_cannot_self_approve_source_authority(self):
        self.write("20-knowledge/a.md", "---\napproved: true\nreview_status: approved\nauthority: definitive\n---\nrollback claim\n")
        match = self.run_retrieval(backend="hybrid")["matches"][0]
        self.assertEqual(match["review_status"], "needs_review")
        self.assertEqual(match["source_authority"], "unverified")

    def test_policy_is_rechecked_after_read_and_drift_discards_context(self):
        self.write("20-knowledge/a.md", "rollback DRIFT_SECRET\n")
        original = retrieval._legacy._read_markdown
        changed = False
        def read_then_restrict(*args, **kwargs):
            nonlocal changed
            text = original(*args, **kwargs)
            if not changed:
                changed = True
                self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["20-knowledge"]}))
            return text
        with mock.patch.object(retrieval._legacy, "_read_markdown", side_effect=read_then_restrict):
            result = self.run_retrieval(backend="bm25")
        self.assertEqual(result["diagnostics"]["status"], "policy_changed")
        self.assertNotIn("DRIFT_SECRET", json.dumps(result))

    def test_policy_change_stops_later_note_reads(self):
        self.write("20-knowledge/a.md", "rollback FIRST\n")
        self.write("20-knowledge/b.md", "rollback SHOULD_NOT_READ\n")
        original = retrieval._legacy._read_markdown
        read_paths = []
        def restrict_after_first(*args, **kwargs):
            read_paths.append(args[2])
            text = original(*args, **kwargs)
            self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["20-knowledge"]}))
            return text
        with mock.patch.object(retrieval._legacy, "_read_markdown", side_effect=restrict_after_first):
            result = self.run_retrieval(backend="hybrid")
        self.assertEqual(result["diagnostics"]["status"], "policy_changed")
        self.assertEqual(read_paths, ["20-knowledge/a.md"])

    def test_bootstrap_policy_snapshot_is_bound_before_config_read(self):
        self.write("20-knowledge/a.md", "rollback SECRET\n")
        original = retrieval._legacy._read_regular_bytes
        changed = False
        def replace_after_config_read(path, bound):
            nonlocal changed
            result = original(path, bound)
            if path.name == "vault-config.json" and not changed:
                changed = True
                self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["20-knowledge"]}))
            return result
        with mock.patch.object(retrieval._legacy, "_read_regular_bytes", side_effect=replace_after_config_read), mock.patch.object(retrieval._legacy, "_read_markdown", wraps=retrieval._legacy._read_markdown) as markdown:
            result = self.run_retrieval(backend="hybrid")
        self.assertTrue(changed)
        self.assertEqual(markdown.call_count, 0)
        self.assertEqual(result["matches"], [])
        self.assertNotEqual(result["diagnostics"]["status"], "ok")

    def test_final_policy_snapshot_is_checked_after_reader_callback(self):
        self.write("20-knowledge/a.md", "rollback FIRST_OLD\n")
        original = retrieval._legacy._read_config
        count = 0
        def replace_after_final_config(*args):
            nonlocal count
            count += 1
            result = original(*args)
            if count == 3:
                self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["20-knowledge"]}))
            return result
        with mock.patch.object(retrieval._legacy, "_read_config", side_effect=replace_after_final_config):
            result = self.run_retrieval(backend="hybrid")
        self.assertEqual(count, 3)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["diagnostics"]["status"], "policy_changed")
        self.assertNotIn("FIRST_OLD", json.dumps(result))

    def test_later_source_verification_cannot_leave_earlier_source_stale(self):
        first = self.write("20-knowledge/a.md", "rollback FIRST_OLD\n")
        self.write("20-knowledge/b.md", "rollback SECOND\n")
        original = retrieval._legacy._read_regular_bytes
        second_reads = 0
        def replace_earlier_after_later(path, bound):
            nonlocal second_reads
            result = original(path, bound)
            if path.name == "b.md":
                second_reads += 1
                if second_reads == 2:
                    first.write_text("rollback FIRST_NEW\n", encoding="utf-8")
            return result
        with mock.patch.object(retrieval._legacy, "_read_regular_bytes", side_effect=replace_earlier_after_later):
            result = self.run_retrieval(backend="hybrid")
        self.assertEqual(second_reads, 2)
        self.assertNotIn("FIRST_OLD", json.dumps(result))
        self.assertFalse(result["diagnostics"]["search_complete"])

    def test_incomplete_filename_index_cannot_establish_supersession_cutoff(self):
        self.write("20-knowledge/old.md", '---\nvalid_from: 2020-01-01\nsuperseded_by: "[[New]]"\n---\nrollback OLD\n')
        self.write("20-knowledge/x/New.md", "---\nvalid_from: 2026-01-01\n---\nnew version\n")
        self.write("20-knowledge/y/New.md", "---\nvalid_from: 2024-01-01\n---\nduplicate version\n")
        original = retrieval._legacy._markdown_paths
        def incomplete(*args):
            paths = original(*args)
            retrieval._legacy._omit(args[-1], "directory_entry_limit")
            return [(rel, path) for rel, path in paths if rel != "20-knowledge/y/New.md"]
        with mock.patch.object(retrieval._legacy, "_markdown_paths", side_effect=incomplete):
            result = self.run_retrieval(as_of="2025-01-01", backend="hybrid")
        self.assertEqual(result["matches"], [])
        old = next(row for row in result["diagnostics"]["temporal"] if row["path"] == "20-knowledge/old.md")
        self.assertEqual(old["status"], "supersession_unknown")
        self.assertNotIn("superseded_at", old)

    def test_ambiguous_duplicate_or_nonfinite_config_is_rejected(self):
        self.write("private/a.md", "rollback SECRET\n")
        for raw in ('{"deny_zones":["private"],"deny_zones":[],"exclude_dirs":[]}', '{"deny_zones":[],"untrusted":NaN}'):
            self.write("00-meta/vault-config.json", raw)
            result = self.run_retrieval(backend="hybrid")
            self.assertEqual(result["diagnostics"]["status"], "invalid_config")
            self.assertEqual(result["diagnostics"]["files_read"], 0)
            self.assertNotIn("SECRET", json.dumps(result))

    def test_learned_policy_denies_its_config_path_before_note_or_refresh_reads(self):
        self.write("20-knowledge/a.md", "rollback SECRET\n")
        for configuration in ({"deny_zones": ["00-meta/vault-config.json"]}, {"exclude_dirs": ["00-meta"]}):
            self.write("00-meta/vault-config.json", json.dumps(configuration))
            with mock.patch.object(retrieval._legacy, "_read_config", wraps=retrieval._legacy._read_config) as reader:
                result = self.run_retrieval(backend="hybrid")
            self.assertEqual(result["diagnostics"]["status"], "invalid_config")
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(result["diagnostics"]["files_read"], 0)
            self.assertNotIn("SECRET", json.dumps(result))

    def test_source_drift_between_read_and_render_is_not_returned(self):
        note = self.write("20-knowledge/a.md", "rollback STALE_MARKER\n")
        original = retrieval._legacy._read_markdown
        def read_then_change(*args, **kwargs):
            text = original(*args, **kwargs)
            note.write_text("rollback CURRENT\n", encoding="utf-8")
            return text
        with mock.patch.object(retrieval._legacy, "_read_markdown", side_effect=read_then_change):
            result = self.run_retrieval(backend="hybrid")
        self.assertEqual(result["matches"], [])
        self.assertIn("source_changed", result["diagnostics"]["omissions"])
        self.assertNotIn("STALE_MARKER", json.dumps(result))

    def test_same_size_source_drift_is_rejected(self):
        note = self.write("20-knowledge/a.md", "rollback OLD\n")
        original_time = note.stat().st_mtime_ns
        original = retrieval._legacy._read_markdown
        def read_then_replace(*args, **kwargs):
            text = original(*args, **kwargs)
            note.write_text("rollback NEW\n", encoding="utf-8")
            os.utime(note, ns=(original_time, original_time))
            return text
        with mock.patch.object(retrieval._legacy, "_read_markdown", side_effect=read_then_replace):
            result = self.run_retrieval(backend="hybrid")
        self.assertEqual(result["matches"], [])
        self.assertIn("source_changed", result["diagnostics"]["omissions"])

    def test_additional_exclusion_path_size_is_bounded_before_reads(self):
        result = self.run_retrieval(excluded_sources=["x" * 5000])
        self.assertEqual(result["diagnostics"]["status"], "invalid_options")
        self.assertEqual(result["diagnostics"]["files_read"], 0)

    def test_source_mtime_and_content_hash_do_not_establish_currentness(self):
        path = self.write("20-knowledge/a.md", "rollback no date evidence\n")
        os.utime(path, (1, 1))
        before = self.run_retrieval(as_of="2025-01-01")["matches"][0]
        os.utime(path, (2_000_000_000, 2_000_000_000))
        after = self.run_retrieval(as_of="2025-01-01")["matches"][0]
        self.assertEqual(before["temporal"], {"status": "unknown", "currentness": "unknown", "errors": []})
        self.assertEqual(after["temporal"], before["temporal"])

    def test_symlink_and_hardlink_candidates_never_surface_outside_data(self):
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_text("OUTSIDE_SECRET\n", encoding="utf-8")
        directory = self.vault / "20-knowledge"
        directory.mkdir()
        try:
            os.link(outside, directory / "hard.md")
        except OSError as exc:
            self.skipTest(str(exc))
        try:
            (directory / "sym.md").symlink_to(outside)
        except (OSError, NotImplementedError):
            pass
        result = self.run_retrieval("unmatched", external_candidates=[{"path": "20-knowledge/hard.md", "rank": 1}, {"path": "20-knowledge/sym.md", "rank": 2}])
        self.assertEqual(result["matches"], [])
        self.assertNotIn("OUTSIDE_SECRET", json.dumps(result))

    def test_advanced_read_and_file_budgets_are_enforced(self):
        self.write("20-knowledge/a.md", "rollback " + "a" * 90)
        self.write("20-knowledge/b.md", "rollback " + "b" * 90)
        with mock.patch.object(retrieval._legacy, "MAX_TOTAL_READ_BYTES", 110):
            result = self.run_retrieval(backend="bm25")
        self.assertLessEqual(result["diagnostics"]["bytes_read"], 110)
        self.assertFalse(result["diagnostics"]["search_complete"])
        self.assertIn("total_byte_limit", result["diagnostics"]["omissions"])

    def test_source_hash_binds_original_bom_bytes(self):
        path = self.write("20-knowledge/a.md", "\ufeff# Rollback\nrollback quoted evidence\n")
        result = self.run_retrieval(backend="bm25")
        import hashlib
        self.assertEqual(result["matches"][0]["source"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_cli_sidecar_reads_only_safe_json_paths_and_works_without_loader_sys_path(self):
        self.write("20-knowledge/a.md", "# Actual\nEVIDENCE\n")
        self.write("00-meta/candidates.json", json.dumps({"provider": "qmd", "candidates": [{"path": "20-knowledge/a.md", "rank": 1, "snippet": "FORGED"}]}))
        run = subprocess.run([sys.executable, str(SCRIPTS / "vault_recall.py"), "--vault", str(self.vault), "--query", "unmatched", "--external-candidates", "00-meta/candidates.json", "--backend", "hybrid", "--format", "json"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(self.paths(result), ["20-knowledge/a.md"])
        self.assertNotIn("FORGED", run.stdout)
        loader = "import importlib.util,sys,pathlib;sys.path.insert(0," + repr(str(SCRIPTS)) + ");s=importlib.util.spec_from_file_location('transient_recall'," + repr(str(SCRIPTS / "vault_recall.py")) + ");m=importlib.util.module_from_spec(s);s.loader.exec_module(m);sys.path.remove(" + repr(str(SCRIPTS)) + ");print(m.recall(pathlib.Path(" + repr(str(self.vault)) + "),'Actual',backend='bm25')['diagnostics']['status'])"
        loaded = subprocess.run([sys.executable, "-c", loader], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        self.assertEqual(loaded.stdout.strip(), "ok")

    def test_cli_sidecar_rejects_oversized_duplicate_json_and_denied_path(self):
        self.write("20-knowledge/a.md", "rollback\n")
        for rel, content in (("00-meta/large.json", " " * (65536 + 1)), ("00-meta/dupe.json", '{"candidates":[],"candidates":[]}'), ("90-assets/candidates.json", '{"candidates":[]}')):
            self.write(rel, content)
            run = subprocess.run([sys.executable, str(SCRIPTS / "vault_recall.py"), "--vault", str(self.vault), "--query", "rollback", "--external-candidates", rel, "--format", "json"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(json.loads(run.stdout)["matches"], [])

    def test_explicit_dated_fixture_removes_observed_stale_top_one(self):
        shutil.rmtree(self.vault)
        shutil.copytree(FIXTURE, self.vault)
        legacy = self.run_retrieval("phoenix rollback", limit=1)
        self.assertEqual(self.paths(legacy), ["20-knowledge/phoenix-old.md"])
        current = self.run_retrieval("phoenix rollback", limit=1, as_of="2026-10-07", backend="hybrid")
        historical = self.run_retrieval("phoenix rollback", limit=1, as_of="2026-07-01", backend="hybrid")
        self.assertEqual(self.paths(current), ["20-knowledge/phoenix-current.md"])
        self.assertEqual(self.paths(historical), ["20-knowledge/phoenix-old.md"])

    def test_bilingual_advanced_fixture_labels_match_sources_under_explicit_options(self):
        queries = json.loads((FIXTURE.parent / "queries.json").read_text(encoding="utf-8"))["queries"]
        self.assertEqual(len(queries), 16)
        for item in queries:
            with self.subTest(query=item["id"]):
                options = {key: item[key] for key in ("as_of", "expand_links", "backend", "external_candidates") if key in item}
                result = retrieval.retrieve(FIXTURE, item["query"], limit=3, **options)
                paths = self.paths(result)
                self.assertTrue(set(item["expected_paths"]).issubset(paths))
                self.assertFalse(set(item.get("stale_paths", [])).intersection(paths))
                self.assertFalse(set(item.get("forbidden_paths", [])).intersection(paths))
                if item.get("expectation") == "no_answer":
                    self.assertEqual(paths, [])


if __name__ == "__main__":
    unittest.main()
