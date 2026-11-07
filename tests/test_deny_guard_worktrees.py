"""Deny-zone guard and copies of the vault in linked working trees.

The zone rule stays root-anchored. The guard adds a second view of a path: its
parts from the top of a working tree of the vault's own repository. A copy of a
zone is then closed whichever tree the session runs in, including the main
checkout when CLAUDE_PROJECT_DIR is a linked tree. Repositories are temporary
and synthetic, and the PowerShell branch uses a stand-in for its parser.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _git_env import git_environment
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import git_environment


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "assets" / "adapters" / "deny-guard" / "deny-guard.py"
ZONES = ("10-inbox/_processed", "20-knowledge/_archive", "50-projects/_completed", "90-assets", ".obsidian")
TREE = ".claude/worktrees/s1"
_LOADED: dict = {}


def load_guard():
    if "guard" not in _LOADED:
        spec = importlib.util.spec_from_file_location("deny_guard_asset_worktrees", GUARD)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED["guard"] = module
    return _LOADED["guard"]


def git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, env=git_environment(), capture_output=True, encoding="utf-8", timeout=60, check=False)
    if result.returncode:
        raise AssertionError(f"git {args[0]} failed: {result.stderr}")


def facts(**fields) -> dict:
    answer = {"errors": 0, "commands": [], "strings": [], "expandable": 0, "expandable_strings": [], "flags": []}
    answer.update(fields)
    return answer


def seed(vault: Path, repository: Path | None = None) -> None:
    for rel in ("10-inbox/_processed/held.md", "20-knowledge/_archive/old.md", "50-projects/_completed/done.md",
                "90-assets/a.png", ".obsidian/app.json", "notes/public.md"):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic\n", encoding="utf-8")
    config = vault / "00-meta" / "vault-config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"deny_zones": list(ZONES)}), encoding="utf-8")
    top = repository or vault
    git(top, "init", "-q")
    git(top, "add", ".")
    git(top, "commit", "-q", "-m", "seed")


class Lab(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = load_guard()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.vault = self.base / "vault"
        seed(self.vault)
        git(self.vault, "worktree", "add", "-q", "-b", "s1", TREE)
        self.tree = self.vault / TREE

    def decide(self, tool: str, tool_input: dict, project: Path | None = None, cwd: Path | None = None) -> str | None:
        project = project or self.vault
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or project)}
        result = self.guard.evaluate(payload, str(project))
        return None if result is None else result[0]


class ProjectIsTheMainCheckout(Lab):
    def test_copies_of_every_zone_are_denied_for_path_tools(self) -> None:
        for cwd in (self.vault, self.tree):
            for rel in ("10-inbox/_processed/held.md", "20-knowledge/_archive/old.md",
                        "50-projects/_completed/done.md", "90-assets/a.png", ".obsidian/app.json"):
                with self.subTest(cwd=cwd.name, rel=rel):
                    target = str(self.tree / rel)
                    self.assertEqual(self.decide("Read", {"file_path": target}, cwd=cwd), "deny")
                    self.assertEqual(self.decide("Write", {"file_path": target, "content": "x"}, cwd=cwd), "deny")
                    self.assertEqual(self.decide(
                        "Edit", {"file_path": target, "old_string": "a", "new_string": "b"}, cwd=cwd), "deny")

    def test_a_note_in_the_copy_keeps_no_decision(self) -> None:
        self.assertIsNone(self.decide("Read", {"file_path": str(self.tree / "notes" / "public.md")}))
        self.assertIsNone(self.decide("Grep", {"path": str(self.tree), "pattern": "x"}))
        self.assertIsNone(self.decide("Read", {"file_path": str(self.tree / "00-meta" / "vault-config.json")}))

    def test_the_copy_of_a_guard_control_stays_as_it_was(self) -> None:
        target = str(self.tree / "00-meta" / "vault-config.json")
        self.assertIsNone(self.decide("Write", {"file_path": target, "content": "{}"}))

    def test_searches_into_a_copied_zone_are_denied(self) -> None:
        zone = str(self.tree / "10-inbox" / "_processed")
        self.assertEqual(self.decide("Grep", {"path": zone, "pattern": "x"}), "deny")
        self.assertEqual(self.decide("Grep", {"path": str(self.tree), "pattern": "x",
                                              "glob": "10-inbox/_processed/**"}), "deny")
        self.assertEqual(self.decide("Glob", {"path": str(self.tree), "pattern": "10-inbox/_processed/**"}), "deny")
        self.assertEqual(self.decide("Glob", {"pattern": f"{TREE}/10-inbox/_processed/*"}), "deny")
        self.assertEqual(self.decide("Glob", {"pattern": "**/_processed/*"}), "ask")

    def test_shell_words_into_a_copied_zone_are_denied(self) -> None:
        rel = f"{TREE}/10-inbox/_processed/held.md"
        for command in (f"cat {rel}", f"ls {TREE}/10-inbox/_processed", f"grep -r x {TREE}/10-inbox/_processed",
                        f"cp {rel} /tmp/out", f"rg x {TREE}/10-inbox/_processed"):
            with self.subTest(command=command):
                self.assertEqual(self.decide("Bash", {"command": command}), "deny")
                self.assertEqual(self.decide("Monitor", {"command": command}), "deny")
        self.assertEqual(self.decide("Bash", {"command": "cat 10-inbox/_processed/held.md"}, cwd=self.tree), "deny")
        self.assertIsNone(self.decide("Bash", {"command": f"cat {TREE}/notes/public.md"}))

    def test_powershell_literal_into_a_copied_zone_is_denied(self) -> None:
        target = f"{TREE}/10-inbox/_processed/held.md"
        answer = facts(commands=["Get-Content"], strings=["Get-Content", target])
        with mock.patch.object(self.guard, "parse_powershell", return_value=(answer, "")):
            self.assertEqual(self.decide("PowerShell", {"command": f"Get-Content {target}"}), "deny")

    def test_a_working_directory_inside_a_copied_zone_asks(self) -> None:
        zone = self.tree / "10-inbox" / "_processed"
        self.assertEqual(self.decide("Bash", {"command": "ls"}, cwd=zone), "ask")

    def test_a_tree_outside_the_vault_counts_when_it_is_this_repository(self) -> None:
        outside = self.base / "beside"
        git(self.vault, "worktree", "add", "-q", "-b", "s2", str(outside))
        self.assertEqual(self.decide("Read", {"file_path": str(outside / "10-inbox" / "_processed" / "held.md")}), "deny")
        self.assertEqual(self.decide("Read", {"file_path": str(outside / "90-assets" / "a.png")}), "deny")
        self.assertIsNone(self.decide("Read", {"file_path": str(outside / "notes" / "public.md")}))

    def test_another_repository_stays_outside(self) -> None:
        other = self.base / "other"
        seed(other)
        beside = self.base / "other-tree"
        git(other, "worktree", "add", "-q", "-b", "t1", str(beside))
        for target in (other / "10-inbox" / "_processed" / "held.md", other / "90-assets" / "a.png",
                       beside / "10-inbox" / "_processed" / "held.md", beside / "90-assets" / "a.png"):
            with self.subTest(target=target.parent.name):
                self.assertIsNone(self.decide("Read", {"file_path": str(target)}))

    def test_a_worktree_of_another_repository_inside_the_vault_keeps_the_root_contract(self) -> None:
        other = self.base / "other"
        seed(other)
        inside = self.vault / "notes" / "tree"
        git(other, "worktree", "add", "-q", "-b", "t1", str(inside))
        self.assertIsNone(self.decide("Read", {"file_path": str(inside / "10-inbox" / "_processed" / "held.md")}))
        self.assertEqual(self.decide("Read", {"file_path": str(inside / "90-assets" / "a.png")}), "deny")

    def test_a_submodule_keeps_the_root_contract(self) -> None:
        submodule = self.vault / "notes" / "sub"
        target = submodule / "10-inbox" / "_processed" / "a.md"
        target.parent.mkdir(parents=True)
        target.write_text("synthetic\n", encoding="utf-8")
        (submodule / ".git").write_text("gitdir: ../../.git/modules/sub\n", encoding="utf-8")
        self.assertIsNone(self.decide("Read", {"file_path": str(target)}))

    def test_a_vault_in_a_repository_subfolder_uses_the_same_subfolder_of_the_tree(self) -> None:
        repository = self.base / "repository"
        vault = repository / "Vault"
        seed(vault, repository)
        git(repository, "worktree", "add", "-q", "-b", "s1", "Vault/copy")
        copy = vault / "copy" / "Vault"
        self.assertEqual(self.decide("Read", {"file_path": str(copy / "10-inbox" / "_processed" / "held.md")},
                                     project=vault), "deny")
        self.assertIsNone(self.decide("Read", {"file_path": str(vault / "copy" / "10-inbox" / "_processed" / "x.md")},
                                      project=vault))

    def test_no_lookup_on_another_host(self) -> None:
        seen: list[str] = []
        real = os.lstat

        def spy(path, *args, **kwargs):
            seen.append(os.fspath(path))
            return real(path, *args, **kwargs)
        with mock.patch.object(self.guard.os, "lstat", spy):
            self.decide("Read", {"file_path": "//other-host/share/10-inbox/_processed/a.md"})
        self.assertFalse([path for path in seen if "other-host" in path])

    def test_a_vault_without_a_repository_reads_no_gitfile(self) -> None:
        plain = self.base / "plain"
        (plain / "00-meta").mkdir(parents=True)
        (plain / "00-meta" / "vault-config.json").write_text(json.dumps({"deny_zones": list(ZONES)}), encoding="utf-8")
        copy = plain / "copy"
        copy.mkdir()
        (copy / ".git").write_text(f"gitdir: {self.vault.as_posix()}/.git/worktrees/s1\n", encoding="utf-8")
        target = copy / "10-inbox" / "_processed" / "a.md"
        self.assertIsNone(self.decide("Read", {"file_path": str(target)}, project=plain))


class ProjectIsALinkedTree(Lab):
    def test_the_main_checkout_keeps_its_zones(self) -> None:
        for rel in ("10-inbox/_processed/held.md", "20-knowledge/_archive/old.md",
                    "50-projects/_completed/done.md", "90-assets/a.png", ".obsidian/app.json"):
            with self.subTest(rel=rel):
                target = str(self.vault / rel)
                self.assertEqual(self.decide("Read", {"file_path": target}, project=self.tree), "deny")
                self.assertEqual(self.decide("Write", {"file_path": target, "content": "x"},
                                             project=self.tree), "deny")

    def test_the_tree_itself_stays_closed(self) -> None:
        target = str(self.tree / "10-inbox" / "_processed" / "held.md")
        self.assertEqual(self.decide("Read", {"file_path": target}, project=self.tree), "deny")

    def test_notes_of_the_main_checkout_keep_no_decision(self) -> None:
        self.assertIsNone(self.decide("Read", {"file_path": str(self.vault / "notes" / "public.md")}, project=self.tree))

    def test_a_sibling_tree_is_closed_too(self) -> None:
        sibling = self.base / "sibling"
        git(self.vault, "worktree", "add", "-q", "-b", "s2", str(sibling))
        target = str(sibling / "10-inbox" / "_processed" / "held.md")
        self.assertEqual(self.decide("Read", {"file_path": target}, project=self.tree), "deny")

    def test_shell_words_and_globs_into_the_main_checkout(self) -> None:
        zone = (self.vault / "10-inbox" / "_processed").as_posix()
        spellings = [Path(os.path.relpath(zone, self.tree)).as_posix()]
        if "[" not in zone:  # a bracket makes the word a glob, and the guard then asks
            spellings.append(zone)
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                self.assertEqual(self.decide("Bash", {"command": f'cat "{spelling}/held.md"'},
                                             project=self.tree), "deny")
                answer = facts(commands=["Get-Content"], strings=["Get-Content", f"{spelling}/held.md"])
                with mock.patch.object(self.guard, "parse_powershell", return_value=(answer, "")):
                    self.assertEqual(self.decide("PowerShell", {"command": f'Get-Content "{spelling}/held.md"'},
                                                 project=self.tree), "deny")
        self.assertEqual(self.decide("Glob", {"path": str(self.vault), "pattern": "10-inbox/_processed/**"},
                                     project=self.tree), "deny")
        self.assertEqual(self.decide("Glob", {"path": str(self.vault), "pattern": "*/90-assets/*"},
                                     project=self.tree), "deny")

    def test_a_main_checkout_beside_the_tree_is_reached_by_a_wildcard_pattern(self) -> None:
        beside = self.base / "beside"
        git(self.vault, "worktree", "add", "-q", "-b", "s2", str(beside))
        self.assertEqual(self.decide("Glob", {"path": str(self.vault), "pattern": "*/90-assets/*"},
                                     project=beside), "deny")
        self.assertEqual(self.decide("Glob", {"path": str(self.vault), "pattern": "*/_processed/*"},
                                     project=beside), "ask")


class ProcessContract(Lab):
    def run_hook(self, project: Path, target: Path, cwd: Path) -> dict | None:
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(target)}, "cwd": str(cwd)}
        result = subprocess.run([sys.executable, "-X", "utf8", "-I", str(GUARD)], input=json.dumps(payload),
                                env=env, capture_output=True, encoding="utf-8", timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def test_the_script_denies_in_both_directions_and_names_no_path(self) -> None:
        held = "10-inbox/_processed/held.md".split("/")
        for project, target in ((self.vault, self.tree.joinpath(*held)), (self.tree, self.vault.joinpath(*held))):
            with self.subTest(project=project.name):
                specific = self.run_hook(project, target, project)["hookSpecificOutput"]
                self.assertEqual(specific["permissionDecision"], "deny")
                self.assertNotIn(self.base.name, specific["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
