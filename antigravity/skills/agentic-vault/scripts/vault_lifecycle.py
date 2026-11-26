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

from vault_evidence import fs_path, is_path_too_long
from vault_paths import fold_path_part

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
MAX_TOTAL_CHARS = 8_000_000
MAX_WARNINGS = 200
MAX_CASCADE_DEPTH = 16
MAX_DETAIL_STRING = 256
MAX_DETAIL_ITEMS = 64

_GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_LINK_END_RE = re.compile(r"[]\n]")
_COMMENT_MARK_RE = re.compile(r"\s#")
_TOP_FIELD_RE = re.compile(
    r"^(status|withdrawn_reason|withdrawn_revision|superseded_by):(.*)$"
)
_ACTIVE_CITING_STATUSES = frozenset({"active", "draft", ""})


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
    return (
        isinstance(path, str)
        and bool(path)
        and not path.startswith(("/", "\\"))
        and ":" not in path
        and "\\" not in path
        and all(part not in ("", ".", "..") for part in path.split("/"))
    )


def _denied(path: str, config: dict) -> bool:
    folded = "/".join(fold_path_part(p) for p in path.split("/"))
    runtime = "00-meta/.agentic-vault/runtime"
    if folded == runtime or folded.startswith(runtime + "/"):
        return True
    parts = folded.split("/")
    for zone in config.get("deny_zones", []) or []:
        norm_zone = "/".join(
            fold_path_part(p)
            for p in str(zone).replace("\\", "/").strip("/").split("/")
        )
        if norm_zone and (
            ("/" in norm_zone and (folded == norm_zone or folded.startswith(norm_zone + "/")))
            or ("/" not in norm_zone and norm_zone in parts)
        ):
            return True
    return any(fold_path_part(str(x)) in parts for x in config.get("exclude_dirs", []) or [])


def _before_comment(value: str) -> str:
    mark = _COMMENT_MARK_RE.search(value)
    return value if mark is None else value[: mark.end() - 1].rstrip()


def _unquote_scalar(value: str) -> str:
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
            if ch == quote:
                tail = raw[i + 1 :].strip()
                if not tail or tail.startswith("#"):
                    return "".join(buf).strip()
                break
            buf.append(ch)
            i += 1
        return ""
    return _before_comment(raw).strip()


def _clean_lines(text: str) -> list[str]:
    """Strip fenced code blocks and HTML comments while preserving 1-based line indices."""
    result: list[str] = []
    fence: tuple[str, int] | None = None
    comment = False
    for raw in text.splitlines():
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


def _extract_lifecycle_meta(lines: list[str], end: int) -> dict:
    """Parse top-level lifecycle keys from frontmatter without echoing prose."""
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
        line = lines[i]
        match = _TOP_FIELD_RE.match(line)
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2)
        scalar = _unquote_scalar(rest)
        if key == "status":
            meta["status"] = scalar.casefold()
            meta["status_line"] = i + 1
        elif key == "withdrawn_reason":
            if scalar and scalar not in ("|", ">", "|-", ">-", "|+", ">+"):
                meta["has_withdrawn_reason"] = True
            elif scalar in ("|", ">", "|-", ">-", "|+", ">+"):
                # Block scalar: verify at least one non-empty indented line follows.
                j = i + 1
                while j < end - 1 and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    if lines[j].strip():
                        meta["has_withdrawn_reason"] = True
                        break
                    j += 1
        elif key == "withdrawn_revision":
            if scalar:
                meta["withdrawn_revision_raw"] = scalar
                meta["withdrawn_revision_line"] = i + 1
        elif key == "superseded_by":
            if scalar:
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


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        st = os.lstat(fs_path(path))
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(attrs & reparse)


