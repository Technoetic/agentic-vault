"""Bounded contracts and offline grading of caller-attested skill events.

No model or skill is executed here. Hashes bind supplied documents; they do not
independently establish the truth, completeness, or success of host events.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat


MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 100000
MAX_CONTEXT_BYTES = 16384
MAX_CONTEXT_DEPTH = 8
MAX_CONTEXT_NODES = 1024
_SKILL_ID = re.compile(r"[a-z0-9_-]{1,64}:[a-z0-9_-]{1,64}\Z", re.ASCII)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class RoutingError(ValueError):
    """A bounded diagnostic code, without caller-controlled input contents."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _copy_json(value, *, max_depth=MAX_JSON_DEPTH, max_nodes=MAX_JSON_NODES):
    """Validate plain JSON types and copy them within depth/node bounds."""
    remaining = max_nodes

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > max_depth:
            raise RoutingError("json_limit_exceeded")
        kind = type(item)
        if item is None or kind in (bool, int):
            return item
        if kind is float:
            if not math.isfinite(item):
                raise RoutingError("nonfinite_value")
            return item
        if kind is str:
            try:
                if len(item.encode("utf-8")) > MAX_DOCUMENT_BYTES:
                    raise RoutingError("document_too_large")
            except UnicodeError:
                raise RoutingError("invalid_unicode") from None
            return item
        if kind is list:
            return [visit(child, depth + 1) for child in item]
        if kind is dict:
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise RoutingError("invalid_json_type")
                visit(key, depth + 1)
                result[key] = visit(child, depth + 1)
            return result
        raise RoutingError("invalid_json_type")

    return visit(value, 0)


def _canonical_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise RoutingError("invalid_json_value") from None


def _document(value):
    result = _copy_json(value)
    if len(_canonical_bytes(result)) > MAX_DOCUMENT_BYTES:
        raise RoutingError("document_too_large")
    return result


def digest_document(value):
    """SHA256 of canonical UTF-8 JSON, with no appended newline."""
    return hashlib.sha256(_canonical_bytes(_document(value))).hexdigest()


def _input_path(path):
    try:
        raw = os.fspath(path)
    except TypeError:
        raise RoutingError("unsafe_path") from None
    if type(raw) is not str or not raw or "\x00" in raw:
        raise RoutingError("unsafe_path")
    portable = raw.replace("\\", "/")
    # Reject network/device paths before touching the filesystem, including ADS.
    if portable.startswith("//") or portable.startswith("/??/"):
        raise RoutingError("unsafe_path")
    drive = re.match(r"^[A-Za-z]:/", portable)
    tail = portable[3:] if drive else portable.lstrip("/")
    parts = tail.split("/")
    if ":" in tail or any(part == ".." for part in parts):
        raise RoutingError("unsafe_path")
    if any(any(char in part for char in '<>"|?*') or part.endswith((" ", "."))
           for part in parts if part not in ("", ".")):
        raise RoutingError("unsafe_path")
    reserved = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
    if any(reserved.match(part) for part in parts):
        raise RoutingError("unsafe_path")
    return Path(raw).absolute()


def _check_no_links(path):
    for candidate in (*reversed(path.parents), path):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise RoutingError("symlink_input")


def _read_bytes(path):
    target = _input_path(path)
    try:
        _check_no_links(target)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise RoutingError("invalid_input_file")
            if info.st_size > MAX_DOCUMENT_BYTES:
                raise RoutingError("document_too_large")
            raw = source.read(MAX_DOCUMENT_BYTES + 1)
            _check_no_links(target)
            current = target.stat()
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise RoutingError("input_changed")
    except RoutingError:
        raise
    except (OSError, ValueError):
        raise RoutingError("input_unreadable") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise RoutingError("document_too_large")
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RoutingError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise RoutingError("nonfinite_value")


def read_document_with_sha256(path):
    """Read once, returning strict JSON and the digest of the exact raw bytes."""
    raw = _read_bytes(path)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except RoutingError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise RoutingError("malformed_json") from None
    return _document(value), hashlib.sha256(raw).hexdigest()


def read_document(path):
    """Read a local regular JSON file without following input symlinks."""
    return read_document_with_sha256(path)[0]


def _shape(value, fields, code):
    if type(value) is not dict or set(value) != set(fields):
        raise RoutingError(code)


def _header(value, kind, fields):
    _shape(value, fields, "invalid_" + kind)
    if value["kind"] != kind or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise RoutingError("unsupported_schema")


def _text(value, maximum, code, *, nonblank=True):
    if type(value) is not str or len(value) > maximum or (nonblank and not value.strip()):
        raise RoutingError(code)


def _identifier(value):
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise RoutingError("invalid_identifier")


def _skill_id(value):
    if type(value) is not str or not _SKILL_ID.fullmatch(value):
        raise RoutingError("invalid_skill_id")


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise RoutingError("invalid_digest")


