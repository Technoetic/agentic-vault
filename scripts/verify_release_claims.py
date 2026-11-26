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
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Iterable


SCHEMA = "agentic-vault/release-claims/v1"
EXACT_TREE_SCHEMA = "agentic-vault/exact-tree-membership/v1"
REVIEW_STATUSES = frozenset({"unchecked", "independent_reviewed"})
_ZERO_EVIDENCE_MAX_BYTES = 256 * 1024
_ZERO_EVIDENCE_MAX_DEPTH = 32
_ZERO_EVIDENCE_MAX_NODES = 4096
_ZERO_EVIDENCE_KEYS = frozenset({
    "count", "total", "failures", "failed", "findings",
    "leaks", "errors", "unresolved", "false_positives", "bad",
})
_ZERO_CLEAN_STATUS_KEYS = frozenset({"status", "verdict", "result", "conclusion"})
_ZERO_CLEAN_STATUS_VALUES = frozenset({"ok", "pass", "passed", "clean", "zero", "none", "empty"})
_ZERO_CLEAN_BOOL_KEYS = frozenset({"ok", "passed", "clean", "valid"})
_ZERO_METADATA_STRING_KEYS = frozenset({"schema", "tool", "version", "checked_at", "timestamp", "note", "kind"})
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
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


def _normalize_bundle_rel(raw: str) -> str:
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or raw != raw.strip()
        or "\\" in raw
        or ":" in raw
        or raw.startswith("/")
        or any(ord(ch) < 32 for ch in raw)
    ):
        raise ValueError(f"invalid relative bundle path: {raw!r}")
    parts = raw.split("/")
    if any(p in ("", ".", "..") or p != p.strip() for p in parts):
        raise ValueError(f"invalid relative bundle path: {raw!r}")
    return "/".join(parts)


def _stat_is_reparse_or_symlink(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE_FLAG)


def _is_reparse_or_symlink(path: Path) -> bool:
    return _stat_is_reparse_or_symlink(os.lstat(path))


def _strict_json_loads(text: str) -> object:
    def _no_dupes(pairs: list[tuple[str, object]]) -> dict[str, object]:
        out: dict[str, object] = {}
        for k, v in pairs:
            if k in out:
                raise ValueError(f"duplicate_json_key:{k}")
            out[k] = v
        return out

    def _reject_const(token: str) -> object:
        raise ValueError(f"non_finite_json:{token}")

    def _finite_float(raw: str) -> float:
        val = float(raw)
        if not math.isfinite(val):
            raise ValueError("non_finite_json")
        return val

    return json.loads(
        text,
        object_pairs_hook=_no_dupes,
        parse_constant=_reject_const,
        parse_float=_finite_float,
    )


