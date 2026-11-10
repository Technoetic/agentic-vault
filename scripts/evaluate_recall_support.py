#!/usr/bin/env python3
"""Measure authored evidence visibility through actual recall on original synthetic vaults.

Standard library only. No user-vault input, model call, network, or executor.
The naive control deliberately confuses any lexical match with support.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/agentic-vault/scripts"))
from vault_paths import relative_parts
from vault_recall import recall
from vault_recall_support import assess_support

DEFAULT_PACK = ROOT / "tests/fixtures/recall_support/pack.json"
MAX_PACK_BYTES = 256 * 1024
MAX_CASES = 64
MAX_CASE_FILES = 16
MAX_FILE_BYTES = 524289
MAX_CASE_BYTES = 2 * 1024 * 1024
CONTROLS = ("assessor", "naive_any_match")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _sha(_json(value).encode("utf-8"))


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("invalid fixture fields")


def _text(value, maximum, *, multiline=False):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError("invalid bounded text")
    if any((ord(c) < 32 and not (multiline and c in "\n\t")) or
           0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("invalid text character")
    return value


def _list(value, maximum):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("invalid bounded list")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid bounded integer")


def _path(value):
    _text(value, 512)
    parts = relative_parts(value, "fixture path")
    # Explicit portable spelling keeps case/alias differences out of labels.
    if "\\" in value or not value.endswith(".md") or parts[0] != "facts":
        raise ValueError("unsafe fixture path")
    return value


def _padding(size):
    """Exactly size UTF-8 bytes; multibyte filler makes character counts wrong."""
    return ("가" * (size // 3) + "x" * (size % 3)).encode("utf-8")


def _file_bytes(file):
    if "text" in file:
        _shape(file, {"path", "text"})
        _text(file["text"], MAX_FILE_BYTES, multiline=True)
        raw = file["text"].encode("utf-8")
    else:
        _shape(file, {"path", "boundary"})
        spec = file["boundary"]
        _shape(spec, {"bytes", "position", "quote"})
        _integer(spec["bytes"], 524287, 524289)
        _text(spec["quote"], 200)
        quote = spec["quote"].encode("utf-8")
        if spec["position"] == "start":
            raw = quote + b"\n" + _padding(spec["bytes"] - len(quote) - 1)
        elif spec["position"] == "end":
            raw = _padding(spec["bytes"] - len(quote) - 1) + b"\n" + quote
        elif spec["position"] == "middle":
            remaining = spec["bytes"] - len(quote) - 2
            raw = _padding(remaining // 2) + b"\n" + quote + b"\n" + _padding(remaining - remaining // 2)
        else:
            raise ValueError("invalid boundary position")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("fixture file exceeds bound")
    return raw


def _empty_recall():
    # Actual recall produces this shape for an empty, valid search. Used only
    # to validate contracts before allocating/writing a synthetic vault.
    return {"context": "", "matches": [], "diagnostics": {
        "status": "ok", "search_complete": True, "context_matches": 0,
        "context_omitted": 0, "context_truncated": 0, "omissions": []}}


def _validate_pack(pack):
    """Validate bounded direct inputs before copies, allocations or disk writes."""
    _shape(pack, {"schema_version", "synthetic", "description", "cases", "labels"})
    if type(pack["schema_version"]) is not int or pack["schema_version"] != 1 or pack["synthetic"] is not True:
        raise ValueError("expected original synthetic version-one fixture")
    _text(pack["description"], 2048)
    _list(pack["cases"], MAX_CASES)
    if not pack["cases"] or type(pack["labels"]) is not dict or len(pack["labels"]) > MAX_CASES:
        raise ValueError("invalid fixture labels")
    seen = set()
    for case in pack["cases"]:
        _shape(case, {"id", "category", "query", "limit", "max_tokens", "files", "contract"})
        ident = _text(case["id"], 80)
        if ident in seen:
            raise ValueError("duplicate case identifier")
        seen.add(ident)
        _text(case["category"], 80)
        _text(case["query"], 1000)
        _integer(case["limit"], 1, 50)
        _integer(case["max_tokens"], 0, 100000)
        _list(case["files"], MAX_CASE_FILES)
        paths, total = set(), 0
        for file in case["files"]:
            if type(file) is not dict:
                raise ValueError("invalid fixture file")
            path = _path(file.get("path"))
            if path.casefold() in paths:
                raise ValueError("duplicate fixture path")
            paths.add(path.casefold())
            total += len(_file_bytes(file))
            if total > MAX_CASE_BYTES:
                raise ValueError("fixture case exceeds byte bound")
        assess_support(_empty_recall(), case["contract"])
        # Contract validation above also bounds every quote and locator.
        for kind in ("requirements", "blockers"):
            for entry in case["contract"][kind]:
                for alternative in entry["alternatives"]:
                    _path(alternative["path"])
        label = pack["labels"].get(ident)
        _shape(label, {"observed_requirement_ids", "observed_blocker_ids", "scope_complete", "output_complete"})
        for key, kind in (("observed_requirement_ids", "requirements"), ("observed_blocker_ids", "blockers")):
            ids = {entry["id"] for entry in case["contract"][kind]}
            values = _list(label[key], 64)
            if any(type(value) is not str or value not in ids for value in values) or len(set(values)) != len(values):
                raise ValueError("invalid expected evidence identifiers")
        if type(label["scope_complete"]) is not bool or type(label["output_complete"]) is not bool:
            raise ValueError("invalid expected completeness")
    if set(pack["labels"]) != seen:
        raise ValueError("fixture case and label identifiers differ")
    if len(_json(pack).encode("utf-8")) > MAX_PACK_BYTES:
        raise ValueError("fixture exceeds JSON byte bound")
    return copy.deepcopy(pack)


def load_pack():
    """Load the repository's authored pack; no caller-supplied filesystem path."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate fixture JSON field")
            result[key] = value
        return result

    def nonfinite(_value):
        raise ValueError("nonfinite fixture number")

    with DEFAULT_PACK.open("rb") as handle:
        raw = handle.read(MAX_PACK_BYTES + 1)
    if len(raw) > MAX_PACK_BYTES:
        raise ValueError("fixture exceeds JSON byte bound")
    try:
        pack = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
        return _validate_pack(pack)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid fixture JSON") from error


