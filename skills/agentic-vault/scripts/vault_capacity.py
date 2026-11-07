#!/usr/bin/env python3
"""Opt-in, read-only capacity check for the notes an engine reads every session.

``vault_doctor.py --capacity`` compares each configured note with the state and
recall read limits and, when Git history is available, fits a line to the note's
recent committed sizes to estimate when it reaches each limit. It writes nothing,
makes no network or model call and never changes the doctor exit code.

The reach time is a linear extrapolation over the last commits that touched a
note, which is a commit window and not a time window: every value is an estimate.
Only sizes, counts, status words and vault-relative note paths leave this module.
"""
from __future__ import annotations

import math
import re
import statistics
import subprocess
from pathlib import Path

from vault_evidence import EvidenceError, regular
from vault_recall import MAX_FILE_BYTES as RECALL_FILE_BYTES
from vault_state import MAX_BYTES as STATE_READ_BYTES
from vault_state import StateError, _Policy


SCHEMA_VERSION = 1
TRUST_BOUNDARY = "linear_estimate_from_recent_commits_not_a_measurement"
TARGETS = (("handoff", "handoff_note"), ("hot", "hot_note"), ("log", "log_note"),
           ("tasks", "tasks_note"), ("index", "index_note"))
LIMITS = (("state_read", STATE_READ_BYTES), ("recall_file", RECALL_FILE_BYTES))
HISTORY_COMMITS = 40
GIT_TIMEOUT_SECONDS = 5.0
WARN_USAGE_PERCENT = 80.0
WARN_DAYS = 14.0
SECONDS_PER_DAY = 86400.0
REDUCE_ACTION = "reduce_the_note_or_review_the_budget"
_LOG_LINE = re.compile(rb"([0-9a-f]{40,64}) ([0-9]{1,12})")
_BLOB_LINE = re.compile(rb"blob ([0-9]{1,15})")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_RUN_FAILURES = {"git_unavailable", "git_timeout", "git_history_unavailable"}


class _HistoryError(Exception):
    """Content-free Git history failure code."""


def _run_git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    command = ["git", "--literal-pathspecs", "-c", "core.quotepath=false", *args]
    options = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        result = subprocess.run(
            command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **options,
        )
    except subprocess.TimeoutExpired as exc:
        raise _HistoryError("git_timeout") from exc
    except (OSError, ValueError) as exc:
        raise _HistoryError("git_unavailable") from exc
    if result.returncode != 0:
        raise _HistoryError("git_history_unavailable")
    return result.stdout


def _size_samples(root: Path, relative: str) -> list[tuple[int, int]]:
    """Read (commit time, blob size) pairs with one log and one cat-file call."""
    log = _run_git(root, "log", f"-n{HISTORY_COMMITS}", "--format=%H %ct", "--", relative)
    commits = [match.groups() for match in map(_LOG_LINE.fullmatch, log.splitlines()) if match]
    if not commits:
        return []
    # `./` keeps the object name relative to a vault inside a larger repository.
    names = "".join(f"{commit.decode('ascii')}:./{relative}\n" for commit, _ in commits)
    sizes = _run_git(root, "cat-file", "--batch-check=%(objecttype) %(objectsize)",
                     stdin=names.encode("utf-8")).splitlines()
    if len(sizes) != len(commits):
        raise _HistoryError("history_unreadable")
    samples = []
    for (_, moment), line in zip(commits, sizes):
        # A commit that removed the note names no blob.
        match = _BLOB_LINE.fullmatch(line)
        if match:
            samples.append((int(moment), int(match.group(1))))
    return samples


def _fit(samples: list[tuple[int, int]]) -> tuple[str, float | None]:
    """Return the history status and, for a growing note, its bytes per day."""
    if len(samples) < 2:
        return "insufficient_history", None
    origin = min(moment for moment, _ in samples)
    days = [(moment - origin) / SECONDS_PER_DAY for moment, _ in samples]
    sizes = [float(size) for _, size in samples]
    try:
        slope = statistics.linear_regression(days, sizes).slope
    except statistics.StatisticsError:
        return "same_commit_time", None
    if not math.isfinite(slope) or slope <= 0:
        return "not_growing", None
    return "estimated", slope


