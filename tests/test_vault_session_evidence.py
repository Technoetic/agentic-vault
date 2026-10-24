"""Opt-in session evidence of the doctor (--session-evidence).

Every transcript here is synthetic JSONL built from the record shapes of the
2026-10-24 verification record (key names, lengths and numbers only) and the
hook-failure records and oversize notices of the Claude Code 2.1.309
executable; no real transcript is opened. Secrets are planted in every
content-bearing field: none may reach the text or JSON output, and instruction
paths only with the second flag.
"""
from __future__ import annotations

import codecs
import contextlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

try:
    from test_vault_doctor_host_version import (
        NOT_VAULT_REPORT, NOT_VAULT_TEXT, READY_REPORT, READY_TEXT, platform_bytes,
    )
except ImportError:  # run as tests.<module> from the repository root
    from tests.test_vault_doctor_host_version import (
        NOT_VAULT_REPORT, NOT_VAULT_TEXT, READY_REPORT, READY_TEXT, platform_bytes,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
DOCTOR = SCRIPTS / "vault_doctor.py"
MISSING = object()
# Every planted secret carries this prefix; no output may contain it.
SECRET = "SECRET-"
# Instruction paths carry this prefix; only --session-instruction-paths shows them.
PATH_MARK = "PATHMARK-"
PLUGIN_COMMAND = (
    'sh "/SECRET-PLUGIN-ROOT/hooks/run_python_hook.sh" "/SECRET-PLUGIN-ROOT/hooks/session_start.py"'
)
OTHER_COMMAND = 'powershell -NoProfile -File "D:/SECRET-VAULT/00-meta/scripts/session-handoff.ps1"'
SAVED_PATH = "C:/Users/SECRET-ACCOUNT/.claude/projects/d--v/SECRET-SESSION/tool-results/hook-1-stdout.txt"
# Hook text that only quotes the host's notice, as a note about this truncation can.
QUOTED_NOTICE = (
    '=== HOT ===\nM-016: handoff hook stdout was replaced with "Output too large (597.5KB)" '
    'and a "Preview (first 2KB)" only.'
)
# Unicode categories that can end, split or reorder a printed line.
UNSAFE_CATEGORIES = frozenset(("Cc", "Cf", "Cs", "Zl", "Zp"))
# Hook stdout the 2.1.309 host replaces: over 10,000 UTF-16 units once trimmed.
OVER_CAP_STDOUT = "SECRET-STDOUT " + "s" * 20_000 + "\n"
# Another SessionStart hook, as a vault's own settings can add one.
VAULT_HOOK_COMMAND = 'powershell -NoProfile -File "D:/SECRET-VAULT/00-meta/scripts/local-llm-up.ps1"'


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def meta(version: object = "2.1.300") -> dict:
    """Keys every Claude Code record carries; the identifiers are planted secrets."""
    record = {
        "parentUuid": "SECRET-PARENT-UUID",
        "isSidechain": False,
        "userType": "external",
        "cwd": "C:/Users/SECRET-ACCOUNT/vault",
        "sessionId": "SECRET-SESSION-ID",
        "gitBranch": "SECRET-BRANCH",
        "entrypoint": "claude-vscode",
        "timestamp": "2026-10-24T00:00:00.000Z",
        "uuid": "SECRET-RECORD-UUID",
    }
    if version is not MISSING:
        record["version"] = version
    return record


def hook_record(*, stdout: str = "=== HOT ===\nSECRET-STDOUT hot context\n", content: str | None = None,
                source: str = "startup", command: object = PLUGIN_COMMAND,
                version: object = "2.1.300", exit_code: object = 0, duration_ms: object = 812,
                event: str = "SessionStart", kind: str = "hook_success") -> dict:
    """An attachment record of a SessionStart hook; inline output by default."""
    attachment = {
        "type": kind,
        "hookName": f"{event}:{source}",
        "toolUseID": "SECRET-TOOL-USE-ID",
        "hookEvent": event,
        "content": stdout.strip() if content is None else content,
        "stdout": stdout,
        "stderr": "SECRET-STDERR",
        "exitCode": exit_code,
        "command": command,
        "durationMs": duration_ms,
    }
    return {**meta(version), "type": "attachment", "attachment": attachment}


def persisted_notice(stdout: str, saved_path: str = SAVED_PATH, *, size: str = "597.5KB") -> str:
    """Claude Code 2.1.309's oversize notice: the saved-file path sits between the markers."""
    return (
        "<persisted-output>\nOutput too large (" + size + "). Full output saved to: " + saved_path
        + "\n\nPreview (first 2KB):\n" + stdout[:2000] + "\n...\n</persisted-output>"
    )


def not_saved_notice(stdout: str, *, size: str = "18.0KB", shown: str = "9.8KB") -> str:
    """2.1.309 when the output could not be saved: no preview marker, only the first part."""
    return (
        "<truncated-output>\nOutput too large (" + size + "). It could not be saved, so only the "
        "first " + shown + " are shown; the rest was dropped. If the tool can page or filter its "
        "results, call it again for the part you need.\n\n" + stdout[:9800] + "\n</truncated-output>"
    )


def persist_failed_notice(stdout: str, error: str = "SECRET-PERSIST-ERROR C:/Users/SECRET-ACCOUNT") -> str:
    """2.1.309 when saving failed: the first 10,000 units and a one-line notice at the end."""
    return (stdout.strip()[:10_000] + "\n\n[Hook stdout truncated at 10000 chars " + chr(0x2014)
            + " persist-to-disk failed: " + error + "]")


def failure_record(kind: str = "hook_non_blocking_error", *, source: str = "startup",
                   event: str = "SessionStart", version: object = "2.1.300") -> dict:
    """A hook that did not succeed, in the 2.1.309 shape of its type; every text is a secret."""
    shapes = {
        # A non-zero exit: run_python_hook.sh exits 127 when no Python 3.10+ is found.
        "hook_non_blocking_error": {
            "stderr": "SECRET-FAILURE-STDERR", "stdout": "SECRET-FAILURE-STDOUT", "exitCode": 127,
            "command": PLUGIN_COMMAND, "durationMs": 40,
        },
        "hook_blocking_error": {
            "blockingError": {"blockingError": "SECRET-BLOCKING-ERROR", "command": PLUGIN_COMMAND},
        },
        "hook_cancelled": {
            "command": PLUGIN_COMMAND, "durationMs": 60_000, "timedOut": True, "timeoutMs": 60_000,
        },
        "hook_error_during_execution": {
            "content": "Failed to prepare hook input: SECRET-PREPARE-ERROR",
            "command": PLUGIN_COMMAND, "durationMs": 3,
        },
    }
    attachment = {"type": kind, "hookName": f"{event}:{source}", "toolUseID": "SECRET-TOOL-USE-ID",
                  "hookEvent": event, **shapes[kind]}
    return {**meta(version), "type": "attachment", "attachment": attachment}


def blocking_exit_record(*, version: object = "2.1.300") -> dict:
    """2.1.309 rewrites a SessionStart blocking error (exit 2) as a non-blocking one."""
    attachment = {
        "type": "hook_non_blocking_error", "hookName": "SessionStart:startup",
        "toolUseID": "SECRET-TOOL-USE-ID", "hookEvent": "SessionStart",
        "stderr": "SECRET-EXIT-2-STDERR", "stdout": "", "exitCode": 2, "command": OTHER_COMMAND,
    }
    return {**meta(version), "type": "attachment", "attachment": attachment}


def context_record(*, version: object = "2.1.300") -> dict:
    """The additional context of JSON hooks: one SessionStart record for all of them."""
    attachment = {
        "type": "hook_additional_context", "content": ["SECRET-ADDITIONAL-CONTEXT"],
        "hookName": "SessionStart", "toolUseID": "SessionStart", "hookEvent": "SessionStart",
    }
    return {**meta(version), "type": "attachment", "attachment": attachment}


def system_message_record(*, version: object = "2.1.300") -> dict:
    attachment = {
        "type": "hook_system_message", "content": "SECRET-SYSTEM-MESSAGE",
        "hookName": "SessionStart:startup", "toolUseID": "SECRET-TOOL-USE-ID",
        "hookEvent": "SessionStart",
    }
    return {**meta(version), "type": "attachment", "attachment": attachment}


def instructions_record(paths: tuple | list = ("PATHMARK-root/CLAUDE.md",), *,
                        version: object = "2.1.300", **extra: object) -> dict:
    """An instructions attachment: files[] holds content, path and type."""
    attachment: dict = {
        "type": "instructions",
        "files": [
            {"content": "SECRET-INSTRUCTION-TEXT", "path": path, "type": "SECRET-FILE-TYPE"}
            for path in paths
        ],
    }
    attachment.update(extra)
    return {**meta(version), "type": "attachment", "attachment": attachment}


def assistant_record(message_id: str = "msg_SECRET-0001", request_id: object = "req_SECRET-0001", *,
                     usage: object = None, block: dict | None = None, model: str = "SECRET-MODEL",
                     version: object = "2.1.300", **extra: object) -> dict:
    """One content-block line of an API response."""
    if usage is None:
        usage = {"input_tokens": 4, "cache_creation_input_tokens": 7600,
                 "cache_read_input_tokens": 70000, "output_tokens": 9}
    record = {
        **meta(version),
        "type": "assistant",
        "requestId": request_id,
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [block or {"type": "text", "text": "SECRET-ANSWER-TEXT"}],
            "stop_reason": "SECRET-STOP",
            "usage": usage,
        },
    }
    record.update(extra)
    return record


def user_record(content: object = "SECRET-USER-PROMPT", *, version: object = "2.1.300",
                **extra: object) -> dict:
    record = {**meta(version), "type": "user", "message": {"role": "user", "content": content}}
    record.update(extra)
    return record


def api_error_record(status: object = 429, *, version: object = "2.1.300") -> dict:
    error = {
        "connection": "SECRET-CONNECTION",
        "formatted": "SECRET-FORMATTED-ERROR",
        "isNetworkDown": False,
        "message": "SECRET-ERROR-MESSAGE",
        "noResponse": False,
        "rateLimits": {"SECRET-RATE-LIMIT": 1},
        "requestId": "SECRET-ERROR-REQUEST",
    }
    if status is not MISSING:
        error["status"] = status
    return {
        **meta(version), "type": "system", "subtype": "api_error", "level": "error",
        "error": error, "retryInMs": 1000, "retryAttempt": 1, "maxRetries": 10,
        "source": "SECRET-ERROR-SOURCE",
    }


