#!/usr/bin/env python3
"""Opt-in, read-only evidence from one Claude Code session transcript.

``vault_doctor.py --session-evidence PATH`` reads only the named JSONL file: no
directory scan, no write, no network or model call, and the doctor exit code
never changes. The transcript is a host-internal, undocumented format, so only
the record shapes observed on Claude Code 2.1.252 to 2.1.309 are read, whatever
version wrote them; a record of another shape is counted, never guessed.

A transcript holds every prompt, answer, tool result and hook output. Only fixed
status words, counts, lengths, numbers and booleans leave this module: never
content, stdout, stderr, commands, user text, identifiers, version strings as
written, the transcript path or exception text. Instruction file paths appear
only with ``include_paths``, because they can contain account names.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
from pathlib import Path


SCHEMA_VERSION = 1
TRUST_BOUNDARY = "host_internal_format_undocumented"

# Bounds on the work. One long session's transcript reached 213 MB, and one
# SessionStart record carried about 632,000 characters of hook stdout.
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
DISCARD_CHUNK_BYTES = 1024 * 1024
MAX_LISTED_RECORDS = 50
MAX_PATHS_PER_RECORD = 50
MAX_PATH_CHARS = 1024
MAX_COMMAND_CHARS = 8192
MAX_INTEGER = 2 ** 53 - 1  # the largest integer a JavaScript host writes exactly

# Observed on one Windows PC on 2026-10-24 from key names, lengths and numbers
# only: no SessionStart hook_success record in the 173 sessions written by
# 2.1.252, records in the sessions written by 2.1.278 to 2.1.309 (synchronous
# hooks only) and no session from the versions in between. These are observed
# ranges, not thresholds: a transcript without a record is
# unsupported_host_version only when its newest version is at or below the
# version seen without records, and not_recorded otherwise, including the
# unobserved versions in between.
HOOK_RECORDS_ABSENT = ((2, 1, 252), (2, 1, 252))
HOOK_RECORDS_PRESENT = ((2, 1, 278), (2, 1, 309))

# Claude Code 2.1.309 replaces hook stdout longer than 10,000 UTF-16 units with
# "<persisted-output>\nOutput too large (<size>). Full output saved to: <path>
# \n\nPreview (first 2KB):\n<preview>..." ("Output exceeded the ... persist
# limit ..." when even the saved copy is cut). The saved-file path sits between
# the two markers, so only the first one is held to the first 200 characters.
OVERSIZE_MARKERS = ("Output too large (", "Output exceeded the ")
PREVIEW_MARKER = "Preview (first"
NOTICE_HEAD_CHARS = 200
NOTICE_CHARS = 4096

STATUSES = (
    "observed_ran", "observed_truncated", "not_recorded", "unparsed", "unsupported_host_version",
)
_NEXT_ACTIONS = {
    "observed_ran": "none",
    "observed_truncated": "reduce_the_session_start_output_below_the_host_cap",
    "not_recorded": "measure_with_the_debug_log_if_needed",
    "unsupported_host_version": "measure_with_the_debug_log_or_update_claude_code",
    "unparsed": "check_the_named_transcript_file",
}
FIRST_TURN_LABEL = "whole_prompt_not_attributable_to_hooks"
HOOK_FIELDS = (
    "source", "exit_code", "duration_ms", "emitted_chars", "injected_chars", "truncated",
    "command_mentions_session_start_py",
)
_RECORD_TYPES = frozenset(("attachment", "assistant", "system"))
# Lines without this marker are skipped without JSON parsing (user input,
# request blobs, file history). Quotes inside JSON strings are escaped, so
# string content cannot match; a nested object can, and is parsed and dropped.
_LINE_TYPE = re.compile(rb'"type"[ \t]*:[ \t]*"(?:attachment|assistant|system)"')
_MARKER_OVERLAP = 64
_VERSION = re.compile(r"([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})")
_MAX_VERSION_CHARS = 32
_SESSION_START_SOURCES = frozenset(("startup", "resume", "clear", "compact", "fork"))
_INSTRUCTION_REASONS = frozenset(("session_start", "compaction"))
_SESSION_START_SCRIPT = re.compile(r"(?:^|[\\/\s\"'])session_start\.py(?=$|[\s\"'])")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_ID_CHARS = 256


def _utf16_len(text: str) -> int:
    """The length the host measures: UTF-16 code units, as in JavaScript."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _integer(value: object, low: int, high: int = MAX_INTEGER) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _duration(value: object) -> int | float | None:
    if isinstance(value, float):
        return value if math.isfinite(value) and 0 <= value <= MAX_INTEGER else None
    return _integer(value, 0)


