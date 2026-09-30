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
_SAVED_SYS_PATH = list(sys.path)
try:
    session_hook = load_module("session_test_hook", SESSION_HOOK)
finally:
    sys.path[:] = _SAVED_SYS_PATH
HOST_CAP = session_hook.HOST_MAX_OUTPUT_CHARS
HANDOFF_HEADER = session_hook.HANDOFF_HEADER
HOT_HEADER = session_hook.HOT_HEADER
RECALL_HINT = "; for a topic run /vault-recall <topic> (Codex: recall) ...]"


def utf16_units(text: str) -> int:
    """Length as a JavaScript host counts it (supplementary characters count 2)."""
    return len(text.encode("utf-16-le")) // 2


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

    def hook_env(self, *, codex: bool, claude_project_dir: str | None) -> dict[str, str]:
        env = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith("CLAUDE_")
        }
        if not codex:
            env["CLAUDE_PROJECT_DIR"] = str(self.vault)
        if claude_project_dir is not None:
            env["CLAUDE_PROJECT_DIR"] = claude_project_dir
        return env

    def run_hook(
        self,
        *arguments: str,
        codex: bool = False,
        cwd: Path | None = None,
        claude_project_dir: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SESSION_HOOK), *arguments],
            cwd=cwd if cwd is not None else (self.vault if codex else REPO_ROOT),
            env=self.hook_env(codex=codex, claude_project_dir=claude_project_dir),
            input='{"source":"startup"}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )

    def run_hook_bytes(self, *, codex: bool = False) -> subprocess.CompletedProcess[bytes]:
        """Run the hook and keep stdout as bytes: no newline translation on read."""
        return subprocess.run(
            [sys.executable, str(SESSION_HOOK)],
            cwd=self.vault if codex else REPO_ROOT,
            env=self.hook_env(codex=codex, claude_project_dir=None),
            input=b'{"source":"startup"}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )

    def emitted(self) -> str:
        """Return the exact text the host would receive, checked for both entry points."""
        claude = self.run_hook_bytes()
        codex = self.run_hook_bytes(codex=True)

        self.assertEqual(claude.returncode, 0, claude.stderr)
        self.assertEqual(claude.stderr, b"")
        self.assertEqual(codex.returncode, 0, codex.stderr)
        self.assertEqual(codex.stdout, claude.stdout)
        return claude.stdout.decode("utf-8")

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

    def test_template_budgets_keep_english_output_under_the_host_cap(self) -> None:
        # Unbounded, these budgets emitted about 21,700 characters for English
        # notes; the host then kept only a 2,000-character preview.
        self.write_config(handoff_max_tokens=4000, hot_max_tokens=2500)
        line = "- Continue the release checklist and verify the CI matrix.\n"
        self.write("00-meta/handoff.md", "# Handoff\n\n" + line * 600)
        self.write("00-meta/hot.md", "# Hot\n\n" + line * 600)

        output = self.emitted()

        self.assertLessEqual(utf16_units(output), HOST_CAP)
        self.assertGreater(utf16_units(output), HOST_CAP - 300)
        self.assertNotIn("\r", output)
        handoff, hot = output.split("\n\n" + HOT_HEADER + "\n")
        self.assertTrue(handoff.startswith(HANDOFF_HEADER + "\n# Handoff\n"))
        self.assertTrue(handoff.endswith("[... truncated: 00-meta/handoff.md" + RECALL_HINT))
        self.assertTrue(hot.startswith("# Hot\n"))
        self.assertTrue(hot.endswith("[... truncated: 00-meta/hot.md" + RECALL_HINT + "\n"))
        self.assertGreater(len(handoff), len(hot))

    def test_korean_prose_within_budget_is_emitted_unchanged(self) -> None:
        self.write_config(handoff_max_tokens=4000, hot_max_tokens=2500)
        handoff = "## 다음 작업\n" + "오늘은 안정성 검증을 마치고 릴리스 노트를 정리한다.\n" * 40
        hot = "# 지금 상태\n" + "핵심 결정은 볼트 파일에 남기고 세션마다 다시 확인한다.\n" * 30
        self.write_bytes("00-meta/handoff.md", handoff.encode("utf-8"))
        self.write_bytes("00-meta/hot.md", hot.encode("utf-8"))

        output = self.emitted()

        self.assertEqual(
            output,
            f"{HANDOFF_HEADER}\n{handoff.strip()}\n\n{HOT_HEADER}\n{hot.strip()}\n",
        )

    def test_supplementary_characters_count_as_two_units(self) -> None:
        self.write_config(handoff_max_tokens=0, hot_max_tokens=1_000_000)
        line = "\U0001F600" * 49
        note = "\n".join([line] * 100)
        self.write_bytes("00-meta/hot.md", note.encode("utf-8"))
        # About 5,000 code points would pass a code-point count, but the host
        # counts each of these characters as two UTF-16 units.
        self.assertLess(len(f"{HOT_HEADER}\n{note}\n"), HOST_CAP)
        self.assertGreater(utf16_units(f"{HOT_HEADER}\n{note}\n"), HOST_CAP)

        output = self.emitted()

        self.assertLessEqual(utf16_units(output), HOST_CAP)
        body, _ = output.split("\n\n[... truncated", 1)
        self.assertEqual(set(body.split("\n")[1:]), {line})

    def test_crlf_and_lone_cr_notes_are_emitted_with_lf_only(self) -> None:
        self.write_config()
        self.write_bytes(
            "00-meta/handoff.md", b"first line\r\nsecond line\r\n\r\nthird\rfourth\r\n"
        )
        self.write_bytes("00-meta/hot.md", "﻿한글 줄\r\n다음 줄\r\n".encode("utf-8"))

        output = self.emitted()

        self.assertNotIn("\r", output)
        self.assertEqual(
            output,
            f"{HANDOFF_HEADER}\nfirst line\nsecond line\n\nthird\nfourth\n\n"
            f"{HOT_HEADER}\n한글 줄\n다음 줄\n",
        )

    def test_truncated_section_ends_on_a_complete_line(self) -> None:
        self.write_config(handoff_note="", hot_max_tokens=300)
        lines = [
            f"- entry {index:03d}: keep the verified decision near the top."
            for index in range(200)
        ]
        self.write_bytes("00-meta/hot.md", "\n".join(lines).encode("utf-8"))

        output = self.emitted()

        body, _ = output.split("\n\n[... truncated", 1)
        kept = body.split("\n")[1:]
        self.assertGreater(len(kept), 1)
        self.assertEqual(kept, lines[: len(kept)])
        self.assertLessEqual(healthcheck.estimate_tokens(output.rstrip("\n")), 300)

    def test_marker_names_the_note_and_omitted_headings_without_absolute_paths(self) -> None:
        titles = [f"Heading {index}" for index in range(1, 13)]
        titles[6] = "A very long heading that runs well past the forty character limit"
        text = "# Hot\n" + "".join(
            f"## {title}\n" + "Body text for this section.\n" * 8 for title in titles
        ) + "```sh\n# not a heading\n```\n"
        self.write_bytes("00-meta/hot.md", text.encode("utf-8"))
        self.write_config(handoff_note="", hot_note="00-meta\\hot.md", hot_max_tokens=400)

        output = self.emitted()

        kept = [line[3:] for line in output.split("\n") if line.startswith("## ")]
        omitted = titles[len(kept):]
        self.assertGreater(len(omitted), 5)
        shown = [
            title if len(title) <= 40 else title[:39] + "…" for title in omitted[:5]
        ]
        self.assertIn(titles[6][:39] + "…", shown)
        self.assertTrue(output.endswith(
            "\n\n[... truncated: 00-meta/hot.md; omitted headings: "
            + " | ".join(shown) + f" (+{len(omitted) - 5} more)" + RECALL_HINT + "\n"
        ))
        self.assertLessEqual(healthcheck.estimate_tokens(output.rstrip("\n")), 400)
        self.assertNotIn(str(self.root), output)
        self.assertNotIn("\\", output)
        self.assertNotIn("not a heading", output)

    def test_tiny_budget_falls_back_to_the_short_marker(self) -> None:
        self.write_config(handoff_note="", hot_max_tokens=30)
        self.write_bytes(
            "00-meta/hot.md", ("# Hot\n" + "Visible context line.\n" * 50).encode("utf-8")
        )
        # Even without any heading, the marker that names the note costs more
        # than this budget, so only the short marker can fit.
        self.assertGreater(
            healthcheck.estimate_tokens(
                f"{HOT_HEADER}\n\n\n[... truncated: 00-meta/hot.md{RECALL_HINT}"
            ),
            30,
        )

        output = self.emitted()

        self.assertTrue(output.startswith(f"{HOT_HEADER}\n# Hot\n"))
        self.assertTrue(output.endswith(session_hook.TRUNCATION_MARKER + "\n"))
        self.assertLessEqual(healthcheck.estimate_tokens(output.rstrip("\n")), 30)

    def test_long_heading_line_does_not_stall_the_hook(self) -> None:
        # A backtracking heading pattern took minutes on a heading line with a
        # long run of spaces; the subprocess timeout guards the linear parser.
        self.write_config(handoff_note="", hot_max_tokens=2000)
        self.write_bytes(
            "00-meta/hot.md", ("# a" + " " * 200_000 + "b\n## Next\ntext\n").encode("utf-8")
        )

        output = self.emitted()

        self.assertLessEqual(utf16_units(output), HOST_CAP)
        self.assertTrue(output.endswith(
            "[... truncated: 00-meta/hot.md; omitted headings: Next" + RECALL_HINT + "\n"
        ))


class ComposeContextTests(unittest.TestCase):
    """The shared composition keeps every section header under the host cap."""

    @staticmethod
    def source(header: str, text: str, budget: int) -> object:
        return session_hook.SectionSource(header, text, budget, False, "00-meta/note.md")

    def test_output_fits_the_cap_and_keeps_every_header(self) -> None:
        units = (
            "- a line of English context for the session start hook\n",
            "- 세션 시작 훅이 주입하는 한국어 문맥 줄이다\n",
            "\U0001F600" * 20 + "\n",
        )
        sizes = ((0, 400), (400, 0), (30, 400), (400, 30), (400, 400))
        budgets = ((4000, 2500), (1_000_000, 40), (40, 1_000_000), (1_000_000, 1_000_000))
        for unit in units:
            for handoff_lines, hot_lines in sizes:
                for handoff_budget, hot_budget in budgets:
                    with self.subTest(
                        unit=unit[:8], sizes=(handoff_lines, hot_lines),
                        budgets=(handoff_budget, hot_budget),
                    ):
                        sources = [
                            self.source(
                                HANDOFF_HEADER, (unit * handoff_lines).strip(), handoff_budget,
                            ) if handoff_lines else None,
                            self.source(
                                HOT_HEADER, (unit * hot_lines).strip(), hot_budget,
                            ) if hot_lines else None,
                        ]

                        context = session_hook.compose_context(sources)

                        self.assertLessEqual(utf16_units(context.stdout), HOST_CAP)
                        for source, section in zip(sources, context.sections):
                            if source is None:
                                self.assertIsNone(section)
                                continue
                            self.assertTrue(section.text.startswith(source.header + "\n"))
                            self.assertLessEqual(
                                healthcheck.estimate_tokens(section.text), source.token_budget,
                            )

    def test_short_section_is_kept_whole_and_passes_its_room_on(self) -> None:
        text = "\n".join(f"- line {index:04d} of the hot note" for index in range(2000))
        sources = [
            self.source(HANDOFF_HEADER, "Short handoff.", 4000),
            self.source(HOT_HEADER, text, 10_000),
        ]

        context = session_hook.compose_context(sources)

        handoff, hot = context.sections
        self.assertEqual(handoff, (f"{HANDOFF_HEADER}\nShort handoff.", False, False))
        self.assertTrue(hot.truncated)
        self.assertTrue(hot.host_capped)
        self.assertLessEqual(utf16_units(context.stdout), HOST_CAP)
        # The hot section may use all the room the short handoff left.
        self.assertGreater(utf16_units(hot.text), HOST_CAP - utf16_units(handoff.text) - 60)

    def test_extreme_budget_ratio_still_keeps_the_small_section_header(self) -> None:
        text = "\n".join(f"- line {index:04d} of context" for index in range(3000))
        sources = [
            self.source(HANDOFF_HEADER, text, 1_000_000),
            self.source(HOT_HEADER, text, 40),
        ]

        context = session_hook.compose_context(sources)

        handoff, hot = context.sections
        self.assertEqual(hot.text, f"{HOT_HEADER}\n{session_hook.TRUNCATION_MARKER}")
        self.assertTrue(hot.host_capped)
        self.assertTrue(handoff.host_capped)
        self.assertLessEqual(utf16_units(context.stdout), HOST_CAP)

    def test_heading_index_skips_fenced_code_and_non_headings(self) -> None:
        text = "\n".join((
            "# Title ##", "#hashtag", "####### seven", "    # indented code",
            "```sh", "# shell comment", "```", "## Kept", "~~~", "# tilde fenced",
            "~~~~", "### C#", "## ###",
        ))

        headings = session_hook._heading_index(text)

        self.assertEqual([title for _, title in headings], ["Title", "Kept", "C#"])
        for start, title in headings:
            self.assertTrue(text[start:].lstrip("# ").startswith(title))

    def test_output_within_the_cap_is_not_rendered_again(self) -> None:
        sources = [
            self.source(HANDOFF_HEADER, "Continue the checklist.", 4000),
            None,
        ]

        context = session_hook.compose_context(sources)

        self.assertEqual(
            context.sections,
            ((f"{HANDOFF_HEADER}\nContinue the checklist.", False, False), None),
        )
        self.assertEqual(context.stdout, f"{HANDOFF_HEADER}\nContinue the checklist.\n")
        self.assertEqual(session_hook.compose_context([None, None]).stdout, "")


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