def system_record(subtype: str, *, version: object = "2.1.300") -> dict:
    return {**meta(version), "type": "system", "subtype": subtype,
            "content": "SECRET-SYSTEM-CONTENT", "level": "info"}


def blob_record() -> dict:
    # The quoted text is string content: escaped in JSON, so it is no record marker.
    return {"type": "api-request-blob", "requestId": "SECRET-BLOB-REQUEST",
            "body": 'SECRET-BLOB-BODY "type":"system"'}


def history_record() -> dict:
    return {"type": "file-history-snapshot", "messageId": "SECRET-HISTORY-ID",
            "snapshot": {"trackedFileBackups": {"SECRET-TRACKED-FILE": {}}}}


def jsonl(records: tuple | list, *, compact: bool = True) -> bytes:
    """Host-style JSONL (compact separators); bytes items are written as they are."""
    separators = (",", ":") if compact else None
    out = bytearray()
    for record in records:
        if isinstance(record, bytes):
            out += record
        else:
            out += (json.dumps(record, ensure_ascii=False, separators=separators) + "\n").encode("utf-8")
    return bytes(out)


def load_doctor():
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("doctor_session_evidence_under_test", DOCTOR)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            raise RuntimeError(f"cannot load module: {DOCTOR}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path


class SessionEvidenceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The doctor imports the reader by name only when the option is given.
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        cls.evidence = importlib.import_module("vault_session_evidence")

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def transcript(self, *records: object, compact: bool = True, name: str = "session.jsonl") -> Path:
        path = self.root / "SECRET-TRANSCRIPT-DIR" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jsonl(records, compact=compact))
        return path

    def diagnose(self, *records: object, include_paths: bool = False, compact: bool = True) -> dict:
        path = self.transcript(*records, compact=compact)
        return self.evidence.diagnose_session_evidence(path, include_paths=include_paths)

    def assert_status(self, report: dict, status: str, reason_code: str) -> None:
        self.assertEqual((report["status"], report["reason_code"]), (status, reason_code))
        self.assertEqual(report["trust_boundary"], "host_internal_format_undocumented")
        self.assertIn(report["status"], self.evidence.STATUSES)
        self.assertNotIn(SECRET, json.dumps(report, ensure_ascii=False))


