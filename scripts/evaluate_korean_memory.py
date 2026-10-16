#!/usr/bin/env python3
"""Offline Korean synthetic memory controls and explicitly bound response replay.

This measures exact bounded assertions, not free-form QA or an official benchmark.
The deterministic answerer parses MEMORY_FACT records from returned context only.
No model, network, shell tool, or user vault is accessed by the CLI.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "korean_memory"
SCRIPT_DIR = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_CORPUS_BYTES = 8 * 1024 * 1024
ASSERTION_FIELDS = {"entity", "attribute", "value", "scope", "condition", "source"}
PUBLIC_FIELDS = {"entity", "attribute", "scope", "condition"}
CONTROLS = ("good", "wrong", "stale", "unconditional")
_SOURCE = re.compile(r"^\[Source: (.+?)(?::[0-9]+)?\]$")


def _retrieval_module():
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        spec = importlib.util.spec_from_file_location("vault_retrieval", SCRIPT_DIR / "vault_retrieval.py")
        module = sys.modules.get("vault_retrieval")
        if module is None:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT_DIR))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    if path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON input is unsafe or exceeds byte limit")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def _text(value, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field} must be bounded nonempty text")
    return value


def _path(value) -> str:
    value = _text(value, "source", 1024)
    if not value.endswith(".md") or any(c in value for c in '\\:*?"<>|') or any(
            part in ("", ".", "..") or part.rstrip(" .") != part for part in value.split("/")):
        raise ValueError("source must be a safe relative Markdown path")
    return value


def _assertion(value) -> dict:
    if not isinstance(value, dict) or set(value) != ASSERTION_FIELDS:
        raise ValueError("assertion requires exactly entity/attribute/value/scope/condition/source")
    for field in ("entity", "attribute", "value"):
        _text(value[field], field)
    if value["scope"] not in ("current", "historical"):
        raise ValueError("scope must be current or historical")
    if value["condition"] is not None:
        _text(value["condition"], "condition")
    _path(value["source"])
    return copy.deepcopy(value)


def validate_response(response) -> dict:
    """Validate the entire bounded response; prose and partial assertions are errors."""
    if not isinstance(response, dict) or set(response) != {"status", "assertions"}:
        raise ValueError("response requires exactly status and assertions")
    assertions = response["assertions"]
    if response["status"] not in ("answer", "abstain") or not isinstance(assertions, list) or len(assertions) > 32:
        raise ValueError("invalid response status or assertion bound")
    if bool(assertions) != (response["status"] == "answer"):
        raise ValueError("answer requires assertions; abstain requires no assertions")
    checked = [_assertion(item) for item in assertions]
    if len({_json(item) for item in checked}) != len(checked):
        raise ValueError("duplicate assertions")
    if len(_json(response).encode("utf-8")) > 64 * 1024:
        raise ValueError("response exceeds byte limit")
    return {"status": response["status"], "assertions": checked}


def load_pack(fixture: Path = DEFAULT_FIXTURE) -> dict:
    """Read an explicitly synthetic fixture pack; never accept a CLI vault path."""
    fixture = Path(fixture)
    pack = _read_json(fixture / "pack.json")
    if not isinstance(pack, dict) or pack.get("synthetic") is not True or pack.get("schema_version") != 1:
        raise ValueError("expected version-1 synthetic evaluation pack")
    cases, labels = pack.get("cases"), pack.get("labels")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200 or not isinstance(labels, dict):
        raise ValueError("pack requires bounded cases and separate labels")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"id", "scenario", "question", "options"}:
            raise ValueError("invalid public case")
        case_id = _text(case["id"], "id", 128)
        if case_id in seen:
            raise ValueError("duplicate case id")
        seen.add(case_id)
        _text(case["question"], "question", 2048)
        if case["scenario"] not in ("current", "historical", "conditional", "no_answer", "withdrawn"):
            raise ValueError("unknown case scenario")
        options = case["options"]
        if not isinstance(options, dict) or set(options) != PUBLIC_FIELDS:
            raise ValueError("invalid public options")
        _assertion({**options, "value": "validation", "source": "validation.md"})
        label = labels.get(case_id)
        if not isinstance(label, dict) or set(label) != {
                "expected_paths", "expected_assertions", "allowed_assertions", "forbidden_current_assertions", "withdrawn_paths"}:
            raise ValueError("missing or invalid case labels")
        for field in ("expected_paths", "withdrawn_paths"):
            _paths(label[field])
        for field in ("expected_assertions", "allowed_assertions", "forbidden_current_assertions"):
            if not isinstance(label[field], list) or len(label[field]) > 32:
                raise ValueError("invalid assertion labels")
            for assertion in label[field]:
                _assertion(assertion)
        if bool(label["expected_paths"]) != bool(label["expected_assertions"]):
            raise ValueError("expected path and answer labels disagree")
    if set(labels) != seen:
        raise ValueError("labels and case ids disagree")
    _paths(pack.get("withdrawn_sources"))
    return pack


def _paths(values) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > 50:
        raise ValueError("invalid source path list")
    checked = [_path(value) for value in values]
    if len({value.casefold() for value in checked}) != len(checked):
        raise ValueError("duplicate source paths")
    return checked


def _context_facts(context: str) -> list[dict]:
    facts, source = [], None
    for line in context.splitlines():
        match = _SOURCE.fullmatch(line)
        if match:
            try:
                source = _path(match[1])
            except ValueError:
                source = None
        elif source is not None and line.startswith("MEMORY_FACT "):
            try:
                record = json.loads(line[len("MEMORY_FACT "):])
                if isinstance(record, dict) and set(record) == ASSERTION_FIELDS - {"source"}:
                    facts.append(_assertion({**record, "source": source}))
            except (ValueError, TypeError, RecursionError):
                pass  # A cut-off record is not an assertion.
    return facts


def synthetic_answer(payload: dict, *, control: str = "good") -> dict:
    """Deterministic parser controls, with no reference-label access or QA claim."""
    if control not in CONTROLS:
        raise ValueError("unknown synthetic control")
    options = payload["options"]
    facts = _context_facts(payload["evidence"])
    matches = [item for item in facts if all(item[key] == options[key] for key in PUBLIC_FIELDS)]
    if control == "stale" and options["scope"] == "current":
        old = [item for item in facts if item["entity"] == options["entity"] and
               item["attribute"] == options["attribute"] and item["scope"] == "historical"]
        if old:
            matches = [{**old[0], "scope": "current"}]
    if not matches:
        return {"status": "abstain", "assertions": []}
    assertion = copy.deepcopy(matches[0])
    if control == "wrong":
        assertion["value"] = "의도적으로 틀린 합성 답"
    if control == "unconditional" and assertion["condition"] is not None:
        assertion["condition"] = None
    return {"status": "answer", "assertions": [assertion]}


def grade_response(response, label: dict, *, evidence_complete: bool, evidence_paths: Sequence[str]) -> dict:
    """Grade exact assertions, preserving temporal scope and condition boundaries."""
    response = validate_response(response)
    actual = response["assertions"]
    required = {_json(item) for item in label["expected_assertions"]}
    allowed = required | {_json(item) for item in label["allowed_assertions"]}
    observed = {_json(item) for item in actual}
    stale_facts = [{key: value for key, value in item.items() if key != "source"}
                   for item in label["forbidden_current_assertions"]]
    stale = sum(item["scope"] == "current" and {key: value for key, value in item.items() if key != "source"} in stale_facts
                for item in actual)
    withdrawn = sum(item["source"] in label["withdrawn_paths"] for item in actual)
    unsupported = sum(item["source"] not in evidence_paths for item in actual)
    abstain = response["status"] == "abstain"
    assessed = not (abstain and not evidence_complete)
    correct = (required <= observed <= allowed and not stale and not withdrawn and not unsupported
               and abstain == (not required)) if assessed else None
    return {"status": "assessed" if assessed else "unassessed", "correct": correct,
            "certified_abstention": bool(correct and abstain and evidence_complete),
            "stale_current_assertion_count": stale, "withdrawn_source_assertion_count": withdrawn,
            "unsupported_source_assertion_count": unsupported,
            "missing_required_assertion_count": len(required - observed),
            "unexpected_assertion_count": len(observed - allowed)}


def _corpus_fingerprints(vault: Path) -> tuple[str, str]:
    """Bind eligible inventory/bytes and policy, respecting synthetic pack deny zones."""
    config_path = vault / "00-meta" / "vault-config.json"
    raw_config = config_path.read_bytes()
    config = json.loads(raw_config)
    deny = config.get("deny_zones", []) + config.get("exclude_dirs", []) + [".git"]
    entries, total = [], 0
    for directory, dirs, files in os.walk(vault, followlinks=False):
        relative = Path(directory).relative_to(vault)
        def excluded(name):
            rel = (relative / name).as_posix()
            return any(rel == zone or rel.startswith(zone + "/") or ("/" not in zone and zone in Path(rel).parts) for zone in deny)
        dirs[:] = sorted(name for name in dirs if not excluded(name) and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            if not name.endswith(".md") or excluded(name):
                continue
            path = Path(directory) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                entries.append([path.relative_to(vault).as_posix(), "unsafe-source"])
                continue
            total += info.st_size
            if total > MAX_CORPUS_BYTES:
                raise ValueError("synthetic corpus exceeds evaluation byte limit")
            entries.append([path.relative_to(vault).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
    return _hash(entries), hashlib.sha256(raw_config).hexdigest()


def _observation(value, binding) -> dict:
    required = {"binding", "response", "origin", "model", "usage", "actual_cost"}
    if not isinstance(value, dict) or set(value) != required or value["binding"] != binding:
        raise ValueError("missing observation, invalid schema, or replay binding mismatch")
    if value["origin"] not in ("synthetic_control", "external_observation", "actual_model"):
        raise ValueError("unknown observation origin")
    if value["model"] is not None:
        _text(value["model"], "model", 256)
    if value["origin"] == "actual_model" and value["model"] is None:
        raise ValueError("actual-model observations require explicit model")
    usage = value["usage"]
    if usage is not None and (not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"} or
                              any(type(number) is not int or not 0 <= number <= 10**9 for number in usage.values())):
        raise ValueError("usage must be observed bounded input/output token counts or null")
    cost = value["actual_cost"]
    if cost is not None and (not isinstance(cost, dict) or set(cost) != {"amount", "currency"} or
                            type(cost["amount"]) not in (int, float) or not math.isfinite(cost["amount"]) or
                            not 0 <= cost["amount"] <= 10**6 or not isinstance(cost["currency"], str) or
                            not re.fullmatch(r"[A-Z]{3}", cost["currency"])):
        raise ValueError("actual_cost must be observed bounded currency amount or null")
    if value["origin"] != "actual_model" and (usage is not None or cost is not None):
        raise ValueError("synthetic/external observations cannot claim model usage or billing")
    checked = copy.deepcopy(value)
    checked["response"] = validate_response(value["response"])
    return checked


def _ratio(count: int, total: int):
    return count / total if total else None


def evaluate(fixture: Path = DEFAULT_FIXTURE, *, control: str = "good", answerer: Callable | None = None,
             observations: dict | None = None, max_tokens: int = 1500, top_k: int = 3,
             backend: str = "bm25", withdrawn_sources: Sequence[str] = ()) -> dict:
    """Run synthetic controls or replay observations against fresh synthetic retrieval.

    Optional callbacks receive only a copied question/options/evidence payload and
    return the strict response schema. They are external observations, never an
    implicit actual-model backend. Replay usage is caller-attested, not verified
    billing. Fresh CLI runs make zero model requests.
    """
    if control not in CONTROLS or type(max_tokens) is not int or not 0 <= max_tokens <= 20_000:
        raise ValueError("invalid control/context budget")
    if type(top_k) is not int or not 1 <= top_k <= 10 or backend not in ("lexical", "bm25", "hybrid"):
        raise ValueError("invalid retrieval configuration")
    if observations is not None and (not isinstance(observations, dict) or answerer is not None):
        raise ValueError("replay requires an observation mapping and no answerer")
    if answerer is not None and not callable(answerer):
        raise ValueError("answerer must be callable")
    pack = load_pack(fixture)
    vault = Path(fixture) / "vault"
    withdrawn = sorted(set(pack["withdrawn_sources"]) | set(_paths(withdrawn_sources)))
    if len(withdrawn) > 50:
        raise ValueError("too many excluded sources")
    corpus_hash, policy_hash = _corpus_fingerprints(vault)
    config = {"evaluator_version": 1, "backend": backend, "top_k": top_k,
              "max_tokens": max_tokens, "withdrawn_sources": withdrawn,
              "policy_sha256": policy_hash, "response_schema": "scoped-assertions-v1"}
    config_hash = _hash(config)
    retrieval = _retrieval_module()
    mode = "observation_replay" if observations is not None else "external_answerer" if answerer else "synthetic_control"
    rows = []
    for case in pack["cases"]:
        label = copy.deepcopy(pack["labels"][case["id"]])
        label["withdrawn_paths"] = sorted(set(label["withdrawn_paths"]) | set(withdrawn))
        started = time.perf_counter()
        query = case["options"]["entity"] + " " + case["options"]["attribute"]
        result = retrieval.retrieve(vault, query, limit=top_k, max_tokens=max_tokens,
                                    backend=backend, excluded_sources=withdrawn)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        diagnostics = result["diagnostics"]
        context = result["context"]
        context_paths = []
        for line in context.splitlines():
            match = _SOURCE.fullmatch(line)
            if match:
                context_paths.append(match[1])
        complete = (diagnostics.get("status") == "ok" and diagnostics.get("search_complete") is True
                    and not diagnostics.get("context_omitted") and not diagnostics.get("context_truncated"))
        payload = {"question": case["question"], "options": copy.deepcopy(case["options"]), "evidence": context}
        binding = {"question_sha256": _hash({"question": payload["question"], "options": payload["options"]}),
                   "evidence_sha256": _hash({"context": context, "paths": context_paths,
                                             "complete": complete, "status": diagnostics.get("status")}),
                   "corpus_sha256": corpus_hash, "config_sha256": config_hash}
        error, response, observation, grade = None, None, None, None
        try:
            if observations is not None:
                observation = _observation(observations.get(case["id"]), binding)
            else:
                response = answerer(copy.deepcopy(payload)) if answerer else synthetic_answer(payload, control=control)
                observation = _observation({"binding": binding, "response": response,
                    "origin": "external_observation" if answerer else "synthetic_control",
                    "model": None, "usage": None, "actual_cost": None}, binding)
            response = observation["response"]
            grade = grade_response(response, label, evidence_complete=complete, evidence_paths=context_paths)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)[:256]}
            response, observation = None, None
        if grade is None:
            grade = {"status": "error", "correct": None, "certified_abstention": False,
                     "stale_current_assertion_count": None, "withdrawn_source_assertion_count": None,
                     "unsupported_source_assertion_count": None}
        rows.append({"id": case["id"], "scenario": case["scenario"], "question": case["question"],
            "evidence": {"retrieved_paths": [item["path"] for item in result["matches"]],
                         "returned_context_paths": context_paths, "expected_paths": label["expected_paths"],
                         "expected_source_hit": bool(set(label["expected_paths"]) & set(context_paths)),
                         "expected_source_recall": _ratio(len(set(label["expected_paths"]) & set(context_paths)), len(label["expected_paths"])),
                         "withdrawn_source_exposure": sorted(set(context_paths) & set(withdrawn)),
                         "complete": complete, "context": context, "diagnostics": diagnostics},
            "answer": grade, "response": response, "observation": observation,
            "execution": {"mode": mode, "status": "error" if error else "ok", "error": error,
                          "retrieval_error": not complete, "retrieval_elapsed_ms": elapsed_ms},
            "cost": {"actual_model_usage": observation["usage"] if observation else None,
                     "actual_model_cost": observation["actual_cost"] if observation else None,
                     "estimated_context_tokens": retrieval._legacy.estimate_tokens(context), "model_request_count": 0}})
    correct = sum(row["answer"]["correct"] is True for row in rows)
    assessed = sum(row["answer"]["status"] == "assessed" for row in rows)
    observed_usage = [row["cost"]["actual_model_usage"] for row in rows if row["cost"]["actual_model_usage"] is not None]
    total_usage = {key: sum(usage[key] for usage in observed_usage) for key in ("input_tokens", "output_tokens")} if len(observed_usage) == len(rows) else None
    return {"benchmark": "synthetic Korean scoped business-memory evaluation; not official benchmark or free-form QA",
        "mode": mode, "control": control if mode == "synthetic_control" else None, "case_count": len(rows),
        "by_scenario": dict(Counter(case["scenario"] for case in pack["cases"])),
        "config": config, "corpus_sha256": corpus_hash,
        "summary": {"answer_correct_count": correct, "answer_assessed_count": assessed,
            "answer_accuracy_assessed": _ratio(correct, assessed), "answer_accuracy_all_cases": _ratio(correct, len(rows)),
            "execution_error_count": sum(row["execution"]["status"] == "error" for row in rows),
            "execution_error_denominator": len(rows), "retrieval_error_count": sum(not row["evidence"]["complete"] for row in rows),
            "evidence_complete_count": sum(row["evidence"]["complete"] for row in rows),
            "evidence_expected_source_hit_count": sum(row["evidence"]["expected_source_hit"] for row in rows),
            "evidence_answerable_denominator": sum(bool(row["evidence"]["expected_paths"]) for row in rows),
            "certified_abstention_count": sum(row["answer"]["certified_abstention"] for row in rows),
            "no_answer_denominator": sum(not row["evidence"]["expected_paths"] for row in rows),
            "stale_current_assertion_count": sum(row["answer"]["stale_current_assertion_count"] or 0 for row in rows),
            "withdrawn_source_assertion_count": sum(row["answer"]["withdrawn_source_assertion_count"] or 0 for row in rows)},
        "cost": {"actual_model_usage": total_usage, "actual_model_cost": None, "model_request_count": 0,
            "observed_usage_case_count": len(observed_usage),
            "estimated_context_tokens": sum(row["cost"]["estimated_context_tokens"] for row in rows),
            "estimate_method": "retrieval Hangul/non-Hangul character coefficients; not tokenizer or billing",
            "usage_provenance": "replay model usage/cost is caller-attested; missing values remain null"},
        "interpretation": "Evidence hits do not imply answer correctness. Abstention is limited to this complete synthetic fixture scan; missing, invalid or incomplete observations are errors/unassessed. Historical values are stale only when asserted as current. No actual-model execution occurs in this CLI.",
        "per_case": rows}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", choices=CONTROLS, default="good")
    parser.add_argument("--max-tokens", type=int, default=1500)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--backend", choices=("lexical", "bm25", "hybrid"), default="bm25")
    parser.add_argument("--observations", type=Path, help="explicit JSON replay mapping: case id -> bound observation")
    parser.add_argument("--write-observations", type=Path, help="export bound responses for explicit replay")
    parser.add_argument("--withdraw-source", action="append", default=[], help="additional synthetic source withdrawal")
    args = parser.parse_args(argv)
    try:
        report = evaluate(control=args.control, max_tokens=args.max_tokens, top_k=args.top_k, backend=args.backend,
                          observations=_read_json(args.observations) if args.observations else None,
                          withdrawn_sources=args.withdraw_source)
        if args.write_observations:
            args.write_observations.write_text(json.dumps({row["id"]: row["observation"] for row in report["per_case"]},
                                                         ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))
    return 1 if report["summary"]["execution_error_count"] or report["summary"]["retrieval_error_count"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
