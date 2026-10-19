"""Opt-in in-place gold-set evaluation, dev/held-out split and redaction.

Runs without the new options must keep the v0.24.0 output. The golden digests in
tests/fixtures/recall_default_outputs.json were captured from d2b9315 (v0.24.0)
before these options existed, with capture_default_outputs() below.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

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

    def test_fixture_mode_ignores_a_committed_split_without_the_flag(self):
        # recall_hard now ships split.json; only --split reads it.
        with mock.patch.object(evaluator, "load_split", side_effect=AssertionError("split read")):
            report = evaluator.evaluate(RECALL_HARD)
        self.assertEqual(report["query_count"], 64)
        self.assertNotIn("split", report)


class GoldSetVaultTests(unittest.TestCase):
    """A temporary real-shaped vault evaluated in place."""

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


if __name__ == "__main__":
    unittest.main()
