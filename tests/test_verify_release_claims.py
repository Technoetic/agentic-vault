"""Tests for scripts/verify_release_claims.py (provenance-bound release claim verifier)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_release_claims.py"
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_release_claims  # noqa: E402

CHECKER_HEX = "09642e7aa77d0b812f5afa646b491fe2d79d44104e399bea4a3001a6f2f1279d"
ZIP_HEX = "a" * 64


def _make_ci_run(run_id: int, completed_at: str, *, jobs: int = 12, failed: int = 0) -> dict:
    job_list = [
        {"name": f"job-{i}", "conclusion": "failure" if i < failed else "success"}
        for i in range(jobs)
    ]
    return {
        "run_id": run_id,
        "conclusion": "failure" if failed else "success",
        "updated_at": completed_at,
        "jobs": job_list,
    }


def _valid_verification() -> dict:
    return {
        "tag": "v0.54.0",
        "published_at": "2026-11-20T09:00:00Z",
        "zip_sha256": ZIP_HEX,
        "local_checks": {
            "unit_default_temp": {"tests": 4660, "returncode": 0},
            "unit_long_temp_150": {
                "tests": 4660,
                "returncode": 0,
                "temp_length": 150,
                "tempfile_uses_temp": True,
                "long_paths_enabled": 0,
            },
            "hypothesis_long_temp_150": {"tests": 14, "returncode": 0},
            "offline_smoke": {"passed": 23, "total": 23},
        },
        "ci": {
            "candidate": _make_ci_run(1001, "2026-11-20T08:10:00Z"),
            "tag": _make_ci_run(1002, "2026-11-20T08:30:00Z"),
            "master": _make_ci_run(1003, "2026-11-20T08:50:00Z"),
        },
        "archive_check": {
            "l1": {"status": "match", "given_sha256": ZIP_HEX},
            "l2": {"status": "match"},
        },
        "certificate": {
            "checker_sha256": CHECKER_HEX,
            "false_positives": 0,
        },
    }


def _valid_release_note() -> str:
    return (
        "# v0.54.0\n\n"
        f"Release `v0.54.0` keeps `checker_sha256` `{CHECKER_HEX}` unchanged.\n"
        "See [SECURITY.md](https://github.com/Technoetic/agentic-vault/blob/v0.54.0/SECURITY.md).\n"
    )


class VerifyReleaseClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="av-claims-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()

    def _write_bundle(
        self,
        ver: dict | None = None,
        note: str | None = None,
        sums: str | None = None,
    ) -> tuple[Path, Path, Path]:
        vp = self.dir / "verification.json"
        np = self.dir / "v0.54.0.md"
        sp = self.dir / "SHA256SUMS"
        vp.write_text(json.dumps(ver if ver is not None else _valid_verification(), indent=2), encoding="utf-8")
        np.write_text(note if note is not None else _valid_release_note(), encoding="utf-8")
        sp.write_text(
            sums if sums is not None else f"{ZIP_HEX}  agentic-vault-0.54.0.zip\n",
            encoding="utf-8",
        )
        return vp, np, sp

    def test_valid_bundle_passes_and_writes_provenance_numbers_index(self) -> None:
        vp, np, sp = self._write_bundle()
        out_num = self.dir / "numbers_index.json"
        proc = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--verification", str(vp),
                "--release-note", str(np),
                "--checksums", str(sp),
                "--tag", "v0.54.0",
                "--expected-checker-sha256", CHECKER_HEX,
                "--out-numbers", str(out_num),
                "--json",
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["passed_count"], report["total_count"])
        numbers = json.loads(out_num.read_text(encoding="utf-8"))
        self.assertEqual(numbers["inputs"]["verification"]["sha256"], hashlib.sha256(vp.read_bytes()).hexdigest())

    def test_catches_fake_long_temp_and_test_count_mismatch(self) -> None:
        ver = _valid_verification()
        ver["local_checks"]["unit_long_temp_150"]["tempfile_uses_temp"] = False
        vp, np, sp = self._write_bundle(ver=ver)
        idx = verify_release_claims.build_numbers_index(vp, np, sp, tag="v0.54.0", expected_checker_sha256=CHECKER_HEX)
        claims = {c["claim"]: c["passed"] for c in verify_release_claims.evaluate_claims(idx)}
        self.assertFalse(claims["150-char Windows TEMP unit suite ran under LongPathsEnabled=0 with identical test count"])

    def test_catches_master_ci_after_publication(self) -> None:
        ver = _valid_verification()
        ver["ci"]["master"]["updated_at"] = "2026-11-20T09:53:00Z"
        vp, np, sp = self._write_bundle(ver=ver)
        idx = verify_release_claims.build_numbers_index(vp, np, sp, tag="v0.54.0")
        claims = {c["claim"]: c["passed"] for c in verify_release_claims.evaluate_claims(idx)}
        self.assertFalse(claims["if published_at is recorded, master CI completed strictly before publication"])

    def test_catches_failed_hypothesis_and_smoke_deficit(self) -> None:
        ver = _valid_verification()
        ver["local_checks"]["hypothesis_long_temp_150"]["returncode"] = 1
        ver["local_checks"]["offline_smoke"]["passed"] = 22
        vp, np, sp = self._write_bundle(ver=ver)
        idx = verify_release_claims.build_numbers_index(vp, np, sp, tag="v0.54.0")
        claims = {c["claim"]: c["passed"] for c in verify_release_claims.evaluate_claims(idx)}
        self.assertFalse(claims["150-char TEMP Hypothesis stateful suite passed"])
        self.assertFalse(claims["all offline smoke checks passed"])

    def test_catches_local_path_leak_relative_link_and_checksum_mismatch(self) -> None:
        ver = _valid_verification()
        ver["debug_note"] = r"C:\Users\secretperson\work"
        note = _valid_release_note() + "\nBroken [local link](../SECURITY.md)\n"
        sums = ("b" * 64) + "  agentic-vault-0.54.0.zip\n"
        vp, np, sp = self._write_bundle(ver=ver, note=note, sums=sums)
        idx = verify_release_claims.build_numbers_index(vp, np, sp, tag="v0.54.0")
        claims = {c["claim"]: c["passed"] for c in verify_release_claims.evaluate_claims(idx)}
        self.assertFalse(claims["verification.json contains zero local path or username leaks"])
        self.assertFalse(claims["release note has zero relative links and names both the tag and checker_sha256"])
        self.assertFalse(claims["ZIP rebuild passed both L1_bytes and L2_members_blobs and matches SHA256SUMS"])

    def test_catches_checker_sha256_drift(self) -> None:
        ver = _valid_verification()
        ver["certificate"]["checker_sha256"] = "f" * 64
        vp, np, sp = self._write_bundle(ver=ver)
        idx = verify_release_claims.build_numbers_index(vp, np, sp, tag="v0.54.0", expected_checker_sha256=CHECKER_HEX)
        claims = {c["claim"]: c["passed"] for c in verify_release_claims.evaluate_claims(idx)}
        self.assertFalse(claims["warning-promotion certificate has zero false positives and matches expected checker_sha256"])

    def test_native_release_verification_v1_schema_passes_all_claims(self) -> None:
        native = {
            "schema": "agentic-vault/release-verification/v1",
            "version": "0.54.0",
            "tag": "v0.54.0",
            "archive_check": {
                "sha256": ZIP_HEX,
                "bytes": 4600000,
                "l1": {"status": "match", "given_sha256": ZIP_HEX},
                "l2": {"status": "match"},
            },
            "ci": [
                {"label": "candidate", **_make_ci_run(2001, "2026-11-20T08:10:00Z")},
                {"label": "tag", **_make_ci_run(2002, "2026-11-20T08:30:00Z")},
                {"label": "master", **_make_ci_run(2003, "2026-11-20T08:50:00Z")},
            ],
            "local_exact_source_checks": {
                "full_parallel_default_temp": {
                    "returncode": 0,
                    "tail": ["Ran 4660 tests across 176 modules in 600.0s (workers=4)", "OK (skipped=57)"],
                },
                "full_parallel_150char_temp": {
                    "returncode": 0,
                    "temp_length": 150,
                    "tempfile_uses_temp": True,
                    "long_paths_enabled": 0,
                    "tail": ["Ran 4660 tests across 176 modules in 615.0s (workers=4)", "OK (skipped=57)"],
                },
                "hypothesis_stateful_150char_temp": {
                    "returncode": 0,
                    "tests_summary": "Ran 14 tests — OK",
                },
                "offline_smoke_23": {"total": 23, "passed": 23, "failed": []},
            },
            "certificate_replay": {
                "checker_sha256_unchanged_from_v0460": True,
                "source": {"checker_sha256": CHECKER_HEX, "false_positives": 0},
            },
        }
        vp, np, sp = self._write_bundle(ver=native)
        idx = verify_release_claims.build_numbers_index(vp, np, sp, tag="v0.54.0", expected_checker_sha256=CHECKER_HEX)
        claims = verify_release_claims.evaluate_claims(idx)
        self.assertTrue(all(c["passed"] for c in claims), claims)


if __name__ == "__main__":
    unittest.main()
