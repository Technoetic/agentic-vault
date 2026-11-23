#!/usr/bin/env python3
"""Pre-registered evaluation plan seal and post-hoc amendment dual-verdict verifier.

Selectively adapted from the pre-registered holdout and amendment-disclosure
contract in ai3inc/foldboost (commit ff7ad2208c, Apache-2.0), implemented from
scratch in Python standard library only for agentic-vault evaluation workflows:

1. A pre-registered plan (``agentic-vault/eval-plan/v1``) freezes ``plan_id``,
   ``frozen_utc``, ``claim_limit``, and explicit ``gates`` before measurement,
   sealed by an immutable ``frozen_core_sha256`` plus whole-plan ``plan_sha256``.
2. Post-hoc or pre-measurement ``amendments`` are append-only records carrying
   ``recorded_utc``, ``timing`` (``pre_measurement`` or ``post_hoc``),
   ``what_changed``, ``why``, and explicit ``gate_overrides``. A
   ``pre_measurement`` amendment dated after ``observations.measured_utc`` is
   rejected fail-closed.
3. ``evaluate_plan`` ALWAYS evaluates the original pre-registered gates and
   emits ``original_verdict`` beside ``amended_verdict`` — post-hoc changes can
   never overwrite or hide a pre-registered failure (``post_hoc_rescued_failure``
   flags any case where original failed and amended passed).

Standard library only; zero network, subprocess, or model calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_PLAN = "agentic-vault/eval-plan/v1"
SCHEMA_REPORT = "agentic-vault/eval-plan-verdict/v1"
MAX_JSON_BYTES = 1024 * 1024
MAX_GATES = 128
MAX_AMENDMENTS = 64

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_OPS = frozenset((">=", ">", "<=", "<", "==", "!="))
ALLOWED_TIMINGS = frozenset(("pre_measurement", "post_hoc"))
PLAN_ALLOWED_KEYS = frozenset((
    "schema",
    "plan_id",
    "frozen_utc",
    "claim_limit",
    "gates",
    "amendments",
    "frozen_core_sha256",
    "plan_sha256",
    "notes",
))
GATE_REQUIRED_KEYS = frozenset(("id", "metric", "op", "threshold"))
GATE_OPTIONAL_KEYS = frozenset(("description",))
AMENDMENT_REQUIRED_KEYS = frozenset(("id", "recorded_utc", "timing", "what_changed", "why", "gate_overrides"))
OVERRIDE_KEYS = frozenset(("op", "threshold"))

LIMITATIONS = (
    "Timestamps (frozen_utc, recorded_utc, measured_utc) are caller declarations, not cryptographic timestamp proofs.",
    "Passing original or amended gates reflects supplied observation metrics only, not statistical proof or independent execution.",
    "Post-hoc amendments are disclosed beside original verdicts; this tool never auto-approves rule changes.",
)


class EvalPlanError(ValueError):
    """Operational or schema validation error for evaluation plans and observations."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise EvalPlanError(f"duplicate_json_key:{key}")
        out[key] = value
    return out


def _reject_nonfinite_constant(token: str) -> Any:
    raise EvalPlanError(f"nonfinite_json_number:{token}")


