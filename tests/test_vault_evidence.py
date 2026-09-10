from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
SCRIPT = SCRIPTS / "vault_evidence.py"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Use the resolver's canonical root on macOS and Windows too.
        self.vault = Path(self.temp.name).resolve()
        self.write("00-meta/vault-config.json", b'{"required_keys": ["title"], "enums": {}}')
        self.original = "# 결과\r\n검증 전 파일 🌱\r\n".encode("utf-8")
        self.artifact = self.write("20-knowledge/result.md", self.original)
        self.evidence = self.write("00-meta/check-output.log", b"2 checks passed\r\n")

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def cli(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--vault", str(self.vault), *args],
            capture_output=True, encoding="utf-8", cwd=self.vault, timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        output = json.loads(result.stdout)
        self.assertIsInstance(output, dict)
        return output

    def snapshot(self, *artifacts, success=True):
        args = ["snapshot", "--task", "T-검증-001", "--objective", "검증 결과를 다음 세션에 전달"]
        for artifact in artifacts or ("20-knowledge/result.md",):
            args.extend(["--artifact", artifact])
        return self.cli(*args, success=success)

    def report(self):
        return {
            "verifier": "Codex 검토자",
            "checks": [{
                "id": "C-001", "claim": "결과 문서의 검증이 통과했다", "status": "verified",
                "method": "Read the recorded test output", "observed": "2 checks passed",
                "evidence": ["00-meta/check-output.log"],
                "preserve": "Keep the reviewed line endings and Korean text",
                "next_check": "",
            }],
            "next_action": "Review the remaining integration task",
        }

    def write_report(self, report=None, path="00-meta/report.json"):
        self.write(path, json.dumps(self.report() if report is None else report,
                                    ensure_ascii=False).encode("utf-8"))
        return path

    def finalize(self, evidence_id=None, report=None):
        evidence_id = evidence_id or self.snapshot()["id"]
        self.cli("record", evidence_id, "--report", self.write_report(report))
        return evidence_id

    def receipt_path(self, evidence_id):
        return self.vault / "00-meta/evidence" / (evidence_id + ".json")

    def write_rechecksummed_receipt(self, evidence_id, receipt):
        # Deliberately recalculate without production helpers: a checksum is not
        # authority, so schema and path validation must still reject this input.
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        canonical = json.dumps(body, ensure_ascii=True, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        receipt["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
        data = json.dumps(receipt, ensure_ascii=True).encode("utf-8")
        self.receipt_path(evidence_id).write_bytes(data)
        return data

    def module(self):
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        return importlib.import_module("vault_evidence")

    def call_main(self, module, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = module.main(["--vault", str(self.vault), *args])
        return result, json.loads(output.getvalue())

    def inventory(self):
        return {str(path.relative_to(self.vault)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.vault.rglob("*") if path.is_file()}

    def assert_pending(self, evidence_id):
        result = self.cli("check", evidence_id, success=False)
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["issues"])

    def assert_stale(self, evidence_id):
        result = self.cli("check", evidence_id, success=False)
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["valid"])
        self.assertTrue(result["issues"])
        handoff = self.cli("handoff", evidence_id, success=False)
        self.assertEqual(handoff["status"], "stale")
        self.assertFalse(handoff["valid"])
        self.assertEqual(handoff["preserve"], [])
        self.assertTrue(handoff["gaps"])
        self.assertTrue(handoff["next_action"])
        self.assertNotEqual(handoff["next_action"], self.report()["next_action"])

    def test_snapshot_record_check_handoff_preserves_byte_exact_bindings(self):
        snapshot = self.snapshot()
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["status"], "pending")
        self.assertRegex(snapshot["id"], r"^[0-9a-f]{32}$")
        self.assertTrue(self.receipt_path(snapshot["id"]).is_file())
        saved = json.loads(self.receipt_path(snapshot["id"]).read_bytes())
        self.assertIn(hashlib.sha256(self.original).hexdigest(), json.dumps(saved))
        self.finalize(snapshot["id"])
        checked = self.cli("check", snapshot["id"])
        self.assertEqual(checked["id"], snapshot["id"])
        self.assertEqual(checked["status"], "current")
        self.assertTrue(checked["valid"])
        self.assertEqual(checked["task"], "T-검증-001")
        self.assertEqual(checked["objective"], "검증 결과를 다음 세션에 전달")
        self.assertEqual(checked["issues"], [])
        self.assertIn("20-knowledge/result.md", json.dumps(checked["artifacts"]))
        handoff = self.cli("handoff", snapshot["id"])
        self.assertEqual(handoff["status"], "current")
        self.assertTrue(handoff["valid"])
        self.assertIn(self.report()["checks"][0]["preserve"], json.dumps(handoff["preserve"]))
        self.assertEqual(handoff["gaps"], [])
        self.assertEqual(handoff["next_action"], self.report()["next_action"])
        self.assertEqual(self.artifact.read_bytes(), self.original)
        self.assertEqual(self.evidence.read_bytes(), b"2 checks passed\r\n")

    def test_binary_artifacts_and_multiple_files_are_supported(self):
        binary = b"\x00\xff\xfe\r\n\x80"
        self.write("output/image.bin", binary)
        evidence_id = self.snapshot("20-knowledge/result.md", "output/image.bin")["id"]
        self.finalize(evidence_id)
        checked = self.cli("check", evidence_id)
        self.assertEqual(len(checked["artifacts"]), 2)
        receipt = self.receipt_path(evidence_id).read_text(encoding="utf-8")
        self.assertIn(hashlib.sha256(binary).hexdigest(), receipt)
        self.assertEqual((self.vault / "output/image.bin").read_bytes(), binary)

    def test_pending_handoff_has_no_preserve_advice(self):
        evidence_id = self.snapshot()["id"]
        self.assert_pending(evidence_id)
        handoff = self.cli("handoff", evidence_id, success=False)
        self.assertEqual(handoff["status"], "pending")
        self.assertEqual(handoff["preserve"], [])
        self.assertTrue(handoff["gaps"])
        self.assertTrue(handoff["next_action"])

    def test_changed_candidate_rejects_record_without_finalizing(self):
        evidence_id = self.snapshot()["id"]
        before = self.receipt_path(evidence_id).read_bytes()
        self.artifact.write_bytes(b"A concurrent edit")
        self.cli("record", evidence_id, "--report", self.write_report(), success=False)
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
        self.assertEqual(self.artifact.read_bytes(), b"A concurrent edit")

    def test_changed_artifact_makes_record_and_handoff_stale(self):
        evidence_id = self.finalize()
        self.artifact.write_bytes(self.original.replace(b"\r\n", b"\n"))
        self.assert_stale(evidence_id)

    def test_same_size_artifact_edit_with_restored_mtime_is_stale(self):
        evidence_id = self.finalize()
        metadata = self.artifact.stat()
        replacement = bytearray(self.original)
        replacement[0] = ord("!")
        self.artifact.write_bytes(replacement)
        os.utime(self.artifact, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assert_stale(evidence_id)

    def test_missing_artifact_makes_record_and_handoff_stale(self):
        evidence_id = self.finalize()
        self.artifact.unlink()
        self.assert_stale(evidence_id)

    def test_changed_evidence_makes_record_and_handoff_stale(self):
        evidence_id = self.finalize()
        self.evidence.write_bytes(b"The original evidence was replaced")
        self.assert_stale(evidence_id)

    def test_missing_evidence_makes_record_and_handoff_stale(self):
        evidence_id = self.finalize()
        self.evidence.unlink()
        self.assert_stale(evidence_id)

    def test_changed_report_source_makes_record_and_handoff_stale(self):
        evidence_id = self.finalize()
        path = self.vault / "00-meta/report.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assert_stale(evidence_id)

    def test_missing_report_source_makes_record_and_handoff_stale(self):
        evidence_id = self.finalize()
        (self.vault / "00-meta/report.json").unlink()
        self.assert_stale(evidence_id)

    def test_current_mixed_report_keeps_verified_conditions_and_exposes_gaps(self):
        report = self.report()
        for index, status in enumerate(("gap", "failed", "regression"), start=2):
            report["checks"].append({
                "id": f"C-00{index}", "claim": f"Claim with {status}", "status": status,
                "method": "Manual inspection", "observed": "Further work remains",
                "evidence": [], "preserve": f"Do not preserve this {status} assertion",
                "next_check": f"Recheck the {status} claim",
            })
        evidence_id = self.finalize(report=report)
        self.assertEqual(self.cli("check", evidence_id)["status"], "current")
        handoff = self.cli("handoff", evidence_id)
        preserve = json.dumps(handoff["preserve"])
        self.assertIn(report["checks"][0]["preserve"], preserve)
        self.assertNotIn("Do not preserve", preserve)
        self.assertEqual(len(handoff["gaps"]), 3)
        for actual, expected in zip(handoff["gaps"], report["checks"][1:]):
            for field in ("id", "claim", "status", "next_check"):
                self.assertEqual(actual[field], expected[field])

    def test_finalized_report_is_immutable(self):
        evidence_id = self.finalize()
        before = self.receipt_path(evidence_id).read_bytes()
        second = self.report()
        second["next_action"] = "A different later decision"
        self.cli("record", evidence_id, "--report", self.write_report(second, "00-meta/second.json"),
                 success=False)
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
        handoff = self.cli("handoff", evidence_id)
        self.assertEqual(handoff["next_action"], self.report()["next_action"])

    def test_current_pending_and_stale_views_are_read_only(self):
        evidence_id = self.snapshot()["id"]
        before = self.inventory()
        self.cli("check", evidence_id, success=False)
        self.cli("handoff", evidence_id, success=False)
        self.assertEqual(self.inventory(), before)
        self.finalize(evidence_id)
        before = self.inventory()
        self.cli("check", evidence_id)
        self.cli("handoff", evidence_id)
        self.assertEqual(self.inventory(), before)
        self.evidence.write_bytes(b"changed")
        before = self.inventory()
        self.assert_stale(evidence_id)
        self.assertEqual(self.inventory(), before)

    def test_missing_record_views_do_not_create_store_or_lock(self):
        before = self.inventory()
        for operation in ("check", "handoff"):
            self.cli(operation, "0" * 32, success=False)
        self.assertFalse((self.vault / "00-meta/evidence").exists())
        self.assertEqual(self.inventory(), before)

    def test_report_schema_rejections_leave_pending_receipt_unchanged(self):
        cases = [
            ("top-level list", lambda report: []),
            ("unknown report field", lambda report: dict(report, approval=True)),
            ("empty verifier", lambda report: dict(report, verifier=" ")),
            ("empty next action", lambda report: dict(report, next_action="")),
            ("no checks", lambda report: dict(report, checks=[])),
            ("checks object", lambda report: dict(report, checks={})),
            ("duplicate check ids", lambda report: dict(report, checks=report["checks"] * 2)),
        ]
        for label, mutate in cases:
            with self.subTest(case=label):
                evidence_id = self.snapshot()["id"]
                before = self.receipt_path(evidence_id).read_bytes()
                self.cli("record", evidence_id, "--report", self.write_report(mutate(self.report())),
                         success=False)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
                self.assert_pending(evidence_id)

    def test_check_schema_rejections_leave_pending_receipt_unchanged(self):
        cases = [
            ("unknown field", "approval", True), ("missing id", "id", None),
            ("empty id", "id", ""), ("empty claim", "claim", " "),
            ("unknown status", "status", "approved"), ("empty method", "method", ""),
            ("empty observed", "observed", " "), ("evidence string", "evidence", "output.log"),
            ("evidence null", "evidence", None), ("verified without refs", "evidence", []),
            ("verified without preserve", "preserve", ""),
            ("next check nonstring", "next_check", []),
        ]
        for label, field, value in cases:
            with self.subTest(case=label):
                evidence_id = self.snapshot()["id"]
                before = self.receipt_path(evidence_id).read_bytes()
                report = self.report()
                report["checks"][0][field] = value
                self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
                self.assert_pending(evidence_id)

    def test_each_required_report_and_check_field_is_required(self):
        for scope, fields in (("report", tuple(self.report())),
                              ("check", tuple(self.report()["checks"][0]))):
            for field in fields:
                with self.subTest(scope=scope, field=field):
                    report = self.report()
                    del (report if scope == "report" else report["checks"][0])[field]
                    evidence_id = self.snapshot()["id"]
                    self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
                    self.assert_pending(evidence_id)

    def test_nonverified_checks_require_next_check(self):
        for status in ("gap", "failed", "regression"):
            with self.subTest(status=status):
                report = self.report()
                report["checks"][0].update(status=status, evidence=[], preserve="", next_check=" ")
                evidence_id = self.snapshot()["id"]
                self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
                self.assert_pending(evidence_id)

    def test_duplicate_keys_nonfinite_and_malformed_reports_are_rejected(self):
        normal = json.dumps(self.report()).encode("utf-8")
        invalid = [
            b'{"verifier":"shadow",' + normal[1:],
            normal.replace(b'"observed": "2 checks passed"', b'"observed": NaN'),
            normal.replace(b'"observed": "2 checks passed"', b'"observed": Infinity'),
            b'{not JSON', b'\xff\xfe', b'[' * 20000 + b'0' + b']' * 20000,
        ]
        for raw in invalid:
            with self.subTest(raw_prefix=raw[:50]):
                evidence_id = self.snapshot()["id"]
                before = self.receipt_path(evidence_id).read_bytes()
                self.write("00-meta/report.json", raw)
                self.cli("record", evidence_id, "--report", "00-meta/report.json", success=False)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)

    def test_artifact_paths_reject_traversal_reserved_and_nonfiles(self):
        paths = ["../outside.md", str(self.artifact), ".git/config", "00-meta/evidence/receipt.json",
                 "20-knowledge/result.md:stream", "NUL", "missing.md", "20-knowledge",
                 "20-knowledge/../20-knowledge/result.md", "20-knowledge/./result.md"]
        for path in paths:
            with self.subTest(path=path):
                self.snapshot(path, success=False)
        self.assertEqual(self.artifact.read_bytes(), self.original)

    def test_equivalent_artifact_path_spellings_are_rejected_as_duplicates(self):
        self.snapshot("20-knowledge/result.md", "20-knowledge/result.md", success=False)
        self.snapshot("20-knowledge/result.md", "20-knowledge\\result.md", success=False)

    def test_configured_deny_zone_is_enforced_for_all_input_roles(self):
        self.write("00-meta/vault-config.json", b'{"deny_zones": ["restricted"]}')
        self.write("restricted/private.bin", b"PRIVATE-CONTENT-SHOULD-NOT-APPEAR")
        self.snapshot("restricted/private.bin", success=False)
        evidence_id = self.snapshot()["id"]
        report = self.report()
        report["checks"][0]["evidence"] = ["restricted/private.bin"]
        result = self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assertNotIn("PRIVATE-CONTENT", json.dumps(result))
        self.write_report(path="restricted/report.json")
        self.cli("record", evidence_id, "--report", "restricted/report.json", success=False)
        self.assert_pending(evidence_id)

    def test_config_change_revalidates_artifact_report_and_evidence_paths(self):
        for denied in ("20-knowledge", "00-meta/report.json", "00-meta/check-output.log"):
            with self.subTest(denied=denied):
                self.write("00-meta/vault-config.json", b'{}')
                evidence_id = self.finalize()
                self.write("00-meta/vault-config.json", json.dumps({"deny_zones": [denied]}).encode())
                before = self.inventory()
                self.cli("check", evidence_id, success=False)
                self.cli("handoff", evidence_id, success=False)
                self.assertEqual(self.inventory(), before)

    def test_unsafe_evidence_and_report_paths_do_not_finalize(self):
        evidence_id = self.snapshot()["id"]
        paths = ["../outside.json", str(self.evidence), ".git/config", "missing.log",
                 "00-meta/evidence/" + evidence_id + ".json", "NUL"]
        before = self.receipt_path(evidence_id).read_bytes()
        for path in paths:
            with self.subTest(path=path):
                report = self.report()
                report["checks"][0]["evidence"] = [path]
                self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
                self.cli("record", evidence_id, "--report", path, success=False)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)

    def test_report_source_cannot_be_artifact_or_evidence(self):
        self.write_report(path="00-meta/artifact.json")
        evidence_id = self.snapshot("00-meta/artifact.json")["id"]
        self.cli("record", evidence_id, "--report", "00-meta/artifact.json", success=False)
        self.assert_pending(evidence_id)
        evidence_id = self.snapshot()["id"]
        report = self.report()
        report["checks"][0]["evidence"] = ["00-meta/report.json"]
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assert_pending(evidence_id)

    def test_report_method_and_observation_commands_remain_inert(self):
        report = self.report()
        command = f'"{sys.executable}" -c "from pathlib import Path; Path(\'EXECUTED\').write_text(\'bad\')"'
        report["checks"][0]["method"] = command
        report["checks"][0]["observed"] = "$(" + command + "); " + command
        report["next_action"] = command
        evidence_id = self.finalize(report=report)
        self.cli("check", evidence_id)
        handoff = self.cli("handoff", evidence_id)
        self.assertEqual(handoff["next_action"], command)
        self.assertFalse((self.vault / "EXECUTED").exists())
        self.assertEqual(self.artifact.read_bytes(), self.original)
        self.assertEqual(self.evidence.read_bytes(), b"2 checks passed\r\n")

    def test_receipt_tampering_is_rejected_without_rewriting_it(self):
        evidence_id = self.finalize()
        original = self.receipt_path(evidence_id).read_bytes()
        for field, value in (("task", "unapproved task"), ("objective", "changed purpose"),
                             ("unexpected_field", True)):
            with self.subTest(field=field):
                receipt = json.loads(original)
                receipt[field] = value
                tampered = json.dumps(receipt).encode("utf-8")
                self.receipt_path(evidence_id).write_bytes(tampered)
                self.cli("check", evidence_id, success=False)
                self.cli("handoff", evidence_id, success=False)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), tampered)

    def test_malformed_duplicate_and_oversized_receipts_are_safe_errors(self):
        evidence_id = self.finalize()
        original = self.receipt_path(evidence_id).read_bytes()
        malformed = [b'{"id": "shadow",' + original.lstrip()[1:], b'{"value": NaN}',
                     b'{broken', b'\xff\xfe', b'[' * 20000 + b'0' + b']' * 20000,
                     b' ' * (1024 * 1024 + 1)]
        for raw in malformed:
            with self.subTest(prefix=raw[:30]):
                self.receipt_path(evidence_id).write_bytes(raw)
                output = self.cli("check", evidence_id, success=False)
                self.assertIsInstance(output["error"], str)
                self.assertLessEqual(len(output["error"]), 80)
                self.cli("handoff", evidence_id, success=False)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), raw)

    def test_rechecksummed_unsafe_receipt_paths_are_invalid_errors(self):
        evidence_id = self.finalize()
        original = self.receipt_path(evidence_id).read_bytes()
        paths = ["../outside.md", str(self.artifact), ".git/config", "NUL",
                 "00-meta/evidence/" + evidence_id + ".json"]
        for role in ("artifacts", "evidence", "report_source"):
            for path in paths:
                with self.subTest(role=role, path=path):
                    receipt = json.loads(original)
                    fingerprint = receipt[role] if role == "report_source" else receipt[role][0]
                    fingerprint["path"] = path
                    if role == "evidence":
                        receipt["report"]["checks"][0]["evidence"] = [path]
                    tampered = self.write_rechecksummed_receipt(evidence_id, receipt)
                    for operation in ("check", "handoff"):
                        result = self.cli(operation, evidence_id, success=False)
                        self.assertIsInstance(result.get("error"), str)
                    self.assertEqual(self.receipt_path(evidence_id).read_bytes(), tampered)

    def test_rechecksummed_malformed_receipts_still_require_valid_schema(self):
        evidence_id = self.finalize()
        original = self.receipt_path(evidence_id).read_bytes()

        def unknown_field(receipt):
            receipt["approval"] = True

        def artifact_boolean_size(receipt):
            receipt["artifacts"][0]["size"] = True

        def artifact_negative_size(receipt):
            receipt["artifacts"][0]["size"] = -1

        def verified_without_references(receipt):
            receipt["report"]["checks"][0]["evidence"] = []
            receipt["evidence"] = []

        def unknown_check_status(receipt):
            receipt["report"]["checks"][0]["status"] = "approved"

        def missing_fingerprints(receipt):
            receipt["evidence"] = []

        def report_extra_field(receipt):
            receipt["report"]["run_this_command"] = "malicious"

        def inconsistent_pending(receipt):
            receipt["report"] = None

        for mutate in (unknown_field, artifact_boolean_size, artifact_negative_size,
                       verified_without_references, unknown_check_status,
                       missing_fingerprints, report_extra_field, inconsistent_pending):
            with self.subTest(case=mutate.__name__):
                receipt = json.loads(original)
                mutate(receipt)
                tampered = self.write_rechecksummed_receipt(evidence_id, receipt)
                for operation in ("check", "handoff"):
                    result = self.cli(operation, evidence_id, success=False)
                    self.assertIsInstance(result.get("error"), str)
                self.assertEqual(self.receipt_path(evidence_id).read_bytes(), tampered)

    def test_rechecksummed_receipt_cannot_bind_oversized_report_source(self):
        evidence_id = self.finalize()
        receipt = json.loads(self.receipt_path(evidence_id).read_bytes())
        report_path = self.vault / "00-meta/report.json"
        oversized = report_path.read_bytes() + b" " * (256 * 1024)
        report_path.write_bytes(oversized)
        receipt["report_source"]["sha256"] = hashlib.sha256(oversized).hexdigest()
        receipt["report_source"]["size"] = len(oversized)
        tampered = self.write_rechecksummed_receipt(evidence_id, receipt)
        for operation in ("check", "handoff"):
            result = self.cli(operation, evidence_id, success=False)
            self.assertIsInstance(result.get("error"), str)
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), tampered)

    def test_invalid_record_ids_never_traverse(self):
        before = self.inventory()
        for evidence_id in ("../vault-config", "../evidence/" + "0" * 32, "", "G" * 32):
            for operation in ("check", "handoff", "record"):
                with self.subTest(evidence_id=evidence_id, operation=operation):
                    extra = ("--report", "00-meta/report.json") if operation == "record" else ()
                    self.cli(operation, evidence_id, *extra, success=False)
        self.assertEqual(self.inventory(), before)

    def test_duplicate_nonfinite_invalid_and_oversized_config_fail_closed(self):
        for raw in (b'{"deny_zones": [], "deny_zones": []}', b'{"extra": NaN}',
                    b'{bad', b'[]', b' ' * (256 * 1024 + 1)):
            with self.subTest(prefix=raw[:30]):
                self.write("00-meta/vault-config.json", raw)
                self.snapshot(success=False)

    def test_overflowing_json_number_is_rejected_as_nonfinite_config(self):
        self.write("00-meta/vault-config.json", b'{"extra": 1e309}')
        self.snapshot(success=False)

    def test_case_sensitive_evidence_reference_requires_its_own_fingerprint(self):
        evidence_id = self.finalize()
        receipt = json.loads(self.receipt_path(evidence_id).read_bytes())
        receipt["report"]["checks"][0]["evidence"] = ["output/A.txt", "output/a.txt"]
        receipt["evidence"] = [{"path": "output/A.txt", "sha256": hashlib.sha256(b"A").hexdigest(),
                                "size": 1}]
        self.write_rechecksummed_receipt(evidence_id, receipt)
        module = self.module()

        class CaseSensitivePaths:
            # Pure paths isolate a case-sensitive resolver on Windows runners.
            # Receipt structure/reference validation performs no file reads.
            root = PurePosixPath("/vault")

            def path(self, relative):
                return self.root / relative

        with self.assertRaises(module.EvidenceError):
            module.validate_receipt(CaseSensitivePaths(), receipt, evidence_id)

    def test_distinct_case_evidence_files_are_both_bound_on_case_sensitive_filesystems(self):
        upper = self.write("output/A.txt", b"Upper-case file")
        lower = self.vault / "output/a.txt"
        if lower.exists():
            self.skipTest("temporary filesystem is case-insensitive")
        lower.write_bytes(b"Lower-case file")
        report = self.report()
        report["checks"][0]["evidence"] = ["output/A.txt", "output/a.txt"]
        for path in (upper, lower):
            with self.subTest(path=path.name):
                evidence_id = self.finalize(report=report)
                original = path.read_bytes()
                path.write_bytes(original + b" changed")
                self.assert_stale(evidence_id)
                path.write_bytes(original)

    def test_artifact_and_check_counts_are_bounded(self):
        artifacts = []
        for index in range(65):
            relative = f"output/{index}.bin"
            self.write(relative, b"x")
            artifacts.append(relative)
        self.snapshot(*artifacts, success=False)
        report = self.report()
        report["checks"] = [dict(report["checks"][0], id=f"C-{index}") for index in range(65)]
        evidence_id = self.snapshot()["id"]
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assert_pending(evidence_id)

    def test_maximum_artifact_and_check_counts_are_accepted(self):
        artifacts = []
        for index in range(64):
            relative = f"output/{index}.bin"
            self.write(relative, bytes([index]))
            artifacts.append(relative)
        evidence_id = self.snapshot(*artifacts)["id"]
        report = self.report()
        report["checks"] = [dict(report["checks"][0], id=f"C-{index}") for index in range(64)]
        self.finalize(evidence_id, report=report)
        self.assertEqual(len(self.cli("check", evidence_id)["artifacts"]), 64)
        self.assertTrue(self.cli("handoff", evidence_id)["preserve"])

    def test_unique_evidence_file_count_is_bounded(self):
        report = self.report()
        paths = []
        for index in range(65):
            relative = f"output/evidence-{index}.log"
            self.write(relative, b"passed")
            paths.append(relative)
        report["checks"][0]["evidence"] = paths
        evidence_id = self.snapshot()["id"]
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assert_pending(evidence_id)

    def test_maximum_unique_evidence_files_are_accepted_across_checks(self):
        report = self.report()
        paths = []
        for index in range(64):
            relative = f"output/evidence-{index}.log"
            self.write(relative, f"Check {index} passed".encode())
            paths.append(relative)
        report["checks"][0]["evidence"] = paths[:32]
        report["checks"].append(dict(report["checks"][0], id="C-002", evidence=paths[32:]))
        evidence_id = self.finalize(report=report)
        self.assertEqual(self.cli("check", evidence_id)["status"], "current")

    def test_file_size_bound_applies_to_artifacts_and_evidence(self):
        path = self.write("output/oversized.bin", b"")
        with path.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        self.snapshot("output/oversized.bin", success=False)
        report = self.report()
        report["checks"][0]["evidence"] = ["output/oversized.bin"]
        evidence_id = self.snapshot()["id"]
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assert_pending(evidence_id)

    def test_aggregate_size_bound_applies_to_artifact_and_evidence_sets(self):
        paths = []
        for index, size in enumerate([64 * 1024 * 1024] * 4 + [1]):
            relative = f"output/large-{index}.bin"
            path = self.write(relative, b"")
            with path.open("wb") as stream:
                stream.truncate(size)
            paths.append(relative)
        self.snapshot(*paths, success=False)
        evidence_id = self.snapshot()["id"]
        report = self.report()
        report["checks"][0]["evidence"] = paths
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assert_pending(evidence_id)

    def test_report_input_size_is_bounded(self):
        evidence_id = self.snapshot()["id"]
        raw = json.dumps(self.report()).encode("utf-8") + b" " * (256 * 1024)
        self.write("00-meta/report.json", raw)
        self.cli("record", evidence_id, "--report", "00-meta/report.json", success=False)
        self.assert_pending(evidence_id)

    def test_shared_evidence_can_support_multiple_checks(self):
        report = self.report()
        report["checks"].append(dict(report["checks"][0], id="C-002", claim="A second claim"))
        evidence_id = self.finalize(report=report)
        self.assertEqual(self.cli("check", evidence_id)["status"], "current")
        self.evidence.write_bytes(b"replaced shared evidence")
        self.assert_stale(evidence_id)

    def test_existing_lock_blocks_writers_and_is_retained_for_manual_recovery(self):
        evidence_id = self.snapshot()["id"]
        lock = self.write("00-meta/evidence/.lock", b"An earlier writer owns this lock")
        original_lock = lock.read_bytes()
        before = self.receipt_path(evidence_id).read_bytes()
        self.snapshot(success=False)
        self.cli("record", evidence_id, "--report", self.write_report(), success=False)
        self.assertEqual(lock.read_bytes(), original_lock)
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
        self.assert_pending(evidence_id)
        self.cli("handoff", evidence_id, success=False)
        self.assertEqual(lock.read_bytes(), original_lock)

    def test_existing_lock_does_not_block_read_only_current_views(self):
        evidence_id = self.finalize()
        lock = self.write("00-meta/evidence/.lock", b"Another writer")
        before = self.inventory()
        self.cli("check", evidence_id)
        self.cli("handoff", evidence_id)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(lock.read_bytes(), b"Another writer")

    def test_failed_atomic_finalization_preserves_pending_receipt_and_inputs(self):
        evidence_id = self.snapshot()["id"]
        report_path = self.write_report()
        before = self.inventory()
        module = self.module()
        real_replace = module.os.replace

        def fail_receipt(source, destination):
            if Path(destination) == self.receipt_path(evidence_id):
                raise OSError("PRIVATE injected filesystem error must not be printed")
            return real_replace(source, destination)

        with mock.patch.object(module.os, "replace", side_effect=fail_receipt):
            result, output = self.call_main(module, "record", evidence_id, "--report", report_path)
        self.assertNotEqual(result, 0)
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertEqual(self.inventory(), before)
        self.assert_pending(evidence_id)
        self.cli("record", evidence_id, "--report", report_path)
        self.assertEqual(self.cli("check", evidence_id)["status"], "current")

    def test_failed_atomic_snapshot_publication_leaves_no_partial_receipt(self):
        module = self.module()
        original = self.inventory()
        with mock.patch.object(module.os, "replace", side_effect=OSError("PRIVATE write error")):
            result, output = self.call_main(
                module, "snapshot", "--task", "T-001", "--objective", "Capture evidence",
                "--artifact", "20-knowledge/result.md",
            )
        self.assertNotEqual(result, 0)
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertEqual(self.inventory(), original)
        store = self.vault / "00-meta/evidence"
        self.assertEqual(list(store.iterdir()) if store.exists() else [], [])
        self.assertEqual(self.snapshot()["status"], "pending")

    def test_interrupted_finalization_keeps_pending_receipt_and_lock(self):
        evidence_id = self.snapshot()["id"]
        report_path = self.write_report()
        before = self.receipt_path(evidence_id).read_bytes()
        module = self.module()
        real_replace = module.os.replace

        def interrupt_receipt(source, destination):
            if Path(destination) == self.receipt_path(evidence_id):
                raise KeyboardInterrupt()
            return real_replace(source, destination)

        with mock.patch.object(module.os, "replace", side_effect=interrupt_receipt):
            with self.assertRaises(KeyboardInterrupt):
                self.call_main(module, "record", evidence_id, "--report", report_path)
        lock = self.vault / "00-meta/evidence/.lock"
        self.assertTrue(lock.is_file())
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
        self.assert_pending(evidence_id)
        self.cli("record", evidence_id, "--report", report_path, success=False)
        self.assertTrue(lock.is_file())
        lock.unlink()  # Explicit operator cleanup after the interrupted writer stopped.
        self.cli("record", evidence_id, "--report", report_path)
        self.assertEqual(self.cli("check", evidence_id)["status"], "current")

    def test_candidate_edit_during_evidence_capture_blocks_finalization(self):
        evidence_id = self.snapshot()["id"]
        report_path = self.write_report()
        before = self.receipt_path(evidence_id).read_bytes()
        module = self.module()
        real_fstat = module.os.fstat
        evidence_identity = (self.evidence.stat().st_dev, self.evidence.stat().st_ino)
        changed = False

        def concurrent_edit(descriptor):
            nonlocal changed
            metadata = real_fstat(descriptor)
            if not changed and (metadata.st_dev, metadata.st_ino) == evidence_identity:
                self.artifact.write_bytes(b"A candidate edit during evidence capture")
                changed = True
            return metadata

        with mock.patch.object(module.os, "fstat", side_effect=concurrent_edit):
            result, output = self.call_main(module, "record", evidence_id, "--report", report_path)
        self.assertNotEqual(result, 0, output)
        self.assertEqual(self.artifact.read_bytes(), b"A candidate edit during evidence capture")
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)

    def test_config_change_during_evidence_capture_blocks_finalization(self):
        evidence_id = self.snapshot()["id"]
        report_path = self.write_report()
        before = self.receipt_path(evidence_id).read_bytes()
        module = self.module()
        real_fstat = module.os.fstat
        evidence_identity = (self.evidence.stat().st_dev, self.evidence.stat().st_ino)
        replacement = b'{"required_keys": ["title"], "enums": {}, "deny_zones": ["unrelated"]}'
        changed = False

        def concurrent_config_edit(descriptor):
            nonlocal changed
            metadata = real_fstat(descriptor)
            if not changed and (metadata.st_dev, metadata.st_ino) == evidence_identity:
                self.write("00-meta/vault-config.json", replacement)
                changed = True
            return metadata

        with mock.patch.object(module.os, "fstat", side_effect=concurrent_config_edit):
            result, output = self.call_main(module, "record", evidence_id, "--report", report_path)
        self.assertNotEqual(result, 0, output)
        self.assertEqual((self.vault / "00-meta/vault-config.json").read_bytes(), replacement)
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)
        self.assert_pending(evidence_id)

    def test_unreadable_evidence_returns_stale_without_preserve_advice(self):
        evidence_id = self.finalize()
        module = self.module()
        real_open = module.os.open
        before = self.receipt_path(evidence_id).read_bytes()

        def deny_evidence(path, *args, **kwargs):
            if Path(path) == self.evidence:
                raise PermissionError("PRIVATE permission failure")
            return real_open(path, *args, **kwargs)

        with mock.patch.object(module.os, "open", side_effect=deny_evidence):
            result, output = self.call_main(module, "handoff", evidence_id)
        self.assertNotEqual(result, 0)
        self.assertEqual(output["status"], "stale")
        self.assertEqual(output["preserve"], [])
        self.assertTrue(output["gaps"])
        self.assertNotIn("PRIVATE", json.dumps(output))
        self.assertEqual(self.receipt_path(evidence_id).read_bytes(), before)

    def test_hardlinked_artifacts_and_evidence_are_rejected(self):
        source = self.write("output/source.bin", b"linked content")
        try:
            os.link(source, self.vault / "output/hardlink.bin")
        except OSError:
            self.skipTest("hardlink creation unavailable")
        self.snapshot("output/hardlink.bin", success=False)
        self.snapshot("output/source.bin", success=False)
        evidence_id = self.snapshot()["id"]
        report = self.report()
        report["checks"][0]["evidence"] = ["output/hardlink.bin"]
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.assert_pending(evidence_id)

    def test_symbolic_links_in_artifacts_evidence_and_store_are_rejected(self):
        link = self.vault / "linked.bin"
        try:
            link.symlink_to(self.artifact)
        except OSError:
            self.skipTest("symlink creation unavailable")
        self.snapshot("linked.bin", success=False)
        evidence_id = self.snapshot()["id"]
        report = self.report()
        report["checks"][0]["evidence"] = ["linked.bin"]
        self.cli("record", evidence_id, "--report", self.write_report(report), success=False)
        self.receipt_path(evidence_id).unlink()
        store = self.vault / "00-meta/evidence"
        store.rmdir()
        external = self.vault / "output"
        external.mkdir()
        store.symlink_to(external, target_is_directory=True)
        self.snapshot(success=False)
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