class SessionStartHookTests(SessionEvidenceTestCase):
    def test_inline_output_is_observed_ran_with_lengths_in_utf16_units(self) -> None:
        stdout = "=== HOT ===\nSECRET-STDOUT notes " + chr(0x1F600) + "\n"
        report = self.diagnose(user_record(), hook_record(stdout=stdout))

        self.assert_status(report, "observed_ran", "session_start_hook_recorded")
        self.assertEqual(report["next_action"], "none")
        self.assertTrue(report["complete"])
        self.assertEqual(report["session_start_hooks"]["entries"], [{
            "source": "startup",
            "exit_code": 0,
            "duration_ms": 812,
            "emitted_chars": utf16_units(stdout),
            "injected_chars": utf16_units(stdout.strip()),
            "truncated": False,
            "command_mentions_session_start_py": True,
        }])
        # The astral character counts twice, as in the host and the doctor's estimate.
        self.assertEqual(utf16_units(stdout), len(stdout) + 1)

    def test_oversize_notice_is_observed_truncated(self) -> None:
        stdout = "SECRET-STDOUT " + "가나다라 " * 120_000 + "\n"
        content = persisted_notice(stdout)
        report = self.diagnose(hook_record(stdout=stdout, content=content, command=OTHER_COMMAND))

        self.assert_status(report, "observed_truncated", "session_start_output_truncated")
        self.assertEqual(report["next_action"], "reduce_the_session_start_output_below_the_host_cap")
        hooks = report["session_start_hooks"]
        self.assertEqual((hooks["records"], hooks["truncated"]), (1, 1))
        entry = hooks["entries"][0]
        self.assertEqual(entry["emitted_chars"], utf16_units(stdout))
        self.assertEqual(entry["injected_chars"], utf16_units(content))
        self.assertTrue(entry["truncated"])
        # Another hook than this plugin's session_start.py produced the output.
        self.assertIs(entry["command_mentions_session_start_py"], False)

    def test_a_long_saved_path_still_counts_as_truncated(self) -> None:
        stdout = "SECRET-STDOUT " + "x" * 20_000 + "\n"
        long_path = (
            "C:/Users/SECRET-ACCOUNT/.claude/projects/C--Users-SECRET-ACCOUNT-Documents-GitHub-"
            + "deep-project-name-" * 6 + "/" + "0" * 36 + "/tool-results/hook-" + "1" * 36 + "-stdout.txt"
        )
        content = persisted_notice(stdout, long_path)
        # The path sits between the markers: a 200-character window for both would miss this.
        self.assertLess(content.index("Output too large ("), 200)
        self.assertGreater(content.index("Preview (first"), 200)
        self.assertLess(content.index("Preview (first"), 4096)

        report = self.diagnose(hook_record(stdout=stdout, content=content))

        self.assert_status(report, "observed_truncated", "session_start_output_truncated")

    def test_truncation_needs_both_markers_inside_the_notice(self) -> None:
        def saved(body: str) -> str:
            # The 2.1.309 envelope of a saved output, opened and closed by the host.
            return "<persisted-output>\n" + body + "\n</persisted-output>"

        notice_head = "Output too large (1KB). Full output saved to: p\n\n"
        not_saved = not_saved_notice("body " * 100)
        dash = chr(0x2014)
        failed_line = f"\n\n[Hook stdout truncated at 10000 chars {dash} persist-to-disk failed: EACCES]"
        cases = (
            (saved(notice_head + "Preview (first 2KB):\nbody\n..."), True),
            (saved("Output exceeded the 1GB persist limit; only the first 1GB were saved to: p\n\n"
                   "Preview (first 2KB):\nbody"), True),
            ("=== SESSION HANDOFF ===\nplain hook text", False),
            (saved("x" * 250 + "Output too large (1KB). Preview (first 2KB)"), False),
            (saved("Preview (first 2KB):\nOutput too large"), False),
            (saved("Output too large (1KB). Full output saved to: " + "y" * 5000 + "Preview (first 2KB)"),
             False),
            (saved("Output too large (1KB) without any preview"), False),
            # Saved: only the host's envelope around the whole content counts, not quoted text.
            (QUOTED_NOTICE, False),
            (notice_head + "Preview (first 2KB):\nbody", False),
            ("<persisted-output>\n" + notice_head + "Preview (first 2KB):\nbody", False),
            (notice_head + "Preview (first 2KB):\nbody\n</persisted-output>", False),
            ("hook text first\n" + saved(notice_head + "Preview (first 2KB):\nbody"), False),
            (saved(notice_head + "Preview (first 2KB):\nbody") + "\nmore hook text", False),
            (saved("plain hook text"), False),
            # Not saved: the tag opens the content and the size marker is in its head.
            (not_saved, True),
            ("hook text first\n" + not_saved, False),
            ("<truncated-output>\nplain hook text without the size marker", False),
            ("<truncated-output>\n" + "y" * 300 + "Output too large (1KB)", False),
            # Saving failed: a one-line notice ends the content.
            ("x" * 10_000 + failed_line, True),
            ("short head" + failed_line.replace("EACCES", ""), True),
            ("x" * 10_000 + failed_line + "\nmore hook text", False),
            ("x" * 100 + failed_line + "\n\nA later paragraph that ends with a link [[note]]", False),
            ("x" * 100 + "\n[Hook stdout truncated at 10000 chars - persist-to-disk failed: e]", False),
            ("x" * 100 + "\n\n[Hook stdout truncated at 10000 chars]", False),
            ("x" * 100 + failed_line.replace("EACCES", "e" * 5000), False),
        )
        for content, expected in cases:
            with self.subTest(content=content[:60]):
                # A stdout the host would replace, so only the shape of the content decides.
                self.assertIs(self.evidence._truncated(content, OVER_CAP_STDOUT), expected)

    def test_notices_of_unsaved_output_are_observed_truncated(self) -> None:
        stdout = "SECRET-STDOUT " + "z" * 18_000 + "\n"
        for name, content in (("not_saved", not_saved_notice(stdout)),
                              ("persist_failed", persist_failed_notice(stdout))):
            with self.subTest(form=name):
                report = self.diagnose(hook_record(stdout=stdout, content=content))

                self.assert_status(report, "observed_truncated", "session_start_output_truncated")
                self.assertEqual(report["next_action"],
                                 "reduce_the_session_start_output_below_the_host_cap")
                entry = report["session_start_hooks"]["entries"][0]
                self.assertEqual((entry["emitted_chars"], entry["injected_chars"], entry["truncated"]),
                                 (utf16_units(stdout), utf16_units(content), True))

    def test_hook_text_that_quotes_the_notice_is_not_truncated(self) -> None:
        stdout = QUOTED_NOTICE + "\n"
        report = self.diagnose(hook_record(stdout=stdout))

        self.assert_status(report, "observed_ran", "session_start_hook_recorded")
        self.assertEqual(report["next_action"], "none")
        entry = report["session_start_hooks"]["entries"][0]
        self.assertEqual((entry["emitted_chars"], entry["injected_chars"], entry["truncated"]),
                         (utf16_units(stdout), utf16_units(QUOTED_NOTICE), False))
        self.assertEqual(report["session_start_hooks"]["truncated"], 0)

        # The same text, over the cap and saved by the host, is still truncated.
        long_stdout = QUOTED_NOTICE + "\n" + "x" * 20_000 + "\n"
        saved = self.diagnose(hook_record(stdout=long_stdout, content=persisted_notice(long_stdout)))
        self.assert_status(saved, "observed_truncated", "session_start_output_truncated")
        self.assertIs(saved["session_start_hooks"]["entries"][0]["truncated"], True)

    def test_a_notice_counts_only_when_the_host_could_have_written_it(self) -> None:
        failed_line = ("\n\n[Hook stdout truncated at 10000 chars " + chr(0x2014)
                       + " persist-to-disk failed: EACCES]")
        # Hook text that quotes or carries a notice: the host keeps it as the content.
        unchanged = (
            ("quoted persist-failed line",
             "=== HOT CONTEXT ===\nM-0xx: when saving failed the host left only this line:"
             + failed_line + "\n"),
            ("quoted not-saved notice",
             "<truncated-output>\nOutput too large (18.0KB). It could not be saved, so only the first "
             "9.8KB are shown.\n</truncated-output>\n"),
            ("short output that is a whole saved notice",
             persisted_notice("SECRET-STDOUT " + "y" * 2000) + "\n"),
            # Over the cap, yet the content equals the trimmed stdout: not replaced.
            ("long output that is a whole saved notice",
             "<persisted-output>\nOutput too large (1MB). Full output saved to: p\n\nPreview (first 2KB):\n"
             + "y" * 12_000 + "\n...\n</persisted-output>\n"),
            ("long output that ends with a persist-failed line", "y" * 20_000 + failed_line),
        )
        for name, stdout in unchanged:
            with self.subTest(case=name):
                content = stdout.strip()
                self.assertIs(self.evidence._truncated(content, stdout), False)
                report = self.diagnose(hook_record(stdout=stdout, content=content))
                self.assert_status(report, "observed_ran", "session_start_hook_recorded")
                self.assertEqual(report["next_action"], "none")
                self.assertEqual(report["session_start_hooks"]["truncated"], 0)
        # Replaced by the host: every form still counts.
        for name, content in (("saved", persisted_notice(OVER_CAP_STDOUT)),
                              ("not saved", not_saved_notice(OVER_CAP_STDOUT)),
                              ("persist failed", persist_failed_notice(OVER_CAP_STDOUT))):
            with self.subTest(case=name):
                self.assertIs(self.evidence._truncated(content, OVER_CAP_STDOUT), True)
                report = self.diagnose(hook_record(stdout=OVER_CAP_STDOUT, content=content))
                self.assert_status(report, "observed_truncated", "session_start_output_truncated")

    def test_the_cap_applies_to_the_trimmed_stdout_in_utf16_units(self) -> None:
        notice = persist_failed_notice("x" * 10_000)
        emoji = chr(0x1F600)  # two UTF-16 units
        cases = (
            ("x" * 10_000, False),
            ("x" * 10_001, True),
            ("x" * 10_000 + "\n", False),
            (" " + chr(9) + "x" * 10_000 + chr(13) + chr(10), False),
            # JavaScript trim removes U+FEFF and keeps U+001C and U+0085; Python's strip() differs.
            (chr(0xFEFF) + "x" * 10_000, False),
            (chr(0x1C) + "x" * 10_000, True),
            (chr(0x85) + "x" * 10_000, True),
            (emoji * 5_000, False),
            (emoji * 5_000 + "x", True),
        )
        for stdout, expected in cases:
            with self.subTest(head=hex(ord(stdout[0])), length=len(stdout)):
                self.assertIs(self.evidence._truncated(notice, stdout), expected)

    def test_the_trim_is_javascript_trim(self) -> None:
        space_separators = {chr(code) for code in range(0x110000)
                            if unicodedata.category(chr(code)) == "Zs"}
        # ECMAScript WhiteSpace (TAB, VT, FF, ZWNBSP, Zs) and LineTerminator (LF, CR, LS, PS).
        expected = ({chr(9), chr(11), chr(12), chr(0xFEFF)} | space_separators
                    | {chr(10), chr(13), chr(0x2028), chr(0x2029)})
        self.assertEqual(set(self.evidence._JS_TRIM), expected)
        self.assertEqual(len(self.evidence._JS_TRIM), len(expected))

    def test_sources_other_records_and_other_events(self) -> None:
        sources = ("startup", "resume", "clear", "compact", "fork", "SECRET-SOURCE")
        no_source = hook_record()
        no_source["attachment"]["hookName"] = "SessionStart"
        report = self.diagnose(
            *(hook_record(source=source) for source in sources),
            no_source,
            # A JSON hook: empty content, its context in a separate record, and a system message.
            hook_record(content=""), context_record(), system_message_record(),
            hook_record(event="PreToolUse", source="Bash"),
            failure_record(event="PreToolUse", source="Bash"),
        )

        self.assert_status(report, "observed_ran", "session_start_hook_recorded")
        hooks = report["session_start_hooks"]
        self.assertEqual(
            [entry["source"] for entry in hooks["entries"]],
            ["startup", "resume", "clear", "compact", "fork", "other", "other", "startup"],
        )
        self.assertEqual(hooks["entries"][-1]["injected_chars"], 0)
        # Additional context and system messages are counted, never read as failures.
        self.assertEqual((hooks["records"], hooks["failed"], hooks["other_records"]), (8, 0, 2))
        self.assertEqual(hooks["failed_entries"], [])

        alone = self.diagnose(context_record(), system_message_record())
        self.assert_status(alone, "not_recorded", "no_session_start_hook_record")
        self.assertEqual(alone["session_start_hooks"]["other_records"], 2)

    def test_a_failed_hook_alone_is_observed_error_not_absence(self) -> None:
        cases = (
            (failure_record("hook_non_blocking_error"),
             {"source": "startup", "outcome": "non_blocking_error", "exit_code": 127,
              "duration_ms": 40, "command_mentions_session_start_py": True}),
            (blocking_exit_record(),
             {"source": "startup", "outcome": "non_blocking_error", "exit_code": 2,
              "duration_ms": None, "command_mentions_session_start_py": False}),
            (failure_record("hook_blocking_error", source="clear"),
             {"source": "clear", "outcome": "blocking_error", "exit_code": None, "duration_ms": None,
              "command_mentions_session_start_py": True}),
            (failure_record("hook_cancelled", source="resume"),
             {"source": "resume", "outcome": "cancelled", "exit_code": None, "duration_ms": 60_000,
              "command_mentions_session_start_py": True}),
            (failure_record("hook_error_during_execution", source="compact"),
             {"source": "compact", "outcome": "error_during_execution", "exit_code": None,
              "duration_ms": 3, "command_mentions_session_start_py": True}),
        )
        for record, entry in cases:
            with self.subTest(outcome=entry["outcome"], exit_code=entry["exit_code"]):
                report = self.diagnose(user_record(), record, assistant_record())

                self.assert_status(report, "observed_error", "session_start_hook_failed")
                self.assertEqual(report["next_action"], "check_the_failed_session_start_hook")
                hooks = report["session_start_hooks"]
                self.assertEqual(
                    (hooks["records"], hooks["truncated"], hooks["failed"], hooks["unrecognized"],
                     hooks["other_records"]),
                    (0, 0, 1, 0, 0),
                )
                self.assertEqual(hooks["entries"], [])
                self.assertEqual(hooks["failed_entries"], [entry])
                self.assertNotIn("not_run", json.dumps(report))

    def test_a_failure_comes_before_truncation_and_success(self) -> None:
        stdout = "SECRET-STDOUT " + "x" * 20_000
        report = self.diagnose(
            hook_record(), hook_record(stdout=stdout, content=persisted_notice(stdout)),
            failure_record("hook_cancelled", source="resume"),
        )

        self.assert_status(report, "observed_error", "session_start_hook_failed")
        hooks = report["session_start_hooks"]
        self.assertEqual((hooks["records"], hooks["truncated"], hooks["failed"]), (2, 1, 1))
        self.assertEqual(len(hooks["entries"]), 2)

    def test_failure_fields_outside_the_allowlist_become_none(self) -> None:
        odd = failure_record()
        odd["attachment"].update(exitCode="SECRET-EXIT", durationMs=float("inf"))
        nameless = failure_record("hook_cancelled")
        nameless["attachment"]["hookName"] = ["SECRET-NAME"]
        other_source = failure_record(source="SECRET-SOURCE")

        report = self.diagnose(odd, nameless, other_source)

        self.assert_status(report, "observed_error", "session_start_hook_failed")
        hooks = report["session_start_hooks"]
        self.assertEqual((hooks["failed"], hooks["unrecognized"]), (2, 1))
        self.assertEqual(hooks["failed_entries"], [
            {"source": "startup", "outcome": "non_blocking_error", "exit_code": None,
             "duration_ms": None, "command_mentions_session_start_py": True},
            {"source": "other", "outcome": "non_blocking_error", "exit_code": 127, "duration_ms": 40,
             "command_mentions_session_start_py": True},
        ])
        # Without a usable name the record is counted, never guessed.
        alone = self.diagnose(nameless)
        self.assert_status(alone, "unparsed", "hook_record_shape_unrecognized")

    def test_failure_exit_codes_outside_the_allowlist_become_none(self) -> None:
        cases = (
            (True, None), (False, None), (2 ** 53, None), (-(2 ** 53), None), (1.0, None),
            (2 ** 53 - 1, 2 ** 53 - 1), (-(2 ** 53 - 1), -(2 ** 53 - 1)), (0, 0), (-1, -1),
        )
        for exit_code, expected in cases:
            with self.subTest(exit_code=exit_code):
                record = failure_record()
                record["attachment"]["exitCode"] = exit_code
                entry = self.diagnose(record)["session_start_hooks"]["failed_entries"][0]
                self.assertEqual((entry["exit_code"], type(entry["exit_code"])),
                                 (expected, type(expected)))

    def test_a_failed_entry_says_whether_its_command_names_session_start_py(self) -> None:
        # This plugin's hook ran and another SessionStart hook failed.
        vault_hook = failure_record()
        vault_hook["attachment"]["command"] = VAULT_HOOK_COMMAND
        no_command = failure_record("hook_cancelled")
        del no_command["attachment"]["command"]
        odd_command = failure_record("hook_error_during_execution")
        odd_command["attachment"]["command"] = ["SECRET-COMMAND", "session_start.py"]
        blocking_other = failure_record("hook_blocking_error")
        blocking_other["attachment"]["blockingError"]["command"] = OTHER_COMMAND
        blocking_odd = failure_record("hook_blocking_error")
        blocking_odd["attachment"]["blockingError"] = "SECRET-BLOCKING session_start.py"
        late = failure_record()
        late["attachment"]["command"] = "sh " + "x" * 8192 + " session_start.py"
        report = self.diagnose(
            hook_record(), vault_hook, no_command, odd_command, failure_record("hook_blocking_error"),
            blocking_other, blocking_odd, blocking_exit_record(), late,
        )

        self.assert_status(report, "observed_error", "session_start_hook_failed")
        hooks = report["session_start_hooks"]
        self.assertIs(hooks["entries"][0]["command_mentions_session_start_py"], True)
        self.assertEqual(
            [(entry["outcome"], entry["command_mentions_session_start_py"])
             for entry in hooks["failed_entries"]],
            [("non_blocking_error", False), ("cancelled", None), ("error_during_execution", None),
             ("blocking_error", True), ("blocking_error", False), ("blocking_error", None),
             ("non_blocking_error", False), ("non_blocking_error", False)],
        )

    def test_unrecognized_hook_shapes_are_counted_not_guessed(self) -> None:
        broken = hook_record()
        broken["attachment"]["stdout"] = 123
        alone = self.diagnose(broken)
        self.assert_status(alone, "unparsed", "hook_record_shape_unrecognized")
        self.assertEqual(alone["next_action"], "check_the_named_transcript_file")
        self.assertEqual(alone["session_start_hooks"]["unrecognized"], 1)
        self.assertEqual(alone["session_start_hooks"]["entries"], [])

        mixed = self.diagnose(broken, hook_record())
        self.assert_status(mixed, "observed_ran", "session_start_hook_recorded")
        self.assertEqual(
            (mixed["session_start_hooks"]["records"], mixed["session_start_hooks"]["unrecognized"]),
            (1, 1),
        )

        # Without a stdout the host's own test cannot be applied: never judged truncated.
        no_stdout = hook_record(stdout=OVER_CAP_STDOUT, content=persisted_notice(OVER_CAP_STDOUT))
        del no_stdout["attachment"]["stdout"]
        alone = self.diagnose(no_stdout)
        self.assert_status(alone, "unparsed", "hook_record_shape_unrecognized")
        hooks = alone["session_start_hooks"]
        self.assertEqual((hooks["unrecognized"], hooks["records"], hooks["truncated"]), (1, 0, 0))

    def test_numbers_outside_the_allowlist_become_none(self) -> None:
        cases = (
            ("SECRET-EXIT", "SECRET-DURATION", None, None),
            (True, True, None, None),
            (2 ** 60, -5, None, None),
            (-1, 12.5, -1, 12.5),
            (3221225477, float("nan"), 3221225477, None),
        )
        for exit_code, duration, expected_exit, expected_duration in cases:
            with self.subTest(exit_code=exit_code, duration=duration):
                command = None if exit_code is True else PLUGIN_COMMAND
                report = self.diagnose(
                    hook_record(exit_code=exit_code, duration_ms=duration, command=command)
                )
                entry = report["session_start_hooks"]["entries"][0]
                self.assertEqual(entry["exit_code"], expected_exit)
                self.assertEqual(entry["duration_ms"], expected_duration)
                self.assertEqual(entry["command_mentions_session_start_py"],
                                 None if command is None else True)

    def test_listing_is_bounded_but_counts_are_whole(self) -> None:
        notice = persisted_notice(OVER_CAP_STDOUT)
        records = [hook_record(stdout=OVER_CAP_STDOUT, content=notice) for _ in range(60)]
        records += [failure_record() for _ in range(60)]
        records += [instructions_record() for _ in range(60)]
        report = self.diagnose(*records)

        hooks = report["session_start_hooks"]
        self.assertEqual((hooks["records"], hooks["truncated"], len(hooks["entries"])), (60, 60, 50))
        self.assertEqual((hooks["failed"], len(hooks["failed_entries"])), (60, 50))
        instructions = report["instructions"]
        self.assertEqual((instructions["records"], len(instructions["entries"])), (60, 50))


