#!/usr/bin/env python3
"""Optional, bounded source-binding assessments for caller-attested messages.

This module never authenticates a caller, proves truth or execution, grants
authorization, executes instructions, or writes memory. Even a supported claim
only satisfies the documented byte/role/observation binding checks.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

from vault_healthcheck import estimate_tokens


MAX_INPUT_BYTES = 256 * 1024
MAX_RECORDS = 256
MAX_CLAIMS = 256
MAX_OBSERVATIONS = 256
MAX_TEXT_BYTES = 32 * 1024
MAX_QUOTE_BYTES = 8 * 1024
MAX_DEPTH = 8
MAX_NODES = 8192
MAX_TOKENS = 16384
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
HASH_RE = re.compile(r"[0-9a-f]{64}")
ROLES = frozenset(("user", "assistant", "tool", "external"))
KINDS = frozenset(("observation", "user_instruction", "experience"))
RECORD_FIELDS = frozenset(("id", "role", "text"))
CLAIM_FIELDS = frozenset(("id", "source_id", "source_sha256", "quote", "kind"))
OBSERVATION_FIELDS = frozenset(("id", "source_id", "source_sha256", "quote", "result"))


class MemoryTrustError(ValueError):
    """Invalid input or an unusable budget; messages contain fixed codes only."""


def _json(value):
    # Escaping preserves exact decoded content while keeping terminal control
    # characters, Unicode display controls, and separators inert on stdout.
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _bytes(value):
    try:
        return value.encode("utf-8")
    except UnicodeError:
        raise MemoryTrustError("invalid_unicode") from None


def _bounded_json(value):
    """Check types before serialization, including subclasses and deep objects."""
    pending = [(value, 1)]
    nodes = 0
    serialized_bytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise MemoryTrustError("input_limit")
        kind = type(item)
        if kind is dict:
            if len(item) > 8 or any(type(key) is not str for key in item):
                raise MemoryTrustError("invalid_object")
            serialized_bytes += 2 + max(0, len(item) - 1) + len(item)
            for key, child in item.items():
                if len(key) > 64 or len(_bytes(key)) > 64:
                    raise MemoryTrustError("input_limit")
                serialized_bytes += len(_json(key))
                pending.append((child, depth + 1))
        elif kind is list:
            if len(item) > max(MAX_RECORDS, MAX_CLAIMS, MAX_OBSERVATIONS):
                raise MemoryTrustError("input_limit")
            serialized_bytes += 2 + max(0, len(item) - 1)
            pending.extend((child, depth + 1) for child in item)
        elif kind is str:
            if len(item) > MAX_TEXT_BYTES or len(_bytes(item)) > MAX_TEXT_BYTES:
                raise MemoryTrustError("input_limit")
            serialized_bytes += len(_json(item))
        elif kind is int:
            if not -(2**63) <= item < 2**63:
                raise MemoryTrustError("input_limit")
            serialized_bytes += len(str(item))
        elif kind in (bool, type(None)):
            serialized_bytes += len(_json(item))
        else:
            raise MemoryTrustError("invalid_json_type")
        if serialized_bytes > MAX_INPUT_BYTES:
            raise MemoryTrustError("input_limit")


def _fields(item, required, optional=frozenset()):
    if type(item) is not dict or not required <= item.keys() or item.keys() - required - optional:
        raise MemoryTrustError("invalid_fields")


def _identifier(value):
    if type(value) is not str or ID_RE.fullmatch(value) is None:
        raise MemoryTrustError("invalid_id")


def _proof(item):
    _identifier(item["source_id"])
    digest = item["source_sha256"]
    if type(digest) is not str or HASH_RE.fullmatch(digest) is None:
        raise MemoryTrustError("invalid_hash")
    quote = item["quote"]
    if type(quote) is not str or not quote.strip() or len(_bytes(quote)) > MAX_QUOTE_BYTES:
        raise MemoryTrustError("invalid_quote")


def _index(items, fields, limit, *, category):
    if type(items) is not list or len(items) > limit:
        raise MemoryTrustError("invalid_collection")
    result = {}
    optional = frozenset(("observation_id",)) if category == "claim" else frozenset()
    for item in items:
        _fields(item, fields, optional)
        _identifier(item["id"])
        if item["id"] in result:
            raise MemoryTrustError("duplicate_id")
        if category == "record":
            if type(item["role"]) is not str or item["role"] not in ROLES:
                raise MemoryTrustError("invalid_role")
            if type(item["text"]) is not str or len(_bytes(item["text"])) > MAX_TEXT_BYTES:
                raise MemoryTrustError("invalid_text")
        else:
            _proof(item)
            if category == "claim":
                if type(item["kind"]) is not str or item["kind"] not in KINDS:
                    raise MemoryTrustError("invalid_kind")
                if "observation_id" in item:
                    _identifier(item["observation_id"])
            elif type(item["result"]) is not str or item["result"] not in ("passed", "failed"):
                raise MemoryTrustError("invalid_result")
        result[item["id"]] = item
    return result


def _source_record(record):
    return {**record, "source_sha256": hashlib.sha256(_bytes(record["text"])).hexdigest()}


def _measure(report):
    # The number's own digits are part of the measured envelope. Starting at
    # zero is monotone and converges in a few iterations at these input bounds.
    report["estimated_tokens"] = 0
    for _ in range(12):
        measured = estimate_tokens(_json(report))
        if measured == report["estimated_tokens"]:
            return measured
        report["estimated_tokens"] = measured
    raise MemoryTrustError("budget_measurement_failed")  # defensive invariant


def prepare_context(records, *, max_tokens=1500):
    """Return whole source envelopes under the complete serialized token budget.

    Raw records require exactly id/role/text. Derived context records cannot be
    silently fed back as host records. A caller replaying unchanged records must
    explicitly project those original fields, retaining the original role/text.
    """
    _bounded_json({"records": records, "max_tokens": max_tokens})
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_TOKENS:
        raise MemoryTrustError("invalid_budget")
    indexed = _index(records, RECORD_FIELDS, MAX_RECORDS, category="record")
    report = {
        "schema_version": 1,
        "authorization_granted": False,
        "records": [],
        "omitted_records": len(indexed),
        "estimated_tokens": 0,
        "max_tokens": max_tokens,
    }
    if _measure(report) > max_tokens:
        raise MemoryTrustError("budget_too_small")
    for record in indexed.values():
        report["records"].append(_source_record(record))
        report["omitted_records"] -= 1
        if _measure(report) > max_tokens:
            report["records"].pop()
            report["omitted_records"] += 1
    _measure(report)
    return report


def _assessment_reason(record, item, observations):
    if record is None:
        return "unknown_source"
    if item["source_sha256"] != hashlib.sha256(_bytes(record["text"])).hexdigest():
        return "source_hash_mismatch"
    if item["quote"] not in record["text"]:
        return "quote_not_found"
    if item["kind"] == "user_instruction":
        return "user_source_bound" if record["role"] == "user" else "role_ineligible"
    if item["kind"] == "observation":
        return "source_bound"
    if "observation_id" not in item:
        return "missing_observation"
    proof = observations.get(item["observation_id"])
    if proof is None:
        return "unknown_observation"
    if any(proof[key] != item[key] for key in ("source_id", "source_sha256", "quote")):
        return "observation_binding_mismatch"
    if proof["result"] != "passed":
        return "observation_failed"
    return "observed_source_bound"


def review_claims(records, claims, *, observations=None):
    """Assess exact source bindings, never truth, execution, or permission.

    Observations must come separately from the host, not source text. This is a
    caller contract, not authenticated origin: a caller can forge every field.
    """
    if observations is None:
        observations = []
    _bounded_json({"records": records, "claims": claims, "observations": observations})
    sources = _index(records, RECORD_FIELDS, MAX_RECORDS, category="record")
    proposed = _index(claims, CLAIM_FIELDS, MAX_CLAIMS, category="claim")
    observed = _index(observations, OBSERVATION_FIELDS, MAX_OBSERVATIONS, category="observation")
    assessments = []
    supported = frozenset(("source_bound", "user_source_bound", "observed_source_bound"))
    for item in proposed.values():
        reason = _assessment_reason(sources.get(item["source_id"]), item, observed)
        assessments.append({
            "id": item["id"], "source_id": item["source_id"], "kind": item["kind"],
            "status": "supported" if reason in supported else "rejected", "reason": reason,
        })
    return {"schema_version": 1, "authorization_granted": False, "assessments": assessments}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise MemoryTrustError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise MemoryTrustError("invalid_json")


def _decode(raw):
    if len(raw) > MAX_INPUT_BYTES:
        raise MemoryTrustError("input_limit")
    # Scan before parsing so deeply nested JSON cannot reach parser recursion.
    depth = 0
    in_string = escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                in_string = False
        elif char == 34:
            in_string = True
        elif char in (91, 123):
            depth += 1
            if depth > MAX_DEPTH:
                raise MemoryTrustError("input_limit")
        elif char in (93, 125):
            depth -= 1
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise MemoryTrustError("invalid_json") from None
    _bounded_json(value)
    return value


def main(argv=None):
    """Read one bounded JSON request from stdin; never open caller paths."""
    try:
        arguments = sys.argv[1:] if argv is None else argv
        if len(arguments) != 1 or arguments[0] not in ("prepare", "review"):
            raise MemoryTrustError("invalid_operation")
        request = _decode(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        if arguments[0] == "prepare":
            _fields(request, frozenset(("records",)), frozenset(("max_tokens",)))
            result = prepare_context(request["records"], max_tokens=request.get("max_tokens", 1500))
        else:
            _fields(request, frozenset(("records", "claims")), frozenset(("observations",)))
            if "observations" in request and request["observations"] is None:
                raise MemoryTrustError("invalid_collection")
            result = review_claims(request["records"], request["claims"],
                                   observations=request.get("observations"))
        print(_json(result))
        return 0
    except MemoryTrustError as error:
        print(_json({"error": str(error), "authorization_granted": False}))
        return 2
    except (OSError, ValueError, TypeError, RecursionError):
        print(_json({"error": "operation_failed", "authorization_granted": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
