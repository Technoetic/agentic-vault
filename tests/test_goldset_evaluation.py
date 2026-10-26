"""Opt-in in-place gold-set evaluation, dev/held-out split and redaction.

Runs without the new options must keep the v0.24.0 output. The golden digests in
tests/fixtures/recall_default_outputs.json were captured from d2b9315 (v0.24.0)
before these options existed, with capture_default_outputs() below.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
RECALL_HARD = FIXTURES / "recall_hard"
GOLDEN = FIXTURES / "recall_default_outputs.json"
EVALUATOR_PATH = ROOT / "scripts" / "evaluate_recall.py"
# Wall-clock fields differ between any two runs, before and after any change.
TIMING_FIELDS = frozenset({"elapsed_ms", "total_elapsed_ms", "mean_elapsed_ms"})
HARD_CI = ["--compare", "--expand-query", "--query-mapping", "00-meta/query-map.txt", "--max-tokens", "1500",
           "--max-no-answer-false-positive-rate", "0.25", "--max-stale-top-1-rate", "0"]
# The evaluate_recall.py commands of .github/workflows/tests.yml plus two documented runs.
DEFAULT_OUTPUT_CASES = (
    {"name": "ci_default", "fixture": "recall", "args": []},
    {"name": "ci_advanced_compare", "fixture": "recall_advanced",
     "args": ["--compare", "--max-no-answer-false-positive-rate", "0", "--max-forbidden-exposure-rate", "0",
              "--max-stale-top-1-rate", "0"]},
    {"name": "ci_hard_top3", "fixture": "recall_hard", "args": [*HARD_CI, "--top-k", "3"]},
    {"name": "ci_hard_top5", "fixture": "recall_hard", "args": [*HARD_CI, "--top-k", "5"]},
    {"name": "doc_advanced_render_matched", "fixture": "recall_advanced", "args": ["--compare", "--render-matched"]},
    {"name": "doc_experience", "fixture": "recall_experience", "args": []},
)


def load_evaluator(path: Path = EVALUATOR_PATH, name: str = "goldset_recall_evaluator"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = load_evaluator()


def lf_fixture_copy(name: str, root: Path) -> Path:
    """Copy queries.json and vault/ with LF endings.

    Checkouts differ by OS (Windows autocrlf writes CRLF), which changes
    bytes_read and source hashes; every recall fixture file is LF in git.
    """
    source, target = FIXTURES / name, root / name
    if not target.exists():
        files = [source / "queries.json", *sorted(path for path in (source / "vault").rglob("*") if path.is_file())]
        for path in files:
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    return target


# v0.26.0 (listing fingerprint, limit A11) changed only the query-expansion
# inventory_verification diagnostic: new listing_* fields and the method and
# assurance wording. Map it back to the earlier shape so the pinned digests
# keep proving that ranking, context and every other field are unchanged.
LEGACY_INVENTORY_WORDING = {
    "method": "bounded marks from the original scan; no second scan or payload reads",
    "assurance": "metadata_generation_only; preserved marks can evade checks; "
                 "initial source hashes do not certify current inventory bytes",
}
# The wording replaced above must be the current (listing-enforcing) text.
CURRENT_INVENTORY_WORDING = {
    "method": "bounded marks from the original scan; one bounded listing per marked directory; "
              "no second walk or payload reads",
    "assurance": "metadata_generation_and_name_listing; listing differences invalidate like metadata changes; "
                 "same-name recreation and preserved marks can evade checks; point-in-time checks, not a lock; "
                 "initial source hashes do not certify current inventory bytes",
}


def legacy_inventory_verification(value):
    if not isinstance(value, dict) or "listing_diagnostics" not in value:
        return value
    if value["listing_diagnostics"] or value.get("listing_diagnostics_omitted"):
        raise AssertionError("fixture runs must not observe listing changes")
    for key, expected in CURRENT_INVENTORY_WORDING.items():
        if value.get(key) != expected:
            raise AssertionError(f"unexpected inventory_verification {key}: {value.get(key)!r}")
    legacy = {key: item for key, item in value.items()
              if key not in {"listing_entries", "listing_diagnostics", "listing_diagnostics_omitted"}}
    return {**legacy, **LEGACY_INVENTORY_WORDING}


def canonical_report_text(stdout: str) -> str:
    """The printed JSON with wall-clock values replaced and floats at 12 significant digits.

    Rounding absorbs last-digit libm differences (math.log2 in nDCG) between CI
    platforms; key order, keys, strings, integers and booleans stay exact.
    """
    def walk(value):
        if isinstance(value, dict):
            return {key: ("<wall-clock>" if key in TIMING_FIELDS
                          else walk(legacy_inventory_verification(item)) if key == "inventory_verification"
                          else walk(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, float):
            return float(f"{value:.12g}")
        return value
    return json.dumps(walk(json.loads(stdout)), ensure_ascii=False, indent=2)


def run_main(module, argv) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = module.main([str(value) for value in argv])
    return code, stdout.getvalue(), stderr.getvalue()


def capture_default_outputs(module, root: Path) -> list[dict]:
    rows = []
    for case in DEFAULT_OUTPUT_CASES:
        fixture = lf_fixture_copy(case["fixture"], root)
        code, stdout, stderr = run_main(module, ["--fixture", fixture, *case["args"]])
        rows.append({**case, "exit_code": code, "stderr": stderr,
                     "stdout_canonical_sha256": hashlib.sha256(
                         canonical_report_text(stdout).encode("utf-8")).hexdigest()})
    return rows


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class DefaultOutputTests(unittest.TestCase):
    def test_fixture_runs_without_new_options_keep_v0240_output(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            observed = capture_default_outputs(evaluator, Path(temporary))
        self.assertEqual([row["name"] for row in observed], [row["name"] for row in golden["cases"]])
        for expected, actual in zip(golden["cases"], observed):
            with self.subTest(case=expected["name"]):
                self.assertEqual(actual["args"], expected["args"])
                self.assertEqual(actual["exit_code"], expected["exit_code"])
                self.assertEqual(actual["stderr"], expected["stderr"])
                self.assertEqual(actual["stdout_canonical_sha256"], expected["stdout_canonical_sha256"])

    def test_no_arguments_still_evaluate_the_default_fixture(self):
        original = evaluator.evaluate
        with mock.patch.object(evaluator, "evaluate", wraps=original) as wrapped:
            code, stdout, stderr = run_main(evaluator, [])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(wrapped.call_args.args[0], evaluator.DEFAULT_FIXTURE)
        report = json.loads(stdout)
        self.assertFalse({"goldset", "split", "inspected_queries", "redaction"}.intersection(report))

    def test_new_options_are_off_by_default(self):
        args = evaluator._parser().parse_args([])
        self.assertIsNone(args.fixture)
        for name in ("vault", "goldset", "split", "split_file"):
            self.assertIsNone(getattr(args, name), name)
        self.assertFalse(args.redact)
        self.assertFalse(args.derive_split)
        self.assertFalse(args.gold_removal_twins)
        self.assertEqual(args.redact_row_order, "gold")

    def test_fixture_mode_ignores_a_committed_split_without_the_flag(self):
        # recall_hard now ships split.json; only --split reads it.
        with mock.patch.object(evaluator, "load_split", side_effect=AssertionError("split read")):
            report = evaluator.evaluate(RECALL_HARD)
        self.assertEqual(report["query_count"], 64)
        self.assertNotIn("split", report)


class GoldSetVaultCase(unittest.TestCase):
    """A temporary real-shaped vault evaluated in place (fixture only, no tests)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # CI temp paths can contain 8.3 aliases; work under the canonical root.
        self.root = Path(self.tmp.name).resolve(strict=True)
        self.vault = self.root / "vault"
        self.write("00-meta/vault-config.json", json.dumps({
            "deny_zones": ["private", "10-inbox/_processed"], "exclude_dirs": ["scratch"]}))
        # The body line repeats the query terms so it, not the title, becomes the cited snippet.
        self.write("20-knowledge/procedures/Switchgear isolation.md",
                   "# Switchgear isolation\nSwitchgear isolation: switchgear isolation requires locking the breaker.\n")
        self.write("20-knowledge/a/Checklist.md", "# Checklist\nRelease checklist alpha steps.\n")
        self.write("20-knowledge/b/Checklist.md", "# Checklist\nRelease checklist beta steps.\n")
        self.write("20-knowledge/Rollback plan.md",
                   '---\naliases: ["되돌리기 계획"]\n---\n# Rollback plan\nRollback plan uses the blue green switch.\n')
        self.write("20-knowledge/history/Legacy rollback.md",
                   "# Legacy rollback\nLegacy rollback plan used the manual switch.\n")
        # Deny-zone and excluded notes share query words; they must never be read or returned.
        self.write("private/Switchgear secret.md", "# Switchgear isolation\nSwitchgear isolation DENYTOKEN.\n")
        self.write("10-inbox/_processed/Rollback plan.md", "# Rollback plan\nRollback plan DENYTOKEN copy.\n")
        self.write("scratch/Draft.md", "# Draft\nSwitchgear isolation draft.\n")
        self.queries = [
            self.query(0, "switchgear isolation", ["20-knowledge/Switchgear isolation.md"],
                       required_claims=["locking the breaker"]),
            self.query(1, "release checklist", ["Checklist.md"]),
            self.query(2, "rollback plan", ["20-knowledge/Rollback plan.md"]),
            self.query(3, "switchgear secret", ["private/Switchgear secret.md"]),
            self.query(4, "switchgear isolation", ["Switchgear secret.md"]),
            self.query(5, "switchgear draft", ["scratch/Draft.md"]),
            self.query(6, "quantum teleportation", [], expectation="no_answer"),
            self.query(7, "legacy rollback plan", ["20-knowledge/Rollback plan.md"],
                       stale_paths=["20-knowledge/old/Legacy rollback.md"]),
            self.query(8, "checklist warranty", [], expectation="no_answer"),
            self.query(9, "rollback plan", ["20-knowledge/ROLLBACK PLAN.md"]),
            self.query(10, "release checklist alpha", ["20-knowledge/a/Checklist.md"]),
            self.query(11, "switchgear isolation", ["20-knowledge/procedures/Switchgear isolation.md"]),
        ]
        self.goldset = self.root / "goldset.json"
        self.save()

    def write(self, relative: str, text: str) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def query(index: int, text: str, expected: list, **extra) -> dict:
        # Every free-text field carries a marker that never occurs in a note or path.
        return {"id": f"IDMARK{index:02d}", "language": "ko" if index % 2 else "en",
                "scenario": f"SCENMARK{index % 3}", "query": f"{text} QRYMARK{index:02d}",
                "expected_paths": expected, "label_rationale": f"RATIONALEMARK{index:02d}",
                "owner_note": f"EXTRAMARK{index:02d}", **extra}

    def save(self, path: Path | None = None) -> None:
        (path or self.goldset).write_text(json.dumps({"queries": self.queries}, ensure_ascii=False),
                                          encoding="utf-8")

    def run_goldset(self, *extra):
        return run_main(evaluator, ["--vault", self.vault, "--goldset", self.goldset,
                                    "--min-recall-at-3", "0", "--min-mrr", "0", *extra])


