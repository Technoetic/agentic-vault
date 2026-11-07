"""Linked working tree copies inside a vault: the rule and the tools that apply it.

A linked working tree of the vault's own repository is a second copy of the
vault. Its .git is a file whose gitdir value names <common>/worktrees/<name>.
These tests use temporary repositories with synthetic notes only. They pin the
rule (a submodule, a nested repository and another repository's tree stay
folders), the walks that skip such a copy and report it, the path resolvers
that keep its deny zones closed, and that nothing changes without one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
GUARD = ROOT / "assets" / "adapters" / "deny-guard" / "deny-guard.py"
BEGIN = "# --- BEGIN SHARED WORKTREE RULE"
END = "# --- END SHARED WORKTREE RULE ---"
CONFIG = {
    "required_keys": ["title"],
    "enums": {},
    "deny_zones": ["10-inbox/_processed", "20-knowledge/_archive", "90-assets", ".obsidian"],
    "exclude_dirs": ["node_modules", ".git"],
}
NOTE = "---\ntitle: {title}\n---\n\nneedle {title}\n"
COPY_ROOT = ".claude/worktrees/s1"

sys.path.insert(0, str(SCRIPTS))
try:
    import backup_vault
    import vault_evidence
    import vault_healthcheck
    import vault_instruction_budget
    import vault_lesson_metrics
    import vault_proposals
    import vault_worktrees
finally:
    sys.path.remove(str(SCRIPTS))


def git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items()
           if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, env=env, capture_output=True, encoding="utf-8", timeout=60, check=False)
    if result.returncode:
        raise AssertionError(f"git {args[0]} failed: {result.stderr}")
    return result.stdout


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def gitfile(folder: Path, value: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return write(folder / ".git", f"gitdir: {value}\n")


def seed_repository(folder: Path, relative: str, text: str = "synthetic\n") -> None:
    write(folder / relative, text)
    git(folder, "init", "-q")
    git(folder, "add", ".")
    git(folder, "commit", "-q", "-m", "seed")


def report_of(vault: Path, script: Path, base: Path) -> str:
    report = base / "report.md"
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(script), "--vault", str(vault),
                             "--output", str(report)],
                            capture_output=True, encoding="utf-8", timeout=60, check=False)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return report.read_text(encoding="utf-8")


class Lab(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.vault = self.base / "vault"
        self.vault.mkdir()
        write(self.vault / "00-meta" / "vault-config.json", json.dumps(CONFIG))
        write(self.vault / "20-knowledge" / "public.md", NOTE.format(title="public"))
        write(self.vault / "10-inbox" / "_processed" / "held.md", NOTE.format(title="held"))
        write(self.vault / "20-knowledge" / "_archive" / "old.md", NOTE.format(title="old"))
        write(self.vault / "CLAUDE.md", "root rules\n")
        git(self.vault, "init", "-q")
        git(self.vault, "add", ".")
        git(self.vault, "commit", "-q", "-m", "seed")

    def add_worktree(self, relative: str = COPY_ROOT, branch: str = "s1") -> Path:
        git(self.vault, "worktree", "add", "-q", "-b", branch, relative)
        return self.vault / relative

    def add_foreign_tree(self, relative: str = "vendor/tree") -> Path:
        """A linked tree of another repository, placed inside the vault."""
        other = self.base / "other"
        other.mkdir()
        seed_repository(other, "10-inbox/_processed/a.md", NOTE.format(title="foreign"))
        git(other, "worktree", "add", "-q", "-b", "t1", str(self.vault / relative))
        return self.vault / relative

    def add_submodule(self) -> Path:
        submodule = self.vault / "vendor" / "sub"
        gitfile(submodule, "../../.git/modules/sub")
        write(submodule / "inside.md", NOTE.format(title="inside"))
        return submodule


class RuleTests(Lab):
    def scope(self):
        return vault_worktrees.WorktreeScope(self.vault)

    def test_a_linked_worktree_of_this_repository_counts(self) -> None:
        copy = self.add_worktree()
        self.assertTrue((copy / ".git").is_file())
        self.assertTrue(self.scope().linked(copy))

    def test_a_relative_gitdir_value_counts(self) -> None:
        copy = self.add_worktree()
        (copy / ".git").unlink()  # git hides this file on Windows, and a hidden file refuses a rewrite
        gitfile(copy, "../../../.git/worktrees/s1")
        self.assertTrue(self.scope().linked(copy))

    def test_a_removed_admin_folder_still_counts(self) -> None:
        # The copy keeps its zone files after the admin entry is gone.
        copy = self.add_worktree()
        shutil.rmtree(self.vault / ".git" / "worktrees" / "s1")
        self.assertTrue(self.scope().linked(copy))

    def test_a_submodule_stays_a_folder(self) -> None:
        self.assertFalse(self.scope().linked(self.add_submodule()))

    def test_a_submodule_named_worktrees_stays_a_folder(self) -> None:
        submodule = self.vault / "vendor" / "sub"
        gitfile(submodule, "../../.git/modules/worktrees/sub")
        self.assertFalse(self.scope().linked(submodule))

    def test_a_nested_repository_stays_a_folder(self) -> None:
        nested = self.vault / "vendor" / "lib"
        nested.mkdir(parents=True)
        git(nested, "init", "-q")
        self.assertFalse(self.scope().linked(nested))

    def test_a_worktree_of_another_repository_stays_a_folder(self) -> None:
        foreign = self.add_foreign_tree()
        self.assertTrue((foreign / ".git").is_file())
        self.assertFalse(self.scope().linked(foreign))

    def test_text_that_is_no_gitfile_stays_a_folder(self) -> None:
        folder = self.vault / "vendor" / "odd"
        folder.mkdir(parents=True)
        marker = folder / ".git"
        common = (self.vault / ".git").as_posix()
        for data in (b"", b"gitdir:", b"gitdir: \n", b"\xff\xfe", b"ref: refs/heads/main\n",
                     f"gitdir: {common}/worktrees/x\nsecond line\n".encode("utf-8"),
                     f"gitdir: {common}/worktrees/x\x00\n".encode("utf-8"),
                     b"gitdir: " + b"a/" * 3000 + b"worktrees/x\n"):
            with self.subTest(data=data[:40]):
                marker.write_bytes(data)
                self.assertFalse(self.scope().linked(folder))

    def test_a_folder_named_git_is_no_gitfile(self) -> None:
        folder = self.vault / "vendor" / "odd"
        (folder / ".git").mkdir(parents=True)
        self.assertFalse(self.scope().linked(folder))

    def test_without_a_repository_nothing_counts_or_is_read(self) -> None:
        plain = self.base / "plain"
        copy = plain / "wt"
        gitfile(copy, str(self.base / "x" / ".git" / "worktrees" / "wt"))
        scope = vault_worktrees.WorktreeScope(plain)
        self.assertIsNone(scope.common)
        with mock.patch.object(vault_worktrees.os, "lstat", side_effect=AssertionError("no lookup")):
            self.assertFalse(scope.linked(copy))
            self.assertEqual(scope.views(("wt", "10-inbox", "_processed", "a.md")), ())

    def test_a_vault_that_is_a_worktree_finds_sibling_trees_and_is_no_copy_of_itself(self) -> None:
        tree = self.add_worktree()
        nested = tree / "inner"
        git(tree, "worktree", "add", "-q", "-b", "s2", str(nested))
        scope = vault_worktrees.WorktreeScope(tree)
        self.assertIsNotNone(scope.common)
        self.assertTrue(scope.linked(nested))
        self.assertEqual(scope.views(("10-inbox", "_processed", "held.md")), ())
        self.assertEqual(scope.views(("inner", "10-inbox", "_processed", "held.md")),
                         (("10-inbox", "_processed", "held.md"),))

    def test_views_start_at_the_tree_and_every_tree_gives_one(self) -> None:
        tree = self.add_worktree()
        nested = tree / "inner"
        git(self.vault, "worktree", "add", "-q", "-b", "s2", str(nested))
        scope = self.scope()
        copy = tuple(COPY_ROOT.split("/"))
        parts = (*copy, "inner", "a", "b.md")
        self.assertEqual(scope.views(parts), (("inner", "a", "b.md"), ("a", "b.md")))
        self.assertTrue(scope.inside(parts))
        self.assertFalse(scope.inside(("20-knowledge", "public.md")))
        self.assertTrue(scope.denied((*copy, "inner", "90-assets", "x.png"), ("90-assets",)))
        self.assertTrue(scope.denied((*copy, "10-inbox", "_processed", "held.md"), ("10-inbox/_processed",)))
        self.assertFalse(scope.denied(("notes", "10-inbox", "_processed", "held.md"), ("10-inbox/_processed",)))
        self.assertFalse(scope.denied((*copy, "20-knowledge", "public.md"), ("10-inbox/_processed",)))

    def test_a_vault_in_a_subfolder_of_the_repository_keeps_its_zones(self) -> None:
        repository = self.base / "repository"
        repository.mkdir()
        seed_repository(repository, "Vault/10-inbox/_processed/a.md")
        git(repository, "worktree", "add", "-q", "-b", "s1", "Vault/copy")
        scope = vault_worktrees.WorktreeScope(repository / "Vault")
        self.assertTrue(scope.linked(repository / "Vault" / "copy"))
        self.assertEqual(scope.views(("copy", "Vault", "10-inbox", "_processed", "a.md")),
                         (("10-inbox", "_processed", "a.md"),))
        self.assertEqual(scope.views(("copy", "elsewhere", "a.md")), ())
        self.assertTrue(scope.inside(("copy", "elsewhere", "a.md")))

    @unittest.skipUnless(os.name == "nt", "UNC paths exist on Windows only")
    def test_a_gitdir_on_another_host_is_never_looked_up(self) -> None:
        folder = self.vault / "vendor" / "remote"
        gitfile(folder, "//other-host/share/.git/worktrees/x")
        plain = self.base / "plain"
        gitfile(plain, "//other-host/share/.git/modules/x")
        with mock.patch.object(vault_worktrees.os.path, "realpath", side_effect=AssertionError("no network lookup")):
            self.assertFalse(vault_worktrees.linked_worktree(str(folder), "c:\\repo\\.git"))
            self.assertIsNone(vault_worktrees.repository_common(str(plain)))


class SharedBlockTests(unittest.TestCase):
    @staticmethod
    def block(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        start = text.index("\n", text.index(BEGIN)) + 1
        return text[start:text.index(END)]

    def test_the_guard_and_the_healthcheck_carry_a_verbatim_copy(self) -> None:
        # Both run alone from a vault's 00-meta/scripts, so neither can import the module.
        source = self.block(SCRIPTS / "vault_worktrees.py")
        for path in (GUARD, SCRIPTS / "vault_healthcheck.py"):
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(encoding="utf-8").count(BEGIN), 1)
                self.assertEqual(self.block(path), source)

    def test_the_hardening_guide_states_the_boundary(self) -> None:
        text = (ROOT / "docs" / "hardening.md").read_text(encoding="utf-8")
        section = text[text.index("## Linked worktree copies and deny zones"):text.index("## 측정과 보정")]
        for literal in ("`exclude_dirs` does not hold `.claude/worktrees`", "`vault_ssot.py`", "Not measured:",
                        "https://code.claude.com/docs/en/worktrees", "`0574c192aca7bd43`", "`.worktreeinclude`",
                        "`WorktreeScope.registered`", "`vault_worktrees.checked_note_path`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_the_healthcheck_alone_applies_the_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            vault = base / "vault"
            vault.mkdir()
            write(vault / "00-meta" / "vault-config.json", json.dumps(CONFIG))
            seed_repository(vault, "notes/a.md", NOTE.format(title="a"))
            git(vault, "worktree", "add", "-q", "-b", "s1", COPY_ROOT)
            alone = base / "alone"
            alone.mkdir()
            shutil.copyfile(SCRIPTS / "vault_healthcheck.py", alone / "vault_healthcheck.py")
            text = report_of(vault, alone / "vault_healthcheck.py", base)
        self.assertEqual([line for line in text.splitlines() if COPY_ROOT in line],
                         [f"- {COPY_ROOT}/ (연결 작업 트리 사본)"])


class WalkTests(Lab):
    def recall(self) -> dict:
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "vault_recall.py"), "--vault", str(self.vault),
             "--query", "needle", "--limit", "50", "--format", "json"],
            capture_output=True, encoding="utf-8", timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def healthcheck(self) -> str:
        return report_of(self.vault, SCRIPTS / "vault_healthcheck.py", self.base)

    def config(self) -> dict:
        return vault_healthcheck.validate_config(json.loads(json.dumps(CONFIG)))

    def test_recall_skips_the_copy_and_reports_it(self) -> None:
        before = self.recall()
        self.assertNotIn("skipped_worktrees", before["diagnostics"])
        self.add_worktree()
        after = self.recall()
        self.assertEqual([match["path"] for match in after["matches"]],
                         [match["path"] for match in before["matches"]])
        self.assertEqual(after["context"], before["context"])
        self.assertEqual(after["diagnostics"]["skipped_worktrees"], [COPY_ROOT])
        self.assertTrue(after["diagnostics"]["search_complete"])

    def test_recall_keeps_submodule_notes(self) -> None:
        self.add_submodule()
        answer = self.recall()
        self.assertIn("vendor/sub/inside.md", [match["path"] for match in answer["matches"]])
        self.assertNotIn("skipped_worktrees", answer["diagnostics"])

    def test_recall_keeps_a_tree_of_another_repository(self) -> None:
        tree = self.add_foreign_tree()
        write(tree / "kept.md", NOTE.format(title="kept"))
        self.assertIn("vendor/tree/kept.md", [match["path"] for match in self.recall()["matches"]])

    def test_healthcheck_reports_the_copy_once_and_scans_none_of_it(self) -> None:
        before = self.healthcheck()
        self.assertRegex(before, r"## 14b\. .*\(0\)")
        self.assertNotIn("연결 작업 트리", before)
        self.add_worktree()
        after = self.healthcheck()
        self.assertRegex(after, r"## 14b\. .*\(1\)")
        self.assertEqual([line for line in after.splitlines() if COPY_ROOT in line],
                         [f"- {COPY_ROOT}/ (연결 작업 트리 사본)"])

    def test_healthcheck_stays_silent_about_a_copy_inside_a_deny_zone(self) -> None:
        self.add_worktree("90-assets/copy")
        self.assertNotIn("90-assets/copy", self.healthcheck())

    def test_healthcheck_walk_without_a_copy_is_the_plain_list(self) -> None:
        walked = sorted(vault_healthcheck.rel_posix(path, self.vault) for path in
                        vault_healthcheck.collect_md_files(self.vault, {".git"}))
        self.assertEqual(walked, ["10-inbox/_processed/held.md", "20-knowledge/_archive/old.md",
                                  "20-knowledge/public.md", "CLAUDE.md"])

    def test_healthcheck_walk_keeps_submodules_and_names_the_copy(self) -> None:
        self.add_worktree()
        self.add_submodule()
        skipped: list[str] = []
        trees: list[str] = []
        walked = [vault_healthcheck.rel_posix(path, self.vault) for path in
                  vault_healthcheck.collect_md_files(self.vault, {".git"}, skipped, linked_trees=trees)]
        self.assertIn("vendor/sub/inside.md", walked)
        self.assertFalse([rel for rel in walked if rel.startswith(".claude/")])
        self.assertEqual((skipped, trees), ([], [COPY_ROOT + "/"]))
        fallback: list[str] = []
        vault_healthcheck.collect_md_files(self.vault, {".git"}, fallback)
        self.assertEqual(fallback, [COPY_ROOT + "/"])

    def test_backup_leaves_the_copy_out_and_says_so(self) -> None:
        self.add_worktree()
        self.add_submodule()
        skipped: list[str] = []
        snapshot = backup_vault.create_backup(self.vault, self.base / "backups", skipped=skipped)
        manifest = backup_vault.verify_snapshot(snapshot)
        self.assertEqual(skipped, [COPY_ROOT])
        self.assertFalse([name for name in (*manifest["files"], *manifest["directories"])
                          if name.startswith(COPY_ROOT)])
        self.assertIn("vendor/sub/inside.md", manifest["files"])
        self.assertIn("20-knowledge/public.md", manifest["files"])

    def test_backup_cli_prints_the_skip_only_when_there_is_one(self) -> None:
        def run() -> list[str]:
            result = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "backup_vault.py"), "--vault", str(self.vault),
                 "--target", str(self.base / "backups")],
                capture_output=True, encoding="utf-8", timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.splitlines()
        self.assertEqual(len(run()), 1)
        self.add_worktree()
        lines = run()
        self.assertEqual(len(lines), 2)
        self.assertIn(COPY_ROOT, lines[0])
        self.assertTrue(lines[1].startswith("Snapshot: "))

    def test_backup_inventory_without_a_copy_is_the_same(self) -> None:
        self.assertEqual(backup_vault._inventory(self.vault, {".git"}, []),
                         backup_vault._inventory(self.vault, {".git"}, [], skipped=[]))

    def test_instruction_inventory_skips_a_copy_and_reports_it(self) -> None:
        write(self.vault / ".agents" / "guide.md", "plain\n")
        git(self.vault, "add", ".")
        git(self.vault, "commit", "-q", "-m", "agents")
        before = vault_instruction_budget.diagnose_instructions(self.vault, self.config())
        self.assertEqual(before["status"], "complete", before)
        self.add_worktree(".agents/copy")
        after = vault_instruction_budget.diagnose_instructions(self.vault, self.config())
        self.assertEqual(after["status"], "partial")
        self.assertIn({"code": "skipped_instruction_path", "path": ".agents/copy"}, after["issues"])
        kept = {key: value for key, value in after.items() if key != "issues"}
        self.assertNotIn(".agents/copy", json.dumps(kept))

    def test_instruction_import_into_a_copy_is_blocked(self) -> None:
        self.add_worktree()
        target = f"{COPY_ROOT}/CLAUDE.md"
        before = vault_instruction_budget.diagnose_instructions(self.vault, self.config())
        self.assertNotIn(target, json.dumps(before))
        write(self.vault / "CLAUDE.md", f"@{target}\n")
        after = vault_instruction_budget.diagnose_instructions(self.vault, self.config())
        self.assertTrue([issue for issue in after["issues"] if issue.get("path") == target], after)
        kept = {key: value for key, value in after.items() if key != "issues"}
        self.assertNotIn(f'"{target}"', json.dumps(kept))


class ResolverTests(Lab):
    held = f"{COPY_ROOT}/10-inbox/_processed/held.md"
    kept = f"{COPY_ROOT}/20-knowledge/public.md"
    foreign = "vendor/tree/10-inbox/_processed/a.md"

    def setUp(self) -> None:
        super().setUp()
        self.add_worktree()
        self.add_foreign_tree()

    def test_evidence_store_refuses_a_zone_in_a_copy(self) -> None:
        store = vault_evidence.Store(self.vault)
        with self.assertRaises(vault_evidence.EvidenceError) as caught:
            store.path(self.held)
        self.assertEqual(str(caught.exception), "unsafe_path")
        self.assertTrue(store.path(self.kept).is_file())
        self.assertTrue(store.path(self.foreign).is_file())

    def test_evidence_cli_refuses_a_zone_in_a_copy(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "vault_evidence.py"), "--vault", str(self.vault),
             "snapshot", "--task", "T-1", "--objective", "check", "--artifact", self.held],
            capture_output=True, encoding="utf-8", timeout=60, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe_path", result.stdout)
        self.assertNotIn("_processed", result.stdout)

    def test_proposal_store_refuses_a_zone_in_a_copy(self) -> None:
        store = vault_proposals.Store(self.vault)
        for call in (lambda: store.path(self.held), lambda: store.note_path(self.held),
                     lambda: store.note_path(self.held, respect_exclusions=True)):
            with self.assertRaises(ValueError):
                call()
        self.assertTrue(store.note_path(self.kept).is_file())
        self.assertTrue(store.note_path(self.foreign).is_file())

    def test_proposal_store_applies_exclusions_inside_a_copy(self) -> None:
        write(self.vault / COPY_ROOT / "private" / "drafts" / "a.md", "x\n")
        config = json.loads((self.vault / "00-meta" / "vault-config.json").read_text(encoding="utf-8"))
        config["exclude_dirs"].append("private/drafts")
        (self.vault / "00-meta" / "vault-config.json").write_text(json.dumps(config), encoding="utf-8")
        store = vault_proposals.Store(self.vault)
        copied = f"{COPY_ROOT}/private/drafts/a.md"
        self.assertTrue(store.note_path(copied).is_file())
        with self.assertRaises(ValueError):
            store.note_path(copied, respect_exclusions=True)

    def test_lesson_metrics_store_refuses_a_zone_in_a_copy(self) -> None:
        store = vault_lesson_metrics.Store(self.vault)
        with self.assertRaises(vault_lesson_metrics.LessonMetricsError) as caught:
            store.path(self.held)
        self.assertEqual(str(caught.exception), "unsafe_path")
        self.assertTrue(store.path(self.kept).is_file())


if __name__ == "__main__":
    unittest.main()
