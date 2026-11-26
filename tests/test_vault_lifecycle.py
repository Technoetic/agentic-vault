"""Tests for skills/agentic-vault/scripts/vault_lifecycle.py."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_lifecycle as lifecycle  # noqa: E402

VALID_SHA40 = "adc7f1241b42e322a6451854ab7e4b4c146bf78a"
VALID_SHA64 = "a" * 64


def _note(status: str, body: str = "", **extra: str) -> str:
    lines = ["---", 'title: "Sample"', "type: concept", f"status: {status}"]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    lines.extend(["---", "", body])
    return "\n".join(lines) + "\n"


class VaultLifecycleUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "deny_zones": ["10-inbox/_processed", "20-knowledge/_archive", "90-assets", ".obsidian"],
            "exclude_dirs": [".git", "node_modules"],
        }

    def test_clean_vault_with_valid_withdrawn_and_superseded_has_zero_warnings(self) -> None:
        notes = {
            "20-knowledge/Weil.md": _note(
                "withdrawn",
                "Withdrawn proof notice.",
                withdrawn_reason='"Sign error in trace cancellation"',
                withdrawn_revision=VALID_SHA40,
            ),
            "20-knowledge/VP-v1.md": _note(
                "superseded",
                "Original proof preserved unchanged.",
                superseded_by='"[[VP-v2]]"',
            ),
            "20-knowledge/VP-v2.md": _note("active", "Updated subsuming proof."),
            "20-knowledge/Summary.md": _note("active", "See [[VP-v1]] and updated [[VP-v2]]."),
            "20-knowledge/OldArchive.md": _note("archive", "Historical link to [[Weil]]."),
        }
        report = lifecycle.analyze_lifecycle(notes, self.config)
        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["schema"], lifecycle.SCHEMA)
        self.assertEqual(report["authority"], lifecycle.AUTHORITY)
        self.assertEqual(report["counts"]["withdrawn_notes"], 1)
        self.assertEqual(report["counts"]["superseded_notes"], 1)
        self.assertEqual(report["counts"]["warnings"], 0)

    def test_withdrawn_validation_and_active_citation_cascade_are_content_free(self) -> None:
        secret_reason = "PRIVATE_REASON_PROSE_NEVER_ECHO"
        notes = {
            "20-knowledge/BadWithdrawn.md": _note(
                "withdrawn",
                "Body PRIVATE_BODY_NEVER_ECHO",
                withdrawn_revision="not-a-sha",
            ),
            "20-knowledge/GoodWithdrawn.md": _note(
                "withdrawn",
                "Body text.",
                withdrawn_reason=f'"{secret_reason}"',
                withdrawn_revision=VALID_SHA64,
            ),
            "20-knowledge/DirectDep.md": _note("active", "Depends on [[GoodWithdrawn]]."),
            "20-knowledge/TransitiveDep.md": _note("draft", "Builds on [[DirectDep]]."),
        }
        report = lifecycle.analyze_lifecycle(notes, self.config)
        self.assertFalse(report["ok"])
        codes = {w["code"] for w in report["warnings"]}
        self.assertIn("withdrawn-missing-reason", codes)
        self.assertIn("withdrawn-invalid-revision", codes)
        self.assertIn("cites-withdrawn-note", codes)

        cite = next(w for w in report["warnings"] if w["code"] == "cites-withdrawn-note")
        self.assertEqual(cite["path"], "20-knowledge/DirectDep.md")
        self.assertEqual(cite["details"]["target"], "20-knowledge/GoodWithdrawn.md")
        self.assertEqual(cite["details"]["withdrawn_revision"], VALID_SHA64)
        self.assertTrue(cite["details"]["has_withdrawn_reason"])
        self.assertEqual(cite["details"]["withdrawal_scope"], "proof_or_evidence_only")

        cascade = {c["path"]: c for c in report["cascade"]}
        gw = cascade["20-knowledge/GoodWithdrawn.md"]
        self.assertEqual(gw["direct_dependents"], ["20-knowledge/DirectDep.md"])
        self.assertEqual(
            gw["transitive_dependents"],
            ["20-knowledge/DirectDep.md", "20-knowledge/TransitiveDep.md"],
        )
        self.assertFalse(gw["depth_truncated"])

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(secret_reason, serialized)
        self.assertNotIn("PRIVATE_BODY_NEVER_ECHO", serialized)

    def test_superseded_chain_terminal_replacement_and_cycle_detection(self) -> None:
        notes = {
            "20-knowledge/V1.md": _note("superseded", "v1", superseded_by='"[[V2]]"'),
            "20-knowledge/V2.md": _note("superseded", "v2", superseded_by='"[[V3]]"'),
            "20-knowledge/V3.md": _note("active", "v3 final"),
            "20-knowledge/ConsumerStale.md": _note("active", "Only cites [[V1]]."),
            "20-knowledge/ConsumerUpdated.md": _note("active", "Cites [[V1]] and latest [[V3]]."),
            "20-knowledge/MissingTarget.md": _note("superseded", "missing"),
            "20-knowledge/SelfSup.md": _note("superseded", "self", superseded_by='"[[SelfSup]]"'),
            "20-knowledge/CycA.md": _note("superseded", "a", superseded_by='"[[CycB]]"'),
            "20-knowledge/CycB.md": _note("superseded", "b", superseded_by='"[[CycA]]"'),
        }
        report = lifecycle.analyze_lifecycle(notes, self.config)
        warnings = report["warnings"]
        stale_cites = [w for w in warnings if w["code"] == "cites-superseded-note"]
        self.assertEqual(len(stale_cites), 1)
        self.assertEqual(stale_cites[0]["path"], "20-knowledge/ConsumerStale.md")
        self.assertEqual(stale_cites[0]["details"]["superseded_by"], "20-knowledge/V2.md")
        self.assertEqual(stale_cites[0]["details"]["terminal_replacement"], "20-knowledge/V3.md")

        codes = [w["code"] for w in warnings]
        self.assertIn("superseded-missing-target", codes)
        self.assertIn("superseded-target-unresolved", codes)
        self.assertEqual(codes.count("superseded-cycle"), 2)

    def test_code_fences_inline_code_and_html_comments_do_not_trigger_citations(self) -> None:
        notes = {
            "20-knowledge/Retracted.md": _note(
                "withdrawn",
                "retracted",
                withdrawn_reason='"gap"',
                withdrawn_revision=VALID_SHA40,
            ),
            "20-knowledge/Example.md": _note(
                "active",
                "Inline `[[Retracted]]` and <!-- [[Retracted]] -->\n"
                "```markdown\n[[Retracted]]\n```\n"
                "Plain prose without live link.",
            ),
        }
        report = lifecycle.analyze_lifecycle(notes, self.config)
        self.assertTrue(report["ok"], report["warnings"])

    def test_denied_zones_excluded_and_not_resolved_as_supersession_target(self) -> None:
        notes = {
            "20-knowledge/_archive/Secret.md": _note("active", "archived"),
            "20-knowledge/Old.md": _note(
                "superseded",
                "points into deny zone",
                superseded_by='"[[20-knowledge/_archive/Secret]]"',
            ),
        }
        report = lifecycle.analyze_lifecycle(notes, self.config)
        self.assertEqual(report["counts"]["notes_scanned"], 1)
        unresolved = [w for w in report["warnings"] if w["code"] == "superseded-target-unresolved"]
        self.assertEqual(len(unresolved), 1)
        self.assertNotIn("Secret", json.dumps(report))

    def test_no_network_or_subprocess_imports(self) -> None:
        tree = ast.parse((SCRIPTS / "vault_lifecycle.py").read_text(encoding="utf-8"))
        forbidden = {"requests", "httpx", "aiohttp", "subprocess", "socket", "urllib", "openai", "anthropic"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse({n.name.split(".")[0] for n in node.names} & forbidden)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden)


class VaultLifecycleCLITests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="av-life-")
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name).resolve() / "vault"
        self.vault.mkdir()
        (self.vault / "00-meta").mkdir()
        (self.vault / "20-knowledge").mkdir()
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({"deny_zones": ["20-knowledge/_archive"], "exclude_dirs": [".git"]}),
            encoding="utf-8",
        )

    def _run_cli(self, vault: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "vault_lifecycle.py"), "--vault", str(vault), "check", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_cli_exit_codes_0_1_2_and_read_only_guarantee(self) -> None:
        (self.vault / "20-knowledge" / "A.md").write_text(_note("active", "clean"), encoding="utf-8")
        proc0 = self._run_cli(self.vault)
        self.assertEqual(proc0.returncode, 0, proc0.stdout + proc0.stderr)
        self.assertTrue(json.loads(proc0.stdout)["ok"])

        (self.vault / "20-knowledge" / "W.md").write_text(
            _note("withdrawn", "retracted", withdrawn_reason='"gap"', withdrawn_revision=VALID_SHA40),
            encoding="utf-8",
        )
        (self.vault / "20-knowledge" / "A.md").write_text(_note("active", "Uses [[W]]."), encoding="utf-8")
        proc1 = self._run_cli(self.vault)
        self.assertEqual(proc1.returncode, 1, proc1.stdout + proc1.stderr)
        payload1 = json.loads(proc1.stdout)
        self.assertEqual(payload1["status"], "warnings")
        self.assertEqual(payload1["warnings"][0]["code"], "cites-withdrawn-note")

        non_vault = self.vault.parent / "not-a-vault"
        non_vault.mkdir()
        proc2 = self._run_cli(non_vault)
        self.assertEqual(proc2.returncode, 2)
        self.assertEqual(json.loads(proc2.stdout)["status"], "error")

    def test_cli_skips_symlinks_without_following(self) -> None:
        outside = self.vault.parent / "outside.md"
        outside.write_text(_note("withdrawn", "outside"), encoding="utf-8")
        link_path = self.vault / "20-knowledge" / "linked.md"
        try:
            os.symlink(outside, link_path)
        except OSError:
            self.skipTest("symlink creation not permitted in this environment")
        proc = self._run_cli(self.vault)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["counts"]["notes_scanned"], 0)


if __name__ == "__main__":
    unittest.main()
