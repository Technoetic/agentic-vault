"""Section 14d of the health check: one decision or mistake number on two headings of a note.

Sessions that each add "the next" entry to one decisions or mistakes note can give two
entries the same number. Full mode lists, in section 14d, every group of two or more
level-2 headings of one note whose text starts with the same `ADR-<n>` or `M-<n>` number
(headings inside fenced code and HTML comments do not count, and the number ends at the
first non-digit). Each group is one finding: it adds one to the 관리성 total and, with
`--json-summary`, to `duplicate_entry_ids`, a key that is there only when a finding is.
Each line says whether git has the headings at HEAD. The section is a report only: the
exit code and `--staged` stay as they were, and nothing is renumbered, because a changed
heading breaks its anchor.

Contract test (passes before and after the change): a vault without such headings gets
no section and no summary key. Every other test also needs a finding that only the new
section reports, so it fails before the change.

The checks call `main()` in this process with git's global and system settings cut off;
vaults and repositories are temporary and notes are synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    from _git_env import isolate_git
    from test_healthcheck_summary import FIELDS, HEALTHCHECK, note, run_healthcheck
    from test_worktree_zone_paths import git, write
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git
    from tests.test_healthcheck_summary import FIELDS, HEALTHCHECK, note, run_healthcheck
    from tests.test_worktree_zone_paths import git, write


CONFIG = "00-meta/vault-config.json"
DECISIONS = "20-knowledge/demo decisions.md"
MISTAKES = "20-knowledge/demo mistakes.md"
HEADER = "## 14d. 같은 번호의 결정·실수 항목 — 관리성 ({count})"
KEEP_BOTH = "— 둘 다 커밋됐다. 둘 다 두고 참조에 제목을 함께 적는다(제목을 바꾸면 앵커가 깨진다)."
KEEP_ALL = "— 모두 커밋됐다. 모두 두고 참조에 제목을 함께 적는다(제목을 바꾸면 앵커가 깨진다)."
CHANGE = "— 커밋 안 된 제목이 있다({lines}행). 커밋 안 된 쪽 번호를 바꾼다."
UNKNOWN = "— 커밋 여부를 확인하지 못했다. 번호를 바꾸기 전에 참조를 확인한다."
SUMMARY_KEY = "duplicate_entry_ids"


def entries(*headings: str) -> str:
    """A note body: each heading with one entry line under it, then a link back to note A."""
    parts = ["# 기록", ""]
    for heading in headings:
        parts += [heading, "", "- 상태: 채택", ""]
    return "\n".join([*parts, "[[A]]", ""])


def line_numbers(text: str, heading: str) -> list[int]:
    """The 1-based numbers of the lines of text that equal heading."""
    return [index + 1 for index, line in enumerate(text.splitlines()) if line == heading]


def joined(numbers: list[int]) -> str:
    return "·".join(str(number) for number in numbers)


def entry(path: str, label: str, numbers: list[int], state: str) -> str:
    """One expected line of section 14d."""
    return f"- {path}: {label} ×{len(numbers)} ({joined(numbers)}행) {state}"


def section(report: str) -> list[str]:
    """The lines of section 14d up to the next blank line ([] when the report has none)."""
    lines = report.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("## 14d. ")), None)
    if start is None:
        return []
    end = next((index for index in range(start, len(lines)) if not lines[index]), len(lines))
    return lines[start:end]


def totals(report: str) -> tuple[int, int, int]:
    """(총 이슈, 치명, 관리성) from the report's count line."""
    line = next(line for line in report.splitlines() if line.startswith("- 검사 노트 수:"))
    total = int(line.split("총 이슈: **")[1].split("**")[0])
    critical = int(line.split("(치명 ")[1].split(" ")[0])
    managed = int(line.split("관리성 ")[-1].rstrip(")"))
    return total, critical, managed


def headings(report: str) -> list[str]:
    """The section numbers of the report, in order ('14b', '14c', '14d', '15', ...)."""
    return [line[3:].split(".")[0] for line in report.splitlines() if line.startswith("## ")]