class HostVersionRangeTests(SessionEvidenceTestCase):
    def test_a_host_seen_without_records_is_unsupported_never_not_run(self) -> None:
        old_instructions = {
            **meta("2.1.252"), "type": "attachment",
            "attachment": {"type": "instructions", "files": [
                {"content": "SECRET-INSTRUCTION-TEXT", "path": "PATHMARK-a", "type": "Project"},
            ]},
        }
        report = self.diagnose(
            user_record(version="2.1.252"), old_instructions, assistant_record(version="2.1.252"),
        )

        self.assert_status(report, "unsupported_host_version", "host_version_seen_without_hook_records")
        self.assertEqual(report["next_action"], "measure_with_the_debug_log_or_update_claude_code")
        self.assertEqual((report["host_versions"]["min"], report["host_versions"]["max"]),
                         ("2.1.252", "2.1.252"))
        self.assertEqual(report["instructions"]["entries"],
                         [{"reason": "none", "files": 1, "removed_files": 0, "changed": None}])
        self.assertEqual(report["first_turn_usage"]["prompt_tokens_total"], 77604)
        self.assertNotIn("not_run", json.dumps(report))

    def test_only_the_version_seen_without_records_bounds_the_status(self) -> None:
        cases = (
            # At or below the highest version seen without records, observed or not.
            ("2.1.252", "unsupported_host_version"),
            ("2.1.251", "unsupported_host_version"),
            ("1.0.0", "unsupported_host_version"),
            # Above it, observed with records or not observed at all: not_recorded.
            ("2.1.253", "not_recorded"),
            ("2.1.277", "not_recorded"),
            ("2.1.278", "not_recorded"),
            ("2.1.309", "not_recorded"),
            ("2.1.400", "not_recorded"),
            ("3.0.0", "not_recorded"),
        )
        for version, status in cases:
            with self.subTest(version=version):
                report = self.diagnose(api_error_record(529, version=version))
                self.assertEqual(report["status"], status)
                self.assertEqual(report["host_versions"]["max"], version)
                self.assertEqual(report["observed_ranges"], {
                    "hook_records_absent": {"min": "2.1.252", "max": "2.1.252"},
                    "hook_records_present": {"min": "2.1.278", "max": "2.1.309"},
                })
        report = self.diagnose(api_error_record(529, version="2.1.300"))
        self.assertEqual(report["next_action"], "measure_with_the_debug_log_if_needed")
        self.assertEqual(report["reason_code"], "no_session_start_hook_record")

    def test_a_resumed_file_reports_its_lowest_and_highest_version(self) -> None:
        report = self.diagnose(
            user_record(version="2.1.252"), assistant_record(version="2.1.252"),
            api_error_record(529, version="2.1.300"), assistant_record(version="2.1.30"),
        )

        self.assert_status(report, "not_recorded", "no_session_start_hook_record")
        self.assertEqual((report["host_versions"]["min"], report["host_versions"]["max"]),
                         ("2.1.30", "2.1.300"))

    def test_records_win_over_the_version_seen_without_records(self) -> None:
        report = self.diagnose(hook_record(version="2.1.252"))

        self.assert_status(report, "observed_ran", "session_start_hook_recorded")
        failed = self.diagnose(failure_record(version="2.1.252"))
        self.assert_status(failed, "observed_error", "session_start_hook_failed")

    def test_without_a_usable_version_nothing_is_interpreted(self) -> None:
        missing = self.diagnose(hook_record(version=MISSING), instructions_record(version=None),
                                api_error_record(429, version=MISSING))
        self.assert_status(missing, "unparsed", "version_missing")
        self.assertEqual(missing["host_versions"]["records_without_version"], 3)
        self.assertEqual(missing["lines"]["parsed"], 3)
        self.assertEqual(missing["session_start_hooks"]["records"], 0)
        self.assertEqual(missing["instructions"]["records"], 0)
        self.assertEqual(missing["api_errors"]["records"], 0)
        self.assertIsNone(missing["first_turn_usage"])

        unparsed = self.diagnose(hook_record(version="2.1.300-SECRET-BUILD"), hook_record(version=7),
                                 hook_record(version="2.1"), hook_record(version="9" * 40))
        self.assert_status(unparsed, "unparsed", "version_unparsed")
        self.assertEqual(unparsed["host_versions"]["records_with_unparsed_version"], 4)
        self.assertEqual(unparsed["session_start_hooks"]["entries"], [])