def _list(value, minimum, maximum, code):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise RoutingError(code)


def _choice(value, choices, code):
    if type(value) is not str or value not in choices:
        raise RoutingError(code)


def _source_path(value):
    _text(value, 1024, "invalid_source_path")
    if "\\" in value or ":" in value or "\x00" in value or any(
            part in ("", ".", "..") for part in value.split("/")):
        raise RoutingError("invalid_source_path")


def validate_catalog(value):
    """Validate an owned skill catalog and return an independent JSON value."""
    result = _document(value)
    _header(result, "skill_routing_catalog", ("kind", "schema_version", "source_kind", "skills"))
    _choice(result["source_kind"], ("synthetic", "manifest_scan"), "invalid_catalog_source")
    _list(result["skills"], 1, 256, "invalid_skill_count")
    seen = set()
    for skill in result["skills"]:
        _shape(skill, ("id", "name", "description", "source_sha256", "source_path"), "invalid_skill")
        _skill_id(skill["id"])
        if skill["id"] in seen:
            raise RoutingError("duplicate_skill_id")
        seen.add(skill["id"])
        _text(skill["name"], 256, "invalid_skill_name")
        _text(skill["description"], 4096, "invalid_skill_description")
        _digest(skill["source_sha256"])
        _source_path(skill["source_path"])
    return result


def _skill_list(value, catalog_ids):
    _list(value, 0, 256, "invalid_skill_list")
    seen = set()
    for item in value:
        _skill_id(item)
        if item not in catalog_ids:
            raise RoutingError("unknown_expectation_skill")
        if item in seen:
            raise RoutingError("duplicate_expectation_skill")
        seen.add(item)
    return seen


def _before_constraints(before, permitted):
    _list(before, 0, 65536, "invalid_before")
    edges = {item: set() for item in permitted}
    indegree = {item: 0 for item in permitted}
    seen = set()
    for pair in before:
        _list(pair, 2, 2, "invalid_before")
        for item in pair:
            _skill_id(item)
            if item not in permitted:
                raise RoutingError("invalid_before_skill")
        earlier, later = pair
        if earlier == later or (earlier, later) in seen:
            raise RoutingError("conflicting_before")
        seen.add((earlier, later))
        edges[earlier].add(later)
        indegree[later] += 1
    pending = [item for item, degree in indegree.items() if degree == 0]
    visited = 0
    while pending:
        item = pending.pop()
        visited += 1
        for later in edges[item]:
            indegree[later] -= 1
            if indegree[later] == 0:
                pending.append(later)
    if visited != len(permitted):
        raise RoutingError("cyclic_before")


def validate_suite(value, catalog):
    """Validate all expected skills and acyclic composition constraints."""
    catalog = validate_catalog(catalog)
    catalog_ids = {skill["id"] for skill in catalog["skills"]}
    result = _document(value)
    _header(result, "skill_routing_suite", ("kind", "schema_version", "suite_id", "cases"))
    _identifier(result["suite_id"])
    _list(result["cases"], 1, 1000, "invalid_case_count")
    seen = set()
    for case in result["cases"]:
        _shape(case, ("id", "prompt", "context", "expect"), "invalid_case")
        _identifier(case["id"])
        if case["id"] in seen:
            raise RoutingError("duplicate_case_id")
        seen.add(case["id"])
        _text(case["prompt"], 4096, "invalid_prompt", nonblank=False)
        if type(case["context"]) is not dict:
            raise RoutingError("invalid_context")
        _copy_json(case["context"], max_depth=MAX_CONTEXT_DEPTH, max_nodes=MAX_CONTEXT_NODES)
        if len(_canonical_bytes(case["context"])) > MAX_CONTEXT_BYTES:
            raise RoutingError("context_too_large")
        expect = case["expect"]
        _shape(expect, ("required", "allowed", "forbidden", "before", "entrypoints"), "invalid_expectation")
        required = _skill_list(expect["required"], catalog_ids)
        allowed = _skill_list(expect["allowed"], catalog_ids)
        forbidden = _skill_list(expect["forbidden"], catalog_ids)
        if required & allowed or required & forbidden or allowed & forbidden:
            raise RoutingError("conflicting_expectations")
        entrypoints = _skill_list(expect["entrypoints"], catalog_ids)
        if not entrypoints <= required:
            raise RoutingError("invalid_entrypoints")
        _before_constraints(expect["before"], required | allowed)
    return result


