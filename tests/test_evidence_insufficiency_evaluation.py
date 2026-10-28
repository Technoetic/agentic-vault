"""Real offline scoring and replay boundaries for synthetic insufficiency cases."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_evidence_insufficiency.py"


class EvidenceInsufficiencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location("insufficiency_eval", SCRIPT)
            cls.ev = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.ev)

    def setUp(self):
        self.assertIsNotNone(self.ev, "Task B evaluator has not been implemented")
        self.pack = self.ev.load_pack()

    def rows(self, report):
        return {row["case_id"]: row for row in report["results"]}

    def response(self, kind, slot="meeting", value="개최", polarity="negative"):
        return {"kind": kind, "claims": [{"slot": slot, "value": value, "polarity": polarity}]}

    def test_missing_acknowledgement_differs_from_unsupported_denial(self):
        good = self.rows(self.ev.evaluate(self.pack))
        bad = self.rows(self.ev.evaluate(self.pack, control="deny_missing"))
        self.assertEqual(good["missing"]["category"], "insufficiency_acknowledged")
        self.assertTrue(good["missing"]["correct"])
        self.assertEqual(bad["missing"]["category"], "unsupported_premise_denial")
        self.assertFalse(bad["missing"]["correct"])

    def test_partial_coverage_only_counts_declared_slots(self):
        row = self.rows(self.ev.evaluate(self.pack))["partial"]
        self.assertEqual(row["coverage"], {
            "declared_slots": 2, "covered_slots": 1, "missing_slots": ["place"],
            "ratio": 0.5, "coverage_low": True, "evidence_complete": True})
        self.assertEqual(row["category"], "insufficiency_acknowledged")

    def test_incomplete_negative_is_never_certified(self):
        pack = copy.deepcopy(self.pack)
        case = next(c for c in pack["cases"] if c["id"] == "truncated")
        case["evidence"]["records"] = [{"slot": "inspection", "value": "실시", "polarity": "negative"}]
        row = self.rows(self.ev.evaluate(pack, answerer=lambda p: self.response(
            "answer", "inspection", "실시")))["truncated"]
        self.assertEqual(row["category"], "unsupported_premise_denial")
        self.assertFalse(row["certified_negative"])
        self.assertFalse(row["correct"])

    def test_explicit_negative_is_distinct_from_missing(self):
        row = self.rows(self.ev.evaluate(self.pack))["explicit_negative"]
        self.assertEqual(row["category"], "supported_answer")
        self.assertTrue(row["correct"])
        self.assertTrue(row["certified_negative"])

    def test_answer_kind_cannot_hide_unsupported_negative(self):
        row = self.rows(self.ev.evaluate(self.pack, answerer=lambda p:
            self.response("answer")))["missing"]
        self.assertEqual(row["category"], "unsupported_premise_denial")

    def test_supported_negative_may_be_declared_as_premise_denial(self):
        def answer(payload):
            response = self.ev.synthetic_answer(payload)
            if payload["id"] == "explicit_negative":
                response["kind"] = "premise_denial"
            return response
        self.assertTrue(self.rows(self.ev.evaluate(self.pack, answerer=answer))[
            "explicit_negative"]["certified_negative"])

    def test_incomplete_positive_answer_is_not_a_supported_full_answer(self):
        row = self.rows(self.ev.evaluate(self.pack, answerer=lambda p:
            {"kind": "answer", "claims": p["evidence"]["records"]}))["truncated"]
        self.assertEqual(row["category"], "other_error")
        self.assertFalse(row["correct"])

    def test_callback_only_sees_copied_public_case(self):
        original = copy.deepcopy(self.pack)
        seen = []
        def answer(payload):
            self.assertEqual(set(payload), {"id", "question", "claim_slots", "evidence"})
            seen.append(payload["id"])
            result = self.ev.synthetic_answer(payload)
            payload["evidence"]["records"].clear()
            payload["question"] = "mutated"
            return result
        result = self.ev.evaluate(self.pack, answerer=answer)
        self.assertEqual(self.pack, original)
        self.assertEqual(len(seen), 5)
        self.assertEqual(result["summary"]["correct"], 5)

    def test_controls_have_no_label_access(self):
        payload = copy.deepcopy(self.pack["cases"][0])
        self.assertEqual(self.ev.synthetic_answer(payload)["kind"], "answer")
        self.assertNotIn("labels", payload)

    def test_empty_population_has_null_rate(self):
        pack = {**self.pack, "cases": [], "labels": {}}
        report = self.ev.evaluate(pack)
        self.assertEqual(report["summary"]["total"], 0)
        self.assertIsNone(report["summary"]["accuracy"])

    def test_replay_roundtrip(self):
        first = self.ev.evaluate(self.pack)
        replay = self.ev.evaluate(self.pack, observations=first["observations"])
        self.assertEqual(first["results"], replay["results"])
        self.assertEqual(replay["provenance"]["mode"], "observation_replay")
        self.assertEqual(replay["provenance"]["model_requests"], 0)
        self.assertIsNone(replay["provenance"]["actual_cost"])

    def test_replay_binds_question_evidence_fixture_and_config(self):
        observations = self.ev.evaluate(self.pack)["observations"]
        for change in ("question", "evidence", "description", "order"):
            pack = copy.deepcopy(self.pack)
            if change == "question":
                pack["cases"][0]["question"] += " 변경"
            elif change == "evidence":
                pack["cases"][0]["evidence"]["records"][0]["value"] = "15:00"
                pack["labels"]["answerable"]["expected_claims"][0]["value"] = "15:00"
            elif change == "order":
                pack["cases"].reverse()
            else:
                pack[change] += " 변경"
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ev.evaluate(pack, observations=observations)
        with self.assertRaises(ValueError):
            self.ev.evaluate(self.pack, observations=observations, coverage_low_threshold=0.25)

    def test_replay_rejects_missing_extra_duplicate_and_malformed(self):
        baseline = self.ev.evaluate(self.pack)["observations"]
        for mode in ("missing", "extra", "duplicate", "binding", "response", "origin", "metadata"):
            changed = copy.deepcopy(baseline)
            rows = changed["observations"]
            if mode == "missing":
                rows.pop()
            elif mode == "extra":
                extra = copy.deepcopy(rows[0]); extra["case_id"] = "unexpected"; rows.append(extra)
            elif mode == "duplicate":
                rows[-1] = copy.deepcopy(rows[0])
            elif mode == "binding":
                rows[0]["binding"]["evidence_digest"] = "0" * 64
            elif mode == "response":
                rows[0]["response"] = {"kind": "answer", "claims": []}
            elif mode == "origin":
                rows[0]["origin"] = "actual_model"
            else:
                changed["invented_cost"] = 0
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.ev.evaluate(self.pack, observations=changed)

    def test_external_observations_are_caller_attested(self):
        obs = self.ev.evaluate(self.pack)["observations"]
        for row in obs["observations"]:
            row["origin"] = "external_observation"; row["control"] = None
        report = self.ev.evaluate(self.pack, observations=obs)
        self.assertEqual(report["provenance"]["origins"], ["external_observation"])
        self.assertTrue(report["provenance"]["caller_attested"])

    def test_threshold_is_diagnostic_and_cannot_change_grading(self):
        strict = self.ev.evaluate(self.pack)
        relaxed = self.ev.evaluate(self.pack, coverage_low_threshold=0.25)
        self.assertEqual(strict["summary"], relaxed["summary"])
        self.assertTrue(self.rows(strict)["partial"]["coverage"]["coverage_low"])
        self.assertFalse(self.rows(relaxed)["partial"]["coverage"]["coverage_low"])
        self.assertFalse(self.rows(relaxed)["truncated"]["certified_negative"])

    def test_partial_acknowledgement_cannot_add_unsupported_claim(self):
        report = self.ev.evaluate(self.pack, answerer=lambda p: {
            "kind": "insufficient", "claims": [
                {"slot": "place", "value": "모의 장소", "polarity": "positive"}]})
        self.assertEqual(self.rows(report)["partial"]["category"], "other_error")

    def test_partial_supported_negative_is_not_full_answer_or_certificate(self):
        case = next(c for c in self.pack["cases"] if c["id"] == "partial")
        case["evidence"]["records"][0]["polarity"] = "negative"
        report = self.ev.evaluate(self.pack, answerer=lambda p: {
            "kind": "answer", "claims": p["evidence"]["records"]})
        row = self.rows(report)["partial"]
        self.assertEqual(row["category"], "other_error")
        self.assertFalse(row["certified_negative"])

    def test_execution_errors_produce_nonreplayable_incomplete_envelope(self):
        report = self.ev.evaluate(self.pack, answerer=lambda p: None)
        self.assertFalse(report["observation_export_complete"])
        with self.assertRaises(ValueError):
            self.ev.evaluate(self.pack, observations=report["observations"])

    def test_empty_pack_replay_and_too_many_cases(self):
        empty = {**self.pack, "cases": [], "labels": {}}
        observations = self.ev.evaluate(empty)["observations"]
        self.assertIsNone(self.ev.evaluate(empty, observations=observations)["summary"]["accuracy"])
        self.pack["cases"] *= 21
        with self.assertRaises(ValueError):
            self.ev.evaluate(self.pack)

    def test_callback_errors_and_malformed_results_cannot_score_good(self):
        for answer in (lambda p: (_ for _ in ()).throw(RuntimeError("sensitive")),
                       lambda p: {"kind": "insufficient", "claims": [], "secret": "hidden"},
                       lambda p: {"kind": "prose", "claims": []}):
            report = self.ev.evaluate(self.pack, answerer=answer)
            self.assertEqual(report["summary"]["correct"], 0)
            self.assertEqual(report["summary"]["categories"], {"execution_error": 5})
            self.assertNotIn("sensitive", json.dumps(report))

    def test_schema_rejects_unbounded_unknown_and_conflicting_values(self):
        changes = [lambda p: p.update(schema_version=True),
                   lambda p: p.update(unexpected="value"),
                   lambda p: p["cases"][0].update(question="x" * 2049),
                   lambda p: p["cases"][0]["evidence"].update(complete=1),
                   lambda p: p["cases"][0]["evidence"]["records"].append(
                       copy.deepcopy(p["cases"][0]["evidence"]["records"][0])),
                   lambda p: p["cases"][0]["evidence"]["records"][0].update(slot="unknown"),
                   lambda p: p["labels"].pop("missing")]
        for change in changes:
            pack = copy.deepcopy(self.pack); change(pack)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ev.evaluate(pack)
        for threshold in (True, float("nan"), -1, 1.1):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                self.ev.evaluate(self.pack, coverage_low_threshold=threshold)

    def test_response_bounds_and_unknown_slot_fail_closed(self):
        for response in ({"kind": "answer", "claims": []},
                         {"kind": "premise_denial", "claims": [{
                             "slot": "meeting", "value": "개최", "polarity": "positive"}]},
                         {"kind": "answer", "claims": [self.response("answer")["claims"][0]] * 33}):
            report = self.ev.evaluate(self.pack, answerer=lambda p: response)
            self.assertEqual(report["summary"]["correct"], 0)
        row = self.rows(self.ev.evaluate(self.pack, answerer=lambda p:
            self.response("answer", "unrequested", "unknown", "positive")))["answerable"]
        self.assertEqual(row["category"], "other_error")

    def test_strict_json_rejects_duplicate_nonfinite_deep_large_and_boolean(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pack.json"
            for raw in ('{"synthetic": true, "synthetic": true}', '{"n": NaN}',
                        '{"n": 1e999}', '[' * 50 + '0' + ']' * 50,
                        ' ' * (1024 * 1024 + 1), '{"schema_version": true}'):
                path.write_text(raw, encoding="utf-8")
                with self.subTest(raw=raw[:50]), self.assertRaises(ValueError):
                    self.ev.load_pack(path)

    def test_stable_read_allows_cross_api_ctime_representation_difference(self):
        real_fstat = os.fstat
        def different_ctime(descriptor):
            info = real_fstat(descriptor)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink", "st_mode")
            values = {field: getattr(info, field) for field in fields}
            values["st_ctime_ns"] += 1
            return SimpleNamespace(**values)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pack.json"
            path.write_text(json.dumps(self.pack), encoding="utf-8")
            with mock.patch.object(self.ev.os, "fstat", side_effect=different_ctime):
                self.assertEqual(self.ev.load_pack(path), self.pack)

    def test_stable_read_rejects_same_api_ctime_change(self):
        real_fstat = os.fstat
        calls = 0
        def changing_ctime(descriptor):
            nonlocal calls
            calls += 1
            info = real_fstat(descriptor)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink", "st_mode")
            values = {field: getattr(info, field) for field in fields}
            values["st_ctime_ns"] += calls
            return SimpleNamespace(**values)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pack.json"
            path.write_text(json.dumps(self.pack), encoding="utf-8")
            with mock.patch.object(self.ev.os, "fstat", side_effect=changing_ctime), self.assertRaises(ValueError):
                self.ev.load_pack(path)

    def test_unsafe_hardlink_input_and_existing_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pack.json"
            path.write_text(json.dumps(self.pack), encoding="utf-8")
            linked = Path(temp) / "linked.json"
            os.link(path, linked)
            with self.assertRaises(ValueError):
                self.ev.load_pack(linked)
            result = subprocess.run([sys.executable, str(SCRIPT), "--write-observations", str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.pack)

    def test_symlink_input_and_parent_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "real"; folder.mkdir()
            path = folder / "pack.json"; path.write_text(json.dumps(self.pack), encoding="utf-8")
            link = Path(temp) / "link.json"
            parent = Path(temp) / "alias"
            try:
                link.symlink_to(path)
                parent.symlink_to(folder, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable on this host")
            for unsafe in (link, parent / "pack.json"):
                with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                    self.ev.load_pack(unsafe)

    @unittest.skipUnless(os.name == "nt", "Windows directory junction test")
    def test_windows_junction_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "real"; folder.mkdir()
            path = folder / "pack.json"; path.write_text(json.dumps(self.pack), encoding="utf-8")
            junction = Path(temp) / "junction"
            try:
                make_junction(junction, folder)
            except OSError:
                self.skipTest("directory junction creation unavailable on this host")
            with self.assertRaises(ValueError):
                self.ev.load_pack(junction / "pack.json")

    def test_portable_path_syntax_rejects_stream_components_and_keeps_drive_anchors(self):
        self.assertTrue(hasattr(self.ev, "_validate_path_syntax"), "portable path validation is missing")
        for raw in ("file:observations", "folder:stream/file.json", "C:/folder/file:stream",
                    "C:\\folder\\file::$DATA", "../folder:stream/../safe.json",
                    "C:/folder/name. ", "C:/folder/NUL.json", "C:/folder/name?.json",
                    "\\\\?\\C:\\file.json", "\\\\.\\C:\\file.json", "//server/share/file.json"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.ev._validate_path_syntax(raw)
        for raw in ("C:/folder/file.json", "D:\\folder\\file.json", "relative/observations.json",
                    "../folder/observations.json", "/tmp/file.json", "C:relative.json"):
            with self.subTest(raw=raw):
                self.ev._validate_path_syntax(raw)

    def test_network_paths_are_rejected_before_any_filesystem_inspection(self):
        paths = ("//review-host/share/file.json", r"\\review-host\share\file.json",
                 r"/\review-host/share\file.json", "///review-host/share/file.json",
                 r"\\?\UNC\review-host\share\file.json", "//?/UNC/review-host/share/file.json",
                 r"\\.\UNC\review-host\share\file.json", r"\??\UNC\review-host\share\file.json",
                 "//?/GLOBALROOT/Device/Mup/review-host/share/file.json")
        for raw in paths:
            for boundary in ("--observations", "--write-observations", "load_pack"):
                with self.subTest(raw=raw, boundary=boundary), \
                        mock.patch.object(Path, "is_dir", side_effect=ValueError("filesystem sentinel")) as is_dir, \
                        mock.patch.object(Path, "lstat", side_effect=ValueError("filesystem sentinel")) as lstat, \
                        mock.patch.object(Path, "open", side_effect=ValueError("filesystem sentinel")) as path_open, \
                        mock.patch.object(os, "open", side_effect=ValueError("filesystem sentinel")) as os_open, \
                        mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                        mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    if boundary == "load_pack":
                        with self.assertRaises(ValueError):
                            self.ev.load_pack(raw)
                    else:
                        self.assertEqual(self.ev.main([boundary, raw]), 2)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertLess(len(stderr.getvalue().encode("utf-8")), 256)
                        self.assertNotIn("review-host", stderr.getvalue())
                    for operation in (is_dir, lstat, path_open, os_open):
                        operation.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows file ADS test")
    def test_windows_file_ads_output_is_rejected_without_creating_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "existing.txt"
            base.write_bytes(b"keep")
            stream = str(base) + ":observations"
            run = subprocess.run([sys.executable, str(SCRIPT), "--write-observations", stream],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertLess(len(run.stderr), 500)
            self.assertEqual(base.read_bytes(), b"keep")
            self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), ["existing.txt"])
            with self.assertRaises(FileNotFoundError):
                with open(stream, "rb"):
                    self.fail("export created a hidden file stream")

    @unittest.skipUnless(os.name == "nt", "Windows directory ADS test")
    def test_windows_directory_ads_output_is_rejected_without_creating_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "existing-directory"
            base.mkdir()
            marker = base / "marker.txt"; marker.write_bytes(b"keep")
            stream = str(base) + ":observations"
            run = subprocess.run([sys.executable, str(SCRIPT), "--write-observations", stream],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertLess(len(run.stderr), 500)
            self.assertEqual(marker.read_bytes(), b"keep")
            self.assertEqual(sorted(p.name for p in base.iterdir()), ["marker.txt"])
            with self.assertRaises(FileNotFoundError):
                with open(stream, "rb"):
                    self.fail("export created a hidden directory stream")

    @unittest.skipUnless(os.name == "nt", "Windows ADS input test")
    def test_windows_ads_input_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "existing.txt"; base.write_bytes(b"keep")
            stream = str(base) + ":observations"
            raw = json.dumps(self.ev.evaluate(self.pack)["observations"]).encode("utf-8")
            with open(stream, "wb") as output:
                output.write(raw)
            run = subprocess.run([sys.executable, str(SCRIPT), "--observations", stream],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, "")
            self.assertLess(len(run.stderr), 500)
            self.assertEqual(base.read_bytes(), b"keep")
            with open(stream, "rb") as source:
                self.assertEqual(source.read(), raw)
            pack_stream = str(base) + ":pack"
            with open(pack_stream, "wb") as output:
                output.write(json.dumps(self.pack).encode("utf-8"))
            with self.assertRaises(ValueError):
                self.ev.load_pack(pack_stream)

    def test_cli_roundtrip_and_safe_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            observations = Path(temp) / "observations.json"
            first = subprocess.run([sys.executable, str(SCRIPT), "--write-observations", str(observations)],
                                   capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["summary"]["correct"], 5)
            second = subprocess.run([sys.executable, str(SCRIPT), "--observations", str(observations)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["summary"]["correct"], 5)
            observations.write_text('{"private": NaN}', encoding="utf-8")
            failed = subprocess.run([sys.executable, str(SCRIPT), "--observations", str(observations)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(failed.returncode, 2)
            self.assertNotIn("private", failed.stderr)
            self.assertLess(len(failed.stderr), 500)

    def test_argument_errors_do_not_echo_unbounded_untrusted_values(self):
        private = "private-argument-" + "x" * 4096
        for arguments in (["--control", private], ["--unexpected", private],
                          ["--coverage-low-threshold", private]):
            run = subprocess.run([sys.executable, str(SCRIPT), *arguments],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertNotIn("private-argument", run.stderr)
            self.assertLess(len(run.stderr), 500)


if __name__ == "__main__":
    unittest.main()
