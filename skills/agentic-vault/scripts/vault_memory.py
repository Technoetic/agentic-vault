#!/usr/bin/env python3
"""Compile explicit host-authored memory units into source-bound review data.

Quotes prove provenance only, never claim truth or semantic equivalence. This
tool performs no extraction, entity/date inference, instruction execution or
note writes. Exact deduplication preserves every distinct source proof and all
time, condition and uncertainty fields. Alternative values are review signals,
not established contradictions. Bindings are point-in-time observations.

Input is an explicit vault-relative JSON array (64 KiB maximum). Each item has
subject/predicate/value strings, time with at least one absolute valid_from,
valid_until or observed_at, conditions (a string array), uncertainty (a string),
and sources [{path,line,quote,expected_sha256?}]. Lines are 1-based and split
only at LF; quote text must match entire original lines, including any CR in
CRLF. Unicode line separators remain literal content. Date-only values compare
at midnight UTC; datetimes require a timezone. Validity is [from,until), while
an observed_at without validity bounds is an instant. No text is transmitted.

--max-tokens bounds the estimated tokens of context, including selected units,
literal quotes, file bindings and their potential conflicts. Units are omitted
whole, never shortened. Other JSON review diagnostics are outside that budget.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Sequence

from vault_evidence import EvidenceError, decode, regular, stable_read, stamp
from vault_healthcheck import HealthcheckError, estimate_tokens, validate_config
from vault_paths import resolve_note_path


CONFIG_PATH = "00-meta/vault-config.json"
MAX_CONFIG_BYTES = 256 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_UNITS = 128
MAX_SOURCES_PER_UNIT = 8
MAX_SOURCE_FILES = 64
MAX_FILE_BYTES = 512 * 1024
MAX_BINDING_BYTES = 16 * 1024 * 1024
MAX_QUOTE_CHARS = 8192
MAX_QUERY_CHARS = 512
SEMANTIC_FIELDS = ("subject", "predicate", "value", "time", "conditions", "uncertainty")
UNIT_FIELDS = frozenset((*SEMANTIC_FIELDS, "sources"))
SOURCE_FIELDS = frozenset(("path", "line", "quote", "expected_sha256"))
TIME_FIELDS = frozenset(("valid_from", "valid_until", "observed_at"))
HASH_RE = re.compile(r"[0-9a-f]{64}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)")
PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}|<[^>]*>|\[(?:entity|subject|name|person|organization|unknown)\]", re.I)
UNRESOLVED = frozenset(("it", "he", "she", "they", "them", "this", "that", "these", "those",
                        "i", "you", "we", "us", "someone", "something", "unknown", "tbd", "todo",
                        "그", "그것", "그들", "이것", "저것", "우리", "나", "미상", "알 수 없음"))
RELATIVE_SUBJECT_RE = re.compile(r"(?i)^(?:he|she|they|his|her|their|its|this|that|these|those|our|your)\b|^the (?:company|organization|person|supplier|entity)$")


def _json(value) -> str:
    # ASCII escaping keeps controls and Unicode separators inert in CLI output.
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _result(max_tokens) -> dict:
    return {
        "type": "structured_memory_review", "authority": "data_only",
        "requires_approval": True, "context": "", "units": [], "potential_conflicts": [],
        "limitations": ["provenance_only", "host_authored_semantics_unverified",
                        "point_in_time_bindings", "potential_conflicts_require_review"],
        "diagnostics": {
            "status": "ok", "complete": True, "omissions": [], "rejected": [],
            "input_units": 0, "validated_units": 0, "duplicates_consolidated": 0,
            "query_filtered": 0, "context_omitted": 0, "binding_bytes_read": 0,
            "context_estimated_tokens": 0, "max_tokens": max_tokens,
            "token_budget_scope": "context", "token_estimator": "vault_healthcheck.estimate_tokens",
            "limits": {"json_bytes": MAX_JSON_BYTES, "units": MAX_UNITS,
                       "sources_per_unit": MAX_SOURCES_PER_UNIT, "source_files": MAX_SOURCE_FILES,
                       "file_bytes": MAX_FILE_BYTES, "binding_bytes": MAX_BINDING_BYTES,
                       "quote_chars": MAX_QUOTE_CHARS, "query_chars": MAX_QUERY_CHARS},
        },
    }


def _omit(result, reason):
    diagnostics = result["diagnostics"]
    diagnostics["complete"] = False
    if reason not in diagnostics["omissions"]:
        diagnostics["omissions"].append(reason)


def _fail(result, reason):
    _omit(result, reason)
    result.update(context="", units=[], potential_conflicts=[])
    result["diagnostics"].update(status=reason, context_estimated_tokens=0)
    return result


def _text(value, limit, *, quote=False):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvidenceError("invalid_text")
    for char in value:
        if unicodedata.category(char).startswith("C") and not (quote and char in "\n\r\t"):
            raise EvidenceError("invalid_text")
        if not quote and char in "\u2028\u2029":
            raise EvidenceError("invalid_text")


def _absolute(value):
    if not isinstance(value, str) or len(value) > 64:
        raise EvidenceError("invalid_time")
    try:
        if DATE_RE.fullmatch(value):
            parsed = date.fromisoformat(value)
            return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
        if DATETIME_RE.fullmatch(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise EvidenceError("invalid_time")


def _time(value):
    if not isinstance(value, dict) or not value or not set(value) <= TIME_FIELDS:
        raise EvidenceError("invalid_time")
    parsed = {key: _absolute(item) for key, item in value.items()}
    lower, upper = parsed.get("valid_from"), parsed.get("valid_until")
    if lower is not None and upper is not None and lower >= upper:
        raise EvidenceError("invalid_time")
    return dict(value)


def _terms(value):
    return tuple(dict.fromkeys(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold())))


def _bounded_input(units):
    if not isinstance(units, list) or len(units) > MAX_UNITS:
        raise EvidenceError("invalid_input")
    # Check primitive sizes/depth before serialization so malformed host input
    # cannot create unbounded temporary JSON strings or recurse indefinitely.
    pending = [(units, 0)]
    nodes, chars = 0, 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > 8192 or depth > 8:
            raise EvidenceError("invalid_input")
        if isinstance(value, str):
            chars += len(value)
            if chars > MAX_JSON_BYTES:
                raise EvidenceError("invalid_input")
        elif isinstance(value, dict):
            if len(value) > 32:
                raise EvidenceError("invalid_input")
            pending.extend((key, depth + 1) for key in value)
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            if len(value) > MAX_UNITS:
                raise EvidenceError("invalid_input")
            pending.extend((item, depth + 1) for item in value)
        elif value is not None and type(value) not in (bool, int, float):
            raise EvidenceError("invalid_input")
    try:
        encoded = json.dumps(units, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise EvidenceError("invalid_input") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise EvidenceError("invalid_input")


class _Policy:
    """Current deny+exclude+.git policy with shared stable/hash reads."""

    def __init__(self, vault, result):
        self.vault, self.result = vault, result
        self.root = vault.resolve(strict=True)
        self.config_info, self.config_mark, data = self._config()
        config = validate_config(decode(data))
        self.rules = (*(config.get("deny_zones") or ()),
                      *(config.get("exclude_dirs") or ()), ".git")
        # The single bootstrap read discovers policy; learned rules must allow
        # the config itself before any source or subsequent config read.
        self.path(CONFIG_PATH, ".json")
        self.sources = {}
        self.source_attempts = set()
        self.initial_bytes = 0
        self.input_binding = None

    def _config(self):
        rules = getattr(self, "rules", (".git",))
        return stable_read(lambda: resolve_note_path(self.vault, CONFIG_PATH, rules),
                           MAX_CONFIG_BYTES, contents=True)

    def fresh(self):
        info, mark, _data = self._config()
        if info != self.config_info or mark != self.config_mark:
            raise EvidenceError("configuration_changed")

    def path(self, relative, suffix=".md"):
        try:
            _text(relative, 1024)
            path = resolve_note_path(self.vault, relative, self.rules)
            if path.suffix.casefold() != suffix:
                raise ValueError("wrong extension")
            return path
        except (ValueError, EvidenceError) as exc:
            raise EvidenceError("unsafe_source") from exc

    def source(self, relative):
        path = self.path(relative)
        canonical = path.relative_to(self.root).as_posix()
        if canonical in self.sources:
            return self.sources[canonical]
        if canonical not in self.source_attempts and len(self.source_attempts) >= MAX_SOURCE_FILES:
            raise EvidenceError("source_file_limit")
        # Malformed/unreadable notes consume the unique-file limit too.
        self.source_attempts.add(canonical)
        metadata = path.lstat()
        regular(metadata)
        if metadata.st_size > MAX_FILE_BYTES:
            raise EvidenceError("input_too_large")
        remaining = MAX_BINDING_BYTES // 2 - self.initial_bytes
        if remaining < metadata.st_size:
            raise EvidenceError("binding_byte_limit")
        info, mark, data = stable_read(lambda: self.path(canonical),
                                       min(MAX_FILE_BYTES, remaining), contents=True)
        self.initial_bytes += info["size"]
        self.result["diagnostics"]["binding_bytes_read"] += info["size"]
        try:
            original = data.decode("utf-8")
        except UnicodeError as exc:
            raise EvidenceError("invalid_utf8") from exc
        if "\r" in original.replace("\r\n", ""):
            raise EvidenceError("unsupported_line_endings")
        record = ({"path": canonical, **info}, mark, original)
        self.sources[canonical] = record
        return record

    def sweep(self, marks):
        for relative, expected in marks.items():
            metadata = self.path(relative).lstat()
            regular(metadata)
            if stamp(metadata) != expected:
                raise EvidenceError("source_changed")
        metadata = self.path(CONFIG_PATH, ".json").lstat()
        regular(metadata)
        if stamp(metadata) != self.config_mark:
            raise EvidenceError("configuration_changed")

    def recheck(self):
        self.fresh()
        marks = {}
        for relative, (expected, _mark, _text_value) in self.sources.items():
            remaining = MAX_BINDING_BYTES - self.result["diagnostics"]["binding_bytes_read"]
            info, mark, _data = stable_read(lambda: self.path(relative),
                                           min(MAX_FILE_BYTES, remaining), contents=False)
            self.result["diagnostics"]["binding_bytes_read"] += info["size"]
            if {"path": relative, **info} != expected:
                raise EvidenceError("source_changed")
            marks[relative] = mark
        input_mark = None
        if self.input_binding is not None:
            relative, expected = self.input_binding
            info, input_mark, _data = stable_read(lambda: self.path(relative, ".json"),
                                                  MAX_JSON_BYTES, contents=False)
            if info != expected:
                raise EvidenceError("input_changed")
        # A later hash read must not leave an earlier file silently stale.
        self.sweep(marks)
        self.fresh()
        self.sweep(marks)
        if input_mark is not None:
            metadata = self.path(self.input_binding[0], ".json").lstat()
            regular(metadata)
            if stamp(metadata) != input_mark:
                raise EvidenceError("input_changed")


def _proof(source, policy):
    if (not isinstance(source, dict) or not {"path", "line", "quote"} <= set(source)
            or not set(source) <= SOURCE_FIELDS or type(source.get("line")) is not int
            or not 1 <= source["line"] <= MAX_FILE_BYTES + 1):
        raise EvidenceError("invalid_source_schema")
    try:
        _text(source["quote"], MAX_QUOTE_CHARS, quote=True)
    except EvidenceError as exc:
        raise EvidenceError("invalid_source_schema") from exc
    expected = source.get("expected_sha256")
    if "expected_sha256" in source and (not isinstance(expected, str) or not HASH_RE.fullmatch(expected)):
        raise EvidenceError("invalid_source_schema")
    try:
        binding, _mark, original = policy.source(source["path"])
    except (ValueError, OSError) as exc:
        raise EvidenceError("unsafe_source") from exc
    except EvidenceError as exc:
        if str(exc) == "unsafe_file":
            raise EvidenceError("unsafe_source") from exc
        raise
    if expected is not None and binding["sha256"] != expected:
        raise EvidenceError("source_hash_mismatch")
    quote_lines = source["quote"].split("\n")
    original_lines = original.split("\n")
    # A trailing LF terminates the last line rather than manufacturing an
    # additional empty line for a proof beyond the original note.
    if original_lines and original_lines[-1] == "" and original.endswith("\n"):
        original_lines.pop()
    start = source["line"] - 1
    if original_lines[start:start + len(quote_lines)] != quote_lines:
        raise EvidenceError("quote_mismatch")
    return {**source, "path": binding["path"], "binding": binding}


def _unit(unit, policy):
    if (not isinstance(unit, dict) or set(unit) != UNIT_FIELDS
            or not isinstance(unit["conditions"], list) or len(unit["conditions"]) > 16
            or not isinstance(unit["sources"], list)
            or not 1 <= len(unit["sources"]) <= MAX_SOURCES_PER_UNIT):
        raise EvidenceError("invalid_unit_schema")
    for key, limit in (("subject", 256), ("predicate", 256), ("value", 2048), ("uncertainty", 1024)):
        _text(unit[key], limit)
    for condition in unit["conditions"]:
        _text(condition, 512)
    for key in ("subject", "value"):
        if unit[key].strip().casefold() in UNRESOLVED or PLACEHOLDER_RE.search(unit[key]):
            raise EvidenceError("unresolved_entity")
    if RELATIVE_SUBJECT_RE.search(unit["subject"].strip()):
        raise EvidenceError("unresolved_entity")
    semantic = {key: unit[key] for key in SEMANTIC_FIELDS}
    semantic["conditions"] = list(unit["conditions"])
    semantic["time"] = _time(unit["time"])
    sources = [_proof(source, policy) for source in unit["sources"]]
    identity = hashlib.sha256(_json(semantic).encode("utf-8")).hexdigest()
    return {"id": identity, **semantic, "sources": sources}


def _merge(units, diagnostics):
    merged = {}
    proofs = {}
    for unit in units:
        key = unit["id"]
        if key not in merged:
            merged[key] = {**unit, "sources": []}
            proofs[key] = set()
        else:
            diagnostics["duplicates_consolidated"] += 1
        for source in unit["sources"]:
            proof_key = _json(source)
            if proof_key not in proofs[key]:
                merged[key]["sources"].append(source)
                proofs[key].add(proof_key)
    return list(merged.values())


def _overlap(first, second):
    def interval(value):
        parsed = {key: _absolute(item) for key, item in value.items()}
        if "valid_from" not in parsed and "valid_until" not in parsed:
            return parsed["observed_at"], parsed["observed_at"], True
        return parsed.get("valid_from"), parsed.get("valid_until"), False
    low_a, high_a, point_a = interval(first)
    low_b, high_b, point_b = interval(second)
    if point_a and point_b:
        return low_a == low_b
    if point_a:
        return (low_b is None or low_b <= low_a) and (high_b is None or low_a < high_b)
    if point_b:
        return (low_a is None or low_a <= low_b) and (high_a is None or low_b < high_a)
    return ((high_a is None or low_b is None or low_b < high_a)
            and (high_b is None or low_a is None or low_a < high_b))


def _conflicts(units):
    output = []
    for index, unit in enumerate(units):
        for other in units[index + 1:]:
            if (unit["subject"] == other["subject"] and unit["predicate"] == other["predicate"]
                    and unit["conditions"] == other["conditions"] and unit["value"] != other["value"]
                    and _overlap(unit["time"], other["time"])):
                output.append({"kind": "potential_conflict", "requires_review": True,
                               "unit_ids": [unit["id"], other["id"]],
                               "reason": "alternative_values_with_overlapping_time_and_identical_conditions"})
    return output


def _review_conflicts(conflicts, units, *, relevant_only=False):
    selected_ids = {unit["id"] for unit in units}
    return [{**conflict, "omitted_unit_ids": [uid for uid in conflict["unit_ids"] if uid not in selected_ids]}
            for conflict in conflicts
            if not relevant_only or selected_ids.intersection(conflict["unit_ids"])]


def _context(result, units):
    payload = {key: result[key] for key in ("type", "authority", "requires_approval", "limitations")}
    payload.update(units=units, potential_conflicts=_review_conflicts(
        result["potential_conflicts"], units, relevant_only=True))
    return _json(payload)


def compile_units(vault: Path, units: list[dict], max_tokens: int = 1500, *, query=None) -> dict:
    """Validate, exactly consolidate and select explicit source-bound units."""
    return _compile(vault, units, max_tokens, query)


def _compile(vault, units, max_tokens, query, policy=None):
    result = _result(max_tokens)
    diagnostics = result["diagnostics"]
    if type(max_tokens) is not int or max_tokens < 0:
        return _fail(result, "invalid_options")
    query_terms = ()
    if query is not None:
        try:
            _text(query, MAX_QUERY_CHARS)
            query_terms = _terms(query)
            if not query_terms:
                raise EvidenceError("invalid_text")
        except EvidenceError:
            return _fail(result, "invalid_options")
    try:
        _bounded_input(units)
    except EvidenceError:
        return _fail(result, "invalid_input")
    diagnostics["input_units"] = len(units)
    if not isinstance(vault, Path) or not vault.is_dir():
        return _fail(result, "invalid_vault")
    try:
        if policy is None:
            policy = _Policy(vault, result)
        else:
            policy.result = result
    except (OSError, ValueError, EvidenceError, HealthcheckError, RecursionError, OverflowError):
        return _fail(result, "invalid_config_or_vault")
    accepted = []
    for index, unit in enumerate(units):
        try:
            accepted.append(_unit(unit, policy))
        except EvidenceError as exc:
            reason = str(exc)
            diagnostics["rejected"].append({"index": index, "reason": reason})
            _omit(result, reason)
    diagnostics["validated_units"] = len(accepted)
    merged = _merge(accepted, diagnostics)
    result["potential_conflicts"] = _conflicts(merged)
    if query_terms:
        ranked = []
        for unit in merged:
            text = " ".join((unit["subject"], unit["predicate"], unit["value"],
                             " ".join(unit["conditions"]),
                             " ".join(source["quote"] for source in unit["sources"])))
            terms = set(_terms(text))
            score = sum(term in terms for term in query_terms)
            if score:
                ranked.append((-score, estimate_tokens(_json(unit)), unit["id"], unit))
            else:
                diagnostics["query_filtered"] += 1
        merged = [item[3] for item in sorted(ranked, key=lambda item: item[:3])]
    selected = []
    for unit in merged:
        candidate = _context(result, [*selected, unit])
        if max_tokens and estimate_tokens(candidate) <= max_tokens:
            selected.append(unit)
        else:
            diagnostics["context_omitted"] += 1
            _omit(result, "context_budget")
    # Recheck every source we validated, including omitted units. No partially
    # stale review can be mistaken for a complete source snapshot.
    try:
        policy.recheck()
    except (OSError, ValueError, EvidenceError) as exc:
        try:
            policy.fresh()
        except (OSError, ValueError, EvidenceError):
            return _fail(result, "configuration_changed")
        if isinstance(exc, EvidenceError) and str(exc) == "input_changed":
            return _fail(result, "input_changed")
        return _fail(result, "source_changed")
    result["units"] = selected
    result["potential_conflicts"] = _review_conflicts(result["potential_conflicts"], selected)
    if selected:
        result["context"] = _context(result, selected)
    diagnostics["context_estimated_tokens"] = estimate_tokens(result["context"])
    return result


def _compile_input(vault, relative, max_tokens, query):
    result = _result(max_tokens)
    if not isinstance(vault, Path) or not vault.is_dir():
        return _fail(result, "invalid_vault")
    try:
        policy = _Policy(vault, result)
    except (OSError, ValueError, EvidenceError, HealthcheckError, RecursionError, OverflowError):
        return _fail(result, "invalid_config_or_vault")
    try:
        path = policy.path(relative, ".json")
        canonical = path.relative_to(policy.root).as_posix()
        info, _mark, data = stable_read(lambda: policy.path(canonical, ".json"),
                                       MAX_JSON_BYTES, contents=True)
        units = decode(data)
        policy.input_binding = (canonical, info)
        result = _compile(vault, units, max_tokens, query, policy)
    except (OSError, ValueError, EvidenceError) as exc:
        reason = str(exc) if isinstance(exc, EvidenceError) else "unsafe_input"
        return _fail(result, reason)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--input", required=True, help="explicit vault-relative JSON array of memory units")
    parser.add_argument("--query", help="optional explicit lexical intent")
    parser.add_argument("--max-tokens", type=int, default=1500)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    result = _compile_input(args.vault, args.input, args.max_tokens, args.query)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False))
    elif result["context"]:
        print(result["context"])
    diagnostics = result["diagnostics"]
    if args.format == "text" and diagnostics["status"] != "ok":
        print("memory compilation unavailable: " + diagnostics["status"], file=sys.stderr)
    elif args.format == "text" and not diagnostics["complete"]:
        print("memory compilation incomplete: " + ", ".join(diagnostics["omissions"]), file=sys.stderr)
    return 0 if diagnostics["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
