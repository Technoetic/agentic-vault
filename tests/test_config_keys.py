"""Unknown top-level configuration keys are named instead of ignored silently.

A typo such as `deny_zone` used to leave the folder unprotected without any
sign. The health check now knows the engine's top-level keys
(`KNOWN_CONFIG_KEYS`). The full check names each unknown key in section 0 of
its report and on its console line, with the closest known key, and Doctor
lists them in `config_warnings`. Values are never shown, a name is displayed
with its control, format, separator and surrogate characters escaped, and exit
codes do not change. `validate_config`, the staged commit gate and the session
hook stay silent (the staged and session tests are contract tests: they pass
before and after the change).

Temporary roots are resolved, so a path such as `/var/...` on macOS is the
same `/private/var/...` the scripts see; Git runs with the user's settings cut
off (`isolate_git`). Escaped text is built with `chr(92)` and `chr(0x...)`, so
the source shows which characters it means.
"""
from __future__ import annotations

import ast
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"
DOCTOR = SCRIPTS / "vault_doctor.py"
SESSION_HOOK = ROOT / "hooks" / "session_start.py"
TEMPLATE_CONFIG = ROOT / "assets" / "templates" / "vault-config.json"
CONFIG = "00-meta/vault-config.json"
REPORT = "00-meta/health-report.md"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
finally:
    sys.path.remove(str(SCRIPTS))

BS = chr(92)
MARKER = "SECRET_VALUE_MARKER"
WARNING_WORDS = "알 수 없는 설정 키"
HOT_TEXT = "Ship the verified change."
CLEAN_CONFIG = {"deny_zones": ["90-assets"], "hot_note": "00-meta/hot.md", "handoff_note": ""}
HOT_NOTE = ("---\ntitle: hot\ntype: reference\nstatus: active\nai_priority: low\n"
            "tags: [hot]\ncreated: 2026-01-02\nupdated: 2026-01-02\n---\n" + HOT_TEXT + "\n")

DEFAULT_KEYS = {
    "vault_name", "language", "deny_zones", "exclude_dirs", "required_keys", "enums",
    "frontmatter_max_lines", "index_note", "log_note", "log_tags", "log_tag_epoch",
    "hot_note", "handoff_note", "ssot_note", "ssot_facts", "health_report", "backup_target",
    "stale_days", "index_scopes", "rules_dir", "rules_max_lines", "gates", "hot_max_tokens",
    "handoff_max_tokens", "anchor_drift_threshold",
}
OPTIONAL_KEYS = {
    "frontmatter_roots", "frontmatter_exempt_paths", "fm_exempt_zones", "warning_policy",
    "sync_conflict_check", "checkpoint_drift_check", "sync_conflict_patterns",
    "staged_growth_check", "staged_growth_max_tokens", "jarvis", "recall",
    "memory_revocation_file", "privileged_notes", "handoff_fold", "tasks_note",
    "constraints_note", "lint_max_warnings", "promotion_review_sessions",
}
SUGGESTIONS = (
    ("deny_zone", "deny_zones"),
    ("DENY_ZONES", "deny_zones"),
    ("deny-zones", "deny_zones"),
    ("denyzones", "deny_zones"),
    ("frontmatter_max_line", "frontmatter_max_lines"),
    ("hot_notes", "hot_note"),
    ("exclude_dir", "exclude_dirs"),
    ("gate", "gates"),
    ("recal", "recall"),
    ("anchor_threshold", "anchor_drift_threshold"),
    ("log_tag", "log_tags"),
    ("random_key", None),
    ("$schema", None),
    ("_comment", None),
    ("note_roots", None),
    ("handoff", None),
)
TYPO_LINE = '- 알 수 없는 설정 키 "deny_zone" — 적용되지 않는다("deny_zones"의 오타일 수 있음).'
KEY_LINE = re.compile(
    r'^- 알 수 없는 설정 키 "[^"]*" — 적용되지 않는다(\("[a-z_]+"의 오타일 수 있음\))?\.$')
# Keys whose characters must never reach the report raw: a lone surrogate, a line
# separator, a bidirectional override and NEXT LINE, each with its escaped display.
SPECIAL_KEYS = (
    ("a" + chr(0xDC80), f'"a{BS}udc80"'),
    ("b" + chr(0x2028) + "c", f'"b{BS}u2028c"'),
    ("d" + chr(0x202E) + "e", f'"d{BS}u202ee"'),
    ("f" + chr(0x85) + "g", f'"f{BS}u0085g"'),
)
RAW_SPECIAL = (chr(0xDC80), chr(0x2028), chr(0x202E), chr(0x85))