def _read_bounded_regular(path: Path, max_bytes: int) -> tuple[str, bytes | None]:
    try:
        lst = os.lstat(path)
    except OSError:
        return "unreadable", None
    if _stat_is_reparse_or_symlink(lst) or not stat.S_ISREG(lst.st_mode):
        return "non_regular", None
    if lst.st_size > max_bytes:
        return "oversized", None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = -1
    try:
        fd = os.open(os.fspath(path), flags)
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            return "non_regular", None
        if os.name != "nt" and (fst.st_ino != lst.st_ino or fst.st_dev != lst.st_dev):
            return "changed_during_read", None
        if fst.st_size > max_bytes:
            return "oversized", None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return "oversized", None
            chunks.append(chunk)
        if os.name == "nt":
            if _stat_is_reparse_or_symlink(os.lstat(path)):
                return "non_regular", None
        return "ok", b"".join(chunks)
    except OSError:
        return "unreadable", None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _check_zero_evidence_file(path: Path) -> tuple[bool, str]:
    """Verify a required negative-evidence file represents zero findings/failures."""
    read_status, raw = _read_bounded_regular(path, _ZERO_EVIDENCE_MAX_BYTES)
    if read_status != "ok" or raw is None:
        return False, read_status
    if len(raw) == 0:
        return True, "empty_bytes"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, "non_utf8"
    stripped = text.strip()
    if not stripped:
        return True, "empty_text"
    try:
        parsed = _strict_json_loads(stripped)
    except (ValueError, RecursionError):
        return False, "nonempty_or_invalid_json"
    if parsed == [] or parsed == {}:
        return True, "empty_json"
    if not isinstance(parsed, dict):
        return False, "unsupported_json"
    matched_keys = [k for k in parsed if k in _ZERO_EVIDENCE_KEYS]
    if not matched_keys:
        return False, "no_zero_evidence_keys"

    # Iterative strict validation of all nested keys and values (fail-closed).
    stack: list[tuple[str, str, object, int]] = [
        (k, k, v, 1) for k, v in parsed.items()
    ]
    nodes = 0
    while stack:
        key_name, key_path, val, depth = stack.pop()
        nodes += 1
        if depth > _ZERO_EVIDENCE_MAX_DEPTH or nodes > _ZERO_EVIDENCE_MAX_NODES:
            return False, "json_too_complex"
        low_key = key_name.casefold()
        is_zero_key = key_name in _ZERO_EVIDENCE_KEYS

        if isinstance(val, bool):
            if is_zero_key:
                return False, f"invalid_bool:{key_path}"
            if low_key in _ZERO_CLEAN_BOOL_KEYS:
                if val is not True:
                    return False, f"false_clean_flag:{key_path}"
            else:
                return False, f"unexpected_bool:{key_path}"
        elif isinstance(val, int):
            if val != 0:
                return False, f"nonzero:{key_path}"
        elif isinstance(val, float):
            if not math.isfinite(val) or val != 0.0:
                return False, f"nonzero:{key_path}"
        elif isinstance(val, list):
            if len(val) != 0:
                return False, f"nonempty_list:{key_path}"
        elif isinstance(val, dict):
            if is_zero_key and len(val) != 0:
                return False, f"nonempty_dict:{key_path}"
            for child_k, child_v in val.items():
                stack.append((str(child_k), f"{key_path}.{child_k}", child_v, depth + 1))
        elif isinstance(val, str):
            if is_zero_key:
                return False, f"unsupported_type:{key_path}"
            if low_key in _ZERO_CLEAN_STATUS_KEYS:
                if val.strip().casefold() not in _ZERO_CLEAN_STATUS_VALUES:
                    return False, f"non_clean_status:{key_path}"
            elif depth == 1 and low_key in _ZERO_METADATA_STRING_KEYS:
                continue
            else:
                return False, f"unexpected_string:{key_path}"
        elif val is None:
            if is_zero_key:
                return False, f"unsupported_type:{key_path}"
        else:
            return False, f"unsupported_type:{key_path}"
    return True, "zero_json"


def check_exact_tree_membership(
    root: Path,
    expected_files: Iterable[str],
    *,
    required_zero_evidence: Iterable[str] = (),
) -> dict:
    """Enforce exact file/directory membership and required zero-count negative evidence.

    Adapted from openai/math MUB-6 verification (execution/check_records.py + VERIFY.md):
    rejects missing files, unexpected extra files/directories, and symlinks/non-regular
    entries, and treats empty/zero-count failure files as required negative evidence.
    """
    if not isinstance(root, Path):
        raise ValueError("bundle root must be a Path")
    try:
        root_st = os.lstat(root)
    except OSError as exc:
        raise ValueError("bundle root must be an existing non-symlink directory") from exc
    if _stat_is_reparse_or_symlink(root_st) or not stat.S_ISDIR(root_st.st_mode):
        raise ValueError("bundle root must be an existing non-symlink directory")
    expected_set: set[str] = set()
    for item in expected_files:
        norm = _normalize_bundle_rel(item)
        if norm in expected_set:
            raise ValueError(f"duplicate expected bundle file: {norm}")
        expected_set.add(norm)
    if not expected_set:
        raise ValueError("expected_files must be non-empty")

    zero_set: set[str] = set()
    for item in required_zero_evidence:
        zero_set.add(_normalize_bundle_rel(item))

    expected_dirs: set[str] = set()
    for rel in expected_set:
        parts = rel.split("/")
        for idx in range(1, len(parts)):
            expected_dirs.add("/".join(parts[:idx]))

    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    unsupported_entries: list[str] = []

    def _on_walk_error(exc: OSError) -> None:
        fname = getattr(exc, "filename", None)
        if fname:
            try:
                rel = Path(fname).relative_to(root).as_posix()
                unsupported_entries.append(rel or ".")
                return
            except ValueError:
                pass
        unsupported_entries.append("<unreadable-directory>")

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_on_walk_error):
        cur = Path(dirpath)
        rel_dir = "" if cur == root else cur.relative_to(root).as_posix()
        kept_dirs: list[str] = []
        for dname in sorted(dirnames):
            rel = f"{rel_dir}/{dname}" if rel_dir else dname
            dpath = cur / dname
            try:
                st = os.lstat(dpath)
            except OSError:
                unsupported_entries.append(rel)
                continue
            if _stat_is_reparse_or_symlink(st) or not stat.S_ISDIR(st.st_mode):
                unsupported_entries.append(rel)
                continue
            actual_dirs.add(rel)
            kept_dirs.append(dname)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            fpath = cur / fname
            try:
                st = os.lstat(fpath)
            except OSError:
                unsupported_entries.append(rel)
                continue
            if _stat_is_reparse_or_symlink(st) or not stat.S_ISREG(st.st_mode):
                unsupported_entries.append(rel)
                continue
            actual_files.add(rel)

    missing_files = sorted(expected_set - actual_files)
    extra_files = sorted(actual_files - expected_set)
    missing_dirs = sorted(expected_dirs - actual_dirs)
    extra_dirs = sorted(actual_dirs - expected_dirs)
    undeclared_zero = sorted(zero_set - expected_set)
    zero_checked: list[dict[str, str]] = []
    nonzero_zero: list[dict[str, str]] = []

    for rel in sorted(zero_set & expected_set):
        if rel not in actual_files:
            nonzero_zero.append({"path": rel, "reason": "missing"})
            continue
        ok_zero, kind = _check_zero_evidence_file(root / rel)
        if ok_zero:
            zero_checked.append({"path": rel, "kind": kind})
        else:
            nonzero_zero.append({"path": rel, "reason": kind})

    ok = not (
        missing_files
        or extra_files
        or missing_dirs
        or extra_dirs
        or unsupported_entries
        or undeclared_zero
        or nonzero_zero
    )
    return {
        "schema": EXACT_TREE_SCHEMA,
        "ok": ok,
        "root_name": root.name,
        "expected_file_count": len(expected_set),
        "actual_file_count": len(actual_files),
        "missing_files": missing_files,
        "extra_files": extra_files,
        "missing_dirs": missing_dirs,
        "extra_dirs": extra_dirs,
        "unsupported_entries": sorted(unsupported_entries),
        "zero_evidence_checked": zero_checked,
        "undeclared_zero_evidence": undeclared_zero,
        "nonzero_zero_evidence": nonzero_zero,
    }


