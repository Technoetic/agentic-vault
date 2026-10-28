#!/usr/bin/env python3
"""Offline synthetic state/dependency revision controls and digest-bound replay.

Structured decimal answers only: this is not a free-form QA or model benchmark.
No model, network, shell, or real vault is accessed by this tool.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Sequence


DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/state_revisions/pack.json"
MAX_JSON_BYTES = 1024 * 1024
CONTROLS = ("good", "stale", "mixed", "wrong")
_ID = re.compile(r"[a-z][a-z0-9_\-]{0,63}\Z")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,12})?\Z")
_LIMIT = Decimal("1000000000000")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError("invalid object fields")


def _text(value, maximum=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("expected bounded nonempty text")
    return value


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


def _decimal(value):
    if not isinstance(value, str) or len(value) > 40 or not _NUMBER.fullmatch(value):
        raise ValueError("expected bounded plain decimal string")
    number = Decimal(value)
    if number.copy_abs() > _LIMIT:
        raise ValueError("decimal exceeds magnitude limit")
    return number


def _canonical(number):
    text = format(number, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    text = "0" if text == "-0" else text
    _decimal(text)
    return text


def _values(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 32:
        raise ValueError("expected bounded fact mapping")
    return {_identifier(key): _canonical(_decimal(number)) for key, number in value.items()}


def _tree(value):
    """Also bound caller-supplied Python objects before copying or hashing them."""
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 24 or count > 20000:
            raise ValueError("JSON nesting or population limit exceeded")
        if isinstance(item, dict):
            if len(item) > 4096 or any(not isinstance(key, str) or len(key) > 256 for key in item):
                raise ValueError("invalid JSON keys or object size")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > 4096:
                raise ValueError("JSON list limit exceeded")
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > 4096 or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError("JSON string limit or encoding error")
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int:
            if abs(item) > 10**18:
                raise ValueError("JSON integer limit exceeded")
        elif type(item) is float:
            if not math.isfinite(item) or abs(item) > 10**18:
                raise ValueError("nonfinite or excessive JSON number")
        else:
            raise ValueError("unsupported JSON value")


def _safe_path(path):
    path = Path(path)
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("network paths are not allowed")
    path = Path(os.path.abspath(path))
    for item in (*reversed(path.parents), path):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("linked paths are not allowed")
    return path


def _read_json(path):
    path = _safe_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_JSON_BYTES:
        raise ValueError("unsafe JSON file or byte limit exceeded")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    # On Windows lstat and fstat can expose different ctime meanings. Compare
    # each API's ctime to itself; compare identity/size/mtime across both APIs.
    signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
    if (len(raw) > MAX_JSON_BYTES or signature(before) != signature(opened)
            or signature(opened) != signature(after) or signature(after) != signature(final)
            or before.st_ctime_ns != final.st_ctime_ns or opened.st_ctime_ns != after.st_ctime_ns):
        raise ValueError("JSON input changed during read")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def integer(value):
        if len(value) > 20:
            raise ValueError("JSON integer limit exceeded")
        return int(value)
    def constant(value):
        raise ValueError("nonfinite JSON number")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
                           parse_int=integer, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid JSON encoding or structure") from exc
    _tree(value)
    return value


def _operation(rule, values):
    left, right = (_decimal(values[key]) for key in rule["inputs"])
    with localcontext() as context:
        context.prec = 64
        if rule["op"] == "multiply":
            result = left * right
        elif rule["op"] == "add":
            result = left + right
        else:
            result = left - right
    return _canonical(result)


def _state(evidence, revision_count):
    values = _values(evidence["initial"])
    for revision in evidence["revisions"][:revision_count]:
        values.update(_values(revision["set"]))
    for rule in evidence["rules"]:
        values[rule["target"]] = _operation(rule, values)
    return values


def _history(evidence, count):
    return [_state(evidence, index) for index in range(count + 1)]


def load_pack(fixture: Path = DEFAULT_FIXTURE) -> dict:
    """Load a bounded independently authored synthetic pack, not a user vault."""
    pack = _read_json(fixture)
    _keys(pack, ("schema_version", "synthetic", "cases", "labels"))
    if type(pack["schema_version"]) is not int or pack["schema_version"] != 1 or pack["synthetic"] is not True:
        raise ValueError("expected version-1 synthetic pack")
    if not isinstance(pack["cases"], list) or not 1 <= len(pack["cases"]) <= 64 or not isinstance(pack["labels"], dict):
        raise ValueError("invalid case or label population")
    seen = set()
    for case in pack["cases"]:
        _keys(case, ("id", "question", "initial", "revisions", "rules", "requested", "snapshots"))
        case_id = _identifier(case["id"])
        if case_id in seen:
            raise ValueError("duplicate case id")
        seen.add(case_id)
        _text(case["question"])
        direct = set(_values(case["initial"]))
        revisions, rules = case["revisions"], case["rules"]
        if not isinstance(revisions, list) or not 1 <= len(revisions) <= 16 or not isinstance(rules, list) or len(rules) > 16:
            raise ValueError("invalid revision or rule population")
        for revision in revisions:
            _keys(revision, ("set",))
            if not set(_values(revision["set"])) <= direct:
                raise ValueError("revisions must update declared direct facts")
        available = set(direct)
        for rule in rules:
            _keys(rule, ("target", "op", "inputs"))
            target = _identifier(rule["target"])
            if target in available or rule["op"] not in ("add", "subtract", "multiply"):
                raise ValueError("invalid dependency target or operation")
            if not isinstance(rule["inputs"], list) or len(rule["inputs"]) != 2 or any(_identifier(key) not in available for key in rule["inputs"]):
                raise ValueError("dependencies must reference prior declared facts")
            available.add(target)
        requested = case["requested"]
        if not isinstance(requested, list) or len(requested) != len(available) or any(not isinstance(key, str) for key in requested) or set(requested) != available or len(available) > 32:
            raise ValueError("requested facts must include every declared fact and dependency")
        snapshots = case["snapshots"]
        if not isinstance(snapshots, list) or not 2 <= len(snapshots) <= 17:
            raise ValueError("expected bounded earlier and revised snapshots")
        snapshot_ids, positions = set(), []
        history = _history(case, len(revisions))  # Validate every arithmetic intermediate.
        labels = pack["labels"].get(case_id)
        if not isinstance(labels, dict):
            raise ValueError("missing case labels")
        for snapshot in snapshots:
            _keys(snapshot, ("id", "after_revision"))
            snapshot_id = _identifier(snapshot["id"])
            count = snapshot["after_revision"]
            if snapshot_id in snapshot_ids or type(count) is not int or not 0 <= count <= len(revisions):
                raise ValueError("invalid snapshot id or revision position")
            snapshot_ids.add(snapshot_id)
            positions.append(count)
            label = labels.get(snapshot_id)
            _keys(label, ("current", "superseded"))
            current = history[count]
            prior = {_json(item): item for item in history[:count] if item != current}
            if not isinstance(label["superseded"], list) or len(label["superseded"]) > 16:
                raise ValueError("invalid superseded label population")
            old = [_values(item) for item in label["superseded"]]
            if _values(label["current"]) != current or len(old) != len(prior) or {_json(item) for item in old} != set(prior):
                raise ValueError("labels disagree with declared state history")
        if set(labels) != snapshot_ids or positions != sorted(set(positions)) or positions[0] != 0 or positions[-1] != len(revisions):
            raise ValueError("snapshots must span initial and final revision without duplicate positions")
    if set(pack["labels"]) != seen:
        raise ValueError("extra or missing case labels")
    return pack


def synthetic_answer(payload: dict, *, control: str = "good") -> dict:
    """Label-blind deterministic control; only visible revisions are available."""
    if control not in CONTROLS:
        raise ValueError("unknown synthetic control")
    evidence = payload["evidence"]
    count = len(evidence["revisions"])
    current = _state(evidence, count)
    previous = _state(evidence, max(0, count - 1))
    if control == "stale":
        current = previous
    elif control == "mixed":
        for rule in evidence["rules"]:
            current[rule["target"]] = previous[rule["target"]]
    elif control == "wrong":
        current = {key: _canonical(_decimal(value) + Decimal(1)) for key, value in current.items()}
    return {"status": "answer", "values": current}


def _response(value, requested):
    _keys(value, ("status", "values"))
    if value["status"] == "abstain" and value["values"] == {}:
        return {"status": "abstain", "values": {}}
    if value["status"] != "answer":
        raise ValueError("invalid response status")
    values = _values(value["values"])
    if set(values) != set(requested):
        raise ValueError("response must supply exactly the requested facts")
    return {"status": "answer", "values": values}


def _observation(value, binding, requested):
    _tree(value)
    _keys(value, ("binding", "response", "origin", "model", "usage", "actual_cost"))
    # Canonical comparison preserves JSON type distinctions, including True != 1.
    if _json(value["binding"]) != _json(binding):
        raise ValueError("observation binding mismatch")
    if value["origin"] not in ("synthetic_control", "external_observation", "actual_model"):
        raise ValueError("unknown observation origin")
    if value["model"] is not None:
        _text(value["model"], 256)
    usage, cost = value["usage"], value["actual_cost"]
    if value["origin"] == "actual_model":
        if value["model"] is None:
            raise ValueError("actual-model observation requires a model name")
    elif usage is not None or cost is not None or value["model"] is not None:
        raise ValueError("non-model observations cannot claim model usage or cost")
    if usage is not None:
        _keys(usage, ("input_tokens", "output_tokens"))
        if any(type(number) is not int or not 0 <= number <= 10**9 for number in usage.values()):
            raise ValueError("invalid bounded token usage")
    if cost is not None:
        _keys(cost, ("amount", "currency"))
        if type(cost["amount"]) not in (int, float) or not math.isfinite(cost["amount"]) or not 0 <= cost["amount"] <= 10**6 or not isinstance(cost["currency"], str) or not re.fullmatch(r"[A-Z]{3}", cost["currency"]):
            raise ValueError("invalid observed cost")
    checked = copy.deepcopy(value)
    checked["response"] = _response(value["response"], requested)
    return checked


def _grade(response, label, rules):
    if response["status"] == "abstain":
        return {"classification": "unassessed", "correct": None, "dependency_error_count": None}
    values = response["values"]
    current, prior = _values(label["current"]), [_values(item) for item in label["superseded"]]
    dependency_errors = 0
    for rule in rules:
        try:
            consistent = values[rule["target"]] == _operation(rule, values)
        except ValueError:  # The respondent's inputs may overflow bounded arithmetic.
            consistent = False
        dependency_errors += not consistent
    if values == current:
        classification = "current"
    elif values in prior:
        classification = "superseded"
    elif prior and all(value in {state[key] for state in [current, *prior]} for key, value in values.items()):
        classification = "mixed_dependency_stale"
    else:
        classification = "other"
    return {"classification": classification, "correct": classification == "current",
            "dependency_error_count": dependency_errors}


def evaluate(fixture: Path = DEFAULT_FIXTURE, *, control: str = "good", answerer: Callable | None = None,
             observations: dict | None = None) -> dict:
    """Evaluate copied public snapshots; external outputs are caller-attested only."""
    if control not in CONTROLS or (answerer is not None and observations is not None):
        raise ValueError("invalid control or mutually exclusive answer inputs")
    pack = load_pack(fixture)
    config = {"schema_version": 1, "control": control, "arithmetic": "decimal64-exact-12-fraction-magnitude-1e12",
              "grader_version": 1}
    expected_ids = {case["id"] + "/" + snapshot["id"] for case in pack["cases"] for snapshot in case["snapshots"]}
    if observations is not None:
        _tree(observations)
        if not isinstance(observations, dict) or set(observations) != expected_ids:
            raise ValueError("replay requires exactly one observation per snapshot")
    mode = "observation_replay" if observations is not None else "external_callback" if answerer else "synthetic_control"
    fixture_hash = _hash(pack)
    rows = []
    for case in pack["cases"]:
        for snapshot in case["snapshots"]:
            row_id = case["id"] + "/" + snapshot["id"]
            evidence = {"initial": case["initial"], "revisions": case["revisions"][:snapshot["after_revision"]], "rules": case["rules"]}
            payload = {"case_id": case["id"], "snapshot": snapshot, "question": case["question"],
                       "requested": case["requested"], "evidence": evidence}
            binding = {"schema_version": 1, "case_id": case["id"], "snapshot_id": snapshot["id"],
                       "fixture_sha256": fixture_hash, "question_sha256": _hash(case["question"]),
                       "evidence_sha256": _hash({"snapshot": snapshot, "evidence": evidence, "requested": case["requested"]}),
                       "config_sha256": _hash(config)}
            response = observation = error = None
            grade = {"classification": "unassessed", "correct": None, "dependency_error_count": None}
            try:
                if observations is not None:
                    candidate = observations[row_id]
                else:
                    response = answerer(copy.deepcopy(payload)) if answerer else synthetic_answer(copy.deepcopy(payload), control=control)
                    candidate = {"binding": binding, "response": response,
                                 "origin": "external_observation" if answerer else "synthetic_control",
                                 "model": None, "usage": None, "actual_cost": None}
                observation = _observation(candidate, binding, case["requested"])
                response = observation["response"]
                grade = _grade(response, pack["labels"][case["id"]][snapshot["id"]], case["rules"])
            except Exception:
                # Never echo arbitrary callback exceptions, malformed values or local paths.
                response = observation = None
                error = "invalid observation or callback failure"
            rows.append({"id": row_id, "case_id": case["id"], "snapshot_id": snapshot["id"],
                         "question": case["question"], "after_revision": snapshot["after_revision"],
                         "answer": grade, "response": response, "observation": observation,
                         "execution": {"status": "error" if error else "ok", "error": error}})
    counts = Counter(row["answer"]["classification"] for row in rows)
    usages = [row["observation"]["usage"] for row in rows if row["observation"] and row["observation"]["usage"] is not None]
    summary = {name + "_count": counts[name] for name in ("current", "superseded", "mixed_dependency_stale", "other", "unassessed")}
    summary.update(snapshot_count=len(rows), assessed_count=len(rows) - counts["unassessed"],
                   execution_error_count=sum(row["execution"]["status"] == "error" for row in rows),
                   dependency_error_count=sum(row["answer"]["dependency_error_count"] or 0 for row in rows),
                   dependency_assessed_snapshot_count=sum(row["answer"]["dependency_error_count"] is not None for row in rows))
    return {"benchmark": "independently authored synthetic state/dependency revision evaluation; not an official benchmark",
            "mode": mode, "control": control if mode == "synthetic_control" else None,
            "fixture_sha256": fixture_hash, "config": config, "case_count": len(pack["cases"]),
            "summary": summary, "per_snapshot": rows,
            "cost": {"model_request_count": 0, "actual_model_cost": None,
                     "actual_model_usage": {key: sum(item[key] for item in usages) for key in ("input_tokens", "output_tokens")} if len(usages) == len(rows) else None,
                     "observed_usage_snapshot_count": len(usages),
                     "usage_provenance": "Replay model usage/cost is caller-attested; missing values remain null. Costs are retained per observation, never inferred or aggregated."},
            "interpretation": "Exact structured decimal comparisons within declared synthetic facts only. Dependency consistency is separate from snapshot correctness. No live model execution or robustness/effectiveness claim."}


def _write_observations(path, report):
    path = _safe_path(path)
    if any(row["observation"] is None for row in report["per_snapshot"]):
        raise ValueError("cannot export incomplete observations")
    encoded = (json.dumps({row["id"]: row["observation"] for row in report["per_snapshot"]}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("observation export exceeds byte limit")
    # Fresh files only: no truncation of existing files, hardlinks or input paths.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "state revision evaluation: invalid command line\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--control", choices=CONTROLS, default="good")
    parser.add_argument("--observations", type=Path, help="digest-bound per-snapshot JSON replay")
    parser.add_argument("--write-observations", type=Path, help="export replay to a new local JSON file")
    args = parser.parse_args(argv)
    try:
        report = evaluate(control=args.control, observations=_read_json(args.observations) if args.observations else None)
        if args.write_observations:
            _write_observations(args.write_observations, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError):
        print("state revision evaluation: invalid or unsafe input/output", file=sys.stderr)
        return 2
    return 1 if report["summary"]["execution_error_count"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
