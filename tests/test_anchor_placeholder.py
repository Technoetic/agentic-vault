"""The template's placeholder anchor `(없음)` is not a missing anchor line.

`/vault-init` copies `assets/templates/handoff.md`, whose anchor line holds the
placeholder `(없음)` until the first `/vault-session-end` writes a commit hash.
The health check accepts the placeholder: a vault without Git skips the check,
a Git vault is told that no session end is recorded yet, and only once the
vault folder has `anchor_drift_threshold` commits does section 12 count one
advisory issue. A handoff without an anchor line and a hex anchor keep their
results (contract tests).

Every Git repository here is made in an owned, resolved temporary folder with
the user's Git settings cut off (`isolate_git`) and a local identity, and every
commit carries a fixed date, so no result depends on the machine or the clock.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"
TEMPLATES = ROOT / "assets" / "templates"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
finally:
    sys.path.remove(str(SCRIPTS))

FIXED_DATE = "2026-01-02T12:00:00+00:00"
FIXED_DAY = "2026-01-02"
PLACEHOLDER_LINE = "**기준 커밋(anchor):** `(없음)` — 템플릿 값.\n"
VIOLATION = "계약 위반"

NOT_GIT_LINES = ["- 검사 생략: git 미사용 볼트 — anchor `(없음)`"]
MISSING_SUMMARY = "handoff에 기준 커밋(anchor) 줄이 없음 — session-end 계약 위반"
MISSING_LINES = ["- ⚠️ handoff에 `**기준 커밋(anchor):**` 줄이 없다. session-end가 갱신하는 줄이다."]


def notice_lines(count: int) -> list[str]:
    return [f"- 안내: 아직 session-end 기록이 없다(anchor `(없음)`, 커밋 {count}개). "
            "첫 /vault-session-end가 이 줄을 커밋 해시로 바꾼다."]


def advisory(count: int, threshold: int) -> tuple[str, list[str]]:
    return (f"session-end 기록 없음 — anchor `(없음)`, 커밋 {count}개",
            [f"- ⚠️ anchor가 아직 `(없음)`인데 HEAD까지 이 볼트 폴더를 바꾼 커밋이 {count}개다"
             f"(임계 {threshold}). session-end가 한 번도 기록되지 않았을 수 있다.",
             "- 조치: /vault-session-end 실행(anchor를 커밋 해시로 갱신)."])


def git(cwd: Path, *args: str) -> str:
    environment = dict(os.environ, GIT_AUTHOR_DATE=FIXED_DATE, GIT_COMMITTER_DATE=FIXED_DATE)
    result = subprocess.run(["git", *args], cwd=cwd, env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", check=False)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "Anchor Test")
    git(path, "config", "user.email", "anchor@example.invalid")


def commit_file(repo: Path, relpath: str, text: str) -> str:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", "--", relpath)
    git(repo, "commit", "-q", "-m", f"change {relpath}")
    return git(repo, "rev-parse", "HEAD")


class _OwnedRoot(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        isolate_git(self, ceiling=self.root)


class ScanAnchorDriftTests(_OwnedRoot):
    """`scan_anchor_drift(vault, "h.md", 3)` for every row of the F6 table."""

    def write_handoff(self, vault: Path, anchor_line: str = PLACEHOLDER_LINE) -> None:
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "h.md").write_text("# Handoff\n\n" + anchor_line + "\nbody\n", encoding="utf-8")

    def scan(self, vault: Path, threshold: int = 3, handoff: str = "h.md"):
        return vault_healthcheck.scan_anchor_drift(vault, handoff, threshold)

    def assert_placeholder(self, vault: Path, summary, lines) -> None:
        got_summary, got_lines = self.scan(vault)
        self.assertEqual((got_summary, got_lines), (summary, lines))
        for text in [got_summary or "", *got_lines]:
            self.assertNotIn(VIOLATION, text)

    # --- placeholder rows (fail on v0.45.1) ------------------------------------

    def test_placeholder_without_git_skips_the_check(self) -> None:
        vault = self.root / "vault"
        self.write_handoff(vault)
        self.assert_placeholder(vault, None, NOT_GIT_LINES)

    def test_placeholder_in_git_without_commit_counts_zero(self) -> None:
        vault = self.root / "vault"
        init_repo(vault)
        self.write_handoff(vault)
        self.assert_placeholder(vault, None, notice_lines(0))

    def test_placeholder_below_threshold_gives_a_notice_only(self) -> None:
        vault = self.root / "vault"
        init_repo(vault)
        commit_file(vault, "a.md", "one\n")
        commit_file(vault, "a.md", "two\n")
        self.write_handoff(vault)
        self.assert_placeholder(vault, None, notice_lines(2))

    def test_empty_commits_are_not_counted(self) -> None:
        vault = self.root / "vault"
        init_repo(vault)
        commit_file(vault, "a.md", "one\n")
        commit_file(vault, "a.md", "two\n")
        for _ in range(3):
            git(vault, "commit", "-q", "--allow-empty", "-m", "empty")
        self.write_handoff(vault)
        self.assert_placeholder(vault, None, notice_lines(2))

    def test_placeholder_at_threshold_counts_one_advisory_issue(self) -> None:
        vault = self.root / "vault"
        init_repo(vault)
        for text in ("one\n", "two\n", "three\n"):
            commit_file(vault, "a.md", text)
        self.write_handoff(vault)
        summary, lines = advisory(3, 3)
        self.assert_placeholder(vault, summary, lines)

    def test_vault_subfolder_counts_only_its_own_commits(self) -> None:
        repo = self.root / "repo"
        init_repo(repo)
        for number in range(5):
            commit_file(repo, "outside.txt", f"{number}\n")
        vault = repo / "vault"
        self.write_handoff(vault)
        self.assert_placeholder(vault, None, notice_lines(0))

        git(repo, "add", "--", "vault/h.md")
        git(repo, "commit", "-q", "-m", "add vault")
        self.assert_placeholder(vault, None, notice_lines(1))

    # --- unchanged rows (contract: pass on v0.45.1 too) ------------------------

    def test_contract_missing_anchor_line_is_still_a_violation(self) -> None:
        vault = self.root / "vault"
        self.write_handoff(vault, anchor_line="No anchor here.\n")
        self.assertEqual(self.scan(vault), (MISSING_SUMMARY, MISSING_LINES))

    def test_contract_hex_anchor_below_threshold(self) -> None:
        vault = self.root / "vault"
        init_repo(vault)
        anchor = commit_file(vault, "a.md", "one\n")
        commit_file(vault, "a.md", "two\n")
        self.write_handoff(vault, f"**기준 커밋(anchor):** `{anchor}`\n")
        self.assertEqual(self.scan(vault), (
            None, [f"- 정상: anchor `{anchor}`({FIXED_DAY}) 대비 HEAD 1커밋 (임계 3 미만)"]))

    def test_contract_hex_anchor_drift_counts_the_whole_repository(self) -> None:
        vault = self.root / "vault"
        init_repo(vault)
        anchor = commit_file(vault, "a.md", "one\n")[:7]
        commit_file(vault, "a.md", "two\n")
        for _ in range(2):
            git(vault, "commit", "-q", "--allow-empty", "-m", "empty")
        self.write_handoff(vault, f"**기준 커밋(anchor):** `{anchor}`\n")
        self.assertEqual(self.scan(vault), (
            f"anchor drift 3커밋 — session-end 누락 의심 (anchor `{anchor}` {FIXED_DAY} 이후 미갱신)",
            [f"- ⚠️ **HEAD가 anchor `{anchor}`({FIXED_DAY})보다 3커밋 앞선다** (임계 3).",
             "- 의미: 세션이 session-end 없이 닫혔을 개연성 — handoff·장기기억(remember)이 그만큼 낡았다.",
             f"- 조치: session-end 실행(anchor 갱신 + 교훈 커밋). 중간 확인: `git log --oneline {anchor}..HEAD`"]))

    def test_contract_hex_anchor_without_git(self) -> None:
        vault = self.root / "vault"
        self.write_handoff(vault, "**기준 커밋(anchor):** `abc1234`\n")
        self.assertEqual(self.scan(vault), (None, ["- 판정 불가: git 리포가 아니거나 git 실행 실패"]))

    def test_contract_disabled_or_missing_handoff(self) -> None:
        vault = self.root / "vault"
        self.write_handoff(vault)
        skipped = (None, ["- 검사 생략 (anchor_drift_threshold=0 또는 handoff_note 미설정)"])
        self.assertEqual(self.scan(vault, threshold=0), skipped)
        self.assertEqual(self.scan(vault, handoff=""), skipped)
        self.assertEqual(self.scan(vault, handoff="missing.md"),
                         (None, ["- 판정 불가: handoff 없음 (missing.md)"]))


ALWAYS = {
    "vault-config.json": "00-meta/vault-config.json",
    "frontmatter-schema.md": "00-meta/schemas/frontmatter.md",
    "hot.md": "00-meta/hot.md",
    "index.md": "00-meta/index.md",
    "log.md": "00-meta/log.md",
    "lessons.md": "00-meta/lessons.md",
}
PROJECT = ("context", "tasks", "handoff", "decisions", "mistakes")
PROJECT_HEADING = "## 프로젝트 (50-projects)\n"


class TemplateVaultTests(_OwnedRoot):
    """A vault made as `/vault-init` step 3 makes it has no anchor finding."""

    def make_template_vault(self) -> Path:
        vault = self.root / "vault"
        today = datetime.date.today().isoformat()
        targets = dict(ALWAYS)
        targets.update({f"{name}.md": f"50-projects/Demo/Demo {name}.md" for name in PROJECT})
        self.created = []
        for template, destination in targets.items():
            text = (TEMPLATES / template).read_text(encoding="utf-8")
            text = (text.replace("{{VAULT_NAME}}", "Demo Vault")
                    .replace("{{PROJECT_NAME}}", "Demo").replace("{{DATE}}", today))
            path = vault / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.created.append(path)

        config = vault / ALWAYS["vault-config.json"]
        text = config.read_text(encoding="utf-8")
        self.assertEqual(text.count('"handoff_note": ""'), 1)
        config.write_text(text.replace(
            '"handoff_note": ""', '"handoff_note": "50-projects/Demo/Demo handoff.md"'),
            encoding="utf-8")
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["handoff_note"],
                         "50-projects/Demo/Demo handoff.md")

        index = vault / ALWAYS["index.md"]
        text = index.read_text(encoding="utf-8")
        self.assertEqual(text.count(PROJECT_HEADING), 1)
        entries = "".join(f"- [[Demo {name}]] — 프로젝트 {name}\n" for name in PROJECT)
        index.write_text(text.replace(PROJECT_HEADING, PROJECT_HEADING + "\n" + entries),
                         encoding="utf-8")
        return vault

    def run_check(self, vault: Path) -> None:
        for path in self.created:
            with self.subTest(file=path.name):
                self.assertNotIn("{{", path.read_text(encoding="utf-8"))
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(HEALTHCHECK), "--vault", str(vault)],
            cwd=vault, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=120,
        )
        report_path = vault / "00-meta" / "health-report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + report)
        self.assertIn("(치명 0건)", result.stdout)
        self.assertNotIn(VIOLATION, result.stdout)
        self.assertNotIn(VIOLATION, report)
        headings = [line for line in report.splitlines() if line.startswith("## 12. ")]
        self.assertEqual(len(headings), 1, report)
        self.assertTrue(headings[0].endswith("(0)"), headings[0])

    def test_template_vault_without_git(self) -> None:
        self.run_check(self.make_template_vault())

    def test_template_vault_after_first_commit(self) -> None:
        vault = self.make_template_vault()
        init_repo(vault)
        git(vault, "add", "-A")
        git(vault, "commit", "-q", "-m", "init vault")
        self.run_check(vault)


if __name__ == "__main__":
    unittest.main()
