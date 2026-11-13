"""Observe caller-authored literal evidence in an already emitted recall result.

This pure API does not retrieve, read files, certify truth, or grant permission.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata

from vault_paths import relative_parts

MAX_REQUIREMENTS = 128
MAX_BLOCKERS = 128
MAX_ALTERNATIVES = 16
MAX_QUOTE_CHARS = 500
MAX_CONTEXT_CHARS = 262144
MAX_INPUT_NODES = 50000
MAX_INPUT_CHARS = 2097152
MAX_DEPTH = 20
MAX_INTEGER = 2**63 - 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


class SupportValidationError(ValueError):
    """Fixed error code, deliberately free of input text and source identifiers."""


def _reject(code="invalid_recall"):
    raise SupportValidationError(code)


def _bounded_json(value, code):
    """Bound direct Python objects too; no serialization or recursive repr calls."""
    stack = [(value, 0, False)]
    ancestors = set()
    nodes = chars = 0
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            ancestors.remove(id(item))
            continue
        nodes += 1
        if nodes > MAX_INPUT_NODES or depth > MAX_DEPTH:
            _reject(code)
        kind = type(item)
        if kind in (dict, list):
            if id(item) in ancestors or len(item) > MAX_INPUT_NODES - nodes:
                _reject(code)
            ancestors.add(id(item))
            stack.append((item, depth, True))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str or any(0xD800 <= ord(char) <= 0xDFFF for char in key):
                        _reject(code)
                    chars += len(key)
                    stack.append((child, depth + 1, False))
            else:
                stack.extend((child, depth + 1, False) for child in item)
        elif kind is str:
            chars += len(item)
            if chars > MAX_INPUT_CHARS:
                _reject(code)
            # UTF-8 hashing must not fail later on an unpaired surrogate.
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                _reject(code)
        elif kind is int:
            if not -MAX_INTEGER <= item <= MAX_INTEGER:
                _reject(code)
        elif kind is float:
            if not math.isfinite(item):
                _reject(code)
        elif item is not None and kind is not bool:
            _reject(code)
        if chars > MAX_INPUT_CHARS:
            _reject(code)


def _single_line(value, ceiling, *, nonempty=False):
    return (type(value) is str and len(value) <= ceiling
            and (not nonempty or bool(value.strip()))
            and not any(char in _LINE_BREAKS or ord(char) < 32
                        or unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value))


def _path(value, code):
    if (not _single_line(value, 1024, nonempty=True) or "\\" in value
            or not value.casefold().endswith(".md")):
        _reject(code)
    try:
        relative_parts(value, "source")
    except ValueError:
        _reject(code)


def _contract(contract):
    _bounded_json(contract, "invalid_contract")
    if (type(contract) is not dict or set(contract) != {"schema_version", "requirements", "blockers"}
            or type(contract["schema_version"]) is not int or contract["schema_version"] != 1):
        _reject("invalid_contract")
    identifiers = set()
    for key, ceiling in (("requirements", MAX_REQUIREMENTS), ("blockers", MAX_BLOCKERS)):
        entries = contract[key]
        if type(entries) is not list or len(entries) > ceiling or (key == "requirements" and not entries):
            _reject("invalid_contract")
        for entry in entries:
            if type(entry) is not dict or set(entry) != {"id", "alternatives"}:
                _reject("invalid_contract")
            identifier = entry["id"]
            if type(identifier) is not str or not _ID.fullmatch(identifier) or identifier in identifiers:
                _reject("invalid_contract")
            identifiers.add(identifier)
            alternatives = entry["alternatives"]
            if type(alternatives) is not list or not 1 <= len(alternatives) <= MAX_ALTERNATIVES:
                _reject("invalid_contract")
            unique = set()
            for alternative in alternatives:
                if type(alternative) is not dict or set(alternative) != {"path", "line", "quote"}:
                    _reject("invalid_contract")
                _path(alternative["path"], "invalid_contract")
                if type(alternative["line"]) is not int or not 1 <= alternative["line"] <= 2**31 - 1:
                    _reject("invalid_contract")
                if not _single_line(alternative["quote"], MAX_QUOTE_CHARS, nonempty=True):
                    _reject("invalid_contract")
                binding = (alternative["path"], alternative["line"], alternative["quote"])
                if binding in unique:
                    _reject("invalid_contract")
                unique.add(binding)


def _emitted(recalled):
    _bounded_json(recalled, "invalid_recall")
    if type(recalled) is not dict or set(recalled) != {"context", "matches", "diagnostics"}:
        _reject()
    context, matches, diagnostics = recalled["context"], recalled["matches"], recalled["diagnostics"]
    if type(context) is not str or len(context) > MAX_CONTEXT_CHARS:
        _reject()
    if type(matches) is not list or len(matches) > 50 or type(diagnostics) is not dict:
        _reject()
    if diagnostics.get("status") != "ok":
        _reject("recall_unavailable")
    if type(diagnostics.get("search_complete")) is not bool:
        _reject()
    omissions = diagnostics.get("omissions")
    if type(omissions) is not list or any(not _single_line(item, 128, nonempty=True) for item in omissions):
        _reject()
    if diagnostics["search_complete"] and omissions:
        _reject()
    counters = []
    for key in ("context_matches", "context_truncated", "context_omitted"):
        number = diagnostics.get(key)
        if type(number) is not int or not 0 <= number <= len(matches):
            _reject()
        counters.append(number)
    if counters[0] + counters[2] != len(matches) or counters[1] > counters[0]:
        _reject()
    lookup = {}
    order = {}
    for index, match in enumerate(matches):
        if type(match) is not dict or not {"path", "line", "snippet", "title", "score"}.issubset(match):
            _reject()
        _path(match["path"], "invalid_recall")
        if type(match["line"]) is not int or not 0 <= match["line"] <= 2**31 - 1:
            _reject()
        if (not _single_line(match["snippet"], 506) or not _single_line(match["title"], 512)
                or type(match["score"]) not in (int, float)):
            _reject()
        # One match per path is the actual recall invariant; ambiguous metadata
        # must not provide a second interpretation for an emitted source label.
        if match["path"] in order:
            _reject()
        order[match["path"]] = index
        lookup[f"[Source: {match['path']}:{match['line']}]"] = match
    emitted = {}
    last_index = -1
    truncated = 0
    for block in context.split("\n\n") if context else ():
        parts = block.split("\n")
        if len(parts) != 2 or parts[0] not in lookup:
            _reject()
        match = lookup[parts[0]]
        index = order[match["path"]]
        if index <= last_index or not _single_line(parts[1], 506):
            _reject()
        last_index = index
        visible = parts[1]
        prefix = visible
        if visible != match["snippet"]:
            if (not visible.endswith("...") or not visible[:-3]
                    or not match["snippet"].startswith(visible[:-3])
                    or len(visible[:-3]) >= len(match["snippet"])):
                _reject()
            truncated += 1
            # Remove the context renderer's suffix before mapping the retained
            # prefix to snippet coordinates; it can overlap a partial crop marker.
            prefix = visible[:-3]
        # Determine the eligible interval from the original snippet, not from
        # the shortened text. A retained prefix may contain only one or two of
        # its leading crop periods, or part of its trailing crop marker. None is
        # source evidence. Literal edge ellipses remain conservatively excluded.
        start = 3 if match["snippet"].startswith("...") else 0
        end = len(match["snippet"]) - (3 if match["snippet"].endswith("...") else 0)
        literal = prefix[start:min(len(prefix), end)]
        emitted[(match["path"], match["line"])] = (literal, block)
    if len(emitted) != counters[0] or truncated != counters[1]:
        _reject()
    return emitted, diagnostics


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assess_support(recalled: dict, contract: dict) -> dict:
    """Observe exact authored quotations in emitted, correctly attributed blocks.

    Invalid inputs raise SupportValidationError with a fixed code. No source is
    reopened, no freshness is established, and neither input is mutated.
    """
    _contract(contract)
    emitted, diagnostics = _emitted(recalled)
    observed = {"requirements": [], "blockers": []}
    missing = []
    evidence = []
    for key in ("requirements", "blockers"):
        for entry in contract[key]:
            found = False
            for alternative in entry["alternatives"]:
                block = emitted.get((alternative["path"], alternative["line"]))
                if block and alternative["quote"] in block[0]:
                    found = True
                    evidence.append({"kind": "requirement" if key == "requirements" else "blocker",
                        "id": entry["id"], "path": alternative["path"], "line": alternative["line"],
                        "quote_sha256": _hash(alternative["quote"]), "block_sha256": _hash(block[1])})
                    break
            if found:
                observed[key].append(entry["id"])
            elif key == "requirements":
                missing.append(entry["id"])
    state = ("blocker_observed" if observed["blockers"] else
             "requirements_missing" if missing else "requirements_observed")
    return {"schema_version": 1, "observed_requirement_ids": observed["requirements"],
        "missing_requirement_ids": missing, "observed_blocker_ids": observed["blockers"],
        "scope_complete": diagnostics["search_complete"],
        "output_complete": not (diagnostics["context_truncated"] or diagnostics["context_omitted"]),
        "observation_state": state, "evidence": evidence,
        "limitations": ["literal_observation_only", "source_freshness_unverified", "permission_not_assessed"]}
