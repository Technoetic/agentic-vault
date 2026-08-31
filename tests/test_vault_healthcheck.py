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
validate_staged = getattr(healthcheck, "validate_staged", None)


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

    def test_read_index_text_preserves_git_path_in_show_argument(self) -> None:
        completed = subprocess.CompletedProcess(
            ["git"], 0, stdout=b"staged content\n", stderr=b""
        )
        paths = (
            r"20-knowledge/literal\name.md",
            " leading directory/note with trailing space.md ",
        )

        for path in paths:
            with self.subTest(path=path), mock.patch.object(
                healthcheck.subprocess, "run", return_value=completed
            ) as run:
                self.assertEqual(read_index_text(self.repo, path), "staged content\n")
                command = run.call_args.args[0]
                self.assertEqual(command[-2:], ["show", f":{path}"])

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


class StagedGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "staged vault"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Staged Gate Test")
        self.git("config", "user.email", "staged-gate@example.invalid")

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

    def write(self, relpath: str, text: str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def base_config(**overrides: object) -> dict:
        raw: dict[str, object] = {
            "vault_name": "스테이징 테스트 볼트",
            "required_keys": ["title"],
            "enums": {"status": ["active", "draft"]},
            "frontmatter_max_lines": 16,
            "deny_zones": ["20-knowledge/_archive"],
            "exclude_dirs": ["node_modules", ".git"],
            "index_note": "00-meta/index.md",
            "log_note": "00-meta/log.md",
            "hot_note": "00-meta/hot.md",
            "handoff_note": "",
            "ssot_note": "",
            "health_report": "00-meta/health-report.md",
        }
        raw.update(overrides)
        return raw

    def write_config(self, overrides: dict | None = None) -> Path:
        raw = self.base_config(**(overrides or {}))
        return self.write(
            "00-meta/vault-config.json",
            json.dumps(raw, ensure_ascii=False),
        )

    @staticmethod
    def valid_note(title: str, body: str = "") -> str:
        return f'---\ntitle: "{title}"\nstatus: active\n---\n\n{body}\n'

    def commit_all(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def seed_notes(
        self,
        notes: dict[str, str],
        config_overrides: dict | None = None,
    ) -> None:
        self.write_config(config_overrides)
        for relpath, content in notes.items():
            self.write(relpath, content)
        self.commit_all("seed staged gate")

    def staged_errors(self) -> list[str]:
        assert validate_staged is not None, "validate_staged is not implemented"
        return validate_staged(self.repo, load_staged_config(self.repo))

    def test_staged_content_wins_over_valid_dirty_worktree_content(self) -> None:
        self.seed_notes({"20-knowledge/Note.md": self.valid_note("Note")})
        self.write("20-knowledge/Note.md", "staged content without frontmatter\n")
        self.git("add", "20-knowledge/Note.md")
        self.write("20-knowledge/Note.md", self.valid_note("Note", "dirty repair"))

        errors = self.staged_errors()

        self.assertTrue(any("Note.md" in error and "프런트매터" in error for error in errors))

    def test_schema_rules_apply_only_to_configured_non_exempt_roots(self) -> None:
        self.seed_notes(
            {},
            {
                "frontmatter_roots": ["20-knowledge"],
                "frontmatter_exempt_paths": ["20-knowledge/exempt"],
            },
        )
        self.write("20-knowledge/missing.md", "no frontmatter\n")
        self.write("20-knowledge/keys.md", "---\nstatus: active\n---\n")
        self.write("20-knowledge/enum.md", '---\ntitle: "Enum"\nstatus: broken\n---\n')
        self.write(
            "20-knowledge/link.md",
            '---\ntitle: "Link"\nstatus: active\nparent: [[Target]]\n---\n',
        )
        self.write("30-journal/outside.md", "no frontmatter\n")
        self.write("20-knowledge/exempt/raw.md", "no frontmatter\n")
        self.git("add", "-A")

        errors = self.staged_errors()

        joined = "\n".join(errors)
        self.assertIn("missing.md", joined)
        self.assertIn("프런트매터 누락", joined)
        self.assertIn("keys.md", joined)
        self.assertIn("필수 키 누락", joined)
        self.assertIn("enum.md", joined)
        self.assertIn("Enum 위반", joined)
        self.assertIn("link.md", joined)
        self.assertIn("따옴표 없는 위키링크", joined)
        self.assertNotIn("outside.md", joined)
        self.assertNotIn("exempt/raw.md", joined)
        self.assertEqual(errors, sorted(set(errors)))

    def test_untouched_legacy_schema_violations_do_not_block(self) -> None:
        self.seed_notes({"20-knowledge/legacy.md": "legacy without frontmatter\n"})
        self.write("notes.txt", "non-Markdown staged change\n")
        self.git("add", "notes.txt")

        self.assertEqual(self.staged_errors(), [])

    def test_renamed_new_content_is_schema_checked(self) -> None:
        body = "\n".join(f"stable line {number}" for number in range(20))
        self.seed_notes({"20-knowledge/A.md": self.valid_note("A", body)})
        self.git("mv", "20-knowledge/A.md", "20-knowledge/B.md")
        self.write("20-knowledge/B.md", f"---\nstatus: active\n---\n\n{body}\n")
        self.git("add", "20-knowledge/B.md")

        errors = self.staged_errors()

        self.assertTrue(any("B.md" in error and "필수 키 누락" in error for error in errors))

    def test_copied_new_content_is_checked_without_deleting_old_stem(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "20-knowledge/Ref.md": self.valid_note("Ref", "[[A]]"),
            }
        )
        self.write("20-knowledge/B.md", "---\nstatus: active\n---\n")
        self.git("add", "20-knowledge/B.md")
        copied = StagedChange("C", "20-knowledge/B.md", "20-knowledge/A.md")

        with mock.patch.object(healthcheck, "list_staged_changes", return_value=[copied]):
            errors = self.staged_errors()

        self.assertTrue(any("B.md" in error and "필수 키 누락" in error for error in errors))
        self.assertFalse(any("Ref.md" in error and "A" in error for error in errors))

    def test_directory_only_rename_with_same_stem_does_not_check_backlinks(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/old/A.md": self.valid_note("A"),
                "20-knowledge/Ref.md": self.valid_note("Ref", "[[A]]"),
            }
        )
        self.write("20-knowledge/new/.keep", "")
        self.git("mv", "20-knowledge/old/A.md", "20-knowledge/new/A.md")

        self.assertEqual(self.staged_errors(), [])

    def test_renamed_old_stem_backlinks_block_all_supported_link_forms(self) -> None:
        forms = {
            "Plain.md": "[[A]]",
            "Alias.md": "[[A|표시 이름]]",
            "Anchor.md": "[[A#절]]",
            "Folder.md": "[[folder/A]]",
        }
        notes = {"20-knowledge/A.md": self.valid_note("A")}
        notes.update(
            {f"20-knowledge/{name}": self.valid_note(name[:-3], link)
             for name, link in forms.items()}
        )
        self.seed_notes(notes)
        self.git("mv", "20-knowledge/A.md", "20-knowledge/B.md")

        errors = self.staged_errors()
        joined = "\n".join(errors)

        for referrer in forms:
            with self.subTest(referrer=referrer):
                self.assertIn(referrer, joined)
        self.assertTrue(all("A" in error for error in errors))

    def test_regex_metacharacters_and_korean_stems_are_matched_literally(self) -> None:
        stems = ("A+B(1)", "한글+[테스트]")
        notes: dict[str, str] = {}
        for index, stem in enumerate(stems):
            notes[f"20-knowledge/{stem}.md"] = self.valid_note(stem)
            notes[f"20-knowledge/Ref{index}.md"] = self.valid_note(
                f"Ref{index}", f"[[folder/{stem}|별칭]]"
            )
        self.seed_notes(notes)
        for index, stem in enumerate(stems):
            self.git("mv", f"20-knowledge/{stem}.md", f"20-knowledge/New{index}.md")

        joined = "\n".join(self.staged_errors())

        for index, stem in enumerate(stems):
            with self.subTest(stem=stem):
                self.assertIn(f"Ref{index}.md", joined)
                self.assertIn(stem, joined)

    def test_rename_backlink_repaired_in_same_commit_passes(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "20-knowledge/Ref.md": self.valid_note("Ref", "[[A]]"),
            }
        )
        self.git("mv", "20-knowledge/A.md", "20-knowledge/B.md")
        self.write("20-knowledge/Ref.md", self.valid_note("Ref", "[[B]]"))
        self.git("add", "20-knowledge/Ref.md")

        self.assertEqual(self.staged_errors(), [])

    def test_deleted_note_with_result_index_backlink_is_blocked(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "20-knowledge/Ref.md": self.valid_note("Ref", "[[A]]"),
            }
        )
        self.git("rm", "20-knowledge/A.md")

        errors = self.staged_errors()

        self.assertTrue(any("Ref.md" in error and "A" in error for error in errors))

    def test_another_result_index_note_with_same_stem_keeps_links_valid(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/one/A.md": self.valid_note("A one"),
                "30-journal/two/A.md": self.valid_note("A two"),
                "20-knowledge/Ref.md": self.valid_note("Ref", "[[A]]"),
            }
        )
        self.git("rm", "20-knowledge/one/A.md")

        self.assertEqual(self.staged_errors(), [])

    def test_log_and_health_report_referrers_are_excluded(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "00-meta/log.md": self.valid_note("Log", "[[A]]"),
                "00-meta/health-report.md": self.valid_note("Health", "[[A]]"),
            }
        )
        self.git("rm", "20-knowledge/A.md")

        self.assertEqual(self.staged_errors(), [])

    def test_empty_health_report_excludes_effective_default_referrer(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "00-meta/health-report.md": self.valid_note("Health", "[[A]]"),
            },
            {"health_report": ""},
        )
        self.git("rm", "20-knowledge/A.md")

        self.assertEqual(self.staged_errors(), [])

    def test_deny_and_exclude_zones_are_pruned_from_git_queries_and_reads(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "20-knowledge/_archive/비밀.md": self.valid_note("비밀", "[[A]]"),
                "nested/node_modules/Hidden.md": self.valid_note("Hidden", "[[A]]"),
            }
        )
        self.git("rm", "20-knowledge/A.md")
        self.write("20-knowledge/_archive/비밀.md", "invalid staged secret [[A]]\n")
        self.write("nested/node_modules/Hidden.md", "invalid staged excluded [[A]]\n")
        self.git("add", "-A")
        index_paths = healthcheck._index_markdown_paths(
            self.repo, load_staged_config(self.repo)
        )
        self.assertFalse(
            any("_archive" in path or "node_modules" in path for path in index_paths)
        )
        calls: list[tuple[str, ...]] = []
        read_paths: list[str] = []
        real_run = healthcheck._run_git_bytes
        real_read = healthcheck.read_index_text

        def record_git(vault: Path, *args: str, **kwargs: object) -> bytes:
            calls.append(args)
            return real_run(vault, *args, **kwargs)

        def record_read(vault: Path, path: str) -> str:
            read_paths.append(path)
            return real_read(vault, path)

        with mock.patch.object(healthcheck, "_run_git_bytes", side_effect=record_git), \
             mock.patch.object(healthcheck, "read_index_text", side_effect=record_read):
            errors = self.staged_errors()

        self.assertEqual(errors, [])
        flattened = [arg for call in calls for arg in call]
        self.assertIn(":(top,glob)**/*.md", flattened)
        self.assertIn(":(exclude,top,glob)20-knowledge/_archive/**", flattened)
        self.assertIn(":(exclude,glob)**/node_modules/**", flattened)
        self.assertFalse(any("_archive" in path or "node_modules" in path for path in read_paths))

    def test_non_markdown_commit_still_rejects_invalid_staged_config(self) -> None:
        self.seed_notes({})
        self.write("notes.txt", "ordinary change\n")
        self.write("00-meta/vault-config.json", "{not valid json")
        self.git("add", "notes.txt", "00-meta/vault-config.json")

        with self.assertRaises(HealthcheckError):
            load_staged_config(self.repo)

    def test_validate_staged_writes_no_health_report(self) -> None:
        self.seed_notes({})
        self.write("20-knowledge/New.md", self.valid_note("New"))
        self.git("add", "20-knowledge/New.md")

        self.assertEqual(self.staged_errors(), [])
        self.assertFalse((self.repo / "00-meta/health-report.md").exists())

    def test_git_grep_failure_is_fail_closed(self) -> None:
        self.seed_notes(
            {
                "20-knowledge/A.md": self.valid_note("A"),
                "20-knowledge/Ref.md": self.valid_note("Ref", "[[A]]"),
            }
        )
        self.git("rm", "20-knowledge/A.md")
        real_run = healthcheck._run_git_bytes

        def fail_grep(vault: Path, *args: str, **kwargs: object) -> bytes:
            if args and args[0] == "grep":
                raise HealthcheckError("git grep failed (128)")
            return real_run(vault, *args, **kwargs)

        with mock.patch.object(healthcheck, "_run_git_bytes", side_effect=fail_grep):
            with self.assertRaisesRegex(HealthcheckError, "grep"):
                self.staged_errors()


if __name__ == "__main__":
    unittest.main()
