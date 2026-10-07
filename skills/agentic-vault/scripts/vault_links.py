#!/usr/bin/env python3
"""Suggest filename wiki links as read-only, hash-bound lexical review data.

No file is written and no retrieved instruction is executed. --max-tokens bounds
the estimated tokens of ``context`` (the JSON review payload, including its diff),
not the surrounding JSON diagnostics. Filename uniqueness is checked only among
eligible notes: hidden deny/exclude-zone collisions still require host review.
Bindings are point-in-time observations, not a filesystem freeze or approval.
Source notes with bare-CR line endings are rejected rather than normalized.
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import sys
import unicodedata
from typing import Sequence

from vault_evidence import EvidenceError, regular, stable_read, stamp
from vault_healthcheck import HealthcheckError, WIKILINK_RE, estimate_tokens, validate_config
from vault_paths import resolve_note_path
import vault_recall as recall_module
from vault_recall import recall


MAX_PROPOSALS = 10
MAX_BINDING_BYTES = 16 * 1024 * 1024
LIMITATION = "global_uniqueness_unverified: denied/excluded notes may have colliding filenames; host review is required"


def _result(max_tokens: int) -> dict:
    return {
        "type": "lexical_link_proposal", "authority": "data_only",
        "requires_approval": True, "resolution_scope": "eligible_notes",
        "limitations": [LIMITATION], "source": None, "proposals": [],
        "diff": "", "context": "",
        "diagnostics": {
            "status": "ok", "search_complete": True, "omissions": [],
            "skipped_source": 0, "skipped_existing": 0, "skipped_ambiguous": 0,
            "skipped_link_syntax": 0, "skipped_candidate": 0,
            "candidates_examined": 0, "candidates_unreviewed": 0,
            "binding_bytes_read": 0, "context_omitted": 0,
            "context_truncated": 0, "context_estimated_tokens": 0,
            "max_tokens": max_tokens, "token_budget_scope": "context",
            "token_estimator": "vault_healthcheck.estimate_tokens",
            "limits": {"proposals": MAX_PROPOSALS, "candidates": recall_module.MAX_RESULTS,
                       "file_bytes": recall_module.MAX_FILE_BYTES,
                       "binding_bytes": MAX_BINDING_BYTES},
        },
    }


def _omit(result: dict, reason: str) -> None:
    diagnostics = result["diagnostics"]
    diagnostics["search_complete"] = False
    if reason not in diagnostics["omissions"]:
        diagnostics["omissions"].append(reason)


def _fail(result: dict, reason: str) -> dict:
    _omit(result, reason)
    result["diagnostics"]["status"] = reason
    result.update(source=None, proposals=[], diff="", context="")
    result["diagnostics"]["context_estimated_tokens"] = 0
    return result


class _Policy:
    """Re-use the approved stable reader with both deny and exclude rules."""

    def __init__(self, vault: Path, result: dict):
        self.vault = vault
        self.result = result
        self.config_info, self.config_mark, data = self._config()
        self.config = validate_config(json.loads(data.decode("utf-8-sig")))
        self.root = vault.resolve(strict=True)
        self.denied = tuple(self.config.get("deny_zones") or ())
        self.excluded = tuple(self.config.get("exclude_dirs") or ())
        self.rules = (*self.denied, *self.excluded, ".git")

    def _config(self):
        return stable_read(
            lambda: resolve_note_path(self.vault, recall_module.CONFIG_RELPATH, (".git",)),
            recall_module.MAX_CONFIG_BYTES, contents=True,
        )

    def fresh(self):
        info, _mark, _data = self._config()
        if info != self.config_info:
            raise EvidenceError("configuration_changed")

    def path(self, relative: str) -> Path:
        if (not isinstance(relative, str)
                or any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
                       for char in relative)):
            raise ValueError("note path cannot contain control or line separator characters")
        path = resolve_note_path(self.vault, relative, self.rules)
        if path.suffix.casefold() != ".md":
            raise ValueError("source and targets must be Markdown notes")
        return path

    def read(self, relative: str, *, contents: bool = True):
        path = self.path(relative)
        canonical = path.relative_to(self.root).as_posix()
        remaining = MAX_BINDING_BYTES - self.result["diagnostics"]["binding_bytes_read"]
        if remaining <= 0:
            raise EvidenceError("binding_byte_limit")
        info, mark, data = stable_read(
            lambda: self.path(canonical), min(recall_module.MAX_FILE_BYTES, remaining),
            contents=contents,
        )
        self.result["diagnostics"]["binding_bytes_read"] += info["size"]
        return {"path": canonical, **info}, mark, data

    def inventory(self):
        diagnostics = recall_module._diagnostics()
        paths = recall_module._markdown_paths(self.root, self.denied,
                                             (*self.excluded, ".git"), diagnostics)
        return paths, diagnostics

    def check_mark(self, relative: str, expected) -> None:
        metadata = self.path(relative).lstat()
        regular(metadata)
        if stamp(metadata) != expected:
            raise EvidenceError("file_changed_during_read")


def _safe_link_name(stem: str) -> bool:
    return bool(stem and stem.strip() == stem
                and not any(char in "[]#|^/\\`" or unicodedata.category(char).startswith("C")
                            or char in "\u2028\u2029" for char in stem))


def _existing_links(text: str) -> set[str]:
    # Aliases and headings are decorations on an actual filename/path target.
    # Frontmatter aliases themselves deliberately do not resolve links.
    found = set()
    for match in WIKILINK_RE.finditer(text):
        name = match.group(1).strip().replace("\\", "/").rsplit("/", 1)[-1]
        if name.casefold().endswith(".md"):
            name = name[:-3]
        found.add(name.casefold())
    return found


def _lf_lines(text: str) -> list[str]:
    # Unified patch lines end at LF. Unicode separators and vertical tabs are
    # ordinary body bytes; str.splitlines() would corrupt their patch context.
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _diff(source_path: str, original: str, proposals: list[dict]) -> str:
    if not proposals:
        return ""
    newline = "\r\n" if "\r\n" in original else "\n"
    prefix = "" if not original else (newline if original.endswith(("\n", "\r")) else newline * 2)
    addition = prefix + "## Related notes" + newline
    addition += "".join("- " + item["link"] + newline for item in proposals)
    candidate = original + addition
    lines = difflib.unified_diff(_lf_lines(original), _lf_lines(candidate),
                                 fromfile="a/" + source_path, tofile="b/" + source_path,
                                 n=3, lineterm="\n")
    output = []
    for line in lines:
        output.append(line)
        if not line.endswith("\n"):
            output.append("\n\\ No newline at end of file\n")
    return "".join(output)


def _context(result: dict, original: str, proposals: list[dict]) -> tuple[str, str]:
    diff = _diff(result["source"]["path"], original, proposals)
    payload = {key: result[key] for key in ("type", "authority", "requires_approval", "resolution_scope")}
    payload.update(source=result["source"], proposals=proposals, diff=diff,
                   limitations=result["limitations"])
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), diff


def _fit(result: dict, original: str, proposal: dict, max_tokens: int):
    if max_tokens == 0:
        return None
    previous = result["proposals"]
    context, diff = _context(result, original, [*previous, proposal])
    if estimate_tokens(context) <= max_tokens:
        return proposal, context, diff, False
    snippet = proposal["snippet"]
    shortened = {**proposal, "snippet": ""}
    minimal, _ = _context(result, original, [*previous, shortened])
    if estimate_tokens(minimal) > max_tokens:
        return None
    best = shortened
    best_context, best_diff = _context(result, original, [*previous, shortened])
    low, high = 1, len(snippet)
    while low <= high:
        middle = (low + high) // 2
        shortened = {**proposal, "snippet": snippet[:middle].rstrip() + ("..." if middle < len(snippet) else "")}
        context, diff = _context(result, original, [*previous, shortened])
        if estimate_tokens(context) <= max_tokens:
            best, best_context, best_diff = shortened, context, diff
            low = middle + 1
        else:
            high = middle - 1
    return best, best_context, best_diff, True


def propose_links(vault: Path, source: str, query: str, limit: int = 5,
                  max_tokens: int = 1500) -> dict:
    """Return explicit-query lexical suggestions and a proposed append-only diff."""
    result = _result(max_tokens)
    diagnostics = result["diagnostics"]
    if (type(limit) is not int or not 1 <= limit <= MAX_PROPOSALS
            or type(max_tokens) is not int or max_tokens < 0):
        return _fail(result, "invalid_options")
    if not isinstance(query, str) or not recall_module._terms(query[:recall_module.MAX_QUERY_CHARS]):
        return _fail(result, "invalid_query")
    if not isinstance(vault, Path) or not vault.is_dir():
        return _fail(result, "invalid_vault")
    try:
        policy = _Policy(vault, result)
    except (OSError, ValueError, EvidenceError, HealthcheckError, RecursionError, OverflowError):
        return _fail(result, "invalid_config_or_vault")
    try:
        source_info, _source_mark, source_data = policy.read(source)
        original = source_data.decode("utf-8")  # Preserve BOM and line endings exactly.
    except UnicodeError:
        return _fail(result, "invalid_utf8")
    except EvidenceError as exc:
        return _fail(result, "input_too_large" if str(exc) == "input_too_large" else "unsafe_source")
    except (OSError, ValueError):
        return _fail(result, "unsafe_source")
    if "\r" in original.replace("\r\n", ""):
        return _fail(result, "unsupported_line_endings")
    result["source"] = source_info
    inventory, inventory_diagnostics = policy.inventory()
    diagnostics["inventory"] = inventory_diagnostics
    for reason in inventory_diagnostics["omissions"]:
        _omit(result, reason)
    if not inventory_diagnostics["search_complete"]:
        return _fail(result, "incomplete_inventory")
    stems = {}
    for relative, _path in inventory:
        stems.setdefault(Path(relative).stem.casefold(), []).append(relative)
    existing = _existing_links(original)
    recalled = recall(vault, query, limit=recall_module.MAX_RESULTS, max_tokens=0)
    diagnostics["recall"] = recalled["diagnostics"]
    for reason in recalled["diagnostics"]["omissions"]:
        _omit(result, reason)
    if recalled["diagnostics"]["status"] != "ok":
        return _fail(result, recalled["diagnostics"]["status"])
    bounded_query = query[:recall_module.MAX_QUERY_CHARS]
    query_terms = recall_module._terms(bounded_query)[:recall_module.MAX_QUERY_TERMS]
    phrase = recall_module._normalize(bounded_query)
    matches = recalled["matches"]
    if len(matches) == recall_module.MAX_RESULTS:
        _omit(result, "candidate_limit_reached")
    for match in matches:
        if len(result["proposals"]) >= limit:
            break
        diagnostics["candidates_examined"] += 1
        relative = match["path"]
        stem = Path(relative).stem
        if relative.casefold() == source_info["path"].casefold():
            diagnostics["skipped_source"] += 1
            continue
        if stem.casefold() in existing:
            diagnostics["skipped_existing"] += 1
            continue
        if len(stems.get(stem.casefold(), ())) != 1:
            diagnostics["skipped_ambiguous"] += 1
            continue
        if not _safe_link_name(stem):
            diagnostics["skipped_link_syntax"] += 1
            continue
        # Reserve sufficient bounded reads to recheck the source and every
        # accepted target, even if many excluded candidates consume read bytes.
        reserve = source_info["size"] + (len(result["proposals"]) + 2) * recall_module.MAX_FILE_BYTES
        if diagnostics["binding_bytes_read"] + reserve > MAX_BINDING_BYTES:
            _omit(result, "binding_byte_limit")
            break
        try:
            binding, _mark, data = policy.read(relative)
            text = data.decode("utf-8-sig")
            current = recall_module._rank_document(binding["path"], text, phrase, query_terms)
            if current is None:
                _omit(result, "candidate_no_longer_matches")
                diagnostics["skipped_candidate"] += 1
                continue
        except UnicodeError:
            _omit(result, "invalid_utf8_candidate")
            diagnostics["skipped_candidate"] += 1
            continue
        except (OSError, ValueError, EvidenceError):
            _omit(result, "unreadable_or_unsafe_candidate")
            diagnostics["skipped_candidate"] += 1
            continue
        normalized = recall_module._normalize(text)
        proposal = {"path": binding["path"], "line": current["line"],
                    "snippet": current["snippet"], "score": current["score"],
                    "link": "[[" + stem + "]]", "binding": binding,
                    "reason": {"kind": "lexical_overlap",
                               "terms": [term for term in query_terms if term in normalized]}}
        fitted = _fit(result, original, proposal, max_tokens)
        if fitted is None:
            diagnostics["context_omitted"] += 1
            continue
        proposal, context, diff, truncated = fitted
        result["proposals"].append(proposal)
        result.update(context=context, diff=diff)
        diagnostics["context_truncated"] += int(truncated)
    diagnostics["candidates_unreviewed"] = len(matches) - diagnostics["candidates_examined"]
    try:
        policy.fresh()
    except (OSError, ValueError, EvidenceError):
        return _fail(result, "configuration_changed")
    retained, target_marks = [], {}
    for proposal in result["proposals"]:
        try:
            current, mark, _data = policy.read(proposal["path"], contents=False)
            if current != proposal["binding"]:
                raise EvidenceError("candidate_changed")
        except (OSError, ValueError, EvidenceError):
            diagnostics["skipped_candidate"] += 1
            _omit(result, "candidate_changed_before_publication")
            continue
        retained.append(proposal)
        target_marks[proposal["path"]] = mark
    final_inventory, final_diagnostics = policy.inventory()
    for reason in final_diagnostics["omissions"]:
        _omit(result, reason)
    if (not final_diagnostics["search_complete"]
            or [path for path, _ in final_inventory] != [path for path, _ in inventory]):
        return _fail(result, "inventory_changed")
    try:
        policy.fresh()
    except (OSError, ValueError, EvidenceError):
        return _fail(result, "configuration_changed")
    try:
        current_source, source_mark, _data = policy.read(source_info["path"], contents=False)
        if current_source != source_info:
            return _fail(result, "source_changed")
    except (OSError, ValueError, EvidenceError):
        return _fail(result, "source_changed")
    # A later target read or source recheck must not leave an earlier binding
    # silently stale. The shared reader's portable marks detect such changes.
    current_targets = []
    for proposal in retained:
        try:
            policy.check_mark(proposal["path"], target_marks[proposal["path"]])
        except (OSError, ValueError, EvidenceError):
            diagnostics["skipped_candidate"] += 1
            _omit(result, "candidate_changed_before_publication")
            continue
        current_targets.append(proposal)
    retained = current_targets
    try:
        policy.check_mark(source_info["path"], source_mark)
    except (OSError, ValueError, EvidenceError):
        return _fail(result, "source_changed")
    try:
        policy.fresh()
    except (OSError, ValueError, EvidenceError):
        return _fail(result, "configuration_changed")
    result["proposals"] = retained
    if retained:
        result["context"], result["diff"] = _context(result, original, retained)
    else:
        result.update(context="", diff="")
    diagnostics["context_estimated_tokens"] = estimate_tokens(result["context"])
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--source", required=True, help="vault-relative Markdown source note")
    parser.add_argument("--query", required=True, help="explicit lexical topic; no text is sent to an API")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=1500, help="estimated JSON review-context budget")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    result = propose_links(args.vault, args.source, args.query, args.limit, args.max_tokens)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    elif result["context"]:
        print(result["context"])
    diagnostics = result["diagnostics"]
    if args.format == "text" and diagnostics["status"] != "ok":
        print("link proposals unavailable: " + diagnostics["status"], file=sys.stderr)
    elif args.format == "text" and not diagnostics["search_complete"]:
        print("link proposals incomplete: " + ", ".join(diagnostics["omissions"]), file=sys.stderr)
    return 0 if diagnostics["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