class _History:
    """One Git reader for a report: the first run failure ends every later attempt."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.failure: str | None = None
        self.incomplete = False
        self.cache: dict[str, dict] = {}

    def _read(self, relative: str) -> dict:
        report = {"status": self.failure, "samples": 0, "window_days": None,
                  "slope_bytes_per_day": None, "estimate": True}
        if self.failure is not None:
            return report
        try:
            samples = _size_samples(self.root, relative)
        except _HistoryError as exc:
            code = str(exc)
            report["status"] = code
            if code in _RUN_FAILURES:
                self.failure = code
            else:
                self.incomplete = True
            return report
        status, slope = _fit(samples)
        moments = [moment for moment, _ in samples]
        report.update(status=status, samples=len(samples))
        if moments:
            report["window_days"] = round((max(moments) - min(moments)) / SECONDS_PER_DAY, 1)
        if slope is not None:
            report["slope_bytes_per_day"] = round(slope, 1)
            report["_slope"] = slope
        return report

    def read(self, relative: str) -> dict:
        if relative not in self.cache:
            self.cache[relative] = self._read(relative)
        return self.cache[relative]


def _near_limit(size: int, limit: int) -> bool:
    # Compared unrounded so the 80 percent edge is exact.
    return size * 100.0 >= WARN_USAGE_PERCENT * limit


def _limit_report(name: str, limit: int, size: int, slope: float | None) -> dict:
    over = size > limit
    days = None if over or slope is None else round((limit - size) / slope, 1)
    if over:
        status = "over_limit"
    elif _near_limit(size, limit) or (days is not None and days <= WARN_DAYS):
        status = "warning"
    else:
        status = "ready"
    return {"name": name, "limit_bytes": limit, "used_percent": round(size * 100.0 / limit, 1),
            "status": status, "estimated_days_to_limit": days}


def _printable(relative: object) -> bool:
    return isinstance(relative, str) and not _CONTROL_CHARACTER.search(relative)


def _target(role: str, relative: object, status: str, reason_code: str, next_action: str) -> dict:
    return {"role": role, "path": relative if _printable(relative) else "", "status": status,
            "reason_code": reason_code, "next_action": next_action, "size_bytes": None,
            "limits": [], "history": None}


def _measure(policy: _Policy, history: _History, role: str, relative: object) -> dict:
    if not relative:
        return _target(role, "", "disabled", "path_disabled", "none")
    try:
        if not _printable(relative):
            raise StateError("unsafe_path")
        path = policy.note(relative)
        metadata = path.lstat()
        regular(metadata)
        canonical = path.relative_to(policy.root).as_posix()
    except FileNotFoundError:
        return _target(role, relative, "missing", "note_missing", "none")
    except (StateError, EvidenceError) as exc:
        return _target(role, relative, "unsafe_path", str(exc), "choose_a_vault_local_allowed_note")
    except (OSError, ValueError, RuntimeError):
        return _target(role, relative, "unreadable", "note_unreadable", "restore_note_read_access")

    size = metadata.st_size
    fit = history.read(canonical)
    limits = [_limit_report(name, limit, size, fit.get("_slope")) for name, limit in LIMITS]
    states = {item["status"] for item in limits}
    if "over_limit" in states:
        status, reason, action = "over_limit", "source_byte_limit", REDUCE_ACTION
    elif not states - {"ready"}:
        status, reason, action = "ready", "within_limits", "none"
    elif any(_near_limit(size, limit) for _, limit in LIMITS):
        status, reason, action = "warning", "near_byte_limit", REDUCE_ACTION
    else:
        status, reason, action = "warning", "forecast_within_horizon", REDUCE_ACTION
    report = _target(role, relative, status, reason, action)
    report.update(size_bytes=size, limits=limits,
                  history={key: value for key, value in fit.items() if not key.startswith("_")})
    return report


def _summary(targets: list[dict]) -> tuple[str, str, str]:
    """Return the worst target as status, reason_code and next_action."""
    statuses = {item["status"] for item in targets}
    if "over_limit" in statuses:
        return "fail", "source_byte_limit", REDUCE_ACTION
    if statuses & {"unsafe_path", "unreadable"}:
        reason = "note_path_unsafe" if "unsafe_path" in statuses else "note_unreadable"
        return "fail", reason, "review_capacity_targets"
    if "warning" in statuses:
        near = any(item["reason_code"] == "near_byte_limit" for item in targets)
        return ("warning", "near_byte_limit" if near else "forecast_within_horizon", REDUCE_ACTION)
    return "ready", "within_limits", "none"


def _limits() -> dict:
    return {"state_read_bytes": STATE_READ_BYTES, "recall_file_bytes": RECALL_FILE_BYTES,
            "history_commits": HISTORY_COMMITS}


def unavailable(reason_code: str) -> dict:
    """The report for a check that could not start: fixed words only."""
    return {"schema_version": SCHEMA_VERSION, "status": "unavailable", "reason_code": reason_code,
            "next_action": "repair_vault_config_or_run_inside_a_vault",
            "trust_boundary": TRUST_BOUNDARY, "complete": False, "history": "not_read",
            "limits": _limits(), "targets": []}


def diagnose_capacity(vault: Path) -> dict:
    """Report every configured note against both read limits; never writes."""
    try:
        policy = _Policy(vault)
    except StateError as exc:
        return unavailable(str(exc))
    history = _History(policy.root)
    targets = [_measure(policy, history, role, policy.config.get(key)) for role, key in TARGETS]
    status, reason_code, next_action = _summary(targets)
    measured = all(item["status"] in {"ready", "warning", "over_limit", "missing", "disabled"}
                   for item in targets)
    return {"schema_version": SCHEMA_VERSION, "status": status, "reason_code": reason_code,
            "next_action": next_action, "trust_boundary": TRUST_BOUNDARY,
            "complete": measured and history.failure is None and not history.incomplete,
            "history": history.failure or "available", "limits": _limits(), "targets": targets}


def text_lines(report: dict) -> list[str]:
    """The doctor's plain-text lines: the same values as the JSON report."""
    lines = [
        f"capacity: {report['status']} ({report['reason_code']})",
        f"capacity_next_action: {report['next_action']}",
        f"capacity_history: {report['history']}",
        f"capacity_complete: {str(report['complete']).lower()}",
        f"capacity_trust_boundary: {report['trust_boundary']}",
    ]
    for target in report["targets"]:
        parts = [f"capacity_target: {target['role']} {target['path']}".rstrip(),
                 f"{target['status']} ({target['reason_code']})"]
        if target["size_bytes"] is not None:
            parts.append(f"size={target['size_bytes']}")
        for limit in target["limits"]:
            days = limit["estimated_days_to_limit"]
            parts.append(f"{limit['name']}={limit['used_percent']}%"
                         f" estimated_days={'none' if days is None else days}")
        lines.append("; ".join(parts))
    return lines
