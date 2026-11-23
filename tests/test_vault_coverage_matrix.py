"""Tests for host/OS/adapter capability coverage matrix and vault_doctor --coverage."""
from __future__ import annotations

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

import vault_coverage_matrix as vcm  # noqa: E402


class VaultCoverageMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="av-cov-")
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name).resolve() / "vault"
        (self.vault / "00-meta" / "scripts" / "git-hooks").mkdir(parents=True)
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({
                "deny_zones": ["90-assets"],
                "exclude_dirs": [".git"],
                "handoff_note": "00-meta/handoff.md",
                "hot_note": "00-meta/hot.md",
            }),
            encoding="utf-8",
        )
        (self.vault / "00-meta" / "handoff.md").write_text("Next.\n", encoding="utf-8")
        (self.vault / "00-meta" / "hot.md").write_text("Hot.\n", encoding="utf-8")
        (self.vault / "00-meta" / "scripts" / "vault_healthcheck.py").write_text("# hc\n", encoding="utf-8")
        (self.vault / "00-meta" / "scripts" / "deny-guard.py").write_text("# guard\n", encoding="utf-8")

    def test_windows_vs_linux_coverage_classifies_supported_unsupported_and_not_measured(self) -> None:
        win = vcm.diagnose_coverage(self.vault, host="all", platform_name="win32")
        self.assertEqual(win["schema"], vcm.SCHEMA)
        self.assertEqual(win["platform"], "windows")
        self.assertTrue(win["vault_initialized"])
        by_key = {(r["host"], r["surface"]): r for r in win["rows"]}
        self.assertEqual(by_key[("all", "backup_posix_dir_fsync")]["status"], "unsupported")
        self.assertEqual(by_key[("all", "adapter_aside_helper")]["status"], "opt_in_available")
        self.assertEqual(by_key[("claude", "adapter_deny_guard")]["vault_state"], "configured")
        self.assertEqual(by_key[("codex", "adapter_deny_guard")]["status"], "unsupported")
        self.assertEqual(by_key[("antigravity", "adapter_deny_guard")]["status"], "unsupported")
        self.assertEqual(by_key[("antigravity", "antigravity_live_interactive_session")]["status"], "not_measured")
        self.assertEqual(by_key[("all", "host_runtime_hook_execution")]["status"], "not_measured")

        lin = vcm.diagnose_coverage(self.vault, host="all", platform_name="linux")
        by_key_lin = {(r["host"], r["surface"]): r for r in lin["rows"]}
        self.assertEqual(by_key_lin[("all", "backup_posix_dir_fsync")]["status"], "supported")
        self.assertEqual(by_key_lin[("all", "adapter_aside_helper")]["status"], "unsupported")
        self.assertEqual(sum(lin["counts"].values()), len(lin["rows"]))

    def test_zero_raw_path_leak_and_non_vault_folder(self) -> None:
        empty_dir = self.vault.parent / "not-a-vault"
        empty_dir.mkdir()
        rep = vcm.diagnose_coverage(empty_dir, host="codex", platform_name="darwin")
        self.assertFalse(rep["vault_initialized"])
        self.assertEqual(rep["platform"], "darwin")
        dumped = json.dumps(rep, ensure_ascii=False)
        self.assertNotIn(str(empty_dir), dumped)
        self.assertNotIn(str(self.vault.parent), dumped)

    def test_symlinked_config_is_not_trusted_as_initialized(self) -> None:
        other = self.vault.parent / "other-config.json"
        other.write_text("{}", encoding="utf-8")
        sym_vault = self.vault.parent / "sym-vault"
        (sym_vault / "00-meta").mkdir(parents=True)
        try:
            os.symlink(other, sym_vault / "00-meta" / "vault-config.json")
        except OSError:
            self.skipTest("symlink creation not permitted in this environment")
        rep = vcm.diagnose_coverage(sym_vault)
        self.assertFalse(rep["vault_initialized"])

    def test_doctor_cli_coverage_flag_and_default_output_invariance(self) -> None:
        base_proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "vault_doctor.py"), "--vault", str(self.vault), "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        cov_proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "vault_doctor.py"), "--vault", str(self.vault), "--coverage", "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(base_proc.returncode, cov_proc.returncode)
        base_data = json.loads(base_proc.stdout)
        cov_data = json.loads(cov_proc.stdout)
        self.assertNotIn("coverage_matrix", base_data)
        self.assertIn("coverage_matrix", cov_data)
        self.assertEqual(cov_data["coverage_matrix"]["schema"], vcm.SCHEMA)
        self.assertGreater(cov_data["coverage_matrix"]["counts"]["supported"], 0)
        self.assertGreater(cov_data["coverage_matrix"]["counts"]["not_measured"], 0)

        # Text format also renders cleanly without leaking the raw vault path.
        txt_proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "vault_doctor.py"), "--vault", str(self.vault), "--coverage", "--format", "text"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(txt_proc.returncode, base_proc.returncode)
        self.assertIn("coverage_matrix: ok", txt_proc.stdout)
        self.assertIn("coverage_trust_boundary:", txt_proc.stdout)
        self.assertNotIn(str(self.vault), txt_proc.stdout)

    def test_versioned_darwin_and_unknown_platform_handling(self) -> None:
        mac = vcm.diagnose_coverage(self.vault, host="claude", platform_name="darwin23")
        self.assertEqual(mac["platform"], "darwin")
        by_mac = {(r["host"], r["surface"]): r for r in mac["rows"]}
        self.assertEqual(by_mac[("all", "backup_posix_dir_fsync")]["status"], "supported")
        self.assertEqual(by_mac[("all", "adapter_aside_helper")]["status"], "unsupported")

        unk = vcm.diagnose_coverage(self.vault, host="claude", platform_name="freebsd14")
        self.assertEqual(unk["platform"], "unknown")
        by_unk = {(r["host"], r["surface"]): r for r in unk["rows"]}
        self.assertEqual(by_unk[("all", "backup_posix_dir_fsync")]["status"], "not_measured")
        self.assertEqual(by_unk[("all", "adapter_aside_helper")]["status"], "not_measured")


if __name__ == "__main__":
    unittest.main()
