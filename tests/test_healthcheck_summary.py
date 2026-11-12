"""The health check's opt-in one-line JSON summary (`--json-summary`).

The Telegram butler used to report `치명 없음 ✅` from the exit code alone, so a
check that skipped linked folders or could not read notes still looked clean.
With `--json-summary` the full check ends its stdout with one JSON object that
says whether the check ran (`status`), its counts and whether it was complete;
the bridge reads that line instead of the exit code.

Counts only: no path, note name or value reaches the line. Notes left out only
because the extension evidence byte budget was spent are counted apart
(`evidence_skipped`) and never make the check incomplete, but they do make the
warning extensions' input incomplete (`extension_input_complete`), which the
bridge mentions when certified warning promotion is active
(`warning_promotion_active`).

Contract test (passes before and after the change): without the flag no stdout
line starts with `{"`. The byte comparison with and without the flag needs the
flag, so it is not a contract test.

The checks call `main()` in this process (fast); temporary roots are resolved
and Git runs with the user's settings cut off (`isolate_git`).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _git_env import isolate_git
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git
    from tests._winfs import make_junction


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"
CONFIG = "00-meta/vault-config.json"
REPORT = "00-meta/summary-health.md"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
    import vault_warning_policy
finally:
    sys.path.remove(str(SCRIPTS))

SCHEMA = "agentic-vault/healthcheck-summary/v1"
FIELDS = {
    "complete", "critical", "evidence_skipped", "exit_code", "extension_input_complete",
    "issues", "linked_worktree_copies", "notes_checked", "schema", "skipped_links", "status",
    "unreadable", "warning_promotion_active",
}
STAGED_ERROR = "--json-summary는 --staged와 함께 쓸 수 없다"
CONSOLE_RE = re.compile(r"노트 (\d+)개 검사, 이슈 (\d+)건\(치명 (\d+)건\)")


def note(title: str, body: str = "") -> str:
    return f'---\ntitle: "{title}"\n---\n{body}'


def idle_summary(status: str, exit_code: int) -> dict:
    """The summary of a run that did not check notes: zero counts, nothing complete."""
    return {
        "schema": SCHEMA, "status": status, "exit_code": exit_code, "notes_checked": 0,
        "issues": 0, "critical": 0, "evidence_skipped": 0, "unreadable": 0,
        "skipped_links": 0, "linked_worktree_copies": 0, "complete": False,
        "extension_input_complete": False, "warning_promotion_active": False,
    }


def run_healthcheck(*arguments: str) -> tuple[int, str, str]:
    """Call the health check's main() with these arguments; returns (rc, stdout, stderr).

    The scripts folder is on `sys.path` during the call, as it is when the health
    check runs as a program: the warning analyzer imports its extensions lazily.
    """
    out, err = io.StringIO(), io.StringIO()
    saved_path = list(sys.path)
    sys.path.insert(0, str(SCRIPTS))
    try:
        with mock.patch.object(sys, "argv", [str(HEALTHCHECK), *arguments]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = vault_healthcheck.main()
            except SystemExit as stop:  # argparse usage errors
                code = stop.code
    finally:
        sys.path[:] = saved_path
    return code, out.getvalue(), err.getvalue()


class _SummaryCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        isolate_git(self, ceiling=self.root)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def write(self, relpath: str, text: str) -> Path:
        path = self.vault / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_config(self, **overrides: object) -> None:
        config: dict[str, object] = {
            "vault_name": "Summary vault",
            "required_keys": ["title"],
            "enums": {},
            "deny_zones": [],
            "exclude_dirs": [".git"],
            "frontmatter_roots": ["20-knowledge"],
            "index_note": "",
            "log_note": "",
            "log_tags": [],
            "hot_note": "",
            "handoff_note": "",
            "ssot_note": "",
            "health_report": REPORT,
            "rules_dir": "",
        }
        config.update(overrides)
        self.write(CONFIG, json.dumps(config, ensure_ascii=False))

    def clean_vault(self, **overrides: object) -> None:
        self.write_config(**overrides)
        self.write("20-knowledge/A.md", note("A", "[[B]]\n"))
        self.write("20-knowledge/B.md", note("B", "[[A]]\n"))

    def summary(self, stdout: str) -> tuple[dict, str]:
        """Parse the last stdout line; it must be the one canonical JSON object."""
        lines = stdout.splitlines()
        self.assertTrue(lines, "no stdout line")
        line = lines[-1]
        self.assertTrue(line.startswith('{"'), line)
        parsed = json.loads(line)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(set(parsed), FIELDS)
        self.assertEqual(line, json.dumps(parsed, ensure_ascii=True, sort_keys=True,
                                          separators=(",", ":")))
        self.assertEqual(sum(1 for item in lines if item.startswith('{"')), 1, stdout)
        return parsed, line

    def run_summary(self, *arguments: str) -> tuple[int, dict, str, str]:
        code, stdout, stderr = run_healthcheck("--vault", str(self.vault), *arguments,
                                               "--json-summary")
        parsed, line = self.summary(stdout)
        self.assertEqual(parsed["exit_code"], code, stderr)
        return code, parsed, line, stdout

    def assert_console_counts(self, stdout: str, parsed: dict) -> None:
        match = CONSOLE_RE.search(stdout)
        self.assertIsNotNone(match, stdout)
        notes, issues, critical = (int(value) for value in match.groups())
        self.assertEqual((parsed["notes_checked"], parsed["issues"], parsed["critical"]),
                         (notes, issues, critical))

    def directory_link_or_skip(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            try:
                make_junction(link, target)
            except OSError as exc:
                self.skipTest(f"junction creation is unsupported: {exc}")
        else:
            try:
                link.symlink_to(target, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"real symlink creation is unsupported: {exc}")


class CheckedSummaryTests(_SummaryCase):
    def test_clean_vault_is_checked_and_complete(self) -> None:
        self.clean_vault()

        code, parsed, line, stdout = self.run_summary()

        self.assertEqual(code, 0, stdout)
        self.assertEqual(parsed["schema"], SCHEMA)
        self.assertEqual(parsed["status"], "checked")
        self.assertEqual(parsed["exit_code"], 0)
        self.assertEqual(parsed["notes_checked"], 2)
        self.assertEqual(parsed["critical"], 0)
        for field in ("evidence_skipped", "unreadable", "skipped_links", "linked_worktree_copies"):
            self.assertEqual(parsed[field], 0, field)
        self.assertIs(parsed["complete"], True)
        self.assertIs(parsed["extension_input_complete"], True)
        self.assertIs(parsed["warning_promotion_active"], False)
        self.assert_console_counts(stdout, parsed)
        self.assertNotIn(str(self.vault), line)

    def test_fatal_finding_is_counted_and_exits_1(self) -> None:
        self.clean_vault()
        self.write("20-knowledge/C.md", "no front matter [[A]]\n")

        code, parsed, _line, stdout = self.run_summary()

        self.assertEqual(code, 1, stdout)
        self.assertEqual(parsed["status"], "checked")
        self.assertEqual(parsed["critical"], 1)
        self.assertIs(parsed["complete"], True)
        self.assert_console_counts(stdout, parsed)

    def test_link_or_junction_makes_the_check_incomplete(self) -> None:
        self.clean_vault()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "Hidden.md").write_text("LINKED-SENTINEL\n", encoding="utf-8")
        self.directory_link_or_skip(self.vault / "20-knowledge" / "linked", outside)

        code, parsed, line, stdout = self.run_summary()

        self.assertEqual(code, 0, stdout)
        self.assertEqual(parsed["status"], "checked")
        self.assertEqual(parsed["skipped_links"], 1)
        self.assertEqual(parsed["unreadable"], 0)
        self.assertIs(parsed["complete"], False)
        self.assertIs(parsed["extension_input_complete"], False)
        for hidden in ("20-knowledge", "linked/", "Hidden", "LINKED-SENTINEL", str(outside)):
            self.assertNotIn(hidden, line)

    def test_unreadable_note_makes_the_check_incomplete(self) -> None:
        self.clean_vault()
        real_read_text = vault_healthcheck.read_text

        def flaky(path: Path) -> str:
            if path.name == "B.md":
                raise OSError("locked (test)")
            return real_read_text(path)

        with mock.patch.object(vault_healthcheck, "read_text", side_effect=flaky):
            code, parsed, line, stdout = self.run_summary()

        self.assertEqual(code, 0, stdout)
        self.assertEqual(parsed["status"], "checked")
        self.assertEqual(parsed["notes_checked"], 2)
        self.assertEqual(parsed["unreadable"], 1)
        self.assertEqual(parsed["evidence_skipped"], 0)
        self.assertEqual(parsed["skipped_links"], 0)
        self.assertIs(parsed["complete"], False)
        self.assertIs(parsed["extension_input_complete"], False)
        for hidden in ("B.md", "20-knowledge", "locked"):
            self.assertNotIn(hidden, line)

    def budget_vault(self, **overrides: object) -> None:
        """One target note over 1,000,000 characters spends the whole evidence budget."""
        self.write_config(**overrides)
        self.write("20-knowledge/Big.md", note("Big", "x" * (1_000_001 - len(note("Big")))))
        self.write("CLAUDE.md", "# Rules\n")
        self.assertEqual(len((self.vault / "20-knowledge/Big.md").read_text(encoding="utf-8")),
                         1_000_001)

    def test_evidence_budget_skips_do_not_count_as_unreadable(self) -> None:
        self.budget_vault()

        first_code, first, _line, _stdout = self.run_summary()
        code, parsed, _line, stdout = self.run_summary()

        self.assertEqual(first_code, 0)
        self.assertEqual(first["evidence_skipped"], 1)  # CLAUDE.md
        self.assertEqual(code, 0, stdout)
        self.assertEqual(parsed["status"], "checked")
        self.assertEqual(parsed["unreadable"], 0)
        self.assertGreaterEqual(parsed["evidence_skipped"], 1)
        self.assertEqual(parsed["evidence_skipped"], 2)  # CLAUDE.md and the report
        self.assertIs(parsed["complete"], True)
        self.assertIs(parsed["extension_input_complete"], False)
        self.assertIs(parsed["warning_promotion_active"], False)
        # The default report keeps listing them in section 14 (byte-identical output).
        report = (self.vault / REPORT).read_text(encoding="utf-8")
        self.assertIn("- CLAUDE.md — extension evidence total byte limit", report)
        self.assertIn(f"- {REPORT} — extension evidence total byte limit", report)

    def test_budget_reasons_are_one_constant(self) -> None:
        self.assertEqual(vault_healthcheck.EVIDENCE_BUDGET_REASONS, (
            "extension evidence total byte limit",
            "extension evidence note/total byte limit",
        ))
        spent = self.write("20-knowledge/Spent.md", note("Spent"))
        self.assertEqual(
            vault_healthcheck._read_extension_evidence(self.vault, spent, [], 0),
            (None, vault_healthcheck.EVIDENCE_BUDGET_REASONS[0]))
        self.assertEqual(
            vault_healthcheck._read_extension_evidence(self.vault, spent, [], 3),
            (None, vault_healthcheck.EVIDENCE_BUDGET_REASONS[1]))

    def test_warning_promotion_is_reported(self) -> None:
        policy = {"levels": {"invalid-date": "fatal"},
                  "certificate": vault_warning_policy.build_promotion_certificate()}
        cases = (
            ("clean", self.clean_vault, True),
            ("budget", self.budget_vault, False),
        )
        for label, build, input_complete in cases:
            with self.subTest(label):
                build(warning_policy=policy)

                code, parsed, _line, stdout = self.run_summary()

                self.assertEqual(code, 0, stdout)
                self.assertEqual(parsed["status"], "checked")
                self.assertIs(parsed["warning_promotion_active"], True)
                self.assertIs(parsed["extension_input_complete"], input_complete)
                self.assertIs(parsed["complete"], True)

    def test_warning_level_without_promotion_is_not_active(self) -> None:
        self.clean_vault(warning_policy={"levels": {"invalid-date": "warning"}})

        code, parsed, _line, stdout = self.run_summary()

        self.assertEqual(code, 0, stdout)
        self.assertIs(parsed["warning_promotion_active"], False)


class EarlyReturnSummaryTests(_SummaryCase):
    def test_runs_that_check_no_notes(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        missing = self.root / "missing"
        broken = self.root / "broken"
        (broken / "00-meta").mkdir(parents=True)
        (broken / CONFIG).write_text("{not valid json", encoding="utf-8")
        cases = (
            ("folder without configuration", plain, (), 0, "not_vault"),
            ("--require-vault", plain, ("--require-vault",), 2, "not_vault"),
            ("explicit missing path", missing, (), 2, "vault_not_found"),
            ("explicit file", self.write("file.txt", "x"), (), 2, "vault_not_found"),
            ("broken configuration", broken, (), 1, "error"),
        )
        for label, path, options, expected_code, status in cases:
            with self.subTest(label):
                plain_code, plain_out, plain_err = run_healthcheck("--vault", str(path), *options)
                code, stdout, stderr = run_healthcheck("--vault", str(path), *options,
                                                       "--json-summary")
                self.assertEqual(code, expected_code, stderr)
                self.assertEqual(plain_code, expected_code, plain_err)
                parsed, line = self.summary(stdout)
                self.assertEqual(parsed, idle_summary(status, expected_code))
                self.assertEqual(stdout, plain_out + line + "\n")
                self.assertEqual(stderr, plain_err)

    def test_all_targets_unreadable_is_an_error(self) -> None:
        self.clean_vault()

        def always_fail(_path: Path) -> str:
            raise OSError("offline (test)")

        with mock.patch.object(vault_healthcheck, "read_text", side_effect=always_fail):
            code, parsed, _line, stdout = self.run_summary()

        self.assertEqual(code, 1, stdout)
        self.assertEqual(parsed, idle_summary("error", 1))

    def test_staged_with_summary_is_a_usage_error(self) -> None:
        code, stdout, stderr = run_healthcheck("--vault", str(self.vault), "--staged",
                                               "--json-summary")
        self.assertEqual(code, 2, stderr)
        self.assertEqual(stdout, "")
        self.assertIn(STAGED_ERROR, stderr)


class DefaultOutputTests(_SummaryCase):
    def test_without_the_flag_no_summary_line_is_printed(self) -> None:
        # Contract: passes before and after the change.
        self.clean_vault()
        for arguments in ((), ("--output", str(self.root / "elsewhere.md"))):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = run_healthcheck("--vault", str(self.vault), *arguments)
                self.assertEqual(code, 0, stderr)
                self.assertTrue(stdout)
                self.assertFalse([line for line in stdout.splitlines() if line.startswith('{"')])

    def test_the_flag_only_appends_the_summary_line(self) -> None:
        self.clean_vault()
        run_healthcheck("--vault", str(self.vault))  # the report exists in both runs below

        plain_code, plain_out, plain_err = run_healthcheck("--vault", str(self.vault))
        code, stdout, stderr = run_healthcheck("--vault", str(self.vault), "--json-summary")

        self.assertEqual(code, plain_code)
        _parsed, line = self.summary(stdout)
        self.assertEqual(stdout, plain_out + line + "\n")
        self.assertEqual(stderr, plain_err)


if __name__ == "__main__":
    unittest.main()
