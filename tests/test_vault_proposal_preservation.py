"""Read-only literal preservation check for one lesson proposal (preserve-check).

The stored original and candidate of a proposal are compared by occurrence
counts: date and number tokens with the vault_compile_quality extraction, and
name labels, wiki targets, declared claims and declared phrases with word
boundaries. Undeclared drops are unexpected removals, declared removals must
lower their counts and declared additions must raise theirs. Nothing in the
vault or the receipt changes, and the result never permits or blocks apply.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
SCRIPT = SCRIPTS / "vault_proposals.py"

ORIGINAL = ("# 규칙\n"
            "승인 항목: 로그인 기록은 2026-10-01부터 90일 보존한다.\n"
            "- 담당 [[보안 정책]]\n"
            "- 한도 15건\n")
CANDIDATE = ("# 규칙\n"
             "승인 항목: 로그인 기록은 2026-10-01부터 30일 보존한다.\n"
             "- 한도 15건\n"
             "- 검토일 2026-10-23\n")


def _quality():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("preservation_quality", SCRIPTS / "vault_compile_quality.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


quality = _quality()


def pairs(rows):
    return {(row["kind"], row["literal"]) for row in rows}


class PreservationCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.write("00-meta/vault-config.json", json.dumps({"required_keys": ["title"], "enums": {}}).encode())
        self.target = self.write("CLAUDE.md", ORIGINAL.encode())
        self.write("00-meta/candidate.md", CANDIDATE.encode())

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(self.vault), *args],
                                capture_output=True, encoding="utf-8")
        return result.returncode, json.loads(result.stdout)

    def propose(self, original=None, candidate=None):
        if original is not None:
            self.target.write_bytes(original.encode())
        if candidate is not None:
            self.write("00-meta/candidate.md", candidate.encode())
        code, result = self.run_cli("propose", "--target", "CLAUDE.md", "--candidate", "00-meta/candidate.md",
                                    "--lesson", "L-001", "--summary", "보존 기간 정정")
        self.assertEqual(code, 0, result)
        return result["id"]

    def snapshot(self):
        return {str(path): path.read_bytes() for path in self.vault.rglob("*") if path.is_file()}

    def test_undeclared_removals_need_review_and_nothing_changes(self):
        proposal_id = self.propose()
        before = self.snapshot()
        code, result = self.run_cli("preserve-check", proposal_id)
        self.assertEqual(self.snapshot(), before, "preserve-check is read-only")
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["review_reasons"], ["unexpected_removals"])
        # Wiki targets share the 'name' kind with name labels in the extractor.
        self.assertEqual(pairs(result["unexpected_removals"]), {("number", "90"), ("name", "보안 정책")})
        self.assertTrue(all((row["original_count"], row["candidate_count"]) == (1, 0)
                            for row in result["unexpected_removals"]))
        self.assertEqual(result["authority"], "preservation_observation_only_not_permission_or_truth")
        self.assertEqual(result["semantic_authority"], "advisory_unverified")
        receipt = json.loads((self.vault / "00-meta/proposals" / (proposal_id + ".json")).read_bytes())
        self.assertEqual((result["original_sha256"], result["candidate_sha256"]),
                         (receipt["base_sha256"], receipt["candidate_sha256"]))
        self.assertEqual((result["id"], result["target"], result["proposal_status"]),
                         (proposal_id, "CLAUDE.md", "proposed"))

    def test_declared_removals_and_additions_complete_the_check(self):
        proposal_id = self.propose()
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90",
                                    "--expect-removed", "보안 정책", "--expect-added", "30일",
                                    "--claim", "로그인 기록은")
        self.assertEqual((code, result["status"], result["review_reasons"]), (0, "complete", []), result)
        self.assertEqual(result["unexpected_removals"], [])
        self.assertTrue(all(row["removed"] and row["in_original"] for row in result["declared_removals"]))
        addition = result["expected_additions"][0]
        self.assertEqual((addition["literal"], addition["added"], addition["in_candidate"], addition["in_original"]),
                         ("30일", True, True, False))
        self.assertEqual(addition["counts"], [{"kind": "number", "literal": "30",
                                               "original_count": 0, "candidate_count": 1}])
        self.assertEqual(result["facts_checked"], result["facts_preserved"] + 2)

    def test_declarations_with_a_unit_are_judged_by_their_number_token(self):
        proposal_id = self.propose()
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90일",
                                    "--expect-removed", "보안 정책", "--expect-added", "30일")
        self.assertEqual((code, result["status"]), (0, "complete"), result)
        self.assertEqual(result["declared_removals"][0]["counts"][0]["literal"], "90")

    def test_declared_change_that_did_not_happen_needs_review(self):
        proposal_id = self.propose()
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90",
                                    "--expect-removed", "보안 정책", "--expect-removed", "15건",
                                    "--expect-added", "60일")
        self.assertEqual(code, 1)
        self.assertEqual(set(result["review_reasons"]), {"declared_removal_not_observed", "expected_addition_missing"})
        kept = [row for row in result["declared_removals"] if not row["removed"]]
        self.assertEqual([row["literal"] for row in kept], ["15건"])

    def test_an_addition_must_raise_the_count(self):
        proposal_id = self.propose()
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90",
                                    "--expect-removed", "보안 정책", "--expect-added", "15건")
        self.assertEqual(code, 1)
        self.assertEqual(result["review_reasons"], ["expected_addition_missing"])
        addition = result["expected_additions"][0]
        self.assertEqual((addition["in_original"], addition["in_candidate"], addition["added"]), (True, True, False))

    def test_changing_one_of_repeated_values_is_not_silent(self):
        original, candidate = "# 기한\nA 90일, B 90일\n", "# 기한\nA 90일, B 30일\n"
        proposal_id = self.propose(original, candidate)
        code, result = self.run_cli("preserve-check", proposal_id)
        self.assertEqual(code, 1)
        self.assertEqual(result["unexpected_removals"], [{"kind": "number", "literal": "90",
                                                          "original_count": 2, "candidate_count": 1}])
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90", "--expect-added", "30일")
        self.assertEqual((code, result["status"]), (0, "complete"), result)

    def test_number_tokens_do_not_match_inside_longer_numbers(self):
        # A wrong fix (90 -> 130) is not the declared addition 30.
        proposal_id = self.propose("# 기한\n기한 90일\n", "# 기한\n기한 130일\n")
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90", "--expect-added", "30일")
        self.assertEqual((code, result["review_reasons"]), (1, ["expected_addition_missing"]))
        # A correct fix while an unrelated 130 exists is not a false alarm.
        proposal_id = self.propose("# 기한\n기한 90일, 보관 130일\n", "# 기한\n기한 30일, 보관 130일\n")
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90", "--expect-added", "30일")
        self.assertEqual((code, result["status"]), (0, "complete"), result)

    def test_a_declared_claim_catches_rewrites_and_appended_negations(self):
        rewritten = ORIGINAL.replace("승인 항목: 로그인 기록은", "승인 항목: 접속 기록은")
        proposal_id = self.propose(candidate=rewritten)
        code, plain = self.run_cli("preserve-check", proposal_id)
        self.assertEqual((code, plain["status"]), (0, "complete"), "literals alone miss the rewrite")
        code, claimed = self.run_cli("preserve-check", proposal_id, "--claim", "로그인 기록은 2026-10-01부터")
        self.assertEqual(code, 1)
        self.assertEqual(pairs(claimed["unexpected_removals"]), {("claim", "로그인 기록은 2026-10-01부터")})
        proposal_id = self.propose("# 규칙\n규칙: 관리자 승인 필요\n", "# 규칙\n규칙: 관리자 승인 필요없음\n")
        code, negated = self.run_cli("preserve-check", proposal_id, "--claim", "관리자 승인 필요")
        self.assertEqual((code, negated["review_reasons"]), (1, ["unexpected_removals"]))

    def test_a_spaced_negation_needs_the_clause_end_in_the_claim(self):
        proposal_id = self.propose("# 규칙\n규칙: 관리자 승인 필요.\n", "# 규칙\n규칙: 관리자 승인 필요 없음.\n")
        code, spaced = self.run_cli("preserve-check", proposal_id, "--claim", "관리자 승인 필요")
        self.assertEqual((code, spaced["status"]), (0, "complete"), "a separate negation word keeps the phrase")
        code, clause = self.run_cli("preserve-check", proposal_id, "--claim", "관리자 승인 필요.")
        self.assertEqual((code, clause["review_reasons"]), (1, ["unexpected_removals"]))
        self.assertEqual(pairs(clause["unexpected_removals"]), {("claim", "관리자 승인 필요.")})

    def test_conflicting_and_invalid_declarations_are_errors(self):
        proposal_id = self.propose()
        for args, error in ((("--claim", "원문에 없는 문장"), "claim_not_in_original"),
                            (("--claim", "로그인 기록은", "--expect-removed", "로그인 기록은"), "conflicting_declarations"),
                            (("--expect-added", "30일", "--expect-removed", "30일"), "conflicting_declarations")):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli("preserve-check", proposal_id, *args), (1, {"error": error}))
        code, result = self.run_cli("preserve-check", "0" * 32)
        self.assertEqual(code, 1)
        self.assertIn("error", result)

    def test_bom_and_frontmatter_are_outside_the_comparison(self):
        original = "﻿---\ntitle: 규칙\nupdated: 2026-10-01\n---\n# 규칙\n한도 15건\n"
        candidate = original.replace("updated: 2026-10-01", "updated: 2026-10-23")
        self.write("rules.md", original.encode())
        self.write("00-meta/candidate.md", candidate.encode())
        code, created = self.run_cli("propose", "--target", "rules.md", "--candidate", "00-meta/candidate.md",
                                     "--lesson", "L-002", "--summary", "갱신일만 변경")
        self.assertEqual(code, 0, created)
        code, result = self.run_cli("preserve-check", created["id"])
        self.assertEqual((code, result["status"]), (0, "complete"), result)

    def test_existing_lock_does_not_block_the_read_only_check(self):
        proposal_id = self.propose()
        self.write("00-meta/proposals/.lock", b"held by another writer")
        before = self.snapshot()
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90",
                                    "--expect-removed", "보안 정책")
        self.assertEqual((code, result["status"]), (0, "complete"), result)
        self.assertEqual(self.snapshot(), before)

    def test_applied_proposals_are_checked_from_the_stored_texts(self):
        proposal_id = self.propose()
        code, result = self.run_cli("apply", proposal_id, "--approve")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.target.read_bytes(), CANDIDATE.encode())
        code, result = self.run_cli("preserve-check", proposal_id, "--expect-removed", "90",
                                    "--expect-removed", "보안 정책")
        self.assertEqual((code, result["status"], result["proposal_status"]), (0, "complete", "applied"))

    def test_library_function_matches_the_cli_and_validates_input(self):
        observed = quality.check_preservation(ORIGINAL, CANDIDATE, expected_removals=["90", "보안 정책"])
        self.assertEqual(observed["status"], "complete")
        self.assertEqual(observed["original_sha256"], hashlib.sha256(ORIGINAL.encode()).hexdigest())
        self.assertEqual(quality.check_preservation("# 규칙\n[[90]]\n", "# 규칙\n없음\n",
                                                    expected_removals=["90"])["status"], "complete")
        for kwargs in ({"required_claims": [""]}, {"expected_removals": "90"}, {"expected_additions": [1]},
                       {"required_claims": ["x"] * (quality.MAX_CLAIMS + 1)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(quality.QualityError):
                quality.check_preservation(ORIGINAL, CANDIDATE, **kwargs)
        with self.assertRaises(quality.QualityError):
            quality.check_preservation("가" * (quality.MAX_FILE_BYTES + 1), CANDIDATE)


if __name__ == "__main__":
    unittest.main()
