from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"
SPEC = importlib.util.spec_from_file_location("agentic_vault_healthcheck", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery guard
    raise RuntimeError(f"cannot load healthcheck module: {MODULE_PATH}")
healthcheck = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = healthcheck
SPEC.loader.exec_module(healthcheck)

HealthcheckError = healthcheck.HealthcheckError
StagedChange = healthcheck.StagedChange
VaultPolicy = healthcheck.VaultPolicy
list_staged_changes = healthcheck.list_staged_changes
load_staged_config = healthcheck.load_staged_config
read_index_text = healthcheck.read_index_text
validate_config = healthcheck.validate_config


class ConfigAndGitIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "vault repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Healthcheck Test")
        self.git("config", "user.email", "healthcheck@example.invalid")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {result.stderr}")
        return result

    def write(self, relpath: str, text: str, *, encoding: str = "utf-8") -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
        return path

    def write_config(self, overrides: dict | None = None, *, bom: bool = False) -> Path:
        raw = self.base_config(**(overrides or {}))
        encoding = "utf-8-sig" if bom else "utf-8"
        return self.write(
            "00-meta/vault-config.json",
            json.dumps(raw, ensure_ascii=False),
            encoding=encoding,
        )

    @staticmethod
    def base_config(**overrides: object) -> dict:
        raw: dict[str, object] = {
            "vault_name": "테스트 볼트",
            "required_keys": ["title"],
            "enums": {"status": ["active", "draft"]},
            "frontmatter_max_lines": 16,
            "deny_zones": ["20-knowledge/_archive"],
            "exclude_dirs": [".git"],
            "index_note": "00-meta/index.md",
            "log_note": "00-meta/log.md",
            "hot_note": "00-meta/hot.md",
            "handoff_note": "",
            "ssot_note": "",
            "health_report": "00-meta/health-report.md",
        }
        raw.update(overrides)
        return raw

    @staticmethod
    def valid_note(title: str, body: str = "") -> str:
        return f'---\ntitle: "{title}"\nstatus: active\n---\n\n{body}\n'

    def commit_all(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def test_load_staged_config_accepts_utf8_bom(self) -> None:
        self.write_config({"required_keys": ["제목"]}, bom=True)
        self.git("add", "00-meta/vault-config.json")

        config = load_staged_config(self.repo)

        self.assertEqual(config["required_keys"], ["제목"])

    def test_staged_config_wins_over_worktree(self) -> None:
        self.write_config({"required_keys": ["title"]})
        self.git("add", "00-meta/vault-config.json")
        self.write_config({"required_keys": []})

        self.assertEqual(load_staged_config(self.repo)["required_keys"], ["title"])

    def test_read_index_text_wins_over_dirty_worktree_for_space_and_korean_path(self) -> None:
        self.write_config()
        self.write("20-knowledge/새 노트.md", "staged 내용\n")
        self.git("add", "00-meta/vault-config.json", "20-knowledge/새 노트.md")
        self.write("20-knowledge/새 노트.md", "dirty 내용\n")

        self.assertEqual(read_index_text(self.repo, "20-knowledge/새 노트.md"), "staged 내용\n")

    def test_name_status_z_parses_actual_add_modify_rename_delete(self) -> None:
        self.write_config()
        self.write("20-knowledge/수정 노트.md", self.valid_note("수정 노트", "before"))
        self.write("20-knowledge/이전 노트.md", self.valid_note("이전 노트", "rename payload"))
        self.write("20-knowledge/삭제 노트.md", self.valid_note("삭제 노트", "delete payload"))
        self.commit_all("seed")

        self.write("20-knowledge/새 노트.md", self.valid_note("새 노트"))
        self.write("20-knowledge/수정 노트.md", self.valid_note("수정 노트", "after"))
        self.git("mv", "20-knowledge/이전 노트.md", "20-knowledge/변경 노트.md")
        self.git("rm", "-q", "20-knowledge/삭제 노트.md")
        self.git("add", "-A")

        changes = list_staged_changes(self.repo, load_staged_config(self.repo))

        self.assertIn(StagedChange("A", "20-knowledge/새 노트.md"), changes)
        self.assertIn(StagedChange("M", "20-knowledge/수정 노트.md"), changes)
        self.assertIn(
            StagedChange("R", "20-knowledge/변경 노트.md", "20-knowledge/이전 노트.md"),
            changes,
        )
        self.assertIn(StagedChange("D", "20-knowledge/삭제 노트.md"), changes)

    def test_name_status_z_parses_copy_record(self) -> None:
        output = b"C087\x0020-knowledge/old note.md\x0020-knowledge/new note.md\x00"
        completed = subprocess.CompletedProcess(["git"], 0, stdout=output, stderr=b"")

        with mock.patch.object(healthcheck.subprocess, "run", return_value=completed):
            changes = list_staged_changes(self.repo, validate_config(self.base_config()))

        self.assertEqual(
            changes,
            [StagedChange("C", "20-knowledge/new note.md", "20-knowledge/old note.md")],
        )

    def test_name_status_z_preserves_literal_backslash_in_git_path(self) -> None:
        output = b"A\x0020-knowledge/literal\\name.md\x00"
        completed = subprocess.CompletedProcess(["git"], 0, stdout=output, stderr=b"")

        with mock.patch.object(healthcheck.subprocess, "run", return_value=completed):
            changes = list_staged_changes(self.repo, validate_config(self.base_config()))

        self.assertEqual(changes, [StagedChange("A", r"20-knowledge/literal\name.md")])

    def test_unborn_head_lists_staged_additions(self) -> None:
        self.write_config()
        self.write("20-knowledge/첫 노트.md", self.valid_note("첫 노트"))
        self.git("add", "00-meta/vault-config.json", "20-knowledge/첫 노트.md")

        changes = list_staged_changes(self.repo, load_staged_config(self.repo))

        self.assertIn(StagedChange("A", "20-knowledge/첫 노트.md"), changes)

    def test_config_rejects_path_escape_and_ambiguous_segments(self) -> None:
        invalid_values = (
            "../outside.md",
            "nested/../outside.md",
            "C:/outside.md",
            "C:\\outside.md",
            "/outside.md",
            "//server/share/outside.md",
            "\\\\server\\share\\outside.md",
            "nested//note.md",
        )
        for key in ("handoff_note", "health_report", "rules_dir"):
            for value in invalid_values:
                with self.subTest(key=key, value=value), self.assertRaises(HealthcheckError):
                    validate_config(self.base_config(**{key: value}))

    def test_config_rejects_wrong_list_dict_and_integer_types(self) -> None:
        invalid = (
            {"required_keys": "title"},
            {"required_keys": ["title", 7]},
            {"enums": ["status"]},
            {"enums": {"status": "active"}},
            {"frontmatter_max_lines": "16"},
            {"frontmatter_max_lines": True},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(HealthcheckError):
                validate_config(self.base_config(**overrides))

    def test_legacy_exempt_alias_and_staged_policy_defaults(self) -> None:
        config = validate_config(self.base_config(fm_exempt_zones=["legacy/raw"]))
        policy = VaultPolicy.from_config(config)

        self.assertEqual(config["frontmatter_exempt_paths"], ["legacy/raw"])
        self.assertEqual(
            policy.frontmatter_roots,
            ("00-meta", "20-knowledge", "30-journal", "40-people", "50-projects"),
        )
        self.assertEqual(policy.frontmatter_exempt_paths, ("legacy/raw",))

    def test_legacy_config_preserves_absent_frontmatter_roots_marker(self) -> None:
        config = validate_config(self.base_config())

        self.assertNotIn("frontmatter_roots", config)
        self.assertEqual(
            VaultPolicy.from_config(config).frontmatter_roots,
            ("00-meta", "20-knowledge", "30-journal", "40-people", "50-projects"),
        )

    def test_git_nonzero_and_timeout_are_errors_not_empty_results(self) -> None:
        failed = subprocess.CompletedProcess(["git"], 128, stdout=b"", stderr=b"fatal: broken")
        with mock.patch.object(healthcheck.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(HealthcheckError, "git"):
                list_staged_changes(self.repo, validate_config(self.base_config()))

        timeout = subprocess.TimeoutExpired(["git"], timeout=30)
        with mock.patch.object(healthcheck.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(HealthcheckError, "git"):
                read_index_text(self.repo, "20-knowledge/note.md")

    def test_staged_config_deletion_is_error(self) -> None:
        self.write_config()
        self.commit_all("seed config")
        self.git("rm", "-q", "00-meta/vault-config.json")

        with self.assertRaisesRegex(HealthcheckError, "vault-config"):
            load_staged_config(self.repo)

    def test_explicit_cli_output_outside_vault_remains_allowed(self) -> None:
        self.write_config()
        outside = Path(self._tmp.name) / "outside" / "report.md"

        with mock.patch.object(
            sys,
            "argv",
            ["vault_healthcheck.py", "--vault", str(self.repo), "--output", str(outside)],
        ):
            exit_code = healthcheck.main()

        self.assertEqual(exit_code, 0)
        self.assertTrue(outside.is_file())


if __name__ == "__main__":
    unittest.main()