def validate_trace(value, suite, catalog):
    """Validate full source bindings and ordered caller observations."""
    catalog = validate_catalog(catalog)
    suite = validate_suite(suite, catalog)
    result = _document(value)
    _header(result, "skill_routing_trace", (
        "kind", "schema_version", "suite_sha256", "catalog_sha256", "source_kind",
        "observation_kind", "runtime", "observations"))
    _digest(result["suite_sha256"])
    _digest(result["catalog_sha256"])
    if result["suite_sha256"] != digest_document(suite) or result["catalog_sha256"] != digest_document(catalog):
        raise RoutingError("source_binding_mismatch")
    _choice(result["source_kind"], ("scripted_control", "host_observation"), "invalid_trace_source")
    _choice(result["observation_kind"], ("skill_invocation", "skill_file_read", "self_report"), "invalid_observation_kind")
    _shape(result["runtime"], ("host", "version", "model"), "invalid_runtime")
    for value in result["runtime"].values():
        _text(value, 256, "invalid_runtime")
    _list(result["observations"], 0, 1000, "invalid_observation_count")
    case_ids = {case["id"] for case in suite["cases"]}
    seen = set()
    for observation in result["observations"]:
        _shape(observation, ("case_id", "status", "invoked"), "invalid_observation")
        _identifier(observation["case_id"])
        if observation["case_id"] not in case_ids:
            raise RoutingError("unknown_observation_case")
        if observation["case_id"] in seen:
            raise RoutingError("duplicate_observation_case")
        seen.add(observation["case_id"])
        _choice(observation["status"], ("complete", "unavailable", "error"), "invalid_observation_status")
        _list(observation["invoked"], 0, 128, "invalid_invocation_count")
        for item in observation["invoked"]:
            _skill_id(item)
        if observation["status"] != "complete" and observation["invoked"]:
            raise RoutingError("noncomplete_invocations")
    return result


def _case_result(case, observation, evidence_matches):
    row = {"id": case["id"], "status": "unmeasured", "observed": [],
           "missing_required": [], "forbidden": [], "unexpected": [],
           "order_violations": [], "entrypoint_violations": [], "reason_codes": []}
    if observation is None:
        row["reason_codes"] = ["missing_observation"]
        return row
    if observation["status"] != "complete":
        row["reason_codes"] = ["observation_" + observation["status"]]
        return row
    if not evidence_matches:
        row["reason_codes"] = ["unsupported_observation_kind"]
        return row
    observed = observation["invoked"]
    expect = case["expect"]
    required, allowed, forbidden = (set(expect[key]) for key in ("required", "allowed", "forbidden"))
    present = set(observed)
    row["observed"] = list(observed)
    row["missing_required"] = [item for item in expect["required"] if item not in present]
    row["forbidden"] = [item for item in expect["forbidden"] if item in present]
    row["unexpected"] = list(dict.fromkeys(item for item in observed if item not in required | allowed | forbidden))
    # A late prerequisite cannot excuse an earlier dependent invocation.
    for earlier, later in expect["before"]:
        predecessor_seen = False
        for item in observed:
            if item == earlier:
                predecessor_seen = True
            if item == later and not predecessor_seen:
                row["order_violations"].append([earlier, later])
                break
    if expect["entrypoints"]:
        first = next((item for item in observed if item not in allowed), None)
        if first is not None and first not in expect["entrypoints"]:
            row["entrypoint_violations"] = [first]
    for field, reason in (("missing_required", "missing_required"), ("forbidden", "forbidden"),
                          ("unexpected", "unexpected"), ("order_violations", "order_violation"),
                          ("entrypoint_violations", "entrypoint_violation")):
        if row[field]:
            row["reason_codes"].append(reason)
    row["status"] = "fail" if row["reason_codes"] else "pass"
    return row


def evaluate(catalog, suite, trace, observation_kind="skill_invocation"):
    """Score only complete observations of the explicitly selected evidence kind.

    A synthetic control remains a control. A host observation remains a caller
    attestation. Neither this report nor its digests establish business success.
    """
    _choice(observation_kind, ("skill_invocation", "skill_file_read"), "invalid_evidence_selection")
    catalog = validate_catalog(catalog)
    suite = validate_suite(suite, catalog)
    trace = validate_trace(trace, suite, catalog)
    observations = {row["case_id"]: row for row in trace["observations"]}
    evidence_matches = trace["observation_kind"] == observation_kind
    cases = [_case_result(case, observations.get(case["id"]), evidence_matches) for case in suite["cases"]]
    passed = sum(row["status"] == "pass" for row in cases)
    failed = sum(row["status"] == "fail" for row in cases)
    return {
        "kind": "skill_routing_evaluation", "schema_version": 1,
        "suite_id": suite["suite_id"], "suite_sha256": digest_document(suite),
        "catalog_sha256": digest_document(catalog),
        "catalog_skill_ids": [skill["id"] for skill in catalog["skills"]],
        "source_kind": trace["source_kind"], "observation_kind": trace["observation_kind"],
        "runtime": trace["runtime"], "model_run_performed": False,
        "summary": {"cases": len(cases), "scored": passed + failed,
                    "unmeasured": len(cases) - passed - failed, "passed": passed, "failed": failed},
        "cases": cases,
    }