def _extract_review_status(ver: dict) -> str:
    top_present = "review_status" in ver
    top_raw = ver.get("review_status")
    nested_present = isinstance(ver.get("review"), dict) and "status" in ver["review"]
    nested_raw = ver["review"].get("status") if nested_present else None
    if top_present and nested_present:
        if top_raw != nested_raw:
            return "unchecked"
    raw = top_raw if top_present else nested_raw
    if isinstance(raw, str) and raw in REVIEW_STATUSES:
        return raw
    return "unchecked"


def build_numbers_index(
    verification_path: Path,
    release_note_path: Path,
    checksums_path: Path,
    *,
    tag: str,
    expected_checker_sha256: str | None = None,
    bundle_dir: Path | None = None,
    expected_bundle_files: Iterable[str] | None = None,
    required_zero_evidence: Iterable[str] = (),
    require_independent_review: bool = False,
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
    review_status = _extract_review_status(ver)
    zero_list = list(required_zero_evidence)
    expected_list = list(expected_bundle_files) if expected_bundle_files is not None else None
    bundle_tree = None
    if bundle_dir is None:
        if expected_list is not None or zero_list:
            raise ValueError("bundle_dir is required when expected_bundle_files or required_zero_evidence are provided")
    else:
        if expected_list is None:
            raise ValueError("expected_bundle_files is required when bundle_dir is provided")
        bundle_tree = check_exact_tree_membership(
            bundle_dir,
            expected_list,
            required_zero_evidence=zero_list,
        )

    return {
        "schema": SCHEMA,
        "tag": tag,
        "expected_checker_sha256": expected_checker_sha256,
        "require_independent_review": bool(require_independent_review),
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
            "review_status": review_status,
            "bundle_tree": bundle_tree,
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
    if n.get("bundle_tree") is not None:
        checks.append((
            "release bundle satisfies exact tree membership and required zero-count negative evidence",
            bool(n["bundle_tree"].get("ok")),
        ))
    if index.get("require_independent_review") is True:
        checks.append((
            "independent review status is explicitly independent_reviewed",
            n.get("review_status") == "independent_reviewed",
        ))
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
    parser.add_argument("--bundle-dir", type=Path, default=None, help="Optional release bundle directory to check for exact tree membership.")
    parser.add_argument("--expected-bundle-file", action="append", default=None, dest="expected_bundle_files", help="Expected relative file inside --bundle-dir (repeatable).")
    parser.add_argument("--required-zero-evidence", action="append", default=[], help="Expected relative zero-count negative-evidence file inside --bundle-dir (repeatable).")
    parser.add_argument("--require-independent-review", action="store_true", help="Fail closed unless verification.json declares review_status='independent_reviewed'.")
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
            bundle_dir=args.bundle_dir,
            expected_bundle_files=args.expected_bundle_files,
            required_zero_evidence=args.required_zero_evidence,
            require_independent_review=args.require_independent_review,
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
