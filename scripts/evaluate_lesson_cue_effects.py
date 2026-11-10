#!/usr/bin/env python3
"""Score explicitly supplied lesson-cue observations; never execute a trial.

Default input is an original scripted demonstration, not actual-host evidence.
See docs/lesson-cue-effects.md for the bounded v1 input and measurement limits.
Python 3.10+ standard library only. Error output contains fixed codes, not input.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

MAX_JSON_BYTES = 1024 * 1024
MAX_TRIALS = 300
MAX_SCENARIOS = 100
MAX_ACTIONS = 128
MAX_DEPTH = 20
CONDITIONS = ("baseline", "hint", "guard")
PROVENANCES = ("scripted", "caller_reported_actual_host")
BINDINGS = ("task_sha256", "prompt_sha256", "model_config_sha256", "host_config_sha256",
            "lesson_source_sha256", "compiled_lesson_sha256", "oracle_sha256")
METRICS = ("first_action_correct", "retry_count", "final_task_correct", "false_interruption",
           "elapsed_ms", "input_tokens", "output_tokens")
TIMINGS = ("before_first_decision", "later_decision", "missing_cue", "unknown_timeline")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _fail(code="invalid_input"):
    raise ValueError(code)


def _bounded_tree(value):
    """Apply budgets before hashing/copying even to direct Python API inputs."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 20000 or depth > MAX_DEPTH:
            _fail("input_limit")
        if type(item) is dict:
            if len(item) > 64:
                _fail("input_limit")
            for key, child in item.items():
                if type(key) is not str or not key or len(key) > 128:
                    _fail("invalid_input")
                if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in key):
                    _fail("invalid_input")
                pending.append((child, depth + 1))
        elif type(item) is list:
            if len(item) > MAX_TRIALS:
                _fail("input_limit")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if len(item) > 4096:
                _fail("input_limit")
            if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in item):
                _fail("invalid_input")
        elif item is None or type(item) is bool:
            pass
        elif type(item) in (int, float):
            if not -10**12 <= item <= 10**12 or not math.isfinite(item):
                _fail("invalid_input")
        else:
            _fail("invalid_input")
    if len(_json(value).encode("utf-8")) > MAX_JSON_BYTES:
        _fail("input_limit")


def _shape(value, required, optional=()):
    if (type(value) is not dict or not set(required) <= set(value) or
            set(value) - set(required) - set(optional)):
        _fail()


def _identifier(value):
    if type(value) is not str or ID_RE.fullmatch(value) is None:
        _fail()
    return value


def _hash(value):
    if type(value) is not str or HASH_RE.fullmatch(value) is None:
        _fail()
    return value


def _number(value, integer=False, minimum=0):
    if ((type(value) is not int if integer else type(value) not in (int, float)) or
            not minimum <= value <= 10**12 or not math.isfinite(value)):
        _fail()
    return value


def _bindings(value):
    _shape(value, BINDINGS)
    for key in BINDINGS:
        _hash(value[key])


def _id_list(value):
    if type(value) is not list or not 1 <= len(value) <= 32:
        _fail()
    for item in value:
        _identifier(item)
    if len(set(value)) != len(value):
        _fail()


def _scenario(value):
    _shape(value, ("id", "bindings", "oracle"))
    _identifier(value["id"])
    _bindings(value["bindings"])
    oracle = value["oracle"]
    _shape(oracle, ("id", "expected_first_actions", "acceptable_final_outcomes", "interruption_expected"))
    _identifier(oracle["id"])
    _id_list(oracle["expected_first_actions"])
    _id_list(oracle["acceptable_final_outcomes"])
    if type(oracle["interruption_expected"]) is not bool:
        _fail()
    if value["bindings"]["oracle_sha256"] != _digest(oracle):
        _fail()