class GoldSetVaultTests(GoldSetVaultCase):
    def test_labels_resolve_by_path_or_unique_stem_and_report_the_match_kind(self):
        code, stdout, stderr = self.run_goldset()
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        rows = report["per_query"]
        moved = rows[0]
        self.assertEqual(moved["label_matches"], [{
            "field": "expected_paths", "label": "20-knowledge/Switchgear isolation.md", "match": "stem",
            "path": "20-knowledge/procedures/Switchgear isolation.md"}])
        self.assertEqual(moved["expected_paths"], ["20-knowledge/procedures/Switchgear isolation.md"])
        self.assertEqual(moved["retrieved_paths"][0], "20-knowledge/procedures/Switchgear isolation.md")
        self.assertEqual(moved["source_recall_at_3"], 1.0)
        self.assertEqual(moved["covered_claims"], ["locking the breaker"])
        self.assertEqual(rows[2]["label_matches"][0]["match"], "path")
        # Case-insensitive path, and an exact path wins over a shared stem.
        self.assertEqual(rows[9]["label_matches"][0]["match"], "path")
        self.assertEqual(rows[9]["expected_paths"], ["20-knowledge/Rollback plan.md"])
        self.assertEqual(rows[10]["label_matches"][0]["match"], "path")
        # stale_paths use the same resolution: the moved stale note is now detected.
        stale = rows[7]
        self.assertEqual(stale["label_matches"][1], {
            "field": "stale_paths", "label": "20-knowledge/old/Legacy rollback.md", "match": "stem",
            "path": "20-knowledge/history/Legacy rollback.md"})
        self.assertEqual(stale["stale_retrieved_paths"], ["20-knowledge/history/Legacy rollback.md"])
        section = report["goldset"]
        self.assertEqual(section["mode"], "in_place_vault")
        self.assertEqual(section["query_count"], 12)
        self.assertEqual(section["listed_note_count"], 5)
        self.assertTrue(section["listing_complete"])
        # Paths: queries 2, 7, 9, 10, 11; stems: query 0 and query 7's stale label.
        self.assertEqual(section["label_match_counts"], {
            "path": 5, "stem": 2, "ambiguous_stem": 1, "missing": 1, "denied": 1, "excluded": 1,
            "incomplete_inventory": 0})
        self.assertIn("goldset evaluation: 12 queries, 5 listed notes; labels by path 5, by stem 2, unresolved 4",
                      stderr)

    def test_ambiguous_stem_warns_and_never_guesses(self):
        code, stdout, stderr = self.run_goldset()
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        row = report["per_query"][1]
        self.assertEqual(row["label_matches"], [{
            "field": "expected_paths", "label": "Checklist.md", "match": "ambiguous_stem", "path": None,
            "candidate_count": 2, "candidates": ["20-knowledge/a/Checklist.md", "20-knowledge/b/Checklist.md"]}])
        self.assertEqual(row["source_recall_at_3"], 0.0)
        self.assertTrue(set(row["retrieved_paths"]) & {"20-knowledge/a/Checklist.md", "20-knowledge/b/Checklist.md"})
        warning = ("queries[1].expected_paths[0]: filename stem is shared by 2 eligible notes "
                   "(20-knowledge/a/Checklist.md, 20-knowledge/b/Checklist.md); label left unresolved, "
                   "give the full vault-relative path")
        self.assertIn(warning, report["goldset"]["warnings"])
        self.assertIn(f"goldset warning (non-blocking): {warning}", stderr)

    def test_deny_zones_are_never_listed_read_or_matched(self):
        opened, scanned = [], []
        real_open, real_scandir = os.open, os.scandir

        def recording_open(path, *args, **kwargs):
            opened.append(os.path.normcase(os.fspath(path)))
            return real_open(path, *args, **kwargs)

        def recording_scandir(path=".", *args, **kwargs):
            scanned.append(os.path.normcase(os.fspath(path)))
            return real_scandir(path, *args, **kwargs)

        with mock.patch("os.open", side_effect=recording_open), \
                mock.patch("os.scandir", side_effect=recording_scandir):
            code, stdout, stderr = self.run_goldset("--compare")
        self.assertEqual(code, 0, stderr)
        forbidden = [os.path.normcase(str(self.vault / part)) for part in ("private", "10-inbox", "scratch")]
        touched = [path for path in opened + scanned if any(path.startswith(prefix) for prefix in forbidden)]
        # 10-inbox itself is listed (only 10-inbox/_processed is denied); nothing below it is a note.
        self.assertEqual([path for path in touched if path != forbidden[1]], [])
        self.assertTrue(any(path.endswith(os.path.normcase("Switchgear isolation.md")) for path in opened))
        report = json.loads(stdout)
        for arm in ("baseline", "improved"):
            for row in report[arm]["per_query"]:
                self.assertFalse([path for path in row["retrieved_paths"]
                                  if path.startswith(("private/", "10-inbox/", "scratch/"))])
        rows = report["improved"]["per_query"]
        self.assertEqual(rows[3]["label_matches"][0]["match"], "denied")
        # The denied copy does not make a stem ambiguous or matchable.
        self.assertEqual(rows[4]["label_matches"][0]["match"], "missing")
        self.assertEqual(rows[5]["label_matches"][0]["match"], "excluded")
        self.assertNotIn("DENYTOKEN", stdout + stderr)

    def test_goldset_inside_a_deny_zone_is_refused_before_reading(self):
        inside = self.vault / "private" / "goldset.json"
        self.save(inside)
        with mock.patch.object(evaluator, "_read_bounded_json", side_effect=AssertionError("read")) as reader:
            code, stdout, stderr = run_main(evaluator, ["--vault", self.vault, "--goldset", inside])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("gold set file is inside a deny zone of this vault; it was not read", stderr)
        reader.assert_not_called()
        # A gold set elsewhere inside the vault (not denied) is accepted.
        allowed = self.vault / "00-meta" / "goldset.json"
        self.save(allowed)
        code, _, stderr = run_main(evaluator, ["--vault", self.vault, "--goldset", allowed,
                                               "--min-recall-at-3", "0", "--min-mrr", "0"])
        self.assertEqual(code, 0, stderr)

    def test_redact_hides_query_and_other_goldset_free_text(self):
        self.queries[2] = self.query(2, "되돌리기 계획", ["20-knowledge/Rollback plan.md"])
        self.save()
        markers = ("IDMARK", "SCENMARK", "QRYMARK", "RATIONALEMARK", "EXTRAMARK", "locking the breaker",
                   "되돌리기", "quantum teleportation")
        code, plain, _ = self.run_goldset("--compare", "--expand-query")
        self.assertEqual(code, 0)
        self.assertTrue(all(marker in plain for marker in markers), "markers must be visible without --redact")
        code, stdout, stderr = self.run_goldset("--compare", "--expand-query", "--redact")
        self.assertEqual(code, 0, stderr)
        for marker in markers:
            self.assertNotIn(marker, stdout + stderr)
        report = json.loads(stdout)
        self.assertEqual(report["redaction"], evaluator.REDACTION_DESCRIPTION)
        row = report["improved"]["per_query"][0]
        self.assertEqual(row["query"], sha256_text(self.queries[0]["query"]))
        self.assertEqual(row["id"], sha256_text("IDMARK00"))
        self.assertEqual(row["required_claims"], [sha256_text("locking the breaker")])
        self.assertEqual(row["covered_claims"], [sha256_text("locking the breaker")])
        # Vault-relative paths and labels stay readable.
        self.assertEqual(row["expected_paths"], ["20-knowledge/procedures/Switchgear isolation.md"])
        self.assertEqual(row["label_matches"][0]["label"], "20-knowledge/Switchgear isolation.md")
        self.assertIn(sha256_text("SCENMARK0"), report["improved"]["by_scenario"])
        self.assertEqual({row["id"] for row in report["improved"]["failed_queries"]} - {
            sha256_text(f"IDMARK{index:02d}") for index in range(12)}, set())
        expansion = report["improved"]["per_query"][2]["diagnostics"]["query_expansion"]
        self.assertEqual(expansion["status"], "ok")
        self.assertTrue(expansion["expansions"])
        self.assertTrue(all(len(term) == 64 for term in expansion["terms"]))
        # Metrics are computed before redaction and are unchanged by it.
        plain_report = json.loads(plain)
        self.assertEqual(report["improved"]["recall_at_3"], plain_report["improved"]["recall_at_3"])

    def test_small_sample_statistics_apply_to_a_redacted_goldset_run(self):
        # --exact-paired and --rate-bounds (merged from another branch) on the gold-set input.
        markers = ("IDMARK", "SCENMARK", "QRYMARK", "RATIONALEMARK", "EXTRAMARK", "locking the breaker")
        plain_code, plain, _ = self.run_goldset("--compare", "--redact")
        code, stdout, stderr = self.run_goldset("--compare", "--redact", "--exact-paired", "--rate-bounds")
        self.assertEqual(code, plain_code, stderr)
        for marker in markers:
            self.assertNotIn(marker, stdout + stderr)
        report, plain_report = json.loads(stdout), json.loads(plain)
        self.assertEqual(report["redaction"], evaluator.REDACTION_DESCRIPTION)
        self.assertIn("rate_upper_bound_definitions", report)
        for arm in ("baseline", "improved"):
            with self.subTest(arm=arm):
                summary = report[arm]
                self.assertEqual(summary["goldset"], plain_report[arm]["goldset"])
                bound = summary["rate_upper_bounds"]["no_answer_false_positive_rate"]
                self.assertEqual((bound["numerator"], bound["denominator"]),
                                 (summary["no_answer"]["false_positive_query_count"],
                                  summary["no_answer"]["query_count"]))
                self.assertEqual(set(summary["by_scenario"]), set(plain_report[arm]["by_scenario"]))
                for key, scenario in summary["by_scenario"].items():
                    self.assertRegex(key, "^[0-9a-f]{64}$")
                    self.assertIn("rate_upper_bounds", scenario)
                self.assertEqual(summary["recall_at_3"], plain_report[arm]["recall_at_3"])
        for metric, record in report["paired_randomization"].items():
            with self.subTest(metric=metric):
                self.assertEqual(record["mean_delta"], plain_report["paired_randomization"][metric]["mean_delta"])
                self.assertIn("nonzero_pair_count", record)
                self.assertIn("minimum_attainable_p_value", record)

    def test_gates_apply_as_in_fixture_mode(self):
        # Unresolved labels (ambiguous, denied, missing, excluded) can only miss.
        code, stdout, _ = run_main(evaluator, ["--vault", self.vault, "--goldset", self.goldset])
        self.assertEqual(code, 1)
        report = json.loads(stdout)
        self.assertEqual(report["answerable_query_count"], 10)
        self.assertLess(report["recall_at_3"], 0.85)
        self.assertEqual(report["no_answer"]["false_positive_query_count"], 1)
        for maximum, expected in (("0.5", 0), ("0.25", 1)):
            with self.subTest(maximum=maximum):
                code, stdout, _ = self.run_goldset("--max-no-answer-false-positive-rate", maximum)
                self.assertEqual(code, expected)
                gate = json.loads(stdout)["optional_gates"]["no_answer_false_positive_rate"]
                self.assertEqual(gate["observed"], 0.5)
        code, stdout, _ = self.run_goldset("--compare", "--max-stale-top-1-rate", "0")
        self.assertIn("optional_gates", json.loads(stdout)["improved"])

    def test_inspected_marks_cannot_empty_the_gated_headline(self):
        for item in self.queries:
            if item["expected_paths"]:
                item["inspected"] = True
        self.save()
        # run_goldset sets both thresholds to 0; a gate without answerable queries still cannot pass.
        code, stdout, stderr = self.run_goldset()
        self.assertEqual(code, 1)
        report = json.loads(stdout)
        self.assertEqual((report["query_count"], report["answerable_query_count"]), (2, 0))
        self.assertEqual(report["inspected_queries"]["summary"]["answerable_query_count"], 10)
        self.assertEqual(report["evaluation_errors"], [])
        self.assertIn("recall evaluation gate failed: 10 inspected=true queries leave no answerable query "
                      "in the headline", stderr)

    def test_vault_policy_and_label_problems_exit_2_with_reasons(self):
        cases = []
        no_config = self.root / "plain-folder"
        no_config.mkdir()
        cases.append((["--vault", no_config, "--goldset", self.goldset], "vault configuration unavailable"))
        cases.append((["--vault", self.vault, "--goldset", self.root / "missing.json"], "gold set file not found"))
        # The second label reaches the same note through its unique stem (the denied copy is not listed).
        duplicate = dict(self.queries[2], id="IDMARKDUP",
                         expected_paths=["20-knowledge/Rollback plan.md", "old/Rollback plan.md"])
        broken = self.root / "duplicate.json"
        broken.write_text(json.dumps({"queries": [duplicate, *self.queries[1:]]}), encoding="utf-8")
        cases.append((["--vault", self.vault, "--goldset", broken],
                      "queries[0].expected_paths labels resolve to the same note"))
        small = self.root / "small.json"
        small.write_text(json.dumps({"queries": self.queries[:3]}), encoding="utf-8")
        cases.append((["--vault", self.vault, "--goldset", small], "at least 12 labeled queries"))
        unsafe = self.root / "unsafe.json"
        unsafe.write_text(json.dumps({"queries": [dict(self.queries[0], expected_paths=["../x.md"]),
                                                  *self.queries[1:]]}), encoding="utf-8")
        cases.append((["--vault", self.vault, "--goldset", unsafe], "queries[0].expected_paths must contain"))
        repeated = self.root / "repeated.json"
        repeated.write_text('{"queries": [], "queries": []}', encoding="utf-8")
        cases.append((["--vault", self.vault, "--goldset", repeated], "JSON object repeats a key"))
        for argv, reason in cases:
            with self.subTest(reason=reason):
                code, stdout, stderr = run_main(evaluator, argv)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn(reason, stderr)
                self.assertNotIn("QRYMARK", stderr)

    def test_mode_combinations_are_validated(self):
        split = self.root / "split.json"
        for argv, message in (
            (["--fixture", RECALL_HARD, "--vault", self.vault, "--goldset", self.goldset],
             "--fixture cannot be combined with --vault or --goldset"),
            (["--vault", self.vault], "--vault and --goldset must be given together"),
            (["--goldset", self.goldset], "--vault and --goldset must be given together"),
            (["--vault", self.vault, "--goldset", self.goldset, "--split", "dev"],
             "--split with --goldset requires --split-file"),
            (["--split-file", split], "--split-file requires --split or --derive-split"),
            (["--derive-split", "--redact"], "--derive-split cannot be combined with --split or --redact"),
            (["--derive-split", "--split", "dev"], "--derive-split cannot be combined with --split or --redact"),
        ):
            with self.subTest(argv=argv):
                code, stdout, stderr = run_main(evaluator, argv)
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(stderr.strip(), message)

    def test_incomplete_listing_turns_stem_matching_off(self):
        module = evaluator._load_recall_module()
        module.MAX_DIRECTORY_ENTRIES = 3
        with mock.patch.object(evaluator, "_load_recall_module", return_value=module):
            prepared = evaluator.load_goldset(self.vault, self.goldset)
        section = prepared.sections["goldset"]
        self.assertFalse(section["listing_complete"])
        self.assertTrue(section["warnings"][0].startswith("note listing incomplete (directory_entry_limit)"))
        self.assertEqual(prepared.queries[0]["label_matches"][0]["match"], "incomplete_inventory")
        self.assertEqual(section["label_match_counts"]["stem"], 0)

    def test_file_limit_cut_matches_retrieval_and_turns_stem_matching_off(self):
        module = evaluator._load_recall_module()
        module.MAX_FILES = 3
        with mock.patch.object(evaluator, "_load_recall_module", return_value=module):
            prepared = evaluator.load_goldset(self.vault, self.goldset)
            report = evaluator.evaluate(prepared)
        # The first MAX_FILES notes in the listing order that retrieval also cuts.
        kept = {"20-knowledge/a/Checklist.md", "20-knowledge/b/Checklist.md",
                "20-knowledge/history/Legacy rollback.md"}
        section = prepared.sections["goldset"]
        self.assertFalse(section["listing_complete"])
        self.assertEqual(section["listed_note_count"], 3)
        self.assertTrue(section["warnings"][0].startswith("note listing incomplete (file_count_limit)"))
        self.assertEqual(section["label_match_counts"]["stem"], 0)
        # A moved note's stem is not guessed, and an exact path past the cut is not a match.
        self.assertEqual(prepared.queries[0]["label_matches"][0]["match"], "incomplete_inventory")
        self.assertEqual(prepared.queries[2]["label_matches"][0]["match"], "incomplete_inventory")
        self.assertEqual(prepared.queries[10]["label_matches"][0], {
            "field": "expected_paths", "label": "20-knowledge/a/Checklist.md", "match": "path",
            "path": "20-knowledge/a/Checklist.md"})
        resolved = {found["path"] for row in prepared.queries for found in row["label_matches"] if found["path"]}
        self.assertTrue(resolved and resolved <= kept, resolved)
        retrieved = {path for row in report["per_query"] for path in row["retrieved_paths"]}
        self.assertTrue(retrieved and retrieved <= kept, retrieved)
        self.assertTrue(report["evaluation_errors"])

        # Under the limit nothing changes: the stem still resolves the moved note.
        module.MAX_FILES = 5
        with mock.patch.object(evaluator, "_load_recall_module", return_value=module):
            prepared = evaluator.load_goldset(self.vault, self.goldset)
        self.assertTrue(prepared.sections["goldset"]["listing_complete"])
        self.assertEqual(prepared.queries[0]["label_matches"][0]["match"], "stem")

    def test_goldset_split_needs_its_own_split_file(self):
        derived = self.root / "goldset.split.json"
        code, stdout, stderr = run_main(evaluator, ["--vault", self.vault, "--goldset", self.goldset,
                                                    "--derive-split"])
        self.assertEqual(code, 0, stderr)
        derived.write_text(stdout, encoding="utf-8")
        split = json.loads(stdout)
        self.assertEqual(len(split["dev"]) + len(split["heldout"]), 12)
        code, stdout, stderr = self.run_goldset("--split", "heldout", "--split-file", derived)
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["query_count"], len(split["heldout"]))
        self.assertEqual(report["split"]["query_count_by_split"],
                         {"dev": len(split["dev"]), "heldout": len(split["heldout"]), "unassigned": 0})
        self.assertIn("goldset", report)
        self.assertIn(f"recall evaluation split: heldout n={len(split['heldout'])}", stderr)


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True)

    def test_committed_recall_hard_split_is_rederived_by_the_documented_rule(self):
        committed = json.loads((RECALL_HARD / "split.json").read_text(encoding="utf-8"))
        queries = evaluator._load_queries(RECALL_HARD)
        self.assertEqual(evaluator.derive_split(queries), committed)
        code, stdout, stderr = run_main(evaluator, ["--fixture", RECALL_HARD, "--derive-split"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), committed)
        self.assertEqual((committed["seed"], committed["rule"]), (1707, evaluator.SPLIT_RULE))
        dev, heldout = set(committed["dev"]), set(committed["heldout"])
        self.assertFalse(dev & heldout)
        self.assertEqual(dev | heldout, {item["id"] for item in queries})
        self.assertEqual((len(dev), len(heldout)), (32, 32))
        by_id = {item["id"]: item for item in queries}
        for row in committed["strata"]:
            members = [item["id"] for item in queries
                       if (item["language"], item["scenario"]) == (row["language"], row["scenario"])]
            self.assertEqual(row["query_count"], len(members))
            self.assertEqual(sum(member in heldout for member in members), len(members) // 2)
            self.assertEqual(row["heldout_count"], len(members) // 2)
        for side in (dev, heldout):
            no_answer = [by_id[query_id]["language"] for query_id in side
                         if by_id[query_id]["expectation"] == "no_answer"]
            self.assertEqual(sorted(no_answer), ["en", "en", "ko", "ko"])
        # The rule orders by SHA-256 of "<seed>:<id>", not by id.
        stratum = sorted((query_id for query_id in by_id if query_id.startswith("en-particles-")),
                         key=lambda query_id: sha256_text(f"1707:{query_id}"))
        self.assertEqual(set(stratum[:3]), {query_id for query_id in heldout if query_id.startswith("en-particles-")})

    def test_split_evaluates_one_side_and_prints_n_per_split(self):
        heldout = set(json.loads((RECALL_HARD / "split.json").read_text(encoding="utf-8"))["heldout"])
        code, stdout, stderr = run_main(evaluator, ["--fixture", RECALL_HARD, "--split", "heldout",
                                                    "--min-recall-at-3", "0", "--min-mrr", "0"])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["query_count"], 32)
        self.assertEqual({row["id"] for row in report["per_query"]}, heldout)
        section = report["split"]
        self.assertEqual(section["name"], "heldout")
        self.assertEqual(section["seed"], 1707)
        self.assertEqual(section["query_count_by_split"], {"dev": 32, "heldout": 32, "unassigned": 0})
        self.assertEqual((section["evaluated_query_count"], section["listed_ids_not_in_queries"]), (32, 0))
        self.assertIn("recall evaluation split: heldout n=32 (dev n=32, heldout n=32, unassigned n=0)", stderr)
        # The digest ignores line endings and BOMs, so every checkout reports the same value.
        crlf = self.root / "split-crlf.json"
        crlf.write_bytes(b"\xef\xbb\xbf" + (RECALL_HARD / "split.json").read_bytes().replace(b"\r\n", b"\n")
                         .replace(b"\n", b"\r\n"))
        other = evaluator.select_split(RECALL_HARD, "heldout", crlf)
        self.assertEqual(other.sections["split"]["definition_canonical_sha256"],
                         section["definition_canonical_sha256"])

    def make_fixture(self) -> Path:
        fixture = self.root / "fixture"
        vault = fixture / "vault"
        (vault / "00-meta").mkdir(parents=True)
        (vault / "00-meta" / "vault-config.json").write_text("{}", encoding="utf-8")
        (vault / "note.md").write_text("# Checklist\nService checklist steps.\n", encoding="utf-8")
        queries = [{"id": f"q{index:02d}", "language": "en" if index < 6 else "ko", "scenario": "short",
                    "query": "service checklist", "expected_paths": ["note.md"]} for index in range(12)]
        (fixture / "queries.json").write_text(json.dumps({"queries": queries}), encoding="utf-8")
        return fixture

    def test_split_file_problems_and_unassigned_queries(self):
        fixture = self.make_fixture()
        split = fixture / "split.json"
        split.write_text(json.dumps({"dev": [f"q{index:02d}" for index in range(6)],
                                     "heldout": ["q06", "q07", "q08", "q09", "unknown"]}), encoding="utf-8")
        code, stdout, stderr = run_main(evaluator, ["--fixture", fixture, "--split", "heldout"])
        self.assertEqual(code, 0, stderr)
        section = json.loads(stdout)["split"]
        self.assertEqual(section["query_count_by_split"], {"dev": 6, "heldout": 4, "unassigned": 2})
        self.assertEqual((section["evaluated_query_count"], section["listed_ids_not_in_queries"]), (4, 1))
        self.assertIsNone(section["seed"])
        for content, reason in (
            ({"dev": ["q00"], "heldout": ["q00"]}, "both dev and heldout"),
            ({"dev": "q00", "heldout": []}, "split file dev must list unique non-empty query ids"),
            ({"dev": ["q00", "q00"], "heldout": []}, "split file dev must list unique non-empty query ids"),
            ({"dev": ["q00"], "heldout": ["nothing"]}, "split heldout selects no queries"),
            ([], "split file must be a JSON object"),
        ):
            with self.subTest(reason=reason):
                split.write_text(json.dumps(content), encoding="utf-8")
                code, stdout, stderr = run_main(evaluator, ["--fixture", fixture, "--split", "heldout"])
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn(reason, stderr)
        split.unlink()
        code, _, stderr = run_main(evaluator, ["--fixture", fixture, "--split", "dev"])
        self.assertEqual(code, 2)
        self.assertIn("split file is not a readable file", stderr)

    def test_growing_a_goldset_keeps_every_existing_assignment(self):
        base = [{"id": f"a{index}", "language": "en", "scenario": "short"} for index in range(6)]
        base += [{"id": f"b{index}", "language": "ko", "scenario": "short"} for index in range(5)]
        first = evaluator.derive_split(base)
        self.assertEqual((len(first["dev"]), len(first["heldout"])), (6, 5))  # floor(6/2) + floor(5/2)
        grown = [item for item in base if item["id"] != "a0"]
        grown += [{"id": f"c{index}", "language": "en", "scenario": "short"} for index in range(4)]
        grown += [{"id": "d0", "language": "ko", "scenario": "long"}]
        second = evaluator.derive_split(grown, previous=first)
        for name in ("dev", "heldout"):
            self.assertTrue(set(first[name]) - {"a0"} <= set(second[name]), name)
        self.assertEqual(second["incremental"], {"kept": 10, "added": 5, "dropped": 1})
        self.assertEqual(sum(query_id in second["heldout"] for query_id in ("c0", "c1", "c2", "c3")), 2)
        self.assertIn("d0", second["dev"])  # a one-query stratum stays in dev
        self.assertEqual(evaluator.derive_split(grown, previous=first), second)
        # The CLI keeps the previous file's seed and assignments.
        fixture = self.root / "grown"
        fixture.mkdir()
        previous = fixture / "previous.json"
        previous.write_text(json.dumps({**first, "seed": 11}), encoding="utf-8")
        vault = fixture / "vault"
        (vault / "00-meta").mkdir(parents=True)
        (vault / "00-meta" / "vault-config.json").write_text("{}", encoding="utf-8")
        queries = [{**item, "query": "x", "expected_paths": ["n.md"]} for item in grown]
        (fixture / "queries.json").write_text(json.dumps({"queries": queries}), encoding="utf-8")
        code, stdout, stderr = run_main(evaluator, ["--fixture", fixture, "--derive-split", "--split-file", previous])
        self.assertEqual(code, 0, stderr)
        printed = json.loads(stdout)
        self.assertEqual(printed["seed"], 11)
        self.assertEqual(printed["incremental"], {"kept": 10, "added": 5, "dropped": 1})

    def test_split_and_goldset_files_may_be_utf16_from_powershell_redirection(self):
        fixture = self.make_fixture()
        text = json.dumps({"dev": [f"q{index:02d}" for index in range(6)],
                           "heldout": [f"q{index:02d}" for index in range(6, 12)]})
        for encoding in ("utf-16", "utf-8-sig", "utf-8"):
            with self.subTest(encoding=encoding):
                (fixture / "split.json").write_bytes(text.encode(encoding))
                selected = evaluator.select_split(fixture, "heldout", fixture / "split.json")
                self.assertEqual(len(selected.queries), 6)
        (fixture / "split.json").write_bytes(b"\xff\xfe\x00\xd8")
        with self.assertRaises(evaluator.GoldSetError):
            evaluator.load_split(fixture / "split.json")

    def test_seed_must_be_a_non_negative_integer(self):
        for seed in (-1, True, 1.5, "1707"):
            with self.subTest(seed=seed), self.assertRaises(evaluator.GoldSetError):
                evaluator.derive_split([], seed=seed)


class InspectedQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Path(self.tmp.name).resolve(strict=True) / "fixture"
        vault = self.fixture / "vault"
        (vault / "00-meta").mkdir(parents=True)
        (vault / "00-meta" / "vault-config.json").write_text("{}", encoding="utf-8")
        (vault / "checklist.md").write_text("# Service checklist\nCheck the service checklist.\n", encoding="utf-8")
        (vault / "other.md").write_text("# Other\nUnrelated rotation text.\n", encoding="utf-8")
        self.queries = [{"id": f"q{index:02d}", "language": "en" if index % 2 else "ko", "scenario": "short",
                         "query": "service checklist", "expected_paths": ["checklist.md"]} for index in range(12)]

    def save(self):
        (self.fixture / "queries.json").write_text(json.dumps({"queries": self.queries}), encoding="utf-8")

    @staticmethod
    def no_answer(index: int) -> dict:
        return {"id": f"n{index:02d}", "language": "en", "scenario": "short", "query": "zebra harmonica",
                "expectation": "no_answer", "expected_paths": []}

    def test_inspected_queries_leave_headline_metrics_gates_and_pairs(self):
        for index in (0, 1):
            self.queries[index].update(expected_paths=["other.md"], inspected=True)
        self.queries[2]["inspected"] = False
        self.save()
        report = evaluator.evaluate(self.fixture)
        self.assertEqual(report["query_count"], 10)
        self.assertEqual(report["recall_at_3"], 1.0)
        self.assertEqual(sum(row["answerable_query_count"] for row in report["by_language"].values()), 10)
        section = report["inspected_queries"]
        self.assertEqual((section["query_count"], section["ids"]), (2, ["q00", "q01"]))
        self.assertEqual(section["summary"]["recall_at_3"], 0.0)
        self.assertEqual(len(report["per_query"]), 12)
        self.assertEqual(report["costs"]["estimated_returned_context_tokens"],
                         sum(row["estimated_returned_context_tokens"] for row in report["per_query"]))
        self.assertEqual([row["id"] for row in report["failed_queries"]], ["q00", "q01"])
        code, stdout, stderr = run_main(evaluator, ["--fixture", self.fixture, "--min-recall-at-3", "1"])
        self.assertEqual(code, 0)
        self.assertIn("2 inspected queries are excluded from headline metrics and gates", stderr)
        compared = evaluator.compare(self.fixture)
        self.assertEqual(compared["paired_randomization"]["source_recall_at_k"]["paired_query_count"], 10)

    def test_without_true_marks_nothing_changes(self):
        self.save()
        plain = evaluator.evaluate(self.fixture)
        for item in self.queries:
            item["inspected"] = False
        self.save()
        marked = evaluator.evaluate(self.fixture)
        self.assertNotIn("inspected_queries", marked)
        for key in ("query_count", "recall_at_3", "mrr", "ndcg_at_5", "by_language", "by_scenario", "no_answer"):
            self.assertEqual(marked[key], plain[key], key)

    def test_marks_that_empty_the_headline_fail_the_recall_and_mrr_gates(self):
        # Review finding: with every answerable query inspected, recall@3 and MRR were None, the
        # None-tolerant gates passed and a run whose every query missed exited 0.
        misses = [dict(item, expected_paths=["other.md"], inspected=True) for item in self.queries]
        hits = [dict(item, inspected=True) for item in self.queries]
        for label, queries, inspected, gap in (
            ("every query inspected, all miss", misses, 12, "no query"),
            ("every query inspected, all hit", hits, 12, "no query"),
            ("answerable inspected, no_answer kept", misses[:10] + [self.no_answer(0), self.no_answer(1)], 10,
             "no answerable query"),
        ):
            self.queries = queries
            self.save()
            for extra in ([], ["--compare"], ["--min-recall-at-3", "0", "--min-mrr", "0"]):
                with self.subTest(case=label, extra=extra):
                    code, stdout, stderr = run_main(evaluator, ["--fixture", self.fixture, *extra])
                    self.assertEqual(code, 1)
                    gated = json.loads(stdout)
                    gated = gated.get("improved", gated)
                    self.assertEqual((gated["answerable_query_count"], gated["recall_at_3"], gated["mrr"]),
                                     (0, None, None))
                    self.assertEqual(gated["inspected_queries"]["query_count"], inspected)
                    # Nothing else fails these runs: every search is complete and no optional gate is set.
                    self.assertEqual((gated["evaluation_errors"], gated["optional_gates"]), ([], {}))
                    self.assertIn(f"recall evaluation gate failed: {inspected} inspected=true queries leave "
                                  f"{gap} in the headline, so --min-recall-at-3 and --min-mrr cannot be "
                                  "evaluated", stderr)
                    self.assertNotIn("(non-blocking)", stderr)
        # The same all-hit queries pass once they are no longer marked.
        self.queries = [dict(item, inspected=False) for item in hits]
        self.save()
        code, _, stderr = run_main(evaluator, ["--fixture", self.fixture])
        self.assertEqual((code, stderr), (0, ""))

    def test_the_gate_rule_applies_to_the_evaluated_split(self):
        for item in self.queries[6:]:
            item["inspected"] = True
        self.save()
        (self.fixture / "split.json").write_text(json.dumps({
            "dev": [f"q{index:02d}" for index in range(6)],
            "heldout": [f"q{index:02d}" for index in range(6, 12)]}), encoding="utf-8")
        code, _, stderr = run_main(evaluator, ["--fixture", self.fixture, "--split", "dev"])
        self.assertEqual(code, 0, stderr)
        code, stdout, stderr = run_main(evaluator, ["--fixture", self.fixture, "--split", "heldout"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["split"]["evaluated_inspected_query_count"], 6)
        self.assertIn("recall evaluation gate failed: 6 inspected=true queries leave no query in the headline",
                      stderr)

    def test_sets_without_answerable_queries_keep_the_v0240_recall_gate(self):
        # Regression: v0.24.0 (d2b9315) skips recall@3 and MRR for a set without answerable queries
        # and exits 0 on this fixture. Marks on some no_answer queries remove no answerable query.
        for marked in (0, 6):
            with self.subTest(marked=marked):
                self.queries = [self.no_answer(index) for index in range(12)]
                for item in self.queries[:marked]:
                    item["inspected"] = True
                self.save()
                code, stdout, stderr = run_main(evaluator, ["--fixture", self.fixture])
                self.assertEqual(code, 0, stderr)
                report = json.loads(stdout)
                self.assertEqual((report["query_count"], report["recall_at_3"], report["mrr"]),
                                 (12 - marked, None, None))
                self.assertEqual(report["no_answer"]["false_positive_query_count"], 0)
                self.assertNotIn("gate failed", stderr)
        # Marking every query leaves an empty headline, which cannot pass.
        self.queries = [dict(item, inspected=True) for item in self.queries]
        self.save()
        code, stdout, stderr = run_main(evaluator, ["--fixture", self.fixture])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["query_count"], 0)
        self.assertIn("12 inspected=true queries leave no query in the headline", stderr)

    def test_inspected_must_be_boolean(self):
        for value in ("yes", 1, None):
            with self.subTest(value=value):
                self.queries[0]["inspected"] = value
                self.save()
                with self.assertRaises(ValueError):
                    evaluator._load_queries(self.fixture)

    def test_redaction_also_covers_inspected_ids_in_fixture_mode(self):
        self.queries[0].update(id="INSPECTEDMARK", inspected=True)
        self.save()
        code, stdout, stderr = run_main(evaluator, ["--fixture", self.fixture, "--redact"])
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("INSPECTEDMARK", stdout + stderr)
        self.assertEqual(json.loads(stdout)["inspected_queries"]["ids"], [sha256_text("INSPECTEDMARK")])


class RedactionTests(unittest.TestCase):
    def report(self) -> dict:
        candidates = [{"path": "a.md", "rank": 1, "note": "provider SECRET"}, "b.md"]
        row = {"id": "SECRET-id", "language": "ko", "scenario": "SECRET-scenario", "query": "SECRET words",
               "expected_paths": ["a.md"], "expectation": "answerable", "forbidden_paths": [], "stale_paths": [],
               "required_claims": ["SECRET claim"], "relevance": {"a.md": 1}, "custom": {"nested": "SECRET"},
               "as_of": "next SECRET tuesday", "external_candidates": candidates,
               "retrieved_paths": ["a.md"], "covered_claims": ["SECRET claim"], "source_recall_at_3": 1.0,
               "retrieval_options": {"as_of": "2026-01-01", "backend": "hybrid", "expand_links": 1,
                                     "external_candidates": candidates, "excluded_sources": ["x/y.md"]},
               "diagnostics": {"status": "ok"}}
        return {"query_count": 1, "by_language": {"ko": {}}, "by_scenario": {"SECRET-scenario": {}},
                "per_query": [row], "failed_queries": [{"id": "SECRET-id", "expected_paths": ["a.md"]}],
                "evaluation_errors": [{"id": "SECRET-id", "status": "invalid_options"}]}

    def test_whitelist_keeps_structure_and_hashes_everything_else(self):
        redacted = evaluator.redact_report(self.report())
        self.assertNotIn("SECRET", json.dumps(redacted, ensure_ascii=False))
        row = redacted["per_query"][0]
        self.assertEqual(row["custom"], sha256_text('{"nested":"SECRET"}'))
        self.assertEqual(row["as_of"], sha256_text("next SECRET tuesday"))
        self.assertEqual(row["external_candidates"][0], {"path": "a.md", "rank": 1,
                                                         "note": sha256_text("provider SECRET")})
        self.assertEqual(row["external_candidates"][1], "b.md")
        self.assertEqual({key: row["retrieval_options"][key] for key in ("as_of", "backend", "expand_links",
                                                                         "excluded_sources")},
                         {"as_of": "2026-01-01", "backend": "hybrid", "expand_links": 1,
                          "excluded_sources": ["x/y.md"]})
        self.assertEqual((row["language"], row["expected_paths"], row["relevance"], row["source_recall_at_3"]),
                         ("ko", ["a.md"], {"a.md": 1}, 1.0))
        self.assertEqual(redacted["by_language"], {"ko": {}})
        self.assertEqual(redacted["evaluation_errors"][0]["status"], "invalid_options")

    def test_every_evaluated_arm_is_redacted_and_other_keys_kept(self):
        report = {"benchmark": "paired", "baseline": self.report(), "improved": self.report(),
                  "paired_randomization": {"ndcg_at_5": {"p_value": 1.0}}}
        redacted = evaluator.redact_report(report)
        self.assertNotIn("SECRET", json.dumps(redacted, ensure_ascii=False))
        self.assertEqual(redacted["paired_randomization"], report["paired_randomization"])
        self.assertEqual(redacted["redaction"], evaluator.REDACTION_DESCRIPTION)
        self.assertIn("SECRET", json.dumps(report))  # the input report is not modified



# --- Keyed pseudonymisation (--redact-key-file, --redact-ephemeral) --------
# Captured from the unchanged v0.25.0 code (833d394) with the canonical text
# rules above; plain --redact must keep these bytes apart from the two added
# redaction fields.
PLAIN_REDACT_CASES = (
    ("recall_advanced", ["--compare", "--render-matched", "--redact"],
     "19b04565fd0c345b806bc4609a8c0eebfd99d33ef17575f943933013b59d4f4a"),
    ("recall_hard", [*HARD_CI, "--redact"],
     "f76b03b3f4823a3fc8e0f00b117d33f5e8c430d9b8b5c0a3132e7b094309e3b0"),
)
ADDED_REDACTION_FIELDS = ("redaction_mode", "redaction_algorithm")
KEY_A = b"KEYMARK-alpha:" + bytes(range(200, 232))
KEY_B = b"KEYMARK-bravo:" + bytes(range(100, 132))
FIXED_EPHEMERAL = b"KEYMARK-ephemeral:" + bytes(range(1, 33))
GOLDSET_PATH_TEXT = ("20-knowledge", "Switchgear isolation", "Checklist", "Rollback plan", "Legacy rollback",
                     "Switchgear secret", "Draft", "private/", "scratch/", ".md")


def hmac_hex(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def key_forms(key: bytes) -> list[str]:
    """Every printable form of the key or of an unkeyed fingerprint of it."""
    encoded = [base64.b64encode(key).decode(), base64.urlsafe_b64encode(key).decode(),
               base64.b32encode(key).decode()]
    return ["KEYMARK", key.hex(), key.hex().upper(), *encoded, *(value.rstrip("=") for value in encoded),
            hashlib.sha256(key).hexdigest(), hashlib.sha1(key).hexdigest(), hashlib.md5(key).hexdigest(),
            hashlib.sha256(key.hex().encode()).hexdigest()]


def without_added_fields(stdout: str) -> str:
    report = json.loads(stdout)
    for name in ADDED_REDACTION_FIELDS:
        report.pop(name, None)
    return json.dumps(report, ensure_ascii=False)


def dict_keys(value) -> list[str]:
    if isinstance(value, dict):
        return [*value, *(key for item in value.values() for key in dict_keys(item))]
    if isinstance(value, list):
        return [key for item in value for key in dict_keys(item)]
    return []


class PlainRedactionCompatibilityTests(unittest.TestCase):
    def test_plain_redact_output_is_unchanged_apart_from_the_added_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name, args, expected in PLAIN_REDACT_CASES:
                with self.subTest(fixture=name):
                    fixture = lf_fixture_copy(name, Path(temporary))
                    code, stdout, stderr = run_main(evaluator, ["--fixture", fixture, *args])
                    _, _, plain_stderr = run_main(evaluator, ["--fixture", fixture,
                                                              *(arg for arg in args if arg != "--redact")])
                    self.assertEqual(code, 0, stderr)
                    self.assertEqual(stderr, plain_stderr)
                    report = json.loads(stdout)
                    self.assertEqual((report["redaction_mode"], report["redaction_algorithm"]),
                                     ("unkeyed", "sha256"))
                    self.assertEqual(list(report)[-3:], ["redaction", *ADDED_REDACTION_FIELDS])
                    self.assertEqual(report["redaction"], evaluator.REDACTION_DESCRIPTION)
                    self.assertEqual(sha256_text(canonical_report_text(without_added_fields(stdout))), expected)

    def test_default_output_has_no_redaction_fields(self):
        code, stdout, stderr = run_main(evaluator, [])
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse({"redaction", *ADDED_REDACTION_FIELDS}.intersection(json.loads(stdout)))
        # Nor the opt-in row order or gold-removal twins, at any depth.
        self.assertFalse({"row_order", "gold_removal_twins"}.intersection(dict_keys(json.loads(stdout))))

    def test_keyed_options_are_off_by_default(self):
        args = evaluator._parser().parse_args([])
        self.assertIsNone(args.redact_key_file)
        self.assertFalse(args.redact_ephemeral)


class KeyedRedactionTests(GoldSetVaultCase):
    def setUp(self):
        super().setUp()
        self.keys = self.root / "keys"
        self.keys.mkdir()
        self.key_a = self.write_key("KEYPATHMARK-a.bin", KEY_A)
        self.key_b = self.write_key("KEYPATHMARK-b.bin", KEY_B)

    def write_key(self, name: str, data: bytes, directory: Path | None = None) -> Path:
        path = (directory or self.keys) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_compare(self, *extra):
        return self.run_goldset("--compare", "--expand-query", *extra)

    def assert_hidden(self, text: str, markers) -> None:
        for marker in markers:
            self.assertNotIn(marker, text)

    def test_keyed_tokens_depend_on_the_key_and_repeat_with_it(self):
        _, unkeyed, _ = self.run_compare("--redact")
        code, first, stderr = self.run_compare("--redact-key-file", self.key_a)
        self.assertEqual(code, 0, stderr)
        _, again, _ = self.run_compare("--redact-key-file", self.key_a)
        _, other, _ = self.run_compare("--redact-key-file", self.key_b)
        self.assertEqual(canonical_report_text(first), canonical_report_text(again))
        self.assertNotEqual(canonical_report_text(first), canonical_report_text(other))
        self.assertNotEqual(canonical_report_text(without_added_fields(first)),
                            canonical_report_text(without_added_fields(unkeyed)))
        report, plain = json.loads(first), json.loads(unkeyed)
        self.assertEqual((report["redaction_mode"], report["redaction_algorithm"]), ("keyed", "hmac-sha256"))
        self.assertEqual(report["redaction"], evaluator.KEYED_REDACTION_DESCRIPTION)
        row = report["improved"]["per_query"][0]
        self.assertEqual(row["query"], hmac_hex(KEY_A, self.queries[0]["query"]))
        self.assertEqual(row["id"], hmac_hex(KEY_A, "IDMARK00"))
        self.assertEqual(row["required_claims"], [hmac_hex(KEY_A, "locking the breaker")])
        self.assertEqual(row["label_rationale"], hmac_hex(KEY_A, "RATIONALEMARK00"))
        self.assertIn(hmac_hex(KEY_A, "SCENMARK0"), report["improved"]["by_scenario"])
        self.assertNotIn(sha256_text("IDMARK00"), first)
        self.assert_hidden(first + stderr, ("IDMARK", "SCENMARK", "QRYMARK", "RATIONALEMARK", "EXTRAMARK",
                                            "locking the breaker", "quantum teleportation"))
        # Metrics come from the unredacted run.
        for arm in ("baseline", "improved"):
            for metric in ("recall_at_3", "mrr", "query_count"):
                self.assertEqual(report[arm][metric], plain[arm][metric])
        self.assertEqual(report["paired_randomization"], plain["paired_randomization"])

    def test_paths_labels_and_fingerprints_are_pseudonymised_with_the_key(self):
        # The Korean alias of "Rollback plan" brings a frontmatter_alias expansion with a source path.
        self.queries[2] = self.query(2, "되돌리기 계획", ["20-knowledge/Rollback plan.md"])
        self.save()
        code, plain_out, _ = self.run_compare("--redact")
        self.assertEqual(code, 0)
        self.assertTrue(all(text in plain_out for text in ("20-knowledge", "Checklist", ".md")))
        code, stdout, stderr = self.run_compare("--redact-key-file", self.key_a)
        self.assertEqual(code, 0, stderr)
        self.assert_hidden(stdout + stderr, GOLDSET_PATH_TEXT)
        report, plain = json.loads(stdout), json.loads(plain_out)
        arm = report["improved"]
        rows = arm["per_query"]
        moved = "20-knowledge/procedures/Switchgear isolation.md"
        self.assertEqual(rows[0]["expected_paths"], [hmac_hex(KEY_A, moved)])
        self.assertEqual(rows[0]["label_matches"], [{
            "field": "expected_paths", "label": hmac_hex(KEY_A, "20-knowledge/Switchgear isolation.md"),
            "match": "stem", "path": hmac_hex(KEY_A, moved)}])
        self.assertEqual(rows[0]["retrieved_paths"][0], hmac_hex(KEY_A, moved))
        self.assertEqual(rows[0]["relevance"], {hmac_hex(KEY_A, moved): 1})
        self.assertEqual(rows[1]["label_matches"], [{
            "field": "expected_paths", "label": hmac_hex(KEY_A, "Checklist.md"), "match": "ambiguous_stem",
            "path": None, "candidate_count": 2,
            "candidates": [hmac_hex(KEY_A, "20-knowledge/a/Checklist.md"),
                           hmac_hex(KEY_A, "20-knowledge/b/Checklist.md")]}])
        self.assertEqual(rows[7]["stale_retrieved_paths"],
                         [hmac_hex(KEY_A, "20-knowledge/history/Legacy rollback.md")])
        expansion = rows[2]["diagnostics"]["query_expansion"]
        alias = [entry for entry in expansion["expansions"] if entry["source_kind"] == "frontmatter_alias"]
        self.assertTrue(alias)
        self.assertEqual(alias[0]["source_path"], hmac_hex(KEY_A, "20-knowledge/Rollback plan.md"))
        plain_alias = [entry for entry in plain["improved"]["per_query"][2]["diagnostics"]["query_expansion"]
                       ["expansions"] if entry["source_kind"] == "frontmatter_alias"][0]
        self.assertEqual(alias[0]["source_sha256"], hmac_hex(KEY_A, plain_alias["source_sha256"]))
        for failed in arm["failed_queries"]:
            self.assertTrue(all(len(path) == 64 for path in failed["expected_paths"] + failed["retrieved_paths"]))
        # Whole-value fingerprints become keyed too; the key holder can still recompute them.
        section = arm["goldset"]
        self.assertEqual(section["goldset_canonical_sha256"],
                         hmac_hex(KEY_A, plain["improved"]["goldset"]["goldset_canonical_sha256"]))
        warning = ("queries[1].expected_paths[0]: filename stem is shared by 2 eligible notes "
                   "(paths pseudonymised in label_matches); label left unresolved, give the full "
                   "vault-relative path")
        self.assertIn(warning, section["warnings"])
        self.assertIn("queries[3].expected_paths[0]: label is inside a deny zone; it is never read or matched",
                      section["warnings"])
        self.assertEqual(len(section["warnings"]), len(plain["improved"]["goldset"]["warnings"]))
        self.assertIn(f"goldset warning (non-blocking): {warning}", stderr)
        self.assertEqual(section["label_match_counts"], plain["improved"]["goldset"]["label_match_counts"])

    def test_unknown_warning_text_is_hashed_whole(self):
        report = {"per_query": [], "by_scenario": {}, "failed_queries": [], "evaluation_errors": [],
                  "goldset": {"warnings": ["queries[0].expected_paths[0]: something new about a/b.md"]}}
        redacted = evaluator.redact_report(report, key=KEY_A)
        self.assertEqual(redacted["goldset"]["warnings"],
                         [hmac_hex(KEY_A, "queries[0].expected_paths[0]: something new about a/b.md")])

    def test_split_definition_fingerprint_is_keyed(self):
        split = self.root / "split.json"
        definition = {"seed": 3, "dev": [f"IDMARK{index:02d}" for index in range(6)],
                      "heldout": [f"IDMARK{index:02d}" for index in range(6, 12)]}
        split.write_text(json.dumps(definition), encoding="utf-8")
        _, plain, _ = self.run_goldset("--split", "dev", "--split-file", split, "--redact")
        code, stdout, stderr = self.run_goldset("--split", "dev", "--split-file", split,
                                                "--redact-key-file", self.key_a)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["split"]["definition_canonical_sha256"],
                         hmac_hex(KEY_A, json.loads(plain)["split"]["definition_canonical_sha256"]))
        self.assert_hidden(stdout + stderr, ("IDMARK", *GOLDSET_PATH_TEXT))

    def test_ephemeral_runs_differ_and_use_a_fresh_32_byte_key(self):
        code, first, stderr = self.run_compare("--redact-ephemeral")
        self.assertEqual(code, 0, stderr)
        _, second, _ = self.run_compare("--redact-ephemeral")
        self.assertNotEqual(canonical_report_text(first), canonical_report_text(second))
        report = json.loads(first)
        self.assertEqual((report["redaction_mode"], report["redaction_algorithm"]), ("ephemeral", "hmac-sha256"))
        self.assertEqual(report["redaction"], evaluator.EPHEMERAL_REDACTION_DESCRIPTION)
        self.assert_hidden(first + stderr, ("IDMARK", "QRYMARK", *GOLDSET_PATH_TEXT))
        with mock.patch.object(evaluator.secrets, "token_bytes", return_value=FIXED_EPHEMERAL) as token:
            code, stdout, stderr = self.run_compare("--redact-ephemeral")
        token.assert_called_once_with(32)
        self.assertEqual(code, 0, stderr)
        row = json.loads(stdout)["improved"]["per_query"][0]
        self.assertEqual(row["query"], hmac_hex(FIXED_EPHEMERAL, self.queries[0]["query"]))
        self.assert_hidden(stdout + stderr, key_forms(FIXED_EPHEMERAL))

    def test_key_material_never_appears_in_the_report_or_stderr(self):
        for argv in (["--redact-key-file", self.key_a], ["--redact", "--redact-key-file", self.key_a]):
            with self.subTest(argv=argv):
                code, stdout, stderr = self.run_compare(*argv)
                self.assertEqual(code, 0, stderr)
                self.assert_hidden(stdout + stderr, [*key_forms(KEY_A), "KEYPATHMARK", str(self.keys)])
                self.assertFalse([key for key in dict_keys(json.loads(stdout))
                                  if "fingerprint" in key.casefold() or "key" in key.casefold()])

    def test_short_missing_or_oversized_keys_are_refused_with_exit_2(self):
        unreadable = ("recall evaluation unavailable: redaction key file is not a readable regular file of at "
                      "most 65536 bytes")
        short = "recall evaluation unavailable: redaction key file must hold at least 32 bytes"
        cases = (
            (self.write_key("KEYPATHMARK-short.bin", KEY_A[:31]), short),
            (self.write_key("KEYPATHMARK-empty.bin", b""), short),
            (self.keys / "KEYPATHMARK-missing.bin", unreadable),
            (self.keys, unreadable),
            (self.write_key("KEYPATHMARK-big.bin", KEY_A * 2000), unreadable),
        )
        for path, message in cases:
            with self.subTest(path=path.name):
                with mock.patch.object(evaluator, "load_goldset", side_effect=AssertionError("evaluated")):
                    code, stdout, stderr = self.run_compare("--redact-key-file", path)
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(stderr.strip(), message)
                self.assert_hidden(stderr, [*key_forms(KEY_A), "KEYPATHMARK"])
        # Exactly 32 bytes is enough.
        code, _, stderr = self.run_compare("--redact-key-file", self.write_key("exact.bin", KEY_A[-32:]))
        self.assertEqual(code, 0, stderr)

    def test_key_inside_the_evaluated_vault_is_refused_before_evaluation(self):
        message = ("recall evaluation unavailable: redaction key file is inside the evaluated vault; "
                   "keep the key outside the vault and away from the report")
        inside = self.write_key("KEYPATHMARK.bin", KEY_A, self.vault / "00-meta")
        dotted = self.keys / ".." / "vault" / "00-meta" / "KEYPATHMARK.bin"
        for path in (inside, dotted):
            with self.subTest(path=str(path)):
                with mock.patch.object(evaluator, "load_goldset", side_effect=AssertionError("evaluated")):
                    code, stdout, stderr = self.run_compare("--redact-key-file", path)
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(stderr.strip(), message)
                self.assert_hidden(stderr, [*key_forms(KEY_A), "KEYPATHMARK"])
        # Fixture mode: the fixture directory holds the evaluated vault and its labels.
        fixture = lf_fixture_copy("recall", self.root)
        for path in (self.write_key("k.bin", KEY_A, fixture), self.write_key("k.bin", KEY_A, fixture / "vault")):
            with self.subTest(path=str(path)):
                code, stdout, stderr = run_main(evaluator, ["--fixture", fixture, "--redact-key-file", path])
                self.assertEqual((code, stdout, stderr.strip()), (2, "", message))
        code, stdout, stderr = run_main(evaluator, ["--fixture", fixture, "--redact-key-file", self.key_a])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["redaction_mode"], "keyed")

    KEY_INSIDE_MESSAGE = ("recall evaluation unavailable: redaction key file is inside the evaluated vault; "
                          "keep the key outside the vault and away from the report")

    def assert_key_refused(self, argv) -> None:
        with mock.patch.object(evaluator, "load_goldset", side_effect=AssertionError("evaluated")):
            code, stdout, stderr = run_main(evaluator, argv)
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(stderr.strip(), self.KEY_INSIDE_MESSAGE)
        self.assert_hidden(stderr, [*key_forms(KEY_A), "KEYPATHMARK"])

    @unittest.skipUnless(sys.platform == "win32", "extended-length paths are a Windows spelling")
    def test_extended_length_spellings_cannot_bypass_the_inside_vault_refusal(self):
        extended = "\\\\?\\"
        inside = self.write_key("KEYPATHMARK.bin", KEY_A, self.vault / "00-meta")
        cases = {
            "extended key": (self.vault, Path(extended + str(inside))),
            "extended vault": (Path(extended + str(self.vault)), inside),
            "both extended": (Path(extended + str(self.vault)), Path(extended + str(inside))),
        }
        for name, (vault, key) in cases.items():
            with self.subTest(case=name):
                self.assert_key_refused(["--vault", vault, "--goldset", self.goldset, "--min-recall-at-3", "0",
                                         "--min-mrr", "0", "--redact-key-file", key])
        # A key outside the vault stays accepted under the extended spelling.
        self.assertEqual(evaluator.read_redaction_key(Path(extended + str(self.key_a)),
                                                      Path(extended + str(self.vault))), KEY_A)

    @unittest.skipUnless(sys.platform == "win32", "administrative shares are a Windows spelling")
    def test_admin_share_spelling_cannot_bypass_the_inside_vault_refusal(self):
        # Only the file-identity branch of _key_inside sees through \\localhost\C$ spellings.
        inside = self.write_key("KEYPATHMARK.bin", KEY_A, self.vault / "00-meta")
        drive, rest = os.path.splitdrive(str(inside))
        vault_drive, vault_rest = os.path.splitdrive(str(self.vault))
        if len(drive) != 2 or drive[1] != ":" or vault_drive != drive:
            self.skipTest("the temporary directory is not on a lettered drive")
        share = "\\\\localhost\\" + drive[0] + "$"
        shared_key, shared_vault = Path(share + rest), Path(share + vault_rest)
        try:
            reachable = shared_key.is_file() and shared_vault.is_dir()
        except OSError:
            reachable = False
        if not reachable:
            self.skipTest("the administrative share is not reachable on this host")
        cases = {"share key": (self.vault, shared_key), "share vault": (shared_vault, inside)}
        for name, (vault, key) in cases.items():
            with self.subTest(case=name):
                self.assert_key_refused(["--vault", vault, "--goldset", self.goldset, "--min-recall-at-3", "0",
                                         "--min-mrr", "0", "--redact-key-file", key])
        # A key outside the vault stays accepted under the share spelling.
        outside_drive, outside_rest = os.path.splitdrive(str(self.key_a))
        if outside_drive == drive:
            self.assertEqual(evaluator.read_redaction_key(Path(share + outside_rest), shared_vault), KEY_A)

    @unittest.skipUnless(sys.platform == "win32", "extended-length paths are a Windows spelling")
    def test_extended_length_spelling_is_refused_without_the_identity_check(self):
        # Pins _plain_spelling on its own: with no file identity available, only the prefix stripping
        # lets the lexical comparison see that \\?\ spellings point inside the vault.
        inside = self.write_key("KEYPATHMARK.bin", KEY_A, self.vault / "00-meta")
        extended = "\\\\?\\"
        with mock.patch.object(evaluator, "_identity", return_value=None):
            self.assertTrue(evaluator._key_inside(Path(extended + str(inside)), self.vault))
            self.assertTrue(evaluator._key_inside(inside, Path(extended + str(self.vault))))
            self.assertFalse(evaluator._key_inside(Path(extended + str(self.key_a)), self.vault))

    @unittest.skipUnless(sys.platform == "win32", "directory junctions are a Windows feature")
    def test_key_reached_through_a_link_into_the_vault_is_refused(self):
        target = self.vault / "00-meta"
        self.write_key("KEYPATHMARK.bin", KEY_A, target)
        links = []
        junction = self.keys / "junction"
        make_junction(junction, target)
        self.addCleanup(os.rmdir, junction)  # removes the junction only; cleanups run before the tree removal
        links.append(junction)
        symlink = self.keys / "symlink"
        try:
            os.symlink(target, symlink, target_is_directory=True)
        except OSError:
            pass  # creating symlinks needs Developer Mode or the privilege
        else:
            self.addCleanup(os.rmdir, symlink)
            links.append(symlink)
        for link in links:
            with self.subTest(link=link.name):
                # The written path lies outside the vault; only the resolved path is inside.
                self.assertNotIn(self.vault, Path(os.path.abspath(link / "KEYPATHMARK.bin")).parents)
                self.assert_key_refused(["--vault", self.vault, "--goldset", self.goldset,
                                         "--min-recall-at-3", "0", "--min-mrr", "0",
                                         "--redact-key-file", link / "KEYPATHMARK.bin"])

    def test_key_next_to_the_report_warns_without_blocking(self):
        warning = ("recall evaluation warning (non-blocking): the redaction key file is in the report's "
                   "output directory or the working directory; keep the key away from the report")
        argv = ["--vault", self.vault, "--goldset", self.goldset, "--min-recall-at-3", "0", "--min-mrr", "0",
                "--redact-key-file", self.key_a]
        for directory, warned in ((self.keys, True), (self.root, False)):
            with self.subTest(directory=directory.name):
                stderr = io.StringIO()
                with open(directory / "report.json", "w", encoding="utf-8") as report, \
                        redirect_stdout(report), redirect_stderr(stderr):
                    code = evaluator.main([str(value) for value in argv])
                self.assertEqual(code, 0, stderr.getvalue())
                self.assertEqual(warning in stderr.getvalue(), warned)
                self.assertEqual(json.loads((directory / "report.json").read_text(encoding="utf-8"))
                                 ["redaction_mode"], "keyed")
        # Standard output that is not a file (a pipe, PowerShell 5.1 redirection): the working directory.
        with mock.patch.object(evaluator.os, "getcwd", return_value=str(self.keys)):
            code, _, stderr = run_main(evaluator, argv)
        self.assertEqual(code, 0, stderr)
        self.assertIn(warning, stderr)
        code, _, stderr = run_main(evaluator, argv)
        self.assertNotIn(warning, stderr)

    def test_keyed_options_imply_redact_and_exclude_each_other(self):
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            evaluator._parser().parse_args(["--redact-key-file", str(self.key_a), "--redact-ephemeral"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with argument", error.getvalue())
        self.assert_hidden(error.getvalue(), ["KEYMARK"])
        for argv in (["--derive-split", "--redact-ephemeral"], ["--derive-split", "--redact-key-file", self.key_a]):
            with self.subTest(argv=argv):
                code, stdout, stderr = run_main(evaluator, argv)
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(stderr.strip(), "--derive-split cannot be combined with --split or --redact")
        code, stdout, stderr = run_main(evaluator, ["--redact-ephemeral"])
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["redaction_mode"], "ephemeral")
        self.assertNotIn(".md", stdout)

    def test_help_text_carries_the_linkability_warning(self):
        flat = " ".join(evaluator._parser().format_help().split())
        for phrase in ("--redact-key-file", "--redact-ephemeral", "repeats and frequencies",
                       "without the key", "not hashed one by one", "rows keep the gold-set order"):
            self.assertIn(phrase, flat)


class KeyedRedactReportTests(unittest.TestCase):
    report = RedactionTests.report

    def test_keyed_redaction_hashes_text_and_paths_with_the_key(self):
        source = self.report()
        redacted = evaluator.redact_report(source, key=KEY_A)
        text = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn("SECRET", text)
        self.assertNotIn(".md", text)
        row = redacted["per_query"][0]
        self.assertEqual(row["query"], hmac_hex(KEY_A, "SECRET words"))
        self.assertEqual(row["custom"], hmac_hex(KEY_A, '{"nested":"SECRET"}'))
        self.assertEqual(row["expected_paths"], [hmac_hex(KEY_A, "a.md")])
        self.assertEqual(row["relevance"], {hmac_hex(KEY_A, "a.md"): 1})
        self.assertEqual(row["external_candidates"], [
            {"path": hmac_hex(KEY_A, "a.md"), "rank": 1, "note": hmac_hex(KEY_A, "provider SECRET")},
            hmac_hex(KEY_A, "b.md")])
        options = row["retrieval_options"]
        self.assertEqual({key: options[key] for key in ("as_of", "backend", "expand_links", "excluded_sources")},
                         {"as_of": "2026-01-01", "backend": "hybrid", "expand_links": 1,
                          "excluded_sources": [hmac_hex(KEY_A, "x/y.md")]})
        self.assertEqual((row["language"], row["source_recall_at_3"]), ("ko", 1.0))
        self.assertEqual(redacted["failed_queries"][0]["expected_paths"], [hmac_hex(KEY_A, "a.md")])
        self.assertEqual((redacted["redaction_mode"], redacted["redaction_algorithm"]), ("keyed", "hmac-sha256"))
        self.assertIn("SECRET", json.dumps(source))  # the input report is not modified
        ephemeral = evaluator.redact_report(source, key=KEY_A, ephemeral=True)
        self.assertEqual(ephemeral["redaction_mode"], "ephemeral")
        self.assertEqual(ephemeral["per_query"], redacted["per_query"])
        plain = evaluator.redact_report(source)
        self.assertEqual((plain["redaction_mode"], plain["redaction_algorithm"]), ("unkeyed", "sha256"))
        self.assertEqual(plain["per_query"][0]["expected_paths"], ["a.md"])

    def test_temporal_links_and_non_date_values_are_keyed(self):
        source = self.report()
        source["per_query"][0]["diagnostics"] = {"status": "ok", "temporal": [
            {"path": "people/Kim/Old.md", "status": "invalid", "currentness": "unknown",
             "valid_from": "2026-01-01", "valid_until": "until Kim leaves", "checked_at": "2026-02-03T04:05:06Z",
             "superseded_by": "[[people/Kim/Note]]", "errors": ["invalid_valid_until"]},
            {"path": "a.md", "status": "superseded", "currentness": "unknown", "valid_from": "2026-01-01",
             "checked_at": "Kim said so", "superseded_by": "[[Note]]", "superseded_at": "2026-05-01",
             "supersession_source": {"path": "people/Kim/Note.md", "sha256": "ab" * 32}, "errors": []}]}
        redacted = evaluator.redact_report(source, key=KEY_A)
        text = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn("Kim", text)
        self.assertNotIn("Note", text)
        first, second = redacted["per_query"][0]["diagnostics"]["temporal"]
        self.assertEqual(first["superseded_by"], hmac_hex(KEY_A, "[[people/Kim/Note]]"))
        self.assertEqual(first["valid_until"], hmac_hex(KEY_A, "until Kim leaves"))
        self.assertEqual((first["valid_from"], first["checked_at"]), ("2026-01-01", "2026-02-03T04:05:06Z"))
        self.assertEqual((first["status"], first["currentness"], first["errors"]),
                         ("invalid", "unknown", ["invalid_valid_until"]))
        self.assertEqual(second["superseded_by"], hmac_hex(KEY_A, "[[Note]]"))
        self.assertEqual(second["checked_at"], hmac_hex(KEY_A, "Kim said so"))
        self.assertEqual((second["valid_from"], second["superseded_at"]), ("2026-01-01", "2026-05-01"))
        self.assertEqual(second["supersession_source"], {"path": hmac_hex(KEY_A, "people/Kim/Note.md"),
                                                         "sha256": hmac_hex(KEY_A, "ab" * 32)})
        # Plain --redact keeps vault-side values, as before.
        plain = evaluator.redact_report(source)["per_query"][0]["diagnostics"]["temporal"]
        self.assertEqual(plain, source["per_query"][0]["diagnostics"]["temporal"])

    def test_short_or_non_bytes_keys_are_rejected(self):
        for key in (KEY_A[:31], b"", "x" * 40, bytearray(KEY_A)):
            with self.subTest(key=type(key).__name__), self.assertRaises(ValueError) as raised:
                evaluator.redact_report(self.report(), key=key)
            self.assertNotIn("KEYMARK", str(raised.exception))
        with self.assertRaises(ValueError):
            evaluator.redact_report(self.report(), ephemeral=True)


class KeyedFixtureLeakTests(unittest.TestCase):
    """Keyed reports of a fixture name no note: no stem, folder or wikilink target."""

    FIXTURE = FIXTURES / "recall_advanced"
    CASES = (["--advanced"], ["--advanced", "--as-of", "2026-10-01"], ["--compare", "--render-matched"])
    # Retrieval enums such as status "unknown" may equal a fixture stem.
    ENUM_FIELDS = frozenset({"status", "currentness"})

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.key = Path(temporary.name) / "k.bin"
        self.key.write_bytes(KEY_A)
        vault = self.FIXTURE / "vault"
        names = set()
        for path in vault.rglob("*.md"):
            names.update(part for part in (path.stem, *path.relative_to(vault).parts[:-1]) if len(part) > 1)
        self.names = sorted(name.casefold() for name in names)
        self.assertIn("phoenix-current", self.names)

    def leaks(self, value, field=None) -> list[str]:
        if isinstance(value, dict):
            # The fixed redaction description is compared exactly elsewhere.
            return [leak for key, item in value.items() if key != "redaction" for leak in self.leaks(item, key)]
        if isinstance(value, list):
            return [leak for item in value for leak in self.leaks(item, field)]
        if not isinstance(value, str) or (field in self.ENUM_FIELDS and value.isidentifier()):
            return []
        return [f"{field}={value}" for name in self.names if name in value.casefold()]

    def test_unkeyed_redaction_leaves_the_successor_link_readable(self):
        # The scan works: plain --redact keeps vault-side paths and links by design.
        code, stdout, stderr = run_main(evaluator, ["--fixture", self.FIXTURE, "--advanced", "--redact"])
        self.assertEqual(code, 0, stderr)
        self.assertIn("superseded_by=[[phoenix-current]]", self.leaks(json.loads(stdout)))

    def test_keyed_and_ephemeral_reports_name_no_fixture_note(self):
        for mode in (["--redact-key-file", self.key], ["--redact-ephemeral"]):
            for args in self.CASES:
                with self.subTest(mode=mode[0], args=args):
                    code, stdout, stderr = run_main(evaluator, ["--fixture", self.FIXTURE, *args, *mode])
                    self.assertEqual(code, 0, stderr)
                    self.assertEqual(self.leaks(json.loads(stdout)), [])
                    self.assertNotIn(".md", stdout)
                    self.assertEqual([name for name in self.names if name in stderr.casefold()], [])


# --- Row order of pseudonymised reports (--redact-row-order) ---------------
# The v0.31.0 keyed limits text; without --redact-row-order token it must stay byte-identical.
V0310_KEYED_LIMITS = (
    "Pseudonymisation, not encryption or anonymisation. Equal values get equal tokens within this report, so "
    "repeats and frequencies stay visible. Whole values are hashed, not single words. Rows keep the gold-set "
    "order, so whoever holds the gold set, or part of it, can link rows, and the path tokens labelled in them, "
    "by position; sort or shuffle rows before sharing to avoid that. The report never records the key or a "
    "fingerprint of it.")
ROW_LISTS = ("per_query", "failed_queries", "evaluation_errors")
HARD_ARMS = ("baseline", "expansion_ablation", "improved")
TOKEN_ORDER_ERROR = ("--redact-row-order token requires --redact-key-file or --redact-ephemeral; whoever holds the "
                     "gold set can recompute unkeyed sha256 ids and undo the order")


def frozen_main(module, argv) -> tuple[int, str, str]:
    """run_main() with a frozen clock, so two runs print byte-identical JSON."""
    counter = itertools.count()
    with mock.patch.object(module.time, "perf_counter", new=lambda: next(counter) * 0.001):
        return run_main(module, argv)


def gold_ordered(report: dict) -> dict:
    """The report with every row list sorted by id and the token-order fields left out."""
    def arm(value: dict) -> dict:
        return {**value, **{name: sorted(value[name], key=lambda row: row["id"]) for name in ROW_LISTS}}
    ordered = {name: (arm(value) if isinstance(value, dict) and "per_query" in value else value)
               for name, value in report.items() if name != "row_order"}
    ordered["redaction"] = {key: value for key, value in report["redaction"].items() if key != "limits"}
    return ordered


class TokenRowOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.key = Path(temporary.name) / "keys" / "k.bin"
        self.key.parent.mkdir()
        self.key.write_bytes(KEY_A)
        self.queries = evaluator._load_queries(RECALL_HARD)

    def run_hard(self, *extra) -> tuple[int, str, str]:
        return frozen_main(evaluator, ["--fixture", RECALL_HARD, *HARD_CI, "--top-k", "3", *extra])

    def run_legacy(self, *extra) -> tuple[int, str, str]:
        """One legacy arm of the 64 recall_hard rows, with an optional gate that fails (exit 1)."""
        return frozen_main(evaluator, ["--fixture", RECALL_HARD, "--min-recall-at-3", "0", "--min-mrr", "0",
                                       "--max-no-answer-false-positive-rate", "0.25", *extra])

    def test_token_order_sorts_every_arm_by_the_keyed_id_after_the_gates(self):
        gold_code, gold_out, gold_err = self.run_hard("--redact-key-file", self.key)
        code, stdout, stderr = self.run_hard("--redact-key-file", self.key, "--redact-row-order", "token")
        self.assertEqual((code, stderr), (gold_code, gold_err))
        self.assertEqual(code, 0)
        gold, token = json.loads(gold_out), json.loads(stdout)
        self.assertEqual(token["row_order"], "sorted_by_keyed_id_token")
        self.assertNotIn("row_order", gold)
        # Gold order is the queries.json order; token order is the sorted tokens, the same in every arm.
        gold_ids = [hmac_hex(KEY_A, item["id"]) for item in self.queries]
        self.assertEqual([row["id"] for row in gold["improved"]["per_query"]], gold_ids)
        self.assertNotEqual(sorted(gold_ids), gold_ids)
        for arm in HARD_ARMS:
            with self.subTest(arm=arm):
                self.assertEqual([row["id"] for row in token[arm]["per_query"]], sorted(gold_ids))
                for name in ROW_LISTS:
                    ids = [row["id"] for row in token[arm][name]]
                    self.assertEqual(ids, sorted(ids))
                    self.assertEqual(token[arm][name], sorted(gold[arm][name], key=lambda row: row["id"]))
        self.assertTrue(token["baseline"]["failed_queries"])
        # Gates, paired statistics and every other field are those of the gold-order run.
        self.assertEqual(token["improved"]["optional_gates"], gold["improved"]["optional_gates"])
        self.assertEqual(token["paired_randomization"], gold["paired_randomization"])
        self.assertEqual(gold_ordered(token), gold_ordered(gold))
        self.assertEqual(gold["redaction"]["limits"], evaluator.KEYED_REDACTION_DESCRIPTION["limits"])
        limits = token["redaction"]["limits"]
        self.assertIn("--redact-row-order token", limits)
        self.assertIn("can no longer link rows by position", limits)
        self.assertNotIn("sort or shuffle", limits)
        self.assertTrue(limits.startswith("Pseudonymisation, not encryption or anonymisation."))
        self.assertTrue(limits.endswith(evaluator.KEYED_REDACTION_DESCRIPTION["limits"][len(V0310_KEYED_LIMITS):]))

    def test_same_key_repeats_the_order_and_ephemeral_keys_change_it(self):
        argv = ["--redact-key-file", self.key, "--redact-row-order", "token"]
        first = self.run_legacy(*argv)
        self.assertEqual(first, self.run_legacy(*argv))
        gold_code, gold_out, _ = self.run_legacy("--redact-key-file", self.key)
        # The failing gate keeps its exit code and observed value under token order.
        self.assertEqual((first[0], gold_code), (1, 1))
        self.assertEqual(json.loads(first[1])["optional_gates"], json.loads(gold_out)["optional_gates"])
        second = b"KEYMARK-second-ephemeral:" + bytes(range(64, 96))
        orders = []
        for key in (FIXED_EPHEMERAL, second):
            with mock.patch.object(evaluator.secrets, "token_bytes", return_value=key):
                code, stdout, stderr = self.run_legacy("--redact-ephemeral", "--redact-row-order", "token")
            self.assertEqual(code, 1, stderr)
            report = json.loads(stdout)
            self.assertEqual((report["redaction_mode"], report["row_order"]), ("ephemeral", "sorted_by_keyed_id_token"))
            plain = {hmac_hex(key, item["id"]): item["id"] for item in self.queries}
            order = [plain[row["id"]] for row in report["per_query"]]
            self.assertEqual(order, sorted(order, key=lambda query_id: hmac_hex(key, query_id)))
            orders.append(order)
        self.assertNotEqual(orders[0], orders[1])
        self.assertEqual(sorted(orders[0]), sorted(orders[1]))

    def test_row_shapes_still_group_the_sorted_rows(self):
        # The residual leak docs/memory-evaluation.md measures: with positions hidden, a row's shape (language,
        # expectation, scenario token, numbers of expected, claim, forbidden and stale paths, relevance grade
        # multiset) still splits the 64 recall_hard rows into 14 classes, two of them a single row.
        code, stdout, stderr = self.run_legacy("--redact-key-file", self.key, "--redact-row-order", "token")
        self.assertEqual(code, 1, stderr)
        rows = json.loads(stdout)["per_query"]
        shapes = Counter((row["language"], row["expectation"], row["scenario"], len(row["expected_paths"]),
                          len(row["required_claims"]), len(row["forbidden_paths"]), len(row["stale_paths"]),
                          tuple(sorted(row["relevance"].values()))) for row in rows)
        self.assertEqual(len(rows), 64)
        self.assertEqual(sorted(shapes.values()), [1, 1, 3, 3, 4, 4, 6, 6, 6, 6, 6, 6, 6, 6])
        self.assertTrue(all(len(row["scenario"]) == 64 for row in rows))

    def test_deterministic_row_values_link_most_sorted_rows(self):
        # The stronger channel docs/memory-evaluation.md measures: each row's results and diagnostics stay in
        # clear and repeat on a rerun. With the shape they single out 37 of the 64 recall_hard rows in the
        # baseline arm, and 60 when the four --compare --render-matched arms of a row are read together.
        code, stdout, stderr = self.run_hard("--render-matched", "--redact-key-file", self.key,
                                             "--redact-row-order", "token")
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)

        def values(row):
            diagnostics, expansion = row["diagnostics"], row["diagnostics"].get("query_expansion")
            return (row["language"], row["expectation"], row["scenario"], len(row["expected_paths"]),
                    len(row["required_claims"]), len(row["forbidden_paths"]), len(row["stale_paths"]),
                    tuple(sorted(row["relevance"].values())), row["source_recall_at_3"],
                    row["reciprocal_rank_at_3"], row["ndcg_at_5"], len(row["retrieved_paths"]),
                    len(row["forbidden_retrieved_paths"]), len(row["stale_retrieved_paths"]),
                    row["estimated_returned_context_tokens"], diagnostics["bytes_read"], diagnostics["files_read"],
                    diagnostics["query_terms_used"], None if expansion is None else len(expansion["terms"]))

        def unique(keys) -> int:
            return sum(count == 1 for count in Counter(keys).values())

        arms = [report[arm]["per_query"] for arm in (*HARD_ARMS, "render_matched")]
        self.assertTrue(all([row["id"] for row in rows] == [row["id"] for row in arms[0]] for rows in arms))
        self.assertEqual(len(arms[0]), 64)
        self.assertEqual(unique(values(row) for row in report["baseline"]["per_query"]), 37)
        self.assertEqual(unique(tuple(values(rows[index]) for rows in arms) for index in range(64)), 60)
        # The token-order limits name this channel and the twin exclusion lists that keep vault path order.
        limits = report["redaction"]["limits"]
        for phrase in ("metric values", "retrieved, forbidden and stale path counts",
                       "estimated_returned_context_tokens", "bytes_read, files_read",
                       "query_terms_used (without query expansion it follows from the query text alone)",
                       "query-expansion counts", "a rerun on the same vault gives the same values",
                       "can match each row whose values are unique",
                       "gold-removal twin excluded_sources list removed notes in vault path order"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, limits)

    def test_token_order_needs_a_keyed_or_ephemeral_redaction(self):
        for argv in (["--redact-row-order", "token"], ["--redact", "--redact-row-order", "token"],
                     ["--fixture", RECALL_HARD, "--derive-split", "--redact-row-order", "token"]):
            with self.subTest(argv=argv):
                code, stdout, stderr = run_main(evaluator, argv)
                self.assertEqual((code, stdout, stderr.strip()), (2, "", TOKEN_ORDER_ERROR))
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            evaluator._parser().parse_args(["--redact-row-order", "shuffle"])
        self.assertEqual(raised.exception.code, 2)
        plain = evaluator.redact_report(evaluator.evaluate(RECALL_HARD))
        with self.assertRaises(ValueError):
            evaluator.with_token_row_order(plain)

    def test_gold_order_is_the_default_and_leaves_output_byte_identical(self):
        for mode in (["--redact-key-file", self.key], ["--redact"]):
            with self.subTest(mode=mode[0]):
                self.assertEqual(self.run_legacy(*mode, "--redact-row-order", "gold"), self.run_legacy(*mode))
        self.assertEqual(frozen_main(evaluator, ["--redact-row-order", "gold"]), frozen_main(evaluator, []))
        self.assertEqual(evaluator.KEYED_REDACTION_DESCRIPTION["limits"][:len(V0310_KEYED_LIMITS)],
                         V0310_KEYED_LIMITS)
        self.assertEqual(evaluator.EPHEMERAL_REDACTION_DESCRIPTION["limits"][:len(V0310_KEYED_LIMITS)],
                         V0310_KEYED_LIMITS)
        flat = " ".join(evaluator._parser().format_help().split())
        for phrase in ("--redact-row-order {gold,token}", "keyed id token", "cannot link rows by position",
                       "deterministic metric values of each row remain", "can still match rows"):
            self.assertIn(phrase, flat)
        # Both keyed options name the exception to their gold-order sentence (wide enough not to wrap).
        with mock.patch.dict(os.environ, {"COLUMNS": "1000"}):
            wide = evaluator._parser().format_help()
        self.assertEqual(wide.count("rows keep the gold-set order unless --redact-row-order token is given"), 2)
        self.assertEqual(wide.count("rows keep the gold-set order"), 2)


if __name__ == "__main__":
    unittest.main()