class InstructionUsageAndErrorTests(SessionEvidenceTestCase):
    def test_instruction_records_keep_counts_reason_and_changed_only(self) -> None:
        report = self.diagnose(
            instructions_record(("PATHMARK-a", "PATHMARK-b")),
            instructions_record(("PATHMARK-c",), changed=True, reason="compaction",
                                removed=["PATHMARK-gone"]),
            instructions_record((), changed=True, reason="SECRET-REASON"),
            instructions_record(("PATHMARK-d",), changed=True, reason="session_start", removed=None),
            instructions_record(("PATHMARK-e",), changed="SECRET-CHANGED", reason=7),
            {**meta(), "type": "attachment",
             "attachment": {"type": "instructions", "files": "SECRET-NOT-A-LIST"}},
        )

        self.assert_status(report, "not_recorded", "no_session_start_hook_record")
        instructions = report["instructions"]
        self.assertEqual(instructions["entries"], [
            {"reason": "none", "files": 2, "removed_files": 0, "changed": None},
            {"reason": "compaction", "files": 1, "removed_files": 1, "changed": True},
            {"reason": "other", "files": 0, "removed_files": 0, "changed": True},
            {"reason": "session_start", "files": 1, "removed_files": 0, "changed": True},
            {"reason": "other", "files": 1, "removed_files": 0, "changed": None},
        ])
        self.assertEqual(
            (instructions["records"], instructions["files"], instructions["removed_files"],
             instructions["unrecognized"], instructions["paths_included"]),
            (5, 5, 1, 1, False),
        )
        self.assertNotIn(PATH_MARK, json.dumps(report))

    def test_paths_appear_only_with_the_second_flag_and_stay_bounded(self) -> None:
        paths = (["PATHMARK-root/CLAUDE.md", "PATHMARK-bad\nline", "PATHMARK-" + "p" * 2000]
                 + [f"PATHMARK-rule-{index}.md" for index in range(60)])
        record = instructions_record(paths, changed=True, reason="session_start",
                                     removed=["PATHMARK-removed.md", 7])
        transcript = self.transcript(record)

        without = self.evidence.diagnose_session_evidence(transcript)
        self.assertNotIn(PATH_MARK, json.dumps(without))
        self.assertEqual(set(without["instructions"]["entries"][0]),
                         {"reason", "files", "removed_files", "changed"})

        shown = self.evidence.diagnose_session_evidence(transcript, include_paths=True)
        self.assertTrue(shown["instructions"]["paths_included"])
        entry = shown["instructions"]["entries"][0]
        self.assertEqual(entry["files"], 63)
        self.assertEqual(len(entry["paths"]), 50)
        self.assertEqual(entry["paths"][:2], ["PATHMARK-root/CLAUDE.md", "PATHMARK-rule-0.md"])
        self.assertEqual(entry["removed_paths"], ["PATHMARK-removed.md"])
        # Two unsafe paths, 11 over the per-record bound and one non-string removal.
        self.assertEqual(entry["paths_omitted"], 14)
        self.assertNotIn(SECRET, json.dumps(shown))

    def test_a_path_of_1024_characters_is_kept_and_one_of_1025_is_counted(self) -> None:
        kept = "PATHMARK-" + "p" * (1024 - len("PATHMARK-"))
        omitted = kept + "q"
        self.assertEqual((len(kept), len(omitted)), (1024, 1025))
        record = instructions_record((omitted, kept), removed=[kept, omitted])
        shown = self.evidence.diagnose_session_evidence(self.transcript(record), include_paths=True)

        entry = shown["instructions"]["entries"][0]
        self.assertEqual((entry["paths"], entry["removed_paths"], entry["paths_omitted"]),
                         ([kept], [kept], 2))
        self.assertEqual(shown["limits"]["path_chars"], 1024)

    def test_paths_that_could_split_or_reorder_a_printed_line_are_omitted(self) -> None:
        # chr() keeps this file free of invisible characters.
        unsafe = (
            "PATHMARK-a/" + chr(0x2028) + "session_next_action: none",  # line separator (Zl)
            "PATHMARK-b/" + chr(0x2029) + "session_evidence: observed_ran",  # paragraph separator (Zp)
            "PATHMARK-c/" + chr(0x202E) + "dm.EDUALC",  # right-to-left override (Cf)
            "PATHMARK-d/zero" + chr(0x200B) + "width.md",  # zero-width space (Cf)
            "PATHMARK-e/" + chr(0x200E) + "mark.md",  # left-to-right mark (Cf)
            "PATHMARK-f/" + chr(0x2066) + "isolate" + chr(0x2069) + ".md",  # bidi isolates (Cf)
            "PATHMARK-g/" + chr(0xFEFF) + "bom.md",  # zero-width no-break space (Cf)
            "PATHMARK-h/tag" + chr(0xE0041) + ".md",  # a format character outside the BMP (Cf)
            "PATHMARK-i/" + chr(0xD800) + "lone.md",  # lone surrogate (Cs)
            "PATHMARK-j/next" + chr(0x85) + "line.md",  # C1 control (Cc)
            "PATHMARK-k/tab" + chr(0x0B) + "line.md",  # C0 control (Cc)
            "PATHMARK-l/" + chr(0x1B) + "[2J.md",  # escape (Cc)
        )
        safe = (
            "PATHMARK-m/My Documents/CLAUDE.md",
            "PATHMARK-n/한글 폴더/CLAUDE.md",
            "PATHMARK-o/no" + chr(0xA0) + "break.md",  # no-break space (Zs)
            "PATHMARK-p/wide" + chr(0x3000) + "space.md",  # ideographic space (Zs)
            "PATHMARK-q/private" + chr(0xF03A) + ".md",  # private use (Co)
            "PATHMARK-r/" + chr(0x1F600) + ".md",  # outside the BMP (So)
        )
        for path in unsafe:
            self.assertTrue(any(unicodedata.category(char) in UNSAFE_CATEGORIES for char in path))
        for path in safe:
            self.assertFalse(any(unicodedata.category(char) in UNSAFE_CATEGORIES for char in path))
        removed = ["PATHMARK-s/" + chr(0x2028) + "session_next_action: none"]
        removed += [f"PATHMARK-gone-{index}.md" for index in range(55)]
        record = instructions_record(unsafe + ("",) + safe, changed=True, reason="session_start",
                                     removed=removed)
        # ASCII JSON: the lone surrogate is written as an escape, as the host writes it.
        transcript = self.transcript(json.dumps(record, separators=(",", ":")).encode("ascii") + b"\n")

        without = self.evidence.diagnose_session_evidence(transcript)
        self.assertNotIn(PATH_MARK, json.dumps(without))

        shown = self.evidence.diagnose_session_evidence(transcript, include_paths=True)
        entry = shown["instructions"]["entries"][0]
        self.assertEqual((entry["files"], entry["removed_files"]), (len(unsafe) + 1 + len(safe), 56))
        self.assertEqual(entry["paths"], list(safe))
        # Each list keeps 50 at most. Counted: the unsafe paths, the empty one, the unsafe
        # removal and five removals over the bound.
        self.assertEqual(entry["removed_paths"], removed[1:51])
        self.assertEqual(entry["paths_omitted"], len(unsafe) + 1 + 1 + 5)
        self.assertEqual(shown["limits"]["paths_per_record"], 50)
        printed = json.dumps(shown, ensure_ascii=False)
        for char in (chr(0x2028), chr(0x2029), chr(0x202E), chr(0x200B), chr(0xD800)):
            self.assertNotIn(char, printed)
        self.assertNotIn("session_next_action", printed)

    def test_the_path_filter_is_exactly_the_unsafe_categories(self) -> None:
        everything = "".join(map(chr, range(0x110000)))
        unsafe = [char for char in everything if unicodedata.category(char) in UNSAFE_CATEGORIES]
        unsafe_set = set(unsafe)
        kept = self.evidence._unsafe_path_characters().sub("", everything)
        self.assertEqual(kept, "".join(char for char in everything if char not in unsafe_set))
        # The fast check settles printable paths without the class; none of these is printable.
        self.assertFalse(any(char.isprintable() for char in unsafe))
        self.assertEqual([char for char in unsafe if not self.evidence._unsafe_path(char)], [])
        # Every line break that str.splitlines() knows is among them.
        breaks = [char for char in everything if len(("a" + char + "b").splitlines()) > 1]
        self.assertEqual(len(breaks), 10)
        self.assertTrue(set(breaks) <= unsafe_set)

    def test_first_turn_usage_is_one_response_deduplicated_by_message_and_request(self) -> None:
        first = {"input_tokens": 3, "cache_creation_input_tokens": 5000,
                 "cache_read_input_tokens": 20000, "output_tokens": 1}
        report = self.diagnose(
            user_record(),
            assistant_record("msg_SECRET-A", "req_SECRET-A", usage=first,
                             block={"type": "thinking", "thinking": "SECRET-THINKING"}),
            assistant_record("msg_SECRET-A", "req_SECRET-A", usage={**first, "output_tokens": 30}),
            assistant_record("msg_SECRET-A", "req_SECRET-A", usage={**first, "output_tokens": 60},
                             block={"type": "tool_use", "id": "toolu_SECRET", "name": "Read",
                                    "input": {"file_path": "SECRET-TOOL-INPUT"}}),
            user_record([{"type": "tool_result", "content": "SECRET-TOOL-RESULT"}]),
            assistant_record("msg_SECRET-B", "req_SECRET-B",
                             usage={"input_tokens": 1, "cache_read_input_tokens": 99_999,
                                    "cache_creation_input_tokens": 1}),
            assistant_record("msg_SECRET-A", "req_SECRET-A", usage=first),
        )

        self.assertEqual(report["first_turn_usage"], {
            "label": "whole_prompt_not_attributable_to_hooks",
            "input_tokens": 3,
            "cache_read_input_tokens": 20000,
            "cache_creation_input_tokens": 5000,
            "prompt_tokens_total": 25003,
            "duplicate_lines": 2,
        })

    def test_the_same_message_id_with_another_request_is_another_response(self) -> None:
        report = self.diagnose(
            assistant_record("msg_SECRET-A", "req_SECRET-1"),
            assistant_record("msg_SECRET-A", "req_SECRET-2"),
            assistant_record("msg_SECRET-A", "req_SECRET-1"),
        )

        self.assertEqual(report["first_turn_usage"]["duplicate_lines"], 0)

    def test_first_turn_skips_lines_that_are_not_a_main_api_response(self) -> None:
        zero = {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        report = self.diagnose(
            assistant_record("msg_SECRET-S", "req_S", isSidechain=True),
            assistant_record("msg_SECRET-E", "req_E", usage=zero, isApiErrorMessage=True),
            assistant_record("msg_SECRET-Y", "req_Y", usage=zero, model="<synthetic>"),
            assistant_record("msg_SECRET-N", "req_N", usage="SECRET-USAGE"),
            assistant_record("msg_SECRET-T", "req_T", usage={"input_tokens": True}),
            assistant_record(None, "req_I"),  # type: ignore[arg-type]
            assistant_record("msg_SECRET-M", None, usage={"input_tokens": 7, "output_tokens": 2}),
        )

        self.assertEqual(report["first_turn_usage"], {
            "label": "whole_prompt_not_attributable_to_hooks",
            "input_tokens": 7, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "prompt_tokens_total": 7, "duplicate_lines": 0,
        })
        self.assertIsNone(self.diagnose(user_record(), api_error_record())["first_turn_usage"])

    def test_api_errors_are_counted_by_status_code_only(self) -> None:
        nested = user_record(toolUseResult={"type": "system", "subtype": "api_error",
                                            "error": {"status": 500, "message": "SECRET-NESTED"}})
        report = self.diagnose(
            api_error_record(429), api_error_record(429), api_error_record(529),
            api_error_record(MISSING), api_error_record("SECRET-STATUS"), api_error_record(True),
            api_error_record(99), system_record("compact_boundary"), system_record("local_command"),
            nested,
        )

        self.assertEqual(report["api_errors"],
                         {"records": 7, "status_codes": {"429": 2, "529": 1}, "without_status": 4})
        # The nested object matched the line filter, so it was parsed and then dropped.
        self.assertEqual(report["lines"]["parsed"], 10)


class ReadingBoundsTests(SessionEvidenceTestCase):
    def test_lines_without_a_record_marker_are_never_decoded(self) -> None:
        decoded: list[bytes] = []
        real_decode = self.evidence._decode

        def spy(line: bytes):
            decoded.append(line)
            return real_decode(line)

        with mock.patch.object(self.evidence, "_decode", side_effect=spy):
            report = self.diagnose(
                user_record(), blob_record(), history_record(),
                {"type": "summary", "summary": "SECRET-SUMMARY", "leafUuid": "SECRET-LEAF"},
                hook_record(), assistant_record(), api_error_record(),
            )

        self.assertEqual(len(decoded), 3)
        self.assertEqual({json.loads(line)["type"] for line in decoded},
                         {"attachment", "assistant", "system"})
        self.assertEqual(report["lines"]["skipped_without_parsing"], 4)

    def test_files_that_are_not_transcripts_are_unparsed(self) -> None:
        cases = (
            (b"", 0),
            (b"SECRET-PLAIN-TEXT\nmore SECRET-TEXT\n", 0),
            (jsonl([user_record(), blob_record()]), 0),
            (b'[{"type":"assistant","x":"SECRET-ARRAY"}]\n', 1),
            (b'"type":"system" SECRET-NOT-JSON\n', 1),
        )
        for data, malformed in cases:
            with self.subTest(data=data[:30]):
                path = self.root / "SECRET-TRANSCRIPT-DIR" / "case.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                report = self.evidence.diagnose_session_evidence(path)
                self.assert_status(report, "unparsed", "no_transcript_records")
                self.assertEqual(report["lines"]["malformed"], malformed)

    def test_malformed_and_partial_lines_are_counted_and_make_it_incomplete(self) -> None:
        deep = b'{"type":"system","x":' + b"[" * 100_000 + b"]" * 100_000 + b"}\n"
        report = self.diagnose(
            hook_record(),
            b'{"type":"system","subtype":"api_error","x":"\xff\xfe SECRET-BAD-UTF8"}\n',
            deep,
            api_error_record(429),
            b'{"type":"attachment","attachment":{"type":"hook_success","stdout":"SECRET-PARTIAL',
        )

        self.assert_status(report, "observed_ran", "session_start_hook_recorded")
        self.assertFalse(report["complete"])
        self.assertEqual(report["lines"]["malformed"], 3)
        self.assertEqual(report["api_errors"]["records"], 1)

    def test_spaced_json_a_bom_and_crlf_are_read(self) -> None:
        path = self.root / "SECRET-TRANSCRIPT-DIR" / "spaced.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            codecs.BOM_UTF8 + json.dumps(hook_record()).encode("utf-8") + b"\r\n"
            + json.dumps(api_error_record(529), indent=None).encode("utf-8") + b"\r\n"
        )

        report = self.evidence.diagnose_session_evidence(path)

        self.assert_status(report, "observed_ran", "session_start_hook_recorded")
        self.assertEqual(report["api_errors"]["status_codes"], {"529": 1})
        self.assertTrue(report["complete"])

    def test_oversized_lines_are_skipped_and_only_record_lines_make_it_incomplete(self) -> None:
        with mock.patch.object(self.evidence, "MAX_LINE_BYTES", 4096):
            unrelated = self.diagnose(user_record("SECRET-BIG " * 1000), hook_record(),
                                      api_error_record(429))
            self.assert_status(unrelated, "observed_ran", "session_start_hook_recorded")
            self.assertEqual(unrelated["lines"]["oversized"], 1)
            self.assertEqual(unrelated["lines"]["oversized_with_record_marker"], 0)
            self.assertTrue(unrelated["complete"])
            self.assertEqual(unrelated["limits"]["line_bytes"], 4096)

            hook_only = self.diagnose(hook_record(stdout="SECRET-STDOUT " * 1000),
                                      api_error_record(429))
            # The record could not be read: no record is no evidence either way.
            self.assert_status(hook_only, "not_recorded", "no_session_start_hook_record")
            self.assertEqual(hook_only["lines"]["oversized_with_record_marker"], 1)
            self.assertFalse(hook_only["complete"])
            self.assertEqual(hook_only["api_errors"]["records"], 1)

    def test_a_record_marker_after_a_long_body_is_found_across_chunks(self) -> None:
        record = hook_record(stdout="SECRET-STDOUT " * 2000)
        marker_last = {key: value for key, value in record.items() if key != "type"}
        marker_last["type"] = "attachment"
        for chunk in (7, 13, 19, 64, 1000):
            with self.subTest(chunk=chunk), \
                    mock.patch.object(self.evidence, "MAX_LINE_BYTES", 4096), \
                    mock.patch.object(self.evidence, "DISCARD_CHUNK_BYTES", chunk):
                report = self.diagnose(marker_last, api_error_record(429))
                self.assertEqual(report["lines"]["oversized_with_record_marker"], 1)
                self.assertFalse(report["complete"])
                self.assertEqual(report["api_errors"]["records"], 1)

    def test_the_file_cap_stops_reading_and_marks_it_incomplete(self) -> None:
        first = jsonl([hook_record()])
        rest = jsonl([api_error_record(429), api_error_record(529)])
        path = self.root / "SECRET-TRANSCRIPT-DIR" / "capped.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(first + rest)

        with mock.patch.object(self.evidence, "MAX_FILE_BYTES", len(first) + 10):
            capped = self.evidence.diagnose_session_evidence(path)
        self.assert_status(capped, "observed_ran", "session_start_hook_recorded")
        self.assertFalse(capped["complete"])
        self.assertEqual(capped["bytes_read"], len(first) + 10)
        self.assertEqual(capped["api_errors"]["records"], 0)

        # A file exactly at the cap, last line without a newline, is read whole.
        exact = first + rest.rstrip(b"\n")
        path.write_bytes(exact)
        with mock.patch.object(self.evidence, "MAX_FILE_BYTES", len(exact)):
            whole = self.evidence.diagnose_session_evidence(path)
        self.assertTrue(whole["complete"])
        self.assertEqual(whole["api_errors"]["records"], 2)

        with mock.patch.object(self.evidence, "MAX_FILE_BYTES", len(first)):
            at_line_end = self.evidence.diagnose_session_evidence(path)
        self.assertFalse(at_line_end["complete"])
        self.assertEqual(at_line_end["session_start_hooks"]["records"], 1)

    def test_file_errors_are_reason_codes_without_exception_text(self) -> None:
        directory = self.root / "SECRET-DIRECTORY"
        directory.mkdir()
        (directory / "SECRET-INSIDE.jsonl").write_bytes(jsonl([hook_record()]))
        refuse = mock.MagicMock(side_effect=AssertionError("directory listed"))
        with mock.patch.object(self.evidence.os, "scandir", refuse), \
                mock.patch.object(self.evidence.os, "listdir", refuse):
            report = self.evidence.diagnose_session_evidence(directory)
        refuse.assert_not_called()
        self.assert_status(report, "unparsed", "not_a_regular_file")

        missing = self.evidence.diagnose_session_evidence(self.root / "SECRET-MISSING" / "x.jsonl")
        self.assert_status(missing, "unparsed", "file_not_found")
        self.assertEqual(missing["next_action"], "check_the_named_transcript_file")

        self.assert_status(self.evidence.diagnose_session_evidence("SECRET-NUL\0path.jsonl"),
                           "unparsed", "file_unreadable")

        transcript = self.transcript(hook_record())
        with mock.patch.object(self.evidence.os, "open",
                               side_effect=PermissionError("SECRET-OS-ERROR C:/Users/SECRET-ACCOUNT")):
            denied = self.evidence.diagnose_session_evidence(transcript)
        self.assert_status(denied, "unparsed", "file_unreadable")

        with mock.patch.object(self.evidence._Scan, "read",
                               side_effect=RuntimeError("SECRET-RUNTIME stdout text")):
            failed = self.evidence.diagnose_session_evidence(transcript)
        self.assert_status(failed, "unparsed", "probe_failed")
        self.assertFalse(failed["complete"])

    def test_a_leading_tilde_is_the_home_folder_and_still_one_named_file(self) -> None:
        self.transcript(hook_record())
        # A temporary home folder: os.path.expanduser reads USERPROFILE on Windows, HOME elsewhere.
        home = {"HOME": str(self.root), "USERPROFILE": str(self.root)}
        refuse = mock.MagicMock(side_effect=AssertionError("directory listed"))
        with mock.patch.dict(os.environ, home), \
                mock.patch.object(self.evidence.os, "scandir", refuse), \
                mock.patch.object(self.evidence.os, "listdir", refuse):
            for named in ("~/SECRET-TRANSCRIPT-DIR/session.jsonl",
                          Path("~") / "SECRET-TRANSCRIPT-DIR" / "session.jsonl"):
                with self.subTest(named=named):
                    report = self.evidence.diagnose_session_evidence(named)
                    self.assert_status(report, "observed_ran", "session_start_hook_recorded")
                    self.assertTrue(report["complete"])
            # The home folder and a folder in it are not read; a missing name stays missing.
            for named, reason_code in (("~", "not_a_regular_file"),
                                       ("~/SECRET-TRANSCRIPT-DIR", "not_a_regular_file"),
                                       ("~/SECRET-MISSING.jsonl", "file_not_found")):
                with self.subTest(named=named):
                    report = self.evidence.diagnose_session_evidence(named)
                    self.assert_status(report, "unparsed", reason_code)
        refuse.assert_not_called()

        # Without a known home folder the path is used as given.
        with mock.patch.object(self.evidence.Path, "expanduser",
                               side_effect=RuntimeError("SECRET-NO-HOME")):
            unknown = self.evidence.diagnose_session_evidence("~/SECRET-TRANSCRIPT-DIR/session.jsonl")
        self.assert_status(unknown, "unparsed", "file_not_found")