def _trial(value, scenario):
    required = ("id", "scenario_id", "pair_id", "condition", "provenance", "bindings")
    optional = ("cue", "actions", "final_task_check", "interrupted", "elapsed_ms", "usage", "teacher_label")
    _shape(value, required, optional)
    for key in ("id", "scenario_id", "pair_id"):
        _identifier(value[key])
    if value["condition"] not in CONDITIONS or value["provenance"] not in PROVENANCES:
        _fail()
    _bindings(value["bindings"])
    label = value.get("teacher_label")
    if label is not None and (type(label) is not str or len(label) > 256):
        _fail()
    actions = value.get("actions")
    if actions is not None:
        if type(actions) is not list or len(actions) > MAX_ACTIONS:
            _fail()
        last_decision, last_attempt, last_ms = 0, 1, 0
        for action in actions:
            _shape(action, ("action_id", "decision_index", "attempt_index", "at_ms"))
            _identifier(action["action_id"])
            _number(action["decision_index"], integer=True, minimum=1)
            _number(action["attempt_index"], integer=True, minimum=1)
            _number(action["at_ms"])
            if (action["decision_index"] != last_decision + 1 or
                    action["attempt_index"] not in (last_attempt, last_attempt + 1) or
                    (last_decision == 0 and action["attempt_index"] != 1) or
                    action["at_ms"] < last_ms):
                _fail()
            last_decision, last_attempt, last_ms = (action["decision_index"],
                                                   action["attempt_index"], action["at_ms"])
        # A recorded sequence begins at the first attempted decision. Partial
        # transcripts must use null, not omit a failed earlier action.
        if actions and actions[0]["decision_index"] != 1:
            _fail()
    cue = value.get("cue")
    if value["condition"] == "baseline" and cue is not None:
        _fail()
    timing = "no_cue" if value["condition"] == "baseline" else "missing_cue"
    if cue is not None:
        _shape(cue, ("kind", "shown_at_ms", "decision_index"))
        if cue["kind"] != value["condition"]:
            _fail()
        _number(cue["shown_at_ms"])
        _number(cue["decision_index"], integer=True, minimum=1)
        timing = "before_first_decision" if cue["decision_index"] == 1 else "later_decision"
        if actions is None:
            timing = "unknown_timeline"
        else:
            # A declared first-available decision must actually occur in the
            # complete action sequence, in the interval the cue names. A future
            # index cannot lend outcome credit to an already completed trial.
            decision = cue["decision_index"]
            if decision > len(actions):
                _fail()
            target_time = actions[decision - 1]["at_ms"]
            previous_time = actions[decision - 2]["at_ms"] if decision > 1 else 0
            if not previous_time <= cue["shown_at_ms"] <= target_time:
                _fail()
    final = value.get("final_task_check")
    if final is not None:
        _shape(final, ("oracle_sha256", "outcome_id", "checked_at_ms"))
        _hash(final["oracle_sha256"])
        _identifier(final["outcome_id"])
        _number(final["checked_at_ms"])
        if actions and final["checked_at_ms"] < actions[-1]["at_ms"]:
            _fail()
        if cue is not None and cue["shown_at_ms"] > final["checked_at_ms"]:
            _fail()
    interrupted = value.get("interrupted")
    if interrupted is not None and type(interrupted) is not bool:
        _fail()
    elapsed = value.get("elapsed_ms")
    if elapsed is not None:
        _number(elapsed)
        observed_times = ([a["at_ms"] for a in actions] if actions else [])
        if final is not None:
            observed_times.append(final["checked_at_ms"])
        if cue is not None:
            observed_times.append(cue["shown_at_ms"])
        if any(time > elapsed for time in observed_times):
            _fail()
    usage = value.get("usage")
    if usage is not None:
        _shape(usage, (), ("input_tokens", "output_tokens"))
        for tokens in usage.values():
            if tokens is not None:
                _number(tokens, integer=True)
    metrics = dict.fromkeys(METRICS)
    oracle = scenario["oracle"]
    if actions:
        metrics["first_action_correct"] = actions[0]["action_id"] in oracle["expected_first_actions"]
        metrics["retry_count"] = actions[-1]["attempt_index"] - 1
    if final is not None and final["oracle_sha256"] == scenario["bindings"]["oracle_sha256"]:
        metrics["final_task_correct"] = final["outcome_id"] in oracle["acceptable_final_outcomes"]
    if interrupted is not None:
        metrics["false_interruption"] = interrupted and not oracle["interruption_expected"]
    metrics["elapsed_ms"] = elapsed
    if usage is not None:
        metrics["input_tokens"] = usage.get("input_tokens")
        metrics["output_tokens"] = usage.get("output_tokens")
    mismatched = [key for key in BINDINGS if value["bindings"][key] != scenario["bindings"][key]]
    stale = any(key in mismatched for key in ("lesson_source_sha256", "compiled_lesson_sha256"))
    if final is not None and final["oracle_sha256"] != scenario["bindings"]["oracle_sha256"]:
        stale = True
    confounded = any(key not in ("lesson_source_sha256", "compiled_lesson_sha256") for key in mismatched)
    status = "confounded" if confounded else "stale" if stale else "valid"
    known = sum(value is not None for value in metrics.values())
    return {"status": status, "timing": timing, "metrics": metrics,
            "observation_state": "missing" if known == 0 else "complete" if known == len(METRICS) else "partial"}


def _blank_metrics():
    return dict.fromkeys(METRICS)


