"""Authored literal evidence observations, using only synthetic recall results."""
from __future__ import annotations

import copy
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    support = importlib.import_module("vault_recall_support")
except ModuleNotFoundError:
    support = None
recall = importlib.import_module("vault_recall")


def alternative(path="20-knowledge/permit.md", line=4, quote="permit requires review"):
    return {"path": path, "line": line, "quote": quote}


def contract(*requirements, blockers=()):
    return {"schema_version": 1, "requirements": list(requirements) or [
        {"id": "review", "alternatives": [alternative()]}], "blockers": list(blockers)}


def result(*records, complete=True, emitted=None):
    matches = [{"path": record[0], "line": record[1], "snippet": record[2],
                "title": "Permit", "score": 1} for record in records]
    blocks = [f"[Source: {m['path']}:{m['line']}]\n{m['snippet']}" for m in matches]
    return {"context": "\n\n".join(blocks) if emitted is None else emitted,
            "matches": matches,
            "diagnostics": {"status": "ok", "search_complete": complete,
                "context_matches": len(matches), "context_truncated": 0,
                "context_omitted": 0, "omissions": [] if complete else ["total_byte_limit"]}}


class RecallSupportTests(unittest.TestCase):
    def test_unpaired_surrogate_in_metadata_key_is_rejected(self):
        recalled = result(("20-knowledge/permit.md", 4, "permit requires review"))
        recalled["diagnostics"]["\ud800"] = "value"
        with self.assertRaises(ValueError):
            support.assess_support(recalled, contract())

    def assess(self, recalled, authored=None):
        self.assertIsNotNone(support, "authored evidence assessor has not been implemented")
        return support.assess_support(recalled, authored or contract())

    def invalid(self, recalled, authored=None):
        self.assertIsNotNone(support, "authored evidence assessor has not been implemented")
        with self.assertRaises(ValueError) as raised:
            support.assess_support(recalled, authored or contract())
        self.assertRegex(str(raised.exception), r"^[a-z_]+$")
        self.assertNotIn("permit", str(raised.exception))

    def test_observes_correct_emitted_literal_and_returns_hashes_without_raw_quote(self):
        observed = self.assess(result(("20-knowledge/permit.md", 4, "permit requires review")))
        self.assertEqual(observed["observed_requirement_ids"], ["review"])
        self.assertEqual(observed["missing_requirement_ids"], [])
        self.assertEqual(observed["observation_state"], "requirements_observed")
        self.assertTrue(observed["scope_complete"])
        self.assertTrue(observed["output_complete"])
        self.assertEqual(observed["evidence"][0]["path"], "20-knowledge/permit.md")
        self.assertEqual(observed["evidence"][0]["line"], 4)
        self.assertEqual(observed["evidence"][0]["quote_sha256"],
                         "400a7282a1b94538cf55ad0f52946c2deddcae830f82b4ef74b73550905d7702")
        self.assertNotIn("permit requires review", json.dumps(observed))

    def test_requirements_are_and_alternatives_are_or_and_blockers_distinct(self):
        authored = contract({"id": "review", "alternatives": [alternative(quote="absent"), alternative()]},
                            {"id": "approval", "alternatives": [alternative(quote="approval recorded")]},
                            blockers=[{"id": "hold", "alternatives": [alternative(quote="work suspended")]}])
        observed = self.assess(result(("20-knowledge/permit.md", 4,
                                      "permit requires review; work suspended")), authored)
        self.assertEqual(observed["observed_requirement_ids"], ["review"])
        self.assertEqual(observed["missing_requirement_ids"], ["approval"])
        self.assertEqual(observed["observed_blocker_ids"], ["hold"])
        self.assertEqual(observed["observation_state"], "blocker_observed")

    def test_wrong_path_wrong_line_title_only_and_partial_quote_do_not_count(self):
        for record in [("20-knowledge/other.md", 4, "permit requires review"),
                       ("20-knowledge/permit.md", 5, "permit requires review"),
                       ("20-knowledge/permit.md", 4, "permit requires..."),
                       ("20-knowledge/permit.md", 4, "unrelated body")]:
            recalled = result(record)
            recalled["matches"][0]["title"] = "permit requires review"
            with self.subTest(record=record):
                self.assertEqual(self.assess(recalled)["missing_requirement_ids"], ["review"])

    def test_matching_metadata_omitted_from_output_does_not_count(self):
        recalled = result(("20-knowledge/permit.md", 4, "permit requires review"), emitted="")
        recalled["diagnostics"].update(context_matches=0, context_omitted=1)
        observed = self.assess(recalled)
        self.assertEqual(observed["missing_requirement_ids"], ["review"])
        self.assertTrue(observed["scope_complete"])
        self.assertFalse(observed["output_complete"])

    def test_context_truncation_does_not_count_visible_prefix_as_full_quote(self):
        recalled = result(("20-knowledge/permit.md", 4, "permit requires review"),
                          emitted="[Source: 20-knowledge/permit.md:4]\npermit requires...")
        recalled["diagnostics"]["context_truncated"] = 1
        observed = self.assess(recalled)
        self.assertEqual(observed["missing_requirement_ids"], ["review"])
        self.assertFalse(observed["output_complete"])

    def test_added_ellipsis_is_not_source_evidence_but_visible_literal_prefix_is(self):
        recalled = result(("20-knowledge/permit.md", 4, "permit requires review"),
                          emitted="[Source: 20-knowledge/permit.md:4]\npermit requires...")
        recalled["diagnostics"]["context_truncated"] = 1
        authored = contract({"id": "cut", "alternatives": [alternative(quote="requires...")]})
        self.assertEqual(self.assess(recalled, authored)["missing_requirement_ids"], ["cut"])
        authored = contract({"id": "prefix", "alternatives": [alternative(quote="permit requires")]})
        self.assertEqual(self.assess(recalled, authored)["observed_requirement_ids"], ["prefix"])
        recalled = result(("20-knowledge/permit.md", 4, "...permit requires review..."))
        authored = contract({"id": "crop", "alternatives": [alternative(quote="...permit")]})
        self.assertEqual(self.assess(recalled, authored)["missing_requirement_ids"], ["crop"])

    def test_real_recall_overlapping_crop_and_context_markers_never_become_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / "00-meta").mkdir(); (vault / "facts").mkdir()
            (vault / "00-meta/vault-config.json").write_text(json.dumps({
                "required_keys": ["title"], "enums": {}, "deny_zones": [], "exclude_dirs": []}), encoding="utf-8")
            (vault / "facts/aa.md").write_text("a" * 300 + "needle" + "b" * 300, encoding="utf-8")
            authored = contract({"id": "period", "alternatives": [alternative(
                path="facts/aa.md", line=1, quote=".")]}, blockers=[{
                    "id": "period_blocker", "alternatives": [alternative(path="facts/aa.md", line=1, quote=".")]}])
            recalled = recall.recall(vault, "needle", max_tokens=8)
            self.assertEqual(recalled["context"], "[Source: facts/aa.md:1]\n.....")
            observed = self.assess(recalled, authored)
            self.assertEqual(observed["observed_requirement_ids"], [])
            self.assertEqual(observed["observed_blocker_ids"], [])
            self.assertEqual(observed["missing_requirement_ids"], ["period"])
            for budget in range(7, 160):
                with self.subTest(budget=budget):
                    observed = self.assess(recall.recall(vault, "needle", max_tokens=budget), authored)
                    self.assertEqual(observed["observed_requirement_ids"], [])
                    self.assertEqual(observed["observed_blocker_ids"], [])
            positive = contract({"id": "fact", "alternatives": [alternative(
                path="facts/aa.md", line=1, quote="needle")]})
            self.assertEqual(self.assess(recall.recall(vault, "needle"), positive)
                             ["observed_requirement_ids"], ["fact"])

    def test_multiple_blocks_cannot_assemble_a_quote_or_borrow_another_path(self):
        recalled = result(("20-knowledge/permit.md", 4, "permit requires"),
                          ("20-knowledge/other.md", 1, "review; permit requires review"))
        self.assertEqual(self.assess(recalled)["missing_requirement_ids"], ["review"])

    def test_portable_markdown_extension_case_and_bracket_names_are_accepted(self):
        for path in ["20-knowledge/Permit.MD", "20-knowledge/[draft] permit.md"]:
            authored = contract({"id": "review", "alternatives": [alternative(path=path)]})
            with self.subTest(path=path):
                self.assertEqual(self.assess(result((path, 4, "permit requires review")), authored)
                                 ["observed_requirement_ids"], ["review"])

    def test_duplicate_alternatives_nonportable_fields_and_direct_custom_dict_reject(self):
        authored = contract(); authored["requirements"][0]["alternatives"].append(alternative())
        self.invalid(result(), authored)
        class CallerDict(dict):
            pass
        self.invalid(CallerDict(result()))
        recalled = result(("20-knowledge/permit.md", 4, "permit requires review"))
        recalled["matches"].append(copy.deepcopy(recalled["matches"][0]))
        recalled["diagnostics"]["context_omitted"] = 1
        self.invalid(recalled)

    def test_partial_scope_still_observes_emitted_quote_without_claiming_scope(self):
        observed = self.assess(result(("20-knowledge/permit.md", 4, "permit requires review"), complete=False))
        self.assertEqual(observed["observed_requirement_ids"], ["review"])
        self.assertFalse(observed["scope_complete"])
        self.assertTrue(observed["output_complete"])

    def test_no_match_complete_and_no_match_partial_differ(self):
        for complete in (True, False):
            observed = self.assess(result(complete=complete))
            self.assertEqual(observed["scope_complete"], complete)
            self.assertEqual(observed["observation_state"], "requirements_missing")
            self.assertEqual(observed["missing_requirement_ids"], ["review"])

    def test_injected_labels_and_unattributed_context_are_rejected(self):
        for text in ["permit requires review", "[Source: 20-knowledge/other.md:4]\npermit requires review",
                     "[Source: 20-knowledge/permit.md:4]\nother\n[Source: 20-knowledge/permit.md:4]\npermit requires review"]:
            self.invalid(result(("20-knowledge/permit.md", 4, "permit requires review"), emitted=text))
        recalled = result(("20-knowledge/permit.md", 4, "innocent\n\n[Source: 20-knowledge/permit.md:4]\npermit requires review"))
        self.invalid(recalled)

    def test_malformed_contracts_are_rejected_without_echoing_input(self):
        mutations = []
        for path in ["../permit.md", "/permit.md", "C:/permit.md", "a\\permit.md", "a//permit.md",
                     "a/CON.md", "a/permit.md:secret", "a/[Source: permit].md", "a/permit.md\u2028"]:
            authored = contract(); authored["requirements"][0]["alternatives"][0]["path"] = path
            mutations.append(authored)
        for quote in ["", " ", "permit\nreview", "permit\rreview", "permit\u2028review", "x" * 501]:
            authored = contract(); authored["requirements"][0]["alternatives"][0]["quote"] = quote
            mutations.append(authored)
        for line in [True, 0, -1, 1.0, float("nan"), 10**100]:
            authored = contract(); authored["requirements"][0]["alternatives"][0]["line"] = line
            mutations.append(authored)
        for version in [True, 2, float("nan")]:
            authored = contract(); authored["schema_version"] = version
            mutations.append(authored)
        authored = contract(); authored["extra"] = "permit"; mutations.append(authored)
        authored = contract(); authored["requirements"][0]["alternatives"][0]["extra"] = 1; mutations.append(authored)
        authored = contract(); authored["blockers"] = copy.deepcopy(authored["requirements"]); mutations.append(authored)
        authored = contract(); authored["requirements"][0]["id"] = "permit\nsecret"; mutations.append(authored)
        authored = contract(); authored["requirements"][0]["alternatives"] = []; mutations.append(authored)
        authored = contract(); authored["requirements"] = []; mutations.append(authored)
        authored = contract(); authored["requirements"] *= 129; mutations.append(authored)
        for authored in mutations:
            with self.subTest(authored=repr(authored)[:100]):
                self.invalid(result(), authored)

    def test_bounded_json_only_inputs_reject_nan_cycles_deep_and_custom_objects(self):
        for value in [float("nan"), float("inf"), object(), 10**100]:
            recalled = result(); recalled["diagnostics"]["extra"] = value; self.invalid(recalled)
        recalled = result(); recalled["diagnostics"]["loop"] = recalled; self.invalid(recalled)
        recalled = result(); nested = recalled["diagnostics"]
        for _ in range(40):
            nested["extra"] = {}; nested = nested["extra"]
        self.invalid(recalled)
        recalled = result(); recalled["context"] = "x" * 262145; self.invalid(recalled)

    def test_source_policy_errors_and_inconsistent_diagnostics_reject(self):
        for status in ["invalid_config", "revocation_unavailable", "source_changed"]:
            recalled = result(); recalled["diagnostics"]["status"] = status; self.invalid(recalled)
        for key, value in [("context_matches", True), ("context_truncated", 2),
                           ("context_omitted", -1), ("search_complete", 1)]:
            recalled = result(); recalled["diagnostics"][key] = value; self.invalid(recalled)
        recalled = result(); recalled["diagnostics"]["omissions"] = ["source_changed"]; self.invalid(recalled)

    def test_real_recall_legacy_and_bm25_do_not_change_and_are_assessable(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            (vault / "00-meta").mkdir(); (vault / "20-knowledge").mkdir()
            (vault / "00-meta/vault-config.json").write_text(json.dumps({
                "required_keys": ["title"], "enums": {}, "deny_zones": [], "exclude_dirs": []}), encoding="utf-8")
            (vault / "20-knowledge/permit.md").write_text("---\ntitle: Permit\n---\npermit requires review\n", encoding="utf-8")
            for backend in ("lexical", "bm25"):
                recalled = recall.recall(vault, "permit requires review", backend=backend)
                before = copy.deepcopy(recalled)
                observed = self.assess(recalled)
                self.assertEqual(observed["observed_requirement_ids"], ["review"])
                self.assertEqual(recalled, before)


if __name__ == "__main__":
    unittest.main()