def _loads_strict(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except EvalPlanError:
        raise
    except (ValueError, RecursionError) as exc:
        raise EvalPlanError(f"invalid_json:{type(exc).__name__}") from exc


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvalPlanError(f"unreadable_file:{path.name}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise EvalPlanError("json_file_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvalPlanError("invalid_utf8") from exc
    data = _loads_strict(text)
    if not isinstance(data, dict):
        raise EvalPlanError("json_root_must_be_object")
    return data


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_iso_utc(raw: Any, field: str) -> dt.datetime:
    if type(raw) is not str or not raw.strip():
        raise EvalPlanError(f"invalid_timestamp:{field}")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise EvalPlanError(f"invalid_timestamp:{field}") from exc
    if parsed.tzinfo is None:
        raise EvalPlanError(f"timestamp_missing_timezone:{field}")
    return parsed.astimezone(dt.timezone.utc)


def _check_threshold(value: Any, field: str) -> Any:
    if type(value) is bool or type(value) is str:
        return value
    if type(value) in (int, float):
        if not math.isfinite(value):
            raise EvalPlanError(f"nonfinite_threshold:{field}")
        return value
    raise EvalPlanError(f"invalid_threshold_type:{field}")


def _check_op_and_threshold(op: Any, threshold: Any, field: str) -> None:
    if type(op) is not str or op not in ALLOWED_OPS:
        raise EvalPlanError(f"invalid_operator:{field}")
    _check_threshold(threshold, field)
    if op in (">=", ">", "<=", "<") and type(threshold) not in (int, float):
        raise EvalPlanError(f"ordering_operator_requires_number:{field}")


def compute_frozen_core_sha256(plan: dict[str, Any]) -> str:
    """Compute canonical SHA-256 over the immutable pre-registered core."""
    core = {
        "schema": plan["schema"],
        "plan_id": plan["plan_id"],
        "frozen_utc": plan["frozen_utc"],
        "claim_limit": plan["claim_limit"],
        "gates": plan["gates"],
    }
    return _canonical_sha256(core)


def compute_plan_sha256(plan: dict[str, Any]) -> str:
    """Compute canonical SHA-256 over the full plan excluding ``plan_sha256``."""
    body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    return _canonical_sha256(body)


def validate_plan(plan: dict[str, Any], *, require_sealed: bool = False) -> dict[str, Any]:
    """Validate pre-registered evaluation plan schema, chronology, and digests."""
    if not isinstance(plan, dict):
        raise EvalPlanError("plan_must_be_object")
    unknown = sorted(set(plan) - PLAN_ALLOWED_KEYS)
    if unknown:
        raise EvalPlanError(f"unknown_plan_keys:{','.join(unknown)}")
    if plan.get("schema") != SCHEMA_PLAN:
        raise EvalPlanError("invalid_plan_schema")

    plan_id = plan.get("plan_id")
    if type(plan_id) is not str or not IDENTIFIER_RE.fullmatch(plan_id):
        raise EvalPlanError("invalid_plan_id")
    frozen_dt = _parse_iso_utc(plan.get("frozen_utc"), "frozen_utc")

    claim_limit = plan.get("claim_limit")
    if type(claim_limit) is not str or not claim_limit.strip():
        raise EvalPlanError("missing_claim_limit")

    notes = plan.get("notes")
    if notes is not None and type(notes) is not str:
        raise EvalPlanError("invalid_notes")

    gates = plan.get("gates")
    if not isinstance(gates, list) or not 1 <= len(gates) <= MAX_GATES:
        raise EvalPlanError("invalid_gates_list")
    gate_ids: set[str] = set()
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            raise EvalPlanError(f"invalid_gate:{index}")
        keys = set(gate)
        if not GATE_REQUIRED_KEYS <= keys or keys - GATE_REQUIRED_KEYS - GATE_OPTIONAL_KEYS:
            raise EvalPlanError(f"invalid_gate_keys:{index}")
        gid = gate["id"]
        if type(gid) is not str or not IDENTIFIER_RE.fullmatch(gid) or gid in gate_ids:
            raise EvalPlanError(f"invalid_or_duplicate_gate_id:{index}")
        gate_ids.add(gid)
        metric = gate["metric"]
        if type(metric) is not str or not IDENTIFIER_RE.fullmatch(metric):
            raise EvalPlanError(f"invalid_gate_metric:{gid}")
        _check_op_and_threshold(gate["op"], gate["threshold"], gid)
        if "description" in gate and type(gate["description"]) is not str:
            raise EvalPlanError(f"invalid_gate_description:{gid}")

    amendments = plan.get("amendments", [])
    if not isinstance(amendments, list) or len(amendments) > MAX_AMENDMENTS:
        raise EvalPlanError("invalid_amendments_list")
    amendment_ids: set[str] = set()
    prev_recorded: dt.datetime | None = None
    for index, amend in enumerate(amendments):
        if not isinstance(amend, dict) or set(amend) != AMENDMENT_REQUIRED_KEYS:
            raise EvalPlanError(f"invalid_amendment_keys:{index}")
        aid = amend["id"]
        if type(aid) is not str or not IDENTIFIER_RE.fullmatch(aid) or aid in amendment_ids:
            raise EvalPlanError(f"invalid_or_duplicate_amendment_id:{index}")
        amendment_ids.add(aid)
        rec_dt = _parse_iso_utc(amend["recorded_utc"], f"amendments[{aid}].recorded_utc")
        if rec_dt < frozen_dt:
            raise EvalPlanError(f"amendment_before_freeze:{aid}")
        if prev_recorded is not None and rec_dt < prev_recorded:
            raise EvalPlanError(f"amendments_not_chronological:{aid}")
        prev_recorded = rec_dt
        if amend["timing"] not in ALLOWED_TIMINGS:
            raise EvalPlanError(f"invalid_amendment_timing:{aid}")
        for text_key in ("what_changed", "why"):
            if type(amend[text_key]) is not str or not amend[text_key].strip():
                raise EvalPlanError(f"missing_amendment_{text_key}:{aid}")
        overrides = amend["gate_overrides"]
        if not isinstance(overrides, dict) or not overrides:
            raise EvalPlanError(f"empty_gate_overrides:{aid}")
        for target_gid, spec in overrides.items():
            if target_gid not in gate_ids:
                raise EvalPlanError(f"unknown_override_gate:{aid}:{target_gid}")
            if not isinstance(spec, dict) or set(spec) != OVERRIDE_KEYS:
                raise EvalPlanError(f"invalid_override_spec:{aid}:{target_gid}")
            _check_op_and_threshold(spec["op"], spec["threshold"], f"{aid}:{target_gid}")

    actual_core = compute_frozen_core_sha256(plan)
    actual_plan = compute_plan_sha256(plan)
    claimed_core = plan.get("frozen_core_sha256")
    claimed_plan = plan.get("plan_sha256")

    if claimed_core is not None:
        if type(claimed_core) is not str or not HASH_RE.fullmatch(claimed_core):
            raise EvalPlanError("invalid_frozen_core_sha256")
        if claimed_core != actual_core:
            raise EvalPlanError("frozen_core_digest_mismatch")
    elif require_sealed:
        raise EvalPlanError("unsealed_plan:missing_frozen_core_sha256")

    if claimed_plan is not None:
        if type(claimed_plan) is not str or not HASH_RE.fullmatch(claimed_plan):
            raise EvalPlanError("invalid_plan_sha256")
        if claimed_plan != actual_plan:
            raise EvalPlanError("plan_digest_mismatch")
    elif require_sealed:
        raise EvalPlanError("unsealed_plan:missing_plan_sha256")

    sealed = claimed_core == actual_core and claimed_plan == actual_plan
    return {
        "valid": True,
        "sealed": sealed,
        "plan_id": plan_id,
        "frozen_utc": plan["frozen_utc"],
        "frozen_core_sha256": actual_core,
        "plan_sha256": actual_plan,
        "gate_count": len(gates),
        "amendment_count": len(amendments),
        "post_hoc_amendment_count": sum(1 for a in amendments if a["timing"] == "post_hoc"),
    }


def seal_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``plan`` sealed with ``frozen_core_sha256`` and ``plan_sha256``."""
    stripped = {k: v for k, v in plan.items() if k not in ("frozen_core_sha256", "plan_sha256")}
    validate_plan(stripped, require_sealed=False)
    sealed = dict(stripped)
    sealed["frozen_core_sha256"] = compute_frozen_core_sha256(sealed)
    sealed["plan_sha256"] = compute_plan_sha256(sealed)
    return sealed


def _eval_gate(gate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    gid = gate["id"]
    metric_name = gate["metric"]
    op = gate["op"]
    threshold = gate["threshold"]
    if metric_name not in metrics:
        return {
            "id": gid,
            "metric": metric_name,
            "op": op,
            "threshold": threshold,
            "observed": None,
            "passed": False,
            "reason": "missing_metric",
        }
    observed = metrics[metric_name]
    if type(observed) in (int, float) and not math.isfinite(observed):
        raise EvalPlanError(f"nonfinite_observed_metric:{metric_name}")
    if op in (">=", ">", "<=", "<"):
        if type(observed) not in (int, float):
            return {
                "id": gid,
                "metric": metric_name,
                "op": op,
                "threshold": threshold,
                "observed": observed,
                "passed": False,
                "reason": "type_mismatch",
            }
        if op == ">=":
            passed = observed >= threshold
        elif op == ">":
            passed = observed > threshold
        elif op == "<=":
            passed = observed <= threshold
        else:
            passed = observed < threshold
    else:
        # Strict type check so bool(True) never equals int(1).
        same_family = (
            (type(observed) is type(threshold))
            or (type(observed) in (int, float) and type(threshold) in (int, float))
        )
        if not same_family:
            passed = op == "!="
        else:
            passed = (observed == threshold) if op == "==" else (observed != threshold)
    return {
        "id": gid,
        "metric": metric_name,
        "op": op,
        "threshold": threshold,
        "observed": observed,
        "passed": bool(passed),
        "reason": "ok" if passed else "threshold_unmet",
    }


def evaluate_plan(
    plan: dict[str, Any],
    observations: dict[str, Any],
    *,
    require_sealed: bool = False,
) -> dict[str, Any]:
    """Evaluate observations against both original pre-registered and amended gates."""
    info = validate_plan(plan, require_sealed=require_sealed)
    if not isinstance(observations, dict):
        raise EvalPlanError("observations_must_be_object")
    metrics = observations.get("metrics")
    if not isinstance(metrics, dict):
        raise EvalPlanError("missing_observation_metrics")

    measured_utc = observations.get("measured_utc")
    measured_dt: dt.datetime | None = None
    if measured_utc is not None:
        measured_dt = _parse_iso_utc(measured_utc, "observations.measured_utc")

    amendments = plan.get("amendments", [])
    if measured_dt is not None:
        for amend in amendments:
            rec_dt = _parse_iso_utc(amend["recorded_utc"], f"amendments[{amend['id']}].recorded_utc")
            if amend["timing"] == "pre_measurement" and rec_dt > measured_dt:
                raise EvalPlanError(f"pre_measurement_amendment_after_measurement:{amend['id']}")

    original_results = [_eval_gate(gate, metrics) for gate in plan["gates"]]
    original_failed = [item["id"] for item in original_results if not item["passed"]]
    original_verdict = "passed" if not original_failed else "failed"

    amended_gates = [dict(gate) for gate in plan["gates"]]
    by_id = {gate["id"]: gate for gate in amended_gates}
    for amend in amendments:
        for target_gid, spec in amend["gate_overrides"].items():
            by_id[target_gid]["op"] = spec["op"]
            by_id[target_gid]["threshold"] = spec["threshold"]

    amended_results = [_eval_gate(gate, metrics) for gate in amended_gates]
    amended_failed = [item["id"] for item in amended_results if not item["passed"]]
    amended_verdict = "passed" if not amended_failed else "failed"

    has_post_hoc = any(a["timing"] == "post_hoc" for a in amendments)
    verdict_diverged = original_verdict != amended_verdict
    post_hoc_rescued_failure = has_post_hoc and original_verdict == "failed" and amended_verdict == "passed"

    report: dict[str, Any] = {
        "schema": SCHEMA_REPORT,
        "plan_id": info["plan_id"],
        "frozen_utc": info["frozen_utc"],
        "measured_utc": measured_utc,
        "claim_limit": plan["claim_limit"],
        "sealed": info["sealed"],
        "frozen_core_sha256": info["frozen_core_sha256"],
        "plan_sha256": info["plan_sha256"],
        "original_verdict": original_verdict,
        "original_failed_gates": original_failed,
        "original_gates": original_results,
        "amended_verdict": amended_verdict,
        "amended_failed_gates": amended_failed,
        "amended_gates": amended_results,
        "amendment_count": len(amendments),
        "has_post_hoc_amendments": has_post_hoc,
        "verdict_diverged": verdict_diverged,
        "disclosure_required": has_post_hoc,
        "post_hoc_rescued_failure": post_hoc_rescued_failure,
        "amendments_applied": [
            {
                "id": a["id"],
                "recorded_utc": a["recorded_utc"],
                "timing": a["timing"],
                "what_changed": a["what_changed"],
                "why": a["why"],
                "overridden_gates": sorted(a["gate_overrides"]),
            }
            for a in amendments
        ],
        "limitations": list(LIMITATIONS),
    }
    if "activation_audit" in observations and isinstance(observations["activation_audit"], dict):
        report["activation_audit_summary"] = {
            "passed": observations["activation_audit"].get("passed"),
            "status": observations["activation_audit"].get("status"),
        }
    return report


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_seal = sub.add_parser("seal", help="Compute frozen_core_sha256 and plan_sha256 for a plan JSON.")
    p_seal.add_argument("--plan", required=True, type=Path)
    p_seal.add_argument("--out", type=Path, default=None)

    p_ver = sub.add_parser("verify", help="Validate plan schema, chronology, and optional/required seals.")
    p_ver.add_argument("--plan", required=True, type=Path)
    p_ver.add_argument("--require-sealed", action="store_true")

    p_eval = sub.add_parser("evaluate", help="Evaluate observations against original and amended gates.")
    p_eval.add_argument("--plan", required=True, type=Path)
    p_eval.add_argument("--observations", required=True, type=Path)
    p_eval.add_argument("--require-sealed", action="store_true")
    p_eval.add_argument(
        "--strict-original",
        action="store_true",
        help="Exit 1 unless the original pre-registered verdict passed, even if an amended verdict passed.",
    )
    p_eval.add_argument("--out", type=Path, default=None)

    args = parser.parse_args(argv)
    try:
        plan = _read_json_file(args.plan)
        if args.command == "seal":
            sealed = seal_plan(plan)
            rendered = json.dumps(sealed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if args.out is not None:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(rendered, encoding="utf-8")
                print(json.dumps({
                    "sealed": True,
                    "plan_id": sealed["plan_id"],
                    "frozen_core_sha256": sealed["frozen_core_sha256"],
                    "plan_sha256": sealed["plan_sha256"],
                }, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                sys.stdout.write(rendered)
            return 0

        if args.command == "verify":
            result = validate_plan(plan, require_sealed=args.require_sealed)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        observations = _read_json_file(args.observations)
        report = evaluate_plan(plan, observations, require_sealed=args.require_sealed)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        if args.strict_original:
            return 0 if report["original_verdict"] == "passed" else 1
        return 0 if report["amended_verdict"] == "passed" else 1
    except EvalPlanError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2 if args.command == "evaluate" else 1


if __name__ == "__main__":
    raise SystemExit(main())