def evaluate_trials(pack):
    """Return deterministic observations and paired deltas; never access files.

    The independent oracle and all observations are caller supplied. Labels,
    digests and this structural checker cannot authenticate host execution.
    Malformed pack/scenario raises a sanitized ValueError. Malformed individual
    trial becomes an invalid row, retaining a bounded safe grouping envelope.
    """
    _bounded_tree(pack)
    _shape(pack, ("schema_version", "scenarios", "trials"))
    if type(pack["schema_version"]) is not int or pack["schema_version"] != 1:
        _fail("unsupported_schema")
    if (type(pack["scenarios"]) is not list or not 1 <= len(pack["scenarios"]) <= MAX_SCENARIOS or
            type(pack["trials"]) is not list or len(pack["trials"]) > MAX_TRIALS):
        _fail("invalid_pack")
    scenarios = {}
    for scenario in pack["scenarios"]:
        try:
            _scenario(scenario)
            if scenario["id"] in scenarios:
                _fail()
        except ValueError:
            _fail("invalid_scenario")
        scenarios[scenario["id"]] = scenario
    rows, groups = [], {}
    ids = Counter()
    provenance_counts = dict.fromkeys(PROVENANCES, 0)
    for index, trial in enumerate(pack["trials"]):
        # Only safe IDs survive an invalid record. Never echo raw actions,
        # prompt/configuration, oracle outcomes, teacher labels or path values.
        envelope = {}
        if type(trial) is dict:
            for field in ("id", "scenario_id", "pair_id"):
                candidate = trial.get(field)
                if type(candidate) is str and ID_RE.fullmatch(candidate):
                    envelope[field] = candidate
            for field, choices in (("condition", CONDITIONS), ("provenance", PROVENANCES)):
                if trial.get(field) in choices:
                    envelope[field] = trial[field]
        row = {"index": index, "id": envelope.get("id"), "scenario_id": envelope.get("scenario_id"),
               "pair_id": envelope.get("pair_id"), "condition": envelope.get("condition"),
               "provenance": envelope.get("provenance"), "status": "invalid", "timing": "unknown",
               "observation_state": "invalid", "metrics": _blank_metrics(), "reason": "invalid_trial",
               "record_sha256": _digest(trial), "bindings": None}
        try:
            scenario = scenarios.get(envelope.get("scenario_id"))
            if scenario is None:
                _fail()
            row.update(_trial(trial, scenario))
            row["bindings"] = dict(trial["bindings"])
            row["reason"] = None if row["status"] == "valid" else "binding_mismatch"
        except ValueError:
            pass
        if row["id"] is not None:
            ids[row["id"]] += 1
        if row["provenance"] is not None:
            provenance_counts[row["provenance"]] += 1
        rows.append(row)
        if row["pair_id"] is not None:
            groups.setdefault(row["pair_id"], []).append(row)
    for row in rows:
        if row["status"] != "invalid" and ids[row["id"]] > 1:
            row["status"], row["reason"] = "confounded", "duplicate_trial_id"
    accum = {p: {c: {t: {m: [] for m in METRICS} for t in TIMINGS} for c in ("hint", "guard")}
             for p in PROVENANCES}
    pairs = []
    for pair_id, members in groups.items():
        by_condition = {condition: [r for r in members if r["condition"] == condition]
                        for condition in CONDITIONS}
        conditions_ok = all(len(values) == 1 for values in by_condition.values())
        statuses = {row["status"] for row in members}
        provenances = {row["provenance"] for row in members}
        scenario_ids = {row["scenario_id"] for row in members}
        if ("confounded" in statuses or len(provenances) != 1 or len(scenario_ids) != 1 or
                any(len(values) > 1 for values in by_condition.values())):
            status = "confounded"
        elif "invalid" in statuses:
            status = "contains_invalid"
        elif "stale" in statuses:
            status = "contains_stale"
        elif not conditions_ok:
            status = "unpaired"
        elif any(row["observation_state"] != "complete" or row["timing"] == "missing_cue" for row in members):
            status = "missing_observations"
        else:
            status = "eligible"
        provenance = next(iter(provenances)) if len(provenances) == 1 else None
        comparisons = {}
        for condition in ("hint", "guard"):
            target = by_condition[condition][0] if len(by_condition[condition]) == 1 else None
            baseline = by_condition["baseline"][0] if len(by_condition["baseline"]) == 1 else None
            timing = target["timing"] if target is not None else "unknown"
            deltas = _blank_metrics()
            if status in ("eligible", "missing_observations") and timing in ("before_first_decision", "later_decision"):
                for metric in METRICS:
                    before, after = baseline["metrics"][metric], target["metrics"][metric]
                    if before is not None and after is not None:
                        if metric == "first_action_correct" and timing != "before_first_decision":
                            continue
                        deltas[metric] = after - before
                        accum[provenance][condition][timing][metric].append(deltas[metric])
            comparisons[condition] = {"timing": timing, "deltas": deltas}
        pairs.append({"pair_id": pair_id, "scenario_id": next(iter(scenario_ids)) if len(scenario_ids) == 1 else None,
                      "provenance": provenance, "status": status, "trial_count": len(members),
                      "comparisons": comparisons})
    summary = {p: {c: {t: {m: {"n": len(values),
                               "mean_delta": sum(values) / len(values) if values else None}
                           for m, values in metrics.items()}
                       for t, metrics in timings.items()}
                   for c, timings in conditions.items()}
               for p, conditions in accum.items()}
    return {"schema_version": 1, "authority": "observation_scoring_not_causal_efficacy",
            "input_sha256": _digest(pack), "authenticity_verified": False, "provenance_counts": provenance_counts,
            "trial_status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
            "pair_status_counts": dict(sorted(Counter(pair["status"] for pair in pairs).items())),
            "trials": rows, "pairs": pairs, "summary": summary,
            "limits": ["Scripted observations are not actual-host evidence.",
                       "Caller-reported actual-host labels and hashes are not authenticated.",
                       "Oracles and observed outcomes require independent human review.",
                       "Within-pair deltas describe supplied observations, not causal or statistical efficacy.",
                       "Sample sizes count measured pairs per metric; unknown values remain null.",
                       "Cue timing is caller recorded; no execution or semantic outcome verification occurs."]}


