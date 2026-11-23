#!/usr/bin/env python3
"""Treatment activation and control zero-leakage audit (silent no-op guard).

Selectively adapted from the activation-audit contract in ai3inc/foldboost
(commit ff7ad2208c, Apache-2.0: ``launch.py`` ``INACTIVE_EXIT = 70`` and
``analyze_holdout.py`` treatment-active / control-inactive invariants),
implemented in Python standard library only for agentic-vault evaluation
runners:

- Verifies that every declared treatment arm actually fired its candidate code
  path (no silent no-op passing with exit code 0).
- Verifies that every declared control arm recorded zero candidate activity
  (no unintended cross-arm leakage).
- Emits structured audit JSON and uses distinct exit code ``INACTIVE_EXIT = 70``
  when a caller opts into ``--check-activation`` and an activation invariant
  fails.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA = "agentic-vault/activation-audit/v1"
INACTIVE_EXIT = 70


def _extract_observation(item: Mapping[str, Any]) -> tuple[bool, int, int]:
    """Normalize a single run/query activation observation into (active, calls, fallbacks)."""
    if not isinstance(item, Mapping):
        raise ValueError("activation observation must be a mapping")
    if "candidate_calls" in item:
        calls = item["candidate_calls"]
    elif "calls" in item:
        calls = item["calls"]
    else:
        calls = 1 if item.get("active") is True else 0
    if type(calls) is not int or calls < 0:
        raise ValueError("activation calls must be a non-negative integer")
    fallbacks = item.get("fallback_calls", 0)
    if type(fallbacks) is not int or fallbacks < 0:
        raise ValueError("activation fallback_calls must be a non-negative integer")
    if "active" in item:
        if type(item["active"]) is not bool:
            raise ValueError("activation active flag must be boolean")
        active = item["active"]
    else:
        active = calls > 0
    return active, calls, fallbacks


def audit_arm_activation(
    arms: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    treatment_arms: Sequence[str],
    control_arms: Sequence[str],
    require_all_runs: bool = True,
) -> dict[str, Any]:
    """Audit treatment activation and control zero-leakage across evaluation arms."""
    if not isinstance(arms, Mapping):
        raise ValueError("arms must be a mapping")
    t_names = tuple(treatment_arms)
    c_names = tuple(control_arms)
    if not t_names:
        raise ValueError("at least one treatment arm is required")
    overlap = sorted(set(t_names) & set(c_names))
    if overlap:
        raise ValueError(f"arms cannot be both treatment and control: {','.join(overlap)}")

    treatment_summary: dict[str, dict[str, Any]] = {}
    control_summary: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    silent_noop_failed = False
    control_leak_failed = False

    for name in t_names:
        if name not in arms:
            failures.append(f"missing_treatment_arm:{name}")
            silent_noop_failed = True
            treatment_summary[name] = {
                "runs": 0,
                "active_runs": 0,
                "inactive_runs": 0,
                "total_calls": 0,
                "fallback_calls": 0,
                "passed": False,
            }
            continue
        obs = list(arms[name])
        active_runs = 0
        total_calls = 0
        total_fallbacks = 0
        for item in obs:
            active, calls, fallbacks = _extract_observation(item)
            if active:
                active_runs += 1
            total_calls += calls
            total_fallbacks += fallbacks
        runs = len(obs)
        inactive_runs = runs - active_runs
        arm_ok = runs > 0 and (active_runs == runs if require_all_runs else active_runs > 0)
        if not arm_ok:
            silent_noop_failed = True
            if runs == 0:
                failures.append(f"empty_treatment_arm:{name}")
            else:
                failures.append(f"silent_noop_treatment_arm:{name}:{inactive_runs}_of_{runs}_inactive")
        treatment_summary[name] = {
            "runs": runs,
            "active_runs": active_runs,
            "inactive_runs": inactive_runs,
            "total_calls": total_calls,
            "fallback_calls": total_fallbacks,
            "passed": arm_ok,
        }

    for name in c_names:
        if name not in arms:
            failures.append(f"missing_control_arm:{name}")
            control_leak_failed = True
            control_summary[name] = {
                "runs": 0,
                "active_runs": 0,
                "total_calls": 0,
                "fallback_calls": 0,
                "passed": False,
            }
            continue
        obs = list(arms[name])
        active_runs = 0
        total_calls = 0
        total_fallbacks = 0
        for item in obs:
            active, calls, fallbacks = _extract_observation(item)
            if active:
                active_runs += 1
            total_calls += calls
            total_fallbacks += fallbacks
        arm_ok = len(obs) > 0 and active_runs == 0 and total_calls == 0
        if not arm_ok:
            control_leak_failed = True
            if not obs:
                failures.append(f"empty_control_arm:{name}")
            else:
                failures.append(f"control_arm_leakage:{name}:{active_runs}_active_{total_calls}_calls")
        control_summary[name] = {
            "runs": len(obs),
            "active_runs": active_runs,
            "total_calls": total_calls,
            "fallback_calls": total_fallbacks,
            "passed": arm_ok,
        }

    passed = not failures
    if passed:
        status = "passed"
    elif silent_noop_failed and control_leak_failed:
        status = "both_failed"
    elif silent_noop_failed:
        status = "silent_noop"
    else:
        status = "control_leakage"

    return {
        "schema": SCHEMA,
        "passed": passed,
        "status": status,
        "exit_code": 0 if passed else INACTIVE_EXIT,
        "require_all_runs": bool(require_all_runs),
        "treatment_arms": treatment_summary,
        "control_arms": control_summary,
        "failures": failures,
    }


def audit_retrieval_backend_telemetry(
    telemetry: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    optional_backends: Sequence[str] = (),
) -> dict[str, Any]:
    """Audit ``evaluate_retrieval_backends`` telemetry for Hangul2 + optional backends.

    ``telemetry`` maps backend name -> list of per-query records containing
    ``active`` (bool), ``calls`` (int), and optional ``has_hangul`` (bool).
    For ``bm25_hangul2``, every query with Hangul syllables must activate the
    bigram channel while ``bm25`` (control) must have zero bigram activations.
    Any explicitly requested optional backend (e.g. ``kiwi_bm25``, ``qmd_search``)
    must activate across all its queries without silent fallback.
    """
    arms: dict[str, list[dict[str, Any]]] = {}
    treatment: list[str] = []
    control: list[str] = []

    if "bm25" in telemetry:
        arms["bm25"] = [
            {"active": bool(r.get("active")), "calls": int(r.get("calls", 0))}
            for r in telemetry["bm25"]
        ]
        control.append("bm25")

    if "bm25_hangul2" in telemetry:
        hangul_rows = [r for r in telemetry["bm25_hangul2"] if r.get("has_hangul", True)]
        source_rows = hangul_rows if hangul_rows else list(telemetry["bm25_hangul2"])
        arms["bm25_hangul2"] = [
            {"active": bool(r.get("active")), "calls": int(r.get("calls", 0))}
            for r in source_rows
        ]
        treatment.append("bm25_hangul2")

    for opt_name in optional_backends:
        if opt_name in telemetry:
            arms[opt_name] = [
                {"active": bool(r.get("active")), "calls": int(r.get("calls", 0))}
                for r in telemetry[opt_name]
            ]
        treatment.append(opt_name)

    return audit_arm_activation(
        arms,
        treatment_arms=treatment,
        control_arms=control,
        require_all_runs=True,
    )


def audit_tool_matrix_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Audit an ``evaluate_tool_matrix`` report for arm activation and zero leakage.

    Verifies:
    - When ``attacked`` episodes exist, every attacked episode ran an attacked
      scenario while ``clean`` episodes carried zero attacked scenarios.
    - When ``string_patterns`` is enabled, every schema-B episode carried
      schema-B evaluation conditions and pattern verdicts while schema-A
      episodes recorded zero schema-B pattern evaluations.
    """
    episodes = report.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return audit_arm_activation(
            {"matrix_episodes": []},
            treatment_arms=("matrix_episodes",),
            control_arms=(),
            require_all_runs=True,
        )

    arms: dict[str, list[dict[str, Any]]] = {}
    treatment: list[str] = []
    control: list[str] = []

    has_attacked = any(isinstance(ep, dict) and ep.get("condition") == "attacked" for ep in episodes)
    has_clean = any(isinstance(ep, dict) and ep.get("condition") == "clean" for ep in episodes)
    if has_attacked:
        arms["condition_attacked"] = [
            {
                "active": bool(isinstance(ep.get("episode_id"), str) and ep["episode_id"].endswith("-attacked")),
                "calls": 1 if (isinstance(ep.get("episode_id"), str) and ep["episode_id"].endswith("-attacked")) else 0,
            }
            for ep in episodes
            if isinstance(ep, dict) and ep.get("condition") == "attacked"
        ]
        treatment.append("condition_attacked")
    if has_clean:
        arms["condition_clean"] = [
            {
                "active": bool(isinstance(ep.get("episode_id"), str) and ep["episode_id"].endswith("-attacked")),
                "calls": 1 if (isinstance(ep.get("episode_id"), str) and ep["episode_id"].endswith("-attacked")) else 0,
            }
            for ep in episodes
            if isinstance(ep, dict) and ep.get("condition") == "clean"
        ]
        control.append("condition_clean")

    meas = report.get("measurement_conditions")
    has_patterns = isinstance(meas, dict) and "string_patterns" in meas
    if has_patterns:
        b_eps = [ep for ep in episodes if isinstance(ep, dict) and ep.get("schema_variant") == "B"]
        a_eps = [ep for ep in episodes if isinstance(ep, dict) and ep.get("schema_variant") == "A"]
        arms["schema_B_patterns"] = [
            {
                "active": bool(
                    isinstance(ep.get("evaluation_conditions"), dict)
                    and ep["evaluation_conditions"].get("schema_variant") == "B"
                    and isinstance(ep.get("string_argument_verdicts"), list)
                ),
                "calls": max(1, len(ep.get("string_argument_verdicts", ())))
                if (
                    isinstance(ep.get("evaluation_conditions"), dict)
                    and ep["evaluation_conditions"].get("schema_variant") == "B"
                    and isinstance(ep.get("string_argument_verdicts"), list)
                )
                else 0,
            }
            for ep in b_eps
        ]
        treatment.append("schema_B_patterns")
        arms["schema_A_control"] = [
            {
                "active": bool(
                    isinstance(ep.get("evaluation_conditions"), dict)
                    and ep["evaluation_conditions"].get("schema_variant") == "B"
                ),
                "calls": 1
                if (
                    isinstance(ep.get("evaluation_conditions"), dict)
                    and ep["evaluation_conditions"].get("schema_variant") == "B"
                )
                else 0,
            }
            for ep in a_eps
        ]
        control.append("schema_A_control")

    if not treatment:
        arms["executed_episodes"] = [
            {
                "active": bool(isinstance(ep, dict) and ep.get("status") is not None),
                "calls": 1 if (isinstance(ep, dict) and ep.get("status") is not None) else 0,
            }
            for ep in episodes
        ]
        treatment.append("executed_episodes")

    return audit_arm_activation(
        arms,
        treatment_arms=treatment,
        control_arms=control,
        require_all_runs=True,
    )
