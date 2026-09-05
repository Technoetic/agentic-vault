from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_HOOK = REPO_ROOT / "hooks" / "session_start.py"
HOOKS_CONFIG = REPO_ROOT / "hooks" / "hooks.json"
HOOK_RUNNER = REPO_ROOT / "hooks" / "run_python_hook.sh"
SCRIPTS_DIR = REPO_ROOT / "skills" / "agentic-vault" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import guard
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


healthcheck = load_module(
    "session_test_healthcheck", SCRIPTS_DIR / "vault_healthcheck.py"
)


class SessionStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def write(self, relative: str, data: str) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def write_bytes(self, relative: str, data: bytes) -> Path:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_config(self, **overrides: object) -> None:
        config: dict[str, object] = {
            "handoff_note": "00-meta/handoff.md",
            "hot_note": "00-meta/hot.md",
            "deny_zones": ["90-assets"],
        }
        config.update(overrides)
        self.write(
            "00-meta/vault-config.json",
            json.dumps(config, ensure_ascii=False),
        )

    def run_hook(
        self,
        *arguments: str,
        codex: bool = False,
        cwd: Path | None = None,
        claude_project_dir: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith("CLAUDE_")
        }
        if not codex:
            env["CLAUDE_PROJECT_DIR"] = str(self.vault)
        if claude_project_dir is not None:
            env["CLAUDE_PROJECT_DIR"] = claude_project_dir
        return subprocess.run(
            [sys.executable, str(SESSION_HOOK), *arguments],
            cwd=cwd if cwd is not None else (self.vault if codex else REPO_ROOT),
            env=env,
            input='{"source":"startup"}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )

    def test_injects_korean_and_english_notes(self) -> None:
        self.write_config()
        self.write("00-meta/handoff.md", "Continue the release checklist.")
        self.write("00-meta/hot.md", "오늘은 안정성 검증을 완료한다.")

        result = self.run_hook()

        self.assertEqual(result.returncode, 0)
        self.assertIn("Continue the release checklist.", result.stdout)
        self.assertIn("오늘은 안정성 검증을 완료한다.", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_non_vault_is_a_quiet_noop(self) -> None:
        result = self.run_hook()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_codex_cwd_matches_claude_context_with_spaces_and_korean_paths(self) -> None:
        self.vault = self.root / "작업 vault"
        self.vault.mkdir()
        self.write_config(handoff_note="00-meta/세션 인계.md", hot_note="00-meta/hot note.md")
        self.write("00-meta/세션 인계.md", "Continue the release checklist.")
        self.write("00-meta/hot note.md", "오늘은 안정성 검증을 완료한다.")

        claude = self.run_hook()
        codex = self.run_hook(codex=True)

        self.assertEqual(claude.returncode, 0, claude.stderr)
        self.assertIn("Continue the release checklist.", claude.stdout)
        self.assertIn("오늘은 안정성 검증을 완료한다.", claude.stdout)
        self.assertEqual(codex.returncode, 0, codex.stderr)
        self.assertEqual(codex.stdout, claude.stdout)
        self.assertEqual(codex.stderr, "")

    def test_explicit_vault_wins_over_stale_claude_environment_and_cwd(self) -> None:
        stale_vault = self.vault
        self.write_config()
        self.write("00-meta/hot.md", "STALE_CONTEXT")
        self.vault = self.root / "선택 vault"
        self.vault.mkdir()
        self.write_config()
        self.write("00-meta/hot.md", "EXPLICIT_CONTEXT")

        result = self.run_hook(
            "--vault", str(self.vault), cwd=stale_vault,
            claude_project_dir=str(stale_vault),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EXPLICIT_CONTEXT", result.stdout)
        self.assertNotIn("STALE_CONTEXT", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_claude_environment_wins_over_a_different_vault_cwd(self) -> None:
        cwd_vault = self.vault
        self.write_config()
        self.write("00-meta/hot.md", "CWD_CONTEXT")
        self.vault = self.root / "claude vault"
        self.vault.mkdir()
        self.write_config()
        self.write("00-meta/hot.md", "CLAUDE_CONTEXT")

        result = self.run_hook(cwd=cwd_vault)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLAUDE_CONTEXT", result.stdout)
        self.assertNotIn("CWD_CONTEXT", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_blank_claude_environment_falls_back_to_cwd(self) -> None:
        self.write_config()
        self.write("00-meta/hot.md", "CWD_CONTEXT")

        for value in ("", " \t "):
            with self.subTest(value=value):
                result = self.run_hook(codex=True, claude_project_dir=value)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CWD_CONTEXT", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_codex_non_vault_cwd_is_a_quiet_noop(self) -> None:
        self.write_config()
        self.write("00-meta/hot.md", "CHILD_VAULT_CONTEXT")

        result = self.run_hook(codex=True, cwd=self.root)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_codex_explicit_relative_vault_resolves_from_cwd(self) -> None:
        self.vault = self.root / "선택 vault"
        self.vault.mkdir()
        self.write_config()
        self.write("00-meta/hot.md", "RELATIVE_CONTEXT")

        result = self.run_hook("--vault", self.vault.name, codex=True, cwd=self.root)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RELATIVE_CONTEXT", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_codex_unsafe_configs_keep_diagnostics_content_free(self) -> None:
        (self.root / "outside.md").write_text("OUTSIDE_SECRET", encoding="utf-8")
        self.write("00-meta/handoff.md", "HANDOFF_SECRET")
        self.write("00-meta/hot.md", "HOT_SECRET")
        self.write("90-assets/hot.md", "DENIED_SECRET")
        cases = (
            {"handoff_note": "../outside.md"},
            {"hot_note": "90-assets/hot.md"},
            {"hot_note": "CONOUT$.md"},
            {"hot_max_tokens": -1},
            '{"hot_note": "CONFIG_SECRET"',
            " " * (256 * 1024 + 1),
        )
        for explicit in (False, True):
            for index, config in enumerate(cases):
                with self.subTest(explicit=explicit, config=index):
                    if isinstance(config, dict):
                        self.write_config(**config)
                    else:
                        self.write("00-meta/vault-config.json", config)
                    arguments = ("--vault", str(self.vault)) if explicit else ()

                    result = self.run_hook(
                        *arguments, codex=True, cwd=self.root if explicit else self.vault,
                    )

                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(
                        result.stderr.strip(),
                        "agentic-vault: invalid session context configuration",
                    )

    def test_codex_entry_keeps_token_and_note_read_limits(self) -> None:
        self.write("00-meta/handoff.md", "DISABLED_HANDOFF")
        self.write_bytes(
            "00-meta/hot.md",
            "한글 English ".encode("utf-8") * 100_000 + b"TAIL_SECRET",
        )
        for explicit, budget in (
            (False, 40), (True, 40), (False, 1_000_000), (True, 1_000_000),
        ):
            with self.subTest(explicit=explicit, budget=budget):
                self.write_config(handoff_max_tokens=0, hot_max_tokens=budget)
                arguments = ("--vault", str(self.vault)) if explicit else ()
                result = self.run_hook(
                    *arguments, codex=True, cwd=self.root if explicit else self.vault,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("truncated", result.stdout.casefold())
                self.assertNotIn("DISABLED_HANDOFF", result.stdout)
                self.assertNotIn("TAIL_SECRET", result.stdout)
                self.assertLessEqual(
                    healthcheck.estimate_tokens(result.stdout.rstrip("\n")), budget,
                )
                self.assertLess(len(result.stdout.encode("utf-8")), 512 * 1024)
                self.assertEqual(result.stderr, "")

    def test_traversal_never_reads_outside_marker(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("OUTSIDE_MARKER secret", encoding="utf-8")
        self.write_config(handoff_note="../outside.md", hot_note="")

        result = self.run_hook()

        self.assertEqual(result.stdout, "")
        self.assertNotIn("OUTSIDE_MARKER", result.stderr)
        self.assertNotIn("outside.md", result.stderr)
        self.assertIn("invalid", result.stderr.casefold())

    def test_deny_zone_yields_no_partial_injection(self) -> None:
        self.write_config(handoff_note="00-meta/handoff.md", hot_note="90-assets/hot.md")
        self.write("00-meta/handoff.md", "SAFE_MARKER")
        self.write("90-assets/hot.md", "DENIED_MARKER")

        result = self.run_hook()

        self.assertEqual(result.stdout, "")
        self.assertNotIn("SAFE_MARKER", result.stderr)
        self.assertNotIn("DENIED_MARKER", result.stderr)
        self.assertIn("invalid", result.stderr.casefold())

    def test_malformed_config_yields_no_injection_and_safe_diagnostic(self) -> None:
        self.write("00-meta/vault-config.json", '{"hot_note": "SECRET_CONTENT"')
        self.write("00-meta/hot.md", "SHOULD_NOT_APPEAR")

        result = self.run_hook()

        self.assertEqual(result.stdout, "")
        self.assertNotIn("SECRET_CONTENT", result.stderr)
        self.assertNotIn("SHOULD_NOT_APPEAR", result.stderr)
        self.assertIn("invalid", result.stderr.casefold())

    def test_invalid_budget_yields_no_injection(self) -> None:
        self.write_config(hot_max_tokens=-1)
        self.write("00-meta/handoff.md", "HANDOFF_SECRET")
        self.write("00-meta/hot.md", "HOT_SECRET")

        result = self.run_hook()

        self.assertEqual(result.stdout, "")
        self.assertNotIn("HANDOFF_SECRET", result.stderr)
        self.assertNotIn("HOT_SECRET", result.stderr)
        self.assertIn("invalid", result.stderr.casefold())

    def test_windows_console_device_alias_config_is_rejected_content_free(self) -> None:
        for alias in ("COM¹", "lpt².txt", "ConIn$", "CONOUT$.md"):
            with self.subTest(alias=alias):
                self.write_config(handoff_note="", hot_note=alias)

                result = self.run_hook()

                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr.strip(),
                    "agentic-vault: invalid session context configuration",
                )

    def test_zero_budget_disables_only_that_section(self) -> None:
        self.write_config(handoff_max_tokens=0, hot_max_tokens=100)
        self.write("00-meta/handoff.md", "DISABLED_HANDOFF")
        self.write("00-meta/hot.md", "VISIBLE_HOT")

        result = self.run_hook()

        self.assertNotIn("DISABLED_HANDOFF", result.stdout)
        self.assertIn("VISIBLE_HOT", result.stdout)

    def test_default_healthcheck_budgets_are_used_when_unspecified(self) -> None:
        self.write_config()
        self.write("00-meta/handoff.md", "a" * 50_000)
        self.write("00-meta/hot.md", "b" * 50_000)

        result = self.run_hook()

        sections = result.stdout.strip().split("\n\n=== HOT CONTEXT ===\n")
        self.assertEqual(len(sections), 2)
        self.assertLessEqual(healthcheck.estimate_tokens(sections[0]), 4000)
        hot_section = "=== HOT CONTEXT ===\n" + sections[1]
        self.assertLessEqual(healthcheck.estimate_tokens(hot_section), 2000)

    def test_budget_includes_header_and_truncation_marker(self) -> None:
        self.write_config(handoff_note="", hot_max_tokens=40)
        self.write("00-meta/hot.md", "한글 English " * 1000)

        result = self.run_hook()

        self.assertIn("truncated", result.stdout.casefold())
        self.assertLessEqual(healthcheck.estimate_tokens(result.stdout.rstrip("\n")), 40)

    def test_oversized_note_read_is_cut_off_before_its_tail(self) -> None:
        self.write_config(handoff_note="", hot_max_tokens=1_000_000)
        self.write_bytes(
            "00-meta/hot.md",
            b"A" * (2 * 1024 * 1024) + b"TAIL_MARKER_MUST_NOT_BE_READ",
        )

        result = self.run_hook()

        self.assertNotIn("TAIL_MARKER_MUST_NOT_BE_READ", result.stdout)
        self.assertIn("truncated", result.stdout.casefold())
        self.assertLess(len(result.stdout.encode("utf-8")), 512 * 1024)


class ResolveNotePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = load_module("session_test_vault_paths", SCRIPTS_DIR / "vault_paths.py")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "notes").mkdir()
        (self.vault / "notes" / "ok.md").write_text("ok", encoding="utf-8")
        (self.vault / "private").mkdir()
        (self.vault / "private" / "secret.md").write_text(
            "WINDOWS_ALIAS_SECRET", encoding="utf-8"
        )

    def test_returns_contained_regular_note(self) -> None:
        result = self.paths.resolve_note_path(self.vault, "notes/ok.md")

        self.assertEqual(result, (self.vault / "notes" / "ok.md").resolve())

    def test_rejects_absolute_traversal_and_deny_bypass(self) -> None:
        cases = [
            str((self.root / "outside.md").resolve()),
            "../outside.md",
            "notes/../../outside.md",
            "90-Assets/hidden.md",
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises((ValueError, OSError)):
                    self.paths.resolve_note_path(
                        self.vault, value, deny_zones=["90-assets"]
                    )

    def test_windows_aliases_cannot_bypass_a_real_denied_directory(self) -> None:
        for alias in ("private./secret.md", "private /secret.md"):
            with self.subTest(alias=alias):
                with self.assertRaises((ValueError, OSError)):
                    self.paths.resolve_note_path(
                        self.vault, alias, deny_zones=["private"]
                    )

    def test_rejects_ads_controls_and_reserved_device_names(self) -> None:
        cases = (
            "notes/ok.md:stream",
            "notes/control\x1f.md",
            "CON/secret.md",
            "aux.txt",
            "COM¹",
            "com².txt",
            "COM³.md",
            "LPT¹",
            "lpt².txt",
            "LPT³.md",
            "CONIN$",
            "conout$.txt",
        )
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises((ValueError, OSError)):
                    self.paths.resolve_note_path(self.vault, value)

    def test_internal_spaces_and_korean_names_remain_valid(self) -> None:
        note = self.vault / "notes" / "회의 기록 1.md"
        note.write_text("ok", encoding="utf-8")

        result = self.paths.resolve_note_path(self.vault, "notes/회의 기록 1.md")

        self.assertEqual(result, note.resolve())

    def test_symlink_component_cannot_escape(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("SYMLINK_SECRET", encoding="utf-8")
        link = self.vault / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink unavailable on this host: {exc}")

        with self.assertRaises((ValueError, OSError)):
            self.paths.resolve_note_path(self.vault, "linked/secret.md")

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_junction_component_cannot_escape(self) -> None:
        outside = self.root / "outside-junction-target"
        outside.mkdir()
        (outside / "secret.md").write_text("JUNCTION_SECRET", encoding="utf-8")
        junction = self.vault / "junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation unavailable: {result.stderr.strip()}")
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)

        with self.assertRaises((ValueError, OSError)):
            self.paths.resolve_note_path(self.vault, "junction/secret.md")


class HookWiringTests(unittest.TestCase):
    def test_startup_clear_and_resume_match_session_hook(self) -> None:
        raw = json.loads(HOOKS_CONFIG.read_text(encoding="utf-8"))
        matcher = raw["hooks"]["SessionStart"][0]["matcher"]

        self.assertTrue(re.fullmatch(matcher, "startup"))
        self.assertTrue(re.fullmatch(matcher, "clear"))
        self.assertTrue(re.fullmatch(matcher, "resume"))

    def test_failed_checker_is_not_retried_with_another_python(self) -> None:
        shell = shutil.which("sh")
        git = shutil.which("git")
        if shell is None and git is not None:
            bundled_shell = Path(git).resolve().parents[1] / "bin" / "sh.exe"
            if bundled_shell.is_file():
                shell = str(bundled_shell)
        if shell is None:
            self.skipTest("POSIX shell unavailable for hook launcher")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "counter.txt"
            checker = root / "checker.py"
            checker.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "p = Path(sys.argv[1])\n"
                "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
                "raise SystemExit(7)\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [shell, str(HOOK_RUNNER), str(checker), str(counter)],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 7)
            self.assertEqual(counter.read_text(encoding="utf-8"), "x")


if __name__ == "__main__":
    unittest.main()
