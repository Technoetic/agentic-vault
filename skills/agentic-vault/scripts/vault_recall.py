#!/usr/bin/env python3
"""Bounded, deterministic lexical recall for an agentic-vault Markdown vault."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Sequence

from vault_healthcheck import HealthcheckError, estimate_tokens, validate_config
from vault_paths import resolve_note_path


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


CONFIG_RELPATH = "00-meta/vault-config.json"
MAX_CONFIG_BYTES = 256 * 1024
MAX_DIRECTORY_ENTRIES = 20_000
MAX_FILES = 2_000
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_READ_BYTES = 16 * 1024 * 1024
MAX_QUERY_CHARS = 1_000
MAX_QUERY_TERMS = 32
MAX_RESULTS = 50
MAX_SNIPPET_CHARS = 500

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_TITLE_RE = re.compile(r"^\s*title\s*:\s*(.*?)\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")


def _diagnostics() -> dict:
    return {
        "status": "ok",
        "search_complete": True,
        "entries_scanned": 0,
        "files_considered": 0,
        "files_read": 0,
        "bytes_read": 0,
        "skipped_denied": 0,
        "skipped_excluded": 0,
        "skipped_unsafe": 0,
        "skipped_oversized": 0,
        "skipped_unreadable": 0,
        "omitted_entry_limit": 0,
        "omitted_file_limit": 0,
        "omitted_total_byte_limit": 0,
        "context_matches": 0,
        "context_truncated": 0,
        "context_omitted": 0,
        "query_terms_used": 0,
        "query_truncated": 0,
        "omissions": [],
        "limits": {
            "directory_entries": MAX_DIRECTORY_ENTRIES,
            "files": MAX_FILES,
            "file_bytes": MAX_FILE_BYTES,
            "total_read_bytes": MAX_TOTAL_READ_BYTES,
        },
    }


def _empty_result(diagnostics: dict) -> dict:
    return {"context": "", "matches": [], "diagnostics": diagnostics}


def _omit(diagnostics: dict, reason: str) -> None:
    diagnostics["search_complete"] = False
    if reason not in diagnostics["omissions"]:
        diagnostics["omissions"].append(reason)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _terms(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_WORD_RE.findall(_normalize(text))))


def _path_matches_rule(parts: Sequence[str], rule: str) -> bool:
    rule_parts = tuple(part.casefold() for part in rule.replace("\\", "/").split("/") if part)
    folded = tuple(part.casefold() for part in parts)
    if not rule_parts:
        return False
    if len(rule_parts) == 1:
        return rule_parts[0] in folded
    return folded[:len(rule_parts)] == rule_parts


def _classified_skip(rel_parts: Sequence[str], deny_zones: Sequence[str], exclude_dirs: Sequence[str]) -> str | None:
    if any(_path_matches_rule(rel_parts, rule) for rule in deny_zones):
        return "denied"
    if any(_path_matches_rule(rel_parts, rule) for rule in exclude_dirs):
        return "excluded"
    return None


def _read_config(vault: Path, diagnostics: dict) -> dict | None:
    try:
        config_path = resolve_note_path(vault, CONFIG_RELPATH)
        metadata = config_path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("config is not a regular file")
        size = metadata.st_size
        if size > MAX_CONFIG_BYTES:
            diagnostics["status"] = "invalid_config"
            _omit(diagnostics, "config_byte_limit")
            return None
        raw_bytes, stable = _read_regular_bytes(config_path, MAX_CONFIG_BYTES)
        if not stable:
            raise OSError("config changed while being read")
        if len(raw_bytes) > MAX_CONFIG_BYTES:
            diagnostics["status"] = "invalid_config"
            _omit(diagnostics, "config_byte_limit")
            return None
        raw = json.loads(raw_bytes.decode("utf-8-sig"))
        return validate_config(raw)
    except FileNotFoundError:
        diagnostics["status"] = "not_vault"
        _omit(diagnostics, "config_unavailable")
    except (
        HealthcheckError, json.JSONDecodeError, UnicodeError, OSError,
        ValueError, RecursionError, OverflowError,
    ):
        diagnostics["status"] = "invalid_config"
        _omit(diagnostics, "config_invalid")
    return None


def _read_regular_bytes(path: Path, byte_limit: int) -> tuple[bytes, bool]:
    """Read a stable regular file without ever allocating beyond ``byte_limit``."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("path is not a regular file")
        if before.st_size > byte_limit:
            raise OverflowError("file exceeds byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(before.st_size)
        after = os.fstat(descriptor)
        stable = len(content) == before.st_size and after.st_size == before.st_size
        return content, stable
    finally:
        os.close(descriptor)


def _markdown_paths(
    vault: Path,
    deny_zones: Sequence[str],
    exclude_dirs: Sequence[str],
    diagnostics: dict,
) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    pending: list[tuple[tuple[str, ...], Path]] = [((), vault)]
    hit_entry_limit = False

    while pending and not hit_entry_limit:
        rel_parts, directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = []
                for entry in iterator:
                    if diagnostics["entries_scanned"] >= MAX_DIRECTORY_ENTRIES:
                        diagnostics["omitted_entry_limit"] += 1
                        _omit(diagnostics, "directory_entry_limit")
                        hit_entry_limit = True
                        break
                    diagnostics["entries_scanned"] += 1
                    entries.append(entry)
        except OSError:
            diagnostics["skipped_unreadable"] += 1
            _omit(diagnostics, "unreadable_path")
            continue

        child_dirs: list[tuple[tuple[str, ...], Path]] = []
        for entry in sorted(entries, key=lambda item: (item.name.casefold(), item.name)):
            child_parts = (*rel_parts, entry.name)
            classification = _classified_skip(child_parts, deny_zones, exclude_dirs)
            if classification == "denied":
                diagnostics["skipped_denied"] += 1
                continue
            if classification == "excluded":
                diagnostics["skipped_excluded"] += 1
                continue

            rel_path = "/".join(child_parts)
            try:
                safe_path = resolve_note_path(vault, rel_path, deny_zones)
                is_directory = entry.is_dir(follow_symlinks=False)
            except (OSError, ValueError):
                diagnostics["skipped_unsafe"] += 1
                _omit(diagnostics, "unsafe_path")
                continue

            if is_directory:
                child_dirs.append((child_parts, safe_path))
            elif entry.name.casefold().endswith(".md"):
                found.append((rel_path.replace("\\", "/"), safe_path))

        # LIFO traversal needs reverse insertion to retain lexical directory order.
        pending.extend(reversed(child_dirs))

    return sorted(found, key=lambda item: (item[0].casefold(), item[0]))


def _read_markdown(path: Path, diagnostics: dict) -> str | None:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            diagnostics["skipped_unsafe"] += 1
            _omit(diagnostics, "unsafe_path")
            return None
        size = metadata.st_size
        if size > MAX_FILE_BYTES:
            diagnostics["skipped_oversized"] += 1
            _omit(diagnostics, "file_byte_limit")
            return None
        if diagnostics["bytes_read"] + size > MAX_TOTAL_READ_BYTES:
            diagnostics["omitted_total_byte_limit"] += 1
            _omit(diagnostics, "total_byte_limit")
            return None
        remaining = MAX_TOTAL_READ_BYTES - diagnostics["bytes_read"]
        content, stable = _read_regular_bytes(path, min(MAX_FILE_BYTES, remaining))
        diagnostics["bytes_read"] += len(content)
        if not stable:
            diagnostics["skipped_unreadable"] += 1
            _omit(diagnostics, "file_changed")
            return None
        diagnostics["files_read"] += 1
        return content.decode("utf-8", errors="replace")
    except OverflowError:
        diagnostics["omitted_total_byte_limit"] += 1
        _omit(diagnostics, "total_byte_limit")
        return None
    except ValueError:
        diagnostics["skipped_unsafe"] += 1
        _omit(diagnostics, "unsafe_path")
        return None
    except OSError:
        diagnostics["skipped_unreadable"] += 1
        _omit(diagnostics, "unreadable_file")
        return None


def _title(lines: list[str], fallback: str) -> tuple[str, int, str, bool]:
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:65], start=2):
            if line.strip() == "---":
                break
            match = _TITLE_RE.match(line)
            if match:
                value = match.group(1).strip().strip('"\'')
                return value, index, line.strip(), True
    for index, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match:
            return match.group(1), index, line.strip(), True
    return fallback, 0, "", False