def _parse_version(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or len(value) > _MAX_VERSION_CHARS:
        return None
    match = _VERSION.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _version_text(parts: tuple[int, int, int] | None) -> str | None:
    return None if parts is None else ".".join(str(part) for part in parts)


def _truncated(content: str) -> bool:
    """True when content carries the host's oversize notice, not hook text."""
    head = content[:NOTICE_HEAD_CHARS]
    return (any(marker in head for marker in OVERSIZE_MARKERS)
            and PREVIEW_MARKER in content[:NOTICE_CHARS])


def _source(hook_name: str) -> str:
    event, separator, source = hook_name.partition(":")
    if event == "SessionStart" and separator and source in _SESSION_START_SOURCES:
        return source
    return "other"


def _reason(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, str) and value in _INSTRUCTION_REASONS:
        return value
    return "other"


def _paths(values: list) -> tuple[list[str], int]:
    """Keep plain path strings within the bounds; count the rest as omitted."""
    kept: list[str] = []
    omitted = 0
    for value in values:
        if (len(kept) < MAX_PATHS_PER_RECORD and isinstance(value, str)
                and 0 < len(value) <= MAX_PATH_CHARS and not _CONTROL.search(value)):
            kept.append(value)
        else:
            omitted += 1
    return kept, omitted


def _decode(line: bytes) -> dict | None:
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def _prompt_usage(usage: object) -> dict | None:
    """Prompt-side tokens of one response; missing cache fields count as 0."""
    if not isinstance(usage, dict):
        return None
    values = {}
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        raw = usage.get(key)
        if raw is None and key != "input_tokens":
            raw = 0
        value = _integer(raw, 0)
        if value is None:
            return None
        values[key] = value
    values["prompt_tokens_total"] = sum(values.values())
    return values


def _response_key(record: dict, message: dict) -> tuple[str, str | None] | None:
    """(message.id, requestId): one API response written as several lines."""
    message_id = message.get("id")
    if not isinstance(message_id, str) or not 0 < len(message_id) <= _MAX_ID_CHARS:
        return None
    request_id = record.get("requestId")
    if not isinstance(request_id, str) or len(request_id) > _MAX_ID_CHARS:
        request_id = None
    return message_id, request_id


def _empty_report(status: str, reason_code: str, include_paths: bool) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason_code": reason_code,
        "next_action": _NEXT_ACTIONS[status],
        "trust_boundary": TRUST_BOUNDARY,
        "complete": False,
        "bytes_read": 0,
        "lines": {
            "read": 0, "skipped_without_parsing": 0, "parsed": 0, "malformed": 0,
            "oversized": 0, "oversized_with_record_marker": 0,
        },
        "host_versions": {
            "min": None, "max": None,
            "records_without_version": 0, "records_with_unparsed_version": 0,
        },
        "observed_ranges": {
            "hook_records_absent": {
                "min": _version_text(HOOK_RECORDS_ABSENT[0]),
                "max": _version_text(HOOK_RECORDS_ABSENT[1]),
            },
            "hook_records_present": {
                "min": _version_text(HOOK_RECORDS_PRESENT[0]),
                "max": _version_text(HOOK_RECORDS_PRESENT[1]),
            },
        },
        "session_start_hooks": {
            "records": 0, "truncated": 0, "unrecognized": 0, "other_records": 0, "entries": [],
        },
        "instructions": {
            "records": 0, "files": 0, "removed_files": 0, "unrecognized": 0,
            "paths_included": include_paths, "entries": [],
        },
        "first_turn_usage": None,
        "api_errors": {"records": 0, "status_codes": {}, "without_status": 0},
        "limits": {
            "file_bytes": MAX_FILE_BYTES,
            "line_bytes": MAX_LINE_BYTES,
            "listed_records": MAX_LISTED_RECORDS,
            "paths_per_record": MAX_PATHS_PER_RECORD,
            "path_chars": MAX_PATH_CHARS,
            "notice_head_chars": NOTICE_HEAD_CHARS,
            "notice_chars": NOTICE_CHARS,
        },
    }


