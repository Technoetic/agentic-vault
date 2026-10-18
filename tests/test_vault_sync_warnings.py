"""Opt-in sync-client conflict/temporary file names and checkpoint drift warnings.

Both checks are warning-only and off by default. The golden fixture holds the
full-mode output of the unmodified v0.24.0 engine (d2b9315) for a vault that
already contains sync-style names and a drifted checkpoint, so the default path
is compared byte for byte (report text is compared after universal-newline
decoding because the engine writes it in text mode). Its section15 case adds a
link to a missing heading, so v0.24.0 already writes report section 15 there,
the section the opt-in warnings extend.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_healthcheck as healthcheck  # noqa: E402
import vault_state  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "v0240_healthcheck_sync_default.json"
RUNTIME = "00-meta/.agentic-vault/runtime"
SYNC_CODES = {"sync-conflict-copy", "sync-temp-file", "sync-user-pattern", "checkpoint-drift"}
OPT_IN = {"sync_conflict_check": True, "sync_conflict_patterns": ["*.conflict-copy.md"],
          "checkpoint_drift_check": True}


def load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def fixed_date(today: str) -> type:
    year, month, day = (int(part) for part in today.split("-"))

    class FixedDate(dt.date):
        @classmethod
        def today(cls):  # noqa: D102 - pinned clock for byte comparisons
            return cls(year, month, day)

    return FixedDate


def build_vault(root: Path, files: dict[str, str], config: dict) -> Path:
    vault = root.resolve() / "golden vault"
    for relative, text in {**files, "00-meta/vault-config.json":
                           json.dumps(config, ensure_ascii=False)}.items():
        target = vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return vault


def run_main(vault: Path, today: str, report_relative: str) -> dict:
    """Run full mode in-process with a pinned date, like the golden capture."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(healthcheck, "date", fixed_date(today)), \
            mock.patch.object(sys, "argv", ["vault_healthcheck.py", "--vault", str(vault)]), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = healthcheck.main()
    report_path = vault / report_relative
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else None
    return {"exit": code,
            "stdout": stdout.getvalue().replace(str(report_path), "<REPORT>"),
            "stderr": stderr.getvalue(), "report": report}


def path_name(label: str) -> str:
    """Runtime file name in the checkpoint-<32 hex>.json form vault_state uses."""
    return "checkpoint-" + hashlib.sha256(label.encode()).hexdigest()[:32] + ".json"


def section_15_codes(report: str) -> list[tuple[str, str]]:
    """Return (path, code) pairs of the sync/checkpoint lines in report section 15."""
    pairs = []
    for line in report.splitlines():
        if not line.startswith("- warning: "):
            continue
        location, _, rest = line[len("- warning: "):].partition(" [")
        code = rest.split("]", 1)[0]
        if code in SYNC_CODES or code == "lint-incomplete":
            pairs.append((location.rsplit(":", 1)[0], code))
    return pairs


def section_15_lines(report: str) -> list[str]:
    """Return the header and list lines of report section 15 ([] when it is absent)."""
    lines = report.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("## 15. ")]
    if not starts:
        return []
    section = [lines[starts[0]]]
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            section.append(line)
    return section


