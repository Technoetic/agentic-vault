#!/usr/bin/env python3
"""Provenance-bound release numbers index and declarative claim verifier.

Adapted from the build_numbers.py + claims.py pattern in
ai3inc/connectome-cognition-benchmark v1.1.0 (doi:10.5281/zenodo.24361690, MIT),
specialized for agentic-vault release artifacts (distinct from
semantic-crosscheck's paired SQL fixture check):

1. ``build_numbers_index`` reads ``verification.json``, the tagged release note
   (``docs/releases/vX.Y.Z.md``), and ``SHA256SUMS``, hashes every input for
   provenance, and extracts canonical counts, exit codes, CI completion
   timestamps, ZIP rebuild statuses, leak scan counts, and release-note link
   facts. Supports both the native ``agentic-vault/release-verification/v1``
   schema (``local_exact_source_checks``, list-form ``ci``,
   ``certificate_replay``) and compact dict aliases.
2. ``evaluate_claims`` checks an explicit list of ``(claim, bool)`` invariants
   against the numbers index. Any failed invariant fails closed (exit code 1)
   so numerical, timing, or leakage drift cannot slip into a published release.

Python standard library only; zero network calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path


SCHEMA = "agentic-vault/release-claims/v1"
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("windows_user_path", re.compile(r"[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)(?!yourname\b|username\b|user\b|example\b)[A-Za-z0-9._-]+", re.I)),
    ("posix_user_path", re.compile(r"/Users/(?!yourname\b|username\b|user\b|example\b)[A-Za-z0-9._-]+")),
    ("worktree_drive_path", re.compile(r"[A-Za-z]:(?:\\{1,2}|/)[^/\\\s\"']*worktrees(?:\\{1,2}|/)", re.I)),
    ("windows_temp_path", re.compile(r"AppData(?:\\{1,2}|/)Local(?:\\{1,2}|/)Temp", re.I)),
    ("session_brain_uuid", re.compile(r"brain(?:\\{1,2}|/)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)),
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_iso_utc(raw: object) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _parse_checksums(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            name = parts[-1].lstrip("*")
            mapping[name] = parts[0].lower()
    return mapping


def _find_relative_links(markdown: str) -> list[str]:
    relative: list[str] = []
    for match in _MD_LINK_RE.finditer(markdown):
        target = match.group(1)
        if target.startswith(("https://", "http://", "mailto:", "#")):
            continue
        relative.append(target)
    return relative


def _find_leaks(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for label, pattern in _LEAK_PATTERNS:
        for match in pattern.finditer(text):
            hits.append({"kind": label, "sample": match.group(0)[:64]})
            if len(hits) >= 20:
                return hits
    return hits


def _extract_ci_entry(ci_block: object, key: str) -> dict:
    entry: object = None
    if isinstance(ci_block, dict):
        entry = ci_block.get(key)
    elif isinstance(ci_block, list):
        for item in ci_block:
            if isinstance(item, dict) and item.get("label") == key:
                entry = item
                break
    if not isinstance(entry, dict):
        return {
            "present": False,
            "run_id": None,
            "conclusion": None,
            "jobs_total": 0,
            "jobs_passed": 0,
            "completed_at": None,
        }
    jobs = entry.get("jobs")
    if isinstance(jobs, list):
        jobs_total = len(jobs)
        jobs_passed = sum(
            1 for job in jobs if isinstance(job, dict) and job.get("conclusion") == "success"
        )
    else:
        jobs_total = int(entry.get("jobs_total") or 0)
        jobs_passed = int(entry.get("jobs_passed") or 0)
    completed_at = entry.get("updated_at") or entry.get("completed_at")
    return {
        "present": True,
        "run_id": entry.get("run_id"),
        "conclusion": entry.get("conclusion"),
        "jobs_total": jobs_total,
        "jobs_passed": jobs_passed,
        "completed_at": completed_at,
    }


def _extract_unit_count(block: object) -> tuple[int | None, int | None]:
    if not isinstance(block, dict):
        return None, None
    rc = block.get("returncode")
    if rc is None:
        rc = block.get("exit_code")
    tests = block.get("tests")
    if tests is None:
        tests = block.get("ran")
    if tests is None and isinstance(block.get("tail"), list):
        for line in block["tail"]:
            m = re.search(r"Ran\s+(\d+)\s+tests?", str(line))
            if m:
                tests = int(m.group(1))
                break
    if tests is None and isinstance(block.get("tests_summary"), str):
        m = re.search(r"Ran\s+(\d+)\s+tests?", block["tests_summary"])
        if m:
            tests = int(m.group(1))
    return (int(tests) if isinstance(tests, int) else None), (int(rc) if isinstance(rc, int) else None)


def build_numbers_index(
    verification_path: Path,
    release_note_path: Path,
    checksums_path: Path,
    *,
    tag: str,
    expected_checker_sha256: str | None = None,
) -> dict:
    """Build a provenance-bound numbers index from the three release inputs."""
    ver_raw = verification_path.read_text(encoding="utf-8")
    ver = json.loads(ver_raw)
    note_raw = release_note_path.read_text(encoding="utf-8")
    sums_raw = checksums_path.read_text(encoding="utf-8")
    checksums = _parse_checksums(sums_raw)

    local = (
        ver.get("local_exact_source_checks")
        or ver.get("local_checks")
        or ver.get("local")
        or {}
    )
    default_block = (
        local.get("unit_default_temp")
        or local.get("full_parallel_default_temp")
        or ver.get("unit_default_temp")
        or {}
    )
    long_block = (
        local.get("unit_long_temp_150")
        or local.get("full_parallel_150char_temp")
        or ver.get("unit_long_temp_150")
        or {}
    )
    hypo_block = (
        local.get("hypothesis_long_temp_150")
        or local.get("hypothesis_stateful_150char_temp")
        or local.get("hypothesis_150char_temp")
        or ver.get("hypothesis_long_temp_150")
        or {}
    )
    smoke_block = (
        local.get("offline_smoke")
        or local.get("offline_smoke_23")
        or local.get("smoke_23")
        or ver.get("offline_smoke")
        or {}
    )

    def_tests, def_rc = _extract_unit_count(default_block)
    long_tests, long_rc = _extract_unit_count(long_block)
    hypo_tests, hypo_rc = _extract_unit_count(hypo_block)

    ci_block = ver.get("ci") if "ci" in ver else ver.get("github_actions")
    ci_candidate = _extract_ci_entry(ci_block, "candidate")
    ci_tag = _extract_ci_entry(ci_block, "tag")
    ci_master = _extract_ci_entry(ci_block, "master")

    published_at = ver.get("published_at")
    if published_at is None and isinstance(ver.get("release"), dict):
        published_at = ver["release"].get("published_at")
    pub_dt = _parse_iso_utc(published_at)
    master_dt = _parse_iso_utc(ci_master.get("completed_at"))
    master_before_publish = None
    if pub_dt is not None and master_dt is not None:
        master_before_publish = master_dt < pub_dt

    cert = ver.get("certificate") or ver.get("warning_certificate") or {}
    if not cert and isinstance(ver.get("certificate_replay"), dict):
        cert = ver["certificate_replay"].get("source") or ver["certificate_replay"].get("extracted_zip") or {}
    checker_sha = cert.get("checker_sha256") if isinstance(cert, dict) else None
    false_pos = cert.get("false_positives") if isinstance(cert, dict) else None

    archive = ver.get("archive_check") or ver.get("zip_rebuild") or {}
    l1_status = (archive.get("l1") or {}).get("status") if isinstance(archive.get("l1"), dict) else archive.get("l1_status")
    l2_status = (archive.get("l2") or {}).get("status") if isinstance(archive.get("l2"), dict) else archive.get("l2_status")

    version_str = tag.lstrip("v")
    zip_name = f"agentic-vault-{version_str}.zip"
    zip_sha_ver = (
        ver.get("zip_sha256")
        or archive.get("sha256")
        or (ver.get("artifacts") or {}).get(zip_name, {}).get("sha256")
        or (archive.get("l1") or {}).get("given_sha256")
    )
    zip_sha_sums = checksums.get(zip_name)

    relative_links = _find_relative_links(note_raw)
    leaks = _find_leaks(ver_raw)

    return {
        "schema": SCHEMA,
        "tag": tag,
        "expected_checker_sha256": expected_checker_sha256,
        "inputs": {
            "verification": {"name": verification_path.name, "sha256": _sha256_file(verification_path)},
            "release_note": {"name": release_note_path.name, "sha256": _sha256_file(release_note_path)},
            "checksums": {"name": checksums_path.name, "sha256": _sha256_file(checksums_path)},
        },
        "numbers": {
            "default_temp_tests": def_tests,
            "default_temp_rc": def_rc,
            "long_temp_tests": long_tests,
            "long_temp_rc": long_rc,
            "long_temp_length": long_block.get("temp_length") if isinstance(long_block, dict) else None,
            "long_temp_uses_temp": long_block.get("tempfile_uses_temp") if isinstance(long_block, dict) else None,
            "long_paths_enabled": long_block.get("long_paths_enabled") if isinstance(long_block, dict) else None,
            "hypothesis_tests": hypo_tests,
            "hypothesis_rc": hypo_rc,
            "smoke_passed": smoke_block.get("passed") if isinstance(smoke_block, dict) else None,
            "smoke_total": smoke_block.get("total") if isinstance(smoke_block, dict) else None,
            "ci_candidate": ci_candidate,
            "ci_tag": ci_tag,
            "ci_master": ci_master,
            "published_at": published_at,
            "master_ci_completed_before_publish": master_before_publish,
            "zip_name": zip_name,
            "zip_l1_status": l1_status,
            "zip_l2_status": l2_status,
            "zip_sha256_verification": zip_sha_ver.lower() if isinstance(zip_sha_ver, str) else None,
            "zip_sha256_checksums": zip_sha_sums,
            "checker_sha256": checker_sha,
            "certificate_false_positives": false_pos,
            "verification_leaks": leaks,
            "release_note_relative_links": relative_links,
            "release_note_mentions_tag": tag in note_raw,
            "release_note_mentions_checker_sha256": bool(checker_sha and checker_sha in note_raw),
        },
    }


def evaluate_claims(index: dict) -> list[dict[str, object]]:
    """Evaluate every declarative release invariant against the numbers index."""
    n = index["numbers"]
    expected_checker = index.get("expected_checker_sha256")
    ci_cand = n["ci_candidate"]
    ci_tag = n["ci_tag"]
    ci_master = n["ci_master"]

    checks: list[tuple[str, bool]] = [
        (
            "default-TEMP unit suite passed with positive test count",
            n["default_temp_rc"] == 0 and isinstance(n["default_temp_tests"], int) and n["default_temp_tests"] > 0,
        ),
        (
            "150-char Windows TEMP unit suite ran under LongPathsEnabled=0 with identical test count",
            (
                n["long_temp_rc"] == 0
                and isinstance(n["long_temp_tests"], int)
                and n["long_temp_tests"] == n["default_temp_tests"]
                and isinstance(n["long_temp_length"], int)
                and n["long_temp_length"] >= 150
                and n["long_temp_uses_temp"] is True
                and n["long_paths_enabled"] == 0
            ),
        ),
        (
            "150-char TEMP Hypothesis stateful suite passed",
            n["hypothesis_rc"] == 0 and isinstance(n["hypothesis_tests"], int) and n["hypothesis_tests"] > 0,
        ),
        (
            "all offline smoke checks passed",
            isinstance(n["smoke_total"], int) and n["smoke_total"] > 0 and n["smoke_passed"] == n["smoke_total"],
        ),
        (
            "candidate, tag, and master CI runs all succeeded with 100% jobs passing",
            all(
                run["present"]
                and run["conclusion"] == "success"
                and run["jobs_total"] >= 12
                and run["jobs_passed"] == run["jobs_total"]
                for run in (ci_cand, ci_tag, ci_master)
            ),
        ),
        (
            "if published_at is recorded, master CI completed strictly before publication",
            n["published_at"] is None or n["master_ci_completed_before_publish"] is True,
        ),
        (
            "ZIP rebuild passed both L1_bytes and L2_members_blobs and matches SHA256SUMS",
            (
                n["zip_l1_status"] == "match"
                and n["zip_l2_status"] == "match"
                and isinstance(n["zip_sha256_verification"], str)
                and n["zip_sha256_verification"] == n["zip_sha256_checksums"]
            ),
        ),
        (
            "warning-promotion certificate has zero false positives and matches expected checker_sha256",
            (
                n["certificate_false_positives"] == 0
                and isinstance(n["checker_sha256"], str)
                and len(n["checker_sha256"]) == 64
                and (expected_checker is None or n["checker_sha256"] == expected_checker)
            ),
        ),
        (
            "verification.json contains zero local path or username leaks",
            len(n["verification_leaks"]) == 0,
        ),
        (
            "release note has zero relative links and names both the tag and checker_sha256",
            (
                len(n["release_note_relative_links"]) == 0
                and n["release_note_mentions_tag"] is True
                and n["release_note_mentions_checker_sha256"] is True
            ),
        ),
    ]
    return [{"claim": desc, "passed": bool(ok)} for desc, ok in checks]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification", required=True, type=Path, help="Path to verification.json.")
    parser.add_argument("--release-note", required=True, type=Path, help="Path to docs/releases/vX.Y.Z.md.")
    parser.add_argument("--checksums", required=True, type=Path, help="Path to SHA256SUMS.")
    parser.add_argument("--tag", required=True, help="Target release tag (e.g. v0.54.0).")
    parser.add_argument("--expected-checker-sha256", default=None, help="Expected checker_sha256 hex digest.")
    parser.add_argument("--out-numbers", type=Path, default=None, help="Optional path to write numbers_index.json.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")
    args = parser.parse_args(argv)

    try:
        index = build_numbers_index(
            args.verification,
            args.release_note,
            args.checksums,
            tag=args.tag,
            expected_checker_sha256=args.expected_checker_sha256,
        )
    except (OSError, ValueError, KeyError) as exc:
        err = {"schema": SCHEMA, "status": "error", "error": type(exc).__name__}
        print(json.dumps(err, ensure_ascii=False))
        return 2

    if args.out_numbers is not None:
        args.out_numbers.parent.mkdir(parents=True, exist_ok=True)
        args.out_numbers.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    claims = evaluate_claims(index)
    failed = [item for item in claims if not item["passed"]]
    report = {
        "schema": SCHEMA,
        "tag": args.tag,
        "status": "pass" if not failed else "fail",
        "passed_count": len(claims) - len(failed),
        "total_count": len(claims),
        "inputs": index["inputs"],
        "claims": claims,
    }
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for item in claims:
            mark = "PASS" if item["passed"] else "FAIL"
            print(f"[{mark}] {item['claim']}")
        print(f"Summary: {report['passed_count']}/{report['total_count']} ({report['status']})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