class _Scan:
    """Allowlisted values from one pass over a transcript; never record content."""

    def __init__(self, include_paths: bool) -> None:
        self.include_paths = include_paths
        self.bytes_read = 0
        self.file_cap_reached = False
        self.lines = {
            "read": 0, "skipped_without_parsing": 0, "parsed": 0, "malformed": 0,
            "oversized": 0, "oversized_with_record_marker": 0,
        }
        self.records = 0
        self.lowest: tuple[int, int, int] | None = None
        self.highest: tuple[int, int, int] | None = None
        self.without_version = 0
        self.unparsed_version = 0
        self.hooks = {"records": 0, "truncated": 0, "unrecognized": 0, "other_records": 0}
        self.hook_entries: list[dict] = []
        self.instructions = {"records": 0, "files": 0, "removed_files": 0, "unrecognized": 0}
        self.instruction_entries: list[dict] = []
        self.first_key: tuple[str, str | None] | None = None
        self.first_usage: dict | None = None
        self.first_closed = False
        self.duplicate_lines = 0
        self.api_errors = {"records": 0, "without_status": 0}
        self.status_codes: dict[str, int] = {}

    def _discard(self, handle, line: bytes) -> bool:
        """Skip the rest of an oversized line; True when it named a read record type."""
        marked = _LINE_TYPE.search(line) is not None
        tail = line[-_MARKER_OVERLAP:]
        while not line.endswith(b"\n"):
            budget = MAX_FILE_BYTES - self.bytes_read
            if budget <= 0:
                if handle.read(1):
                    self.file_cap_reached = True
                break
            line = handle.readline(min(DISCARD_CHUNK_BYTES, budget))
            if not line:
                break
            self.bytes_read += len(line)
            if not marked:
                joined = tail + line
                marked = _LINE_TYPE.search(joined) is not None
                # The last bytes of the stream, however short the chunks are.
                tail = joined[-_MARKER_OVERLAP:]
        return marked

    def _lines(self, handle):
        """Yield whole lines within both caps; count and skip the others unparsed."""
        while True:
            budget = MAX_FILE_BYTES - self.bytes_read
            if budget <= 0:
                if handle.read(1):
                    self.file_cap_reached = True
                return
            limit = min(MAX_LINE_BYTES + 1, budget)
            line = handle.readline(limit)
            if not line:
                return
            self.bytes_read += len(line)
            self.lines["read"] += 1
            if len(line) > MAX_LINE_BYTES:
                self.lines["oversized"] += 1
                if self._discard(handle, line):
                    self.lines["oversized_with_record_marker"] += 1
                continue
            if not line.endswith(b"\n") and len(line) == budget and handle.read(1):
                # The file cap ended the read inside this line: it is not read whole.
                self.file_cap_reached = True
                return
            yield line

    def read(self, handle) -> None:
        for line in self._lines(handle):
            if not _LINE_TYPE.search(line):
                self.lines["skipped_without_parsing"] += 1
                continue
            record = _decode(line)
            if record is None:
                self.lines["malformed"] += 1
                continue
            self.lines["parsed"] += 1
            kind = record.get("type")
            if not isinstance(kind, str) or kind not in _RECORD_TYPES:
                continue
            self.records += 1
            self._version(record.get("version"))
            if kind == "attachment":
                self._attachment(record.get("attachment"))
            elif kind == "assistant":
                self._assistant(record)
            else:
                self._system(record)

    def _version(self, value: object) -> None:
        if value is None:
            self.without_version += 1
            return
        parts = _parse_version(value)
        if parts is None:
            self.unparsed_version += 1
            return
        if self.lowest is None or parts < self.lowest:
            self.lowest = parts
        if self.highest is None or parts > self.highest:
            self.highest = parts

    def _attachment(self, attachment: object) -> None:
        if not isinstance(attachment, dict):
            return
        kind = attachment.get("type")
        session_start = attachment.get("hookEvent") == "SessionStart"
        if kind == "hook_success" and session_start:
            self._hook(attachment)
        elif kind == "instructions":
            self._instructions(attachment)
        elif session_start:
            # Other SessionStart outcomes (errors, cancellations) are not interpreted.
            self.hooks["other_records"] += 1

    def _hook(self, attachment: dict) -> None:
        name = attachment.get("hookName")
        stdout = attachment.get("stdout")
        content = attachment.get("content")
        if not (isinstance(name, str) and isinstance(stdout, str) and isinstance(content, str)):
            self.hooks["unrecognized"] += 1
            return
        truncated = _truncated(content)
        self.hooks["records"] += 1
        self.hooks["truncated"] += int(truncated)
        if len(self.hook_entries) >= MAX_LISTED_RECORDS:
            return
        command = attachment.get("command")
        mentions = (
            _SESSION_START_SCRIPT.search(command[:MAX_COMMAND_CHARS]) is not None
            if isinstance(command, str) else None
        )
        self.hook_entries.append({
            "source": _source(name),
            "exit_code": _integer(attachment.get("exitCode"), -MAX_INTEGER),
            "duration_ms": _duration(attachment.get("durationMs")),
            "emitted_chars": _utf16_len(stdout),
            "injected_chars": _utf16_len(content),
            "truncated": truncated,
            "command_mentions_session_start_py": mentions,
        })

    def _instructions(self, attachment: dict) -> None:
        files = attachment.get("files")
        removed = attachment.get("removed")
        removed = [] if removed is None else removed
        if not isinstance(files, list) or not isinstance(removed, list):
            self.instructions["unrecognized"] += 1
            return
        changed = attachment.get("changed")
        self.instructions["records"] += 1
        self.instructions["files"] += len(files)
        self.instructions["removed_files"] += len(removed)
        if len(self.instruction_entries) >= MAX_LISTED_RECORDS:
            return
        entry = {
            "reason": _reason(attachment.get("reason")),
            "files": len(files),
            "removed_files": len(removed),
            "changed": changed if isinstance(changed, bool) else None,
        }
        if self.include_paths:
            paths, omitted = _paths(
                [item.get("path") if isinstance(item, dict) else None for item in files]
            )
            removed_paths, removed_omitted = _paths(removed)
            entry.update(paths=paths, removed_paths=removed_paths,
                         paths_omitted=omitted + removed_omitted)
        self.instruction_entries.append(entry)

    def _assistant(self, record: dict) -> None:
        """Find the file's first API response; later responses are not read."""
        if self.first_closed:
            return
        message = record.get("message")
        if (not isinstance(message, dict) or record.get("isSidechain") is True
                or record.get("isApiErrorMessage") is True
                or message.get("model") == "<synthetic>"):
            return
        key = _response_key(record, message)
        if key is None:
            return
        if self.first_key is None:
            usage = _prompt_usage(message.get("usage"))
            if usage is not None:
                self.first_key = key
                self.first_usage = usage
        elif key == self.first_key:
            # Claude Code writes one line per content block of a response.
            self.duplicate_lines += 1
        else:
            self.first_closed = True

    def _system(self, record: dict) -> None:
        if record.get("subtype") != "api_error":
            return
        self.api_errors["records"] += 1
        error = record.get("error")
        status = _integer(error.get("status"), 100, 599) if isinstance(error, dict) else None
        if status is None:
            self.api_errors["without_status"] += 1
        else:
            code = str(status)
            self.status_codes[code] = self.status_codes.get(code, 0) + 1

    def classify(self) -> tuple[str, str, bool]:
        """Return status, reason_code and whether the records were interpreted."""
        if self.records == 0:
            return "unparsed", "no_transcript_records", False
        if self.highest is None:
            reason = "version_unparsed" if self.unparsed_version else "version_missing"
            return "unparsed", reason, False
        if self.hooks["records"]:
            if self.hooks["truncated"]:
                return "observed_truncated", "session_start_output_truncated", True
            return "observed_ran", "session_start_hook_recorded", True
        if self.hooks["unrecognized"]:
            return "unparsed", "hook_record_shape_unrecognized", True
        if self.highest <= HOOK_RECORDS_ABSENT[1]:
            return "unsupported_host_version", "host_version_seen_without_hook_records", True
        return "not_recorded", "no_session_start_hook_record", True

    def report(self) -> dict:
        status, reason_code, interpreted = self.classify()
        report = _empty_report(status, reason_code, self.include_paths)
        report["complete"] = not (
            self.file_cap_reached or self.lines["malformed"]
            or self.lines["oversized_with_record_marker"]
        )
        report["bytes_read"] = self.bytes_read
        report["lines"] = dict(self.lines)
        report["host_versions"].update(
            min=_version_text(self.lowest),
            max=_version_text(self.highest),
            records_without_version=self.without_version,
            records_with_unparsed_version=self.unparsed_version,
        )
        if not interpreted:
            # No usable version: the shapes are unknown, so nothing is guessed.
            return report
        report["session_start_hooks"] = {**self.hooks, "entries": self.hook_entries}
        report["instructions"] = {
            **self.instructions, "paths_included": self.include_paths,
            "entries": self.instruction_entries,
        }
        if self.first_usage is not None:
            report["first_turn_usage"] = {
                "label": FIRST_TURN_LABEL, **self.first_usage,
                "duplicate_lines": self.duplicate_lines,
            }
        report["api_errors"] = {
            **self.api_errors, "status_codes": dict(sorted(self.status_codes.items())),
        }
        return report