class DefaultOutputGoldenTests(unittest.TestCase):
    """Without the new keys the engine must print and write what v0.24.0 did."""

    def setUp(self) -> None:
        self.golden = load_golden()
        self.assertTrue(self.golden["captured_from"].startswith("agentic-vault v0.24.0 (d2b9315)"))

    def run_case(self, name: str, extra_config: dict | None = None) -> dict:
        case = self.golden["cases"][name]
        config = {**self.golden["config"], **(extra_config or {})}
        with tempfile.TemporaryDirectory() as temporary:
            vault = build_vault(Path(temporary), {**self.golden["files"], **case.get("files", {})},
                                config)
            return run_main(vault, self.golden["today"], self.golden["report_path"])

    def test_default_output_and_exit_code_match_v0240_golden(self) -> None:
        self.assertEqual(sorted(self.golden["cases"]), ["clean", "fatal", "section15"])
        for name, case in sorted(self.golden["cases"].items()):
            with self.subTest(case=name):
                self.assertEqual(self.run_case(name), case["expected"])

    def test_explicitly_disabled_keys_match_v0240_golden(self) -> None:
        disabled = {"sync_conflict_check": False, "sync_conflict_patterns": [],
                    "checkpoint_drift_check": False}
        for name, case in sorted(self.golden["cases"].items()):
            with self.subTest(case=name):
                self.assertEqual(self.run_case(name, disabled), case["expected"])

    def test_golden_vault_really_contains_sync_names_and_checkpoint_drift(self) -> None:
        # Guards the fixture itself: the opt-in run must find what the default ignores.
        result = self.run_case("clean", OPT_IN)
        codes = {code for _path, code in section_15_codes(result["report"])}
        self.assertEqual(codes, SYNC_CODES)
        self.assertEqual(result["exit"], self.golden["cases"]["clean"]["expected"]["exit"])

    def test_section_15_written_by_default_keeps_its_lines_when_opted_in(self) -> None:
        expected = self.golden["cases"]["section15"]["expected"]
        default_section = section_15_lines(expected["report"])
        # v0.24.0 already writes section 15 here: one missing-anchor warning, no sync code.
        self.assertEqual(default_section[0], "## 15. Markdown warning extensions (1)")
        self.assertEqual(len(default_section), 2)
        self.assertIn(" [missing-anchor] ", default_section[1])
        self.assertEqual(section_15_codes(expected["report"]), [])
        self.assertEqual(section_15_lines(self.golden["cases"]["clean"]["expected"]["report"]), [])

        result = self.run_case("section15", OPT_IN)
        opted_section = section_15_lines(result["report"])
        self.assertEqual(opted_section[1], default_section[1], "default lines come first, unchanged")
        codes = {code for _path, code in section_15_codes(result["report"])}
        self.assertEqual(codes, SYNC_CODES)
        self.assertEqual(opted_section[0],
                         f"## 15. Markdown warning extensions ({len(opted_section) - 1})")
        self.assertEqual(result["exit"], expected["exit"])
        self.assertIn("(치명 0건)", result["stdout"])

    def test_default_cli_subprocess_reports_no_sync_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = build_vault(Path(temporary), self.golden["files"], self.golden["config"])
            run = subprocess.run(
                [sys.executable, str(SCRIPTS / "vault_healthcheck.py"), "--vault", str(vault)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)
            report = (vault / self.golden["report_path"]).read_text(encoding="utf-8")
        expected = self.golden["cases"]["clean"]["expected"]
        self.assertEqual(run.returncode, expected["exit"])
        self.assertEqual(run.stderr, b"")
        self.assertNotIn("§15".encode("utf-8"), run.stdout)
        self.assertEqual(section_15_codes(report), [])


class SyncNameWarningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.golden = load_golden()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = {**self.golden["config"], "sync_conflict_check": True,
                       "sync_conflict_patterns": ["*.conflict-copy.md"]}
        self.vault = build_vault(Path(self._tmp.name), self.golden["files"], self.config)

    def main(self) -> dict:
        return run_main(self.vault, self.golden["today"], self.golden["report_path"])

    def test_conflict_copies_temporary_files_and_user_glob_are_reported(self) -> None:
        result = self.main()
        self.assertEqual(section_15_codes(result["report"]), [
            ("00-meta/vault-config.sync-conflict-20261018-101530-ABCDEFG.json", "sync-conflict-copy"),
            ("20-knowledge/.syncthing.Plan.md.tmp", "sync-temp-file"),
            ("20-knowledge/Plan (Conflicted copy MyLaptop 202610181015).md", "sync-conflict-copy"),
            ("20-knowledge/Plan.conflict-copy.md", "sync-user-pattern"),
            ("20-knowledge/Plan.sync-conflict-20261018-101530-ABCDEFG.md", "sync-conflict-copy"),
            ("20-knowledge/~syncthing~Plan.md.tmp", "sync-temp-file"),
        ])
        self.assertIn('"pattern": "obsidian-sync-conflict"', result["report"])
        self.assertIn(healthcheck.SYNCTHING_SYNCING_DOC, result["report"])
        self.assertIn(healthcheck.OBSIDIAN_SYNC_TROUBLESHOOT_DOC, result["report"])
        self.assertIn('"glob": "*.conflict-copy.md"', result["report"])
        # Warning-only: same exit code and critical count as the default golden.
        expected = self.golden["cases"]["clean"]["expected"]
        self.assertEqual(result["exit"], expected["exit"])
        self.assertIn("(치명 0건)", result["stdout"])
        self.assertIn("⚠️동기화 충돌·임시 파일 이름 6건(§15)", result["stdout"])
        self.assertEqual(result["stderr"], "")

    def test_deny_zone_exclude_dir_and_runtime_names_are_ignored(self) -> None:
        report = self.main()["report"]
        for ignored in ("20-knowledge/_archive/Old.sync-conflict", "node_modules/pkg.sync-conflict",
                        ".obsidian/workspace.sync-conflict", RUNTIME):
            with self.subTest(path=ignored):
                self.assertNotIn(ignored, report)

    def test_fatal_case_keeps_exit_code_and_adds_only_warnings(self) -> None:
        case = self.golden["cases"]["fatal"]
        with tempfile.TemporaryDirectory() as temporary:
            vault = build_vault(Path(temporary), {**self.golden["files"], **case["files"]},
                                self.config)
            result = run_main(vault, self.golden["today"], self.golden["report_path"])
        self.assertEqual(result["exit"], case["expected"]["exit"])
        self.assertIn("(치명 1건)", result["stdout"])
        self.assertEqual(len(section_15_codes(result["report"])), 6)

    def test_matched_entries_are_judged_by_name_only(self) -> None:
        # Ghost directory entries have no file behind them: any open, stat or read
        # would fail or skip them, so being reported proves a names-only check.
        ghosts = ("~syncthing~Ghost.md.tmp", "attachment.sync-conflict-20261018-101530-ABCDEFG.pdf")
        real_walk = os.walk

        def walk_with_ghosts(top, *args, **kwargs):
            for root, dirs, files in real_walk(top, *args, **kwargs):
                if Path(root) == self.vault / "20-knowledge":
                    files = [*files, *ghosts]
                yield root, dirs, files

        with mock.patch.object(healthcheck.os, "walk", side_effect=walk_with_ghosts):
            result = self.main()
        pairs = section_15_codes(result["report"])
        self.assertIn(("20-knowledge/~syncthing~Ghost.md.tmp", "sync-temp-file"), pairs)
        self.assertIn(("20-knowledge/attachment.sync-conflict-20261018-101530-ABCDEFG.pdf",
                       "sync-conflict-copy"), pairs)
        self.assertEqual(len(pairs), 8)
        self.assertIn("## 14. 읽기 실패 — 파일 단위 fail-soft — 관리성 (0)", result["report"])
        self.assertEqual(result["stderr"], "")

    def test_patterns_without_switch_only_add_a_config_warning(self) -> None:
        config = {**self.golden["config"], "sync_conflict_patterns": ["*.conflict-copy.md"]}
        with tempfile.TemporaryDirectory() as temporary:
            vault = build_vault(Path(temporary), self.golden["files"], config)
            result = run_main(vault, self.golden["today"], self.golden["report_path"])
        self.assertIn("## 0. 설정 경고 (1)", result["report"])
        self.assertIn("sync_conflict_patterns는 sync_conflict_check가 true일 때만 쓰인다",
                      result["report"])
        self.assertEqual(section_15_codes(result["report"]), [])
        self.assertEqual(result["exit"], self.golden["cases"]["clean"]["expected"]["exit"])

    def test_collect_md_files_default_is_unchanged_and_entries_are_names_only(self) -> None:
        exclude = {".git", "node_modules"}
        default = healthcheck.collect_md_files(self.vault, exclude)
        entries: list[Path] = []
        with_entries = healthcheck.collect_md_files(self.vault, exclude, file_entries=entries)
        self.assertEqual(default, with_entries)
        relative = {healthcheck.rel_posix(path, self.vault) for path in entries}
        self.assertIn("20-knowledge/~syncthing~Plan.md.tmp", relative)
        self.assertIn("00-meta/vault-config.json", relative)
        self.assertFalse([path for path in relative if path.startswith(("node_modules/", RUNTIME))])
        self.assertTrue({healthcheck.rel_posix(path, self.vault) for path in default} <= relative)

    def test_output_is_capped_with_lint_incomplete(self) -> None:
        entries = [self.vault / f"note {index:03d}.sync-conflict-20261018-101530-ABCDEFG.md"
                   for index in range(healthcheck.MAX_SYNC_NAME_WARNINGS + 5)]
        findings, count = healthcheck.sync_name_warnings(self.vault, entries,
                                                         healthcheck.validate_config(self.config))
        self.assertEqual(count, healthcheck.MAX_SYNC_NAME_WARNINGS + 5)
        self.assertEqual(len(findings), healthcheck.MAX_SYNC_NAME_WARNINGS + 1)
        self.assertEqual(findings[-1]["code"], "lint-incomplete")
        self.assertEqual(findings[-1]["details"]["omitted"], 5)
        self.assertTrue(all(finding["severity"] == "warning" for finding in findings))


class SyncNamePatternTests(unittest.TestCase):
    """Built-in names follow the documented forms only; user globs are exact."""

    def match(self, name: str, globs: list[str] | None = None):
        hit = healthcheck._sync_name_match(name, globs or [])
        return None if hit is None else (hit[0], hit[1]["pattern"])

    def test_documented_syncthing_and_obsidian_names_match(self) -> None:
        cases = {
            "note.sync-conflict-20261018-101530-ABCDEFG.md": ("sync-conflict-copy", "syncthing-conflict"),
            "photo.sync-conflict-20261018-101530-ABCDEFG.png": ("sync-conflict-copy", "syncthing-conflict"),
            ".sync-conflict-20261018-101530-ABCDEFG.gitignore": ("sync-conflict-copy", "syncthing-conflict"),
            ".syncthing.note.md.tmp": ("sync-temp-file", "syncthing-temp"),
            "~syncthing~note.md.tmp": ("sync-temp-file", "syncthing-temp"),
            ".syncthing.0f1e2d3c4b5a.tmp": ("sync-temp-file", "syncthing-temp"),
            # The example given verbatim by the Obsidian Sync help page.
            "Meeting notes (Conflicted copy MyMacBook2 202411281430).md":
                ("sync-conflict-copy", "obsidian-sync-conflict"),
            "회의록 (Conflicted copy 내 노트북 202610181015).md":
                ("sync-conflict-copy", "obsidian-sync-conflict"),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.match(name), expected)

    def test_near_misses_do_not_match(self) -> None:
        for name in ("sync-conflict notes.md", "my.sync-conflict-notes.md",
                     "plan.sync-conflict-20261018.md", ".syncthing.note.md", "syncthing.note.tmp",
                     "note.tmp", "~syncthing~note.md",
                     "Meeting notes (Conflicted copy).md",
                     "Meeting notes (conflicted copy MyMac 202411281430).md",
                     "Meeting notes (Conflicted copy MyMac 2024112814).md",
                     "Meeting notes (Conflicted copy MyMac 202411281430).canvas",
                     "Meeting notes (Conflicted copy MyMac 202411281430).md\n",
                     ".syncthing.note.md.tmp\n", "Plan (conflicted copy).md"):
            with self.subTest(name=name):
                self.assertIsNone(self.match(name))

    def test_user_globs_are_nfc_normalized_and_case_sensitive(self) -> None:
        decomposed = unicodedata.normalize("NFD", "계획 충돌본.md")
        self.assertNotEqual(decomposed, "계획 충돌본.md")
        globs = [unicodedata.normalize("NFC", "*충돌본.md")]
        self.assertEqual(self.match(unicodedata.normalize("NFC", decomposed), globs),
                         ("sync-user-pattern", "user"))
        self.assertIsNone(self.match("Plan.CONFLICT-COPY.md", ["*.conflict-copy.md"]))
        self.assertEqual(self.match("Plan.conflict-copy.md", ["*.conflict-copy.md"]),
                         ("sync-user-pattern", "user"))

    def test_scan_normalizes_decomposed_names_before_user_globs(self) -> None:
        config = healthcheck.validate_config({"sync_conflict_check": True,
                                              "sync_conflict_patterns": ["*충돌본.md"]})
        vault = Path(tempfile.gettempdir()).resolve() / "unused vault"
        entry = vault / unicodedata.normalize("NFD", "계획 충돌본.md")
        findings, count = healthcheck.sync_name_warnings(vault, [entry], config)
        self.assertEqual((count, findings[0]["code"]), (1, "sync-user-pattern"))


class SyncConfigValidationTests(unittest.TestCase):
    def test_absent_keys_leave_the_validated_mapping_unchanged(self) -> None:
        config = healthcheck.validate_config({})
        for key in ("sync_conflict_check", "sync_conflict_patterns", "checkpoint_drift_check"):
            with self.subTest(key=key):
                self.assertNotIn(key, config)
                self.assertNotIn(key, healthcheck.DEFAULT_CONFIG)

    def test_valid_settings_are_accepted(self) -> None:
        patterns = ["* (conflicted copy *)*", "*.tmp", "~$*"]
        config = healthcheck.validate_config({"sync_conflict_check": True,
                                              "sync_conflict_patterns": patterns,
                                              "checkpoint_drift_check": False})
        self.assertEqual(config["sync_conflict_patterns"], patterns)
        self.assertIsNot(config["sync_conflict_patterns"], patterns)
        many = [f"*.copy{index}" for index in range(healthcheck.MAX_SYNC_CONFLICT_PATTERNS)]
        self.assertEqual(healthcheck.validate_config({"sync_conflict_patterns": many})
                         ["sync_conflict_patterns"], many)
        longest = "*" + "a" * (healthcheck.MAX_SYNC_CONFLICT_PATTERN_CHARS - 1)
        healthcheck.validate_config({"sync_conflict_patterns": [longest]})

    def test_malformed_settings_are_rejected(self) -> None:
        cases = {
            "sync_conflict_check must be true/false": {"sync_conflict_check": "yes"},
            "checkpoint_drift_check must be true/false": {"checkpoint_drift_check": 1},
            "sync_conflict_patterns must be a list": {"sync_conflict_patterns": "*.tmp"},
            "sync_conflict_patterns[0] must be a string": {"sync_conflict_patterns": [3]},
            "sync_conflict_patterns[1] must not be empty": {"sync_conflict_patterns": ["*.tmp", "  "]},
            "must be at most": {"sync_conflict_patterns": [
                "*" * (healthcheck.MAX_SYNC_CONFLICT_PATTERN_CHARS + 1)]},
            "must have at most": {"sync_conflict_patterns": [
                f"*.c{index}" for index in range(healthcheck.MAX_SYNC_CONFLICT_PATTERNS + 1)]},
            "contains a control character": {"sync_conflict_patterns": ["a\tb"]},
            "matches file names only": {"sync_conflict_patterns": ["20-knowledge/*.md"]},
        }
        for message, raw in cases.items():
            with self.subTest(message=message):
                with self.assertRaises(healthcheck.HealthcheckError) as raised:
                    healthcheck.validate_config(raw)
                self.assertIn(message, str(raised.exception))
        with self.assertRaises(healthcheck.HealthcheckError):
            healthcheck.validate_config({"sync_conflict_patterns": ["a\\b"]})

    def test_new_codes_cannot_be_named_in_warning_promotion_policy(self) -> None:
        for code in sorted(SYNC_CODES):
            for level in ("warning", "fatal"):
                with self.subTest(code=code, level=level):
                    with self.assertRaises(healthcheck.HealthcheckError):
                        healthcheck.validate_config({"warning_policy": {"levels": {code: level}}})

    def test_invalid_setting_fails_like_other_config_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = build_vault(Path(temporary), {"20-knowledge/A.md": '---\ntitle: "A"\n---\n'},
                                {"sync_conflict_check": "on"})
            result = run_main(vault, "2026-10-19", "00-meta/health-report.md")
        self.assertEqual(result["exit"], 1)
        self.assertIn("sync_conflict_check must be true/false", result["stderr"])
        self.assertIsNone(result["report"])


class CheckpointDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name).resolve() / "drift vault"
        self.config = {
            "vault_name": "Drift", "required_keys": ["title"], "enums": {},
            "deny_zones": ["20-knowledge/_archive", ".obsidian"], "exclude_dirs": [".git"],
            "frontmatter_roots": ["20-knowledge"], "index_note": "", "log_note": "00-meta/log.md",
            "log_tags": ["build"], "hot_note": "00-meta/hot.md",
            "handoff_note": "50-projects/P/handoff.md", "anchor_drift_threshold": 0,
            "ssot_note": "", "rules_dir": "", "health_report": "00-meta/health-report.md",
            "checkpoint_drift_check": True,
        }
        self.write("00-meta/vault-config.json", json.dumps(self.config))
        self.write("00-meta/hot.md", "# Hot\n\nstate one\n")
        self.write("00-meta/log.md", "- 2026-10-18 10:00 | tester | [build] entry\n")
        self.write("50-projects/P/handoff.md", "# Handoff\n\nnext step\n")
        self.write("20-knowledge/A.md", '---\ntitle: "A"\n---\n[[hot]] [[log]] [[handoff]]\n')

    def write(self, relative: str, text: str) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def main(self) -> dict:
        return run_main(self.vault, "2026-10-19", "00-meta/health-report.md")

    def drift(self) -> list[dict]:
        return healthcheck.checkpoint_drift_warnings(
            self.vault, healthcheck.validate_config(self.config))

    def record(self, name: str, at: str, sources: dict[str, bytes], *,
               event: str = "SessionEnd") -> Path:
        """Write a record shaped like vault_state.write_checkpoint output."""
        record = {
            "version": 1, "session_sha256": hashlib.sha256(name.encode()).hexdigest(),
            "config_sha256": "0" * 64,
            "sources": [{"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                        for path, data in sources.items()],
            "events": [{"event": event, "at": at}], "pending_references": [],
        }
        return self.write(f"{RUNTIME}/{path_name(name)}", json.dumps(record))

    def snapshot(self) -> dict[str, bytes]:
        return {path.relative_to(self.vault).as_posix(): path.read_bytes()
                for path in self.vault.rglob("*") if path.is_file()
                and path.name != "health-report.md"}

    def test_changed_and_missing_notes_are_reported_after_real_checkpoint(self) -> None:
        vault_state.write_checkpoint(self.vault, "SessionEnd", session_id="session-one")
        self.write("00-meta/hot.md", "# Hot\n\nedited in another editor\n")
        (self.vault / "50-projects/P/handoff.md").unlink()

        findings = self.drift()

        self.assertEqual([(f["path"], f["code"], f["details"]["state"]) for f in findings], [
            ("00-meta/hot.md", "checkpoint-drift", "changed"),
            ("50-projects/P/handoff.md", "checkpoint-drift", "missing"),
        ])
        hot = findings[0]["details"]
        self.assertEqual(hot["event"], "SessionEnd")
        self.assertEqual(hot["current_sha256"], hashlib.sha256(
            (self.vault / "00-meta/hot.md").read_bytes()).hexdigest())
        self.assertIsNone(findings[1]["details"]["current_sha256"])
        self.assertTrue(all(f["severity"] == "warning" for f in findings))

        result = self.main()
        self.assertEqual(result["exit"], 0)
        self.assertIn("⚠️checkpoint 이후 바뀐 엔진 노트 2건(§15)", result["stdout"])
        self.assertEqual(section_15_codes(result["report"]), [
            ("00-meta/hot.md", "checkpoint-drift"), ("50-projects/P/handoff.md", "checkpoint-drift")])

    def test_unchanged_notes_produce_no_warning_and_identical_output(self) -> None:
        vault_state.write_checkpoint(self.vault, "PreCompact", session_id="session-one")
        self.assertEqual(self.drift(), [])
        enabled = self.main()
        self.write("00-meta/vault-config.json",
                   json.dumps({**self.config, "checkpoint_drift_check": False}))
        disabled = self.main()
        self.assertEqual(enabled, disabled)

    def test_no_runtime_or_no_checkpoint_is_silent(self) -> None:
        self.assertEqual(self.drift(), [])
        (self.vault / RUNTIME).mkdir(parents=True)
        self.write(f"{RUNTIME}/lock-{'a' * 64}.json", "{}")
        self.assertEqual(self.drift(), [])
        result = self.main()
        self.assertEqual((result["exit"], result["stderr"]), (0, ""))
        self.assertNotIn("checkpoint-drift", result["report"])

    def test_invalid_checkpoints_are_silent(self) -> None:
        # Sanity: this record is valid and its stale hot hash would be reported.
        valid_path = self.record("valid", "2026-10-18T09:00:00+00:00",
                                 {"00-meta/hot.md": b"older bytes\n"})
        self.assertEqual([finding["path"] for finding in self.drift()], ["00-meta/hot.md"])
        valid = json.loads(valid_path.read_text(encoding="utf-8"))
        valid_path.unlink()
        source = valid["sources"][0]
        invalid = {
            "version": {**valid, "version": 2},
            "bool version": {**valid, "version": True},
            "extra key": {**valid, "extra": 1},
            "missing key": {key: value for key, value in valid.items() if key != "config_sha256"},
            "event name": {**valid, "events": [{"event": "SessionStart",
                                                "at": "2026-10-18T09:00:00+00:00"}]},
            "event type": {**valid, "events": [{"event": [], "at": "2026-10-18T09:00:00+00:00"}]},
            "naive time": {**valid, "events": [{"event": "SessionEnd", "at": "2026-10-18T09:00:00"}]},
            "other offset": {**valid, "events": [{"event": "SessionEnd",
                                                  "at": "2026-10-18T09:00:00+09:00"}]},
            "no events": {**valid, "events": []},
            "bad hash": {**valid, "sources": [{**source, "sha256": "X" * 64}]},
            "bool size": {**valid, "sources": [{**source, "size": True}]},
            "traversal": {**valid, "sources": [{**source, "path": "../outside.md"}]},
            "absolute": {**valid, "sources": [{**source, "path": "/etc/hot.md"}]},
            "backslash": {**valid, "sources": [{**source, "path": "00-meta\\hot.md"}]},
            "not markdown": {**valid, "sources": [{**source, "path": "00-meta/vault-config.json"}]},
            "duplicate source": {**valid, "sources": [source, {**source, "path": "00-meta/HOT.md"}]},
        }
        for label, record in invalid.items():
            with self.subTest(label=label):
                target = self.write(f"{RUNTIME}/{path_name(label)}", json.dumps(record))
                self.assertEqual(self.drift(), [])
                target.unlink()
        raw_cases = {"truncated": b'{"version": 1', "duplicate key": b'{"version":1,"version":1}',
                     "not utf-8": b"\xff\xfe{}", "array": b"[]"}
        for label, raw in raw_cases.items():
            with self.subTest(label=label):
                target = self.vault / RUNTIME / path_name(label)
                target.write_bytes(raw)
                self.assertEqual(self.drift(), [])
                target.unlink()
        oversized = {**valid, "pending_references": ["x" * (healthcheck.MAX_CHECKPOINT_BYTES + 1)]}
        self.write(f"{RUNTIME}/{path_name('oversized')}", json.dumps(oversized))
        self.assertEqual(self.drift(), [])
        result = self.main()
        self.assertEqual((result["exit"], result["stderr"]), (0, ""))
        self.assertNotIn("checkpoint-drift", result["report"])

    def test_newest_valid_checkpoint_wins(self) -> None:
        current = {path: (self.vault / path).read_bytes()
                   for path in ("00-meta/hot.md", "00-meta/log.md")}
        older = {"00-meta/hot.md": b"older bytes\n", "00-meta/log.md": current["00-meta/log.md"]}
        self.record("older", "2026-10-18T08:00:00+00:00", older)
        self.record("newer", "2026-10-18T09:00:00+00:00", current)
        self.assertEqual(self.drift(), [])
        self.record("newest", "2026-10-18T10:00:00+00:00", older, event="PreCompact")
        findings = self.drift()
        self.assertEqual([f["path"] for f in findings], ["00-meta/hot.md"])
        self.assertEqual((findings[0]["details"]["event"], findings[0]["details"]["at"]),
                         ("PreCompact", "2026-10-18T10:00:00+00:00"))
        self.write(f"{RUNTIME}/{path_name('corrupt newest')}", "not json")
        self.assertEqual([f["path"] for f in self.drift()], ["00-meta/hot.md"])

    def test_denied_excluded_and_runtime_sources_are_skipped(self) -> None:
        # An existing changed file and absent files: reading would report
        # "changed", existence checks would report "missing". Neither may happen.
        self.write("20-knowledge/_archive/old.md", "archived\n")
        self.write(".git/notes.md", "excluded directory\n")
        self.record("denied", "2026-10-18T09:00:00+00:00", {
            "20-knowledge/_archive/old.md": b"different\n",
            ".obsidian/notes.md": b"different\n",
            ".git/notes.md": b"different\n",
            f"{RUNTIME}/note.md": b"different\n",
        })
        self.assertEqual(self.drift(), [])

    def test_check_never_writes_notes_or_runtime(self) -> None:
        vault_state.write_checkpoint(self.vault, "SessionEnd", session_id="session-one")
        self.write("00-meta/hot.md", "# Hot\n\nchanged\n")
        before = self.snapshot()
        result = self.main()
        self.assertIn("checkpoint-drift", result["report"])
        self.assertEqual(self.snapshot(), before)

    def test_file_swapped_after_lstat_is_not_read(self) -> None:
        path = self.vault / "00-meta/hot.md"
        real_fstat = os.fstat

        def other_identity(descriptor):
            info = real_fstat(descriptor)
            return os.stat_result((info.st_mode, info.st_ino + 1, *tuple(info)[2:]))

        with mock.patch.object(healthcheck.os, "fstat", side_effect=other_identity):
            self.assertIsNone(healthcheck._read_regular_bytes(path, 1024))
        self.assertEqual(healthcheck._read_regular_bytes(path, 1024), path.read_bytes())

    def test_large_current_note_is_reported_without_hash(self) -> None:
        self.record("large", "2026-10-18T09:00:00+00:00",
                    {"00-meta/hot.md": (self.vault / "00-meta/hot.md").read_bytes()})
        self.write("00-meta/hot.md", "x" * (healthcheck.MAX_CHECKPOINT_SOURCE_BYTES + 1))
        findings = self.drift()
        self.assertEqual([(f["path"], f["details"]["state"], f["details"]["current_sha256"])
                          for f in findings], [("00-meta/hot.md", "changed", None)])


class StagedModeTests(unittest.TestCase):
    def test_staged_gate_accepts_new_keys_and_never_scans_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary).resolve() / "staged vault"
            vault.mkdir()

            def git(*args: str) -> None:
                subprocess.run(["git", *args], cwd=vault, check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=30)

            git("init", "-q")
            config = {"required_keys": ["title"], "enums": {}, "frontmatter_roots": ["20-knowledge"],
                      **OPT_IN}
            (vault / "00-meta").mkdir()
            (vault / "00-meta/vault-config.json").write_text(json.dumps(config), encoding="utf-8")
            (vault / "20-knowledge").mkdir()
            (vault / "20-knowledge/A.md").write_text('---\ntitle: "A"\n---\n', encoding="utf-8")
            (vault / "20-knowledge/A.sync-conflict-20261018-101530-ABCDEFG.md").write_text(
                '---\ntitle: "A"\n---\n', encoding="utf-8")
            (vault / "20-knowledge/.syncthing.A.md.tmp").write_text("partial", encoding="utf-8")
            git("add", "00-meta/vault-config.json", "20-knowledge/A.md")
            run = subprocess.run([sys.executable, str(SCRIPTS / "vault_healthcheck.py"),
                                  "--vault", str(vault), "--staged"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                                 check=False)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
        for code in SYNC_CODES:
            with self.subTest(code=code):
                self.assertNotIn(code.encode(), run.stderr + run.stdout)


if __name__ == "__main__":
    unittest.main()