def _safe_path(path):
    try:
        raw = os.fspath(path)
    except TypeError:
        _fail("unsafe_input_path")
    if type(raw) is not str or not raw or len(raw) > 4096:
        _fail("unsafe_input_path")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("//") or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in normalized):
        _fail("unsafe_input_path")
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isascii() and normalized[0].isalpha():
        # Drive-relative C:foo depends on hidden per-drive working directories.
        if len(normalized) < 3 or normalized[2] != "/":
            _fail("unsafe_input_path")
        normalized = normalized[2:]
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³")
    for component in normalized.split("/"):
        if component in ("", ".", ".."):
            continue
        if (any(c in component for c in ':<>"|?*') or component.endswith((".", " ")) or
                component.split(".")[0].upper() in reserved):
            _fail("unsafe_input_path")
    result = Path(raw).absolute()
    for component in (result, *result.parents):
        try:
            info = component.lstat()
        except OSError:
            _fail("input_unreadable")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("unsafe_input_path")
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _json_depth(raw):
    depth, in_string, escaped = 0, False, False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                _fail("input_limit")
        elif char in "]}":
            depth -= 1


def load_trials(path):
    """Read one explicitly named local regular JSON file, bounded to 1 MiB.

    Reject network/device/stream paths and existing symlink/reparse components.
    No path or action referenced *inside* a record is opened or executed.
    """
    safe = _safe_path(path)
    descriptor = None
    try:
        before = safe.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail("unsafe_input_path")
        if before.st_size > MAX_JSON_BYTES:
            _fail("input_limit")
        descriptor = os.open(safe, os.O_RDONLY | getattr(os, "O_BINARY", 0) |
                             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _stamp(before) != _stamp(info):
            _fail("unsafe_input_path")
        if info.st_size > MAX_JSON_BYTES:
            _fail("input_limit")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(MAX_JSON_BYTES + 1)
            after = os.fstat(handle.fileno())
        final = safe.lstat()
        if (_stamp(info) != _stamp(after) or _stamp(after) != _stamp(final) or
                info.st_ctime_ns != after.st_ctime_ns or before.st_ctime_ns != final.st_ctime_ns):
            _fail("unsafe_input_path")
        _safe_path(safe)
        if len(raw) > MAX_JSON_BYTES:
            _fail("input_limit")
        decoded = raw.decode("utf-8")
        _json_depth(decoded)
        value = json.loads(decoded, object_pairs_hook=_unique_object, parse_int=_json_number,
                           parse_float=_json_number,
                           parse_constant=lambda _: _fail("invalid_json"))
    except (OSError, UnicodeError, RecursionError, json.JSONDecodeError):
        _fail("input_unreadable_or_invalid_json")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _bounded_tree(value)
    return value


def _stamp(info):
    # Windows lstat/fstat expose creation/change time differently; compare
    # change times only across matching kinds of stat observations above.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


def _json_number(token):
    if len(token) > 32:
        _fail("input_limit")
    result = float(token) if any(char in token for char in ".eE") else int(token)
    if not -10**12 <= result <= 10**12 or not math.isfinite(result):
        _fail("invalid_input")
    return result


def demo_pack():
    """Original scripted controls; deliberately includes failures and late cues."""
    oracle = {"id": "independent-demo-oracle", "expected_first_actions": ["inspect-snapshot"],
              "acceptable_final_outcomes": ["snapshot-verified"], "interruption_expected": False}
    bindings = {"task_sha256": _digest("scripted-demo-task"), "prompt_sha256": _digest("same-demo-prompt"),
                "model_config_sha256": _digest("scripted-no-model"),
                "host_config_sha256": _digest("scripted-no-host"),
                "lesson_source_sha256": _digest("inspect before changing"),
                "compiled_lesson_sha256": _digest("scripted compiled cue"), "oracle_sha256": _digest(oracle)}
    scenario = {"id": "demo-snapshot", "bindings": bindings, "oracle": oracle}
    trials = []
    # A positive pattern, an ineffectual cue, a late cue, missing observations,
    # stale compilation and an unpaired trial make the defaults falsifiable.
    for pair, control in (("demo-benefit", "benefit"), ("demo-no-effect", "no-effect"),
                          ("demo-late", "late"), ("demo-missing", "missing"),
                          ("demo-stale", "stale"), ("demo-unpaired", "unpaired")):
        for condition in CONDITIONS:
            if control == "unpaired" and condition != "baseline":
                continue
            right = condition != "baseline" and control != "no-effect"
            cue = None if condition == "baseline" else {"kind": condition, "shown_at_ms": 5,
                                                         "decision_index": 1}
            # The existing PreToolUse hint reaches the next model request; it
            # cannot change the triggering action. Keep that boundary visible
            # even in a fictional positive-pattern demonstration.
            if cue is not None and (condition == "hint" or control == "late"):
                cue.update(shown_at_ms=25, decision_index=2)
            trial = {"id": pair + "-" + condition, "scenario_id": scenario["id"], "pair_id": pair,
                     "condition": condition, "provenance": "scripted", "bindings": dict(bindings),
                     "cue": cue, "actions": [{"action_id": "inspect-snapshot" if right else "change-first",
                                               "decision_index": 1, "attempt_index": 1, "at_ms": 20}],
                     "final_task_check": {"oracle_sha256": bindings["oracle_sha256"],
                                          "outcome_id": "snapshot-verified", "checked_at_ms": 80},
                     "interrupted": condition == "guard" and control == "no-effect",
                     "elapsed_ms": 100 if condition == "baseline" else 110,
                     "usage": {"input_tokens": 100 if condition == "baseline" else 120,
                               "output_tokens": 30}, "teacher_label": "ignored-demo-label"}
            if cue is not None and cue["decision_index"] == 2:
                trial["actions"].append({"action_id": "inspect-snapshot", "decision_index": 2,
                                          "attempt_index": 1, "at_ms": 40})
            if control == "missing":
                for field in ("actions", "final_task_check", "interrupted", "elapsed_ms", "usage"):
                    trial[field] = None
            if control == "stale" and condition == "hint":
                trial["bindings"]["compiled_lesson_sha256"] = "0" * 64
            trials.append(trial)
    return {"schema_version": 1, "scenarios": [scenario], "trials": trials}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _fail("invalid_arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--input", help="explicit local observation JSON; default is scripted demo")
    parser.add_argument("--json", action="store_true", help="emit full deterministic report")
    try:
        args = parser.parse_args(argv)
        report = evaluate_trials(load_trials(args.input) if args.input is not None else demo_pack())
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
        else:
            print("Lesson cue observations: scoring only; no host or model execution.")
            print("Scripted trials:", report["provenance_counts"]["scripted"])
            print("Caller-reported actual-host trials (unauthenticated):",
                  report["provenance_counts"]["caller_reported_actual_host"])
            print("Pair states:", _json(report["pair_status_counts"]))
            print("Use --json for per-metric sample sizes, timing strata and unknown values.")
            print("Observed deltas do not establish causal efficacy.")
        return 0
    except ValueError as error:
        # Only fixed codes generated here can reach stderr, even if a lower
        # level decoder raises an unexpected ValueError containing input text.
        allowed = {"input_limit", "invalid_input", "unsupported_schema", "invalid_pack", "invalid_scenario",
                   "unsafe_input_path", "input_unreadable", "duplicate_json_key", "invalid_json",
                   "input_unreadable_or_invalid_json", "invalid_arguments"}
        code = str(error) if str(error) in allowed else "invalid_input"
        print("lesson_cue_effects_error:" + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