def run(arguments: list[str], cwd: Path, env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", *arguments], cwd=cwd, env=env,
        input='{"source":"startup"}', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", check=False, timeout=60,
    )


def without_claude(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("CLAUDE_")}
    env.update(extra)
    return env


def load_doctor():
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("doctor_config_keys_under_test", DOCTOR)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            raise RuntimeError(f"cannot load module: {DOCTOR}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path


def section_zero(report: str) -> list[str]:
    """The lines of section 0, header included, up to the blank line that ends it."""
    lines = report.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## 0. ")), None)
    if start is None:
        return []
    end = lines.index("", start)
    return lines[start:end]


class KnownKeyTests(unittest.TestCase):
    def test_known_keys_are_the_43_engine_keys(self) -> None:
        self.assertEqual(len(DEFAULT_KEYS | OPTIONAL_KEYS), 43)
        self.assertIsInstance(vault_healthcheck.KNOWN_CONFIG_KEYS, frozenset)
        self.assertEqual(vault_healthcheck.KNOWN_CONFIG_KEYS, DEFAULT_KEYS | OPTIONAL_KEYS)
        self.assertEqual(set(vault_healthcheck.DEFAULT_CONFIG), DEFAULT_KEYS)

    def test_template_and_default_keys_are_known(self) -> None:
        template = json.loads(TEMPLATE_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(vault_healthcheck.unknown_config_keys(template), [])
        self.assertEqual(vault_healthcheck.unknown_config_keys(vault_healthcheck.DEFAULT_CONFIG), [])

    def test_every_known_key_is_read_by_shipped_code(self) -> None:
        """Each known key outside DEFAULT_CONFIG is read somewhere, so no stale name stays listed.

        The two statements that list the names are left out of the search, so a
        name that only they mention fails here.
        """
        source_tree = ast.parse(HEALTHCHECK.read_text(encoding="utf-8"))
        listing_lines: set[int] = set()
        listing_names: set[str] = set()
        for node in source_tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"DEFAULT_CONFIG",
                                                                  "KNOWN_CONFIG_KEYS"}:
                    listing_names.add(target.id)
                    listing_lines.update(range(node.lineno, node.end_lineno + 1))
        self.assertEqual(listing_names, {"DEFAULT_CONFIG", "KNOWN_CONFIG_KEYS"})

        files = (sorted(SCRIPTS.rglob("*.py")) + sorted((ROOT / "hooks").rglob("*.py"))
                 + sorted((ROOT / "assets" / "adapters").rglob("*.py")))
        constants: set[str] = set()
        for path in files:
            tree = (source_tree if path == HEALTHCHECK
                    else ast.parse(path.read_text(encoding="utf-8")))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                if path == HEALTHCHECK and node.lineno in listing_lines:
                    continue
                constants.add(node.value)
        commands = "\n".join(path.read_text(encoding="utf-8")
                             for path in sorted((ROOT / "commands").glob("*.md")))

        optional = sorted(vault_healthcheck.KNOWN_CONFIG_KEYS
                          - set(vault_healthcheck.DEFAULT_CONFIG))
        self.assertEqual(len(optional), 18)
        for name in optional:
            with self.subTest(name=name):
                self.assertTrue(name in constants or f"`{name}`" in commands,
                                f"{name} is not read by shipped code or named in commands")


class SuggestionTests(unittest.TestCase):
    def test_suggestions(self) -> None:
        for name, expected in SUGGESTIONS:
            with self.subTest(name=name):
                self.assertEqual(vault_healthcheck.unknown_config_keys({name: 1}),
                                 [(name, expected)])
        together = {name: index for index, (name, _expected) in enumerate(SUGGESTIONS)}
        together.update({"deny_zones": [], "vault_name": "V"})
        self.assertEqual(vault_healthcheck.unknown_config_keys(together),
                         sorted(SUGGESTIONS, key=lambda row: row[0]))
        for raw in ([], None, "deny_zone", 3):
            with self.subTest(raw=raw):
                self.assertEqual(vault_healthcheck.unknown_config_keys(raw), [])

    def test_validate_config_is_unchanged_by_unknown_keys(self) -> None:
        raw = {"vault_name": "V", "deny_zone": [MARKER], "hot_notes": "00-meta/hot.md",
               "recal": {"max_total_read_bytes": 1}, "deny_zones": ["90-assets"]}
        snapshot = deepcopy(raw)
        before = vault_healthcheck.validate_config(raw)
        found = vault_healthcheck.unknown_config_keys(raw)
        self.assertEqual(found, [("deny_zone", "deny_zones"), ("hot_notes", "hot_note"),
                                 ("recal", "recall")])
        self.assertEqual(raw, snapshot)
        self.assertEqual(vault_healthcheck.validate_config(raw), before)


class DisplayTests(unittest.TestCase):
    def test_display_escapes_and_cuts(self) -> None:
        display = vault_healthcheck.config_key_display
        self.assertEqual(display("x" * 80), '"' + "x" * 80 + '"')
        self.assertEqual(display("x" * 81), '"' + "x" * 80 + chr(0x2026) + '"')
        self.assertEqual(display("제외"), '"제외"')
        self.assertEqual(display('a"b' + BS + "c"), '"a' + BS + '"b' + BS + BS + 'c"')
        self.assertEqual(display("a\nb"), f'"a{BS}u000ab"')
        self.assertEqual(display("t" + chr(0xE0001)), f'"t{BS}U000e0001"')
        for name, expected in SPECIAL_KEYS:
            with self.subTest(name=ascii(name)):
                self.assertEqual(display(name), expected)


class _VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def make_vault(self, name: str, extra: dict | None = None, *, ascii_only: bool = False) -> Path:
        vault = self.root / name
        (vault / "00-meta").mkdir(parents=True)
        config = {**CLEAN_CONFIG, **(extra or {})}
        (vault / CONFIG).write_text(json.dumps(config, ensure_ascii=ascii_only),
                                    encoding="utf-8")
        (vault / "00-meta" / "hot.md").write_text(HOT_NOTE, encoding="utf-8")
        return vault

    def healthcheck(self, vault: Path) -> tuple[subprocess.CompletedProcess[str], str]:
        result = run([str(HEALTHCHECK), "--vault", str(vault)], vault, without_claude())
        report_path = vault / REPORT
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        return result, report


class FullCheckTests(_VaultCase):
    def test_cli_reports_unknown_keys_without_changing_the_exit_code(self) -> None:
        clean_result, clean_report = self.healthcheck(self.make_vault("clean"))
        self.assertEqual(clean_result.returncode, 0, clean_result.stdout + clean_result.stderr)
        self.assertEqual(section_zero(clean_report), [])
        self.assertNotIn(WARNING_WORDS, clean_report + clean_result.stdout + clean_result.stderr)

        result, report = self.healthcheck(self.make_vault("typo", {"deny_zone": [MARKER]}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(section_zero(report), ["## 0. 설정 경고 (1)", TYPO_LINE])
        self.assertIn('"deny_zone"', report)
        self.assertIn('"deny_zones"', report)
        self.assertIn(" ⚠️알 수 없는 설정 키 1건(§0)", result.stdout)
        self.assertIn("이슈 0건(치명 0건)", result.stdout)
        for text in (report, result.stdout, result.stderr):
            self.assertNotIn(MARKER, text)

    def test_cap_and_escaping(self) -> None:
        names = ["a\nb", *(f"extra_key_{i:02d}" for i in range(24))]
        result, report = self.healthcheck(
            self.make_vault("many", {name: [MARKER] for name in names}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        section = section_zero(report)
        self.assertEqual(section[:1], ["## 0. 설정 경고 (21)"])
        self.assertEqual(len(section), 22)
        key_lines = section[1:21]
        for line in key_lines:
            self.assertRegex(line, KEY_LINE)
        self.assertEqual(key_lines[0], f'- 알 수 없는 설정 키 "a{BS}u000ab" — 적용되지 않는다.')
        self.assertEqual(section[21], "- 알 수 없는 설정 키 5건 더 — 생략.")
        self.assertIn(" ⚠️알 수 없는 설정 키 25건(§0)", result.stdout)
        self.assertNotIn(MARKER, report + result.stdout + result.stderr)

        result, report = self.healthcheck(
            self.make_vault("special", {name: [MARKER] for name, _ in SPECIAL_KEYS},
                            ascii_only=True))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "special" / REPORT).is_file())
        for text in (report, result.stdout):
            for character in RAW_SPECIAL:
                self.assertNotIn(character, text)
        section = section_zero(report)
        self.assertEqual(section[:1], ["## 0. 설정 경고 (4)"])
        lines = report.splitlines()
        for _name, expected in SPECIAL_KEYS:
            with self.subTest(display=expected):
                matching = [line for line in lines if expected in line]
                self.assertEqual(
                    matching, [f"- 알 수 없는 설정 키 {expected} — 적용되지 않는다."])
                self.assertEqual(len(matching[0].splitlines()), 1)
        self.assertIn(" ⚠️알 수 없는 설정 키 4건(§0)", result.stdout)
        self.assertNotIn(MARKER, report + result.stdout + result.stderr)


class StagedModeTests(_VaultCase):
    def setUp(self) -> None:
        super().setUp()
        isolate_git(self, ceiling=self.root)

    def git(self, vault: Path, *arguments: str) -> None:
        result = subprocess.run(["git", *arguments], cwd=vault, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def staged(self, name: str, extra: dict | None) -> subprocess.CompletedProcess[str]:
        vault = self.make_vault(name, extra)
        self.git(vault, "init", "-q")
        self.git(vault, "add", "--", CONFIG, "00-meta/hot.md")
        return run([str(HEALTHCHECK), "--vault", str(vault), "--staged"], vault,
                   without_claude())

    def test_staged_mode_prints_no_key_warning(self) -> None:
        clean = self.staged("clean", None)
        typo = self.staged("typo", {"deny_zone": [MARKER]})
        self.assertEqual(typo.returncode, clean.returncode, typo.stdout + typo.stderr)
        self.assertNotIn(WARNING_WORDS, typo.stdout + typo.stderr)
        self.assertNotIn(MARKER, typo.stdout + typo.stderr)


class DoctorTests(_VaultCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doctor = load_doctor()

    def run_main(self, vault: Path, *arguments: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with mock.patch.object(self.doctor, "_utf8_streams"), contextlib.redirect_stdout(buffer):
            code = self.doctor.main(["--vault", str(vault), *arguments])
        return code, buffer.getvalue()

    def test_doctor_lists_unknown_keys(self) -> None:
        clean = self.make_vault("clean")
        clean_code, clean_output = self.run_main(clean, "--format", "json")
        self.assertNotIn("config_warnings", json.loads(clean_output))
        _code, clean_text = self.run_main(clean)
        self.assertNotIn("config_warning", clean_text)

        typo = self.make_vault("typo", {"deny_zone": [MARKER], "random_key": 1})
        code, output = self.run_main(typo, "--format", "json")
        self.assertEqual(code, clean_code)
        report = json.loads(output)
        self.assertEqual(report["config_warnings"], [
            {"code": "unknown_key", "key": "deny_zone", "suggestion": "deny_zones"},
            {"code": "unknown_key", "key": "random_key", "suggestion": None},
        ])
        self.assertNotIn(MARKER, output)
        code, text = self.run_main(typo)
        self.assertEqual(code, clean_code)
        self.assertIn('\nconfig_warning: unknown_key "deny_zone" suggestion="deny_zones"\n', text)
        self.assertIn('\nconfig_warning: unknown_key "random_key"\n', text)
        self.assertNotIn(MARKER, text)

    def test_doctor_caps_and_escapes_unknown_keys(self) -> None:
        names = ["a\nb", *(f"extra_key_{i:02d}" for i in range(24))]
        vault = self.make_vault("many", {name: 1 for name in names})
        _code, output = self.run_main(vault, "--format", "json")
        warnings = json.loads(output)["config_warnings"]
        self.assertEqual(len(warnings), 21)
        self.assertEqual(warnings[0], {"code": "unknown_key", "key": f"a{BS}u000ab",
                                       "suggestion": None})
        self.assertTrue(all(item["code"] == "unknown_key" for item in warnings[:20]))
        self.assertEqual(warnings[20], {"code": "more_unknown_keys", "count": 5})
        _code, text = self.run_main(vault)
        self.assertIn(f'\nconfig_warning: unknown_key "a{BS}u000ab"\n', text)
        self.assertIn("\nconfig_warning: more_unknown_keys 5\n", text)


class SessionStartTests(_VaultCase):
    def test_session_start_stays_silent(self) -> None:
        outputs = []
        for name, extra in (("clean", None), ("typo", {"deny_zone": [MARKER]})):
            vault = self.make_vault(name, extra)
            result = run([str(SESSION_HOOK)], self.root,
                         without_claude(CLAUDE_PROJECT_DIR=str(vault)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIn(HOT_TEXT, result.stdout)
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertNotIn(WARNING_WORDS, outputs[1])


if __name__ == "__main__":
    unittest.main()
