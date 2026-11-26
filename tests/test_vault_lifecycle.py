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

    def test_frontmatter_wikilinks_dotted_names_and_yaml_edge_cases(self) -> None:
        notes = {
            "20-knowledge/Proof-v1.0.md": (
                "---\n"
                'title: "<!-- does not corrupt frontmatter"\n'
                "status: withdrawn\n"
                "withdrawn_reason: 'Proof doesn''t hold under edge case'\n"
                f"withdrawn_revision: {VALID_SHA40}\n"
                "---\n\nBody.\n"
            ),
            "20-knowledge/MultilineReason.md": (
                "---\n"
                "status: withdrawn\n"
                "withdrawn_reason:\n"
                "  Sign error discovered in lemma 3\n"
                "---\n\nBody.\n"
            ),
            "20-knowledge/BlockWithFence.md": (
                "---\n"
                "status: withdrawn\n"
                "withdrawn_reason: |\n"
                "  ```\n"
                "  actual reason line\n"
                "  ```\n"
                "---\n\nBody.\n"
            ),
            "20-knowledge/NullReason.md": (
                "---\n"
                "status: withdrawn\n"
                "withdrawn_reason: null\n"
                "---\n\nBody.\n"
            ),
            "20-knowledge/TildeReason.md": (
                "---\n"
                "status: withdrawn\n"
                "withdrawn_reason: ~\n"
                "---\n\nBody.\n"
            ),
            "20-knowledge/EmptyBlock2.md": (
                "---\n"
                "status: withdrawn\n"
                "withdrawn_reason: |2\n"
                "---\n\nBody.\n"
            ),
            "20-knowledge/FmCiter.md": (
                "---\n"
                "status: active\n"
                'sources: ["[[Proof-v1.0]]"]\n'
                "---\n\nNo body links.\n"
            ),
        }
        report = lifecycle.analyze_lifecycle(notes, self.config)
        missing_reason_paths = {
            w["path"] for w in report["warnings"] if w["code"] == "withdrawn-missing-reason"
        }
        self.assertEqual(
            missing_reason_paths,
            {
                "20-knowledge/NullReason.md",
                "20-knowledge/TildeReason.md",
                "20-knowledge/EmptyBlock2.md",
            },
        )
        cites = [w for w in report["warnings"] if w["code"] == "cites-withdrawn-note"]
        self.assertEqual(len(cites), 1)
        self.assertEqual(cites[0]["path"], "20-knowledge/FmCiter.md")
        self.assertEqual(cites[0]["details"]["target"], "20-knowledge/Proof-v1.0.md")
        cascade = {c["path"]: c for c in report["cascade"]}
        self.assertEqual(
            cascade["20-knowledge/Proof-v1.0.md"]["direct_dependents"],
            ["20-knowledge/FmCiter.md"],
        )

    def test_superseded_chain_is_linear_time_and_handles_lollipops(self) -> None:
        # 2500-node acyclic superseded chain plus a lollipop into a 2-cycle must complete quickly.
        n = 2500
        notes = {
            f"20-knowledge/N{i:04d}.md": _note(
                "superseded", "", superseded_by=f'"[[N{i + 1:04d}]]"'
            )
            for i in range(n)
        }
        notes[f"20-knowledge/N{n:04d}.md"] = _note("active", "final")
        notes["20-knowledge/Lolli.md"] = _note("superseded", "", superseded_by='"[[C1]]"')
        notes["20-knowledge/C1.md"] = _note("superseded", "", superseded_by='"[[C2]]"')
        notes["20-knowledge/C2.md"] = _note("superseded", "", superseded_by='"[[C1]]"')

        report = lifecycle.analyze_lifecycle(notes, self.config)
        cycles = {w["path"] for w in report["warnings"] if w["code"] == "superseded-cycle"}
        self.assertEqual(cycles, {"20-knowledge/Lolli.md", "20-knowledge/C1.md", "20-knowledge/C2.md"})
        cascade = {c["path"]: c for c in report["cascade"]}
        self.assertEqual(
            cascade["20-knowledge/N0000.md"]["terminal_replacement"],
            f"20-knowledge/N{n:04d}.md",
        )

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

    def test_registered_worktree_excluded_and_unregistered_worktree_deny_zone_blocked(self) -> None:
        # Create a real git repository with a registered linked worktree inside the vault.
        git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True, env=git_env)
        (self.vault / "20-knowledge" / "Main.md").write_text(_note("active", "clean"), encoding="utf-8")
        (self.vault / "20-knowledge" / "_archive").mkdir(exist_ok=True)
        (self.vault / "20-knowledge" / "_archive" / "Secret.md").write_text(_note("withdrawn", "no reason"), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.vault, check=True, env=git_env)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=self.vault, check=True, env=git_env)

        wt_dir = self.vault / "linked-wt"
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt_dir)], cwd=self.vault, check=True, env=git_env)
        # Put a broken withdrawn note inside the registered worktree; must be skipped entirely.
        (wt_dir / "20-knowledge" / "WorktreeBad.md").write_text(_note("withdrawn", "missing"), encoding="utf-8")

        report = lifecycle.check_vault(self.vault)
        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["counts"]["notes_scanned"], 1)

        # Unregistered linked worktree (stale .git file pointing to worktrees/ghost):
        # its own deny zone (20-knowledge/_archive) must stay blocked.
        unreg = self.vault / "stale-wt"
        (unreg / "20-knowledge" / "_archive").mkdir(parents=True)
        common_git = self.vault / ".git"
        (unreg / ".git").write_text(f"gitdir: {common_git / 'worktrees' / 'ghost'}\n", encoding="utf-8")
        (unreg / "20-knowledge" / "_archive" / "HiddenBad.md").write_text(_note("withdrawn", "missing"), encoding="utf-8")
        report2 = lifecycle.check_vault(self.vault)
        self.assertTrue(report2["ok"], report2["warnings"])
        self.assertEqual(report2["counts"]["notes_scanned"], 1)

    def test_refuses_junctioned_or_symlinked_vault_root_and_meta_dir(self) -> None:
        if os.name == "nt":
            junction_root = self.vault.parent / "vault-junction"
            proc_j = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction_root), str(self.vault)],
                capture_output=True, text=True, check=False,
            )
            if proc_j.returncode != 0:
                self.skipTest("mklink /J unavailable")
            with self.assertRaises(lifecycle.LifecycleError) as ctx1:
                lifecycle.check_vault(junction_root)
            self.assertEqual(str(ctx1.exception), "vault_not_found")

            # Junctioned 00-meta inside a fresh vault pointing to external dir with permissive config.
            ext_meta = self.vault.parent / "ext-meta"
            ext_meta.mkdir()
            (ext_meta / "vault-config.json").write_text('{"deny_zones": []}', encoding="utf-8")
            evil_vault = self.vault.parent / "evil-vault"
            evil_vault.mkdir()
            proc_m = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(evil_vault / "00-meta"), str(ext_meta)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc_m.returncode, 0, proc_m.stdout + proc_m.stderr)
            with self.assertRaises(lifecycle.LifecycleError) as ctx2:
                lifecycle.check_vault(evil_vault)
            self.assertEqual(str(ctx2.exception), "not_a_vault")
        else:
            sym_root = self.vault.parent / "vault-sym"
            os.symlink(self.vault, sym_root)
            with self.assertRaises(lifecycle.LifecycleError) as ctx1:
                lifecycle.check_vault(sym_root)
            self.assertEqual(str(ctx1.exception), "vault_not_found")
            ext_meta = self.vault.parent / "ext-meta"
            ext_meta.mkdir()
            (ext_meta / "vault-config.json").write_text('{"deny_zones": []}', encoding="utf-8")
            evil_vault = self.vault.parent / "evil-vault"
            evil_vault.mkdir()
            os.symlink(ext_meta, evil_vault / "00-meta")
            with self.assertRaises(lifecycle.LifecycleError) as ctx2:
                lifecycle.check_vault(evil_vault)
            self.assertEqual(str(ctx2.exception), "not_a_vault")

    def test_long_path_vault_and_deep_nested_notes_do_not_fail_open(self) -> None:
        from vault_evidence import fs_path
        # Build a deep vault (> 245 chars) and a deep note (> 310 chars) safely via fs_path.
        nested_base = self.vault.parent / ("a" * 60) / ("b" * 60) / ("c" * 60) / ("d" * 60) / "long-vault"
        os.makedirs(fs_path(nested_base / "00-meta"), exist_ok=True)
        deep_dir = nested_base / "20-knowledge" / ("sub_" + "x" * 60)
        os.makedirs(fs_path(deep_dir), exist_ok=True)
        with open(fs_path(nested_base / "00-meta" / "vault-config.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"deny_zones": ["20-knowledge/_archive"], "exclude_dirs": [".git"]}))
        with open(fs_path(deep_dir / "DeepBroken.md"), "w", encoding="utf-8") as fh:
            fh.write(_note("withdrawn", "missing reason"))

        report = lifecycle.check_vault(nested_base)
        self.assertFalse(report["ok"])
        self.assertEqual(report["counts"]["notes_scanned"], 1)
        self.assertEqual([w["code"] for w in report["warnings"]], ["withdrawn-missing-reason"])


if __name__ == "__main__":
    unittest.main()
