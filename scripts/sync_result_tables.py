#!/usr/bin/env python3
"""Deterministic marker-based Markdown table sync and fail-closed drift checker.

Selectively adapts the `<!-- table:NAME --> ... <!-- /table:NAME -->` pattern from
ai3inc/opentoolsearch (`scripts/update_tables.py`, v0.2.1, Apache-2.0) into a
strict, stdlib-only generator and CI drift checker:

  - Reads a declarative JSON spec binding each named table to a JSON source
    artifact, a dot-separated `rows_path`, and ordered `columns` definitions.
  - Renders deterministic GFM Markdown tables (escaping pipe characters and
    newlines, rejecting HTML comment marker injection, formatting finite numbers
    predictably).
  - `check` fails closed (exit 1) when any marker block in any target Markdown
    file differs from the rendered output, and exit 2 on invalid spec/JSON,
    duplicate keys, non-finite numbers, malformed/duplicate/unclosed/orphan
    markers, path escapes, or symlink/reparse paths.
  - `write` validates all targets before atomically updating marker interiors
    (`os.replace`, UTF-8 LF) without modifying text outside markers.

Python 3.10+ standard library only; zero network calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

SCHEMA = "agentic-vault/table-sync/v1"
MAX_SPEC_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_TARGET_BYTES = 8 * 1024 * 1024
MAX_TABLES = 64
MAX_ROWS = 500
MAX_FORMAT_DIGITS = 6
_TABLE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_MARKER_TOKEN = re.compile(r"<!--\s*(/?)table:([^\s>]*)\s*-->")
_PIPE_ESCAPE = re.compile(r"(\\*)\|")
_BACKTICK_RUN = re.compile(r"`+")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class TableSyncError(ValueError):
    """Fail-closed validation or IO error (exit 2)."""


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _check_leaf_regular(path: Path, limit: int) -> bytes:
    """Read a regular file up to limit bytes, refusing symlink/reparse leaves (without
    failing on OS system symlinks above the workspace such as macOS /var -> private/var)."""
    if _is_link_or_reparse(path):
        raise TableSyncError(f"symlink or reparse path refused: {path}")
    try:
        info = path.lstat()
    except OSError as exc:
        raise TableSyncError(f"file missing or unreadable: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise TableSyncError(f"file missing, non-regular, or exceeds limit: {path}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise TableSyncError(f"file exceeds limit: {path}")
    return data


def _load_strict_json(path: Path, limit: int) -> Any:
    raw = _check_leaf_regular(path, limit)

    def unique_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise TableSyncError(f"duplicate JSON key: {key}")
            out[key] = value
        return out

    def reject_nonfinite(_val):
        raise TableSyncError("non-finite JSON number")

    def parse_finite_float(text: str) -> float:
        val = float(text)
        if not math.isfinite(val):
            raise TableSyncError("non-finite JSON number")
        return val

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=reject_nonfinite,
            parse_float=parse_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TableSyncError(f"invalid JSON in {path}: {exc}") from None


def _resolve_under(base_dir: Path, raw_path: object, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        raise TableSyncError(f"invalid {label} path")
    if "\\" in raw_path or any(ord(ch) < 32 for ch in raw_path):
        raise TableSyncError(f"unsafe {label} path")
    candidate = Path(raw_path)
    if candidate.is_absolute() or bool(candidate.drive) or bool(candidate.root):
        raise TableSyncError(f"{label} path must be relative to the spec directory")
    parts = raw_path.split("/")
    if any(part in ("", ".", "..") or ":" in part or part.rstrip(" .") != part for part in parts):
        raise TableSyncError(f"{label} path contains unsafe segment")
    resolved_base = base_dir.resolve()
    current = resolved_base
    for part in parts:
        current = current / part
        if _is_link_or_reparse(current):
            raise TableSyncError(f"symlink or reparse component refused in {label} path: {raw_path}")
    try:
        resolved = current.resolve()
        resolved.relative_to(resolved_base)
    except (OSError, ValueError):
        raise TableSyncError(f"{label} path escapes the spec directory") from None
    return resolved


def _extract_path(obj: Any, dotted: object) -> Any:
    if not isinstance(dotted, str):
        raise TableSyncError("rows_path must be a string")
    if dotted == "" or dotted == ".":
        return obj
    current = obj
    for segment in dotted.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            raise TableSyncError(f"rows_path segment not found: {segment}")
    return current


def _validate_fmt(fmt: object) -> str | None:
    if fmt is None:
        return None
    if not isinstance(fmt, str):
        raise TableSyncError("column fmt must be a string or null")
    if fmt in ("int", "code"):
        return fmt
    if fmt.startswith(("pct:", "fixed:")):
        digits_raw = fmt.split(":", 1)[1]
        if not digits_raw.isdigit() or not 0 <= int(digits_raw) <= MAX_FORMAT_DIGITS:
            raise TableSyncError(f"invalid precision in column format: {fmt}")
        return fmt
    raise TableSyncError(f"unsupported column format: {fmt}")


def _fence_code_span(text: str) -> str:
    longest = max((len(m.group(0)) for m in _BACKTICK_RUN.finditer(text)), default=0)
    fence = "`" * (longest + 1)
    if text.startswith("`") or text.endswith("`") or (text.startswith(" ") and text.endswith(" ") and text.strip()):
        return f"{fence} {text} {fence}"
    return f"{fence}{text}{fence}"


def _sanitize_cell(text: str) -> str:
    clean = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    if "<!--" in clean or "-->" in clean:
        raise TableSyncError("cell text must not contain HTML comment markers")
    return _PIPE_ESCAPE.sub(lambda m: m.group(1) * 2 + r"\|", clean)


def _format_cell(value: Any, fmt: str | None) -> str:
    validated_fmt = _validate_fmt(fmt)
    if value is None:
        return "—"
    if validated_fmt is not None:
        if validated_fmt.startswith("pct:"):
            digits = int(validated_fmt.split(":", 1)[1])
            if type(value) not in (int, float) or not math.isfinite(value):
                raise TableSyncError(f"non-numeric value for {validated_fmt}")
            return _sanitize_cell(f"{100.0 * value:.{digits}f}%")
        if validated_fmt.startswith("fixed:"):
            digits = int(validated_fmt.split(":", 1)[1])
            if type(value) not in (int, float) or not math.isfinite(value):
                raise TableSyncError(f"non-numeric value for {validated_fmt}")
            return _sanitize_cell(f"{value:.{digits}f}")
        if validated_fmt == "int":
            if type(value) is not int:
                raise TableSyncError("non-integer value for int format")
            return str(value)
        if validated_fmt == "code":
            raw_text = "true" if value is True else "false" if value is False else str(value)
            return _fence_code_span(_sanitize_cell(raw_text))
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not math.isfinite(value):
        raise TableSyncError("non-finite float cell value")
    return _sanitize_cell(str(value))


def render_table(rows: Sequence[dict], columns: Sequence[dict]) -> str:
    """Render a deterministic GitHub-Flavored Markdown table from rows and column specs."""
    if not isinstance(columns, list) or not columns:
        raise TableSyncError("columns must be a non-empty list")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise TableSyncError(f"rows must be a list of at most {MAX_ROWS} objects")
    headers: list[str] = []
    aligns: list[str] = []
    validated_cols: list[tuple[str, str | None]] = []
    for col in columns:
        if not isinstance(col, dict) or not isinstance(col.get("header"), str) or not isinstance(col.get("key"), str):
            raise TableSyncError("each column requires string header and key")
        fmt = _validate_fmt(col.get("fmt"))
        headers.append(_format_cell(col["header"], None))
        align = col.get("align", "left")
        if align == "right":
            aligns.append("---:")
        elif align == "center":
            aligns.append(":---:")
        elif align == "left":
            aligns.append("---")
        else:
            raise TableSyncError(f"unsupported alignment: {align}")
        validated_cols.append((col["key"], fmt))

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(aligns) + " |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            raise TableSyncError("every row must be an object")
        cells = []
        for key, fmt in validated_cols:
            val: Any = row
            for part in key.split("."):
                if isinstance(val, dict) and part in val:
                    val = val[part]
                else:
                    val = None
                    break
            cells.append(_format_cell(val, fmt))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _load_tables(spec_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    spec = _load_strict_json(spec_path, MAX_SPEC_BYTES)
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise TableSyncError("spec must be an object with schema_version == 1")
    tables_spec = spec.get("tables")
    if not isinstance(tables_spec, dict) or not 1 <= len(tables_spec) <= MAX_TABLES:
        raise TableSyncError(f"tables must be a mapping of 1..{MAX_TABLES} named specs")

    base_dir = spec_path.parent
    rendered: dict[str, str] = {}
    source_digests: dict[str, str] = {}
    cache: dict[Path, Any] = {}

    for name, cfg in tables_spec.items():
        if not isinstance(name, str) or not _TABLE_NAME.fullmatch(name):
            raise TableSyncError(f"invalid table name: {name!r}")
        if not isinstance(cfg, dict):
            raise TableSyncError(f"table {name} spec must be an object")
        src_path = _resolve_under(base_dir, cfg.get("source"), f"table {name} source")
        if src_path not in cache:
            raw = _check_leaf_regular(src_path, MAX_SOURCE_BYTES)
            source_digests[str(cfg["source"])] = hashlib.sha256(raw).hexdigest()
            cache[src_path] = _load_strict_json(src_path, MAX_SOURCE_BYTES)
        rows = _extract_path(cache[src_path], cfg.get("rows_path", ""))
        rendered[name] = render_table(rows, cfg.get("columns"))

    return rendered, source_digests


def sync_markdown_text(text: str, tables: dict[str, str]) -> tuple[str, list[dict]]:
    """Sequentially scan and replace `<!-- table:NAME --> ... <!-- /table:NAME -->` blocks.

    Refuses nested markers, duplicate opening markers, unclosed markers, orphan closing
    markers, malformed marker names, and unknown table names so prose is never destroyed.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[tuple[str, int, int, int, int, str, str]] = []
    seen: set[str] = set()
    open_block: tuple[str, int, int, str] | None = None

    for match in _MARKER_TOKEN.finditer(normalized):
        is_close = match.group(1) == "/"
        name = match.group(2)
        if not _TABLE_NAME.fullmatch(name):
            raise TableSyncError(f"malformed table marker name: {name!r}")
        if not is_close:
            if open_block is not None:
                raise TableSyncError(f"nested or duplicate opening marker {name} inside unclosed {open_block[0]}")
            if name not in tables:
                raise TableSyncError(f"unknown table marker in target markdown: {name}")
            if name in seen:
                raise TableSyncError(f"duplicate table marker block in target markdown: {name}")
            open_block = (name, match.start(), match.end(), match.group(0))
        else:
            if open_block is None:
                raise TableSyncError(f"orphan closing table marker: {name}")
            if name != open_block[0]:
                raise TableSyncError(f"mismatched closing table marker: expected {open_block[0]}, got {name}")
            blocks.append((
                open_block[0], open_block[1], open_block[2],
                match.start(), match.end(), open_block[3], match.group(0),
            ))
            seen.add(name)
            open_block = None

    if open_block is not None:
        raise TableSyncError(f"unclosed table marker in target markdown: {open_block[0]}")

    pieces: list[str] = []
    findings: list[dict] = []
    cursor = 0
    for name, open_start, open_end, close_start, close_end, open_tag, close_tag in blocks:
        expected_body = "\n" + tables[name] + "\n"
        current_body = normalized[open_end:close_start]
        drifted = current_body != expected_body
        findings.append({
            "table": name,
            "drifted": drifted,
            "rendered_sha256": hashlib.sha256(tables[name].encode("utf-8")).hexdigest(),
        })
        pieces.append(normalized[cursor:open_start])
        pieces.append(open_tag)
        pieces.append(expected_body if drifted else current_body)
        pieces.append(close_tag)
        cursor = close_end
    pieces.append(normalized[cursor:])
    return "".join(pieces), findings


