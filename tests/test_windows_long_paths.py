"""Regression tests for Windows MAX_PATH (>260 chars) runtime paths and error classification."""
from __future__ import annotations

import errno
import hashlib
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
import vault_runs  # noqa: E402
import vault_state  # noqa: E402
import vault_task_contracts  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