def analyze_lifecycle(notes: dict[str, str], config: dict) -> dict:
    """Analyze note withdrawal/supersession metadata, citations, and cascade impact."""
    if not isinstance(notes, dict) or not isinstance(config, dict):
        raise LifecycleError("invalid_input")

    approved: dict[str, str] = {}
    incomplete: list[str] = []
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
    cleaned: dict[str, list[str]] = {}
    fm_ends: dict[str, int] = {}
    metas: dict[str, dict] = {}

    for path, text in approved.items():
        stem = path.rsplit("/", 1)[-1]
        stem = stem[:-3] if stem.lower().endswith(".md") else stem
        stems.setdefault(unicodedata.normalize("NFC", stem), []).append(path)
        qual = path[:-3] if path.lower().endswith(".md") else path
        qualified.setdefault(unicodedata.normalize("NFC", qual), []).append(path)
        lines = _clean_lines(text)
        cleaned[path] = lines
        end = _frontmatter_end(lines)
        fm_ends[path] = end
        metas[path] = _extract_lifecycle_meta(lines, end)

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

    # Pass 2: detect cycles and compute terminal replacements for superseded chains.
    terminal_replacement: dict[str, str | None] = {}
    cycle_nodes: set[str] = set()
    for start in sorted(superseded_edges):
        visited: list[str] = [start]
        seen: set[str] = {start}
        curr = superseded_edges[start]
        is_cycle = False
        while curr is not None:
            if curr in seen:
                is_cycle = True
                break
            visited.append(curr)
            seen.add(curr)
            if curr in superseded_edges:
                curr = superseded_edges[curr]
            else:
                break
        if is_cycle:
            cycle_nodes.add(start)
            terminal_replacement[start] = None
            warnings.append(
                _make_warning(
                    "superseded-cycle",
                    start,
                    metas[start]["superseded_by_line"],
                    "superseded_by forms a cycle without reaching a non-superseded replacement",
                    details={"chain_length": len(visited)},
                )
            )
        else:
            terminal_replacement[start] = curr

    # Pass 3: collect resolved body/frontmatter wikilinks per note & build reverse edges.
    note_links: dict[str, list[tuple[int, str]]] = {}
    note_target_set: dict[str, set[str]] = {}
    reverse_active_edges: dict[str, set[str]] = {}

    for path in sorted(approved):
        lines = cleaned[path]
        start_line = fm_ends[path]
        links: list[tuple[int, str]] = []
        targets_seen: set[str] = set()
        for line_number in range(start_line + 1, len(lines) + 1):
            body_line = _without_code_spans(lines[line_number - 1])
            in_table = body_line.lstrip().startswith("|")
            for raw in _wiki_link_texts(body_line):
                target = _link_target(raw, in_table)
                if not target:
                    continue
                if re.search(r"\.[^./]+$", target) and not target.lower().endswith(".md"):
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
                        if metas[path]["status"] in _ACTIVE_CITING_STATUSES:
                            reverse_active_edges.setdefault(dest, set()).add(path)
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
    for root_path in tracked_roots:
        direct = sorted(reverse_active_edges.get(root_path, set()))
        visited_dep: set[str] = {root_path}
        transitive: list[str] = []
        depth_truncated = False
        queue: deque[tuple[str, int]] = deque()
        for dep in direct:
            visited_dep.add(dep)
            transitive.append(dep)
            queue.append((dep, 1))
        while queue:
            node, depth = queue.popleft()
            next_nodes = sorted(reverse_active_edges.get(node, set()))
            for nxt in next_nodes:
                if nxt in visited_dep:
                    continue
                if depth >= MAX_CASCADE_DEPTH:
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
    if incomplete:
        warnings.append(
            _make_warning(
                "lifecycle-incomplete",
                "",
                0,
                "Lifecycle audit is incomplete; omitted notes or diagnostics cannot certify clean citations",
                details={"reasons": sorted(set(incomplete))},
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
    """Read-only vault scan enforcing deny/exclude zones, symlink refusal, and config."""
    if not isinstance(vault, Path):
        raise LifecycleError("invalid_vault")
    resolved = vault.resolve()
    if not resolved.is_dir() or _is_symlink_or_reparse(resolved):
        raise LifecycleError("vault_not_found")
    config_path = resolved / "00-meta" / "vault-config.json"
    if not config_path.is_file() or _is_symlink_or_reparse(config_path):
        raise LifecycleError("not_a_vault")
    try:
        from vault_healthcheck import HealthcheckError, validate_config
        raw_cfg = json.loads(Path(fs_path(config_path)).read_text(encoding="utf-8-sig"))
        config = validate_config(raw_cfg)
    except (ImportError, OSError, ValueError, TypeError, Exception) as exc:
        raise LifecycleError("invalid_config") from exc

    excluded_worktrees: set[str] = set()
    try:
        from vault_worktrees import registered_worktrees
        for wt in registered_worktrees(resolved):
            try:
                rel = wt.resolve().relative_to(resolved).as_posix()
                if rel and rel != ".":
                    excluded_worktrees.add("/".join(fold_path_part(p) for p in rel.split("/")))
            except ValueError:
                pass
    except Exception:
        pass

    notes: dict[str, str] = {}
    read_errors = 0

    for dirpath, dirnames, filenames in os.walk(fs_path(resolved), followlinks=False):
        cur_dir = Path(dirpath)
        try:
            rel_dir = cur_dir.relative_to(Path(fs_path(resolved))).as_posix()
        except ValueError:
            dirnames[:] = []
            continue
        if rel_dir == ".":
            rel_dir = ""

        kept_dirs: list[str] = []
        for dname in sorted(dirnames):
            rel = f"{rel_dir}/{dname}" if rel_dir else dname
            folded = "/".join(fold_path_part(p) for p in rel.split("/"))
            if _denied(rel, config) or folded in excluded_worktrees:
                continue
            cand = cur_dir / dname
            if _is_symlink_or_reparse(cand):
                continue
            kept_dirs.append(dname)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            if not fname.lower().endswith(".md"):
                continue
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            if not _valid_rel_path(rel) or _denied(rel, config):
                continue
            fpath = cur_dir / fname
            if _is_symlink_or_reparse(fpath):
                continue
            try:
                st = os.lstat(fs_path(fpath))
                if not stat.S_ISREG(st.st_mode):
                    continue
                if st.st_size > MAX_NOTE_CHARS * 4:
                    read_errors += 1
                    continue
                raw = Path(fs_path(fpath)).read_text(encoding="utf-8", errors="replace")
                notes[rel] = raw
            except OSError as exc:
                if is_path_too_long(exc):
                    read_errors += 1
                    continue
                read_errors += 1

    report = analyze_lifecycle(notes, config)
    if read_errors > 0:
        has_incomplete = any(w["code"] == "lifecycle-incomplete" for w in report["warnings"])
        if not has_incomplete:
            report["warnings"].append(
                _make_warning(
                    "lifecycle-incomplete",
                    "",
                    0,
                    "Some vault notes could not be read; lifecycle audit is incomplete",
                    details={"read_errors": read_errors},
                )
            )
            report["counts"]["warnings"] = len(report["warnings"])
            report["ok"] = False
            report["status"] = "warnings"
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path, help="Vault root containing 00-meta/vault-config.json.")
    subs = parser.add_subparsers(dest="command", required=True)
    p_check = subs.add_parser("check", help="Run read-only withdrawal/supersession lifecycle audit.")
    p_check.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report.")
    args = parser.parse_args(argv)
    try:
        report = check_vault(args.vault)
    except LifecycleError as exc:
        err = {"schema": SCHEMA, "authority": AUTHORITY, "ok": False, "status": "error", "error": str(exc)}
        print(json.dumps(err, ensure_ascii=True, sort_keys=True))
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        c = report["counts"]
        print(
            f"[vault-lifecycle] scanned={c['notes_scanned']} withdrawn={c['withdrawn_notes']} "
            f"superseded={c['superseded_notes']} warnings={c['warnings']} status={report['status']}"
        )
        for w in report["warnings"]:
            loc = f"{w['path']}:{w['line']}" if w["path"] else "<vault>"
            print(f"  - [{w['code']}] {loc} {w['message']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
