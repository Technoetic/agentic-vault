"""Opt-in gold-removal twins for the recall evaluator (--gold-removal-twins).

Every answerable headline query runs a second time with its gold notes removed
(expected_paths, every relevance path graded above 0 and every note holding one
of its required claims), which turns it into a no-answer probe. Protocol after
arXiv 2610.05348 (v1); only the twin protocol and its recall at a false-refusal
budget are borrowed. Without the flag every report, the CLI output and the exit
code stay as before.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import importlib.util
import io
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RECALL_HARD = ROOT / "tests" / "fixtures" / "recall_hard"
ADVANCED = ROOT / "tests" / "fixtures" / "recall_advanced"
HARD_CI = ["--compare", "--expand-query", "--query-mapping", "00-meta/query-map.txt", "--top-k", "3",
           "--max-tokens", "1500", "--max-no-answer-false-positive-rate", "0.25", "--max-stale-top-1-rate", "0"]
TWIN_KEY = "gold_removal_twins"
KEY = b"TWINKEYMARK:" + bytes(range(40, 80))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluator = _load("gold_removal_twins_recall_evaluator", ROOT / "scripts" / "evaluate_recall.py")


def run_main(argv):
    """main() with a frozen clock so two runs print byte-comparable JSON."""
    counter = itertools.count()
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(evaluator.time, "perf_counter", new=lambda: next(counter) * 0.001), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = evaluator.main([str(value) for value in argv])
    return code, stdout.getvalue(), stderr.getvalue()


def without_twins(value):
    if isinstance(value, dict):
        return {key: without_twins(item) for key, item in value.items() if key != TWIN_KEY}
    if isinstance(value, list):
        return [without_twins(item) for item in value]
    return value


def all_keys(value) -> set:
    if isinstance(value, dict):
        return set(value).union(*(all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value))
    return set()


def hmac_hex(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def by_id(section: dict) -> dict:
    return {row["id"]: row for row in section["rows"]}


PUMP = "20-knowledge/Pump seal procedure.md"
PUMP_CHECKLIST = "20-knowledge/Pump seal checklist.md"
PUMP_COPY = "20-knowledge/archive/Pump seal copy.md"
VALVE = "20-knowledge/current/Valve torque.md"
VALVE_OLD = "20-knowledge/history/Valve torque old.md"
RELAY = "20-knowledge/Relay test.md"
RELAY_GLOSSARY = "20-knowledge/glossary/Relay.md"
LOOSE_ROOT = "Loose note.md"
NOTES = {
    PUMP: "# Pump seal procedure\nDrain the casing first, then replace the pump seal.\n",
    # A near-duplicate that paraphrases the claim; another query grades it.
    PUMP_CHECKLIST: "# Pump seal checklist\nPump seal replacement: empty the casing before work.\n",
    # Holds the claim literally but nobody graded it: the claim scan removes it.
    PUMP_COPY: "# Pump seal copy\nDrain the casing first, then replace the pump seal.\n",
    VALVE: "# Valve torque\nTighten the valve flange to forty newton metres.\n",
    VALVE_OLD: "# Valve torque old\nTighten the valve flange to thirty newton metres.\n",
    "20-knowledge/Compressor oil.md": "# Compressor oil\nChange the compressor oil every quarter.\n",
    "20-knowledge/압축기 오일.md": "# 압축기 오일\n압축기 오일은 분기마다 교체한다.\n",
    RELAY: "# Relay test\nTest the protection relay every month.\n",
    RELAY_GLOSSARY: "# Relay\nA relay is a switch.\n",
    "20-knowledge/릴레이 시험.md": "# 릴레이 시험\n보호 릴레이를 매월 시험한다.\n",
    # A vault-root note is a one-segment exclusion rule: it also removes the same name elsewhere.
    LOOSE_ROOT: "# Loose note\nLoose fastener register.\n",
    "20-knowledge/Loose note.md": "# Loose note\nAnother loose fastener register.\n",
    # Deny zone and excluded directory: never listed or read, by retrieval or by the claim scan.
    "private/Pump seal secret.md": "# Pump seal secret\nDrain the casing first DENYTOKEN.\n",
    "scratch/Pump seal draft.md": "# Pump seal draft\nDrain the casing first DENYTOKEN draft.\n",
}
CLAIM = "drain the casing first"


def query(index: int, text: str, expected: list, language: str = "en", **extra) -> dict:
    return {"id": f"TWINID{index:02d}", "language": language, "scenario": f"TWINSCEN{index % 2}",
            "query": text, "expected_paths": expected, **extra}


QUERIES = [
    query(0, "pump seal replacement", [PUMP], required_claims=[CLAIM]),
    query(1, "pump seal checklist", [PUMP_CHECKLIST], required_claims=[CLAIM]),
    query(2, "valve torque", [VALVE], stale_paths=[VALVE_OLD], relevance={VALVE: 3, VALVE_OLD: 0},
          required_claims=["forty newton metres"]),
    query(3, "압축기 오일", ["20-knowledge/압축기 오일.md"], language="ko"),
    query(4, "relay test", [RELAY]),
    query(5, "loose fastener register", [LOOSE_ROOT]),
    query(6, "zebra harmonica", [], expectation="no_answer"),
    query(7, "compressor oil", ["20-knowledge/Compressor oil.md"], inspected=True),
    query(8, "릴레이 시험", ["20-knowledge/릴레이 시험.md"], language="ko"),
    query(9, "compressor quarter", ["20-knowledge/Compressor oil.md"]),
    query(10, "protection relay", [RELAY], relevance={RELAY: 3, RELAY_GLOSSARY: 1}),
    query(11, "quantum teleportation", [], expectation="no_answer"),
]


class TwinFixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.fixture = self.root / "fixture"
        self.vault = self.fixture / "vault"
        self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["private"], "exclude_dirs": ["scratch"]}))
        for relative, text in NOTES.items():
            self.write(relative, text)
        self.queries = copy.deepcopy(QUERIES)
        self.save()

    def write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def save(self) -> None:
        (self.fixture / "queries.json").write_text(json.dumps({"queries": self.queries}, ensure_ascii=False),
                                                   encoding="utf-8")

    def twins(self, *extra) -> tuple[int, dict, str]:
        code, stdout, stderr = run_main(["--fixture", self.fixture, "--min-recall-at-3", "0", "--min-mrr", "0",
                                         "--gold-removal-twins", *extra])
        return code, json.loads(stdout), stderr


class DefaultOutputTests(unittest.TestCase):
    def test_options_are_off_by_default_and_add_nothing(self):
        args = evaluator._parser().parse_args([])
        self.assertFalse(args.gold_removal_twins)
        self.assertEqual(args.redact_row_order, "gold")
        code, stdout, stderr = run_main([])
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse({TWIN_KEY, "row_order"} & all_keys(json.loads(stdout)))

    def test_flag_only_adds_a_section_to_every_arm(self):
        # Four arms (baseline, expansion_ablation, improved, render_matched) with the CI gates of this fixture;
        # its queries carry as_of, backend, expand_links and external-candidate options.
        argv = ["--fixture", ADVANCED, "--compare", "--expand-query", "--render-matched",
                "--max-no-answer-false-positive-rate", "0", "--max-forbidden-exposure-rate", "0",
                "--max-stale-top-1-rate", "0"]
        plain_code, plain_out, plain_err = run_main(argv)
        flag_code, flag_out, flag_err = run_main([*argv, "--gold-removal-twins"])
        self.assertEqual((flag_code, flag_err), (plain_code, plain_err))
        flagged = json.loads(flag_out)
        self.assertEqual(json.dumps(without_twins(flagged), ensure_ascii=False, indent=2) + "\n", plain_out)
        for arm in ("baseline", "expansion_ablation", "improved", "render_matched"):
            with self.subTest(arm=arm):
                # Appended to each arm; main() adds the gated arm's optional_gates afterwards.
                tail = [TWIN_KEY, "optional_gates"] if arm == "improved" else [TWIN_KEY]
                self.assertEqual(list(flagged[arm])[-len(tail):], tail)
                section = flagged[arm][TWIN_KEY]
                self.assertEqual((section["twin_count"], section["twin_error_count"]), (14, 0))
                self.assertEqual([row["id"] for row in section["rows"]],
                                 [row["id"] for row in flagged[arm]["per_query"] if row["expectation"] == "answerable"])
        self.assertNotIn(TWIN_KEY, flagged)
        self.assertEqual(flagged["improved"]["optional_gates"], json.loads(plain_out)["improved"]["optional_gates"])

    def test_input_report_is_not_mutated(self):
        report = evaluator.evaluate(ADVANCED)
        original = copy.deepcopy(report)
        extended = evaluator.with_gold_removal_twins(report, ADVANCED)
        self.assertEqual(report, original)
        self.assertEqual(without_twins(extended), report)
        for invalid in ([], {}, {"baseline": {}}):
            with self.subTest(report=invalid), self.assertRaises(ValueError):
                evaluator.with_gold_removal_twins(invalid, ADVANCED)


class RecallHardTwinTests(unittest.TestCase):
    """Measured on recall_hard (56 answerable of 64 queries), legacy top-3 and the CI compare options."""

    @classmethod
    def setUpClass(cls):
        cls.legacy = evaluator.evaluate(RECALL_HARD)
        cls.section = evaluator.with_gold_removal_twins(cls.legacy, RECALL_HARD)[TWIN_KEY]

    def test_legacy_top3_twin_counts_are_pinned(self):
        section = self.section
        self.assertEqual({key: section[key] for key in (
            "twin_count", "complete_empty_query_count", "returned_candidates_query_count",
            "false_positive_query_count", "possibly_still_answerable_query_count", "stale_only_query_count",
            "stale_exposed_query_count", "twin_error_count", "excluded_path_count", "removed_note_count")},
            {"twin_count": 56, "complete_empty_query_count": 26, "returned_candidates_query_count": 30,
             "false_positive_query_count": 30, "possibly_still_answerable_query_count": 0,
             "stale_only_query_count": 0, "stale_exposed_query_count": 4, "twin_error_count": 0,
             "excluded_path_count": 104, "removed_note_count": 104})
        self.assertEqual(section["removed_path_counts"],
                         {"expected": 56, "graded_beyond_expected": 48, "claim_bearing_beyond_graded": 0})
        self.assertEqual({key: (value["false_positive_query_count"], value["twin_count"])
                          for key, value in section["by_scenario"].items()},
                         {"mixed": (6, 12), "particles": (3, 12), "short": (6, 12), "spacing": (11, 12),
                          "update": (4, 8)})
        self.assertEqual({key: (value["false_positive_query_count"], value["twin_count"])
                          for key, value in section["by_language"].items()}, {"en": (19, 28), "ko": (11, 28)})
        self.assertEqual(set(section["by_scenario"]["update"]), set(section["by_language"]["en"]))
        self.assertAlmostEqual(section["false_positive_rate"], 30 / 56)
        point = section["operating_point"]
        self.assertEqual((point["trigger"], point["twin_refusal_count"], point["twin_count"],
                          point["answerable_false_refusal_count"], point["answerable_query_count"]),
                         ("empty result (no abstention trigger exists)", 26, 56, 13, 56))
        self.assertAlmostEqual(point["twin_refusal_recall"], 26 / 56)
        self.assertIn("not a curve", point["note"])
        self.assertIn("n = 56 twins", point["note"])
        self.assertIn("at most 2 false refusals", point["note"])
        self.assertEqual((section["note_scan"]["listed_note_count"], section["note_scan"]["notes_read"]), (27, 27))

    def test_every_relevance_path_above_zero_is_removed(self):
        rows = {row["id"]: row for row in self.legacy["per_query"]}
        twins = by_id(self.section)
        self.assertEqual(set(twins), {key for key, row in rows.items() if row["expectation"] == "answerable"})
        for query_id, twin in twins.items():
            with self.subTest(query=query_id):
                labels = rows[query_id]
                graded = {path for path, grade in labels["relevance"].items() if grade > 0}
                self.assertEqual(set(twin["excluded_sources"]), set(labels["expected_paths"]) | graded)
                # A stale note graded 0 is never removed; returning it is stale exposure, not an answer.
                self.assertFalse(set(twin["excluded_sources"]) & set(labels["stale_paths"]))
                self.assertFalse(set(twin["retrieved_paths"]) & (set(labels["expected_paths"]) | graded))
        self.assertEqual(twins["en-update-01"]["stale_retrieved_paths"],
                         ["20-knowledge/history/Cedar recovery procedure old.md"])
        self.assertEqual(twins["en-update-01"]["outcome"], "returned_candidates")

    def test_removing_expected_paths_only_leaves_answerable_twins(self):
        # The deep-verification probe: expected-only twins still return 36/56, and 10 of those return a
        # graded overview note, which the near-duplicate diagnostic flags instead of counting a false positive.
        with mock.patch.object(evaluator, "_twin_exclusions",
                               side_effect=lambda item, *args, **kwargs: sorted(item["expected_paths"])):
            section = evaluator.with_gold_removal_twins(self.legacy, RECALL_HARD)[TWIN_KEY]
        self.assertEqual((section["returned_candidates_query_count"], section["possibly_still_answerable_query_count"],
                          section["false_positive_query_count"]), (36, 10, 26))
        self.assertEqual(len(section["possibly_still_answerable_ids"]), 10)
        for row in section["rows"]:
            if row["possibly_still_answerable"]:
                self.assertTrue(row["residual_graded_paths"])
                self.assertTrue(all("/primers/" in path for path in row["residual_graded_paths"]))

    def test_noop_exclusion_reproduces_the_legacy_rows(self):
        # The twin reader (retrieve with excluded_sources) is the legacy reader when nothing is removed.
        with mock.patch.object(evaluator, "_twin_exclusions", return_value=["zz-none/none.md"]):
            section = evaluator.with_gold_removal_twins(self.legacy, RECALL_HARD)[TWIN_KEY]
        rows = {row["id"]: row for row in self.legacy["per_query"]}
        self.assertEqual(section["twin_count"], 56)
        for twin in section["rows"]:
            with self.subTest(query=twin["id"]):
                self.assertEqual(twin["retrieved_paths"], rows[twin["id"]]["retrieved_paths"])
                self.assertEqual(twin["removed_note_count"], 0)

    def test_ci_compare_arms_each_get_their_own_twins(self):
        code, stdout, stderr = run_main(["--fixture", RECALL_HARD, *HARD_CI, "--gold-removal-twins"])
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        observed = {arm: tuple(report[arm][TWIN_KEY][key] for key in (
            "complete_empty_query_count", "false_positive_query_count", "stale_exposed_query_count",
            "twin_error_count")) + (report[arm][TWIN_KEY]["operating_point"]["answerable_false_refusal_count"],)
            for arm in ("baseline", "expansion_ablation", "improved")}
        self.assertEqual(observed, {"baseline": (26, 30, 4, 0, 13), "expansion_ablation": (26, 30, 0, 0, 13),
                                    "improved": (24, 32, 0, 0, 0)})
        # The advanced arms reuse each query's own options (as_of on the update queries).
        improved = by_id(report["improved"][TWIN_KEY])
        self.assertEqual(improved["en-update-01"]["outcome"], "returned_candidates")
        self.assertEqual(improved["en-update-01"]["stale_retrieved_paths"], [])

    def test_split_twins_cover_the_evaluated_split_only(self):
        heldout = set(json.loads((RECALL_HARD / "split.json").read_text(encoding="utf-8"))["heldout"])
        code, stdout, stderr = run_main(["--fixture", RECALL_HARD, "--split", "heldout", "--min-recall-at-3", "0",
                                         "--min-mrr", "0", "--gold-removal-twins"])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        answerable = {row["id"] for row in report["per_query"] if row["expectation"] == "answerable"}
        self.assertEqual(set(by_id(report[TWIN_KEY])), answerable)
        self.assertTrue(answerable <= heldout)
        self.assertEqual(report[TWIN_KEY]["twin_count"], 28)

    def test_derive_split_refuses_the_flag(self):
        code, stdout, stderr = run_main(["--fixture", RECALL_HARD, "--derive-split", "--gold-removal-twins"])
        self.assertEqual((code, stdout, stderr.strip()),
                         (2, "", "--derive-split cannot be combined with --gold-removal-twins"))


class TwinOutcomeTests(TwinFixtureCase):
    def test_outcomes_partition_the_twins(self):
        code, report, stderr = self.twins()
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("gold-removal twins note", stderr)
        section = report[TWIN_KEY]
        rows = by_id(section)
        # No twin for no_answer (06, 11) or inspected (07) queries.
        self.assertEqual(sorted(rows), [f"TWINID{index:02d}" for index in (0, 1, 2, 3, 4, 5, 8, 9, 10)])
        self.assertEqual({query_id: row["outcome"] for query_id, row in rows.items()}, {
            "TWINID00": "returned_candidates", "TWINID01": "refused_empty_complete", "TWINID02": "stale_only",
            "TWINID03": "refused_empty_complete", "TWINID04": "returned_candidates",
            "TWINID05": "refused_empty_complete", "TWINID08": "refused_empty_complete",
            "TWINID09": "refused_empty_complete", "TWINID10": "refused_empty_complete"})
        self.assertEqual({key: section[key] for key in (
            "twin_count", "complete_empty_query_count", "returned_candidates_query_count",
            "false_positive_query_count", "possibly_still_answerable_query_count", "stale_only_query_count",
            "stale_exposed_query_count", "twin_error_count")},
            {"twin_count": 9, "complete_empty_query_count": 6, "returned_candidates_query_count": 2,
             "false_positive_query_count": 1, "possibly_still_answerable_query_count": 1,
             "stale_only_query_count": 1, "stale_exposed_query_count": 1, "twin_error_count": 0})
        self.assertEqual(sum(section[key] for key in (
            "complete_empty_query_count", "false_positive_query_count", "possibly_still_answerable_query_count",
            "stale_only_query_count", "twin_error_count")), section["twin_count"])
        self.assertTrue(all(row["outcome"] in evaluator.TWIN_OUTCOMES for row in section["rows"]))
        self.assertTrue(all(row["twin_error_reason"] is None for row in section["rows"]))
        self.assertEqual(rows["TWINID02"]["retrieved_paths"], [VALVE_OLD])
        self.assertEqual(rows["TWINID04"]["retrieved_paths"], [RELAY_GLOSSARY])
        self.assertEqual(rows["TWINID10"]["removed_path_counts"],
                         {"expected": 1, "graded_beyond_expected": 1, "claim_bearing_beyond_graded": 0})
        self.assertEqual(section["by_language"]["ko"]["twin_count"], 2)

    def test_near_duplicate_graded_by_a_query_with_the_same_claim_is_not_a_false_positive(self):
        _, report, _ = self.twins()
        section = report[TWIN_KEY]
        twin = by_id(section)["TWINID00"]
        # The literal claim copy is removed with the gold note; the paraphrase graded by TWINID01 comes back.
        self.assertEqual(twin["excluded_sources"], [PUMP_COPY, PUMP])
        self.assertEqual(twin["removed_path_counts"],
                         {"expected": 1, "graded_beyond_expected": 0, "claim_bearing_beyond_graded": 1})
        self.assertEqual((twin["retrieved_paths"], twin["residual_graded_paths"], twin["covered_claims"]),
                         ([PUMP_CHECKLIST], [PUMP_CHECKLIST], []))
        self.assertTrue(twin["possibly_still_answerable"])
        self.assertEqual(section["possibly_still_answerable_ids"], ["TWINID00"])
        # Without the shared claim the same return is a plain false positive.
        self.queries[1]["required_claims"] = ["empty the casing before work"]
        self.save()
        _, report, _ = self.twins()
        twin = by_id(report[TWIN_KEY])["TWINID00"]
        self.assertEqual((twin["retrieved_paths"], twin["possibly_still_answerable"], twin["residual_graded_paths"]),
                         ([PUMP_CHECKLIST], False, []))
        self.assertEqual(report[TWIN_KEY]["possibly_still_answerable_ids"], [])
        # TWINID01's own claim now removes only its gold note, so its twin also returns notes.
        self.assertEqual(by_id(report[TWIN_KEY])["TWINID01"]["excluded_sources"], [PUMP_CHECKLIST])
        self.assertEqual(report[TWIN_KEY]["false_positive_query_count"], 3)

    def test_claim_found_in_the_twin_context_marks_it_possibly_answerable(self):
        # Safety net behind the claim scan: with the scan bypassed, the literal copy comes back
        # and its cited line holds the claim.
        self.queries[0]["query"] = "drain casing pump seal"
        self.save()
        with mock.patch.object(evaluator, "_twin_exclusions",
                               side_effect=lambda item, *args, **kwargs: sorted(item["expected_paths"])):
            _, report, _ = self.twins()
        twin = by_id(report[TWIN_KEY])["TWINID00"]
        self.assertEqual(twin["retrieved_paths"][0], PUMP_COPY)
        self.assertEqual(twin["covered_claims"], [CLAIM])
        self.assertTrue(twin["possibly_still_answerable"])
        self.assertEqual(twin["outcome"], "returned_candidates")

    def test_claim_scan_never_lists_or_reads_deny_zones_or_excluded_directories(self):
        opened, scanned = [], []
        real_open, real_scandir = os.open, os.scandir

        def recording_open(path, *args, **kwargs):
            opened.append(os.path.normcase(os.fspath(path)))
            return real_open(path, *args, **kwargs)

        def recording_scandir(path=".", *args, **kwargs):
            scanned.append(os.path.normcase(os.fspath(path)))
            return real_scandir(path, *args, **kwargs)

        with mock.patch("os.open", side_effect=recording_open), \
                mock.patch("os.scandir", side_effect=recording_scandir), \
                mock.patch.object(evaluator.Path, "rglob", side_effect=AssertionError("rglob")):
            code, report, stderr = self.twins("--compare")
        self.assertEqual(code, 0, stderr)
        forbidden = [os.path.normcase(str(self.vault / part)) for part in ("private", "scratch")]
        self.assertEqual([path for path in opened + scanned if path.startswith(tuple(forbidden))], [])
        self.assertTrue(any(path.endswith(os.path.normcase("Pump seal copy.md")) for path in opened))
        scan = report["improved"][TWIN_KEY]["note_scan"]
        self.assertEqual((scan["status"], scan["search_complete"], scan["listed_note_count"]), ("ok", True, 12))
        self.assertEqual(report["baseline"][TWIN_KEY]["note_scan"], scan)
        for arm in ("baseline", "improved"):
            for twin in report[arm][TWIN_KEY]["rows"]:
                self.assertFalse([path for path in twin["excluded_sources"] + twin["retrieved_paths"]
                                  if path.startswith(("private/", "scratch/"))])
        self.assertNotIn("DENYTOKEN", json.dumps(report) + stderr)

    def test_without_required_claims_no_note_is_read(self):
        for item in self.queries:
            item.pop("required_claims", None)
        self.save()
        recall_module = evaluator._load_recall_module()
        with mock.patch.object(recall_module, "_read_markdown", side_effect=AssertionError("read")):
            scan = evaluator._twin_note_scan(recall_module, self.vault, read_texts=False)
        self.assertIsNone(scan["texts"])
        self.assertEqual((scan["summary"]["notes_read"], scan["summary"]["listed_note_count"]), (0, 12))
        _, report, _ = self.twins()
        self.assertFalse(report[TWIN_KEY]["note_scan"]["claim_texts_read"])

    def test_vault_root_paths_follow_engine_zone_rules(self):
        _, report, _ = self.twins()
        twin = by_id(report[TWIN_KEY])["TWINID05"]
        self.assertEqual((twin["excluded_path_count"], twin["removed_note_count"]), (1, 2))
        self.assertEqual(twin["outcome"], "refused_empty_complete")
        self.assertIn("one-segment path", report[TWIN_KEY]["definitions"]["removed_notes"])

    def test_more_than_fifty_exclusions_is_a_twin_error_never_a_refusal(self):
        for index in range(51):
            self.write(f"20-knowledge/bulk/Copy {index:02d}.md", f"# Copy {index:02d}\nDrain the casing first.\n")
        plain_code, plain_out, plain_err = run_main(["--fixture", self.fixture, "--min-recall-at-3", "0",
                                                     "--min-mrr", "0"])
        code, report, stderr = self.twins()
        self.assertEqual(code, plain_code)
        section = report[TWIN_KEY]
        rows = by_id(section)
        for query_id in ("TWINID00", "TWINID01"):
            with self.subTest(query=query_id):
                self.assertEqual((rows[query_id]["outcome"], rows[query_id]["twin_error_reason"]),
                                 ("twin_error", "retrieval_not_ok"))
                self.assertEqual(rows[query_id]["diagnostics"]["status"], "invalid_options")
                self.assertGreater(rows[query_id]["excluded_path_count"], 51)
                # The row shows one entry more than retrieve() accepts, not every excluded path.
                self.assertEqual(len(rows[query_id]["excluded_sources"]), 51)
                self.assertFalse(rows[query_id]["possibly_still_answerable"])
        self.assertEqual((section["twin_error_count"], section["complete_empty_query_count"]), (2, 5))
        self.assertEqual(section["operating_point"]["twin_refusal_count"], 5)
        self.assertEqual(stderr, plain_err + "gold-removal twins note (non-blocking): 2 of 9 twins could not be "
                                             "measured (twin_error) and are not counted as refusals\n")
        self.assertEqual(without_twins(report), json.loads(plain_out))

    def test_incomplete_note_scan_makes_claim_twins_errors(self):
        real_scan = evaluator._twin_note_scan

        def incomplete(*args, **kwargs):
            scan = real_scan(*args, **kwargs)
            scan["summary"].update(search_complete=False, omissions=["total_byte_limit"])
            return scan

        with mock.patch.object(evaluator, "_twin_note_scan", side_effect=incomplete):
            code, report, stderr = self.twins()
        self.assertEqual(code, 0, stderr)
        rows = by_id(report[TWIN_KEY])
        # Twins whose removal depends on the claim scan cannot be trusted; the others still run.
        self.assertEqual({query_id for query_id, row in rows.items() if row["outcome"] == "twin_error"},
                         {"TWINID00", "TWINID01", "TWINID02"})
        self.assertEqual({row["twin_error_reason"] for row in rows.values() if row["outcome"] == "twin_error"},
                         {"note_scan_incomplete"})
        self.assertEqual(rows["TWINID04"]["outcome"], "returned_candidates")
        self.assertEqual(report[TWIN_KEY]["note_scan"]["search_complete"], False)
        self.assertIn("3 of 9 twins could not be measured", stderr)

    def test_legacy_twins_ignore_query_options_and_advanced_twins_keep_them(self):
        self.queries[4]["excluded_sources"] = [RELAY_GLOSSARY]
        self.save()
        _, legacy, _ = self.twins()
        _, advanced, _ = self.twins("--advanced")
        legacy_twin = by_id(legacy[TWIN_KEY])["TWINID04"]
        advanced_twin = by_id(advanced[TWIN_KEY])["TWINID04"]
        # evaluate() forwards a query's excluded_sources only on the advanced path; twins do the same.
        self.assertEqual((legacy_twin["excluded_sources"], legacy_twin["outcome"]), ([RELAY], "returned_candidates"))
        self.assertEqual((advanced_twin["excluded_sources"], advanced_twin["outcome"]),
                         ([RELAY_GLOSSARY, RELAY], "refused_empty_complete"))
        self.assertEqual(advanced_twin["excluded_path_count"], 1)

    def test_twin_rows_are_redacted_with_the_report(self):
        _, plain, _ = self.twins()
        key_file = self.root / "keys" / "key.bin"
        key_file.parent.mkdir()
        key_file.write_bytes(KEY)
        code, stdout, stderr = run_main(["--fixture", self.fixture, "--min-recall-at-3", "0", "--min-mrr", "0",
                                         "--gold-removal-twins", "--redact-key-file", key_file])
        self.assertEqual(code, 0, stderr)
        section = json.loads(stdout)[TWIN_KEY]
        text = json.dumps(section, ensure_ascii=False)
        for marker in ("TWINID", "TWINSCEN", ".md", CLAIM, "Pump seal", "압축기"):
            self.assertNotIn(marker, text)
        twin = by_id(section)[hmac_hex(KEY, "TWINID00")]
        plain_twin = by_id(plain[TWIN_KEY])["TWINID00"]
        self.assertEqual(twin["excluded_sources"], [hmac_hex(KEY, path) for path in plain_twin["excluded_sources"]])
        self.assertEqual(twin["residual_graded_paths"], [hmac_hex(KEY, PUMP_CHECKLIST)])
        self.assertEqual(twin["scenario"], hmac_hex(KEY, "TWINSCEN0"))
        self.assertEqual(section["possibly_still_answerable_ids"], [hmac_hex(KEY, "TWINID00")])
        self.assertEqual(set(section["by_scenario"]), {hmac_hex(KEY, "TWINSCEN0"), hmac_hex(KEY, "TWINSCEN1")})
        # Counts, flags, outcomes and definitions stay readable.
        for name in ("outcome", "possibly_still_answerable", "excluded_path_count", "removed_path_counts",
                     "removed_note_count", "twin_error_reason", "language"):
            self.assertEqual(twin[name], plain_twin[name], name)
        self.assertEqual(section["definitions"], plain[TWIN_KEY]["definitions"])
        self.assertEqual(section["operating_point"], plain[TWIN_KEY]["operating_point"])
        # Unkeyed --redact hashes ids, scenarios and claims; paths stay readable, as in per_query rows.
        code, stdout, _ = run_main(["--fixture", self.fixture, "--min-recall-at-3", "0", "--min-mrr", "0",
                                    "--gold-removal-twins", "--redact"])
        unkeyed = json.loads(stdout)[TWIN_KEY]
        twin = by_id(unkeyed)[hashlib.sha256(b"TWINID00").hexdigest()]
        self.assertEqual(twin["residual_graded_paths"], [PUMP_CHECKLIST])
        self.assertNotIn("TWINID", json.dumps(unkeyed))

    def test_token_row_order_also_sorts_inspected_ids_and_twin_rows(self):
        for index in (3, 9, 10):
            self.queries[index]["inspected"] = True
        # A second possibly_still_answerable twin: it returns the near-duplicate graded by TWINID01.
        self.queries[8].update(query="pump seal", required_claims=[CLAIM])
        self.save()
        key_file = self.root / "keys" / "key.bin"
        key_file.parent.mkdir()
        key_file.write_bytes(KEY)
        argv = ["--fixture", self.fixture, "--min-recall-at-3", "0", "--min-mrr", "0", "--gold-removal-twins",
                "--redact-key-file", key_file]
        gold_code, gold_out, gold_err = run_main(argv)
        code, stdout, stderr = run_main([*argv, "--redact-row-order", "token"])
        self.assertEqual((code, stderr), (gold_code, gold_err))
        gold, token = json.loads(gold_out), json.loads(stdout)
        self.assertEqual(token["inspected_queries"]["ids"], sorted(gold["inspected_queries"]["ids"]))
        self.assertEqual(len(token["inspected_queries"]["ids"]), 4)
        twins, gold_twins = token[TWIN_KEY], gold[TWIN_KEY]
        self.assertEqual(twins["rows"], sorted(gold_twins["rows"], key=lambda row: row["id"]))
        self.assertNotEqual([row["id"] for row in twins["rows"]], [row["id"] for row in gold_twins["rows"]])
        self.assertEqual(len(twins["possibly_still_answerable_ids"]), 2)
        self.assertEqual(twins["possibly_still_answerable_ids"], sorted(gold_twins["possibly_still_answerable_ids"]))
        self.assertEqual([row["id"] for row in token["per_query"]], sorted(row["id"] for row in gold["per_query"]))
        unchanged = ("rows", "possibly_still_answerable_ids")
        self.assertEqual({key: value for key, value in twins.items() if key not in unchanged},
                         {key: value for key, value in gold_twins.items() if key not in unchanged})

    def test_goldset_twins_use_the_resolved_labels(self):
        goldset = self.root / "goldset.json"
        moved = [dict(item) for item in self.queries]
        moved[4]["expected_paths"] = ["20-knowledge/old/Relay test.md"]  # resolves by its unique stem
        goldset.write_text(json.dumps({"queries": moved}, ensure_ascii=False), encoding="utf-8")
        code, stdout, stderr = run_main(["--vault", self.vault, "--goldset", goldset, "--min-recall-at-3", "0",
                                         "--min-mrr", "0", "--gold-removal-twins"])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["per_query"][4]["label_matches"][0]["match"], "stem")
        twin = by_id(report[TWIN_KEY])["TWINID04"]
        self.assertEqual((twin["excluded_sources"], twin["retrieved_paths"]), ([RELAY], [RELAY_GLOSSARY]))
        self.assertIn("goldset", report)


if __name__ == "__main__":
    unittest.main()
