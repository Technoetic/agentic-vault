"""The Antigravity install verifier: four agy calls in a disposable home, failing closed.

A fake agy (a .py file the verifier runs with this Python) logs every call with the
environment it saw, then acts as `control.json` next to it says. These tests never read
the user's real home: every verifier run points USERPROFILE and HOME at a temporary
"real home", whose `.gemini/config` the verifier fingerprints before and after.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_antigravity_plugin.py"
SPEC = importlib.util.spec_from_file_location("antigravity_verification", SCRIPT)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

VERSION = "0.48.0"
SKILLS = ["agentic-vault", "vault-day", "webapp-principles"]
LISTED = {"name": "agentic-vault", "version": VERSION, "source": "antigravity", "enabled": True,
          "skills": SKILLS, "hooks": ["agentic-vault"]}
REPORT_FIELDS = ["agy_version", "package_version", "validate", "install_rc", "listed", "bytes_equal",
                 "mismatches", "real_home_unchanged", "passed", "failures"]
# Set by the caller of the verifier; agy must not see any of them.
DROPPED = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "XDG_CONFIG_HOME", "CLAUDE_PROJECT_DIR")

FAKE_AGY = r'''"""Fake agy: logs each call with the environment it saw, then acts as control.json says."""
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time

HERE = Path(__file__).resolve().parent
CONTROL = json.loads((HERE / "control.json").read_text(encoding="utf-8"))
ARGS = sys.argv[1:]
SEEN = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "XDG_CONFIG_HOME", "CLAUDE_PROJECT_DIR")
with (HERE / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps({"args": ARGS, "cwd": os.getcwd(),
                          "env": {name: os.environ.get(name) for name in SEEN}}) + "\n")
HOME = Path(os.environ["USERPROFILE"] if os.name == "nt" else os.environ["HOME"])
INSTALLED = HOME / ".gemini" / "config" / "plugins" / "agentic-vault"


def entries():
    listed = [{"name": "other-plugin", "version": "9.9.9", "source": "antigravity", "enabled": True,
               "skills": ["agentic-vault"], "hooks": ["other-plugin"]}]
    if (INSTALLED / "plugin.json").is_file():
        manifest = json.loads((INSTALLED / "plugin.json").read_text(encoding="utf-8"))
        groups = json.loads((INSTALLED / "hooks.json").read_text(encoding="utf-8"))
        entry = {"name": manifest["name"], "version": manifest["version"], "source": "antigravity",
                 "enabled": True, "path": str(INSTALLED),
                 "skills": sorted(path.name for path in (INSTALLED / "skills").iterdir() if path.is_dir()),
                 "hooks": sorted(groups)}
        entry.update(CONTROL.get("entry", {}))
        for key in CONTROL.get("drop", []):
            del entry[key]
        listed += [entry] * CONTROL.get("copies", 1)
    return {"plugins": listed} if CONTROL.get("object") else listed


if ARGS == ["--version"]:
    time.sleep(CONTROL.get("version_sleep", 0))
    print(CONTROL.get("version", "1.5.7"))
    sys.exit(CONTROL.get("version_rc", 0))
if len(ARGS) == 5 and ARGS[:4] == ["plugin", "validate", "--strict", "--json"]:
    print(CONTROL.get("validate_text") or json.dumps(CONTROL.get("validate", {"findings": []})))
    sys.exit(CONTROL.get("validate_rc", 0))
if len(ARGS) == 3 and ARGS[:2] == ["plugin", "install"]:
    if CONTROL.get("install_rc"):
        print("cannot install " + ARGS[2], file=sys.stderr)
        sys.exit(CONTROL["install_rc"])
    shutil.copytree(ARGS[2], INSTALLED)
    for relative, text in CONTROL.get("change", {}).items():
        target = INSTALLED / relative
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
    if CONTROL.get("leak"):
        Path(CONTROL["leak"]).mkdir(parents=True, exist_ok=True)
        (Path(CONTROL["leak"]) / "leak.json").write_bytes(b"{}")
    for path in INSTALLED.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IREAD)  # agy installs a read-only copy
    sys.exit(0)
if ARGS == ["plugin", "list", "--json"]:
    print(json.dumps(entries()))
    sys.exit(0)
print("fake agy: call not allowed: " + " ".join(ARGS), file=sys.stderr)
sys.exit(97)
'''

PACKAGE_FILES = {
    "plugin.json": json.dumps({
        "$schema": "https://antigravity.google/schemas/v1/plugin.json", "name": "agentic-vault",
        "displayName": "Agentic Vault", "version": VERSION, "description": "Fixture package.",
    }, indent=2) + "\n",
    "hooks.json": json.dumps({"agentic-vault": {
        "SessionStart": [{"type": "command", "command": "python ./hooks/session_start.py --host antigravity",
                          "timeout": 30}],
        "Stop": [{"type": "command", "command": "python ./hooks/session_checkpoint.py --host antigravity",
                  "timeout": 15}],
    }}, indent=2) + "\n",
    "README.md": "# fixture\n",
    "hooks/session_start.py": "print('{}')\n",
    "hooks/session_checkpoint.py": "print('{}')\n",
    "skills/agentic-vault/SKILL.md": "---\nname: agentic-vault\ndescription: Fixture skill.\n---\n\nBody\n",
    "skills/agentic-vault/scripts/vault_recall.py": "# fixture\n",
    "skills/vault-day/SKILL.md": ("---\nname: vault-day\ndescription: Fixture command skill.\n"
                                  "disable-model-invocation: true\n---\n\nBody\n"),
    "skills/webapp-principles/SKILL.md": "---\nname: webapp-principles\ndescription: Fixture skill.\n---\n\nBody\n",
    "docs/antigravity.md": "# fixture\n",
    "assets/sample.bin": bytes([0, 255, 13, 10, 26]),
    # Bytecode next to the sources is not part of the package and stays out of the copy.
    "hooks/__pycache__/session_start.cpython-312.pyc": b"bytecode",
    "skills/agentic-vault/scripts/__pycache__/vault_recall.cpython-312.pyc": b"bytecode",
}


def make_package(folder: Path) -> Path:
    for relative, content in PACKAGE_FILES.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return folder


def write_fake(folder: Path, control: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "control.json").write_text(json.dumps(control or {}), encoding="utf-8")
    log = folder / "calls.jsonl"
    if log.exists():
        log.unlink()
    fake = folder / "agy.py"
    fake.write_text(FAKE_AGY, encoding="utf-8")
    return fake


def logged_calls(folder: Path) -> list[dict]:
    log = folder / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (*strings_in(key), *strings_in(item))]
    if isinstance(value, list):
        return [text for item in value for text in strings_in(item)]
    return []


class AgyCallContractTests(unittest.TestCase):
    def test_only_version_validate_install_and_list_are_allowed(self) -> None:
        self.assertEqual(verifier.ALLOWED_SUBCOMMANDS,
                         ("--version", "plugin validate", "plugin install", "plugin list"))
        self.assertEqual(verifier.AGY_CALLS, (
            ("--version",),
            ("plugin", "validate", "--strict", "--json", "PATH"),
            ("plugin", "install", "PATH"),
            ("plugin", "list", "--json"),
        ))

    def test_other_argument_combinations_are_refused_before_a_process_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fake = write_fake(root / "fake")
            command = verifier.agy_command(str(fake))
            environment = verifier.isolated_environment(root / "home")
            package = str(root / "package")
            refused = (
                [], ["-p", "hello"], ["login"], ["logout"], ["version"], ["--help"], ["--version", "--verbose"],
                ["-p", "--version"], ["plugin", "validate", package], ["plugin", "validate", "--json", package],
                ["plugin", "validate", "--strict", "--json", "relative/package"],
                ["plugin", "validate", "--strict", "--json", package, "-p"],
                ["plugin", "install", "--force", package], ["plugin", "install", "agentic-vault"],
                ["plugin", "list"], ["plugin", "list", "--json", "-p"], ["plugin", "uninstall", "agentic-vault"],
                ["plugin", "enable", "agentic-vault"], ["plugin", "disable", "agentic-vault"], ["plugin", "help"],
                ["plugins", "list", "--json"],
            )
            for args in refused:
                with self.subTest(args=args), self.assertRaises(verifier.CallRefused):
                    verifier.run_agy(command, args, environment, root)
            self.assertEqual(logged_calls(fake.parent), [])
            for args in (["--version"], ["plugin", "list", "--json"]):
                returncode, _stdout, stderr = verifier.run_agy(command, args, environment, root)
                self.assertEqual(returncode, 0, stderr)
            self.assertEqual([call["args"] for call in logged_calls(fake.parent)],
                             [["--version"], ["plugin", "list", "--json"]])

    def test_the_verifier_sets_the_disposable_home_itself(self) -> None:
        # No wrapper script: the six variables come from this module, inside the given home.
        self.assertEqual(verifier.HOME_VARIABLES, ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP"))
        self.assertNotIn("agy_iso", SCRIPT.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve() / "home"
            with patch.dict(os.environ, {name: "from-the-caller" for name in DROPPED}):
                environment = verifier.isolated_environment(home)
            self.assertEqual({name: environment[name] for name in verifier.HOME_VARIABLES}, {
                "USERPROFILE": str(home), "HOME": str(home),
                "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "TMP": str(home / "tmp"), "TEMP": str(home / "tmp"),
            })
            for name in DROPPED:
                self.assertNotIn(name, environment)
            self.assertEqual(environment.get("PATH"), os.environ.get("PATH"))
            for name in ("APPDATA", "LOCALAPPDATA", "TMP"):
                self.assertTrue(Path(environment[name]).is_dir(), name)

    def test_a_native_agy_path_runs_directly(self) -> None:
        self.assertEqual(verifier.agy_command(sys.executable), [str(Path(sys.executable).resolve())])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fake = write_fake(root / "fake")
            self.assertEqual(verifier.agy_command(str(fake)), [sys.executable, str(fake.resolve())])
            environment = verifier.isolated_environment(root / "home")
            returncode, stdout, stderr = verifier.run_agy(
                verifier.agy_command(sys.executable), ["--version"], environment, root)
        self.assertEqual(returncode, 0, stderr)
        self.assertIn("Python", stdout + stderr)


class ValidationSummaryTests(unittest.TestCase):
    def test_findings_are_counted_by_severity_at_any_depth(self) -> None:
        data = {"findings": [{"severity": "error", "message": "bad"}, {"severity": "Warning"},
                             {"severity": "note"}, {"severity": "INFO"}],
                "components": {"skills": [{"name": "vault-day", "findings": [{"severity": "warning"}]}],
                               "hooks": 1, "agents": 0}}
        self.assertEqual(verifier.summarize_validation(data), {
            "errors": 1, "warnings": 2, "notes": 2, "components": {"agents": 0, "hooks": 1, "skills": 1}})

    def test_an_unknown_severity_counts_as_a_warning(self) -> None:
        self.assertEqual(verifier.summarize_validation([{"severity": "fatal"}, {"severity": "hint"}]),
                         {"errors": 0, "warnings": 2, "notes": 0, "components": None})

    def test_summary_counts_and_lists_can_only_raise_the_counts(self) -> None:
        data = {"findings": [{"severity": "error"}], "errors": 0, "warnings": [{"message": "unnamed"}],
                "summary": {"errors": 2, "notes": 3, "warnings": True}}
        self.assertEqual(verifier.summarize_validation(data),
                         {"errors": 2, "warnings": 1, "notes": 3, "components": None})

    def test_component_lists_are_counted_by_kind_without_names(self) -> None:
        data = {"findings": [], "components": [{"type": "skill", "name": "vault-day"}, {"type": "skill"},
                                               {"kind": "hooks"}, "rules"]}
        self.assertEqual(verifier.summarize_validation(data),
                         {"errors": 0, "warnings": 0, "notes": 0, "components": {"hooks": 1, "items": 1, "skill": 2}})


class JsonOutputTests(unittest.TestCase):
    def test_json_is_read_after_a_byte_order_mark_or_leading_lines(self) -> None:
        cases = {
            '{"findings": []}\n': (True, {"findings": []}),
            chr(0xFEFF) + '[{"name": "x"}]': (True, [{"name": "x"}]),
            'Validating plugin...\n{"findings": []}\n': (True, {"findings": []}),
            "not JSON\n": (False, None),
            '{"findings": []} and more': (False, None),
            "": (False, None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(verifier.parse_json(text), expected)


class ListingTests(unittest.TestCase):
    @staticmethod
    def without(key: str) -> dict:
        return {name: value for name, value in LISTED.items() if name != key}

    def test_accepted_listing_shapes(self) -> None:
        other = {"name": "other-plugin", "version": "1.0.0", "source": "claude-code", "enabled": False,
                 "skills": [], "hooks": []}
        cases = {
            "top-level list": [other, dict(LISTED)],
            "plugins list": {"plugins": [dict(LISTED), other]},
            "plugins map": {"plugins": {"agentic-vault": self.without("name"), "other-plugin": other}},
            "name objects": [{**LISTED, "skills": [{"name": name} for name in reversed(SKILLS)],
                              "hooks": [{"name": "agentic-vault"}]}],
            "disabled false": [{**self.without("enabled"), "disabled": False}],
        }
        for name, data in cases.items():
            with self.subTest(case=name):
                self.assertEqual(verifier.check_listing(data, VERSION, SKILLS), (LISTED, []))

    def test_rejected_listings(self) -> None:
        cases = {
            "source claude-code": ([{**LISTED, "source": "claude-code"}],
                                   "source 'claude-code', expected 'antigravity'"),
            "version": ([{**LISTED, "version": "0.47.0"}], "version '0.47.0', expected the package version '0.48.0'"),
            "disabled": ([{**LISTED, "enabled": False}], "not enabled"),
            "disabled flag": ([{**self.without("enabled"), "disabled": True}], "not enabled"),
            "enabled not reported": ([self.without("enabled")], "enabled state not reported"),
            "missing skill": ([{**LISTED, "skills": SKILLS[:2]}],
                              "skills differ from the package skill folders (missing ['webapp-principles'], extra [])"),
            "skill count only": ([{**LISTED, "skills": 3}], "skills are not a list of names"),
            "hooks": ([{**LISTED, "hooks": ["agentic-vault", "other"]}],
                      "hooks ['agentic-vault', 'other'], expected ['agentic-vault']"),
            "absent": ([{**LISTED, "name": "other-plugin"}], "agentic-vault listed 0 times, expected once"),
            "twice": ([LISTED, LISTED], "agentic-vault listed 2 times, expected once"),
            "unknown shape": ({"items": [LISTED]}, "agentic-vault listed 0 times, expected once"),
        }
        for name, (data, problem) in cases.items():
            with self.subTest(case=name):
                _listed, problems = verifier.check_listing(data, VERSION, SKILLS)
                self.assertEqual(problems, [problem])


class FingerprintTests(unittest.TestCase):
    def test_names_sizes_and_modification_times_are_compared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / ".gemini" / "config"
            (config / "plugins").mkdir(parents=True)
            settings = config / "settings.json"
            settings.write_bytes(b"{}")
            stamp = settings.stat()
            before = verifier.fingerprint(config)
            self.assertEqual(verifier.fingerprint(config), before)
            os.utime(settings, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 5_000_000_000))
            self.assertNotEqual(verifier.fingerprint(config), before, "modification time")
            os.utime(settings, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(verifier.fingerprint(config), before)
            settings.write_bytes(b"{ }")
            os.utime(settings, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertNotEqual(verifier.fingerprint(config), before, "size")
            settings.write_bytes(b"{}")
            os.utime(settings, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(verifier.fingerprint(config), before)
            (config / "plugins" / "added.json").write_bytes(b"{}")
            self.assertNotEqual(verifier.fingerprint(config), before, "new name")

    def test_contents_are_never_read_and_an_absent_folder_has_no_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / ".gemini" / "config"
            self.assertIsNone(verifier.fingerprint(config))
            config.mkdir(parents=True)
            (config / "settings.json").write_bytes(b"{}")
            with patch("builtins.open", side_effect=AssertionError("content read")), \
                 patch.object(Path, "read_bytes", side_effect=AssertionError("content read")), \
                 patch.object(Path, "read_text", side_effect=AssertionError("content read")):
                self.assertIsNotNone(verifier.fingerprint(config))


class VerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.package = make_package(self.root / "source package")
        self.real_home = self.root / "real home"
        self.real_config = self.real_home / ".gemini" / "config"
        self.real_config.mkdir(parents=True)
        (self.real_config / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.fake_folder = self.root / "fake agy"
        self.fake = self.fake_folder / "agy.py"
        self.report_path = self.root / "out" / "report.json"

    @contextlib.contextmanager
    def simulated_home(self, extra_environment: dict | None = None):
        # The verifier's real home is this temporary folder; its own temporary folders go to scratch.
        environment = {"USERPROFILE": str(self.real_home), "HOME": str(self.real_home), **(extra_environment or {})}
        with patch.dict(os.environ, environment), patch.object(tempfile, "tempdir", str(self.scratch)):
            yield

    def run_verifier(self, control: dict | None = None, *, argv: list[str] | None = None,
                     extra_environment: dict | None = None) -> SimpleNamespace:
        write_fake(self.fake_folder, control)
        if self.report_path.exists():
            self.report_path.unlink()
        if argv is None:
            argv = ["--agy", str(self.fake), "--package", str(self.package), "--json", str(self.report_path)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.simulated_home(extra_environment), contextlib.redirect_stdout(stdout), \
             contextlib.redirect_stderr(stderr):
            code = verifier.main(argv)
        text = stdout.getvalue()
        return SimpleNamespace(code=code, report=json.loads(text) if text.strip() else None,
                               calls=logged_calls(self.fake_folder), stdout=text, stderr=stderr.getvalue())

    def assert_no_paths(self, report: dict) -> None:
        for text in strings_in(report):
            for path in (str(self.root), self.root.as_posix()):
                self.assertNotIn(path.lower(), text.lower())

    def test_clean_package_passes_with_exactly_the_four_allowed_calls(self) -> None:
        with patch.object(verifier, "fingerprint", wraps=verifier.fingerprint) as fingerprint:
            outcome = self.run_verifier()
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(outcome.report, {
            "agy_version": "1.5.7", "package_version": VERSION,
            "validate": {"errors": 0, "warnings": 0, "notes": 0, "components": None},
            "install_rc": 0, "listed": LISTED, "bytes_equal": True, "mismatches": [],
            "real_home_unchanged": True, "passed": True, "failures": [],
        })
        self.assertEqual(list(outcome.report), REPORT_FIELDS)
        written = self.report_path.read_bytes()
        self.assertEqual(json.loads(written.decode("utf-8")), outcome.report)
        self.assertTrue(written.endswith(b"}\n"))
        self.assertNotIn(b"\r", written)
        self.assert_no_paths(outcome.report)
        copy = outcome.calls[1]["args"][-1]
        self.assertEqual([call["args"] for call in outcome.calls], [
            ["--version"],
            ["plugin", "validate", "--strict", "--json", copy],
            ["plugin", "install", copy],
            ["plugin", "list", "--json"],
        ])
        self.assertTrue(os.path.isabs(copy))
        self.assertEqual(Path(copy).name, "agentic-vault")
        self.assertTrue(Path(copy).resolve().is_relative_to(self.scratch))
        # Only the simulated real home is read, before and after the agy calls, and it is untouched.
        self.assertEqual([call.args[0] for call in fingerprint.call_args_list], [self.real_config] * 2)
        self.assertEqual(sorted(path.name for path in self.real_config.iterdir()), ["settings.json"])
        # The copy and the disposable home are removed although agy left read-only files.
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_agy_sees_the_redirected_home_and_no_caller_credentials(self) -> None:
        outcome = self.run_verifier(extra_environment={name: "from-the-caller" for name in DROPPED})
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(len(outcome.calls), 4)
        home = Path(outcome.calls[0]["env"]["USERPROFILE"])
        self.assertTrue(home.resolve().is_relative_to(self.scratch))
        self.assertNotEqual(home.resolve(), self.real_home)
        for call in outcome.calls:
            with self.subTest(args=call["args"][:2]):
                environment = call["env"]
                self.assertEqual(environment["USERPROFILE"], str(home))
                self.assertEqual(environment["HOME"], str(home))
                self.assertEqual(environment["APPDATA"], str(home / "AppData" / "Roaming"))
                self.assertEqual(environment["LOCALAPPDATA"], str(home / "AppData" / "Local"))
                self.assertEqual(environment["TMP"], str(home / "tmp"))
                self.assertEqual(environment["TEMP"], str(home / "tmp"))
                for name in DROPPED:
                    self.assertIsNone(environment[name], name)
                self.assertTrue(Path(call["cwd"]).resolve().is_relative_to(self.scratch))

    def test_validate_warning_fails_and_notes_are_allowed(self) -> None:
        warning = {"findings": [{"severity": "warning", "message": "SKILL.md body is over 24000 bytes"}]}
        outcome = self.run_verifier({"validate": warning})
        self.assertEqual(outcome.code, 1)
        self.assertFalse(outcome.report["passed"])
        self.assertEqual(outcome.report["validate"], {"errors": 0, "warnings": 1, "notes": 0, "components": None})
        self.assertEqual(outcome.report["failures"], ["validate: 0 error(s) and 1 warning(s)"])
        self.assertIn("FAIL validate: 0 error(s) and 1 warning(s)", outcome.stderr.splitlines())
        self.assertEqual(len(outcome.calls), 4)
        self.assertTrue(outcome.report["bytes_equal"])

        notes = {"findings": [{"severity": "note", "message": "no agents"}],
                 "components": {"skills": 3, "hooks": ["agentic-vault"]}}
        outcome = self.run_verifier({"validate": notes})
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(outcome.report["validate"],
                         {"errors": 0, "warnings": 0, "notes": 1, "components": {"hooks": 1, "skills": 3}})

    def test_validate_error_exit_code_and_unreadable_output_fail(self) -> None:
        cases = {
            "error": ({"validate": {"findings": [{"severity": "error"}]}}, "validate: 1 error(s) and 0 warning(s)"),
            "exit code": ({"validate_rc": 1}, "validate: agy plugin validate exited 1"),
            "not JSON": ({"validate_text": "Plugin is invalid"}, "validate: output is not JSON"),
        }
        for name, (control, failure) in cases.items():
            with self.subTest(case=name):
                outcome = self.run_verifier(control)
                self.assertEqual(outcome.code, 1)
                self.assertEqual(outcome.report["failures"], [failure])
                self.assertEqual(outcome.report["install_rc"], 0)

    def test_listed_source_claude_code_fails(self) -> None:
        outcome = self.run_verifier({"entry": {"source": "claude-code"}})
        self.assertEqual(outcome.code, 1)
        self.assertEqual(outcome.report["listed"]["source"], "claude-code")
        self.assertEqual(outcome.report["failures"], ["listed: source 'claude-code', expected 'antigravity'"])
        self.assertIn("FAIL listed: source 'claude-code', expected 'antigravity'", outcome.stderr.splitlines())

    def test_listed_version_must_equal_the_package_version(self) -> None:
        outcome = self.run_verifier({"entry": {"version": "0.47.0"}})
        self.assertEqual(outcome.code, 1)
        self.assertEqual(outcome.report["failures"],
                         ["listed: version '0.47.0', expected the package version '0.48.0'"])

    def test_listing_as_a_plugins_object_passes(self) -> None:
        outcome = self.run_verifier({"object": True})
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(outcome.report["listed"], LISTED)

    def test_installed_files_must_equal_the_copy_byte_for_byte(self) -> None:
        cases = {
            "differs": ({"assets/sample.bin": "rewritten"}, {"path": "assets/sample.bin", "problem": "differs"}),
            "line endings": ({"README.md": "# fixture\r\n"}, {"path": "README.md", "problem": "differs"}),
            "missing": ({"skills/vault-day/SKILL.md": None}, {"path": "skills/vault-day/SKILL.md", "problem": "missing"}),
            "extra": ({"install-state.json": "{}"}, {"path": "install-state.json", "problem": "extra"}),
        }
        for name, (change, mismatch) in cases.items():
            with self.subTest(case=name):
                outcome = self.run_verifier({"change": change})
                self.assertEqual(outcome.code, 1)
                self.assertFalse(outcome.report["bytes_equal"])
                self.assertEqual(outcome.report["mismatches"], [mismatch])
                self.assertEqual(outcome.report["failures"],
                                 [f"bytes_equal: 1 mismatch(es): {mismatch['path']} ({mismatch['problem']})"])

    def test_failed_install_is_reported_without_temporary_paths(self) -> None:
        outcome = self.run_verifier({"install_rc": 3})
        report = outcome.report
        self.assertEqual(outcome.code, 1)
        self.assertEqual(report["install_rc"], 3)
        self.assertIsNone(report["listed"])
        self.assertFalse(report["bytes_equal"])
        self.assertEqual(report["mismatches"], [{"path": ".", "problem": "missing"}])
        install = [failure for failure in report["failures"] if failure.startswith("install_rc: ")]
        self.assertEqual(len(install), 1)
        self.assertTrue(install[0].startswith("install_rc: agy plugin install exited 3: cannot install <temp>"),
                        install[0])
        self.assertIn("listed: agentic-vault listed 0 times, expected once", report["failures"])
        self.assert_no_paths(report)

    def test_failed_version_call_is_reported_and_the_other_checks_run(self) -> None:
        outcome = self.run_verifier({"version_rc": 2})
        self.assertEqual(outcome.code, 1)
        self.assertIsNone(outcome.report["agy_version"])
        self.assertEqual(outcome.report["failures"], ["agy_version: agy --version exited 2"])
        self.assertEqual(len(outcome.calls), 4)

    def test_change_to_the_real_gemini_config_fails(self) -> None:
        for case in ("existing config", "config created"):
            with self.subTest(case=case):
                if case == "config created":
                    shutil.rmtree(self.real_home / ".gemini")
                outcome = self.run_verifier({"leak": str(self.real_config)})
                self.assertEqual(outcome.code, 1)
                self.assertFalse(outcome.report["real_home_unchanged"])
                self.assertEqual(outcome.report["failures"],
                                 ["real_home_unchanged: the real ~/.gemini/config changed during verification"])
                self.assertTrue(outcome.report["bytes_equal"])

    def test_keep_leaves_the_copy_and_home_and_the_copy_has_no_bytecode(self) -> None:
        outcome = self.run_verifier(argv=["--agy", str(self.fake), "--package", str(self.package), "--keep"])
        self.assertEqual(outcome.code, 0, outcome.stderr)
        kept = [line[len("kept: "):] for line in outcome.stderr.splitlines() if line.startswith("kept: ")]
        self.assertEqual(len(kept), 1)
        root = Path(kept[0])
        self.assertTrue(root.resolve().is_relative_to(self.scratch))
        copy = root / "package" / "agentic-vault"
        installed = root / "home" / ".gemini" / "config" / "plugins" / "agentic-vault"
        self.assertEqual((copy / "assets" / "sample.bin").read_bytes(), PACKAGE_FILES["assets/sample.bin"])
        self.assertEqual((installed / "plugin.json").read_bytes(), (self.package / "plugin.json").read_bytes())
        self.assertFalse((copy / "hooks" / "__pycache__").exists())
        self.assertFalse((copy / "skills" / "agentic-vault" / "scripts" / "__pycache__").exists())
        self.assertTrue((self.package / "hooks" / "__pycache__").is_dir())

    def test_default_package_is_the_antigravity_folder_of_the_repository(self) -> None:
        repo = self.root / "repo"
        shutil.copytree(self.package, repo / "antigravity")
        with patch.object(verifier, "ROOT", repo):
            outcome = self.run_verifier(argv=["--agy", str(self.fake)])
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(outcome.report["listed"], LISTED)
        with patch.object(verifier, "ROOT", self.root / "no repo"):
            outcome = self.run_verifier(argv=["--agy", str(self.fake)])
        self.assertEqual(outcome.code, 2)
        self.assertIn("package folder not found", outcome.stderr)

    def test_timed_out_call_stops_the_remaining_agy_calls(self) -> None:
        write_fake(self.fake_folder, {"version_sleep": 60})
        with self.simulated_home():
            report, kept = verifier.verify(str(self.fake), self.package, timeout=3)
        self.assertIsNone(kept)
        self.assertFalse(report["passed"])
        self.assertEqual(report["failures"][:4], [
            "agy_version: agy --version timed out after 3 s",
            "validate: not run because an earlier agy call timed out",
            "install_rc: not run because an earlier agy call timed out",
            "listed: not run because an earlier agy call timed out",
        ])
        self.assertIn([call["args"] for call in logged_calls(self.fake_folder)], ([], [["--version"]]))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_missing_agy_or_package_exits_2_before_the_home_is_read(self) -> None:
        cases = {
            "absent .py": ["--agy", str(self.root / "absent-agy.py"), "--package", str(self.package)],
            "absent executable": ["--agy", "agentic-vault-absent-agy-for-tests", "--package", str(self.package)],
            "absent package": ["--agy", str(self.fake), "--package", str(self.root / "absent package")],
        }
        for name, argv in cases.items():
            with self.subTest(case=name), patch.object(verifier, "fingerprint") as fingerprint:
                outcome = self.run_verifier(argv=argv)
                self.assertEqual(outcome.code, 2)
                self.assertEqual(outcome.stdout, "")
                self.assertEqual(outcome.calls, [])
                fingerprint.assert_not_called()
        self.assertEqual(list(self.scratch.iterdir()), [])
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            verifier.main(["--package", str(self.package)])
        self.assertEqual(raised.exception.code, 2)


class CommandLineTests(unittest.TestCase):
    def test_missing_agy_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = make_package(root / "package")
            home = root / "real home"
            environment = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
            for argv in (["--agy", str(root / "absent-agy.py"), "--package", str(package)],
                         ["--package", str(package)]):
                with self.subTest(argv=argv[0]):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), *argv], env=environment, capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=120)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
