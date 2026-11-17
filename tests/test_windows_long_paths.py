"""Regression tests for Windows MAX_PATH (>260 chars) runtime paths and error classification."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backup_vault  # noqa: E402
import vault_evidence  # noqa: E402
import vault_improvements  # noqa: E402
import vault_lesson_metrics  # noqa: E402
import vault_proposals  # noqa: E402
import vault_runs  # noqa: E402
import vault_state  # noqa: E402
import vault_task_contracts  # noqa: E402

try:
    from _winfs import extended, make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import extended, make_junction


def _rmtree_long(root: Path) -> None:
    target = Path("\\\\?\\" + str(root.resolve())) if os.name == "nt" and not str(root).startswith("\\\\?\\") else root
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


class WindowsLongPathTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="av-lp-")).resolve()
        self.addCleanup(lambda: _rmtree_long(base))
        # Build a vault root ~175-190 chars long so runtime files (112-120 chars) exceed 285-305 chars (>260 MAX_PATH).
        nested = base
        while len(str(nested)) < 170:
            nested = nested / ("segment_" + "x" * 24)
        self.vault = nested / "vault"
        fs_vault = Path("\\\\?\\" + str(self.vault)) if os.name == "nt" else self.vault
        (fs_vault / "00-meta").mkdir(parents=True, exist_ok=True)
        (fs_vault / "20-knowledge").mkdir(parents=True, exist_ok=True)
        config = {
            "deny_zones": ["90-assets"],
            "exclude_dirs": [".git", ".agentic-vault"],
            "handoff_note": "00-meta/handoff.md",
            "hot_note": "00-meta/hot.md",
            "log_note": "00-meta/log.md",
        }
        (fs_vault / "00-meta" / "vault-config.json").write_text(json.dumps(config), encoding="utf-8")
        (fs_vault / "00-meta" / "handoff.md").write_text("---\ntitle: H\n---\nInitial handoff.\n", encoding="utf-8")
        (fs_vault / "00-meta" / "hot.md").write_text("---\ntitle: Hot\n---\nInitial hot.\n", encoding="utf-8")
        (fs_vault / "00-meta" / "log.md").write_text("- 2026-11-16 08:00 | t | [ops] init\n", encoding="utf-8")
        (fs_vault / "20-knowledge" / "spec.md").write_text("---\ntitle: S\n---\nGoal: preserve safety.\n", encoding="utf-8")
        self.backup_dir = nested / "backups"

    def test_advisory_lock_patch_note_and_checkpoint_succeed_beyond_260_chars(self) -> None:
        self.assertGreater(len(str(self.vault)) + 115, 275)
        with vault_state.advisory_lock(self.vault, "long-path-lock-key", wait_seconds=1) as lease:
            self.assertIn("lock-", lease["lock_path"])
        raw = (Path("\\\\?\\" + str(self.vault)) / "00-meta" / "hot.md" if os.name == "nt"
               else self.vault / "00-meta" / "hot.md").read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        patched = vault_state.patch_note(
            self.vault,
            "00-meta/hot.md",
            sha,
            [{"start": len(raw) - len(b"Initial hot.\n"), "end": len(raw), "replacement": "Updated hot.\n"}],
        )
        self.assertEqual(patched["status"], "applied")
        cp = vault_state.write_checkpoint(self.vault, "SessionEnd", session_id="sess-long-path")
        self.assertEqual(cp["event"], "SessionEnd")

    def test_task_contracts_and_evidence_store_succeed_beyond_260_chars(self) -> None:
        store = vault_evidence.Store(self.vault)
        records, _ = store.capture(["20-knowledge/spec.md"])
        self.assertEqual(records[0]["path"], "20-knowledge/spec.md")

        declaration = {
            "version": 1,
            "task": "Long path contract check",
            "fields": [
                {
                    "id": "goal_1",
                    "kind": "goal",
                    "text": "Preserve safety under deep Windows paths.",
                    "origin": "user_request",
                    "status": "confirmed",
                    "sources": [{"path": "20-knowledge/spec.md", "excerpt": "Goal: preserve safety."}],
                },
                {
                    "id": "scope_1",
                    "kind": "scope",
                    "text": "Runtime and backup paths.",
                    "origin": "user_confirmation",
                    "status": "confirmed",
                    "sources": [{"path": "20-knowledge/spec.md", "excerpt": "Goal: preserve safety."}],
                },
                {
                    "id": "acc_1",
                    "kind": "acceptance",
                    "text": "Tests pass beyond 260 characters.",
                    "origin": "user_confirmation",
                    "status": "confirmed",
                    "sources": [{"path": "20-knowledge/spec.md", "excerpt": "Goal: preserve safety."}],
                },
            ],
            "questions": [],
        }
        decl_file = (Path("\\\\?\\" + str(self.vault)) / "20-knowledge" / "decl.json" if os.name == "nt"
                     else self.vault / "20-knowledge" / "decl.json")
        decl_file.write_text(json.dumps(declaration), encoding="utf-8")
        captured = vault_task_contracts.capture(self.vault, "20-knowledge/decl.json")
        self.assertEqual(captured["status"], "ready_for_execution_review")
        inspected = vault_task_contracts.inspect(self.vault, captured["contract_id"])
        self.assertEqual(inspected["task"], "Long path contract check")

    def test_backup_and_restore_succeed_beyond_260_chars(self) -> None:
        snap = backup_vault.create_backup(self.vault, self.backup_dir)
        manifest = backup_vault.verify_snapshot(snap)
        self.assertIn("20-knowledge/spec.md", manifest["files"])
        restored = self.backup_dir.parent / "restored_vault"
        backup_vault.restore_snapshot(snap, restored)
        check_spec = (Path("\\\\?\\" + str(restored)) / "20-knowledge" / "spec.md" if os.name == "nt"
                      else restored / "20-knowledge" / "spec.md")
        self.assertTrue(check_spec.is_file())

    def test_os_filename_too_long_maps_to_path_too_long_not_unsafe_lock_or_input(self) -> None:
        err = OSError(errno.ENAMETOOLONG, "File name too long")
        if os.name == "nt":
            err.winerror = 206
        with mock.patch.object(vault_state, "_exclusive", side_effect=err):
            with self.assertRaises(vault_state.StateError) as ctx:
                with vault_state.advisory_lock(self.vault, "boom"):
                    pass
            self.assertEqual(str(ctx.exception), "path_too_long")

        with mock.patch.object(vault_task_contracts, "_Store", side_effect=err):
            with self.assertRaises(vault_task_contracts.ContractError) as c_ctx:
                vault_task_contracts.inspect(self.vault, "a" * 64)
            self.assertEqual(str(c_ctx.exception), "path_too_long")

        with mock.patch.object(vault_runs, "Store", side_effect=err):
            with self.assertRaises(vault_runs.RunError) as r_ctx:
                vault_runs.inspect(self.vault, "b" * 64)
            self.assertEqual(str(r_ctx.exception), "path_too_long")

    @unittest.skipUnless(os.name == "nt", "Windows TemporaryDirectory._rmtree wrapper test")
    def test_longpath_rmtree_preserves_python310_and_python312_signatures(self) -> None:
        import tests as tests_pkg

        calls_310 = []

        def fake_py310_rmtree(name, ignore_errors=False):
            calls_310.append((name, ignore_errors))

        calls_312 = []

        def fake_py312_rmtree(name, ignore_errors=False, repeated=False):
            calls_312.append((name, ignore_errors, repeated))

        with mock.patch.object(tests_pkg, "_orig_rmtree", side_effect=fake_py310_rmtree):
            tempfile.TemporaryDirectory._rmtree("dummy_310", ignore_errors=True)
        self.assertEqual(calls_310, [("dummy_310", True)])

        with mock.patch.object(tests_pkg, "_orig_rmtree", side_effect=fake_py312_rmtree):
            tempfile.TemporaryDirectory._rmtree("dummy_312", ignore_errors=False, repeated=True)
        self.assertEqual(calls_312, [("dummy_312", False, True)])


class LessonMetricsLongPathTests(unittest.TestCase):
    """An observation record lies 102 characters below the vault root, beyond MAX_PATH from a 158-character root."""

    # Proposal receipts (56 characters below the root) stay below MAX_PATH at this length; observation records do not.
    ROOT_LENGTH = 172
    OBSERVATION = {"attempt_id": "A-001", "session_id": "S-001", "applicability": "applicable",
                   "outcome": "success", "verification_status": "unverified", "evidence_ids": []}

    def setUp(self) -> None:
        self.vault = self.make_vault(self.ROOT_LENGTH)

    def make_vault(self, length: int) -> Path:
        """A fresh vault whose path has at least `length` characters (exactly that many below an ordinary TEMP)."""
        base = Path(tempfile.mkdtemp(prefix="av-lp-")).resolve()
        self.addCleanup(lambda: _rmtree_long(base))
        padding = length - len(str(base)) - len(os.sep + "vault") - 1
        vault = base / ("p" * max(padding, 1)) / "vault"
        fs = vault_evidence.fs_path  # a very long vault holds files beyond the plain limit
        fs(vault / "00-meta").mkdir(parents=True)
        fs(vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({"required_keys": ["title"], "enums": {}}), encoding="utf-8")
        fs(vault / "CLAUDE.md").write_bytes(b"# Rules\nBefore\n")
        fs(vault / "00-meta" / "candidate.md").write_bytes(b"# Rules\nAfter: retry once\n")
        return vault

    def call(self, module, *arguments: str, vault: Path | None = None) -> dict:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = module.main(["--vault", str(vault or self.vault), *arguments])
        output = json.loads(stream.getvalue())
        self.assertEqual(code, 0, output)
        return output

    def applied_proposal(self, vault: Path) -> str:
        proposal = self.call(vault_proposals, "propose", "--target", "CLAUDE.md", "--candidate",
                             "00-meta/candidate.md", "--lesson", "L-001", "--summary", "Retry recovery", vault=vault)
        self.call(vault_proposals, "apply", proposal["id"], "--approve", vault=vault)
        return proposal["id"]

    def test_observations_are_recorded_and_summarized_beyond_260_chars(self) -> None:
        self.assertGreaterEqual(len(str(self.vault)), self.ROOT_LENGTH)
        proposal_id = self.applied_proposal(self.vault)
        observation = dict(self.OBSERVATION)
        recorded = vault_lesson_metrics.record_observation(self.vault, proposal_id, observation)
        record = self.vault / recorded["path"]
        self.assertGreater(len(str(record)), 260)
        self.assertTrue(vault_evidence.fs_path(record).is_file())
        # Neither the temporary nor the writer lock is left behind.
        self.assertEqual(os.listdir(vault_evidence.fs_path(record.parent)), [record.name])
        self.assertFalse(vault_evidence.fs_path(record.parent.parent / ".lock").exists())
        second = vault_lesson_metrics.record_observation(
            self.vault, proposal_id, {**observation, "attempt_id": "A-002"})
        self.assertNotEqual(second["id"], recorded["id"])
        summary = vault_lesson_metrics.summarize(self.vault, proposal_id)
        self.assertEqual(summary["counts"]["eligible_attempts"], 2)
        with self.assertRaises(vault_lesson_metrics.LessonMetricsError) as caught:
            vault_lesson_metrics.record_observation(self.vault, proposal_id, observation)
        self.assertEqual(str(caught.exception), "duplicate_attempt")

    def test_a_vault_named_in_the_extended_form_records_and_leaves_nothing_behind(self) -> None:
        # A caller may name the vault in the extended-length form, or on Windows in the Win32 device
        # namespace, at any length; the store then works in that form throughout, as it did before (on
        # other systems extended() returns the same path).
        spellings = [("extended", extended)]
        if os.name == "nt":
            spellings.append(("device", lambda path: Path("\\\\.\\" + str(path))))
        for length in (1, self.ROOT_LENGTH):
            for name, spell in spellings:
                vault = self.make_vault(length)
                with self.subTest(length=len(str(vault)), spelled=name):
                    proposal_id = self.applied_proposal(vault)
                    spelled = spell(vault)
                    recorded = vault_lesson_metrics.record_observation(spelled, proposal_id,
                                                                       dict(self.OBSERVATION))
                    summary = vault_lesson_metrics.summarize(spelled, proposal_id)
                    self.assertEqual(summary["counts"]["eligible_attempts"], 1)
                    folder = vault_evidence.fs_path(vault / recorded["path"]).parent
                    self.assertEqual(os.listdir(folder), [Path(recorded["path"]).name])
                    self.assertFalse(vault_evidence.fs_path(folder.parent / ".lock").exists())

    @unittest.skipUnless(os.name == "nt", "junctions exist only on Windows")
    def test_a_vault_reached_through_a_junction_is_refused_on_the_long_branch_too(self) -> None:
        # From a root of about 230 characters every store path takes the long branch; the vault folder must
        # still be checked as the caller named it, so a junction that leads to the vault is refused.
        vault = self.make_vault(235)
        alias_parent = Path(tempfile.mkdtemp(prefix="av-lp-alias-")).resolve()
        self.addCleanup(lambda: _rmtree_long(alias_parent))
        alias = alias_parent / "alias"
        make_junction(alias, vault)
        for call in (lambda: vault_lesson_metrics.record_observation(alias, "0" * 32, dict(self.OBSERVATION)),
                     lambda: vault_lesson_metrics.summarize(alias, "0" * 32)):
            with self.assertRaises(vault_lesson_metrics.LessonMetricsError) as caught:
                call()
            self.assertEqual(str(caught.exception), "unsafe_path")

    @unittest.skipUnless(os.name == "nt", "junctions exist only on Windows")
    def test_a_junction_beyond_260_chars_is_refused_like_a_short_one(self) -> None:
        # The record folder lies 64 characters below the root, so from 197 characters it passes MAX_PATH,
        # out of reach of a plain lstat call, while the proposal receipt (56 characters below) does not.
        vault = self.make_vault(197)
        proposal_id = self.applied_proposal(vault)
        outside = Path(tempfile.mkdtemp(prefix="av-lp-outside-")).resolve()
        self.addCleanup(lambda: _rmtree_long(outside))
        observations = vault / "00-meta" / "proposals" / "observations"
        vault_evidence.fs_path(observations).mkdir(parents=True, exist_ok=True)
        record_folder = observations / proposal_id
        self.assertGreaterEqual(len(str(record_folder)), 260)
        make_junction(record_folder, outside)
        for call in (lambda: vault_lesson_metrics.record_observation(vault, proposal_id, dict(self.OBSERVATION)),
                     lambda: vault_lesson_metrics.summarize(vault, proposal_id)):
            with self.assertRaises(vault_lesson_metrics.LessonMetricsError) as caught:
                call()
            self.assertEqual(str(caught.exception), "unsafe_path")
        self.assertEqual(os.listdir(outside), [])

if __name__ == "__main__":
    unittest.main()
