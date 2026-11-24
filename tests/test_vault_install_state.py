"""Tests for deterministic install-state tracking and ownership-guarded uninstall."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_install_state  # noqa: E402


class VaultInstallStateTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="av-inst-")
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name).resolve() / "vault"
        (self.vault / "00-meta" / "scripts" / "git-hooks").mkdir(parents=True)
        (self.vault / ".claude" / "rules").mkdir(parents=True)
        # User-owned protected files
        (self.vault / "CLAUDE.md").write_text("# User custom CLAUDE\n", encoding="utf-8")
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({
                "deny_zones": ["90-assets"],
                "exclude_dirs": [".git"],
                "handoff_note": "00-meta/handoff.md",
                "hot_note": "00-meta/hot.md",
            }),
            encoding="utf-8",
        )
        (self.vault / "00-meta" / "handoff.md").write_text("Continue.\n", encoding="utf-8")
        (self.vault / "00-meta" / "hot.md").write_text("Hot.\n", encoding="utf-8")
        # Stamped managed files
        (self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").write_text(
            "#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=0.54.0\n",
            encoding="utf-8",
        )
        (self.vault / "00-meta" / "scripts" / "git-hooks" / "pre-commit").write_text(
            "#!/bin/sh\n# agentic-vault:hook engine=0.54.0\n",
            encoding="utf-8",
        )
        (self.vault / ".claude" / "rules" / "vault-workflow.md").write_text(
            "<!-- agentic-vault:rule engine=0.54.0 -->\n# Workflow\n",
            encoding="utf-8",
        )
        # Unstamped AGENTS.md -> must be treated as user_owned!
        (self.vault / "AGENTS.md").write_text("# Hand-crafted user AGENTS\n", encoding="utf-8")

    def test_record_status_and_uninstall_preserve_user_owned_and_modified_files(self) -> None:
        # Before record: status reports not_recorded + untracked managed list
        pre = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(pre["status"], "not_recorded")
        self.assertEqual(len(pre["untracked_managed"]), 3)

        rec = vault_install_state.record_install_state(self.vault, host="all", engine_version="0.54.0")
        self.assertEqual(rec["managed_count"], 3)
        self.assertEqual(rec["user_owned_count"], 3)  # CLAUDE.md, vault-config.json, unstamped AGENTS.md
        self.assertTrue((self.vault / vault_install_state.STATE_REL).is_file())

        clean = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(clean["status"], "clean")
        self.assertEqual(clean["managed_intact"], 3)
        self.assertEqual(clean["managed_modified"], 0)

        # User edits one managed file -> must be flagged as modified_by_user and preserved on uninstall!
        rule_file = self.vault / ".claude" / "rules" / "vault-workflow.md"
        rule_file.write_text("<!-- agentic-vault:rule engine=0.54.0 -->\n# Custom user edit\n", encoding="utf-8")

        drift = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(drift["status"], "drift_detected")
        self.assertEqual(drift["managed_intact"], 2)
        self.assertEqual(drift["managed_modified"], 1)

        # Dry-run uninstall changes nothing on disk
        dry = vault_install_state.uninstall_managed(self.vault, apply=False)
        self.assertEqual(dry["status"], "dry_run")
        self.assertEqual(sorted(dry["removable"]), [
            "00-meta/scripts/git-hooks/pre-commit",
            "00-meta/scripts/vault_healthcheck.py",
        ])
        self.assertEqual(dry["removed"], [])
        self.assertTrue((self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").is_file())

        # Apply uninstall removes ONLY the 2 unmodified managed files
        applied = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(applied["removed"]), 2)
        self.assertFalse((self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").exists())
        self.assertFalse((self.vault / "00-meta" / "scripts" / "git-hooks" / "pre-commit").exists())
        # Empty managed directories were pruned cleanly
        self.assertFalse((self.vault / "00-meta" / "scripts").exists())
        # User-owned and user-modified files are 100% preserved!
        self.assertTrue((self.vault / "CLAUDE.md").is_file())
        self.assertTrue((self.vault / "AGENTS.md").is_file())
        self.assertTrue((self.vault / "00-meta" / "vault-config.json").is_file())
        self.assertTrue(rule_file.is_file())
        preserved_reasons = {item["path"]: item["reason"] for item in applied["preserved"]}
        self.assertEqual(preserved_reasons[".claude/rules/vault-workflow.md"], "modified_by_user")
        self.assertEqual(preserved_reasons["CLAUDE.md"], "user_owned")
        self.assertEqual(preserved_reasons["AGENTS.md"], "user_owned")
        self.assertEqual(preserved_reasons["00-meta/vault-config.json"], "user_owned")

    def test_full_clean_uninstall_removes_state_manifest(self) -> None:
        vault_install_state.record_install_state(self.vault)
        applied = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertEqual(len(applied["removed"]), 3)
        self.assertTrue(applied["state_removed"])
        self.assertFalse((self.vault / vault_install_state.STATE_REL).exists())
        self.assertTrue((self.vault / "CLAUDE.md").is_file())

    def test_old_sealed_command_entry_is_ignored_on_status_record_and_uninstall(self) -> None:
        rel = ".claude/commands/aside-repl.md"
        target = self.vault / rel
        target.parent.mkdir()
        original = b"<!-- agentic-vault:adapter aside-repl engine=0.47.0 -->\nold procedure\n"
        target.write_bytes(original)
        recorded = vault_install_state.record_install_state(self.vault)
        self.assertNotIn(rel, [item["path"] for item in recorded["entries"]])
        state_path = self.vault / vault_install_state.STATE_REL
        old_manifest = json.loads(state_path.read_text(encoding="utf-8"))
        old_manifest["entries"].append({"path": rel, "ownership": "managed", "stamp": "0.47.0",
                                        "sha256": hashlib.sha256(original).hexdigest(), "bytes": len(original)})
        old_manifest["manifest_sha256"] = vault_install_state._canonical_manifest_sha256(old_manifest)
        state_path.write_text(json.dumps(old_manifest), encoding="utf-8")

        for current in (original, original + b"a separately edited local command\n", None):
            with self.subTest(present=current is not None, edited=current != original):
                if current is None:
                    target.unlink()
                else:
                    target.write_bytes(current)
                status = vault_install_state.diagnose_install_state(self.vault)
                self.assertEqual(status["manifest_integrity"], "verified")
                self.assertEqual(status["status"], "clean")
                self.assertNotIn(rel, [item["path"] for item in status["items"]])
                self.assertNotIn(rel, vault_install_state.uninstall_managed(self.vault)["removable"])

        # Even matching the old hash must not allow removal. An unrelated local edit still reports drift.
        target.write_bytes(original)
        rule = ".claude/rules/vault-workflow.md"
        with (self.vault / rule).open("ab") as handle:
            handle.write(b"local rule change\n")
        drift = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(drift["near_misses"], [{"path": rule, "mismatch": "sha256_mismatch"}])
        applied = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertIn("00-meta/scripts/vault_healthcheck.py", applied["removed"])
        self.assertNotIn(rel, applied["removed"])
        self.assertEqual(target.read_bytes(), original)
        refreshed = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertNotIn(rel, [item["path"] for item in refreshed["entries"]])

    def test_refuses_uninstall_without_recorded_state(self) -> None:
        res = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertEqual(res["status"], "refused")
        self.assertEqual(res["reason_code"], "not_recorded")
        self.assertTrue((self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").is_file())

    def test_rejects_symlinked_managed_target(self) -> None:
        outside = self.vault.parent / "outside-secret.txt"
        outside.write_text("# agentic-vault:adapter deny-guard engine=0.54.0\n", encoding="utf-8")
        link_path = self.vault / "00-meta" / "scripts" / "deny-guard.py"
        try:
            os.symlink(outside, link_path)
        except OSError:
            self.skipTest("symlink creation not permitted in this environment")
        rec = vault_install_state.record_install_state(self.vault)
        by_path = {e["path"]: e for e in rec["entries"]}
        self.assertEqual(by_path["00-meta/scripts/deny-guard.py"]["ownership"], "unknown")
        self.assertEqual(by_path["00-meta/scripts/deny-guard.py"]["state"], "unsafe_path")
        applied = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertTrue(outside.is_file())
        self.assertIn("00-meta/scripts/deny-guard.py", {p["path"] for p in applied["preserved"]})

    def test_doctor_cli_install_state_flag(self) -> None:
        vault_install_state.record_install_state(self.vault)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "vault_doctor.py"), "--vault", str(self.vault), "--install-state", "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["install_state"]["status"], "clean")
        self.assertEqual(data["install_state"]["managed_intact"], 3)

    def test_tampered_manifest_cannot_delete_user_files_or_unstamped_files(self) -> None:

        user_note = self.vault / "20-knowledge" / "my-note.md"
        user_note.parent.mkdir(parents=True)
        user_note.write_text("# Private user note\n", encoding="utf-8")
        # Even if an attacker recomputes manifest_sha256 after forging entries
        # (defense-in-depth), canonical allowlist + stamp checks still protect user files.
        vault_install_state.record_install_state(self.vault)
        state_file = self.vault / vault_install_state.STATE_REL
        doc = json.loads(state_file.read_text(encoding="utf-8"))
        for rel in ("CLAUDE.md", "00-meta/vault-config.json", "20-knowledge/my-note.md", "AGENTS.md"):
            digest = hashlib.sha256((self.vault / rel).read_bytes()).hexdigest()
            doc["entries"] = [e for e in doc["entries"] if e["path"] != rel]
            doc["entries"].append({"path": rel, "ownership": "managed", "sha256": digest, "bytes": 1})
        doc["manifest_sha256"] = vault_install_state._canonical_manifest_sha256(doc)
        state_file.write_text(json.dumps(doc), encoding="utf-8")

        applied = vault_install_state.uninstall_managed(self.vault, apply=True)
        reasons = {p["path"]: p["reason"] for p in applied["preserved"]}
        self.assertEqual(reasons["CLAUDE.md"], "not_managed_surface")
        self.assertEqual(reasons["00-meta/vault-config.json"], "not_managed_surface")
        self.assertEqual(reasons["20-knowledge/my-note.md"], "not_managed_surface")
        self.assertEqual(reasons["AGENTS.md"], "missing_engine_stamp")
        for rel in ("CLAUDE.md", "00-meta/vault-config.json", "20-knowledge/my-note.md", "AGENTS.md"):
            self.assertTrue((self.vault / rel).is_file(), f"{rel} was unexpectedly deleted!")

    def test_manifest_digest_mismatch_fails_closed_on_status_and_uninstall(self) -> None:
        rec = vault_install_state.record_install_state(self.vault)
        self.assertRegex(rec["manifest_sha256"], r"^[0-9a-f]{64}$")
        state_file = self.vault / vault_install_state.STATE_REL
        doc = json.loads(state_file.read_text(encoding="utf-8"))
        doc["host"] = "tampered"
        state_file.write_text(json.dumps(doc), encoding="utf-8")

        diag = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(diag["status"], "invalid_state")
        self.assertEqual(diag["reason_code"], "manifest_digest_mismatch")
        self.assertEqual(diag["manifest_integrity"], "mismatch")
        self.assertTrue(any(m["mismatch"] == "manifest_digest_mismatch" for m in diag["near_misses"]))

        refused = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(refused["reason_code"], "manifest_digest_mismatch")
        self.assertEqual(refused["removed"], [])
        self.assertTrue((self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").is_file())

    def test_legacy_unsealed_manifest_remains_compatible_and_near_misses_report_drift(self) -> None:
        vault_install_state.record_install_state(self.vault)
        state_file = self.vault / vault_install_state.STATE_REL
        doc = json.loads(state_file.read_text(encoding="utf-8"))
        doc.pop("manifest_sha256")
        state_file.write_text(json.dumps(doc), encoding="utf-8")

        diag = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(diag["status"], "clean")
        self.assertEqual(diag["manifest_integrity"], "unsealed_legacy")
        self.assertEqual(diag["near_misses"], [])

        # Modify a managed file and verify structured near_misses reason.
        rule_file = self.vault / ".claude" / "rules" / "vault-workflow.md"
        rule_file.write_text("<!-- agentic-vault:rule engine=0.54.0 -->\n# Edited\n", encoding="utf-8")
        drift = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(drift["status"], "drift_detected")
        self.assertIn(
            {"path": ".claude/rules/vault-workflow.md", "mismatch": "sha256_mismatch"},
            drift["near_misses"],
        )

        # Partial uninstall re-seals the remaining manifest cleanly.
        vault_install_state.record_install_state(self.vault)
        rule_file.write_text("<!-- agentic-vault:rule engine=0.54.0 -->\n# Edited again\n", encoding="utf-8")
        applied = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertEqual(applied["status"], "applied")
        self.assertFalse(applied["state_removed"])
        after = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(after["manifest_integrity"], "verified")

    def test_rejects_hardlinked_managed_target(self) -> None:
        outside = self.vault.parent / "hardlink-source.py"
        outside.write_text("# agentic-vault:adapter deny-guard engine=0.54.0\n", encoding="utf-8")
        target = self.vault / "00-meta" / "scripts" / "deny-guard.py"
        try:
            os.link(outside, target)
        except OSError:
            self.skipTest("hardlink creation not supported on this filesystem")
        rec = vault_install_state.record_install_state(self.vault)
        by_path = {e["path"]: e for e in rec["entries"]}
        self.assertEqual(by_path["00-meta/scripts/deny-guard.py"]["ownership"], "unknown")
        self.assertEqual(by_path["00-meta/scripts/deny-guard.py"]["state"], "unsafe_path")

    def test_managed_replaced_by_dir_reports_drift_and_manifest_rejects_nan_and_duplicates(self) -> None:
        vault_install_state.record_install_state(self.vault)
        hc = self.vault / "00-meta" / "scripts" / "vault_healthcheck.py"
        hc.unlink()
        hc.mkdir()
        diag = vault_install_state.diagnose_install_state(self.vault)
        self.assertEqual(diag["status"], "drift_detected")
        self.assertGreaterEqual(diag["managed_modified"], 1)
        self.assertIn({"path": "00-meta/scripts/vault_healthcheck.py", "mismatch": "unsafe_path"}, diag["near_misses"])

        # Restore file, test NaN rejected fail-closed in legacy manifest (never mid-uninstall crash).
        hc.rmdir()
        hc.write_text("#!/usr/bin/env python3\n# agentic-vault:healthcheck engine=0.54.0\n", encoding="utf-8")
        vault_install_state.record_install_state(self.vault)
        state_file = self.vault / vault_install_state.STATE_REL
        raw_nan = '{"schema":"agentic-vault/install-state/v1","entries":[{"path":"CLAUDE.md","ownership":"user_owned","bytes":NaN}]}'
        state_file.write_text(raw_nan, encoding="utf-8")
        self.assertEqual(vault_install_state.diagnose_install_state(self.vault)["status"], "invalid_state")
        refused = vault_install_state.uninstall_managed(self.vault, apply=True)
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(refused["reason_code"], "invalid_json")
        self.assertTrue(hc.is_file())

        # Duplicate paths and non-dict entries rejected fail-closed.
        dup_doc = {
            "schema": vault_install_state.SCHEMA,
            "entries": [
                {"path": "00-meta/scripts/vault_healthcheck.py", "ownership": "managed", "sha256": "0" * 64},
                {"path": "00-meta/scripts/vault_healthcheck.py", "ownership": "managed", "sha256": "0" * 64},
            ],
        }
        state_file.write_text(json.dumps(dup_doc), encoding="utf-8")
        self.assertEqual(vault_install_state.diagnose_install_state(self.vault)["reason_code"], "invalid_schema")
        self.assertEqual(vault_install_state.uninstall_managed(self.vault, apply=True)["status"], "refused")

        nondict_doc = {"schema": vault_install_state.SCHEMA, "entries": [123]}
        state_file.write_text(json.dumps(nondict_doc), encoding="utf-8")
        self.assertEqual(vault_install_state.diagnose_install_state(self.vault)["reason_code"], "invalid_schema")


if __name__ == "__main__":
    unittest.main()
