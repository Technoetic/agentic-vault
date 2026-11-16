"""Regression tests for /vault-init clean scaffolding, vault-copy guards, and early-exit summary fields."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "assets" / "templates"
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"


def _scaffold_vault(vault: Path, *, project_name: str | None = None, date: str = "2026-11-16") -> None:
    """Reproduce the exact /vault-init steps 2-4 & 8 deterministically."""
    for rel in (
        "00-meta/schemas",
        "00-meta/scratch/step_archive",
        "10-inbox/quick",
        "10-inbox/voice",
        "10-inbox/web",
        "10-inbox/_processed",
        "20-knowledge/concepts",
        "20-knowledge/domains",
        "20-knowledge/patterns",
        "20-knowledge/references",
        "20-knowledge/tools",
        "20-knowledge/sources",
        "20-knowledge/_archive",
        "30-journal",
        "40-people/individuals",
        "40-people/organizations",
        "40-people/interactions",
        "50-projects/_completed",
        "90-assets",
        ".claude/rules",
    ):
        (vault / rel).mkdir(parents=True, exist_ok=True)

    replacements = {
        "{{VAULT_NAME}}": "TestVault",
        "{{PROJECT_NAME}}": project_name or "",
        "{{DATE}}": date,
    }

    def render(template_name: str) -> str:
        text = (TEMPLATES / template_name).read_text(encoding="utf-8")
        for key, val in replacements.items():
            text = text.replace(key, val)
        return text

    always_map = {
        "vault-config.json": "00-meta/vault-config.json",
        "frontmatter-schema.md": "00-meta/schemas/frontmatter.md",
        "hot.md": "00-meta/hot.md",
        "index.md": "00-meta/index.md",
        "log.md": "00-meta/log.md",
        "lessons.md": "00-meta/lessons.md",
    }
    for src, dst in always_map.items():
        (vault / dst).write_text(render(src), encoding="utf-8", newline="\n")

    if project_name:
        proj_dir = vault / "50-projects" / project_name
        proj_dir.mkdir(parents=True, exist_ok=True)
        for kind in ("context", "tasks", "handoff", "decisions", "mistakes"):
            (proj_dir / f"{project_name} {kind}.md").write_text(
                render(f"{kind}.md"), encoding="utf-8", newline="\n"
            )
        cfg = json.loads((vault / "00-meta/vault-config.json").read_text(encoding="utf-8"))
        cfg["handoff_note"] = f"50-projects/{project_name}/{project_name} handoff.md"
        (vault / "00-meta/vault-config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        idx = (vault / "00-meta/index.md").read_text(encoding="utf-8")
        project_entries = "\n".join(
            f"- [[{project_name} {kind}]] — 프로젝트 {kind} 노트"
            for kind in ("context", "tasks", "handoff", "decisions", "mistakes")
        )
        idx = idx.replace(
            "## 프로젝트 (50-projects)\n",
            f"## 프로젝트 (50-projects)\n\n{project_entries}\n",
        )
        (vault / "00-meta/index.md").write_text(idx, encoding="utf-8", newline="\n")

    # Step 4: rules + CLAUDE.md + AGENTS.md
    rule_order = ("architecture", "linking", "frontmatter", "workflow", "collab", "browser")
    rule_bodies = []
    for name in rule_order:
        src_rule = TEMPLATES / "rules" / f"vault-{name}.md"
        raw_rule = src_rule.read_text(encoding="utf-8")
        (vault / ".claude/rules" / src_rule.name).write_text(raw_rule, encoding="utf-8", newline="\n")
        lines = raw_rule.splitlines()
        if lines and lines[0].startswith("<!-- agentic-vault:rule"):
            lines = lines[1:]
            while lines and not lines[0].strip():
                lines = lines[1:]
        rule_bodies.append("\n".join(lines).rstrip())

    (vault / "CLAUDE.md").write_text(render("CLAUDE-vault-stub.md"), encoding="utf-8", newline="\n")
    agents_stub = render("AGENTS-vault-stub.md").rstrip()
    (vault / "AGENTS.md").write_text(
        agents_stub + "\n\n" + "\n\n".join(rule_bodies) + "\n", encoding="utf-8", newline="\n"
    )

    # Step 8: first log entry
    log_path = vault / "00-meta/log.md"
    log_lines = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines()
        if "(아직 항목이 없다" not in ln
    ]
    log_lines.append(f"- {date} 09:00 | Claude | [ops] 볼트 초기화 — /vault-init 스캐폴딩 (TestVault)")
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8", newline="\n")


class VaultInitCleanlinessTests(unittest.TestCase):
    def test_fresh_vault_init_has_zero_issues_with_and_without_minivault(self) -> None:
        for project in (None, "Alpha"):
            with self.subTest(project=project), tempfile.TemporaryDirectory() as tmp:
                vault = Path(tmp) / "vault"
                vault.mkdir()
                _scaffold_vault(vault, project_name=project)
                proc = subprocess.run(
                    [sys.executable, "-X", "utf8", str(HEALTHCHECK), "--vault", str(vault), "--json-summary"],
                    capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                summary = json.loads(proc.stdout.strip().splitlines()[-1])
                self.assertEqual(summary["status"], "checked")
                self.assertEqual(summary["critical"], 0)
                self.assertEqual(summary["issues"], 0, (vault / "00-meta/health-report.md").read_text(encoding="utf-8"))

    def test_early_exit_does_not_emit_unreachable_worktree_heads_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain_dir = Path(tmp) / "plain"
            plain_dir.mkdir()
            missing_dir = Path(tmp) / "missing"
            for target, expected_rc, expected_status in (
                (plain_dir, 0, "not_vault"),
                (missing_dir, 2, "vault_not_found"),
            ):
                with self.subTest(status=expected_status):
                    proc = subprocess.run(
                        [
                            sys.executable, "-X", "utf8", str(HEALTHCHECK),
                            "--vault", str(target), "--worktree-heads", "--json-summary",
                        ],
                        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=20,
                    )
                    self.assertEqual(proc.returncode, expected_rc, proc.stderr)
                    summary = json.loads(proc.stdout.strip().splitlines()[-1])
                    self.assertEqual(summary["status"], expected_status)
                    self.assertNotIn("unreachable_worktree_heads", summary)

    def test_live_claude_probe_is_not_shipped_inside_engine_scripts(self) -> None:
        self.assertFalse((SCRIPTS / "test_rules_inheritance.py").exists())
        self.assertTrue((ROOT / "scripts" / "manual_rules_inheritance_probe.py").is_file())

    def test_vault_copied_doctor_and_adapters_do_not_crash_without_plugin_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            _scaffold_vault(vault)
            bundle = vault / "00-meta" / "scripts"
            bundle.mkdir(parents=True, exist_ok=True)
            for src in SCRIPTS.glob("vault_*.py"):
                shutil.copyfile(src, bundle / src.name)
            for extra in ("jev_client.py", "jev_ask.py", "bizrouter_client.py"):
                shutil.copyfile(SCRIPTS / extra, bundle / extra)
            shutil.copytree(SCRIPTS / "resources", bundle / "resources")

            env_no_plugin = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
            for script_name in ("vault_doctor.py", "vault_adapters.py"):
                help_proc = subprocess.run(
                    [sys.executable, "-X", "utf8", str(bundle / script_name), "--help"],
                    capture_output=True, text=True, encoding="utf-8",
                    env=env_no_plugin, stdin=subprocess.DEVNULL, timeout=15,
                )
                self.assertEqual(help_proc.returncode, 0, help_proc.stderr)

            doc_no_plugin = subprocess.run(
                [sys.executable, "-X", "utf8", str(bundle / "vault_doctor.py"), "--vault", str(vault), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8",
                env=env_no_plugin, stdin=subprocess.DEVNULL, timeout=15,
            )
            self.assertEqual(doc_no_plugin.returncode, 2)
            self.assertEqual(json.loads(doc_no_plugin.stdout)["reason_code"], "session_hook_not_bundled")

            env_with_plugin = {**env_no_plugin, "CLAUDE_PLUGIN_ROOT": str(ROOT)}
            doc_with_plugin = subprocess.run(
                [sys.executable, "-X", "utf8", str(bundle / "vault_doctor.py"), "--vault", str(vault), "--format", "json"],
                capture_output=True, text=True, encoding="utf-8",
                env=env_with_plugin, stdin=subprocess.DEVNULL, timeout=15,
            )
            self.assertEqual(doc_with_plugin.returncode, 0, doc_with_plugin.stderr)
            self.assertIn(json.loads(doc_with_plugin.stdout)["status"], ("ready", "degraded"))


if __name__ == "__main__":
    unittest.main()