class _EntryCase(unittest.TestCase):
    """A vault with no issue: notes A and B and the two entry notes all link to each other."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        isolate_git(self, ceiling=self.root)
        self.vault = self.root / "vault"
        self.report = self.root / "report.md"
        write(self.vault / CONFIG, json.dumps({
            "vault_name": "Entry vault", "required_keys": ["title"], "enums": {},
            "deny_zones": ["20-knowledge/_archive"], "exclude_dirs": [".git", "drafts"],
            "frontmatter_roots": ["20-knowledge"], "index_note": "", "log_note": "",
            "log_tags": [], "hot_note": "", "handoff_note": "", "ssot_note": "",
            "health_report": "00-meta/health.md", "rules_dir": "",
        }, ensure_ascii=False))
        write(self.vault / "20-knowledge" / "A.md", note("A", "[[B]] [[demo decisions]] [[demo mistakes]]\n"))
        write(self.vault / "20-knowledge" / "B.md", note("B", "[[A]]\n"))
        self.decisions(entries("## ADR-1 | 2026-01-01 | 처음 결정"))
        self.mistakes(entries("## M-1 — 처음 실수"))

    def decisions(self, body: str) -> str:
        """Write the decisions note with this body; returns the whole note text."""
        text = note("demo decisions", body)
        write(self.vault / DECISIONS, text)
        return text

    def mistakes(self, body: str) -> str:
        """Write the mistakes note with this body; returns the whole note text."""
        text = note("demo mistakes", body)
        write(self.vault / MISTAKES, text)
        return text

    def check(self, *arguments: str, vault: Path | None = None) -> tuple[int, str, str]:
        """Run the full check; returns (exit code, report text, stdout)."""
        if self.report.exists():
            self.report.unlink()
        code, stdout, stderr = run_healthcheck(
            "--vault", str(vault or self.vault), "--output", str(self.report), *arguments)
        self.assertTrue(self.report.is_file(), stderr)
        return code, self.report.read_bytes().decode("utf-8"), stdout

    @staticmethod
    def summary(stdout: str) -> dict:
        """The --json-summary object on the last stdout line."""
        return json.loads(stdout.splitlines()[-1])


class DuplicateEntryIdTests(_EntryCase):
    """The vault is no repository here, so no line can say whether a heading is committed."""

    def test_no_number_twice_prints_no_section_and_no_summary_key(self) -> None:
        # Contract test: entry headings with distinct numbers leave the report as it was.
        self.decisions(entries("## ADR-1 | 2026-01-01 | 하나", "## ADR-2 | 2026-01-02 | 둘"))
        self.mistakes(entries("## M-1 — 하나", "## M-2 — 둘"))
        code, report, stdout = self.check("--json-summary")
        self.assertEqual(code, 0, stdout)
        self.assertNotIn("14d", report)
        self.assertNotIn("14d", stdout)
        self.assertEqual(set(self.summary(stdout)), FIELDS)
        self.assertEqual(totals(report), (0, 0, 0))

        self.decisions(entries("## 결정 하나", "## 결정 둘"))
        self.mistakes(entries("## 실수 하나", "## 실수 둘"))
        plain_code, plain_report, plain_stdout = self.check("--json-summary")
        self.assertEqual((code, report.encode("utf-8"), stdout),
                         (plain_code, plain_report.encode("utf-8"), plain_stdout))

    def test_without_a_repository_the_line_says_the_state_is_unknown(self) -> None:
        first, second = "## ADR-7 | 2026-01-02 | 둘째 세션", "## ADR-7 | 2026-01-01 | 첫 세션"
        text = self.decisions(entries(first, second))
        code, report, stdout = self.check()
        self.assertEqual(code, 0, stdout)
        self.assertEqual(section(report), [
            HEADER.format(count=1),
            entry(DECISIONS, "ADR-7", line_numbers(text, first) + line_numbers(text, second), UNKNOWN),
        ])
        self.assertEqual(totals(report), (1, 0, 1))

    def test_each_kind_and_number_is_one_finding_and_the_summary_counts_them(self) -> None:
        plain_code, _plain_report, plain_stdout = self.check("--json-summary")
        adr = ("## ADR-7 | 2026-01-03 | 셋", "## ADR-8 | 2026-01-02 | 하나뿐",
               "## ADR-7 | 2026-01-02 | 둘", "## ADR-7 | 2026-01-01 | 하나")
        decisions = self.decisions(entries(*adr))
        mistakes = self.mistakes(entries("## M-3 — 앞", "## M-3 — 뒤"))
        code, report, stdout = self.check("--json-summary")
        self.assertEqual(code, plain_code, stdout)
        sevens = [line_numbers(decisions, heading)[0] for heading in adr if "ADR-7 " in heading]
        threes = line_numbers(mistakes, "## M-3 — 앞") + line_numbers(mistakes, "## M-3 — 뒤")
        self.assertEqual(section(report), [
            HEADER.format(count=2),
            entry(DECISIONS, "ADR-7", sevens, UNKNOWN),
            entry(MISTAKES, "M-3", threes, UNKNOWN),
        ])
        self.assertEqual(totals(report), (2, 0, 2))
        plain, flagged = self.summary(plain_stdout), self.summary(stdout)
        self.assertNotIn(SUMMARY_KEY, plain)
        self.assertEqual(flagged.pop(SUMMARY_KEY), 2)
        self.assertEqual(flagged.pop("issues"), plain.pop("issues") + 2)
        self.assertEqual(flagged, plain)

    def test_headings_in_fenced_code_and_html_comments_do_not_count(self) -> None:
        text = self.decisions("\n".join([
            "# 기록", "",
            "## ADR-7 | 2026-01-01 | 실제 결정", "",
            "```markdown", "## ADR-7 | 형식 예시", "```", "",
            "~~~", "## ADR-7 | 물결 울타리 예시", "~~~", "",
            "<!--", "## ADR-7 | 주석 속 제목", "-->", "",
            "<!-- ## ADR-7 | 한 줄 주석 -->", "",
            "## ADR-9 | 2026-01-02 | 하나", "",
            "## ADR-9 | 2026-01-03 | 둘", "",
            "[[A]]", "",
        ]))
        _code, report, _stdout = self.check()
        nines = line_numbers(text, "## ADR-9 | 2026-01-02 | 하나") + line_numbers(text, "## ADR-9 | 2026-01-03 | 둘")
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-9", nines, UNKNOWN)])

    def test_the_same_number_in_two_notes_is_not_a_finding(self) -> None:
        self.decisions(entries("## ADR-7 | 2026-01-01 | 결정 노트의 7번", "## M-7 — 다른 종류의 7번"))
        text = self.mistakes(entries("## ADR-7 | 2026-01-02 | 실수 노트의 7번", "## M-5 — 하나", "## M-5 — 둘"))
        _code, report, _stdout = self.check()
        fives = line_numbers(text, "## M-5 — 하나") + line_numbers(text, "## M-5 — 둘")
        self.assertEqual(section(report), [HEADER.format(count=1), entry(MISTAKES, "M-5", fives, UNKNOWN)])

    def test_adr_12_and_adr_120_are_different_numbers(self) -> None:
        text = self.decisions(entries("## ADR-12 | 2026-01-01 | 열둘", "## ADR-120 | 2026-01-02 | 백스물",
                                      "## ADR-3 | 2026-01-03 | 셋", "## ADR-3 | 2026-01-04 | 또 셋"))
        _code, report, _stdout = self.check()
        threes = line_numbers(text, "## ADR-3 | 2026-01-03 | 셋") + line_numbers(text, "## ADR-3 | 2026-01-04 | 또 셋")
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-3", threes, UNKNOWN)])

    def test_the_number_ends_at_the_first_non_digit_and_leading_zeros_do_not_count(self) -> None:
        # The label is the number as the first heading of the group writes it.
        text = self.decisions(entries("## ADR-007 | 일곱", "## ADR-70 | 일흔", "## ADR-7b | 일곱 다음"))
        _code, report, _stdout = self.check()
        sevens = line_numbers(text, "## ADR-007 | 일곱") + line_numbers(text, "## ADR-7b | 일곱 다음")
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-007", sevens, UNKNOWN)])

    def test_only_level_two_headings_count(self) -> None:
        text = self.decisions(entries("## ADR-5 | 둘째 단계", "### ADR-5 | 셋째 단계", "# ADR-5 | 첫째 단계",
                                      "    ## ADR-5 | 들여쓴 코드", "##ADR-5 | 띄어 쓰지 않음",
                                      "   ## ADR-6 | 세 칸 들여씀", "## ADR-6 | 들여쓰지 않음"))
        _code, report, _stdout = self.check()
        sixes = line_numbers(text, "   ## ADR-6 | 세 칸 들여씀") + line_numbers(text, "## ADR-6 | 들여쓰지 않음")
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-6", sixes, UNKNOWN)])

    def test_deny_zones_and_excluded_folders_are_not_scanned(self) -> None:
        twice = note("old decisions", entries("## ADR-4 | 하나", "## ADR-4 | 둘"))
        write(self.vault / "20-knowledge" / "_archive" / "old decisions.md", twice)
        write(self.vault / "drafts" / "draft decisions.md", twice)
        text = self.decisions(entries("## ADR-8 | 하나", "## ADR-8 | 둘"))
        _code, report, _stdout = self.check()
        eights = line_numbers(text, "## ADR-8 | 하나") + line_numbers(text, "## ADR-8 | 둘")
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-8", eights, UNKNOWN)])

    def test_the_exit_code_and_the_critical_count_do_not_change(self) -> None:
        clean_code, clean_report, _stdout = self.check()
        self.decisions(entries("## ADR-7 | 하나", "## ADR-7 | 둘"))
        code, report, stdout = self.check()
        self.assertEqual((clean_code, code), (0, 0), stdout)
        self.assertEqual((totals(clean_report), totals(report)), ((0, 0, 0), (1, 0, 1)))
        self.assertEqual(len(section(report)), 2, report)

        # A note without front matter is critical: exit code 1 with and without the finding.
        write(self.vault / "20-knowledge" / "broken.md", "앞머리 없는 노트\n")
        twice_code, twice_report, stdout = self.check()
        self.decisions(entries("## ADR-7 | 하나", "## ADR-8 | 둘"))
        plain_code, plain_report, _stdout = self.check()
        self.assertEqual((twice_code, plain_code), (1, 1), stdout)
        total, critical, managed = totals(plain_report)
        self.assertEqual(totals(twice_report), (total + 1, critical, managed + 1))
        self.assertEqual(len(section(twice_report)), 2, twice_report)

    def test_a_health_check_copied_alone_skips_the_section_without_an_error(self) -> None:
        # A vault copy run without the warning extension modules (whose line rule the section
        # uses) reports them as unavailable in section 15 and leaves section 14d out.
        alone = self.root / "alone"
        alone.mkdir()
        shutil.copyfile(HEALTHCHECK, alone / "vault_healthcheck.py")
        self.decisions(entries("## ADR-7 | 하나", "## ADR-7 | 둘"))
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(alone / "vault_healthcheck.py"),
             "--vault", str(self.vault), "--output", str(self.report)],
            capture_output=True, encoding="utf-8", timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report.read_bytes().decode("utf-8")
        self.assertIn("[lint-incomplete] Warning extension modules are unavailable", report)
        self.assertEqual(section(report), [])
        self.assertEqual(len(section(self.check()[1])), 2)

    def test_the_section_follows_14b_or_14c_and_precedes_15(self) -> None:
        self.decisions(entries("## ADR-7 | 하나", "## ADR-7 | 둘"))
        # A link to a heading the note does not have is a section 15 warning.
        write(self.vault / "20-knowledge" / "B.md", note("B", "[[A]] [[demo decisions#없는 제목]]\n"))
        _code, report, _stdout = self.check()
        order = headings(report)
        self.assertEqual(order[order.index("14d") - 1:order.index("14d") + 2], ["14b", "14d", "15"])
        _code, report, _stdout = self.check("--worktree-heads")
        order = headings(report)
        self.assertEqual(order[order.index("14d") - 2:order.index("14d") + 2], ["14b", "14c", "14d", "15"])


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class DuplicateEntryCommitStateTests(_EntryCase):
    """The vault is a git repository: each line says what HEAD holds."""

    def setUp(self) -> None:
        super().setUp()
        git(self.vault, "init", "-q")
        self.commit("seed")

    def commit(self, message: str, folder: Path | None = None) -> None:
        git(folder or self.vault, "add", "-A")
        git(folder or self.vault, "commit", "-q", "-m", message)

    def test_two_committed_headings_are_both_kept(self) -> None:
        first, second = "## ADR-7 | 2026-01-02 | 둘째 세션", "## ADR-7 | 2026-01-01 | 첫 세션"
        text = self.decisions(entries(first, second))
        self.commit("two sevens")
        code, report, stdout = self.check("--json-summary")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(section(report), [
            HEADER.format(count=1),
            entry(DECISIONS, "ADR-7", line_numbers(text, first) + line_numbers(text, second), KEEP_BOTH),
        ])
        self.assertEqual(totals(report), (1, 0, 1))
        self.assertEqual(self.summary(stdout)[SUMMARY_KEY], 1)

    def test_three_committed_headings_are_all_kept(self) -> None:
        adr = ("## ADR-7 | 셋", "## ADR-7 | 둘", "## ADR-7 | 하나")
        text = self.decisions(entries(*adr))
        self.commit("three sevens")
        _code, report, _stdout = self.check()
        numbers = [line_numbers(text, heading)[0] for heading in adr]
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-7", numbers, KEEP_ALL)])

    def test_an_uncommitted_second_heading_is_the_one_to_renumber(self) -> None:
        old, new = "## M-3 — 먼저 적은 실수", "## M-3 — 나중에 적은 실수"
        self.mistakes(entries(old))
        self.commit("first three")
        text = self.mistakes(entries(new, old))  # the newer entry goes on top
        _code, report, _stdout = self.check()
        old_line, new_line = line_numbers(text, old), line_numbers(text, new)
        self.assertEqual(section(report), [
            HEADER.format(count=1),
            entry(MISTAKES, "M-3", new_line + old_line, CHANGE.format(lines=joined(new_line))),
        ])

    def test_two_new_headings_are_both_named(self) -> None:
        first, second = "## M-3 — 하나", "## M-3 — 둘"
        text = self.mistakes(entries("## M-1 — 처음 실수", first, second))
        _code, report, _stdout = self.check()
        numbers = line_numbers(text, first) + line_numbers(text, second)
        self.assertEqual(section(report), [
            HEADER.format(count=1), entry(MISTAKES, "M-3", numbers, CHANGE.format(lines=joined(numbers))),
        ])

    def test_a_note_staged_but_never_committed_has_no_committed_heading(self) -> None:
        first, second = "## ADR-2 | 하나", "## ADR-2 | 둘"
        path = "20-knowledge/new decisions.md"
        text = note("new decisions", entries(first, second))
        write(self.vault / path, text)
        git(self.vault, "add", "--", path)
        _code, report, _stdout = self.check()
        numbers = line_numbers(text, first) + line_numbers(text, second)
        self.assertEqual(section(report), [
            HEADER.format(count=1), entry(path, "ADR-2", numbers, CHANGE.format(lines=joined(numbers))),
        ])

    def test_an_untracked_note_says_the_state_is_unknown(self) -> None:
        first, second = "## ADR-2 | 하나", "## ADR-2 | 둘"
        path = "20-knowledge/new decisions.md"
        text = note("new decisions", entries(first, second))
        write(self.vault / path, text)
        _code, report, _stdout = self.check()
        numbers = line_numbers(text, first) + line_numbers(text, second)
        self.assertEqual(section(report), [HEADER.format(count=1), entry(path, "ADR-2", numbers, UNKNOWN)])

    def test_identical_lines_that_head_holds_fewer_times_say_the_state_is_unknown(self) -> None:
        # Git cannot tell which of two identical lines is the committed one.
        same = "## M-3 — 같은 제목"
        self.mistakes(entries(same))
        self.commit("one line")
        text = self.mistakes(entries(same, same))
        _code, report, _stdout = self.check()
        self.assertEqual(section(report), [
            HEADER.format(count=1), entry(MISTAKES, "M-3", line_numbers(text, same), UNKNOWN),
        ])
        self.commit("both lines")
        _code, report, _stdout = self.check()
        self.assertEqual(section(report), [
            HEADER.format(count=1), entry(MISTAKES, "M-3", line_numbers(text, same), KEEP_BOTH),
        ])

    def test_a_note_with_a_byte_order_mark_reads_alike_at_head(self) -> None:
        # Full mode drops a UTF-8 byte order mark when it reads a note; the blob at HEAD keeps it.
        first, second = "## ADR-7 | 첫 줄의 결정", "## ADR-7 | 다음 결정"
        text = entries(first, second).removeprefix("# 기록\n\n")
        (self.vault / DECISIONS).write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
        self.commit("a note with a byte order mark")
        _code, report, _stdout = self.check()
        numbers = line_numbers(text, first) + line_numbers(text, second)
        self.assertEqual(numbers[0], 1)
        self.assertEqual(section(report), [HEADER.format(count=1), entry(DECISIONS, "ADR-7", numbers, KEEP_BOTH)])

    def test_a_vault_in_a_repository_subfolder_reads_its_notes_at_head(self) -> None:
        repository = self.root / "repository"
        repository.mkdir()
        vault = repository / "vault"
        self.vault.rename(vault)
        (vault / ".git").rename(repository / ".git")
        self.vault = vault
        first, second = "## ADR-7 | 하나", "## ADR-7 | 둘"
        text = self.decisions(entries(first, second))
        self.commit("move the vault into a subfolder", folder=repository)
        new = "## M-1 — 나중에 적은 같은 번호"
        mistakes = self.mistakes(entries(new, "## M-1 — 처음 실수"))
        _code, report, _stdout = self.check()
        ones = line_numbers(mistakes, new) + line_numbers(mistakes, "## M-1 — 처음 실수")
        self.assertEqual(section(report), [
            HEADER.format(count=2),
            entry(DECISIONS, "ADR-7", line_numbers(text, first) + line_numbers(text, second), KEEP_BOTH),
            entry(MISTAKES, "M-1", ones, CHANGE.format(lines=joined(line_numbers(mistakes, new)))),
        ])

    def test_staged_mode_output_does_not_change(self) -> None:
        self.decisions(entries("## ADR-7 | 하나", "## ADR-8 | 둘"))
        git(self.vault, "add", "--", DECISIONS)
        distinct = run_healthcheck("--vault", str(self.vault), "--staged")
        self.decisions(entries("## ADR-7 | 하나", "## ADR-7 | 둘"))
        git(self.vault, "add", "--", DECISIONS)
        twice = run_healthcheck("--vault", str(self.vault), "--staged")
        self.assertEqual(twice, distinct)
        self.assertEqual(twice[0], 0, twice)
        self.assertNotIn("14d", twice[1] + twice[2])
        _code, report, _stdout = self.check()
        self.assertEqual(len(section(report)), 2, report)


if __name__ == "__main__":
    unittest.main()
