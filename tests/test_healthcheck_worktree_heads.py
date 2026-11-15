"""The health check's opt-in check for linked-worktree HEADs no branch or tag contains.

A commit made on a detached HEAD in a linked working tree is reachable only from
that tree. Once the tree is removed and git prunes the object, the work is gone.
`--worktree-heads` (full mode only) lists such trees in section 14c and adds the
count to the 관리성 total and, with `--json-summary`, to the summary line. Without
the flag nothing in the report, the console line or the summary changes.

Repositories are temporary and notes are synthetic. The health check runs as a
program with git's global and system settings cut off.
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
    from _git_env import git_environment
    from test_worktree_zone_paths import git, seed
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import git_environment
    from tests.test_worktree_zone_paths import git, seed


ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK = ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"
FLAG = "--worktree-heads"
HEADER = "## 14c. 분기·태그 밖 HEAD를 가진 연결 작업 트리 — 관리성 ({count})"
ENTRY = "- {path} HEAD {short} — 어느 분기·태그에도 없다. 작업 트리를 지우기 전에 `git branch <이름> <sha>`로 고정한다."
FAILED = "- (확인 실패) "
TOTAL = "관리성 "


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class WorktreeHeadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        seed(self.vault)
        self.report = self.root / "report.md"
        self.environment = git_environment()
        self.environment["GIT_CEILING_DIRECTORIES"] = str(self.root)

    def check(self, *args: str, vault: Path | None = None) -> tuple[subprocess.CompletedProcess, str]:
        """Run the health check on the vault; returns the process and the report text ('' in staged mode)."""
        if self.report.exists():
            self.report.unlink()
        extra = () if "--staged" in args else ("--output", str(self.report))
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(HEALTHCHECK), "--vault", str(vault or self.vault), *extra, *args],
            capture_output=True, encoding="utf-8", timeout=120, check=False, env=self.environment)
        text = self.report.read_bytes().decode("utf-8") if self.report.exists() else ""
        return result, text

    def detached_tree(self, location: Path) -> str:
        """Add a linked tree on a detached HEAD at location and commit there; returns the new commit."""
        git(self.vault, "worktree", "add", "-q", "--detach", str(location))
        (location / "work.txt").write_text("work in a linked tree\n", encoding="utf-8", newline="")
        git(location, "add", "work.txt")
        git(location, "commit", "-q", "-m", "detached work")
        return git(location, "rev-parse", "HEAD").strip()

    @staticmethod
    def section(report: str) -> list[str]:
        """The lines of section 14c up to the next blank line (the report may end lines with CRLF)."""
        lines = report.splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("## 14c. "))
        end = next((index for index in range(start, len(lines)) if not lines[index]), len(lines))
        return lines[start:end]

    @staticmethod
    def totals(report: str) -> tuple[int, int]:
        """(관리성 total, 총 이슈) from the report's count line."""
        line = next(line for line in report.splitlines() if line.startswith("- 검사 노트 수:"))
        total = int(line.split("총 이슈: **")[1].split("**")[0])
        managed = int(line.split(TOTAL)[-1].rstrip(")"))
        return managed, total

    def test_a_detached_head_no_branch_contains_is_listed_until_a_branch_holds_it(self) -> None:
        plain, plain_report = self.check()
        tree = self.root / "detached tree"
        sha = self.detached_tree(tree)
        flagged, report = self.check(FLAG)
        self.assertEqual(flagged.returncode, plain.returncode, flagged.stderr)
        self.assertEqual(self.section(report), [
            HEADER.format(count=1),
            ENTRY.format(path=tree.as_posix(), short=sha[:12]),
        ])
        before_managed, before_total = self.totals(plain_report)
        self.assertEqual(self.totals(report), (before_managed + 1, before_total + 1))

        git(self.vault, "branch", "keep", sha)
        kept, kept_report = self.check(FLAG)
        self.assertEqual(kept.returncode, plain.returncode, kept.stderr)
        self.assertEqual(self.section(kept_report), [HEADER.format(count=0), "- 없음"])
        self.assertEqual(self.totals(kept_report), (before_managed, before_total))

    def test_a_tag_also_holds_a_detached_head(self) -> None:
        sha = self.detached_tree(self.root / "tagged tree")
        git(self.vault, "tag", "kept-by-tag", sha)
        _result, report = self.check(FLAG)
        self.assertEqual(self.section(report), [HEADER.format(count=0), "- 없음"])

    def test_the_section_follows_section_14b(self) -> None:
        self.detached_tree(self.root / "detached tree")
        _result, report = self.check(FLAG)
        headings = [line for line in report.splitlines() if line.startswith("## ")]
        position = next(index for index, line in enumerate(headings) if line.startswith("## 14c. "))
        self.assertTrue(headings[position - 1].startswith("## 14b. "), headings)

    def test_without_the_flag_the_report_and_console_line_do_not_change(self) -> None:
        before, before_report = self.check()
        self.detached_tree(self.root / "detached tree")
        after, after_report = self.check()
        self.assertEqual(after_report.encode("utf-8"), before_report.encode("utf-8"))
        self.assertEqual(after.stdout, before.stdout)
        self.assertEqual(after.returncode, before.returncode)
        self.assertNotIn("14c", after_report)
        self.assertNotIn("14c", after.stdout + after.stderr)

    def test_a_tree_on_a_branch_is_not_listed(self) -> None:
        tree = self.root / "branch tree"
        git(self.vault, "worktree", "add", "-q", "-b", "feature", str(tree))
        (tree / "work.txt").write_text("work on a branch\n", encoding="utf-8", newline="")
        git(tree, "add", "work.txt")
        git(tree, "commit", "-q", "-m", "branch work")
        _result, report = self.check(FLAG)
        self.assertEqual(self.section(report), [HEADER.format(count=0), "- 없음"])
        self.assertNotIn(tree.as_posix(), report)

    def test_a_tree_inside_the_vault_prints_a_vault_relative_path(self) -> None:
        sha = self.detached_tree(self.vault / ".claude" / "worktrees" / "d1")
        _result, report = self.check(FLAG)
        self.assertEqual(self.section(report), [
            HEADER.format(count=1),
            ENTRY.format(path=".claude/worktrees/d1", short=sha[:12]),
        ])

    def test_the_main_tree_prints_as_a_dot(self) -> None:
        git(self.vault, "checkout", "-q", "--detach")
        (self.vault / "work.txt").write_text("work in the main tree\n", encoding="utf-8", newline="")
        git(self.vault, "add", "work.txt")
        git(self.vault, "commit", "-q", "-m", "main tree work")
        sha = git(self.vault, "rev-parse", "HEAD").strip()
        _result, report = self.check(FLAG)
        self.assertEqual(self.section(report), [
            HEADER.format(count=1),
            ENTRY.format(path=".", short=sha[:12]),
        ])

    def test_a_prunable_tree_is_still_listed(self) -> None:
        tree = self.root / "removed tree"
        sha = self.detached_tree(tree)
        shutil.rmtree(tree)
        self.assertIn("prunable", git(self.vault, "worktree", "list", "--porcelain"))
        _result, report = self.check(FLAG)
        self.assertEqual(self.section(report), [
            HEADER.format(count=1),
            ENTRY.format(path=tree.as_posix(), short=sha[:12]),
        ])

    def test_a_vault_without_a_repository_reports_a_failure_line_and_counts_nothing(self) -> None:
        plain_vault = self.root / "plain vault"
        shutil.copytree(self.vault, plain_vault, ignore=shutil.ignore_patterns(".git"))
        plain, plain_report = self.check(vault=plain_vault)
        flagged, report = self.check(FLAG, vault=plain_vault)
        self.assertEqual(flagged.returncode, plain.returncode, flagged.stderr)
        section = self.section(report)
        self.assertEqual(section[0], HEADER.format(count=0))
        self.assertEqual(len(section), 2, section)
        self.assertTrue(section[1].startswith(FAILED), section)
        self.assertEqual(self.totals(report), self.totals(plain_report))

    def test_staged_mode_refuses_the_flag(self) -> None:
        result, _report = self.check("--staged", FLAG)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(FLAG, result.stderr)

    def test_the_summary_carries_the_count_only_with_the_flag(self) -> None:
        self.detached_tree(self.root / "detached tree")
        plain, _report = self.check("--json-summary")
        flagged, _report = self.check("--json-summary", FLAG)
        plain_summary = json.loads(plain.stdout.splitlines()[-1])
        flagged_summary = json.loads(flagged.stdout.splitlines()[-1])
        self.assertNotIn("unreachable_worktree_heads", plain_summary)
        self.assertEqual(flagged_summary.pop("unreachable_worktree_heads"), 1)
        self.assertEqual(flagged_summary.pop("issues"), plain_summary.pop("issues") + 1)
        self.assertEqual(flagged_summary, plain_summary)


if __name__ == "__main__":
    unittest.main()
