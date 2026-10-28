"""Offline state revision evaluation contracts; no live vault or model input."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_state_revisions.py"


class StateRevisionEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location("state_revision_evaluation", SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def evaluator(self):
        self.assertIsNotNone(self.module, "Task A standalone evaluator is not implemented")
        return self.module

    def test_quantity_update_preserves_unit_price_and_recomputes_total(self):
        report = self.evaluator().evaluate()
        rows = {row["id"]: row for row in report["per_snapshot"]}
        self.assertEqual(rows["derived/pre"]["response"]["values"],
                         {"quantity": "2", "unit_price": "12.5", "total": "25"})
        self.assertEqual(rows["derived/post"]["response"]["values"],
                         {"quantity": "5", "unit_price": "12.5", "total": "62.5"})
        self.assertTrue(all(row["answer"]["classification"] == "current" for row in rows.values()))

    def test_old_derived_total_is_separate_dependency_error(self):
        rows = {row["id"]: row for row in self.evaluator().evaluate(control="mixed")["per_snapshot"]}
        row = rows["derived/post"]
        self.assertEqual(row["response"]["values"]["quantity"], "5")
        self.assertEqual(row["response"]["values"]["total"], "25")
        self.assertEqual(row["answer"]["classification"], "mixed_dependency_stale")
        self.assertEqual(row["answer"]["dependency_error_count"], 1)

    def test_changed_and_malformed_observations_never_pass(self):
        module = self.evaluator()
        observations = {row["id"]: row["observation"] for row in module.evaluate()["per_snapshot"]}
        observations["derived/post"]["binding"]["evidence_sha256"] = "0" * 64
        observations["direct/pre"]["response"]["values"]["quantity"] = True
        report = module.evaluate(observations=observations)
        self.assertEqual(report["summary"]["execution_error_count"], 2)
        self.assertIsNone(report["per_snapshot"][0]["answer"]["correct"])

    def observations(self, **kwargs):
        return {row["id"]: row["observation"] for row in self.evaluator().evaluate(**kwargs)["per_snapshot"]}

    def test_stale_whole_snapshot_is_superseded_not_dependency_error(self):
        rows = {row["id"]: row for row in self.evaluator().evaluate(control="stale")["per_snapshot"]}
        self.assertEqual(rows["derived/post"]["answer"]["classification"], "superseded")
        self.assertEqual(rows["derived/post"]["answer"]["dependency_error_count"], 0)
        self.assertEqual(rows["derived/pre"]["answer"]["classification"], "current")

    def test_chained_revisions_and_dependency_rules(self):
        rows = {row["id"]: row for row in self.evaluator().evaluate()["per_snapshot"]}
        self.assertEqual(rows["chained/mid"]["response"]["values"]["grand_total"], "13")
        self.assertEqual(rows["chained/post"]["response"]["values"]["grand_total"], "21")

    def test_copying_final_answer_fails_earlier_snapshot(self):
        module = self.evaluator()
        final = {row["case_id"]: row["response"] for row in module.evaluate()["per_snapshot"]}
        report = module.evaluate(answerer=lambda payload: final[payload["case_id"]])
        pre = next(row for row in report["per_snapshot"] if row["id"] == "derived/pre")
        self.assertEqual(pre["answer"]["classification"], "other")
        self.assertFalse(pre["answer"]["correct"])

    def test_callback_gets_copied_public_payload_and_no_future_revision(self):
        module = self.evaluator()
        pack = module.load_pack()
        original = copy.deepcopy(pack)
        calls = []
        def answer(payload):
            calls.append(copy.deepcopy(payload))
            self.assertNotIn("labels", payload)
            self.assertNotIn("current", payload)
            self.assertNotIn("superseded", payload)
            result = module.synthetic_answer(payload)
            payload["evidence"]["initial"].clear()
            payload["question"] = "mutated"
            return result
        report = module.evaluate(answerer=answer)
        self.assertEqual(report["summary"]["current_count"], len(calls))
        self.assertEqual(module.load_pack(), original)
        self.assertEqual(calls[0]["evidence"]["revisions"], [])
        self.assertEqual(len(calls), 9)

    def test_exact_replay_and_cost_provenance(self):
        module = self.evaluator()
        report = module.evaluate(observations=self.observations())
        self.assertEqual(report["mode"], "observation_replay")
        self.assertEqual(report["summary"]["current_count"], 9)
        self.assertEqual(report["cost"]["model_request_count"], 0)
        self.assertIsNone(report["cost"]["actual_model_cost"])
        self.assertIn("caller-attested", report["cost"]["usage_provenance"])

    def test_missing_extra_and_wrong_container_observations_rejected(self):
        module = self.evaluator()
        for mutation in ("missing", "extra", "list"):
            observations = self.observations()
            if mutation == "missing":
                observations.pop("derived/pre")
            elif mutation == "extra":
                observations["unknown/pre"] = observations["derived/pre"]
            else:
                observations = list(observations.values())
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.evaluate(observations=observations)

    def test_question_fixture_and_config_binding_changes_fail(self):
        module = self.evaluator()
        observations = self.observations()
        report = module.evaluate(control="stale", observations=observations)
        self.assertEqual(report["summary"]["execution_error_count"], 9)
        pack = module.load_pack()
        pack["cases"][0]["initial"]["quantity"] = "4"
        pack["labels"]["direct"]["pre"]["current"]["quantity"] = "4"
        pack["labels"]["direct"]["post"]["superseded"][0]["quantity"] = "4"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "pack.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            report = module.evaluate(path, observations=observations)
        self.assertEqual(report["summary"]["execution_error_count"], 9)
        pack = module.load_pack()
        pack["cases"][0]["question"] += " Revised wording."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "pack.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            report = module.evaluate(path, observations=observations)
        self.assertEqual(report["summary"]["execution_error_count"], 9)

    def test_binding_fields_cannot_be_omitted_added_or_swapped(self):
        module = self.evaluator()
        for mutation in ("missing", "extra", "swap"):
            observations = self.observations()
            binding = observations["derived/pre"]["binding"]
            if mutation == "missing":
                binding.pop("question_sha256")
            elif mutation == "extra":
                binding["extra"] = "unbound"
            else:
                observations["derived/pre"] = observations["derived/post"]
            report = module.evaluate(observations=observations)
            self.assertEqual(report["summary"]["execution_error_count"], 1)

    def test_abstention_and_callback_exception_remain_unassessed(self):
        module = self.evaluator()
        report = module.evaluate(answerer=lambda payload: {"status": "abstain", "values": {}})
        self.assertEqual(report["summary"]["unassessed_count"], 9)
        self.assertEqual(report["summary"]["current_count"], 0)
        def broken(payload):
            raise RuntimeError("private callback contents must not be echoed")
        report = module.evaluate(answerer=broken)
        self.assertEqual(report["summary"]["execution_error_count"], 9)
        self.assertNotIn("private callback", json.dumps(report))

    def test_unknown_response_values_are_other(self):
        report = self.evaluator().evaluate(control="wrong")
        self.assertEqual(report["summary"]["other_count"], 9)

    def test_duplicate_nonfinite_and_boolean_json_rejected(self):
        module = self.evaluator()
        pack = module.load_pack()
        raw = json.dumps(pack)
        variants = [raw.replace('"synthetic": true', '"synthetic": true, "synthetic": true', 1),
                    raw.replace('"synthetic": true', '"synthetic": NaN', 1),
                    raw.replace('"synthetic": true', '"synthetic": Infinity', 1)]
        bad = copy.deepcopy(pack)
        bad["cases"][0]["initial"]["quantity"] = True
        variants.append(json.dumps(bad))
        bad = copy.deepcopy(pack)
        bad["schema_version"] = True
        variants.append(json.dumps(bad))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "pack.json"
            for raw in variants:
                path.write_text(raw, encoding="utf-8")
                with self.subTest(raw=raw[:80]), self.assertRaises(ValueError):
                    module.load_pack(path)

    def test_schema_rejects_overflow_cycles_invalid_rules_and_labels(self):
        module = self.evaluator()
        for mutation in ("huge", "expression", "cycle", "op", "label", "duplicate", "index"):
            pack = module.load_pack()
            case = pack["cases"][-1]
            if mutation == "huge":
                case["initial"]["quantity"] = "1e99999999"
            elif mutation == "expression":
                case["initial"]["quantity"] = "__import__('os')"
            elif mutation == "cycle":
                case["rules"][0]["inputs"][0] = "total"
            elif mutation == "op":
                case["rules"][0]["op"] = "execute"
            elif mutation == "label":
                pack["labels"]["derived"]["post"]["current"]["total"] = "99"
            elif mutation == "duplicate":
                pack["cases"].append(copy.deepcopy(case))
            else:
                case["snapshots"][0]["after_revision"] = True
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory).resolve() / "pack.json"
                path.write_text(json.dumps(pack), encoding="utf-8")
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    module.load_pack(path)

    def test_unsafe_file_and_parent_links_rejected(self):
        module = self.evaluator()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source = base / "pack.json"
            source.write_text(json.dumps(module.load_pack()), encoding="utf-8")
            link = base / "hard.json"
            os.link(source, link)
            with self.assertRaises(ValueError):
                module.load_pack(link)
            link.unlink()
            try:
                os.symlink(source, base / "soft.json")
            except OSError:
                pass  # Windows may not grant symlink privilege; hardlink check still ran.
            else:
                with self.assertRaises(ValueError):
                    module.load_pack(base / "soft.json")
            real = base / "real"
            real.mkdir()
            (real / "pack.json").write_text(json.dumps(module.load_pack()), encoding="utf-8")
            try:
                os.symlink(real, base / "linked_parent", target_is_directory=True)
            except OSError:
                pass
            else:
                with self.assertRaises(ValueError):
                    module.load_pack(base / "linked_parent" / "pack.json")
            with self.assertRaises(ValueError):
                module.load_pack(base)

    def test_json_byte_and_depth_limits(self):
        module = self.evaluator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "pack.json"
            for raw in (" " * (module.MAX_JSON_BYTES + 1), "[" * 200 + "]" * 200):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    module.load_pack(path)

    def test_stable_windows_path_and_handle_ctimes_can_differ(self):
        module = self.evaluator()
        real_fstat = os.fstat
        def handle_stat(descriptor):
            info = real_fstat(descriptor)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
            result = {key: getattr(info, key) for key in fields}
            result["st_ctime_ns"] += 1  # Path and handle APIs can expose different Windows times.
            return SimpleNamespace(**result)
        with mock.patch.object(module.os, "fstat", side_effect=handle_stat):
            self.assertEqual(module.load_pack()["schema_version"], 1)

    def test_file_changed_during_read_is_rejected(self):
        module = self.evaluator()
        real_fstat, calls = os.fstat, []
        def handle_stat(descriptor):
            info = real_fstat(descriptor)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
            result = {key: getattr(info, key) for key in fields}
            calls.append(descriptor)
            if len(calls) == 2:
                result["st_mtime_ns"] += 1
            return SimpleNamespace(**result)
        with mock.patch.object(module.os, "fstat", side_effect=handle_stat), self.assertRaises(ValueError):
            module.load_pack()

    def test_exact_decimal_subtraction_and_arithmetic_overflow(self):
        module = self.evaluator()
        pack = module.load_pack()
        pack["cases"] = [{"id": "decimal", "question": "Give all declared values.",
                          "initial": {"a": "0.3", "b": "0.1"},
                          "revisions": [{"set": {"a": "0.4"}}],
                          "rules": [{"target": "difference", "op": "subtract", "inputs": ["a", "b"]}],
                          "requested": ["a", "b", "difference"],
                          "snapshots": [{"id": "pre", "after_revision": 0}, {"id": "post", "after_revision": 1}]}]
        pre, post = {"a": "0.3", "b": "0.1", "difference": "0.2"}, {"a": "0.4", "b": "0.1", "difference": "0.3"}
        pack["labels"] = {"decimal": {"pre": {"current": pre, "superseded": []},
                                      "post": {"current": post, "superseded": [pre]}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "pack.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            self.assertEqual(module.evaluate(path)["summary"]["current_count"], 2)
            pack["cases"][0]["initial"] = {"a": "1000000000000", "b": "-1"}
            path.write_text(json.dumps(pack), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.load_pack(path)

    def test_input_magnitude_check_ignores_global_decimal_precision(self):
        from decimal import localcontext
        module = self.evaluator()
        pack = module.load_pack()
        pack["cases"][0]["initial"]["quantity"] = "1000000000000.1"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "pack.json"
            path.write_text(json.dumps(pack), encoding="utf-8")
            with localcontext() as context:
                context.prec = 4
                with self.assertRaisesRegex(ValueError, "magnitude"):
                    module.load_pack(path)

    def test_cli_export_does_not_overwrite_existing_file(self):
        module = self.evaluator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "existing.json"
            path.write_text("keep", encoding="utf-8")
            with self.assertRaises(OSError):
                module._write_observations(path, module.evaluate())
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")

    def test_portable_path_validation_rejects_windows_streams_and_unsafe_names(self):
        module = self.evaluator()
        paths = ("existing.txt:observations", "folder:stream/observations.json",
                 "nested/file.json:input:$DATA", "C:/folder/file.json:stream",
                 "C:relative.json", "NUL", "nested/CON.json", "nested/COM1.json",
                 "nested/LPT9", "nested/aux .json", "nested/trailing. ",
                 "nested/trailing.", "nested/question?.json", "nested/control\x01.json")
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                module._safe_path(path)

    def test_invalid_stream_path_is_rejected_before_filesystem_checks_or_open(self):
        module = self.evaluator()
        report = module.evaluate()
        with mock.patch.object(Path, "exists") as exists, mock.patch.object(module.os, "open") as opened:
            for action in (module._safe_path, module._read_json,
                           lambda path: module._write_observations(path, report)):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    action("folder:stream/observations.json")
            exists.assert_not_called()
            opened.assert_not_called()

    def test_normal_local_and_absolute_windows_drive_paths_remain_accepted(self):
        module = self.evaluator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "safe.json"
            self.assertEqual(module._safe_path(path), path)
        with mock.patch.object(Path, "exists", return_value=False), mock.patch.object(Path, "is_symlink", return_value=False):
            module._safe_path("C:/safe-folder/observations.json")
            module._safe_path("D:\\safe-folder\\observations.json")

    @unittest.skipUnless(os.name == "nt", "NTFS stream regression requires Windows")
    def test_windows_ads_output_never_creates_file_or_directory_stream(self):
        module = self.evaluator()
        report = module.evaluate()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve() / "existing.txt"
            base.write_text("keep", encoding="utf-8")
            folder = Path(directory).resolve() / "existing-folder"
            folder.mkdir()
            for target in (base, folder):
                stream = str(target) + ":observations"
                with self.subTest(target=target.name), self.assertRaises(ValueError):
                    module._write_observations(stream, report)
                self.assertFalse(os.path.exists(stream), "rejected export created an alternate data stream")
                self.assertEqual(base.read_text(encoding="utf-8"), "keep")
                self.assertEqual(list(folder.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "NTFS stream regression requires Windows")
    def test_windows_existing_ads_input_rejected_before_open(self):
        module = self.evaluator()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve() / "existing.txt"
            base.write_text("keep", encoding="utf-8")
            folder = Path(directory).resolve() / "existing-folder"
            folder.mkdir()
            for target in (base, folder):
                stream = str(target) + ":input"
                # The fixture deliberately pre-creates a stream outside the evaluator.
                with open(stream, "w", encoding="utf-8") as output:
                    output.write('{"synthetic_input":true}')
                with mock.patch.object(module.os, "open") as opened:
                    with self.subTest(target=target.name), self.assertRaises(ValueError):
                        module._read_json(stream)
                    opened.assert_not_called()
                with open(stream, encoding="utf-8") as source:
                    self.assertEqual(source.read(), '{"synthetic_input":true}')
                self.assertEqual(base.read_text(encoding="utf-8"), "keep")
                self.assertEqual(list(folder.iterdir()), [])

    def test_invalid_responses_and_usage_never_score_current(self):
        module = self.evaluator()
        for mutation in ("bool", "infinite", "missing", "extra", "usage", "origin", "binding_bool"):
            observations = self.observations()
            observation = observations["derived/post"]
            if mutation == "bool":
                observation["response"]["values"]["quantity"] = True
            elif mutation == "infinite":
                observation["response"]["values"]["total"] = "Infinity"
            elif mutation == "missing":
                observation["response"]["values"].pop("total")
            elif mutation == "extra":
                observation["response"]["values"]["extra"] = "1"
            elif mutation == "usage":
                observation["usage"] = {"input_tokens": 10, "output_tokens": 10}
            elif mutation == "origin":
                observation["origin"] = "invented_model"
            else:
                observation["binding"]["schema_version"] = True
            report = module.evaluate(observations=observations)
            with self.subTest(mutation=mutation):
                self.assertEqual(report["summary"]["execution_error_count"], 1)

    def test_actual_model_observations_are_caller_attested_only(self):
        module = self.evaluator()
        observations = self.observations()
        for item in observations.values():
            item.update(origin="actual_model", model="caller-model",
                        usage={"input_tokens": 2, "output_tokens": 3},
                        actual_cost={"amount": 0.01, "currency": "USD"})
        report = module.evaluate(observations=observations)
        self.assertEqual(report["cost"]["actual_model_usage"], {"input_tokens": 18, "output_tokens": 27})
        self.assertEqual(report["cost"]["model_request_count"], 0)
        self.assertIsNone(report["cost"]["actual_model_cost"])
        self.assertEqual(report["per_snapshot"][0]["observation"]["actual_cost"]["amount"], 0.01)

    def test_real_cli_round_trip_and_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "observations.json"
            first = subprocess.run([sys.executable, str(SCRIPT), "--write-observations", str(output)],
                                   capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(first.returncode, 0, first.stderr)
            replay = subprocess.run([sys.executable, str(SCRIPT), "--observations", str(output)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(replay.stdout)["summary"], json.loads(first.stdout)["summary"])
            output.write_text('{"secret-do-not-echo":NaN}', encoding="utf-8")
            bad = subprocess.run([sys.executable, str(SCRIPT), "--observations", str(output)],
                                 capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(bad.returncode, 2)
            self.assertNotIn("secret-do-not-echo", bad.stderr)
            self.assertNotIn("Traceback", bad.stderr)

    def test_cli_invalid_arguments_are_bounded_and_do_not_echo_input(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--control", "private-argument-" + "x" * 10000],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertLess(len(result.stderr), 256)
        self.assertNotIn("private-argument", result.stderr)


if __name__ == "__main__":
    unittest.main()