def _line_quality(normalized_line: str, phrase: str, query_terms: Sequence[str]) -> tuple[int, int, int]:
    phrase_hit = int(bool(phrase and phrase in normalized_line))
    term_hits = sum(term in normalized_line for term in query_terms)
    occurrences = sum(min(normalized_line.count(term), 3) for term in query_terms)
    return phrase_hit, term_hits, occurrences


def _snippet(line: str, phrase: str, query_terms: Sequence[str]) -> str:
    clean = line.strip()
    if len(clean) <= MAX_SNIPPET_CHARS:
        return clean
    offset_text = _normalize(clean)
    positions = [offset_text.find(value) for value in (phrase, *query_terms)]
    positions = [position for position in positions if position >= 0]
    normalized_position = min(positions) if positions else 0
    # Locate the source prefix that reaches the normalized match. Normalizing
    # whole prefixes accounts for uneven expansions, composition and whitespace;
    # a global source/normalized length ratio cannot locate a particular match.
    low, high = 0, len(clean)
    while low < high:
        middle = (low + high) // 2
        if len(_normalize(clean[:middle])) <= normalized_position:
            low = middle + 1
        else:
            high = middle
    center = max(0, low - 1)
    start = max(0, center - MAX_SNIPPET_CHARS // 4)
    end = min(len(clean), start + MAX_SNIPPET_CHARS)
    excerpt = clean[start:end]
    return ("..." if start else "") + excerpt + ("..." if end < len(clean) else "")


def _rank_document(rel_path: str, text: str, phrase: str, query_terms: Sequence[str]) -> dict | None:
    lines = text.splitlines()
    title, title_line, title_source, title_has_source = _title(lines, Path(rel_path).stem)
    normalized_title = _normalize(title) if title_has_source else ""
    normalized_body = _normalize(text)
    title_quality = _line_quality(normalized_title, phrase, query_terms)
    body_quality = _line_quality(normalized_body, phrase, query_terms)
    if body_quality[1] == 0 and title_quality[1] == 0:
        return None

    score = (
        title_quality[0] * 40
        + body_quality[0] * 20
        + title_quality[1] * 10
        + body_quality[1] * 4
        + min(body_quality[2], len(query_terms) * 3)
    )

    best_line = title_line
    best_source = title_source
    best_quality = _line_quality(_normalize(title_source), phrase, query_terms)
    for line_number, line in enumerate(lines, start=1):
        quality = _line_quality(_normalize(line), phrase, query_terms)
        if quality > best_quality:
            best_line = line_number
            best_source = line
            best_quality = quality

    return {
        "path": rel_path,
        "line": best_line,
        "title": title,
        "score": score,
        "snippet": _snippet(best_source, phrase, query_terms),
    }


def _fit_block(label: str, snippet: str, prefix: str, max_tokens: int) -> tuple[str | None, bool]:
    full = label + "\n" + snippet
    candidate = prefix + full
    if estimate_tokens(candidate) <= max_tokens:
        return full, False
    if not snippet or estimate_tokens(prefix + label + "\n" + snippet[:1] + "...") > max_tokens:
        return None, False

    low, high = 1, len(snippet)
    best = snippet[:1] + "..."
    while low <= high:
        middle = (low + high) // 2
        shortened = snippet[:middle].rstrip()
        marker = "..." if middle < len(snippet) else ""
        if estimate_tokens(prefix + label + "\n" + shortened + marker) <= max_tokens:
            best = shortened + marker
            low = middle + 1
        else:
            high = middle - 1
    return label + "\n" + best, True


def _render_context(matches: list[dict], max_tokens: int, diagnostics: dict) -> str:
    if max_tokens == 0:
        diagnostics["context_omitted"] = len(matches)
        return ""
    blocks: list[str] = []
    for match in matches:
        label = f"[Source: {match['path']}:{match['line']}]"
        prefix = "\n\n".join(blocks)
        if prefix:
            prefix += "\n\n"
        block, truncated = _fit_block(label, match["snippet"], prefix, max_tokens)
        if block is None:
            diagnostics["context_omitted"] += 1
            continue
        blocks.append(block)
        diagnostics["context_matches"] += 1
        if truncated:
            diagnostics["context_truncated"] += 1
    return "\n\n".join(blocks)


def recall(vault: Path, query: str, limit: int = 5, max_tokens: int = 1500) -> dict:
    """Return bounded lexical context, attributed source matches, and diagnostics."""
    diagnostics = _diagnostics()
    if not isinstance(query, str):
        diagnostics["status"] = "invalid_query"
        diagnostics["search_complete"] = False
        return _empty_result(diagnostics)
    if (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_RESULTS
        or not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0
    ):
        diagnostics["status"] = "invalid_options"
        diagnostics["search_complete"] = False
        return _empty_result(diagnostics)
    if not isinstance(vault, Path):
        diagnostics["status"] = "invalid_vault"
        diagnostics["search_complete"] = False
        return _empty_result(diagnostics)

    bounded_query = query[:MAX_QUERY_CHARS]
    if not _terms(bounded_query):
        diagnostics["status"] = "invalid_query"
        diagnostics["search_complete"] = False
        return _empty_result(diagnostics)
    if len(query) > MAX_QUERY_CHARS:
        diagnostics["query_truncated"] = 1
    query_terms = _terms(bounded_query)[:MAX_QUERY_TERMS]
    diagnostics["query_terms_used"] = len(query_terms)
    phrase = _normalize(bounded_query)

    try:
        if not vault.is_dir():
            raise OSError
        # Validate the caller-supplied root itself before normalizing it; otherwise
        # resolving first would conceal a symlink or Windows junction vault root.
        resolve_note_path(vault, CONFIG_RELPATH)
        safe_vault = vault.resolve(strict=True)
    except (OSError, ValueError):
        diagnostics["status"] = "invalid_vault"
        diagnostics["search_complete"] = False
        return _empty_result(diagnostics)

    config = _read_config(safe_vault, diagnostics)
    if config is None:
        return _empty_result(diagnostics)
    deny_zones = tuple(config.get("deny_zones") or ())
    exclude_dirs = tuple(config.get("exclude_dirs") or ())
    candidates = _markdown_paths(safe_vault, deny_zones, exclude_dirs, diagnostics)
    if len(candidates) > MAX_FILES:
        diagnostics["omitted_file_limit"] = len(candidates) - MAX_FILES
        _omit(diagnostics, "file_count_limit")
        candidates = candidates[:MAX_FILES]

    ranked: list[dict] = []
    for index, (rel_path, path) in enumerate(candidates):
        diagnostics["files_considered"] += 1
        try:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                diagnostics["skipped_unsafe"] += 1
                _omit(diagnostics, "unsafe_path")
                continue
            size = metadata.st_size
        except OSError:
            diagnostics["skipped_unreadable"] += 1
            _omit(diagnostics, "unreadable_file")
            continue
        if diagnostics["bytes_read"] + min(size, MAX_FILE_BYTES + 1) > MAX_TOTAL_READ_BYTES:
            diagnostics["omitted_total_byte_limit"] += len(candidates) - index
            _omit(diagnostics, "total_byte_limit")
            break
        text = _read_markdown(path, diagnostics)
        if text is None:
            continue
        match = _rank_document(rel_path, text, phrase, query_terms)
        if match is not None:
            ranked.append(match)

    ranked.sort(key=lambda match: (-match["score"], match["path"].casefold(), match["path"]))
    matches = ranked[:limit]
    context = _render_context(matches, max_tokens, diagnostics)
    return {"context": context, "matches": matches, "diagnostics": diagnostics}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=1500)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = recall(args.vault, args.query, args.limit, args.max_tokens)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["context"]:
            print(result["context"])
        diagnostics = result["diagnostics"]
        if diagnostics["status"] != "ok":
            print(f"recall unavailable: {diagnostics['status']}", file=sys.stderr)
        elif not diagnostics["search_complete"]:
            reasons = ", ".join(diagnostics["omissions"])
            print(f"recall incomplete: omitted {reasons}", file=sys.stderr)
    return 0 if result["diagnostics"]["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