def _atomic_write_lf(path: Path, text: str) -> None:
    if _is_link_or_reparse(path):
        raise TableSyncError(f"symlink or reparse target refused: {path}")
    payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".table-sync-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def run_sync(spec_path: Path, targets: Sequence[Path], *, write: bool = False) -> dict:
    """Check or atomically update Markdown table blocks against the JSON spec.

    Validates all target files before writing any target so multi-target writes fail closed.
    """
    if not targets:
        raise TableSyncError("at least one target Markdown file is required")
    tables, source_digests = _load_tables(spec_path)
    seen_tables: set[str] = set()
    staged: list[tuple[Path, str, list[dict], bool]] = []
    any_drifted = False

    for target in targets:
        raw = _check_leaf_regular(target, MAX_TARGET_BYTES)
        try:
            original = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TableSyncError(f"target file is not valid UTF-8: {target}") from exc
        updated, findings = sync_markdown_text(original, tables)
        if not findings:
            raise TableSyncError(f"no table markers found in {target}")
        seen_tables.update(item["table"] for item in findings)
        file_drifted = any(item["drifted"] for item in findings)
        if file_drifted:
            any_drifted = True
        staged.append((target, updated, findings, file_drifted))

    file_reports: list[dict] = []
    for target, updated, findings, file_drifted in staged:
        if write and file_drifted:
            _atomic_write_lf(target, updated)
        file_reports.append({
            "target": target.name,
            "tables": findings,
            "drifted": file_drifted,
            "written": bool(write and file_drifted),
        })

    unused = sorted(set(tables) - seen_tables)
    return {
        "schema": SCHEMA,
        "mode": "write" if write else "check",
        "source_sha256": source_digests,
        "files": file_reports,
        "unused_spec_tables": unused,
        "in_sync": (not any_drifted) or write,
        "drifted": any_drifted,
    }


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "verify markers match JSON sources (exit 1 on drift)"),
                            ("write", "atomically update marker interiors in place")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--spec", type=Path, required=True, help="JSON table specification file")
        cmd.add_argument("targets", nargs="+", type=Path, help="target Markdown file(s)")

    args = parser.parse_args(argv)
    try:
        report = run_sync(args.spec, args.targets, write=(args.command == "write"))
    except (TableSyncError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.command == "check" and report["drifted"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