def _open_regular(path: Path):
    """Open the named file for binary reading, or return a reason code."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None, "not_a_regular_file"
        # O_NONBLOCK: a FIFO swapped in after the check cannot block the open.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None, "file_not_found"
    except (OSError, ValueError):
        return None, "file_unreadable"
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None, "not_a_regular_file"
        return os.fdopen(descriptor, "rb"), None
    except OSError:
        os.close(descriptor)
        return None, "file_unreadable"


def diagnose_session_evidence(path: str | os.PathLike, *, include_paths: bool = False) -> dict:
    """Report what one named transcript records about the session's start.

    Opt-in and fail-soft: never raises for an ordinary error and never puts an
    exception message or the path in the report. A SessionStart record shows
    that the host ran a hook, not that the model used what it injected; a
    missing record is ``not_recorded``, never "not run".
    """
    try:
        handle, reason_code = _open_regular(Path(path))
        if handle is None:
            return _empty_report("unparsed", reason_code, include_paths)
        scan = _Scan(include_paths)
        with handle:
            scan.read(handle)
        return scan.report()
    except Exception:  # fail-soft by contract: no exception text, which may quote content
        return _empty_report("unparsed", "probe_failed", include_paths)


def _text(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def text_lines(report: dict) -> list[str]:
    """The doctor's plain-text lines: the same allowlisted values as the JSON."""
    versions = report["host_versions"]
    hooks = report["session_start_hooks"]
    instructions = report["instructions"]
    usage = report["first_turn_usage"]
    errors = report["api_errors"]
    lines = [
        f"session_evidence: {report['status']} ({report['reason_code']})",
        f"session_trust_boundary: {report['trust_boundary']}",
        f"session_complete: {_text(report['complete'])}",
        f"session_host_versions: min={_text(versions['min'])}; max={_text(versions['max'])}",
        f"session_start_hook_records: {hooks['records']}; truncated={hooks['truncated']}; "
        f"unrecognized={hooks['unrecognized']}; other_records={hooks['other_records']}",
    ]
    for entry in hooks["entries"]:
        lines.append(
            "session_start_hook: " + "; ".join(f"{key}={_text(entry[key])}" for key in HOOK_FIELDS)
        )
    lines.append(
        f"session_instruction_records: {instructions['records']}; files={instructions['files']}; "
        f"removed_files={instructions['removed_files']}; unrecognized={instructions['unrecognized']}"
    )
    for entry in instructions["entries"]:
        lines.append(
            f"session_instruction: reason={entry['reason']}; files={entry['files']}; "
            f"removed_files={entry['removed_files']}; changed={_text(entry['changed'])}"
        )
        for path in entry.get("paths", ()):
            lines.append(f"session_instruction_path: {path}")
        for path in entry.get("removed_paths", ()):
            lines.append(f"session_instruction_removed_path: {path}")
    if usage is None:
        lines.append("session_first_turn_prompt_tokens: none")
    else:
        lines.append(
            f"session_first_turn_prompt_tokens: {usage['prompt_tokens_total']} ({usage['label']})"
        )
    codes = "".join(f"; {code}={count}" for code, count in errors["status_codes"].items())
    lines.append(
        f"session_api_errors: {errors['records']}{codes}; without_status={errors['without_status']}"
    )
    lines.append(f"session_next_action: {report['next_action']}")
    return lines