def _source_ids(contract, files):
    lines = {path: raw.decode("utf-8").splitlines() for path, raw in files.items()}
    return [entry["id"] for entry in contract["requirements"] if any(
        alt["path"] in lines and alt["line"] <= len(lines[alt["path"]]) and
        alt["quote"] in lines[alt["path"]][alt["line"] - 1]
        for alt in entry["alternatives"])]


def _supported(observed, required_count):
    return (len(observed["observed_requirement_ids"]) == required_count and
            not observed["observed_blocker_ids"] and observed["scope_complete"] and observed["output_complete"])


def evaluate(pack=None, *, control="assessor"):
    """Run real recall once per fresh synthetic vault and compare frozen labels."""
    if control not in CONTROLS:
        raise ValueError("unknown evaluation control")
    pack = load_pack() if pack is None else _validate_pack(pack)
    rows = []
    for case in pack["cases"]:
        files = {file["path"]: _file_bytes(file) for file in case["files"]}
        with tempfile.TemporaryDirectory(prefix="av-recall-support-") as temp:
            vault = Path(temp).resolve()
            config = vault / "00-meta/vault-config.json"
            config.parent.mkdir()
            config.write_text('{"deny_zones": [], "exclude_dirs": [".git"]}', encoding="utf-8")
            for path, raw in files.items():
                target = vault.joinpath(*path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            recalled = recall(vault, case["query"], limit=case["limit"], max_tokens=case["max_tokens"])
        assessment = assess_support(recalled, case["contract"])
        expected = pack["labels"][case["id"]]
        matches_expected = all(assessment[key] == value for key, value in expected.items())
        count = len(case["contract"]["requirements"])
        expected_support = _supported(expected, count)
        claims_support = bool(recalled["matches"]) if control == "naive_any_match" else _supported(assessment, count)
        diagnostics = recalled["diagnostics"]
        manifest = [{"path": path, "bytes": len(raw), "sha256": _sha(raw)} for path, raw in sorted(files.items())]
        rows.append({"case_id": case["id"], "category": case["category"],
                     "configuration": {"query": case["query"], "limit": case["limit"], "max_tokens": case["max_tokens"]},
                     "files": manifest,
                     "binding": {"case_sha256": _digest(case), "expected_sha256": _digest(expected),
                                 "contract_sha256": _digest(case["contract"]), "context_sha256": _sha(recalled["context"].encode("utf-8"))},
                     "source_requirement_ids": _source_ids(case["contract"], files),
                     "required_count": count, "expected": expected, "assessment": assessment,
                     "retrieval": {"match_count": len(recalled["matches"]), "matched_paths": [m["path"] for m in recalled["matches"]],
                                   **{key: diagnostics[key] for key in ("status", "files_read", "bytes_read", "skipped_oversized",
                                                                      "context_matches", "context_truncated", "context_omitted", "scope_state", "answer_state")}},
                     "expectation_matches": matches_expected, "expected_support": expected_support,
                     "claims_support": claims_support, "decision_correct": claims_support == expected_support,
                     "false_assurance": claims_support and not expected_support})
    total = len(rows)
    unsafe = sum(not row["expected_support"] for row in rows)
    false = sum(row["false_assurance"] for row in rows)
    required = sum(row["required_count"] for row in rows)
    observed = sum(len(row["assessment"]["observed_requirement_ids"]) for row in rows)
    summary = {"total": total, "expectations_matched": sum(row["expectation_matches"] for row in rows),
               "correct_decisions": sum(row["decision_correct"] for row in rows),
               "unsafe_to_claim_support_cases": unsafe, "false_assurance_count": false,
               "false_assurance_rate": false / unsafe if unsafe else None,
               "declared_requirements": required, "observed_requirements": observed,
               "evidence_completeness": observed / required if required else None,
               "source_present_but_unobserved_requirements": sum(len(set(row["source_requirement_ids"]) -
                    set(row["assessment"]["observed_requirement_ids"])) for row in rows),
               "scope_complete_cases": sum(row["assessment"]["scope_complete"] for row in rows),
               "output_complete_cases": sum(row["assessment"]["output_complete"] for row in rows)}
    return {"schema_version": 1, "synthetic_fixture": True, "fixture_sha256": _digest(pack),
            "provenance": {"retriever": "vault_recall.recall", "control": control, "fresh_vault_per_case": True,
                           "model_requests": 0, "external_datasets": False},
            "summary": summary, "results": rows,
            "passed": all(row["expectation_matches"] and row["decision_correct"] for row in rows),
            "limitations": ["original_synthetic_fixture_only", "literal_authored_evidence_only", "no_retrieval_improvement_claim",
                            "no_semantic_truth_or_permission_assessment", "fixture_labels_are_not_external_ground_truth"]}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "error: invalid recall support evaluation arguments\n")


def main(argv=None):
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--control", choices=CONTROLS, default="assessor")
    parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args(argv)
    try:
        report = evaluate(control=args.control)
    except (OSError, ValueError, RecursionError, OverflowError):
        print("recall support evaluation unavailable: invalid fixture or recall", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
