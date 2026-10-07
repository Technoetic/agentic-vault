#!/usr/bin/env python3
"""Normalize explicit qmd JSON exports into untrusted retrieval candidates.

Only exact qmd://<explicit-collection>/<literal-relative-Markdown-path> values
and array position are used. Scores, snippets, explanations and approval data
are ignored. This adapter cannot know the destination vault's current policy;
vault_retrieval must resolve these paths and reread original permitted notes.
No provider command, network call, model download or output file write occurs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

# Reuse the vault's portable path rule without relying on ambient PYTHONPATH.
_SCRIPT_DIR = str(Path(__file__).resolve().parents[1] / "skills" / "agentic-vault" / "scripts")
sys.path.insert(0, _SCRIPT_DIR)
try:
    from vault_paths import relative_parts
finally:
    sys.path.remove(_SCRIPT_DIR)

MAX_INPUT_BYTES = 1024 * 1024
MAX_ROWS = 1000
MAX_CANDIDATES = 50
MAX_REJECTIONS = 50
MAX_PATH_CHARS = 1024
MAX_CANDIDATE_BYTES = 48 * 1024  # leave space for diagnostics within a 64KiB sidecar
_COLLECTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _result(status="ok") -> dict:
    return {"provider": "qmd", "candidates": [], "diagnostics": {
        "status": status, "processing_complete": status == "ok",
        "candidate_coverage_complete": status == "ok", "rows_received": 0,
        "accepted": 0, "skipped_invalid": 0, "duplicates": 0,
        "omitted_candidate_limit": 0, "omitted_candidate_byte_limit": 0,
        "rejected_rows": [], "omitted_rejections": 0,
        "provider_status": "normalization_only_unverified",
        "vault_policy": "must_be_checked_by_retrieve",
        "limits": {"input_bytes": MAX_INPUT_BYTES, "rows": MAX_ROWS,
                   "candidates": MAX_CANDIDATES, "rejection_diagnostics": MAX_REJECTIONS},
    }}


def _valid_collection(collection) -> bool:
    if not isinstance(collection, str) or not _COLLECTION.fullmatch(collection):
        return False
    try:
        relative_parts(collection, "collection")
    except ValueError:
        return False
    return True


def _path(row: object, collection: str) -> str:
    if not isinstance(row, dict) or not isinstance(row.get("file"), str):
        raise ValueError("missing_file_uri")
    uri = row["file"]
    if len(uri) > MAX_PATH_CHARS + len(collection) + 7:
        raise ValueError("path_too_long")
    if not uri.startswith("qmd://") or any(char in uri for char in "\\%?#") or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in uri):
        raise ValueError("unsafe_uri")
    try:
        parsed = urlsplit(uri)
    except ValueError as exc:
        # urlsplit's NFKC netloc errors can embed raw credentials in the text.
        raise ValueError("unsafe_uri") from exc
    if parsed.scheme != "qmd" or parsed.netloc != collection or parsed.query or parsed.fragment or not parsed.path.startswith("/"):
        raise ValueError("wrong_collection_or_uri")
    path = parsed.path[1:]
    if not path.casefold().endswith(".md") or len(path) > MAX_PATH_CHARS:
        raise ValueError("invalid_markdown_path")
    try:
        parts = relative_parts(path, "candidate path")
    except ValueError as exc:
        raise ValueError("unsafe_portable_path") from exc
    return "/".join(parts)


def convert(results, collection) -> dict:
    """Return bounded paths/ranks; malformed roots fail, malformed rows skip.

    collection is a safe literal namespace and is case sensitive. Rank is the
    original one-based array position, not a provider score or authority.
    Casefold path duplicates keep the earliest URI spelling and rank.
    """
    if not _valid_collection(collection):
        return _result("invalid_collection")
    if not isinstance(results, list) or len(results) > MAX_ROWS:
        return _result("invalid_input")
    result = _result()
    diagnostics = result["diagnostics"]
    diagnostics["rows_received"] = len(results)
    seen = set()
    for number, row in enumerate(results, 1):
        try:
            path = _path(row, collection)
        except ValueError as exc:
            diagnostics["skipped_invalid"] += 1
            diagnostics["candidate_coverage_complete"] = False
            if len(diagnostics["rejected_rows"]) < MAX_REJECTIONS:
                diagnostics["rejected_rows"].append({"row": number, "reason": str(exc)})
            else:
                diagnostics["omitted_rejections"] += 1
            continue
        folded = path.casefold()
        if folded in seen:
            diagnostics["duplicates"] += 1
            continue
        seen.add(folded)
        if len(result["candidates"]) >= MAX_CANDIDATES:
            diagnostics["omitted_candidate_limit"] += 1
            diagnostics["candidate_coverage_complete"] = False
            continue
        candidate = {"path": path, "rank": number}
        prospective = [*result["candidates"], candidate]
        if len(json.dumps(prospective, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_CANDIDATE_BYTES:
            diagnostics["omitted_candidate_byte_limit"] += 1
            diagnostics["candidate_coverage_complete"] = False
            continue
        result["candidates"].append(candidate)
    diagnostics["accepted"] = len(result["candidates"])
    return result


def _stamp(metadata) -> tuple:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _regular(metadata) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or getattr(metadata, "st_file_attributes", 0) & _REPARSE:
        raise ValueError("unsafe_input")


def _input_path(path: Path) -> Path:
    absolute = path.absolute()
    # Inspect before resolving so a symlink/junction is never concealed by
    # Path.resolve. Explicit exports may be outside the repository.
    for component in (*reversed(absolute.parents), absolute):
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE:
            raise ValueError("unsafe_input")
    _regular(absolute.lstat())
    return absolute


def _read_file(path: Path) -> bytes:
    safe = _input_path(path)
    original = _stamp(safe.lstat())
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(safe, flags)
    try:
        before = os.fstat(descriptor)
        _regular(before)
        # Windows path stat and descriptor stat can represent creation time
        # differently. Compare identity/size/mtime across APIs, and compare
        # full stamps within each API before/after the read.
        if before.st_size > MAX_INPUT_BYTES or _stamp(before)[:4] != original[:4]:
            raise ValueError("invalid_input")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(before.st_size + 1)
        after = os.fstat(descriptor)
        _regular(after)
        if len(raw) != before.st_size or _stamp(after) != _stamp(before) or _stamp(_input_path(path).lstat()) != original:
            raise ValueError("input_changed")
        return raw
    finally:
        os.close(descriptor)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_input_field")
        result[key] = value
    return result


def _finite(_value):
    raise ValueError("nonfinite_input")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo untrusted arguments/provider text into a stderr report.
        raise ValueError("invalid_options")


def main(argv=None) -> int:
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("--collection", required=True)
        parser.add_argument("--input", type=Path, help="explicit qmd JSON export; otherwise bounded stdin")
        args = parser.parse_args(argv)
        if not _valid_collection(args.collection):
            result = _result("invalid_collection")
        else:
            raw = _read_file(args.input) if args.input else sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError("input_too_large")
            results = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_fields, parse_constant=_finite)
            result = convert(results, args.collection)
    except (ValueError, OSError, UnicodeError, RecursionError, OverflowError):
        result = _result("invalid_input")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["diagnostics"]["status"] == "ok" else 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
