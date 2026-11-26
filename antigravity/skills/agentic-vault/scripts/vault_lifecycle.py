#!/usr/bin/env python3
"""Read-only, bounded note withdrawal & supersession lifecycle auditor.

Inspired by the artifact-governance lifecycle practices in openai/math
(Apache-2.0): structured withdrawal notices separating proof/evidence retraction
from claim falsity, pinned pre-withdrawal Git commit revisions, preserved prior
editions with explicit supersession pointers, and dependent-note cascade lists.

Pure Python 3.10+ standard library only; strictly offline and read-only. Never
mutates vault notes, executes commands, invokes models, or accesses the network.
Diagnostics are strictly content-free (never echoing note prose or withdrawal
reason text).
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import sys
import unicodedata

from vault_evidence import fs_path, is_path_too_long, plain_path
from vault_paths import fold_path_part, unsafe_path_segment
from vault_worktrees import WorktreeScope

SCHEMA = "agentic-vault/lifecycle-audit/v1"
AUTHORITY = "lifecycle_audit_only"
WITHDRAWAL_SCOPE = "proof_or_evidence_only"

LIFECYCLE_WARNING_CODES = frozenset({
    "withdrawn-missing-reason",
    "withdrawn-invalid-revision",
    "superseded-missing-target",
    "superseded-target-unresolved",
    "superseded-cycle",
    "cites-withdrawn-note",
    "cites-superseded-note",
    "lifecycle-incomplete",
})

LIMITATIONS = (
    "Withdrawing a note flags its proof or supporting evidence as retracted; "
    "it does not assert that the underlying proposition is false.",
    "Checks inspect explicit frontmatter fields and literal wiki links only; "
    "undeclared prose references and semantic equivalence are out of scope.",
    "Withdrawn revisions are checked for exact 40- or 64-character lowercase "
    "hex format only; Git object existence is not queried offline.",
    "This tool is read-only and never edits, archives, or deletes vault files.",
)

MAX_NOTES = 10_000
MAX_NOTE_CHARS = 1_000_000
MAX_NOTE_BYTES = MAX_NOTE_CHARS * 4
MAX_CONFIG_BYTES = 256 * 1024
MAX_TOTAL_CHARS = 8_000_000
MAX_WARNINGS = 200
MAX_CASCADE_DEPTH = 16
MAX_CASCADE_ROOTS = 200
MAX_CASCADE_VISITED = 2_000
MAX_DETAIL_STRING = 256
MAX_DETAIL_ITEMS = 64

_GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_LINK_END_RE = re.compile(r"[]\n]")
_COMMENT_MARK_RE = re.compile(r"\s#")
_TOP_FIELD_RE = re.compile(
    r"^(status|withdrawn_reason|withdrawn_revision|superseded_by):(.*)$"
)
_BLOCK_INDICATOR_RE = re.compile(r"^[|>](?:[+-]?[1-9]|[1-9][+-]?|[+-])?$")
_YAML_NULL_LITERALS = frozenset({"", "null", "~", "[]", "{}"})
_ACTIVE_CITING_STATUSES = frozenset({"active", "draft", ""})
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)


class LifecycleError(Exception):
    """Operational error code that never leaks raw note text."""


def _bounded(value, depth: int = 0):
    if isinstance(value, str):
        return value[:MAX_DETAIL_STRING]
    if depth >= 3:
        return str(value)[:MAX_DETAIL_STRING]
    if isinstance(value, dict):
        return {
            str(k)[:80]: _bounded(v, depth + 1)
            for k, v in list(value.items())[:MAX_DETAIL_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(v, depth + 1) for v in value[:MAX_DETAIL_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_DETAIL_STRING]


def _make_warning(code: str, path: str, line: int, message: str, *, details: dict | None = None) -> dict:
    facts = {"code": code, "path": path, "line": line, "details": details or {}}
    digest = hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "issue_id": "life-" + digest[:20],
        "code": code,
        "severity": "warning",
        "path": path[:MAX_DETAIL_STRING],
        "line": line,
        "message": message[:512],
        "details": _bounded(details or {}),
    }


def _valid_rel_path(path: str) -> bool:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith(("/", "\\"))
        or ":" in path
        or "\\" in path
    ):
        return False
    return not any(unsafe_path_segment(part) for part in path.split("/"))


def _denied(path: str, config: dict) -> bool:
    parts = [fold_path_part(p) for p in path.split("/")]
    if ".git" in parts:
        return True
    folded = "/".join(parts)
    runtime = "00-meta/.agentic-vault/runtime"
    if folded == runtime or folded.startswith(runtime + "/"):
        return True
    for zone in config.get("deny_zones", []) or []:
        norm_zone = "/".join(
            fold_path_part(p)
            for p in str(zone).replace("\\", "/").strip("/").split("/")
            if p
        )
        if norm_zone and (
            ("/" in norm_zone and (folded == norm_zone or folded.startswith(norm_zone + "/")))
            or ("/" not in norm_zone and norm_zone in parts)
        ):
            return True
    return any(fold_path_part(str(x)) in parts for x in config.get("exclude_dirs", []) or [])


def _before_comment(value: str) -> str:
    stripped = value.lstrip()
    if stripped.startswith("#"):
        return ""
    mark = _COMMENT_MARK_RE.search(value)
    return value if mark is None else value[: mark.end() - 1].rstrip()


def _parse_scalar(value: str) -> tuple[bool, str]:
    """Return (is_quoted, unquoted_text) for a single-line YAML scalar value."""
    raw = value.strip()
    if raw and raw[0] in ('"', "'"):
        quote = raw[0]
        buf: list[str] = []
        i = 1
        while i < len(raw):
            ch = raw[i]
            if quote == '"' and ch == "\\" and i + 1 < len(raw):
                buf.append(raw[i + 1])
                i += 2
                continue
            if quote == "'" and ch == "'" and i + 1 < len(raw) and raw[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            if ch == quote:
                tail = raw[i + 1 :].strip()
                if not tail or tail.startswith("#"):
                    return True, "".join(buf).strip()
                break
            buf.append(ch)
            i += 1
        return True, ""
    return False, _before_comment(raw).strip()


def _clean_lines(raw_lines: list[str]) -> list[str]:
    """Strip fenced code blocks and HTML comments from body lines, preserving count."""
    result: list[str] = []
    fence: tuple[str, int] | None = None
    comment = False
    for raw in raw_lines:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", raw)
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= fence[1]
                and not marker[2].strip()
            ):
                fence = None
            result.append("")
            continue
        if marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            result.append("")
            continue
        visible = ""
        offset = 0
        while offset < len(raw):
            token = "-->" if comment else "<!--"
            idx = raw.find(token, offset)
            if idx < 0:
                if not comment:
                    visible += raw[offset:]
                break
            if not comment:
                visible += raw[offset:idx]
            comment = not comment
            offset = idx + len(token)
        result.append(visible)
    return result


def _frontmatter_end(lines: list[str]) -> int:
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return i + 1
    return 0


def _without_code_spans(line: str) -> str:
    runs = [m.span() for m in _BACKTICK_RUN_RE.finditer(line)]
    later = [0] * len(runs)
    longest = 0
    for j in range(len(runs) - 1, -1, -1):
        if j + 1 < len(runs) and line.find("\n", runs[j][1], runs[j + 1][0]) >= 0:
            longest = 0
        later[j], longest = longest, max(longest, runs[j][1] - runs[j][0])
    kept: list[str] = []
    cursor = 0
    j = 0
    while j < len(runs):
        start = max(cursor, runs[j][0])
        size = runs[j][1] - start
        close = min(size, later[j])
        if 2 * close > size:
            j += 1
            while runs[j][1] - runs[j][0] < close:
                j += 1
            end = runs[j][0] + close
        elif size >= 2:
            end = start + (size // 2) * 2
        else:
            j += 1
            continue
        kept.append(line[cursor:start])
        cursor = end
        if cursor == runs[j][1]:
            j += 1
    kept.append(line[cursor:])
    return "".join(kept)


def _wiki_link_texts(line: str) -> list[str]:
    texts: list[str] = []
    search = 0
    while True:
        opening = line.find("[[", search)
        if opening < 0:
            return texts
        stop = _LINK_END_RE.search(line, opening + 2)
        end = stop.start() if stop else len(line)
        if end > opening + 2 and line.startswith("]]", end):
            texts.append(line[opening + 2 : end])
            search = end + 2
        else:
            search = end + 1


def _link_target(raw: str, table_context: bool = False) -> str:
    sep = r"\\?\|" if table_context else r"(?<!\\)\|"
    head = re.split(sep, raw, maxsplit=1)[0].replace("\\|", "|").strip()
    target, _, _ = head.partition("#")
    return target.strip()


def _destination_path(target: str, source: str) -> str:
    if target.startswith(("./", "../")):
        return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    return posixpath.normpath(target)


def _resolve_target(
    target: str,
    source: str,
    notes: dict[str, str],
    stems: dict[str, list[str]],
    qualified: dict[str, list[str]],
) -> list[str]:
    if not target:
        return [source]
    name = target[:-3] if target.lower().endswith(".md") else target
    if "/" not in name and not name.startswith("."):
        return stems.get(unicodedata.normalize("NFC", name), [])
    if name.startswith("/") or ":" in name or "\\" in name:
        return []
    path = _destination_path(target, source)
    if not _valid_rel_path(path):
        return []
    if path in notes:
        return [path]
    base = path[:-3] if path.lower().endswith(".md") else path
    return qualified.get(unicodedata.normalize("NFC", base), [])


def _is_nonempty_yaml_value(quoted: bool, scalar: str) -> bool:
    if not scalar:
        return False
    if not quoted and scalar.casefold() in _YAML_NULL_LITERALS:
        return False
    return True


def _extract_lifecycle_meta(raw_lines: list[str], end: int) -> dict:
    """Parse top-level lifecycle keys from raw frontmatter without echoing prose."""
    meta = {
        "status": "",
        "status_line": 1,
        "has_withdrawn_reason": False,
        "withdrawn_revision_raw": None,
        "withdrawn_revision_line": 1,
        "superseded_by_raw": None,
        "superseded_by_line": 1,
    }
    if end < 3:
        return meta
    i = 1
    while i < end - 1:
        line = raw_lines[i]
        match = _TOP_FIELD_RE.match(line)
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2)
        quoted, scalar = _parse_scalar(rest)
        if key == "status":
            if _is_nonempty_yaml_value(quoted, scalar):
                meta["status"] = scalar.casefold()
                meta["status_line"] = i + 1
            else:
                meta["status"] = ""
                meta["status_line"] = i + 1
        elif key == "withdrawn_reason":
            if not quoted and _BLOCK_INDICATOR_RE.fullmatch(scalar):
                # Block scalar: require at least one non-empty indented content line.
                j = i + 1
                while j < end - 1 and (not raw_lines[j].strip() or raw_lines[j].startswith((" ", "\t"))):
                    if raw_lines[j].strip():
                        meta["has_withdrawn_reason"] = True
                        break
                    j += 1
            elif not quoted and not scalar:
                # Indented multiline plain scalar: scan continuation lines.
                j = i + 1
                while j < end - 1 and (not raw_lines[j].strip() or raw_lines[j].startswith((" ", "\t"))):
                    cont_quoted, cont_scalar = _parse_scalar(raw_lines[j])
                    if _is_nonempty_yaml_value(cont_quoted, cont_scalar):
                        meta["has_withdrawn_reason"] = True
                        break
                    j += 1
            elif _is_nonempty_yaml_value(quoted, scalar):
                meta["has_withdrawn_reason"] = True
        elif key == "withdrawn_revision":
            if _is_nonempty_yaml_value(quoted, scalar):
                meta["withdrawn_revision_raw"] = scalar
                meta["withdrawn_revision_line"] = i + 1
        elif key == "superseded_by":
            if _is_nonempty_yaml_value(quoted, scalar):
                meta["superseded_by_raw"] = scalar
                meta["superseded_by_line"] = i + 1
        i += 1
    return meta


def _parse_superseded_target_text(raw: str) -> str:
    text = raw.strip()
    links = _wiki_link_texts(text)
    if links:
        return _link_target(links[0])
    return _link_target(text)


def _stat_is_symlink_or_reparse(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE_FLAG)


def _classify_lstat(path: Path) -> tuple[str, os.stat_result | None]:
    """Return ('dir'|'file'|'link'|'other'|'error', stat_result_or_None)."""
    try:
        st = os.lstat(fs_path(path))
    except OSError:
        return "error", None
    if _stat_is_symlink_or_reparse(st):
        return "link", st
    if stat.S_ISDIR(st.st_mode):
        return "dir", st
    if stat.S_ISREG(st.st_mode):
        return "file", st
    return "other", st


def _read_regular_file_bytes(path: Path, max_bytes: int) -> tuple[str, bytes | None]:
    """Safely read a regular non-symlink file with O_NOFOLLOW + fstat verification.

    Returns ('ok', data) | ('link', None) | ('non_regular', None) | ('too_large', None) | ('error', None).
    """
    kind, lst = _classify_lstat(path)
    if kind == "link":
        return "link", None
    if kind == "error" or lst is None:
        return "error", None
    if kind != "file":
        return "non_regular", None
    if lst.st_size > max_bytes:
        return "too_large", None

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = -1
    try:
        fd = os.open(os.fspath(fs_path(path)), flags)
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            return "non_regular", None
        if os.name != "nt" and (fst.st_ino != lst.st_ino or fst.st_dev != lst.st_dev):
            return "error", None
        if fst.st_size > max_bytes:
            return "too_large", None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return "too_large", None
            chunks.append(chunk)
        # Re-check lstat after read on Windows where O_NOFOLLOW is absent.
        if os.name == "nt":
            post_kind, _ = _classify_lstat(path)
            if post_kind == "link":
                return "link", None
        return "ok", b"".join(chunks)
    except OSError:
        return "error", None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _resolve_superseded_chains(
    superseded_edges: dict[str, str | None],
) -> tuple[dict[str, str | None], set[str], dict[str, int]]:
    """O(N) 3-color cycle detection and terminal replacement computation."""
    # state: 0 = unvisited, 1 = visiting, 2 = done
    visit_state: dict[str, int] = {}
    terminal: dict[str, str | None] = {}
    in_cycle: set[str] = set()
    chain_len: dict[str, int] = {}

    for start in sorted(superseded_edges):
        if visit_state.get(start, 0) == 2:
            continue
        path_nodes: list[str] = []
        path_index: dict[str, int] = {}
        curr: str | None = start
        while curr is not None and curr in superseded_edges:
            st = visit_state.get(curr, 0)
            if st == 2:
                break
            if st == 1:
                # Cycle detected: every node on path_nodes reaches this cycle.
                cycle_start_idx = path_index[curr]
                total_len = len(path_nodes)
                for idx, node in enumerate(path_nodes):
                    visit_state[node] = 2
                    in_cycle.add(node)
                    terminal[node] = None
                    chain_len[node] = total_len - idx if idx <= cycle_start_idx else total_len - cycle_start_idx
                path_nodes.clear()
                break
            visit_state[curr] = 1
            path_index[curr] = len(path_nodes)
            path_nodes.append(curr)
            curr = superseded_edges[curr]

        if not path_nodes:
            continue

        # Reached either None, a non-superseded note, or an already-resolved superseded node.
        if curr is not None and curr in superseded_edges and curr in in_cycle:
            base_len = chain_len.get(curr, 1)
            for offset, node in enumerate(reversed(path_nodes), start=1):
                visit_state[node] = 2
                in_cycle.add(node)
                terminal[node] = None
                chain_len[node] = base_len + offset
        else:
            term = terminal[curr] if (curr is not None and curr in superseded_edges) else curr
            for node in reversed(path_nodes):
                visit_state[node] = 2
                terminal[node] = term

    return terminal, in_cycle, chain_len


def analyze_lifecycle(
    notes: dict[str, str],
    config: dict,
    *,
    extra_incomplete: list[str] | None = None,
    read_errors: int = 0,
) -> dict:
    """Analyze note withdrawal/supersession metadata, citations, and cascade impact."""
    if not isinstance(notes, dict) or not isinstance(config, dict):
        raise LifecycleError("invalid_input")

    approved: dict[str, str] = {}
    incomplete: list[str] = list(extra_incomplete or [])
    total_chars = 0

    for path in sorted(notes):
        if not _valid_rel_path(path) or _denied(path, config):
            continue
        text = notes[path]
        if not isinstance(text, str):
            incomplete.append("non-text note")
            continue
        if (
            len(approved) >= MAX_NOTES
            or len(text) > MAX_NOTE_CHARS
            or total_chars + len(text) > MAX_TOTAL_CHARS
        ):
            incomplete.append("note input limit")
            continue
        approved[path] = text
        total_chars += len(text)

    stems: dict[str, list[str]] = {}
    qualified: dict[str, list[str]] = {}
    raw_frontmatter: dict[str, list[str]] = {}
    cleaned_body: dict[str, list[str]] = {}
    fm_ends: dict[str, int] = {}
    metas: dict[str, dict] = {}

    for path, text in approved.items():
        stem = path.rsplit("/", 1)[-1]
        stem = stem[:-3] if stem.lower().endswith(".md") else stem
        stems.setdefault(unicodedata.normalize("NFC", stem), []).append(path)
        qual = path[:-3] if path.lower().endswith(".md") else path
        qualified.setdefault(unicodedata.normalize("NFC", qual), []).append(path)

        raw_lines = text.splitlines()
        end = _frontmatter_end(raw_lines)
        fm_ends[path] = end
        raw_frontmatter[path] = raw_lines[:end]
        metas[path] = _extract_lifecycle_meta(raw_lines, end)
        cleaned_body[path] = _clean_lines(raw_lines[end:])

    warnings: list[dict] = []
    cap = config.get("lint_max_warnings", MAX_WARNINGS)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        cap = MAX_WARNINGS
    cap = min(cap, MAX_WARNINGS)

    withdrawn_info: dict[str, dict] = {}
    superseded_edges: dict[str, str | None] = {}

    # Pass 1: validate withdrawn / superseded frontmatter declarations.
    for path in sorted(approved):
        meta = metas[path]
        status = meta["status"]
        rev_raw = meta["withdrawn_revision_raw"]
        valid_rev = rev_raw if (isinstance(rev_raw, str) and _GIT_SHA_RE.fullmatch(rev_raw)) else None
        if rev_raw is not None and valid_rev is None:
            warnings.append(
                _make_warning(
                    "withdrawn-invalid-revision",
                    path,
                    meta["withdrawn_revision_line"],
                    "withdrawn_revision must be a full 40- or 64-character lowercase hex Git SHA",
                    details={"field": "withdrawn_revision"},
                )
            )
        if status == "withdrawn":
            if not meta["has_withdrawn_reason"]:
                warnings.append(
                    _make_warning(
                        "withdrawn-missing-reason",
                        path,
                        meta["status_line"],
                        "Withdrawn note should declare a non-empty withdrawn_reason explaining the gap",
                        details={"withdrawal_scope": WITHDRAWAL_SCOPE},
                    )
                )
            withdrawn_info[path] = {
                "withdrawn_revision": valid_rev,
                "has_withdrawn_reason": meta["has_withdrawn_reason"],
            }
        elif status == "superseded":
            raw_sup = meta["superseded_by_raw"]
            if not raw_sup:
                superseded_edges[path] = None
                warnings.append(
                    _make_warning(
                        "superseded-missing-target",
                        path,
                        meta["status_line"],
                        "Superseded note must declare superseded_by pointing to its replacement note",
                        details={"field": "superseded_by"},
                    )
                )
            else:
                target_text = _parse_superseded_target_text(raw_sup)
                policy_target = _destination_path(target_text, path) if target_text else ""
                if not target_text or _denied(policy_target, config):
                    superseded_edges[path] = None
                    warnings.append(
                        _make_warning(
                            "superseded-target-unresolved",
                            path,
                            meta["superseded_by_line"],
                            "superseded_by target does not resolve to a unique allowed note",
                            details={"reason": "denied_or_empty"},
                        )
                    )
                else:
                    dests = _resolve_target(target_text, path, approved, stems, qualified)
                    if len(dests) != 1 or dests[0] == path:
                        superseded_edges[path] = None
                        reason = (
                            "self_reference"
                            if (len(dests) == 1 and dests[0] == path)
                            else "ambiguous"
                            if len(dests) > 1
                            else "not_found"
                        )
                        warnings.append(
                            _make_warning(
                                "superseded-target-unresolved",
                                path,
                                meta["superseded_by_line"],
                                "superseded_by target does not resolve to a unique allowed note",
                                details={"reason": reason, "candidate_count": len(dests)},
                            )
                        )
                    else:
                        superseded_edges[path] = dests[0]

    # Pass 2: O(N) cycle detection and terminal replacement computation.
    terminal_replacement, cycle_nodes, cycle_lengths = _resolve_superseded_chains(superseded_edges)
    for start in sorted(cycle_nodes):
        warnings.append(
            _make_warning(
                "superseded-cycle",
                start,
                metas[start]["superseded_by_line"],
                "superseded_by forms a cycle without reaching a non-superseded replacement",
                details={"chain_length": cycle_lengths.get(start, 1)},
            )
        )

    # Pass 3: collect resolved frontmatter + body wikilinks per note & build reverse edges.
    note_links: dict[str, list[tuple[int, str]]] = {}
    note_target_set: dict[str, set[str]] = {}
    reverse_active_edges: dict[str, set[str]] = {}

    for path in sorted(approved):
        links: list[tuple[int, str]] = []
        targets_seen: set[str] = set()
        is_active_citing = metas[path]["status"] in _ACTIVE_CITING_STATUSES

        def _scan_line(line_number: int, text_line: str, is_body: bool) -> None:
            scan_text = _without_code_spans(text_line) if is_body else text_line
            in_table = is_body and scan_text.lstrip().startswith("|")
            for raw in _wiki_link_texts(scan_text):
                target = _link_target(raw, in_table)
                if not target:
                    continue
                policy_target = _destination_path(target, path)
                if _denied(policy_target, config):
                    continue
                dests = _resolve_target(target, path, approved, stems, qualified)
                if len(dests) == 1:
                    dest = dests[0]
                    if dest != path:
                        links.append((line_number, dest))
                        targets_seen.add(dest)
                        if is_active_citing:
                            reverse_active_edges.setdefault(dest, set()).add(path)

        # Frontmatter wikilinks (lines 2 .. end - 1, excluding delimiter and comment lines).
        fm_lines = raw_frontmatter[path]
        if len(fm_lines) >= 3:
            for idx in range(1, len(fm_lines) - 1):
                if fm_lines[idx].lstrip().startswith("#"):
                    continue
                _scan_line(idx + 1, fm_lines[idx], is_body=False)

        # Body wikilinks (after code fences / HTML comments stripped).
        body_offset = fm_ends[path]
        for offset, line in enumerate(cleaned_body[path], start=1):
            _scan_line(body_offset + offset, line, is_body=True)

        note_links[path] = links
        note_target_set[path] = targets_seen

    # Pass 4: flag active/draft notes citing withdrawn or superseded notes.
    for path in sorted(approved):
        if metas[path]["status"] not in _ACTIVE_CITING_STATUSES:
            continue
        emitted_targets: set[tuple[str, str]] = set()
        targets_in_note = note_target_set[path]
        for line_number, dest in note_links[path]:
            if dest in withdrawn_info and ("withdrawn", dest) not in emitted_targets:
                emitted_targets.add(("withdrawn", dest))
                info = withdrawn_info[dest]
                warnings.append(
                    _make_warning(
                        "cites-withdrawn-note",
                        path,
                        line_number,
                        "Active note links to a withdrawn note; verify whether dependent claims remain supported",
                        details={
                            "target": dest,
                            "withdrawn_revision": info["withdrawn_revision"],
                            "has_withdrawn_reason": info["has_withdrawn_reason"],
                            "withdrawal_scope": WITHDRAWAL_SCOPE,
                        },
                    )
                )
            elif dest in superseded_edges and ("superseded", dest) not in emitted_targets:
                direct_rep = superseded_edges[dest]
                term_rep = terminal_replacement.get(dest)
                acknowledges = (
                    (direct_rep is not None and direct_rep in targets_in_note)
                    or (term_rep is not None and term_rep in targets_in_note)
                )
                if not acknowledges:
                    emitted_targets.add(("superseded", dest))
                    warnings.append(
                        _make_warning(
                            "cites-superseded-note",
                            path,
                            line_number,
                            "Active note links to a superseded note without referencing its replacement",
                            details={
                                "target": dest,
                                "superseded_by": direct_rep,
                                "terminal_replacement": term_rep,
                            },
                        )
                    )
            if len(warnings) > cap:
                incomplete.append("warning output limit")
                break
        if len(warnings) > cap:
            break

    # Pass 5: bounded BFS cascade impact over active/draft dependents.
    cascade: list[dict] = []
    tracked_roots = sorted(set(withdrawn_info) | set(superseded_edges))
    if len(tracked_roots) > MAX_CASCADE_ROOTS:
        incomplete.append("cascade output limit")
        tracked_roots = tracked_roots[:MAX_CASCADE_ROOTS]
    for root_path in tracked_roots:
        direct = sorted(reverse_active_edges.get(root_path, set()))
        visited_dep: set[str] = {root_path}
        transitive: list[str] = []
        depth_truncated = False
        queue: deque[tuple[str, int]] = deque()
        for dep in direct:
            if len(transitive) >= MAX_CASCADE_VISITED:
                depth_truncated = True
                break
            visited_dep.add(dep)
            transitive.append(dep)
            queue.append((dep, 1))
        while queue:
            node, depth = queue.popleft()
            next_nodes = sorted(reverse_active_edges.get(node, set()))
            for nxt in next_nodes:
                if nxt in visited_dep:
                    continue
                if depth >= MAX_CASCADE_DEPTH or len(transitive) >= MAX_CASCADE_VISITED:
                    depth_truncated = True
                    continue
                visited_dep.add(nxt)
                transitive.append(nxt)
                queue.append((nxt, depth + 1))
        entry = {
            "path": root_path,
            "status": metas[root_path]["status"],
            "direct_dependents": direct[:MAX_DETAIL_ITEMS],
            "direct_dependent_count": len(direct),
            "transitive_dependents": sorted(transitive)[:MAX_DETAIL_ITEMS],
            "transitive_dependent_count": len(transitive),
            "depth_truncated": depth_truncated,
        }
        if root_path in withdrawn_info:
            entry["withdrawn_revision"] = withdrawn_info[root_path]["withdrawn_revision"]
            entry["has_withdrawn_reason"] = withdrawn_info[root_path]["has_withdrawn_reason"]
            entry["withdrawal_scope"] = WITHDRAWAL_SCOPE
        else:
            entry["superseded_by"] = superseded_edges[root_path]
            entry["terminal_replacement"] = terminal_replacement.get(root_path)
            entry["in_cycle"] = root_path in cycle_nodes
        cascade.append(entry)

    warnings.sort(key=lambda w: (w["path"], w["line"], w["code"], w["issue_id"]))
    if len(warnings) > cap:
        incomplete.append("warning output limit")
    warnings = warnings[:cap]
    if read_errors > 0:
        incomplete.append("read errors")
    if incomplete:
        inc_details: dict[str, object] = {"reasons": sorted(set(incomplete))}
        if read_errors > 0:
            inc_details["read_errors"] = read_errors
        warnings.append(
            _make_warning(
                "lifecycle-incomplete",
                "",
                0,
                "Lifecycle audit is incomplete; omitted notes or diagnostics cannot certify clean citations",
                details=inc_details,
            )
        )

    ok = len(warnings) == 0
    return {
        "schema": SCHEMA,
        "authority": AUTHORITY,
        "ok": ok,
        "status": "clean" if ok else "warnings",
        "counts": {
            "notes_scanned": len(approved),
            "withdrawn_notes": len(withdrawn_info),
            "superseded_notes": len(superseded_edges),
            "warnings": len(warnings),
            "cascade_roots": len(cascade),
        },
        "warnings": warnings,
        "cascade": cascade,
        "limitations": list(LIMITATIONS),
    }


def check_vault(vault: Path) -> dict:
    """Read-only vault scan enforcing deny/exclude zones, linked worktrees, symlink refusal, and config."""
    if not isinstance(vault, Path):
        raise LifecycleError("invalid_vault")

    # Check vault root BEFORE resolving so symlinked/junctioned roots are refused.
    raw_kind, _ = _classify_lstat(vault)
    if raw_kind != "dir":
        raise LifecycleError("vault_not_found")
    try:
        resolved = plain_path(fs_path(vault).resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise LifecycleError("vault_not_found") from exc
    resolved_kind, _ = _classify_lstat(resolved)
    if resolved_kind != "dir":
        raise LifecycleError("vault_not_found")

    meta_dir = resolved / "00-meta"
    meta_kind, _ = _classify_lstat(meta_dir)
    if meta_kind != "dir":
        raise LifecycleError("not_a_vault")

    config_path = meta_dir / "vault-config.json"
    cfg_status, cfg_bytes = _read_regular_file_bytes(config_path, MAX_CONFIG_BYTES)
    if cfg_status != "ok" or cfg_bytes is None:
        raise LifecycleError("not_a_vault")

    try:
        from vault_healthcheck import validate_config
        raw_cfg = json.loads(cfg_bytes.decode("utf-8-sig"))
        config = validate_config(raw_cfg)
    except (ImportError, OSError, UnicodeDecodeError, ValueError, TypeError, Exception) as exc:
        raise LifecycleError("invalid_config") from exc

    worktree_scope = WorktreeScope(resolved)
    all_zones = tuple(
        str(z)
        for z in (*(config.get("deny_zones") or ()), *(config.get("exclude_dirs") or ()))
    )

    notes: dict[str, str] = {}
    read_errors = 0
    walk_incomplete: list[str] = []
    total_chars = 0

    # Iterative scandir traversal applying fs_path() at every level (safe under Windows LongPathsEnabled=0).
    dir_stack: list[tuple[Path, str]] = [(resolved, "")]
    while dir_stack:
        cur_dir, rel_dir = dir_stack.pop()
        try:
            with os.scandir(os.fspath(fs_path(cur_dir))) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            read_errors += 1
            continue

        child_dirs: list[tuple[Path, str]] = []
        for entry in entries:
            name = entry.name
            rel = f"{rel_dir}/{name}" if rel_dir else name
            cand = cur_dir / name
            kind, st = _classify_lstat(cand)
            if kind == "error":
                read_errors += 1
                continue
            if kind == "link":
                continue

            parts = rel.split("/")
            if kind == "dir":
                if not _valid_rel_path(rel) or _denied(rel, config):
                    continue
                if worktree_scope.registered(fs_path(cand)):
                    continue
                if worktree_scope.denied(parts, all_zones):
                    continue
                child_dirs.append((cand, rel))
            elif kind == "file":
                if not name.lower().endswith(".md"):
                    continue
                if not _valid_rel_path(rel) or _denied(rel, config):
                    continue
                if worktree_scope.denied(parts, all_zones):
                    continue
                if len(notes) >= MAX_NOTES:
                    walk_incomplete.append("note input limit")
                    continue
                if st is not None and st.st_size > MAX_NOTE_BYTES:
                    walk_incomplete.append("note input limit")
                    continue
                status, data = _read_regular_file_bytes(cand, MAX_NOTE_BYTES)
                if status == "link":
                    continue
                if status == "too_large":
                    walk_incomplete.append("note input limit")
                    continue
                if status != "ok" or data is None:
                    read_errors += 1
                    continue
                raw = data.decode("utf-8", errors="replace")
                if len(raw) > MAX_NOTE_CHARS or total_chars + len(raw) > MAX_TOTAL_CHARS:
                    walk_incomplete.append("note input limit")
                    continue
                notes[rel] = raw
                total_chars += len(raw)

        # Push in reverse sorted order so DFS pops in sorted order.
        for item in reversed(child_dirs):
            dir_stack.append(item)

    return analyze_lifecycle(
        notes,
        config,
        extra_incomplete=walk_incomplete,
        read_errors=read_errors,
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path, help="Vault root directory.")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("check", help="Run read-only withdrawal and supersession lifecycle audit.")
    cmd.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report.")
    args = parser.parse_args(argv)
    try:
        report = check_vault(args.vault)
    except LifecycleError as exc:
        err = {"schema": SCHEMA, "authority": AUTHORITY, "ok": False, "status": "error", "error": str(exc)}
        print(json.dumps(err, ensure_ascii=False))
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        c = report["counts"]
        print(
            f"Lifecycle audit ({report['status']}): scanned={c['notes_scanned']} "
            f"withdrawn={c['withdrawn_notes']} superseded={c['superseded_notes']} "
            f"warnings={c['warnings']}"
        )
        for w in report["warnings"]:
            loc = f"{w['path']}:{w['line']}" if w["path"] else "<vault>"
            print(f"  [{w['code']}] {loc} - {w['message']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
