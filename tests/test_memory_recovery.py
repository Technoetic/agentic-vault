"""A saved vault must recover the context and evidence from that snapshot."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/agentic-vault/scripts"
SPEC = importlib.util.spec_from_file_location("recovery_backup", SCRIPTS / "backup_vault.py")
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class MemoryRecoveryTests(unittest.TestCase):
    def test_restored_vault_injects_and_recalls_saved_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            vault = root / "working vault"
            (vault / "00-meta").mkdir(parents=True)
            (vault / "20-knowledge").mkdir()
            (vault / "90-assets").mkdir()
            config = {
                "vault_name": "recovery fixture",
                "hot_note": "00-meta/hot.md",
                "handoff_note": "00-meta/handoff.md",
                "hot_max_tokens": 200,
                "handoff_max_tokens": 200,
                "deny_zones": ["90-assets"],
                "exclude_dirs": [".git"],
            }
            (vault / "00-meta/vault-config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            hot = vault / "00-meta/hot.md"
            note = vault / "20-knowledge/rollback.md"
            binary = vault / "90-assets/diagram.bin"
            hot.write_text("Current approved build: 42", encoding="utf-8")
            (vault / "00-meta/handoff.md").write_text(
                "Next action: verify the saved deployment decision.", encoding="utf-8"
            )
            note.write_text(
                "# Pipeline rollback\nPipeline rollback restores build 42.\n",
                encoding="utf-8",
            )
            binary.write_bytes(b"original-diagram\x00\xff")

            snapshot = backup.create_backup(vault, root / "backups")
            hot.write_text("Current approved build: 99", encoding="utf-8")
            note.write_text("# Pipeline rollback\nUse build 99.\n", encoding="utf-8")
            binary.unlink()
            restored = backup.restore_snapshot(snapshot, root / "restored vault")

            environment = os.environ.copy()
            environment["CLAUDE_PROJECT_DIR"] = str(restored)
            injection = subprocess.run(
                [sys.executable, str(ROOT / "hooks/session_start.py")],
                env=environment, capture_output=True, text=True, encoding="utf-8",
                timeout=15, check=False,
            )
            self.assertEqual(injection.returncode, 0, injection.stderr)
            self.assertIn("Current approved build: 42", injection.stdout)
            self.assertNotIn("build: 99", injection.stdout)

            retrieval = subprocess.run(
                [sys.executable, str(SCRIPTS / "vault_recall.py"),
                 "--vault", str(restored), "--query", "pipeline rollback build",
                 "--max-tokens", "200", "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
                check=False,
            )
            self.assertEqual(retrieval.returncode, 0, retrieval.stderr)
            result = json.loads(retrieval.stdout)
            self.assertEqual(result["matches"][0]["path"], "20-knowledge/rollback.md")
            self.assertIn("Pipeline rollback", result["context"])
            self.assertIn("build 42", result["context"])
            self.assertNotIn("99", result["context"])
            self.assertEqual(
                (restored / "90-assets/diagram.bin").read_bytes(),
                b"original-diagram\x00\xff",
            )
            self.assertEqual(hot.read_text(encoding="utf-8"), "Current approved build: 99")


if __name__ == "__main__":
    unittest.main()
