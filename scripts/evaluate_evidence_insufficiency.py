#!/usr/bin/env python3
"""Offline, synthetic structured evidence-insufficiency evaluation (stdlib only).

Claims and coverage are exact fixture slots, never free-prose semantic inference.
No retrieval policy, model, network, shell execution, or user vault is involved.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Callable


DEFAULT_FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/evidence_insufficiency/pack.json"
MAX_JSON_BYTES = 1024 * 1024
MAX_CASES = 100
MAX_CLAIMS = 32
CONTROLS = ("good", "deny_missing", "always_insufficient", "wrong")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _shape(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("unexpected object fields")


def _text(value, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("expected bounded nonempty text")
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("unsafe text character")
    return value


def _list(value, maximum=MAX_CLAIMS):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("expected bounded list")
    return value


def _bounded_tree(value):
    """Bound direct Python inputs as well as decoded JSON before copies/hashes."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 20000 or depth > 24:
            raise ValueError("JSON structure exceeds limit")
        if isinstance(item, dict):
            if len(item) > 200:
                raise ValueError("JSON object exceeds limit")
            for key, child in item.items():
                _text(key, 128)
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > 200:
                raise ValueError("JSON array exceeds limit")
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            _text(item, 4096)
        elif item is None or type(item) is bool:
            pass
        elif type(item) in (int, float):
            if not -10**9 <= item <= 10**9 or not math.isfinite(item):
                raise ValueError("JSON number exceeds limit")
        else:
            raise ValueError("unsupported JSON value")
    if len(_json(value).encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("JSON exceeds byte limit")


def _validate_path_syntax(path):
    """Reject Windows stream/device syntax portably before any filesystem open."""
    raw = _text(os.fspath(path), 4096).replace("\\", "/")
    if raw.startswith(("//?/", "//./")):
        raise ValueError("unsafe Windows device path")
    if len(raw) >= 2 and raw[1] == ":" and raw[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
        raw = raw[2:]  # The sole permitted colon belongs to an ordinary drive anchor.
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³")
    for component in raw.split("/"):
        if component in ("", ".", ".."):
            continue
        if (any(character in component for character in ':<>"|?*') or
                component.rstrip(" .") != component or component.split(".")[0].upper() in reserved):
            raise ValueError("unsafe portable path component")


def _check_path(path: Path):
    """Refuse symlinks, Windows reparse points and linked parent directories."""
    _validate_path_syntax(path)
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("unsafe filesystem link")
    return path


def _stamp(info):
    # Windows lstat/fstat can represent creation/change time differently.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


def _read_json(path):
    path = _check_path(Path(path))
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_JSON_BYTES:
        raise ValueError("unsafe or oversized JSON input")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if _stamp(before) != _stamp(opened) or opened.st_nlink != 1:
            raise ValueError("JSON input changed while opening")
        raw = stream.read(MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    if (len(raw) > MAX_JSON_BYTES or _stamp(opened) != _stamp(after) or _stamp(after) != _stamp(final) or
            opened.st_ctime_ns != after.st_ctime_ns or before.st_ctime_ns != final.st_ctime_ns):
        raise ValueError("JSON input changed or exceeds limit")
    _check_path(path)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def number(token):
        if len(token) > 32:
            raise ValueError("JSON number exceeds limit")
        result = float(token) if any(c in token for c in ".eE") else int(token)
        if not -10**9 <= result <= 10**9 or not math.isfinite(result):
            raise ValueError("nonfinite or oversized JSON number")
        return result

    def nonfinite(token):
        raise ValueError("nonfinite JSON number")

    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs,
                           parse_int=number, parse_float=number, parse_constant=nonfinite)
        _bounded_tree(value)
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid JSON encoding or structure") from error


def _claims(value):
    checked = []
    for claim in _list(value):
        _shape(claim, {"slot", "value", "polarity"})
        _text(claim["slot"], 128)
        _text(claim["value"])
        if claim["polarity"] not in ("positive", "negative"):
            raise ValueError("invalid claim polarity")
        checked.append(copy.deepcopy(claim))
    if len({item["slot"] for item in checked}) != len(checked):
        raise ValueError("duplicate or conflicting claim slot")
    return checked


def _claim_set(claims):
    return {_json(claim) for claim in claims}


def _validate_pack(pack):
    _bounded_tree(pack)
    _shape(pack, {"schema_version", "synthetic", "description", "cases", "labels"})
    if type(pack["schema_version"]) is not int or pack["schema_version"] != 1 or pack["synthetic"] is not True:
        raise ValueError("expected version-1 synthetic pack")
    _text(pack["description"], 2048)
    _list(pack["cases"], MAX_CASES)
    if not isinstance(pack["labels"], dict):
        raise ValueError("expected separate labels")
    seen = set()
    for case in pack["cases"]:
        _shape(case, {"id", "question", "claim_slots", "evidence"})
        case_id = _text(case["id"], 128)
        if case_id in seen:
            raise ValueError("duplicate case id")
        seen.add(case_id)
        _text(case["question"], 2048)
        slots = set()
        for slot in _list(case["claim_slots"]):
            _shape(slot, {"id", "subject", "predicate"})
            _text(slot["id"], 128)
            _text(slot["subject"])
            _text(slot["predicate"])
            if slot["id"] in slots:
                raise ValueError("duplicate declared slot")
            slots.add(slot["id"])
        if not slots:
            raise ValueError("case requires declared claim slots")
        evidence = case["evidence"]
        _shape(evidence, {"complete", "records"})
        if type(evidence["complete"]) is not bool:
            raise ValueError("evidence completeness must be boolean")
        records = _claims(evidence["records"])
        if any(record["slot"] not in slots for record in records):
            raise ValueError("evidence refers to undeclared slot")
        label = pack["labels"].get(case_id)
        _shape(label, {"expected_kind", "expected_claims"})
        expected = _claims(label["expected_claims"])
        sufficient = evidence["complete"] and {record["slot"] for record in records} == slots
        if label["expected_kind"] == "answer":
            if not sufficient or _claim_set(expected) != _claim_set(records):
                raise ValueError("answer labels require complete explicit evidence")
        elif label["expected_kind"] == "insufficient":
            if sufficient or expected:
                raise ValueError("insufficiency labels disagree with evidence")
        else:
            raise ValueError("invalid expected response kind")
    if set(pack["labels"]) != seen:
        raise ValueError("case and label ids disagree")
    return copy.deepcopy(pack)


def load_pack(fixture=DEFAULT_FIXTURE) -> dict:
    """Load only a bounded synthetic pack JSON file (or its containing folder)."""
    _validate_path_syntax(fixture)
    path = Path(fixture)
    if path.is_dir():
        path = path / "pack.json"
    return _validate_pack(_read_json(path))


def validate_response(response):
    _bounded_tree(response)
    _shape(response, {"kind", "claims"})
    if response["kind"] not in ("answer", "insufficient", "premise_denial"):
        raise ValueError("invalid structured response kind")
    claims = _claims(response["claims"])
    if response["kind"] != "insufficient" and not claims:
        raise ValueError("asserted answers require claims")
    if response["kind"] == "premise_denial" and any(c["polarity"] != "negative" for c in claims):
        raise ValueError("premise denial requires explicit negative claims")
    return {"kind": response["kind"], "claims": claims}


def synthetic_answer(payload: dict, *, control="good") -> dict:
    """Label-blind deterministic control consuming only one public case."""
    if control not in CONTROLS:
        raise ValueError("unknown synthetic control")
    evidence = payload["evidence"]
    slots = [item["id"] for item in payload["claim_slots"]]
    records = copy.deepcopy(evidence["records"])
    covered = {item["slot"] for item in records}
    sufficient = evidence["complete"] and covered == set(slots)
    if control == "always_insufficient":
        return {"kind": "insufficient", "claims": []}
    if control == "deny_missing" and not sufficient:
        slot = next((item for item in slots if item not in covered), slots[0])
        return {"kind": "premise_denial", "claims": [{"slot": slot, "value": "존재하지 않음", "polarity": "negative"}]}
    if not sufficient:
        return {"kind": "insufficient", "claims": [item for item in records if item["polarity"] == "positive"]}
    if control == "wrong":
        records[0]["value"] = "의도적으로 틀린 합성 값"
    return {"kind": "answer", "claims": records}


def _config(threshold):
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("invalid coverage threshold")
    return {"evaluator": "evidence_insufficiency_v1", "coverage_low_threshold": float(threshold),
            "negative_requires_complete_explicit_evidence": True,
            "coverage_scope": "declared_fixture_claim_slots_only"}


def _binding(pack, case, config):
    return {"fixture_digest": _digest(pack), "case_digest": _digest(case),
            "question_digest": _digest(case["question"]), "evidence_digest": _digest(case["evidence"]),
            "config_digest": _digest(config)}


def _replay(value, pack, config):
    _bounded_tree(value)
    _shape(value, {"schema_version", "fixture_digest", "config_digest", "observations"})
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1 or
            value["fixture_digest"] != _digest(pack) or value["config_digest"] != _digest(config)):
        raise ValueError("observation envelope binding mismatch")
    _list(value["observations"], MAX_CASES)
    expected = {case["id"]: case for case in pack["cases"]}
    checked = {}
    for row in value["observations"]:
        _shape(row, {"case_id", "binding", "response", "origin", "control"})
        case_id = _text(row["case_id"], 128)
        if case_id in checked or case_id not in expected or row["binding"] != _binding(pack, expected[case_id], config):
            raise ValueError("missing, extra, duplicate or changed observation binding")
        if row["origin"] == "synthetic_control":
            if row["control"] not in CONTROLS:
                raise ValueError("unknown control provenance")
        elif row["origin"] == "external_observation":
            if row["control"] is not None:
                raise ValueError("external observation cannot claim a control")
        else:
            raise ValueError("unknown observation origin")
        response = validate_response(row["response"])
        checked[case_id] = {**copy.deepcopy(row), "response": response}
    if set(checked) != set(expected):
        raise ValueError("missing observation")
    return checked


def _coverage(case, config):
    declared = [item["id"] for item in case["claim_slots"]]
    covered = {item["slot"] for item in case["evidence"]["records"]}
    ratio = len(covered) / len(declared)
    return {"declared_slots": len(declared), "covered_slots": len(covered),
            "missing_slots": [item for item in declared if item not in covered], "ratio": ratio,
            "coverage_low": ratio < config["coverage_low_threshold"],
            "evidence_complete": case["evidence"]["complete"]}


def _grade(response, case, label):
    actual = _claim_set(response["claims"])
    evidence = _claim_set(case["evidence"]["records"])
    negative = [claim for claim in response["claims"] if claim["polarity"] == "negative"]
    unsupported_negative = any(not case["evidence"]["complete"] or _json(claim) not in evidence
                               for claim in negative)
    if unsupported_negative:
        category, correct = "unsupported_premise_denial", False
    elif response["kind"] == "insufficient":
        correct = label["expected_kind"] == "insufficient" and actual <= evidence
        category = "insufficiency_acknowledged" if correct else "other_error"
    else:
        correct = label["expected_kind"] == "answer" and actual == _claim_set(label["expected_claims"])
        category = "supported_answer" if correct else "other_error"
    return {"category": category, "correct": correct,
            "certified_negative": bool(correct and negative and response["kind"] != "insufficient")}


def evaluate(pack=None, *, control="good", observations=None, answerer: Callable | None = None,
             coverage_low_threshold=1.0) -> dict:
    """Evaluate copied public payloads or validate a complete digest-bound replay.

    Malformed replay aborts the run. Callback exceptions/malformed responses are
    execution errors, with no exception text or successful replay observation.
    """
    pack = load_pack() if pack is None else _validate_pack(pack)
    config = _config(coverage_low_threshold)
    if control not in CONTROLS or (observations is not None and answerer is not None):
        raise ValueError("invalid or conflicting response source")
    replay = _replay(observations, pack, config) if observations is not None else None
    results, recorded = [], []
    for case in pack["cases"]:
        case_id = case["id"]
        row = {"case_id": case_id, "coverage": _coverage(case, config)}
        if replay is not None:
            observation = copy.deepcopy(replay[case_id])
            response = observation["response"]
        else:
            try:
                payload = copy.deepcopy(case)
                response = validate_response(answerer(payload) if answerer else synthetic_answer(payload, control=control))
            except Exception:
                row.update(response=None, category="execution_error", correct=False, certified_negative=False)
                results.append(row)
                continue
            observation = {"case_id": case_id, "binding": _binding(pack, case, config), "response": response,
                           "origin": "external_observation" if answerer else "synthetic_control",
                           "control": None if answerer else control}
        row["response"] = copy.deepcopy(response)
        row.update(_grade(response, case, pack["labels"][case_id]))
        results.append(row)
        recorded.append(copy.deepcopy(observation))
    correct = sum(row["correct"] for row in results)
    origins = sorted({row["origin"] for row in recorded})
    envelope = {"schema_version": 1, "fixture_digest": _digest(pack), "config_digest": _digest(config),
                "observations": recorded}
    return {"schema_version": 1, "synthetic_fixture": True, "configuration": config,
            "provenance": {"mode": "observation_replay" if replay is not None else
                           ("callback" if answerer else "synthetic_control"),
                           "control": None if replay is not None or answerer else control,
                           "origins": origins, "caller_attested": "external_observation" in origins,
                           "model_requests": 0, "actual_cost": None},
            "summary": {"total": len(results), "correct": correct,
                        "accuracy": correct / len(results) if results else None,
                        "categories": dict(sorted(Counter(row["category"] for row in results).items()))},
            "results": results, "observations": envelope,
            "observation_export_complete": len(recorded) == len(results)}


def _write_observations(path, observations):
    _validate_path_syntax(path)
    path = Path(os.path.abspath(path))
    _check_path(path.parent)
    raw = (_json(observations) + "\n").encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("observations exceed output limit")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal messages echo arbitrary input, including long values.
        self.exit(2, "error: invalid evaluation arguments\n")


def main(argv=None) -> int:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--control", choices=CONTROLS, default=None)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--write-observations", type=Path)
    parser.add_argument("--coverage-low-threshold", type=float, default=1.0,
                        help="Diagnostic threshold over declared fixture slots only (default: 1)")
    args = parser.parse_args(argv)
    try:
        if args.observations and args.control is not None:
            raise ValueError("control and replay are mutually exclusive")
        observations = _read_json(args.observations) if args.observations else None
        report = evaluate(control=args.control or "good", observations=observations,
                          coverage_low_threshold=args.coverage_low_threshold)
        if args.write_observations:
            if not report["observation_export_complete"]:
                raise ValueError("cannot export incomplete observations")
            _write_observations(args.write_observations, report["observations"])
        # ASCII escapes keep JSON portable through Windows legacy console pipes.
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, RecursionError, OverflowError, TypeError):
        print("error: invalid, unsafe, changed or oversized evaluation input/output", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
