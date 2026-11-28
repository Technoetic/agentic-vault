"""Tests for vault_policy_drift.py and vault_doctor.py --policy-drift."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vault_policy_drift
from tests._winfs import make_junction


class VaultPolicyDriftTests(unittest.TestCase):
    def _make_vault(self, root: Path) -> Path:
        (root / "00-meta" / "scripts" / "git-hooks").mkdir(parents=True)
        (root / ".claude").mkdir(parents=True)
        cfg = {
            "vault_name": "policy-drift-test",
            "hot_note": "00-meta/hot.md",
            "handoff_note": "00-meta/handoff.md",
            "hot_max_tokens": 500,
            "handoff_max_tokens": 500,
        }
        (root / "00-meta" / "vault-config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        (root / "00-meta" / "hot.md").write_text("Hot context.\n", encoding="utf-8")
        (root / "00-meta" / "handoff.md").write_text("Handoff context.\n", encoding="utf-8")
        (root / "00-meta" / "scripts" / "deny-guard.py").write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
        (root / "00-meta" / "scripts" / "vault_healthcheck.py").write_text("#!/usr/bin/env python3\nprint('hc')\n", encoding="utf-8")
        (root / "00-meta" / "scripts" / "git-hooks" / "pre-commit").write_text("#!/bin/sh\necho pre-commit\n", encoding="utf-8")
        settings = {
            "permissions": {
                "deny": [
                    "Read(SECRET_TOKEN_PATH_DO_NOT_ECHO/.env)",
                    "Bash(rm -rf /)",
                ]
            },
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'py -3 "${CLAUDE_PROJECT_DIR}/00-meta/scripts/deny-guard.py" --sentinel=SECRET_ARG_NEVER_ECHO',
                            }
                        ],
                    }
                ]
            },
        }
        (root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return root

    def test_record_check_clean_and_content_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av_pd_clean_") as td:
            vault = self._make_vault(Path(td))
            before = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(before["status"], "not_recorded")
            self.assertEqual(before["live_deny_rules"], 2)
            self.assertGreaterEqual(before["live_hooks_resolved"], 3)

            rec = vault_policy_drift.record_policy_baseline(vault, plugin_root=REPO_ROOT)
            self.assertEqual(rec["status"], "recorded")
            self.assertEqual(rec["deny_rules_recorded"], 2)
            self.assertGreaterEqual(rec["hooks_recorded"], 3)

            # Baseline JSON must never contain raw deny rule strings or raw hook commands
            baseline_raw = (vault / vault_policy_drift.BASELINE_REL).read_text(encoding="utf-8")
            self.assertNotIn("SECRET_TOKEN_PATH_DO_NOT_ECHO", baseline_raw)
            self.assertNotIn("SECRET_ARG_NEVER_ECHO", baseline_raw)

            checked = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(checked["status"], "clean")
            self.assertEqual(checked["baseline_integrity"], "verified")
            self.assertEqual(checked["deny_rules_removed"], 0)
            self.assertEqual(checked["hooks_modified"], 0)
            self.assertEqual(checked["hooks_missing"], 0)
            self.assertEqual(checked["hooks_untracked"], 0)
            checked_dump = json.dumps(checked)
            self.assertNotIn("SECRET_TOKEN_PATH_DO_NOT_ECHO", checked_dump)
            self.assertNotIn("SECRET_ARG_NEVER_ECHO", checked_dump)

    def test_detects_deny_rule_deletion_and_allows_monotonic_addition(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av_pd_deny_") as td:
            vault = self._make_vault(Path(td))
            vault_policy_drift.record_policy_baseline(vault, plugin_root=REPO_ROOT)

            settings_path = vault / ".claude" / "settings.json"
            doc = json.loads(settings_path.read_text(encoding="utf-8"))
            # Adding a stricter deny rule keeps status clean and counts deny_rules_added
            doc["permissions"]["deny"].append("Read(90-assets/**)")
            settings_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            added = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(added["status"], "clean")
            self.assertEqual(added["deny_rules_removed"], 0)
            self.assertEqual(added["deny_rules_added"], 1)

            # Deleting an operator deny rule (arXiv:2611.32195v1 attack pattern) triggers drift_detected
            doc["permissions"]["deny"] = ["Read(90-assets/**)"]
            settings_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            removed = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(removed["status"], "drift_detected")
            self.assertEqual(removed["reason_code"], "deny_rules_removed")
            self.assertEqual(removed["deny_rules_removed"], 2)
            self.assertTrue(any(f["kind"] == "deny_rules_removed" for f in removed["drift_findings"]))
            self.assertNotIn("SECRET_TOKEN_PATH_DO_NOT_ECHO", json.dumps(removed))

            # Deleting settings.json entirely also flags removed deny rules
            settings_path.unlink()
            missing = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(missing["status"], "drift_detected")
            self.assertEqual(missing["deny_rules_removed"], 2)
            self.assertTrue(any(f["kind"] == "settings_file_missing" for f in missing["drift_findings"]))

    def test_detects_hook_script_modification_missing_and_untracked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av_pd_hook_") as td:
            vault = self._make_vault(Path(td))
            vault_policy_drift.record_policy_baseline(vault, plugin_root=REPO_ROOT)

            # Silent hook script update (arXiv:2611.01976v1 tool update gap)
            guard = vault / "00-meta" / "scripts" / "deny-guard.py"
            guard.write_text("#!/usr/bin/env python3\nprint('tampered')\n", encoding="utf-8")
            mod = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(mod["status"], "drift_detected")
            self.assertEqual(mod["reason_code"], "hook_script_drift")
            self.assertEqual(mod["hooks_modified"], 1)

            # Restore and add an untracked hook script
            guard.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
            (vault / "00-meta" / "scripts" / "git-hooks" / "pre-push").write_text("#!/bin/sh\necho push\n", encoding="utf-8")
            untr = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(untr["status"], "drift_detected")
            self.assertEqual(untr["hooks_untracked"], 1)

            # Delete a baseline hook script
            guard.unlink()
            miss = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(miss["status"], "drift_detected")
            self.assertGreaterEqual(miss["hooks_missing"], 1)

    def test_rejects_tampered_baseline_duplicate_json_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av_pd_sec_") as td:
            vault = self._make_vault(Path(td))
            vault_policy_drift.record_policy_baseline(vault, plugin_root=REPO_ROOT)

            # Tamper with sealed baseline digest
            bpath = vault / vault_policy_drift.BASELINE_REL
            bdoc = json.loads(bpath.read_text(encoding="utf-8"))
            bdoc["hooks"] = []
            bpath.write_text(json.dumps(bdoc), encoding="utf-8")
            bad = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(bad["status"], "invalid_baseline")
            self.assertEqual(bad["baseline_integrity"], "mismatch")

            # Re-record clean baseline, then corrupt settings with duplicate JSON key
            bpath.unlink()
            vault_policy_drift.record_policy_baseline(vault, plugin_root=REPO_ROOT)
            (vault / ".claude" / "settings.json").write_text(
                '{"permissions": {"deny": ["a"]}, "permissions": {"deny": []}}',
                encoding="utf-8",
            )
            dup = vault_policy_drift.diagnose_policy_drift(vault, plugin_root=REPO_ROOT)
            self.assertEqual(dup["status"], "drift_detected")
            self.assertTrue(any(e["reason"] == "invalid_json" for e in dup["settings_errors"]))

            # Hardlink on hook script fails closed as unsafe
            vault2 = self._make_vault(Path(td) / "v2")
            vault_policy_drift.record_policy_baseline(vault2, plugin_root=REPO_ROOT)
            guard2 = vault2 / "00-meta" / "scripts" / "deny-guard.py"
            alias = Path(td) / "alias_guard.py"
            os.link(guard2, alias)
            hl = vault_policy_drift.diagnose_policy_drift(vault2, plugin_root=REPO_ROOT)
            self.assertEqual(hl["status"], "drift_detected")
            self.assertGreaterEqual(hl["hooks_unsafe"], 1)

    def test_refuses_junctioned_or_symlinked_vault_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av_pd_lnk_") as td:
            base = Path(td)
            real_vault = self._make_vault(base / "real")
            link_vault = base / "link_vault"
            if os.name == "nt":
                make_junction(link_vault, real_vault)
            else:
                os.symlink(real_vault, link_vault)
            res = vault_policy_drift.diagnose_policy_drift(link_vault, plugin_root=REPO_ROOT)
            self.assertEqual(res["status"], "error")
            self.assertEqual(res["reason_code"], "vault_unsafe")
            with self.assertRaises(vault_policy_drift.PolicyDriftError):
                vault_policy_drift.record_policy_baseline(link_vault, plugin_root=REPO_ROOT)

    def test_doctor_policy_drift_flag_is_opt_in_and_fail_soft(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av_pd_doc_") as td:
            vault = self._make_vault(Path(td))
            vault_policy_drift.record_policy_baseline(vault, plugin_root=REPO_ROOT)

            # Default doctor output does not include policy_drift
            p_def = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "vault_doctor.py"),
                 "--vault", str(vault), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p_def.returncode, 0, p_def.stderr)
            d_def = json.loads(p_def.stdout)
            self.assertNotIn("policy_drift", d_def)

            # Mutate settings to remove deny rule: doctor --policy-drift reports drift_detected
            # while keeping doctor's own exit code 0 (fail-soft contract).
            s_path = vault / ".claude" / "settings.json"
            s_doc = json.loads(s_path.read_text(encoding="utf-8"))
            s_doc["permissions"]["deny"] = []
            s_path.write_text(json.dumps(s_doc), encoding="utf-8")

            p_pd = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "vault_doctor.py"),
                 "--vault", str(vault), "--policy-drift", "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p_pd.returncode, 0, p_pd.stderr)
            d_pd = json.loads(p_pd.stdout)
            self.assertEqual(d_pd["policy_drift"]["status"], "drift_detected")
            self.assertEqual(d_pd["policy_drift"]["deny_rules_removed"], 2)

            # Standalone CLI check returns exit code 1 on drift_detected
            p_cli = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "vault_policy_drift.py"),
                 "check", "--vault", str(vault), "--plugin-root", str(REPO_ROOT), "--format", "text"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p_cli.returncode, 1)
            self.assertIn("policy_drift: drift_detected (deny_rules_removed)", p_cli.stdout)


if __name__ == "__main__":
    unittest.main()
