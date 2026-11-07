"""Deny zones in linked working trees, through every tool that takes a named path.

A path a caller names, by argument or through vault-config.json, is resolved
from the vault root, where a zone in a copy does not match. Each tool below must
refuse it the way it refuses the same zone in the main checkout. A walk or a
backup leaves a folder out only when git's admin folder names that folder back,
so a hand-written .git file hides nothing, and a zone stays closed either way.
Repositories are temporary and notes are synthetic.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

try:
    from _git_env import git_environment
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import git_environment


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HOOKS = ROOT / "hooks"
TEMPLATE = ROOT / "assets" / "templates" / "vault-config.json"
COPY = ".claude/worktrees/s1"
MARK = "SECRETMARK"
NOTE = "---\ntitle: {title}\ntype: concept\nstatus: active\n---\n\nneedle {title}\n"
ZONE_NOTE = NOTE.format(title=MARK)
HELD = "10-inbox/_processed/secret.md"
HELD_JSON = "10-inbox/_processed/secret.json"
ARCHIVED = "20-knowledge/_archive/lessons.md"
CUSTOM = "20-knowledge/hold/secret.md"
OPEN = "20-knowledge/open.md"
HANDOFF = "50-projects/demo/handoff.md"
LESSON = ('- 2026-01-01 | 횟수 1 | zone lesson | 근거: x | 상태: 관찰중 <!-- agentic-vault:lesson '
          '{"id":"L-001","count":1,"state":"active","references":[],"cues":[{"kind":"hint","text":"FROM ZONE",'
          '"command_contains":["zz"]}]} -->\n')

for folder in (str(SCRIPTS), str(HOOKS)):
    sys.path.insert(0, folder)
try:
    import backup_vault
    import jarvis_bridge
    import session_start
    import vault_capacity
    import vault_doctor
    import vault_evidence
    import vault_handoff_fold
    import vault_healthcheck
    import vault_impact
    import vault_index_fold
    import vault_instruction_budget
    import vault_lesson_delta
    import vault_memory
    import vault_ontology
    import vault_operations
    import vault_query_expansion
    import vault_retrieval
    import vault_revocation
    import vault_runs
    import vault_state
    import vault_worktrees
finally:
    for folder in (str(SCRIPTS), str(HOOKS)):
        sys.path.remove(folder)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "core.autocrlf=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, env=git_environment(), capture_output=True, encoding="utf-8", timeout=60, check=False)
    if result.returncode:
        raise AssertionError(f"git {args[0]} failed: {result.stderr}")
    return result.stdout


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def template() -> dict:
    text = TEMPLATE.read_text(encoding="utf-8").replace("{{VAULT_NAME}}", "t").replace("{{DATE}}", "2026-01-01")
    config = json.loads(text)
    config["handoff_note"] = HANDOFF
    config["deny_zones"] = [*config["deny_zones"], "20-knowledge/hold"]
    return config


def seed(vault: Path) -> None:
    write(vault / "00-meta" / "vault-config.json", json.dumps(template()))
    for relative in ("00-meta/hot.md", "00-meta/log.md", "00-meta/index.md", HANDOFF, OPEN):
        write(vault / relative, NOTE.format(title=Path(relative).stem))
    for relative in (HELD, CUSTOM):
        write(vault / relative, ZONE_NOTE)
    write(vault / ARCHIVED, NOTE.format(title="lessons") + "\n" + LESSON)
    write(vault / HELD_JSON, json.dumps({"schema_version": 1, "summaries": {}}))
    git(vault, "init", "-q")
    git(vault, "add", ".")
    git(vault, "commit", "-q", "-m", "seed")


def run(script: str, vault: Path, *args: str, folder: Path = SCRIPTS) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", "-X", "utf8", str(folder / script), "--vault", str(vault), *args],
                          capture_output=True, encoding="utf-8", timeout=120, check=False)


class NamedPathTests(unittest.TestCase):
    """A zone in a copy is refused wherever the same zone in the main checkout is."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.vault = Path(cls.temporary.name).resolve() / "vault"
        cls.vault.mkdir()
        seed(cls.vault)
        git(cls.vault, "worktree", "add", "-q", "-b", "s1", COPY)
        git(cls.vault, "worktree", "add", "-q", "-b", "s2", "trees/s2")
        cls.zone = f"{COPY}/{HELD}"
        cls.zone_json = f"{COPY}/{HELD_JSON}"
        cls.lessons = f"{COPY}/{ARCHIVED}"
        cls.open = f"{COPY}/{OPEN}"
        cls.bytes = (cls.vault / cls.zone).read_bytes()

    def tearDown(self) -> None:
        self.assertEqual((self.vault / self.zone).read_bytes(), self.bytes)

    def configured(self, **changes) -> None:
        path = self.vault / "00-meta" / "vault-config.json"
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        path.write_text(json.dumps({**json.loads(original), **changes}), encoding="utf-8")

    def config(self) -> dict:
        return vault_healthcheck.validate_config(
            json.loads((self.vault / "00-meta" / "vault-config.json").read_text(encoding="utf-8")))

    def test_every_view_of_the_shared_check(self) -> None:
        policy = vault_state._Policy(self.vault)
        with self.assertRaises(ValueError):
            policy.checked(self.zone)
        with self.assertRaises(ValueError):
            vault_worktrees.checked_note_path(self.vault, self.zone, policy.rules)
        self.assertTrue(policy.checked(self.open).is_file())
        self.assertTrue(policy.checked(OPEN).is_file())

    def test_state_reads_and_hashes(self) -> None:
        policy = vault_state._Policy(self.vault)
        for call in (policy.read_note, policy.hash_note):
            with self.assertRaises(vault_state.StateError) as caught:
                call(self.zone)
            self.assertEqual(str(caught.exception), "unsafe_path")
        self.assertEqual(policy.read_note(self.open)[0]["path"], self.open)

    def test_state_case_variant_of_a_copy(self) -> None:
        with self.assertRaises(vault_state.StateError):
            vault_state._Policy(self.vault).read_note(f"{COPY}/10-INBOX/_Processed/secret.md")

    def test_state_patch_changes_nothing(self) -> None:
        digest = hashlib.sha256(self.bytes).hexdigest()
        with self.assertRaises(vault_state.StateError):
            vault_state.patch_note(self.vault, self.zone, digest,
                                   [{"start": 0, "end": 0, "replacement": "x"}])

    def test_state_handoff_projection(self) -> None:
        with self.assertRaises(vault_state.StateError):
            vault_state.project_handoff(self.vault, self.zone, OPEN, "00-meta/hot.md")

    def test_runs_fingerprint(self) -> None:
        store = vault_runs.Store(self.vault)
        with self.assertRaises(ValueError):
            store.fingerprint(self.zone, markdown=True)
        with self.assertRaises(ValueError):
            store.path(f"{COPY}/00-meta/proposals/x.json")
        self.assertTrue(store.path(self.open, markdown=True).is_file())

    def test_memory_ontology_and_impact_sources(self) -> None:
        for policy in (vault_memory._Policy(self.vault, {"diagnostics": {"binding_bytes_read": 0}}),
                       vault_ontology.policy(self.vault), vault_impact.policy(self.vault)):
            with self.subTest(policy=type(policy).__name__):
                with self.assertRaises(vault_evidence.EvidenceError) as caught:
                    policy.path(self.zone)
                self.assertEqual(str(caught.exception), "unsafe_source")
                self.assertTrue(policy.path(self.open).is_file())

    def test_links_source(self) -> None:
        result = run("vault_links.py", self.vault, "--source", self.zone, "--query", "needle", "--format", "json")
        self.assertNotIn(MARK, result.stdout)
        self.assertNotEqual(json.loads(result.stdout)["diagnostics"]["status"], "ok")

    def test_revocation_reader(self) -> None:
        reader = vault_revocation._Reader(self.vault, self.config())
        with self.assertRaises(vault_revocation.RevocationError) as caught:
            reader.path(self.zone, ".md")
        self.assertEqual(str(caught.exception), "unsafe_path")
        self.assertTrue(reader.path(self.open, ".md").is_file())

    def test_ssot_reads_through_the_shared_policy(self) -> None:
        for command in ("check", "check-support"):
            with self.subTest(command=command):
                result = run("vault_ssot.py", self.vault, command, "--path", self.zone)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn(MARK, result.stdout)

    def test_lesson_delta(self) -> None:
        with self.assertRaises(vault_lesson_delta.LessonDeltaError) as caught:
            vault_lesson_delta.prepare_delta(self.vault, self.lessons, [{"op": "INC", "id": "L-001"}])
        self.assertEqual(str(caught.exception), "unsafe_path")

    def test_lesson_cues_write_nothing(self) -> None:
        output = self.vault / "00-meta" / "lesson-cues.json"
        result = run("vault_lesson_cues.py", self.vault, "build", "--ledger", self.lessons, "--write")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(output.exists())

    def test_lesson_clusters(self) -> None:
        result = run("vault_lesson_clusters.py", self.vault, "--mistakes", self.zone)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(MARK, result.stdout)

    def test_fold_summaries(self) -> None:
        with self.assertRaises(vault_state.StateError) as caught:
            vault_handoff_fold._read_summaries(self.vault, self.zone_json)
        self.assertEqual(str(caught.exception), "invalid_summary")

    def test_fold_of_a_handoff_note_in_a_copy_zone(self) -> None:
        self.configured(handoff_note=self.zone)
        with self.assertRaises(vault_state.StateError):
            vault_handoff_fold.analyze(self.vault)

    def test_capacity_target(self) -> None:
        self.configured(tasks_note=self.zone)
        report = vault_capacity.diagnose_capacity(self.vault)
        tasks = [target for target in report["targets"] if target["role"] == "tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertIsNone(tasks[0]["size_bytes"], tasks)

    def test_operations_input(self) -> None:
        with self.assertRaises(vault_operations.OperationError):
            vault_operations.load_input(self.vault, self.zone_json)

    def test_bundle_input_answers_as_the_main_zone_does(self) -> None:
        main = run("vault_bundle.py", self.vault, "prepare", "--input", HELD_JSON)
        copy = run("vault_bundle.py", self.vault, "prepare", "--input", self.zone_json)
        self.assertNotEqual(copy.returncode, 0)
        self.assertEqual(json.loads(copy.stdout), json.loads(main.stdout))

    def test_index_fold_applies_a_zone_it_does_not_ship(self) -> None:
        reader = vault_index_fold._Reader(self.vault, vault_index_fold._blank())
        with self.assertRaises(vault_evidence.EvidenceError) as caught:
            reader.path(f"trees/s2/{CUSTOM}", ".md")
        self.assertEqual(str(caught.exception), "unsafe_path")
        self.assertTrue(reader.path(f"trees/s2/{OPEN}", ".md").is_file())

    def test_query_mapping_and_sidecar(self) -> None:
        config = self.config()
        deny, exclude = config["deny_zones"], config["exclude_dirs"]
        with self.assertRaises(ValueError):
            vault_query_expansion._read_mapping(self.vault, self.zone_json, deny, exclude, {"mapping_bytes_read": 0})
        with self.assertRaises(ValueError):
            vault_retrieval._sidecar(self.vault, self.zone_json, deny, exclude, {})

    def test_session_context_sources(self) -> None:
        config = {**self.config(), "hot_note": self.zone, "hot_max_tokens": 100, "compact_note": self.zone}
        with self.assertRaises(ValueError):
            session_start._configured_source(self.vault, config, path_key="hot_note",
                                             budget_key="hot_max_tokens", header="hot")
        with self.assertRaises(ValueError):
            session_start._compact_extra_source(self.vault, config, "compact_note")

    def test_doctor_section(self) -> None:
        config = {**self.config(), "hot_note": self.zone, "hot_max_tokens": 100}
        section, source = vault_doctor._diagnose_section(self.vault, config, path_key="hot_note",
                                                         budget_key="hot_max_tokens", header="hot")
        self.assertEqual(section["status"], "unsafe_path")
        self.assertIsNone(source)

    def test_bridge_prompt(self) -> None:
        self.configured(hot_note=self.zone)
        self.assertNotIn(MARK, jarvis_bridge._generation_prompt(self.vault, {}, "needle"))

    def test_healthcheck_stops_on_a_configured_note_in_a_copy_zone(self) -> None:
        self.configured(hot_note=self.zone)
        result = run("vault_healthcheck.py", self.vault, "--output", str(self.vault.parent / "report.md"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("hot_note", result.stderr)


class EveryResolverIsChecked(unittest.TestCase):
    """A new caller of resolve_note_path has to say why the view of a copy does not apply."""

    WALKED = "the path came from a walk that already applied the zones and the copies"
    NEXT_TO = "checks the view next to the call"
    CONFIG = "the fixed configuration file at the vault root"
    # (file, enclosing function) or (file, "") for a whole file: why the call has no second view.
    SITES = {
        ("vault_paths.py", ""): "the definition",
        ("vault_connectors.py", ""): "the plugin's own script folder, not a vault",
        ("vault_ssot.py", "_inventory"): "names only, in a bound file: a documented limit, and reads use the policy",
        ("vault_worktrees.py", "checked_note_path"): "the check itself",
        ("vault_evidence.py", "Store.path"): NEXT_TO,
        ("vault_proposals.py", "Store._resolved"): NEXT_TO,
        ("vault_lesson_metrics.py", "Store.path"): NEXT_TO,
        ("vault_instruction_budget.py", "_Inventory.resolve"): NEXT_TO,
        ("vault_commit.py", "_resolve"): NEXT_TO,
        ("vault_recall.py", "_markdown_paths"): WALKED,
        ("vault_query_expansion.py", "verify_inventory"): WALKED,
        ("vault_query_expansion.py", "expand_query"): WALKED,
        ("vault_retrieval.py", "retrieve"): WALKED,  # an external candidate must also be in the walked set
        ("vault_doctor.py", "diagnose"): CONFIG,
        ("vault_memory.py", "_Policy._config"): CONFIG,
        ("vault_state.py", "_Policy.__init__"): CONFIG,
        ("vault_state.py", "_Policy.runtime"): "the fixed runtime folder at the vault root",
        ("vault_state.py", "_ensure_runtime"): "the fixed runtime folder at the vault root",
    }

    @staticmethod
    def fixed(call: ast.Call) -> bool:
        """A call for a fixed file: no zones, or only .git, in the third argument."""
        if len(call.args) < 3:
            return True
        third = call.args[2]
        return isinstance(third, ast.Tuple) and all(
            isinstance(item, ast.Constant) and item.value == ".git" for item in third.elts)

    @classmethod
    def sites(cls, path: Path) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []

        def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
            for child in ast.iter_child_nodes(node):
                inner = scope
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    inner = (*scope, child.name)
                if (isinstance(child, ast.Call) and not cls.fixed(child)
                        and getattr(child.func, "id", getattr(child.func, "attr", "")) == "resolve_note_path"):
                    found.append((path.name, ".".join(scope)))
                visit(child, inner)
        visit(ast.parse(path.read_text(encoding="utf-8")), ())
        return found

    def test_every_call_is_the_check_or_has_a_reason(self) -> None:
        unknown = []
        for path in (*sorted(SCRIPTS.glob("*.py")), *sorted(HOOKS.glob("*.py"))):
            for site in self.sites(path):
                if site not in self.SITES and (site[0], "") not in self.SITES:
                    unknown.append(site)
        self.maxDiff = None
        self.assertEqual(unknown, [])


class UnregisteredTreeTests(unittest.TestCase):
    """Only git's own record of a tree takes a folder out of a walk."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.vault = self.base / "vault"
        self.vault.mkdir()
        seed(self.vault)
        self.scope = vault_worktrees.WorktreeScope(self.vault)
        self.forged = self.vault / "20-knowledge" / "hidden"
        write(self.forged / "a.md", NOTE.format(title="hiddenmark"))
        write(self.forged / "bad.md", "---\ntitle: bad\nrelated: [[x]]\n---\nneedle hiddenmark\n")
        write(self.forged / HELD, f"no frontmatter {MARK}\n")
        write(self.forged / ".git", "gitdir: ../../.git/worktrees/fake\n")

    def add_tree(self, relative: str = COPY) -> Path:
        git(self.vault, "worktree", "add", "-q", "-b", "s1", relative)
        return self.vault / relative

    def admin(self, name: str = "s1") -> Path:
        return self.vault / ".git" / "worktrees" / name

    def recall(self) -> dict:
        result = run("vault_recall.py", self.vault, "--query", "needle", "--limit", "50", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def report(self) -> str:
        report = self.base / "report.md"
        result = run("vault_healthcheck.py", self.vault, "--output", str(report))
        self.assertTrue(report.is_file(), result.stdout + result.stderr)
        return report.read_text(encoding="utf-8")

    def test_a_tree_git_made_is_registered(self) -> None:
        copy = self.add_tree()
        self.assertTrue(self.scope.registered(copy))

    def test_a_relative_record_is_registered(self) -> None:
        # worktree.useRelativePaths stores this file relative to the admin folder.
        copy = self.add_tree()
        record = self.admin() / "gitdir"
        os.chmod(record, stat.S_IWRITE | stat.S_IREAD)
        record.write_text(os.path.relpath(copy / ".git", self.admin()).replace(os.sep, "/") + "\n", encoding="utf-8")
        self.assertTrue(self.scope.registered(copy))

    def test_a_hand_written_gitfile_is_not_registered_and_its_zones_stay_closed(self) -> None:
        self.assertTrue(self.scope.linked(self.forged))
        self.assertFalse(self.scope.registered(self.forged))
        self.assertTrue(self.scope.denied(("20-knowledge", "hidden", *HELD.split("/")), ("10-inbox/_processed",)))

    def test_a_gitfile_naming_a_real_admin_folder_is_not_registered(self) -> None:
        self.add_tree()
        (self.forged / ".git").unlink()
        write(self.forged / ".git", f"gitdir: {self.admin().as_posix()}\n")
        self.assertTrue(self.scope.linked(self.forged))
        self.assertFalse(self.scope.registered(self.forged))

    def test_a_removed_admin_folder_is_not_registered(self) -> None:
        copy = self.add_tree()
        shutil.rmtree(self.admin())
        self.assertTrue(self.scope.linked(copy))
        self.assertFalse(self.scope.registered(copy))

    def test_an_unusable_record_is_not_registered(self) -> None:
        copy = self.add_tree()
        record = self.admin() / "gitdir"
        os.chmod(record, stat.S_IWRITE | stat.S_IREAD)
        original = record.read_bytes()
        for data in (b"", b"\xff\xfe", original + b"\x00", b"x" * 5000):
            with self.subTest(data=data[:20]):
                record.write_bytes(data)
                self.assertFalse(self.scope.registered(copy))
        record.unlink()
        record.mkdir()
        self.assertFalse(self.scope.registered(copy))

    def test_recall_walks_the_folder_but_not_its_zone(self) -> None:
        answer = self.recall()
        paths = [match["path"] for match in answer["matches"]]
        self.assertIn("20-knowledge/hidden/a.md", paths)
        self.assertNotIn(f"20-knowledge/hidden/{HELD}", paths)
        self.assertNotIn(MARK, json.dumps(answer))
        self.assertNotIn("skipped_worktrees", answer["diagnostics"])

    def test_recall_of_a_copy_without_its_admin_folder_keeps_the_zone_closed(self) -> None:
        self.add_tree()
        shutil.rmtree(self.admin())
        answer = self.recall()
        paths = [match["path"] for match in answer["matches"]]
        self.assertIn(f"{COPY}/{OPEN}", paths)
        self.assertNotIn(f"{COPY}/{HELD}", paths)
        self.assertNotIn(MARK, json.dumps(answer))

    def test_a_registered_copy_is_still_left_out_of_recall(self) -> None:
        self.add_tree()
        answer = self.recall()
        self.assertEqual(answer["diagnostics"]["skipped_worktrees"], [COPY])
        self.assertFalse([match for match in answer["matches"] if match["path"].startswith(COPY)])

    def test_healthcheck_lints_the_folder_but_not_its_zone(self) -> None:
        report = self.report()
        self.assertIn("hidden/bad.md", report)
        self.assertNotIn(f"hidden/{HELD}", report)
        self.assertNotIn("연결 작업 트리", report)

    def test_a_registered_copy_is_still_left_out_of_the_healthcheck(self) -> None:
        self.add_tree()
        report = self.report()
        self.assertEqual([line for line in report.splitlines() if COPY in line],
                         [f"- {COPY}/ (연결 작업 트리 사본)"])

    def test_backup_keeps_the_folder(self) -> None:
        skipped: list[str] = []
        snapshot = backup_vault.create_backup(self.vault, self.base / "backups", skipped=skipped)
        manifest = backup_vault.verify_snapshot(snapshot)
        self.assertEqual(skipped, [])
        self.assertIn("20-knowledge/hidden/a.md", manifest["files"])

    def test_a_registered_copy_is_still_left_out_of_the_backup(self) -> None:
        self.add_tree()
        skipped: list[str] = []
        backup_vault.create_backup(self.vault, self.base / "backups", skipped=skipped)
        self.assertEqual(skipped, [COPY])

    def test_instruction_inventory_counts_the_folder_but_not_its_zone(self) -> None:
        folder = self.vault / ".agents" / "kit"
        write(folder / "guide.md", "plain\n")
        write(folder / HELD, f"{MARK}\n")
        write(folder / ".git", "gitdir: ../../.git/worktrees/fake\n")
        scope = vault_instruction_budget._Inventory(self.vault, vault_healthcheck.validate_config(template()))
        self.assertTrue(scope.resolve(".agents/kit/guide.md").is_file())
        with self.assertRaises(ValueError):
            scope.resolve(f".agents/kit/{HELD}")

    def test_the_named_path_check_stays_wide(self) -> None:
        relative = f"20-knowledge/hidden/{HELD}"
        with self.assertRaises(vault_state.StateError):
            vault_state._Policy(self.vault).read_note(relative)
        self.assertTrue(vault_state._Policy(self.vault).read_note("20-knowledge/hidden/a.md"))


if __name__ == "__main__":
    unittest.main()