class DoctorSessionEvidenceTests(SessionEvidenceTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.doctor = load_doctor()

    def make_vault(self, name: str, hot_max_tokens: int = 100, raw_config: str | None = None) -> Path:
        vault = self.root / name
        (vault / "00-meta").mkdir(parents=True)
        config = raw_config if raw_config is not None else json.dumps({
            "handoff_note": "",
            "hot_note": "00-meta/hot.md",
            "deny_zones": ["90-assets"],
            "hot_max_tokens": hot_max_tokens,
        })
        (vault / "00-meta" / "vault-config.json").write_text(config, encoding="utf-8")
        (vault / "00-meta" / "hot.md").write_text("Ship the verified change.", encoding="utf-8")
        return vault

    def run_main(self, vault: Path, *arguments: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with mock.patch.object(self.doctor, "_utf8_streams"), contextlib.redirect_stdout(buffer):
            code = self.doctor.main(["--vault", str(vault), *arguments])
        return code, buffer.getvalue()

    def run_cli(self, vault: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-X", "importtime", str(DOCTOR), "--vault", str(vault), *arguments],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
        )

    def canary_transcript(self) -> Path:
        """Planted secrets in every content-bearing field the reader could touch."""
        # Under the 64 KiB line cap of the canary test, so the record is read.
        truncated_stdout = "SECRET-TRUNCATED-STDOUT " + "가" * 15_000 + "\n"
        unsaved_stdout = "SECRET-UNSAVED-STDOUT " + "z" * 12_000 + "\n"
        odd_hook = hook_record(source="SECRET-HOOK-SOURCE", exit_code="SECRET-EXIT-CODE",
                               duration_ms="SECRET-DURATION")
        assistant_blocks = (
            {"type": "thinking", "thinking": "SECRET-THINKING", "signature": "SECRET-SIGNATURE"},
            {"type": "text", "text": "SECRET-ANSWER-TEXT"},
            {"type": "tool_use", "id": "toolu_SECRET", "name": "Write",
             "input": {"file_path": "SECRET-TOOL-PATH", "content": "SECRET-TOOL-CONTENT"}},
        )
        records = [
            user_record("SECRET-USER-PROMPT"),
            hook_record(stdout="=== HOT ===\nSECRET-INLINE-STDOUT\n"),
            hook_record(stdout=truncated_stdout, content=persisted_notice(truncated_stdout),
                        command=OTHER_COMMAND, source="resume"),
            odd_hook,
            hook_record(stdout=unsaved_stdout, content=not_saved_notice(unsaved_stdout),
                        source="compact"),
            hook_record(stdout=unsaved_stdout, content=persist_failed_notice(unsaved_stdout),
                        source="clear"),
            hook_record(kind="hook_non_blocking_error", stdout="SECRET-ERROR-STDOUT"),
            *(failure_record(kind, source=source) for kind, source in (
                ("hook_non_blocking_error", "startup"), ("hook_blocking_error", "clear"),
                ("hook_cancelled", "resume"), ("hook_error_during_execution", "compact"),
            )),
            blocking_exit_record(),
            context_record(),
            system_message_record(),
            # Paths that could forge or reorder a printed line are omitted even with the flag.
            instructions_record(("PATHMARK-root/CLAUDE.md", "PATHMARK-rules/vault-collab.md",
                                 "PATHMARK-x/" + chr(0x2028) + "SECRET-FORGED-LINE",
                                 "PATHMARK-y/" + chr(0x202E) + "SECRET-BIDI-PATH"),
                                changed=True, reason="SECRET-REASON",
                                removed=["PATHMARK-removed.md",
                                         "PATHMARK-z/" + chr(0x200B) + "SECRET-ZERO-WIDTH"]),
            *(assistant_record("msg_SECRET-FIRST", "req_SECRET-FIRST", block=block)
              for block in assistant_blocks),
            user_record([{"type": "tool_result", "tool_use_id": "toolu_SECRET",
                          "content": "SECRET-TOOL-RESULT"}],
                        toolUseResult={"type": "system", "content": "SECRET-NESTED-RESULT"}),
            api_error_record(529),
            api_error_record("SECRET-STATUS-TEXT"),
            system_record("local_command"),
            system_record("compact_boundary"),
            blob_record(),
            history_record(),
            {"type": "summary", "summary": "SECRET-SUMMARY", "leafUuid": "SECRET-LEAF"},
            api_error_record(500, version="2.1.300-SECRET-VERSION"),
            b'{"type":"system","subtype":"api_error","error":"SECRET-MALFORMED"\n',
            user_record("SECRET-OVERSIZED " * 5000),
        ]
        return self.transcript(*records)

    def test_default_output_is_byte_identical_and_never_loads_the_reader(self) -> None:
        not_vault = self.root / "not-vault"
        not_vault.mkdir()
        ready = self.make_vault("ready")
        cases = (
            (not_vault, (), NOT_VAULT_TEXT),
            (not_vault, ("--format", "json"),
             json.dumps(NOT_VAULT_REPORT, ensure_ascii=False, sort_keys=True) + "\n"),
            (ready, (), READY_TEXT),
            (ready, ("--format", "json"),
             json.dumps(READY_REPORT, ensure_ascii=False, sort_keys=True) + "\n"),
        )
        for vault, arguments, expected in cases:
            with self.subTest(vault=vault.name, arguments=arguments):
                result = self.run_cli(vault, *arguments)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                self.assertEqual(result.stdout, platform_bytes(expected))
                # -X importtime lists every imported module on stderr.
                self.assertIn(b"vault_healthcheck", result.stderr)
                self.assertNotIn(b"vault_session_evidence", result.stderr)

        opted = self.run_cli(ready, "--format", "json", "--session-evidence", str(self.canary_transcript()))
        self.assertEqual(opted.returncode, 0)
        self.assertIn(b"vault_session_evidence", opted.stderr)
        report = json.loads(opted.stdout)
        report.pop("session_evidence")
        self.assertEqual(report, READY_REPORT)

    def test_option_adds_only_its_block_and_never_changes_the_exit_code(self) -> None:
        not_vault = self.root / "not-vault"
        not_vault.mkdir()
        vaults = (
            (not_vault, 0),
            (self.make_vault("ready"), 0),
            (self.make_vault("degraded", hot_max_tokens=1), 1),
            (self.make_vault("invalid", raw_config='{"broken"'), 2),
        )
        stdout = "SECRET-STDOUT " + "x" * 20_000
        transcripts = {
            "observed_truncated": self.transcript(
                hook_record(stdout=stdout, content=persisted_notice(stdout)), name="truncated.jsonl"),
            "observed_ran": self.transcript(hook_record(), name="ran.jsonl"),
            "observed_error": self.transcript(failure_record(), name="failed.jsonl"),
            "not_recorded": self.transcript(api_error_record(529), name="none.jsonl"),
            "unsupported_host_version": self.transcript(user_record(version="2.1.252"),
                                                        assistant_record(version="2.1.252"),
                                                        name="old.jsonl"),
            "unparsed": self.root / "SECRET-MISSING.jsonl",
        }
        for vault, expected_code in vaults:
            default_code, default_json = self.run_main(vault, "--format", "json")
            _, default_text = self.run_main(vault)
            self.assertEqual(default_code, expected_code)
            for status, transcript in transcripts.items():
                with self.subTest(vault=vault.name, status=status):
                    code, raw = self.run_main(vault, "--format", "json",
                                              "--session-evidence", str(transcript))
                    text_code, text = self.run_main(vault, "--session-evidence", str(transcript))
                    self.assertEqual((code, text_code), (expected_code, expected_code))
                    report = json.loads(raw)
                    self.assertEqual(report.pop("session_evidence")["status"], status)
                    self.assertEqual(report, json.loads(default_json))
                    self.assertTrue(text.startswith(default_text))
                    self.assertIn(f"session_evidence: {status} (", text)

    def test_text_lines_for_one_observed_record(self) -> None:
        ready = self.make_vault("ready")
        transcript = self.transcript(
            hook_record(stdout="=== HOT ===\nnotes\n"),
            instructions_record(("PATHMARK-root/CLAUDE.md",), changed=True, reason="compaction"),
            assistant_record(),
            api_error_record(429), api_error_record(429), api_error_record(MISSING),
        )
        code, text = self.run_main(ready, "--session-evidence", str(transcript))

        self.assertEqual(code, 0)
        self.assertEqual(text[len(READY_TEXT):].splitlines(), [
            "session_evidence: observed_ran (session_start_hook_recorded)",
            "session_trust_boundary: host_internal_format_undocumented",
            "session_complete: true",
            "session_host_versions: min=2.1.300; max=2.1.300",
            "session_start_hook_records: 1; truncated=0; failed=0; unrecognized=0; other_records=0",
            "session_start_hook: source=startup; exit_code=0; duration_ms=812; emitted_chars=18; "
            "injected_chars=17; truncated=false; command_mentions_session_start_py=true",
            "session_instruction_records: 1; files=1; removed_files=0; unrecognized=0",
            "session_instruction: reason=compaction; files=1; removed_files=0; changed=true",
            "session_first_turn_prompt_tokens: 77604 (whole_prompt_not_attributable_to_hooks)",
            "session_api_errors: 3; 429=2; without_status=1",
            "session_next_action: none",
        ])
        _, with_paths = self.run_main(ready, "--session-evidence", str(transcript),
                                      "--session-instruction-paths")
        self.assertIn("session_instruction_path: PATHMARK-root/CLAUDE.md", with_paths.splitlines())

    def test_text_lines_for_one_failed_hook(self) -> None:
        ready = self.make_vault("ready")
        transcript = self.transcript(failure_record(), api_error_record(529))
        code, text = self.run_main(ready, "--session-evidence", str(transcript))

        self.assertEqual(code, 0)
        self.assertEqual(text[len(READY_TEXT):].splitlines(), [
            "session_evidence: observed_error (session_start_hook_failed)",
            "session_trust_boundary: host_internal_format_undocumented",
            "session_complete: true",
            "session_host_versions: min=2.1.300; max=2.1.300",
            "session_start_hook_records: 0; truncated=0; failed=1; unrecognized=0; other_records=0",
            "session_start_hook_failed: source=startup; outcome=non_blocking_error; exit_code=127; "
            "duration_ms=40; command_mentions_session_start_py=true",
            "session_instruction_records: 0; files=0; removed_files=0; unrecognized=0",
            "session_first_turn_prompt_tokens: none",
            "session_api_errors: 1; 529=1; without_status=0",
            "session_next_action: check_the_failed_session_start_hook",
        ])
        self.assertNotIn(SECRET, text)

    def test_text_lines_tell_this_plugins_hook_from_a_failed_vault_hook(self) -> None:
        ready = self.make_vault("ready")
        vault_hook = failure_record()
        vault_hook["attachment"].update(command=VAULT_HOOK_COMMAND, exitCode=1, durationMs=3000)
        transcript = self.transcript(hook_record(stdout="=== HOT ===\nnotes\n"), vault_hook)
        code, text = self.run_main(ready, "--session-evidence", str(transcript))

        self.assertEqual(code, 0)
        lines = text[len(READY_TEXT):].splitlines()
        self.assertEqual(lines[0], "session_evidence: observed_error (session_start_hook_failed)")
        self.assertIn("session_start_hook: source=startup; exit_code=0; duration_ms=812; emitted_chars=18; "
                      "injected_chars=17; truncated=false; command_mentions_session_start_py=true", lines)
        self.assertIn("session_start_hook_failed: source=startup; outcome=non_blocking_error; exit_code=1; "
                      "duration_ms=3000; command_mentions_session_start_py=false", lines)
        self.assertEqual(lines[-1], "session_next_action: check_the_failed_session_start_hook")
        self.assertNotIn(SECRET, text)

    def test_a_path_cannot_forge_a_status_line_in_the_text_output(self) -> None:
        ready = self.make_vault("ready")
        forged = "PATHMARK-x/" + chr(0x2028) + "session_next_action: none"
        bidi = "PATHMARK-y/" + chr(0x202E) + "dm.EDUALC"
        transcript = self.transcript(
            failure_record(),
            instructions_record(("PATHMARK-root/CLAUDE.md", forged, bidi), removed=[forged]),
        )
        arguments = ("--session-evidence", str(transcript), "--session-instruction-paths")
        code, text = self.run_main(ready, *arguments)

        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual([line for line in lines if line.startswith("session_next_action:")],
                         ["session_next_action: check_the_failed_session_start_hook"])
        self.assertIn("session_instruction_path: PATHMARK-root/CLAUDE.md", lines)
        self.assertNotIn("session_instruction_removed_path:", text)
        for char in (chr(0x2028), chr(0x202E)):
            self.assertNotIn(char, text)

        _, raw = self.run_main(ready, "--format", "json", *arguments)
        entry = json.loads(raw)["session_evidence"]["instructions"]["entries"][0]
        self.assertEqual((entry["paths"], entry["removed_paths"], entry["paths_omitted"]),
                         (["PATHMARK-root/CLAUDE.md"], [], 3))

    def test_cli_reads_a_tilde_path_from_the_home_folder(self) -> None:
        ready = self.make_vault("ready")
        self.transcript(hook_record())
        environment = {**os.environ, "HOME": str(self.root), "USERPROFILE": str(self.root)}
        result = subprocess.run(
            [sys.executable, str(DOCTOR), "--vault", str(ready), "--format", "json",
             "--session-evidence", "~/SECRET-TRANSCRIPT-DIR/session.jsonl"],
            cwd=REPO_ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=60,
        )

        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = json.loads(result.stdout)
        self.assertEqual(report.pop("session_evidence")["status"], "observed_ran")
        self.assertEqual(report, READY_REPORT)
        self.assertNotIn(SECRET.encode("ascii"), result.stdout + result.stderr)

    def test_paths_flag_requires_session_evidence(self) -> None:
        ready = self.make_vault("ready")
        errors = io.StringIO()
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(errors):
            self.run_main(ready, "--session-instruction-paths")
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("--session-instruction-paths requires --session-evidence", errors.getvalue())

    def test_planted_secrets_never_reach_text_or_json(self) -> None:
        ready = self.make_vault("ready")
        transcript = self.canary_transcript()
        with mock.patch.object(self.evidence, "MAX_LINE_BYTES", 64 * 1024):
            for paths in (False, True):
                for output in ("text", "json"):
                    with self.subTest(paths=paths, output=output):
                        arguments = ["--format", output, "--session-evidence", str(transcript)]
                        if paths:
                            arguments.append("--session-instruction-paths")
                        code, printed = self.run_main(ready, *arguments)
                        self.assertEqual(code, 0)
                        self.assertNotIn(SECRET, printed)
                        self.assertNotIn(str(transcript.parent), printed)
                        if paths:
                            self.assertIn("PATHMARK-root/CLAUDE.md", printed)
                            self.assertIn("PATHMARK-removed.md", printed)
                        else:
                            self.assertNotIn(PATH_MARK, printed)
                        if output == "text":
                            self.assertIn("session_start_hook_failed: source=clear; "
                                          "outcome=blocking_error; exit_code=none; duration_ms=none; "
                                          "command_mentions_session_start_py=true",
                                          printed.splitlines())
                        if output == "json":
                            block = json.loads(printed)["session_evidence"]
                            # Not vacuous: the reader did interpret the planted records.
                            self.assertEqual(block["status"], "observed_error")
                            hooks = block["session_start_hooks"]
                            self.assertEqual(
                                (hooks["records"], hooks["truncated"], hooks["failed"],
                                 hooks["unrecognized"], hooks["other_records"]),
                                (5, 3, 6, 0, 2),
                            )
                            self.assertEqual(hooks["failed_entries"], [
                                {"source": "startup", "outcome": "non_blocking_error",
                                 "exit_code": 0, "duration_ms": 812,
                                 "command_mentions_session_start_py": True},
                                {"source": "startup", "outcome": "non_blocking_error",
                                 "exit_code": 127, "duration_ms": 40,
                                 "command_mentions_session_start_py": True},
                                {"source": "clear", "outcome": "blocking_error",
                                 "exit_code": None, "duration_ms": None,
                                 "command_mentions_session_start_py": True},
                                {"source": "resume", "outcome": "cancelled",
                                 "exit_code": None, "duration_ms": 60_000,
                                 "command_mentions_session_start_py": True},
                                {"source": "compact", "outcome": "error_during_execution",
                                 "exit_code": None, "duration_ms": 3,
                                 "command_mentions_session_start_py": True},
                                {"source": "startup", "outcome": "non_blocking_error",
                                 "exit_code": 2, "duration_ms": None,
                                 "command_mentions_session_start_py": False},
                            ])
                            self.assertEqual(block["instructions"]["records"], 1)
                            if paths:
                                self.assertEqual(
                                    block["instructions"]["entries"][0]["paths_omitted"], 3)
                            self.assertEqual(block["first_turn_usage"]["duplicate_lines"], 2)
                            self.assertEqual(block["api_errors"]["records"], 3)
                            self.assertEqual(block["lines"]["oversized"], 1)
                            self.assertEqual(block["lines"]["malformed"], 1)
                            self.assertEqual(
                                block["host_versions"]["records_with_unparsed_version"], 1)

        result = self.run_cli(ready, "--format", "json", "--session-evidence", str(transcript),
                              "--session-instruction-paths")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(SECRET.encode("ascii"), result.stdout)
        self.assertNotIn(SECRET.encode("ascii"), result.stderr)
        self.assertIn(b"PATHMARK-root/CLAUDE.md", result.stdout)

    def test_session_evidence_and_host_version_together(self) -> None:
        ready = self.make_vault("degraded", hot_max_tokens=1)
        transcript = self.transcript(hook_record())
        block = self.doctor._host_report("unavailable", "timeout")
        with mock.patch.object(self.doctor, "diagnose_host_version", return_value=block):
            code, raw = self.run_main(ready, "--format", "json", "--host-version",
                                      "--session-evidence", str(transcript))
        report = json.loads(raw)
        self.assertEqual(code, 1)
        self.assertEqual(report["host_version"], block)
        self.assertEqual(report["session_evidence"]["status"], "observed_ran")

    def test_the_fallback_block_uses_the_readers_own_words(self) -> None:
        own = self.evidence._empty_report("unparsed", "probe_failed", False)
        block = self.doctor.SESSION_EVIDENCE_UNAVAILABLE
        self.assertEqual(block, {key: own[key] for key in block})
        prefixes = ("session_evidence:", "session_trust_boundary:", "session_complete:",
                    "session_next_action:")
        self.assertEqual(list(self.doctor.SESSION_EVIDENCE_UNAVAILABLE_LINES),
                         [line for line in self.evidence.text_lines(own) if line.startswith(prefixes)])

    def test_a_reader_that_cannot_be_loaded_gives_a_fixed_unparsed_block(self) -> None:
        ready = self.make_vault("ready")
        degraded = self.make_vault("degraded", hot_max_tokens=1)
        degraded_code, degraded_json = self.run_main(degraded, "--format", "json")
        self.assertEqual(degraded_code, 1)
        transcript = self.transcript(hook_record())
        # A copy of the plugin with a damaged reader; the doctor finds its hook from its own path.
        copy = self.root / "SECRET-COPY"
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REPO_ROOT / "hooks", copy / "hooks", ignore=ignore)
        scripts = copy / "skills" / "agentic-vault" / "scripts"
        shutil.copytree(SCRIPTS, scripts, ignore=ignore)
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        fallback_text = "".join(line + "\n" for line in self.doctor.SESSION_EVIDENCE_UNAVAILABLE_LINES)
        damages = {
            "syntax_error": "def SECRET_BROKEN(:\n",
            "error_at_import": 'raise RuntimeError("SECRET-IMPORT-ERROR")\n',
        }
        runs = (
            (ready, "text", 0, None),
            (ready, "json", 0, READY_REPORT),
            (degraded, "json", 1, json.loads(degraded_json)),
        )
        for damage, source in damages.items():
            (scripts / "vault_session_evidence.py").write_text(source, encoding="utf-8")
            for vault, output, expected_code, expected_report in runs:
                with self.subTest(damage=damage, vault=vault.name, output=output):
                    result = subprocess.run(
                        [sys.executable, str(scripts / "vault_doctor.py"), "--vault", str(vault),
                         "--format", output, "--session-evidence", str(transcript)],
                        cwd=self.root, env=environment, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, check=False, timeout=60,
                    )
                    # The vault's exit code and output, no traceback and no exception text.
                    self.assertEqual(result.returncode, expected_code, result.stderr[-2000:])
                    self.assertEqual(result.stderr, b"")
                    self.assertNotIn(SECRET.encode("ascii"), result.stdout)
                    if output == "json":
                        report = json.loads(result.stdout)
                        self.assertEqual(report.pop("session_evidence"),
                                         self.doctor.SESSION_EVIDENCE_UNAVAILABLE)
                        self.assertEqual(report, expected_report)
                    else:
                        self.assertEqual(result.stdout, platform_bytes(READY_TEXT + fallback_text))


if __name__ == "__main__":
    unittest.main()
