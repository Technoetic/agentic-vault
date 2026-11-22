from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class CloudSyncDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("vault_cloud_sync")

    def test_detects_onedrive_env_root_and_path_segments_without_path_echo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            synced_root = root / "PersonalSync"
            vault = synced_root / "team-vault"
            result = self.module.detect_cloud_sync_path(vault, env={"OneDrive": str(synced_root)})
            self.assertEqual(result, {"detected": True, "provider": "onedrive", "signal": "env_root"})
            serialized = json.dumps(result)
            self.assertNotIn("PersonalSync", serialized)
            self.assertNotIn("team-vault", serialized)

            for folder_parts, expected_provider in (
                (("OneDrive", "vault"), "onedrive"),
                (("OneDrive - ExampleCorp", "vault"), "onedrive"),
                (("Google Drive", "vault"), "google_drive"),
                (("My Drive", "vault"), "google_drive"),
                (("내 드라이브", "vault"), "google_drive"),
                (("Dropbox", "vault"), "dropbox"),
                (("Dropbox (Team)", "vault"), "dropbox"),
                (("iCloud Drive", "vault"), "icloud"),
                (("Library", "Mobile Documents", "vault"), "icloud"),
            ):
                candidate = root.joinpath(*folder_parts)
                with self.subTest(folder=folder_parts):
                    found = self.module.detect_cloud_sync_path(candidate, env={})
                    self.assertEqual(
                        found,
                        {"detected": True, "provider": expected_provider, "signal": "path_segment"},
                    )

            local = self.module.detect_cloud_sync_path(root / "plain-local" / "vault", env={})
            self.assertEqual(local, {"detected": False, "provider": None, "signal": "none"})

    def test_diagnose_cloud_sync_inspects_backup_target_and_local_artifact_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault = root / "local-vault"
            (vault / "00-meta").mkdir(parents=True)
            (vault / ".claude").mkdir(parents=True)
            (vault / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
            (vault / ".env").write_text("SECRET_TOKEN=supersecretvalue1234\n", encoding="utf-8")
            cloud_backup = root / "Google Drive" / "backups"
            (vault / "00-meta" / "vault-config.json").write_text(
                json.dumps({"vault_name": "Demo", "backup_target": str(cloud_backup)}),
                encoding="utf-8",
            )
            report = self.module.diagnose_cloud_sync(vault, env={})
            self.assertEqual(report["status"], "cloud_synced")
            self.assertFalse(report["vault_sync"]["detected"])
            self.assertTrue(report["backup_target_sync"]["configured"])
            self.assertTrue(report["backup_target_sync"]["detected"])
            self.assertEqual(report["backup_target_sync"]["provider"], "google_drive")
            self.assertEqual(
                report["local_artifact_signals"],
                ["claude_settings_local", "dotenv_file"],
            )
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("supersecretvalue1234", serialized)
            self.assertNotIn(str(cloud_backup), serialized)

    def test_summarize_capabilities_handles_vault_and_non_vault_without_secret_echo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            non_vault = self.module.summarize_capabilities(
                root,
                {"status": "not_vault", "next_action": "run_with_a_vault"},
                env={"TYPESAFE_API_KEY": "ts_dummy_secret_value_should_never_echo"},
            )
            self.assertEqual(non_vault["vault_status"], "not_vault")
            self.assertIn("vault_init_scaffolding", non_vault["ready_now"])
            self.assertIn("jev_direct_judgment_credential_present", non_vault["ready_now"])
            self.assertNotIn("ts_dummy_secret_value_should_never_echo", json.dumps(non_vault))

            synced_vault = root / "OneDrive" / "my-vault"
            synced_vault.mkdir(parents=True)
            ready = self.module.summarize_capabilities(
                synced_vault,
                {"status": "ready", "next_action": "none"},
                env={},
            )
            self.assertEqual(ready["vault_status"], "ready")
            self.assertTrue(ready["cloud_sync_detected"])
            self.assertEqual(ready["cloud_sync_provider"], "onedrive")
            self.assertIn("review_cloud_sync_boundary_for_sensitive_notes", ready["optional_setup"])
            self.assertIn("set_typesafe_api_key_for_optional_jev_judgments", ready["optional_setup"])


class DoctorBackupOkfCloudSyncCliTests(unittest.TestCase):
    def _make_vault(self, base: Path) -> Path:
        vault = base / "vault"
        (vault / "00-meta").mkdir(parents=True)
        (vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({
                "vault_name": "CloudTest",
                "required_keys": ["title"],
                "enums": {},
                "hot_note": "00-meta/hot.md",
                "handoff_note": "",
            }),
            encoding="utf-8",
        )
        (vault / "00-meta" / "hot.md").write_text("# Hot\nCurrent status.\n", encoding="utf-8")
        (vault / "note.md").write_text("---\ntitle: Note\n---\nBody text.\n", encoding="utf-8")
        return vault

    def test_doctor_cloud_sync_and_summary_flags_are_opt_in_and_keep_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault = self._make_vault(root / "Dropbox")
            doctor = str(SCRIPTS / "vault_doctor.py")

            default_proc = subprocess.run(
                [sys.executable, doctor, "--vault", str(vault), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(default_proc.returncode, 0, default_proc.stderr)
            default_data = json.loads(default_proc.stdout)
            self.assertNotIn("cloud_sync", default_data)
            self.assertNotIn("capability_summary", default_data)

            opt_proc = subprocess.run(
                [sys.executable, doctor, "--vault", str(vault), "--cloud-sync", "--summary", "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(opt_proc.returncode, 0, opt_proc.stderr)
            opt_data = json.loads(opt_proc.stdout)
            self.assertEqual(opt_data["cloud_sync"]["status"], "cloud_synced")
            self.assertEqual(opt_data["cloud_sync"]["vault_sync"]["provider"], "dropbox")
            self.assertEqual(opt_data["capability_summary"]["vault_status"], "ready")
            self.assertTrue(opt_data["capability_summary"]["cloud_sync_detected"])

            text_proc = subprocess.run(
                [sys.executable, doctor, "--vault", str(vault), "--cloud-sync", "--summary"],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(text_proc.returncode, 0, text_proc.stderr)
            self.assertIn("cloud_sync: cloud_synced (vault_provider=dropbox", text_proc.stdout)
            self.assertIn("summary_vault_status: ready (cloud_sync=true)", text_proc.stdout)

    def test_backup_and_okf_emit_advisory_stderr_only_for_cloud_synced_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault = self._make_vault(root / "local")
            local_backup = root / "local-backups"
            cloud_backup = root / "Google Drive" / "backups"

            backup_script = str(SCRIPTS / "backup_vault.py")
            local_proc = subprocess.run(
                [sys.executable, backup_script, "--vault", str(vault), "--target", str(local_backup)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(local_proc.returncode, 0, local_proc.stderr)
            self.assertNotIn("cloud-synced", local_proc.stderr)

            cloud_proc = subprocess.run(
                [sys.executable, backup_script, "--vault", str(vault), "--target", str(cloud_backup)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(cloud_proc.returncode, 0, cloud_proc.stderr)
            self.assertIn("cloud-synced folder (google_drive)", cloud_proc.stderr)

            okf_script = str(SCRIPTS / "vault_okf.py")
            local_out = root / "local-okf"
            cloud_out = root / "OneDrive" / "okf-export"
            (root / "OneDrive").mkdir(parents=True)

            okf_local = subprocess.run(
                [sys.executable, okf_script, "--vault", str(vault), "--note", "note.md", "--output", str(local_out)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(okf_local.returncode, 0, okf_local.stderr)
            self.assertNotIn("cloud-synced", okf_local.stderr)

            okf_cloud = subprocess.run(
                [sys.executable, okf_script, "--vault", str(vault), "--note", "note.md", "--output", str(cloud_out)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(okf_cloud.returncode, 0, okf_cloud.stderr)
            self.assertIn("cloud-synced folder (onedrive)", okf_cloud.stderr)
            self.assertEqual(json.loads(okf_local.stdout), json.loads(okf_cloud.stdout))


if __name__ == "__main__":
    unittest.main()
