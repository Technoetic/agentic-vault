"""Snapshot recovery and rejection tests using disposable local vaults."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts/backup_vault.py"
SPEC = importlib.util.spec_from_file_location("backup_vault", SCRIPT)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.vault = self.root / "vault"
        (self.vault / "00-meta").mkdir(parents=True)
        self.config = self.vault / "00-meta/vault-config.json"
        self.config.write_text('{}', encoding="utf-8")
        self.target = self.root / "backups"
        (self.vault / "90-assets").mkdir()
        self.binary = self.vault / "90-assets/sample.bin"
        self.binary.write_bytes(b"original\x00\xff\x01")

    def create(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)),
                        "independent snapshot creation is missing")
        return backup.create_backup(self.vault, self.target)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8")

    def test_deleted_untracked_binary_recovers_from_older_independent_snapshot(self):
        first = self.create()
        self.binary.unlink()
        second = self.create()
        self.assertNotEqual(first, second)
        self.assertFalse((second / "files/90-assets/sample.bin").exists())
        restored = self.root / "restored"
        backup.restore_snapshot(first, restored)
        self.assertEqual((restored / "90-assets/sample.bin").read_bytes(),
                         b"original\x00\xff\x01")
        self.assertEqual(len(list((self.target / "snapshots").iterdir())), 2)

    def test_source_mutation_does_not_change_snapshot(self):
        snapshot = self.create()
        self.binary.write_bytes(b"modified")
        backup.verify_snapshot(snapshot)
        self.assertEqual((snapshot / "files/90-assets/sample.bin").read_bytes(),
                         b"original\x00\xff\x01")

    def test_source_change_during_copy_prevents_publication(self):
        original = backup._copy_file

        def changed_source(source, destination):
            record = original(source, destination)
            if source == self.binary:
                source.write_bytes(b"changed while backing up")
            return record

        with mock.patch.object(backup, "_copy_file", side_effect=changed_source):
            with self.assertRaises((ValueError, OSError)):
                backup.create_backup(self.vault, self.target)
        self.assertEqual(list((self.target / "snapshots").iterdir()), [])

    def test_empty_directories_and_configured_exclusions_are_preserved(self):
        (self.vault / "empty").mkdir()
        (self.vault / "generated").mkdir()
        (self.vault / "generated/cache.bin").write_bytes(b"excluded")
        self.config.write_text('{"exclude_dirs": ["generated"]}', encoding="utf-8")
        snapshot = self.create()
        restored = self.root / "restored"
        backup.restore_snapshot(snapshot, restored)
        self.assertTrue((restored / "empty").is_dir())
        self.assertFalse((restored / "generated").exists())

    def test_corruption_is_refused_before_restore_side_effects(self):
        snapshot = self.create()
        (snapshot / "files/90-assets/sample.bin").write_bytes(b"tampered")
        destination = self.root / "new-parent/restored"
        with self.assertRaises((ValueError, OSError)):
            backup.restore_snapshot(snapshot, destination)
        self.assertFalse(destination.parent.exists())
        result = self.cli("--verify", snapshot)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr)

    def test_restore_rejects_existing_destination_and_snapshot_overlap(self):
        snapshot = self.create()
        existing = self.root / "existing"
        existing.mkdir()
        for destination in (existing, snapshot / "export"):
            with self.subTest(destination=destination):
                with self.assertRaises((ValueError, OSError)):
                    backup.restore_snapshot(snapshot, destination)
        self.assertEqual(list(existing.iterdir()), [])

    def test_overlap_is_normalized_and_rejected_in_both_directions(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        for target in (self.vault, self.vault / "child", self.root,
                       self.root / "backups/../vault/child"):
            with self.subTest(target=target):
                with self.assertRaises((ValueError, OSError)):
                    backup.create_backup(self.vault, target)
        self.assertFalse((self.vault / "child").exists())

    def test_malformed_manifest_and_path_traversal_are_refused(self):
        snapshot = self.create()
        manifest_path = snapshot / "manifest.json"
        valid = json.loads(manifest_path.read_text(encoding="utf-8"))
        bad_manifests = [[], {}, {**valid, "version": 999},
                         {**valid, "files": []}]
        for bad_path in ("../outside", "/absolute", "C:/absolute", "a\\b",
                         "files/../../outside", "a//b", "a/./b", "CON", "x:stream",
                         "COM¹", "com².txt", "COM³.md", "LPT¹", "lpt².txt",
                         "LPT³.md", "CONIN$", "conout$.txt"):
            changed = json.loads(json.dumps(valid))
            changed["files"][bad_path] = {"size": 0, "sha256": "0" * 64}
            bad_manifests.append(changed)
        for invalid in bad_manifests:
            manifest_path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.subTest(manifest=invalid):
                with self.assertRaises((ValueError, OSError)):
                    backup.restore_snapshot(snapshot, self.root / "restored")
                self.assertFalse((self.root / "restored").exists())

    def test_portable_snapshot_names_reject_console_device_aliases(self):
        aliases = (
            "COM¹", "com².txt", "COM³.md", "LPT¹", "lpt².txt", "LPT³.md",
            "CONIN$", "conout$.txt",
        )
        for alias in aliases:
            with self.subTest(alias=alias), self.assertRaises(backup.BackupError):
                backup._relative_name(f"notes/{alias}")

        self.assertEqual(
            backup._relative_name("notes/회의 기록 1.md"),
            "notes/회의 기록 1.md",
        )

    def test_unlisted_files_and_missing_files_are_corruption(self):
        snapshot = self.create()
        extra = snapshot / "files/extra.txt"
        extra.write_text("extra", encoding="utf-8")
        with self.assertRaises((ValueError, OSError)):
            backup.verify_snapshot(snapshot)
        extra.unlink()
        (snapshot / "files/90-assets/sample.bin").unlink()
        with self.assertRaises((ValueError, OSError)):
            backup.verify_snapshot(snapshot)

    def test_copy_failure_never_publishes_and_releases_writer_lock(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        with mock.patch.object(backup, "_copy_file", side_effect=OSError("read failure")):
            with self.assertRaises(OSError):
                backup.create_backup(self.vault, self.target)
        self.assertEqual(list((self.target / "snapshots").iterdir()), [])
        self.assertFalse((self.target / ".backup.lock").exists())
        backup.verify_snapshot(self.create())

    def test_concurrent_writer_is_refused(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        entered, release = threading.Event(), threading.Event()
        original = backup._copy_file
        failures = []

        def paused_copy(*args, **kwargs):
            entered.set()
            if not release.wait(15):
                raise OSError("test timed out")
            return original(*args, **kwargs)

        def first_writer():
            try:
                backup.create_backup(self.vault, self.target)
            except Exception as exc:
                failures.append(exc)

        with mock.patch.object(backup, "_copy_file", side_effect=paused_copy):
            worker = threading.Thread(target=first_writer)
            worker.start()
            try:
                self.assertTrue(entered.wait(10))
                result = self.cli("--vault", self.vault, "--target", self.target)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("lock", result.stderr.lower())
            finally:
                release.set()
                worker.join(15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(list((self.target / "snapshots").iterdir())), 1)

    def test_invalid_backup_configuration_fails_visibly(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        for config in ('[]', '{', '{"exclude_dirs": "oops"}',
                       '{"exclude_dirs": ["../outside"]}', '{"backup_target": 3}'):
            self.config.write_text(config, encoding="utf-8")
            result = self.cli("--vault", self.vault, "--target", self.target)
            with self.subTest(config=config):
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)
                self.assertFalse((self.target / "snapshots").exists())

    def test_cli_create_verify_and_restore(self):
        result = self.cli("--vault", self.vault, "--target", self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = list((self.target / "snapshots").glob("*")) if (self.target / "snapshots").exists() else []
        self.assertEqual(len(snapshots), 1, "creation must publish an independent snapshot")
        self.assertEqual(self.cli("--verify", snapshots[0]).returncode, 0)
        restored = self.root / "export"
        result = self.cli("--restore", snapshots[0], "--destination", restored)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((restored / "90-assets/sample.bin").read_bytes(), b"original\x00\xff\x01")

    def make_external_link(self, link, directory=False):
        outside = self.root / "outside"
        if directory:
            outside.mkdir(exist_ok=True)
            (outside / "secret").write_text("outside marker", encoding="utf-8")
        else:
            outside.write_text("outside marker", encoding="utf-8")
        try:
            link.symlink_to(outside, target_is_directory=directory)
        except OSError:
            if os.name != "nt" or not directory:
                self.skipTest("symlink creation unavailable")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                    capture_output=True)
            if result.returncode:
                self.skipTest("junction creation unavailable")
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        return outside

    def test_external_directory_link_even_when_excluded_is_rejected(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        self.make_external_link(self.vault / "node_modules", directory=True)
        with self.assertRaises((ValueError, OSError)):
            backup.create_backup(self.vault, self.target)
        self.assertEqual(list((self.target / "snapshots").glob("*")), [])

    def test_linked_backup_target_is_rejected(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        self.make_external_link(self.target, directory=True)
        with self.assertRaises((ValueError, OSError)):
            backup.create_backup(self.vault, self.target / "backups")

    def test_snapshot_link_is_rejected_before_restore(self):
        snapshot = self.create()
        self.make_external_link(snapshot / "files/external", directory=True)
        destination = self.root / "restored"
        with self.assertRaises((ValueError, OSError)):
            backup.restore_snapshot(snapshot, destination)
        self.assertFalse(destination.exists())

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_git_history_bundle_clones_without_source_repository(self):
        subprocess.run(["git", "init", "-q", str(self.vault)], check=True)
        subprocess.run(["git", "-C", str(self.vault), "add", "00-meta"], check=True)
        subprocess.run(["git", "-C", str(self.vault), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "commit", "-qm", "history"], check=True)
        snapshot = self.create()
        moved_source = self.root / "unavailable-source"
        self.assertTrue(self.vault.resolve().is_relative_to(self.root))
        self.assertTrue(moved_source.resolve().is_relative_to(self.root))
        self.vault.rename(moved_source)
        clone = self.root / "history-clone"
        result = subprocess.run(["git", "clone", str(snapshot / "history.bundle"), str(clone)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((clone / "00-meta/vault-config.json").exists())
        self.assertFalse((snapshot / "files/.git").exists())

    def test_missing_git_is_a_failure_when_history_is_required(self):
        (self.vault / ".git").mkdir()
        with mock.patch.object(backup.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Git"):
                backup.create_backup(self.vault, self.target)
        self.assertEqual(list((self.target / "snapshots").iterdir()), [])

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_git_bundle_failure_is_not_reported_as_success(self):
        self.assertTrue(callable(getattr(backup, "create_backup", None)))
        # Git refuses to bundle an unborn repository: a real command failure.
        subprocess.run(["git", "init", "-q", str(self.vault)], check=True)
        result = self.cli("--vault", self.vault, "--target", self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git", result.stderr.lower())
        self.assertEqual(list((self.target / "snapshots").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
