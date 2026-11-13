"""Antigravity CLI mode of the session hooks (v0.48.0, items 1 and 2).

Antigravity CLI runs a plugin's hook commands with the plugin folder as the
working directory and writes one camelCase JSON event to stdin. The hooks
enter that mode only through ``--host antigravity``; nothing in the input
switches it on. SessionStart answers with one ``injectSteps`` system message
that carries the text the default hook prints for the same vault, Stop
records a checkpoint when the conversation is fully idle, and both always
answer with a JSON object and exit 0.

The generated package registers three handlers per event (the sh wrapper,
``py -3`` and ``python``), so a claim file in the temporary folder lets one of
them act. A claim holds its key for 120 seconds; after that the key is
processed again. On POSIX, where the temporary folder is shared, the claim
folder is per user and private. Every subprocess here gets its own TMP, TEMP
and TMPDIR, so the claims of one test never meet those of another. The
subprocess runs use a copy of the hooks and engine scripts and run it from its
own folder, the way Antigravity CLI runs an installed plugin.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import make_junction


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HELPERS = HOOKS / "antigravity_hook.py"
RUNTIME = "00-meta/.agentic-vault/runtime"
CLAIMS = "agentic-vault-hook-claims"
HANDOFF_HEADER = "=== SESSION HANDOFF (직전 세션 인계) ==="
SEVERAL = "agentic-vault: several vault candidates; nothing injected"
START_UNAVAILABLE = "agentic-vault: session start unavailable ({})"
CHECKPOINT_UNAVAILABLE = "agentic-vault: checkpoint unavailable ({})"
EMPTY = b"{}\n"
ABSENT = object()


def load(name: str, path: Path):
    """Load a hook file as a module and leave sys.path as it was."""
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - import guard
            raise RuntimeError(f"cannot load {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def write(root: Path, relative: str, data: str | bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def make_vault(root: Path, *, handoff: str = "Continue the release checklist.",
               hot: str = "오늘은 안정성 검증을 완료한다.", **config: object) -> Path:
    settings: dict[str, object] = {
        "deny_zones": ["90-assets"], "exclude_dirs": [RUNTIME],
        "handoff_note": "00-meta/handoff.md", "hot_note": "00-meta/hot.md", "log_note": "00-meta/log.md",
    }
    settings.update(config)
    write(root, "00-meta/vault-config.json", json.dumps(settings, ensure_ascii=False))
    write(root, "00-meta/handoff.md", handoff)
    write(root, "00-meta/hot.md", hot)
    write(root, "00-meta/log.md", "- 2026-11-13 10:00 | tester | [build] entry\n")
    return root


def encode(event: dict, fields: dict) -> bytes:
    merged = {**event, **fields}
    return json.dumps({key: value for key, value in merged.items() if value is not ABSENT},
                      ensure_ascii=False).encode("utf-8")


def claim_name(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:40]


def claims_folder(temp: Path) -> Path:
    """The claim folder under temp: per user on POSIX, whose /tmp is shared; the plain name on Windows."""
    return temp / (f"{CLAIMS}-{os.getuid()}" if hasattr(os, "getuid") else CLAIMS)


def set_age(path: Path, seconds: float) -> None:
    """Set path's times to `seconds` ago (a negative age is a time ahead of the clock)."""
    moment = time.time() - seconds
    os.utime(path, (moment, moment))


def sweepable(name: str) -> bool:
    """A name the claim sweep removes after two days: 40 lowercase hex characters."""
    return len(name) == 40 and set(name) <= set("0123456789abcdef")


def alias_folder(link: Path, target: Path) -> None:
    """A second spelling of target: a junction on Windows, a symbolic link elsewhere."""
    if os.name == "nt":
        make_junction(link, target)
    else:
        os.symlink(target, link, target_is_directory=True)


class AntigravityHookCase(unittest.TestCase):
    """Runs a copy of the plugin from its own folder, as Antigravity CLI does."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._plugin_tmp = tempfile.TemporaryDirectory(prefix="agy plugin ")
        cls.plugin = Path(cls._plugin_tmp.name).resolve() / "agentic-vault"
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(HOOKS, cls.plugin / "hooks", ignore=ignore)
        shutil.copytree(SCRIPTS, cls.plugin / "skills" / "agentic-vault" / "scripts", ignore=ignore)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._plugin_tmp.cleanup()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="agy hook ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.temp = self.base / "temp"
        self.temp.mkdir()
        self.vault = make_vault(self.base / "작업 vault")

    def env(self, **extra: str) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("CLAUDE_")}
        env.update({"TMP": str(self.temp), "TEMP": str(self.temp), "TMPDIR": str(self.temp)})
        env.update(extra)
        return env

    def run_hook(self, name: str, *arguments: str, stdin: bytes = b"", cwd: Path | None = None,
                 env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
        # From the plugin folder the command names the hook relatively, as the generated
        # hooks.json does; from any other folder it names the copy absolutely.
        script = f"hooks/{name}" if cwd is None else str(self.plugin / "hooks" / name)
        return subprocess.run([sys.executable, script, *arguments], input=stdin,
                              cwd=self.plugin if cwd is None else cwd, env=env or self.env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False)

    def start(self, stdin: bytes, *arguments: str, **options) -> subprocess.CompletedProcess[bytes]:
        return self.run_hook("session_start.py", "--host", "antigravity", *arguments, stdin=stdin, **options)

    def stop(self, stdin: bytes, *arguments: str, **options) -> subprocess.CompletedProcess[bytes]:
        return self.run_hook("session_checkpoint.py", "--host", "antigravity", *arguments, stdin=stdin, **options)

    @staticmethod
    def stderr_text(result: subprocess.CompletedProcess[bytes]) -> str:
        return result.stderr.decode("utf-8").replace("\r\n", "\n")

    def start_event(self, **fields: object) -> bytes:
        return encode({
            "conversationId": "conversation-start", "workspacePaths": [str(self.vault)],
            "transcriptPath": str(self.base / "TRANSCRIPT-SECRET.jsonl"),
            "artifactDirectoryPath": str(self.base / "artifacts"), "modelName": "test-model",
            "lastUserInput": "LAST-INPUT-SECRET", "hookEventName": "SessionStart",
        }, fields)

    def stop_event(self, **fields: object) -> bytes:
        return encode({
            "conversationId": "conversation-stop", "workspacePaths": [str(self.vault)], "cwd": str(self.vault),
            "transcriptPath": str(self.base / "TRANSCRIPT-SECRET.jsonl"),
            "artifactDirectoryPath": str(self.base / "artifacts"), "modelName": "test-model",
            "lastUserInput": "LAST-INPUT-SECRET", "hookEventName": "Stop", "executionNum": 1,
            "terminationReason": "completed", "fullyIdle": True,
        }, fields)

    def injected(self, result: subprocess.CompletedProcess[bytes]) -> str:
        """The system message text of an injecting answer, after checking its exact shape."""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"\n"), 1, result.stdout[:200])
        self.assertTrue(result.stdout.endswith(b"\n"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(list(payload), ["injectSteps"])
        self.assertEqual(len(payload["injectSteps"]), 1)
        step = payload["injectSteps"][0]
        self.assertEqual(list(step), ["systemMessage"])
        message = step["systemMessage"]
        self.assertEqual(list(message), ["systemMessage", "metadata"])
        self.assertEqual(message["metadata"], {"source": "agentic-vault"})
        self.assertIsInstance(message["systemMessage"], str)
        return message["systemMessage"]

    def claims(self) -> list[str]:
        folder = claims_folder(self.temp)
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    def age_claim(self, *parts: str, seconds: float) -> None:
        set_age(claims_folder(self.temp) / claim_name(*parts), seconds)

    def run_unread(self, name: str, raw: bytes) -> tuple[int, bytes]:
        """Run a hook whose host closed its end of stdout before the answer; return (exit code, stderr)."""
        worker = subprocess.Popen([sys.executable, f"hooks/{name}", "--host", "antigravity"], cwd=self.plugin,
                                  env=self.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        worker.stdout.close()
        worker.stdout = None  # communicate() must not read the pipe closed above
        _, stderr = worker.communicate(raw, timeout=60)
        return worker.returncode, stderr

    @staticmethod
    def together(workers: list[subprocess.Popen], raw: bytes) -> list[tuple[bytes, bytes]]:
        """Give every worker its event at the same time, as when Antigravity CLI starts all handlers."""
        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            return list(pool.map(lambda worker: worker.communicate(raw, timeout=60), workers))

    def records(self, vault: Path | None = None) -> list[Path]:
        return sorted(((vault or self.vault) / RUNTIME).glob("checkpoint-*.json"))


class AntigravitySessionStartTests(AntigravityHookCase):
    def test_workspace_vault_is_injected_as_one_system_message(self) -> None:
        # The workspace is the vault; no CLAUDE_PROJECT_DIR; the process runs in the plugin folder.
        result = self.start(self.start_event())

        text = self.injected(result)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(text.startswith(HANDOFF_HEADER + "\n"))
        self.assertIn("Continue the release checklist.", text)
        self.assertIn("오늘은 안정성 검증을 완료한다.", text)
        # UTF-8 as is on stdout, not \\u escapes, and nothing from the event comes back.
        self.assertIn("오늘은 안정성 검증을 완료한다.".encode("utf-8"), result.stdout)
        for secret in (b"LAST-INPUT-SECRET", b"TRANSCRIPT-SECRET"):
            self.assertNotIn(secret, result.stdout)

    def test_system_message_is_the_default_output_for_the_same_vault_and_input(self) -> None:
        long_handoff = "## Context\n" + "history line\n" * 6000
        long_hot = "현재 상황 " * 9000
        cases = (
            ("short notes", {}, {}),
            ("notes cut by the host cap", {"handoff": long_handoff, "hot": long_hot}, {}),
            ("event cwd only", {}, {"workspacePaths": ABSENT}),
        )
        texts = {}
        for label, notes, fields in cases:
            with self.subTest(case=label):
                vault = make_vault(self.base / label, **notes)
                workspace = {"workspacePaths": [str(vault)], **fields}
                raw = self.start_event(conversationId=label, cwd=str(vault), **workspace)
                default = self.run_hook("session_start.py", "--vault", str(vault), stdin=raw)
                self.assertEqual((default.returncode, default.stderr), (0, b""))
                self.assertTrue(default.stdout.startswith(HANDOFF_HEADER.encode("utf-8")))

                texts[label] = self.injected(self.start(raw))

                # The same string, final line feed included: what Claude Code receives on stdout.
                self.assertEqual(texts[label], default.stdout.decode("utf-8"))
        self.assertIn("truncated", texts["notes cut by the host cap"])

    def test_antigravity_mode_always_renders_the_startup_context(self) -> None:
        # Antigravity CLI has no compaction event: a source field never selects the compact block.
        startup = self.run_hook("session_start.py", "--vault", str(self.vault), stdin=b'{"source":"startup"}')

        text = self.injected(self.start(self.start_event(source="compact")))

        self.assertEqual(text, startup.stdout.decode("utf-8"))
        self.assertNotIn("FIXED CONSTRAINTS", text)

    def test_no_vault_or_unusable_input_answers_an_empty_object(self) -> None:
        plain = self.base / "plain folder"
        plain.mkdir()
        directory_config = self.base / "directory config"
        (directory_config / "00-meta" / "vault-config.json").mkdir(parents=True)
        cases = {
            "non-vault workspace": self.start_event(workspacePaths=[str(plain)], cwd=str(plain)),
            "config is a folder": self.start_event(workspacePaths=[str(directory_config)]),
            "no workspace fields": self.start_event(workspacePaths=ABSENT),
            "workspace string": self.start_event(workspacePaths=str(self.vault)),
            "workspace list with a number": self.start_event(workspacePaths=[str(self.vault), 3]),
            "cwd not a string": self.start_event(workspacePaths=ABSENT, cwd=[str(self.vault)]),
            "broken json": b'{"conversationId": "x", "workspacePaths": [',
            "empty": b"",
            "whitespace": b" \n\t ",
            "array": json.dumps([str(self.vault)]).encode("utf-8"),
            "not utf-8": b'{"workspacePaths": ["\xff"]}',
            "duplicate key": ('{"workspacePaths": [], "workspacePaths": ' + json.dumps([str(self.vault)])
                              + "}").encode("utf-8"),
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                result = self.start(raw)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        self.assertEqual(self.claims(), [])

    def test_process_folder_and_claude_project_dir_are_never_used(self) -> None:
        bare = self.start_event(workspacePaths=ABSENT)
        relative = self.start_event(workspacePaths=[self.vault.name], cwd=self.vault.name)
        cases = (
            ("process runs in the vault", bare, {"cwd": self.vault}),
            ("CLAUDE_PROJECT_DIR names the vault", bare, {"env": self.env(CLAUDE_PROJECT_DIR=str(self.vault))}),
            ("relative workspace beside the process folder", relative, {"cwd": self.base}),
        )
        for label, raw, options in cases:
            with self.subTest(case=label):
                result = self.start(raw, **options)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))

    def test_two_vault_candidates_inject_nothing_and_name_no_path(self) -> None:
        other = make_vault(self.base / "다른 vault", handoff="OTHER-VAULT-HANDOFF")

        for label, fields in (("cwd and workspace", {"cwd": str(self.vault), "workspacePaths": [str(other)]}),
                              ("two workspaces", {"workspacePaths": [str(self.vault), str(other)]})):
            with self.subTest(case=label):
                result = self.start(self.start_event(**fields))
                self.assertEqual((result.returncode, result.stdout), (0, EMPTY))
                self.assertEqual(self.stderr_text(result), SEVERAL + "\n")
        self.assertEqual(self.claims(), [])

    def test_one_vault_spelled_twice_is_one_candidate(self) -> None:
        spellings = [str(self.vault) + os.sep]
        if os.name == "nt":
            spellings.append(str(self.vault).upper())
        alias = self.base / "vault alias"
        try:
            alias_folder(alias, self.vault)
        except (OSError, NotImplementedError):
            pass
        else:
            spellings.append(str(alias))

        text = self.injected(self.start(self.start_event(cwd=str(self.vault), workspacePaths=spellings)))

        self.assertIn("Continue the release checklist.", text)

    def test_explicit_vault_is_the_only_candidate(self) -> None:
        other = make_vault(self.base / "explicit vault", handoff="EXPLICIT-HANDOFF")
        plain = self.base / "plain"
        plain.mkdir()

        chosen = self.injected(self.start(self.start_event(conversationId="c-explicit"), "--vault", str(other)))
        self.assertIn("EXPLICIT-HANDOFF", chosen)
        for label, value in (("non-vault", str(plain)), ("relative", other.name)):
            with self.subTest(explicit=label):
                result = self.start(self.start_event(conversationId="c-" + label), "--vault", value,
                                    cwd=self.base)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))

    def test_second_call_for_the_same_conversation_answers_an_empty_object(self) -> None:
        first = self.start(self.start_event(conversationId="conversation-claim"))
        second = self.start(self.start_event(conversationId="conversation-claim"))
        other = self.start(self.start_event(conversationId="conversation-other"))

        self.injected(first)
        self.assertEqual((second.returncode, second.stdout, second.stderr), (0, EMPTY, b""))
        self.injected(other)
        self.assertEqual(self.claims(), sorted([claim_name("SessionStart", "conversation-claim"),
                                                claim_name("SessionStart", "conversation-other")]))

    def test_three_handlers_for_one_event_inject_once(self) -> None:
        raw = self.start_event(conversationId="conversation-three")
        workers = [subprocess.Popen([sys.executable, "hooks/session_start.py", "--host", "antigravity"],
                                    cwd=self.plugin, env=self.env(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        outputs = [worker.communicate(raw, timeout=60) for worker in workers]

        self.assertEqual([worker.returncode for worker in workers], [0, 0, 0])
        self.assertEqual([error for _, error in outputs], [b"", b"", b""])
        self.assertEqual(sorted(output == EMPTY for output, _ in outputs), [False, True, True])
        self.assertEqual(sum(b"injectSteps" in output for output, _ in outputs), 1)

    def test_conversation_is_injected_again_once_its_claim_is_older_than_120_seconds(self) -> None:
        raw = self.start_event(conversationId="conversation-reopened")
        self.injected(self.start(raw))
        self.age_claim("SessionStart", "conversation-reopened", seconds=100)
        within = self.start(raw)

        self.age_claim("SessionStart", "conversation-reopened", seconds=130)
        again = self.start(raw)
        renewed = self.start(raw)

        # Within 120 seconds the same conversation is not injected again; after that it is, once.
        self.assertEqual((within.returncode, within.stdout, within.stderr), (0, EMPTY, b""))
        self.assertIn("Continue the release checklist.", self.injected(again))
        self.assertEqual(again.stderr, b"")
        self.assertEqual((renewed.returncode, renewed.stdout, renewed.stderr), (0, EMPTY, b""))
        self.assertIn(claim_name("SessionStart", "conversation-reopened"), self.claims())
        self.assertTrue(all(sweepable(name) for name in self.claims()))

    def test_three_handlers_for_a_reopened_conversation_inject_once(self) -> None:
        raw = self.start_event(conversationId="conversation-three-again")
        self.injected(self.start(raw))
        self.age_claim("SessionStart", "conversation-three-again", seconds=600)
        workers = [subprocess.Popen([sys.executable, "hooks/session_start.py", "--host", "antigravity"],
                                    cwd=self.plugin, env=self.env(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]

        outputs = self.together(workers, raw)

        self.assertEqual([worker.returncode for worker in workers], [0, 0, 0])
        self.assertEqual([error for _, error in outputs], [b"", b"", b""])
        self.assertEqual(sum(b"injectSteps" in output for output, _ in outputs), 1)
        self.assertEqual(sum(output == EMPTY for output, _ in outputs), 2)

    def test_event_without_a_conversation_id_is_not_claimed(self) -> None:
        for _ in range(2):
            self.injected(self.start(self.start_event(conversationId=ABSENT)))
        self.assertEqual(self.claims(), [])

    def test_last_user_input_of_100_kib_is_read_and_never_echoed(self) -> None:
        sentinel = "LAST-USER-INPUT-SENTINEL"
        raw = self.start_event(lastUserInput=sentinel + "가나다 abc " * 10_000)
        # More than the 64 KiB event bound of the default Claude Code path.
        self.assertGreater(len(raw), 100 * 1024)

        result = self.start(raw)

        text = self.injected(result)
        self.assertEqual(result.stderr, b"")
        self.assertIn("Continue the release checklist.", text)
        self.assertNotIn(sentinel.encode("utf-8"), result.stdout)

    def test_unsafe_configuration_answers_an_empty_object_with_a_fixed_code(self) -> None:
        write(self.base, "outside.md", "OUTSIDE-SECRET")
        write(self.vault, "90-assets/hot.md", "DENIED-SECRET")
        cases = {
            "malformed": '{"hot_note": "CONFIG-SECRET"',
            "traversal": {"handoff_note": "../outside.md"},
            "deny zone": {"hot_note": "90-assets/hot.md"},
            "negative budget": {"hot_max_tokens": -1},
        }
        for index, (label, config) in enumerate(cases.items()):
            with self.subTest(case=label):
                if isinstance(config, str):
                    write(self.vault, "00-meta/vault-config.json", config)
                else:
                    write(self.vault, "00-meta/vault-config.json", json.dumps({
                        "deny_zones": ["90-assets"], "handoff_note": "00-meta/handoff.md",
                        "hot_note": "00-meta/hot.md", **config}))
                result = self.start(self.start_event(conversationId=f"conversation-{index}"))
                self.assertEqual((result.returncode, result.stdout), (0, EMPTY))
                stderr = self.stderr_text(result)
                self.assertEqual(stderr, START_UNAVAILABLE.format("invalid_configuration") + "\n")
                for secret in ("SECRET", str(self.base), self.vault.name):
                    self.assertNotIn(secret, stderr)

    def test_empty_context_answers_an_empty_object(self) -> None:
        write(self.vault, "00-meta/vault-config.json", json.dumps({
            "deny_zones": ["90-assets"], "handoff_note": "00-meta/handoff.md", "hot_note": "00-meta/hot.md",
            "handoff_max_tokens": 0, "hot_max_tokens": 0}))

        result = self.start(self.start_event())

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))


class DefaultModeTests(AntigravityHookCase):
    """Without --host antigravity the hooks keep their Claude Code and Codex behaviour."""

    def test_session_start_prints_plain_text_for_claude_and_antigravity_shaped_input(self) -> None:
        env = self.env(CLAUDE_PROJECT_DIR=str(self.vault))
        startup = self.run_hook("session_start.py", stdin=b'{"source":"startup"}', env=env)
        self.assertEqual((startup.returncode, startup.stderr), (0, b""))
        self.assertTrue(startup.stdout.startswith(HANDOFF_HEADER.encode("utf-8")))
        claude = json.dumps({"session_id": "claude-session", "cwd": str(self.vault),
                             "hook_event_name": "SessionStart", "source": "startup",
                             "transcript_path": str(self.base / "t.jsonl")}).encode("utf-8")
        cases = (("claude input", (), claude),
                 ("claude input, --host claude", ("--host", "claude"), claude),
                 ("antigravity-shaped input", (), self.start_event()))
        for label, arguments, raw in cases:
            with self.subTest(case=label):
                result = self.run_hook("session_start.py", *arguments, stdin=raw, env=env)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, startup.stdout, b""))
        self.assertEqual(self.claims(), [])

    def test_checkpoint_events_of_the_default_mode_are_unchanged(self) -> None:
        env = self.env(CLAUDE_PROJECT_DIR=str(self.vault))
        for event in ("PreCompact", "SessionEnd"):
            with self.subTest(event=event):
                raw = json.dumps({"hook_event_name": event, "session_id": "claude-session",
                                  "cwd": str(self.vault)}).encode("utf-8")
                result = self.run_hook("session_checkpoint.py", "--event", event, stdin=raw, env=env)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        [record] = self.records()
        saved = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual([item["event"] for item in saved["events"]], ["PreCompact", "SessionEnd"])
        self.assertEqual(saved["session_sha256"], hashlib.sha256(b"claude-session").hexdigest())

    def test_checkpoint_does_not_take_an_antigravity_stop_without_the_flag(self) -> None:
        env = self.env(CLAUDE_PROJECT_DIR=str(self.vault))
        for arguments in ((), ("--host", "claude")):
            with self.subTest(arguments=arguments):
                result = self.run_hook("session_checkpoint.py", *arguments, stdin=self.stop_event(), env=env)
                self.assertEqual((result.returncode, result.stdout), (0, b""))
                self.assertEqual(self.stderr_text(result), "agentic-vault: invalid checkpoint event\n")
        self.assertEqual(self.records(), [])


class AntigravityStopTests(AntigravityHookCase):
    def test_fully_idle_stop_records_a_checkpoint_and_answers_an_empty_object(self) -> None:
        result = self.stop(self.stop_event())

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        [record] = self.records()
        raw = record.read_text(encoding="utf-8")
        saved = json.loads(raw)
        self.assertEqual([item["event"] for item in saved["events"]], ["Stop"])
        self.assertEqual(saved["session_sha256"], hashlib.sha256(b"conversation-stop").hexdigest())
        self.assertEqual(record.name, "checkpoint-" + saved["session_sha256"][:32] + ".json")
        self.assertEqual(saved["pending_references"], [])
        self.assertEqual(sorted(source["path"] for source in saved["sources"]),
                         ["00-meta/handoff.md", "00-meta/hot.md", "00-meta/log.md"])
        readable = json.dumps(saved, ensure_ascii=False)
        for forbidden in ("LAST-INPUT-SECRET", "TRANSCRIPT-SECRET", "conversation-stop", "artifacts",
                          str(self.base), self.base.as_posix(), self.vault.name):
            self.assertNotIn(forbidden, readable)

    def test_stop_that_is_not_fully_idle_or_not_identified_records_nothing(self) -> None:
        cases = {
            "fullyIdle false": {"fullyIdle": False},
            "fullyIdle absent": {"fullyIdle": ABSENT},
            "fullyIdle string": {"fullyIdle": "true"},
            "fullyIdle one": {"fullyIdle": 1},
            "another event": {"hookEventName": "SessionStart"},
            "event null": {"hookEventName": None},
            "no conversation": {"conversationId": ABSENT},
            "empty conversation": {"conversationId": ""},
            "long conversation": {"conversationId": "c" * 129},
            "control character": {"conversationId": "conversation\n1"},
            "number conversation": {"conversationId": 7},
        }
        for label, fields in cases.items():
            with self.subTest(case=label):
                result = self.stop(self.stop_event(**fields))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        for label, raw in (("broken", b'{"fullyIdle": true,'), ("empty", b""), ("array", b"[]")):
            with self.subTest(case=label):
                result = self.stop(raw)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        self.assertEqual(self.records(), [])
        self.assertEqual(self.claims(), [])

    def test_absent_event_name_and_longest_id_still_count_as_a_stop(self) -> None:
        longest = "c" * 128
        result = self.stop(self.stop_event(hookEventName=ABSENT, conversationId=longest))

        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        [record] = self.records()
        saved = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(saved["session_sha256"], hashlib.sha256(longest.encode("utf-8")).hexdigest())

    def test_same_execution_number_records_once(self) -> None:
        for number in (3, 3, 4):
            result = self.stop(self.stop_event(executionNum=number))
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))

        [record] = self.records()
        saved = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual([item["event"] for item in saved["events"]], ["Stop", "Stop"])
        self.assertEqual(self.claims(), sorted([claim_name("Stop", "conversation-stop", "3"),
                                                claim_name("Stop", "conversation-stop", "4")]))

    def test_stop_without_an_integer_execution_number_is_not_claimed(self) -> None:
        for value in (ABSENT, "5", True, 5.0):
            with self.subTest(executionNum=value):
                result = self.stop(self.stop_event(executionNum=value))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        [record] = self.records()
        saved = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["events"]), 4)
        self.assertEqual(self.claims(), [])

    def test_three_handlers_for_one_stop_record_once(self) -> None:
        raw = self.stop_event(executionNum=9)
        workers = [subprocess.Popen([sys.executable, "hooks/session_checkpoint.py", "--host", "antigravity"],
                                    cwd=self.plugin, env=self.env(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        outputs = [worker.communicate(raw, timeout=60) for worker in workers]

        self.assertEqual([worker.returncode for worker in workers], [0, 0, 0])
        self.assertEqual(outputs, [(EMPTY, b"")] * 3)
        [record] = self.records()
        self.assertEqual(len(json.loads(record.read_text(encoding="utf-8"))["events"]), 1)

    def test_execution_number_is_recorded_again_once_its_claim_is_older_than_120_seconds(self) -> None:
        raw = self.stop_event(executionNum=5)
        results = [self.stop(raw), self.stop(raw)]
        self.age_claim("Stop", "conversation-stop", "5", seconds=100)
        results.append(self.stop(raw))
        [record] = self.records()
        held = len(json.loads(record.read_text(encoding="utf-8"))["events"])

        self.age_claim("Stop", "conversation-stop", "5", seconds=130)
        results += [self.stop(raw), self.stop(raw)]

        self.assertEqual([(result.returncode, result.stdout, result.stderr) for result in results],
                         [(0, EMPTY, b"")] * 5)
        # One write within 120 seconds; the reused executionNum is written again after that, once.
        self.assertEqual(held, 1)
        saved = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual([item["event"] for item in saved["events"]], ["Stop", "Stop"])

    def test_three_handlers_for_a_stale_stop_claim_record_once(self) -> None:
        raw = self.stop_event(executionNum=12)
        self.assertEqual(self.stop(raw).returncode, 0)
        self.age_claim("Stop", "conversation-stop", "12", seconds=600)
        workers = [subprocess.Popen([sys.executable, "hooks/session_checkpoint.py", "--host", "antigravity"],
                                    cwd=self.plugin, env=self.env(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]

        outputs = self.together(workers, raw)

        self.assertEqual([worker.returncode for worker in workers], [0, 0, 0])
        self.assertEqual(outputs, [(EMPTY, b"")] * 3)
        [record] = self.records()
        self.assertEqual(len(json.loads(record.read_text(encoding="utf-8"))["events"]), 2)

    def test_no_vault_or_several_vaults_record_nothing(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        other = make_vault(self.base / "other vault")
        cases = {
            "non-vault": {"workspacePaths": [str(plain)], "cwd": str(plain)},
            "relative": {"workspacePaths": [self.vault.name], "cwd": ABSENT},
            "several": {"workspacePaths": [str(self.vault), str(other)]},
        }
        for label, fields in cases.items():
            with self.subTest(case=label):
                result = self.stop(self.stop_event(**fields))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, EMPTY, b""))
        self.assertEqual((self.records(), self.records(other), list(plain.iterdir())), ([], [], []))
        self.assertEqual(self.claims(), [])

    def test_checkpoint_failure_prints_the_fixed_code_line(self) -> None:
        write(self.vault, "00-meta/vault-config.json",
              json.dumps({"deny_zones": ["private"], "hot_note": "private/no.md"}))
        write(self.vault, "private/no.md", "PRIVATE-NOTE")

        result = self.stop(self.stop_event())

        self.assertEqual((result.returncode, result.stdout), (0, EMPTY))
        stderr = self.stderr_text(result)
        self.assertRegex(stderr, r"\Aagentic-vault: checkpoint unavailable \([a-z][a-z0-9_]{0,63}\)\n\Z")
        for secret in ("PRIVATE-NOTE", str(self.base), "private/no.md"):
            self.assertNotIn(secret, stderr)
        self.assertEqual(self.records(), [])


class ClosedOutputTests(AntigravityHookCase):
    """A host that stops reading before the answer: exit code 0 and nothing on stderr."""

    def test_closed_stdout_exits_0_without_a_traceback(self) -> None:
        # More than one 8 KiB stdout buffer (the write itself fails) and a short answer (the flush fails).
        long_vault = make_vault(self.base / "long vault", handoff="## Context\n" + "history line\n" * 6000)
        cases = (
            ("long injection", "session_start.py",
             self.start_event(conversationId="closed-long", workspacePaths=[str(long_vault)])),
            ("short injection", "session_start.py", self.start_event(conversationId="closed-short")),
            ("empty object", "session_start.py", self.start_event(workspacePaths=ABSENT)),
            ("stop", "session_checkpoint.py", self.stop_event(executionNum=11)),
        )
        for label, name, raw in cases:
            with self.subTest(case=label):
                # A traceback would name the plugin folder; an empty stderr names no path at all.
                self.assertEqual(self.run_unread(name, raw), (0, b""))
        # Only the answers were lost: the claims were made and the checkpoint was written.
        self.assertEqual(self.claims(), sorted([claim_name("SessionStart", "closed-long"),
                                                claim_name("SessionStart", "closed-short"),
                                                claim_name("Stop", "conversation-stop", "11")]))
        [record] = self.records()
        self.assertEqual([item["event"] for item in json.loads(record.read_text(encoding="utf-8"))["events"]],
                         ["Stop"])


class AntigravityHelperTests(unittest.TestCase):
    """hooks/antigravity_hook.py, the helpers both hooks use in Antigravity mode."""

    def setUp(self) -> None:
        self.assertTrue(HELPERS.is_file(), "hooks/antigravity_hook.py is missing")
        self.helpers = load("antigravity_hook_under_test", HELPERS)
        temporary = tempfile.TemporaryDirectory(prefix="agy helpers ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.temp = self.base / "temp"
        self.temp.mkdir()
        tempdir = mock.patch.object(tempfile, "tempdir", str(self.temp))
        tempdir.start()
        self.addCleanup(tempdir.stop)

    def test_event_bound_is_16_mib(self) -> None:
        self.assertEqual(self.helpers.MAX_EVENT_BYTES, 16 * 1024 * 1024)

    def test_read_event_returns_objects_and_none_for_everything_else(self) -> None:
        read = self.helpers.read_event
        self.assertEqual(read(io.BytesIO(b'{"conversationId": "c", "n": 1.5}')), {"conversationId": "c", "n": 1.5})
        self.assertEqual(read(io.BytesIO(b'\xef\xbb\xbf{"a": "\xea\xb0\x80"}')), {"a": "가"})
        self.assertEqual(read(io.StringIO('{"a": "가"}')), {"a": "가"})
        for label, raw in {"empty": b"", "whitespace": b"  \n", "broken": b'{"a": ', "array": b"[1]",
                           "string": b'"text"', "null": b"null", "not utf-8": b'{"a": "\xff"}',
                           "utf-16": '{"a": 1}'.encode("utf-16"), "duplicate": b'{"a": 1, "a": 2}',
                           "NaN": b'{"a": NaN}', "Infinity": b'{"a": -Infinity}',
                           "deep": b"[" * 200_000}.items():
            with self.subTest(case=label):
                self.assertIsNone(read(io.BytesIO(raw)))

    def test_read_event_bound_and_terminal_input(self) -> None:
        with mock.patch.object(self.helpers, "MAX_EVENT_BYTES", 8):
            self.assertEqual(self.helpers.read_event(io.BytesIO(b'{"a":12}')), {"a": 12})
            self.assertIsNone(self.helpers.read_event(io.BytesIO(b'{"a":123}')))

        class Terminal(io.BytesIO):
            def isatty(self) -> bool:
                return True

        terminal = Terminal(b'{"a": 1}')
        self.assertIsNone(self.helpers.read_event(terminal))
        self.assertEqual(terminal.tell(), 0)

        class Failing(io.BytesIO):
            def read(self, *args):
                raise OSError("SECRET read failure")

        self.assertIsNone(self.helpers.read_event(Failing()))
        with mock.patch.object(sys, "stdin", None):
            self.assertIsNone(self.helpers.read_event())
        with mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"b": 2}'), encoding="utf-8")):
            self.assertEqual(self.helpers.read_event(), {"b": 2})

    def test_resolve_vault_takes_one_absolute_vault_from_the_event(self) -> None:
        first = make_vault(self.base / "first")
        second = make_vault(self.base / "second")
        plain = self.base / "plain"
        plain.mkdir()
        resolve = self.helpers.resolve_vault

        self.assertEqual(resolve(None, {"workspacePaths": [str(first)]}), (first, "one_vault"))
        self.assertEqual(resolve(None, {"cwd": str(first), "workspacePaths": [str(plain)]}), (first, "one_vault"))
        self.assertEqual(resolve(None, {"cwd": str(plain), "workspacePaths": [str(plain), str(second)]}),
                         (second, "one_vault"))
        self.assertEqual(resolve(None, {"cwd": str(first), "workspacePaths": [str(second)]}),
                         (None, "several_vaults"))
        self.assertEqual(resolve(None, {"workspacePaths": [str(first), str(first) + os.sep]}), (first, "one_vault"))
        for label, event in {"none": None, "list": [str(first)], "empty": {},
                             "plain": {"cwd": str(plain), "workspacePaths": [str(plain)]},
                             "relative": {"cwd": "first", "workspacePaths": ["first", "./second"]},
                             "drive relative": {"workspacePaths": [str(first)[:2] + "first"]},
                             "rooted without drive": {"workspacePaths": [os.sep + "first"]},
                             "workspace string": {"workspacePaths": str(first)},
                             "workspace with a number": {"workspacePaths": [str(first), 1]},
                             "nul": {"workspacePaths": [str(first) + "\0x"]}}.items():
            with self.subTest(event=label):
                self.assertEqual(resolve(None, event), (None, "no_vault"))

    def test_explicit_vault_is_the_only_candidate(self) -> None:
        first = make_vault(self.base / "first")
        second = make_vault(self.base / "second")
        resolve = self.helpers.resolve_vault

        self.assertEqual(resolve(first, {"workspacePaths": [str(second)]}), (first, "one_vault"))
        self.assertEqual(resolve(str(first), None), (first, "one_vault"))
        self.assertEqual(resolve(self.base, {"workspacePaths": [str(first)]}), (None, "no_vault"))
        self.assertEqual(resolve(Path("first"), {"workspacePaths": [str(first)]}), (None, "no_vault"))

    def test_resolve_vault_ignores_the_process_folder_and_claude_project_dir(self) -> None:
        vault = make_vault(self.base / "vault")
        previous = os.getcwd()
        os.chdir(vault)
        self.addCleanup(os.chdir, previous)
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(vault)}):
            self.assertEqual(self.helpers.resolve_vault(None, {}), (None, "no_vault"))
            self.assertEqual(self.helpers.resolve_vault(None, {"cwd": "."}), (None, "no_vault"))
            self.assertEqual(self.helpers.vault_candidates({"workspacePaths": ["."]}), [])

    def test_vault_candidates_keep_event_order_and_drop_repeats(self) -> None:
        first = make_vault(self.base / "first")
        second = make_vault(self.base / "second")
        alias = self.base / "alias"
        try:
            alias_folder(alias, first)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"folder alias unavailable: {error}")

        found = self.helpers.vault_candidates(
            {"cwd": str(second), "workspacePaths": [str(first), str(alias), str(second) + os.sep]})

        self.assertEqual(found, [second, first])

    def test_claim_once_lets_the_first_caller_act(self) -> None:
        claim = self.helpers.claim_once
        self.assertTrue(claim("SessionStart", "c-1"))
        self.assertFalse(claim("SessionStart", "c-1"))
        self.assertTrue(claim("SessionStart", "c-2"))
        self.assertTrue(claim("Stop", "c-1", "1"))
        self.assertFalse(claim("Stop", "c-1", "1"))
        self.assertTrue(claim("Stop", "c-1", "2"))

        names = sorted(path.name for path in claims_folder(self.temp).iterdir())
        self.assertEqual(names, sorted([claim_name("SessionStart", "c-1"), claim_name("SessionStart", "c-2"),
                                        claim_name("Stop", "c-1", "1"), claim_name("Stop", "c-1", "2")]))

    def test_claim_once_fails_open_on_other_errors(self) -> None:
        with mock.patch.object(self.helpers.os, "open", side_effect=PermissionError("SECRET denied")):
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-open"))
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-open"))
        # A file where the marker folder belongs: no marker can be made, so every caller acts.
        folder = claims_folder(self.temp)
        shutil.rmtree(folder)
        folder.write_bytes(b"")
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-file"))
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-file"))

    def test_claim_holds_for_120_seconds_then_the_key_is_processed_again(self) -> None:
        self.assertEqual(self.helpers.CLAIM_VALID_SECONDS, 120)
        claim = self.helpers.claim_once
        folder = claims_folder(self.temp)
        # SessionStart's key is the conversation ID; Stop's adds the executionNum.
        for parts in (("SessionStart", "c-renew"), ("Stop", "c-renew", "7")):
            with self.subTest(key=parts):
                marker = folder / claim_name(*parts)
                self.assertTrue(claim(*parts))
                self.assertFalse(claim(*parts))
                set_age(marker, 100)  # within 120 seconds: the claim still holds
                self.assertFalse(claim(*parts))
                set_age(marker, 130)  # older: stale, so the key is processed again and the claim renewed
                self.assertTrue(claim(*parts))
                self.assertFalse(claim(*parts))
                self.assertLess(abs(time.time() - marker.stat().st_mtime), 60)
                set_age(marker, -3600)  # ahead of the clock by more than 120 seconds (a clock set back): stale
                self.assertTrue(claim(*parts))
                self.assertFalse(claim(*parts))
        names = sorted(os.listdir(folder))
        self.assertIn(claim_name("SessionStart", "c-renew"), names)
        self.assertIn(claim_name("Stop", "c-renew", "7"), names)
        # Whatever a renewal leaves beside the markers, the two-day sweep can remove.
        self.assertTrue(all(sweepable(name) for name in names), names)

    def test_one_of_many_simultaneous_callers_renews_a_stale_claim(self) -> None:
        marker = claims_folder(self.temp) / claim_name("Stop", "c-race", "1")
        self.assertTrue(self.helpers.claim_once("Stop", "c-race", "1"))
        callers = 8
        with ThreadPoolExecutor(max_workers=callers) as pool:
            for round_index in range(10):
                with self.subTest(round=round_index):
                    set_age(marker, 130 + round_index)
                    start = threading.Barrier(callers)

                    def call(_: int, start: threading.Barrier = start) -> bool:
                        start.wait(timeout=30)
                        return self.helpers.claim_once("Stop", "c-race", "1")

                    taken = list(pool.map(call, range(callers)))
                    self.assertEqual(sorted(taken), [False] * (callers - 1) + [True])

    @unittest.skipIf(hasattr(os, "getuid"), "Windows: the temporary folder belongs to the user")
    def test_windows_claim_folder_keeps_its_name(self) -> None:
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-windows"))
        self.assertEqual(os.listdir(self.temp), [CLAIMS])
        self.assertEqual(os.listdir(self.temp / CLAIMS), [claim_name("SessionStart", "c-windows")])

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX: a shared temporary folder needs a folder per user")
    def test_posix_claim_folder_is_per_user_and_private(self) -> None:
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-private"))

        folder = self.temp / f"{CLAIMS}-{os.getuid()}"
        info = os.lstat(folder)
        self.assertTrue(stat.S_ISDIR(info.st_mode))
        self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)
        self.assertEqual(os.listdir(self.temp), [folder.name])
        self.assertEqual(os.listdir(folder), [claim_name("SessionStart", "c-private")])

    @unittest.skipUnless(hasattr(os, "getuid"), "POSIX: a symbolic link planted in the shared temporary folder")
    def test_posix_claim_folder_behind_a_symbolic_link_is_not_used(self) -> None:
        planted = self.base / "planted target"
        planted.mkdir()
        old = planted / ("e" * 40)
        old.write_bytes(b"")
        set_age(old, 3 * 24 * 60 * 60)
        os.symlink(planted, self.temp / f"{CLAIMS}-{os.getuid()}", target_is_directory=True)

        # Fail open: every caller acts; nothing is made in the target and nothing there is swept.
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-link"))
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-link"))
        self.assertEqual(os.listdir(planted), ["e" * 40])

    def test_claim_folder_of_another_owner_is_not_used(self) -> None:
        # This user's folder name, made by another user: on POSIX the real check; on
        # Windows os.getuid is simulated (lstat reports owner 0 there).
        other = os.lstat(self.temp).st_uid + 4242
        folder = self.temp / f"{CLAIMS}-{other}"
        folder.mkdir()
        old = folder / ("f" * 40)
        old.write_bytes(b"")
        set_age(old, 3 * 24 * 60 * 60)
        with mock.patch.object(self.helpers.os, "getuid", create=True, return_value=other):
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-owner"))
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-owner"))
        self.assertEqual(os.listdir(folder), ["f" * 40])

    def test_claim_folder_that_is_not_a_directory_is_not_used(self) -> None:
        owner = os.lstat(self.temp).st_uid
        planted = self.temp / f"{CLAIMS}-{owner}"
        planted.write_bytes(b"PLANTED")
        with mock.patch.object(self.helpers.os, "getuid", create=True, return_value=owner):
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-file"))
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-file"))
        self.assertEqual(planted.read_bytes(), b"PLANTED")

    def test_per_user_claim_folder_is_made_0700_and_claims_as_usual(self) -> None:
        # The POSIX branch on any platform: os.getuid is simulated on Windows.
        owner = os.lstat(self.temp).st_uid
        folder = self.temp / f"{CLAIMS}-{owner}"
        with mock.patch.object(self.helpers.os, "getuid", create=True, return_value=owner), \
                mock.patch.object(self.helpers.os, "mkdir", wraps=os.mkdir) as made:
            self.assertTrue(self.helpers.claim_once("SessionStart", "c-user"))
            self.assertFalse(self.helpers.claim_once("SessionStart", "c-user"))
        self.assertEqual(made.call_args_list, [mock.call(folder, 0o700)] * 2)
        self.assertEqual(os.listdir(self.temp), [folder.name])
        self.assertEqual(os.listdir(folder), [claim_name("SessionStart", "c-user")])

    def test_claim_once_sweeps_markers_older_than_two_days(self) -> None:
        folder = claims_folder(self.temp)
        folder.mkdir()
        old = time.time() - 3 * 24 * 60 * 60
        stale = [folder / (f"{index:x}" * 40) for index in range(3)]
        fresh = folder / ("d" * 40)
        other = folder / "keep-this-name.txt"
        expired_claim = folder / claim_name("SessionStart", "c-expired")
        for path in (*stale, fresh, other, expired_claim):
            path.write_bytes(b"")
        for path in (*stale, other, expired_claim):
            os.utime(path, (old, old))

        # An expired marker of the same claim no longer blocks it.
        self.assertTrue(self.helpers.claim_once("SessionStart", "c-expired"))

        self.assertEqual([path.exists() for path in stale], [False, False, False])
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())
        self.assertTrue(expired_claim.exists())
        self.assertFalse(self.helpers.claim_once("SessionStart", "c-expired"))

    def test_claim_sweep_scans_at_most_200_entries(self) -> None:
        folder = claims_folder(self.temp)
        folder.mkdir()
        old = time.time() - 3 * 24 * 60 * 60
        for index in range(260):
            path = folder / f"{index:040x}"
            path.write_bytes(b"")
            os.utime(path, (old, old))

        self.assertTrue(self.helpers.claim_once("SessionStart", "c-many"))

        left = [path for path in folder.iterdir() if path.name != claim_name("SessionStart", "c-many")]
        self.assertGreaterEqual(len(left), 60)
        self.assertLess(len(left), 260)

    def test_emit_writes_one_compact_utf8_json_line(self) -> None:
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="utf-8")
        payload = {"injectSteps": [{"systemMessage": {"systemMessage": "한글\n둘 \"q\"",
                                                      "metadata": {"source": "agentic-vault"}}}]}
        with mock.patch.object(sys, "stdout", stream):
            self.helpers.emit(payload)
            self.helpers.emit({})

        expected = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n{}\n"
        self.assertEqual(raw.getvalue(), expected.encode("utf-8"))
        self.assertNotIn(b"\r", raw.getvalue())

    def test_emit_drops_a_failed_write_and_points_the_descriptor_at_the_null_device(self) -> None:
        # A closed stream raises ValueError; emit raises nothing.
        closed = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        closed.close()
        with mock.patch.object(sys, "stdout", closed):
            self.helpers.emit({})

        # A closed pipe makes the flush fail. The descriptor behind the stream then
        # writes to the null device, so the flush at interpreter exit cannot fail again.
        descriptor, name = tempfile.mkstemp(dir=self.temp)
        self.addCleanup(os.close, descriptor)

        class ClosedPipe(io.RawIOBase):
            broken = True

            def writable(self) -> bool:
                return True

            def fileno(self) -> int:
                return descriptor

            def write(self, data) -> int:
                if self.broken:
                    raise OSError(errno.EINVAL, "Invalid argument")
                return len(data)

        pipe = ClosedPipe()
        stream = io.TextIOWrapper(io.BufferedWriter(pipe), encoding="utf-8")
        with mock.patch.object(sys, "stdout", stream):
            self.helpers.emit({"injectSteps": []})
        pipe.broken = False
        stream.close()
        os.write(descriptor, b"written after the failure")
        self.assertEqual(Path(name).read_bytes(), b"")

    def test_valid_id(self) -> None:
        valid = self.helpers.valid_id
        for value in ("conversation-1", "가나다", "c" * 16, "a b"):
            with self.subTest(value=value):
                self.assertEqual(valid(value, 16), value)
        for value in (None, "", 7, ["c"], "c" * 17, "a\nb", "a\tb", "a\x00b", "a\x7fb", "a\x85b", "a\ud800b"):
            with self.subTest(value=value):
                self.assertIsNone(valid(value, 16))


class AntigravityFailureTests(unittest.TestCase):
    """In-process runs that force failures the subprocess tests cannot reach."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="agy failure ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.temp = self.base / "temp"
        self.temp.mkdir()
        self.vault = make_vault(self.base / "vault")
        saved_path = list(sys.path)
        self.addCleanup(sys.path.__setitem__, slice(None), saved_path)
        self.addCleanup(sys.modules.pop, "antigravity_hook", None)
        tempdir = mock.patch.object(tempfile, "tempdir", str(self.temp))
        tempdir.start()
        self.addCleanup(tempdir.stop)

    def run_main(self, module, raw: bytes, *patches) -> tuple[int, bytes, str]:
        output = io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding="utf-8")
        stderr = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(sys, "stderr", stderr):
            for patch in patches:
                patch.start()
            try:
                code = module.main(["--host", "antigravity"])
            finally:
                for patch in reversed(patches):
                    patch.stop()
            stdout.flush()
        return code, output.getvalue(), stderr.getvalue()

    def event(self, **fields: object) -> bytes:
        return encode({"conversationId": "conversation-failure", "workspacePaths": [str(self.vault)],
                       "hookEventName": "Stop", "executionNum": 1, "fullyIdle": True}, fields)

    def test_unexpected_session_start_failure_names_a_fixed_code(self) -> None:
        hook = load("antigravity_start_failure_test", HOOKS / "session_start.py")
        for index, error in enumerate((KeyError("C:/private/SECRET.md"), TypeError("SECRET"))):
            with self.subTest(error=type(error).__name__):
                code, stdout, stderr = self.run_main(
                    hook, self.event(conversationId=f"c-{index}", hookEventName="SessionStart"),
                    mock.patch.object(hook, "compose_context", side_effect=error))
                self.assertEqual((code, stdout), (0, EMPTY))
                self.assertEqual(stderr, START_UNAVAILABLE.format("unclassified_failure") + "\n")

    def test_session_start_without_its_helpers_still_answers_an_empty_object(self) -> None:
        hook = load("antigravity_start_helpers_test", HOOKS / "session_start.py")
        code, stdout, stderr = self.run_main(
            hook, self.event(hookEventName="SessionStart"),
            mock.patch.object(hook, "_antigravity_helpers", side_effect=ImportError("SECRET")))
        self.assertEqual((code, stdout), (0, EMPTY))
        self.assertEqual(stderr, START_UNAVAILABLE.format("helpers_unavailable") + "\n")

    def test_checkpoint_failures_print_fixed_codes_and_answer_an_empty_object(self) -> None:
        hook = load("antigravity_checkpoint_failure_test", HOOKS / "session_checkpoint.py")
        import vault_evidence
        import vault_state
        cases = ((vault_state.StateError("source_changed"), "source_changed"),
                 (vault_evidence.EvidenceError("file_changed_during_read"), "file_changed_during_read"),
                 (vault_state.StateError("C:/private/SECRET-NOTE.md"), "unclassified_failure"),
                 (OSError("C:/private/SECRET-NOTE.md"), "unclassified_failure"),
                 (KeyError("SECRET-NOTE"), "unclassified_failure"))
        for index, (error, expected) in enumerate(cases):
            with self.subTest(error=type(error).__name__, code=expected):
                code, stdout, stderr = self.run_main(
                    hook, self.event(executionNum=index),
                    mock.patch.object(hook, "write_checkpoint", side_effect=error))
                self.assertEqual((code, stdout), (0, EMPTY))
                self.assertEqual(stderr, CHECKPOINT_UNAVAILABLE.format(expected) + "\n")

    def test_checkpoint_without_its_helpers_still_answers_an_empty_object(self) -> None:
        hook = load("antigravity_checkpoint_helpers_test", HOOKS / "session_checkpoint.py")
        code, stdout, stderr = self.run_main(
            hook, self.event(),
            mock.patch.object(hook, "_antigravity_helpers", side_effect=ImportError("SECRET")))
        self.assertEqual((code, stdout), (0, EMPTY))
        self.assertEqual(stderr, CHECKPOINT_UNAVAILABLE.format("helpers_unavailable") + "\n")
        self.assertEqual(list((self.vault / RUNTIME).glob("checkpoint-*.json")) if (self.vault / RUNTIME).is_dir()
                         else [], [])

    def test_helpers_fallback_drops_a_closed_stdout(self) -> None:
        # Without its helpers each hook still answers {}. A host that has stopped reading makes
        # that write fail: the failure is dropped, exit code 0, only the fixed diagnostic on
        # stderr, and the descriptor behind stdout then writes to the null device.
        cases = (("session_start.py", "SessionStart", START_UNAVAILABLE),
                 ("session_checkpoint.py", "Stop", CHECKPOINT_UNAVAILABLE))
        for name, event, unavailable in cases:
            with self.subTest(hook=name, stream="closed pipe"):
                hook = load(f"antigravity_closed_fallback_{event}", HOOKS / name)
                descriptor, path = tempfile.mkstemp(dir=self.temp)
                self.addCleanup(os.close, descriptor)

                class ClosedPipe(io.RawIOBase):
                    broken = True

                    def writable(self) -> bool:
                        return True

                    def fileno(self) -> int:
                        return descriptor

                    def write(self, data) -> int:
                        if self.broken:
                            raise OSError(errno.EINVAL, "Invalid argument")
                        return len(data)

                pipe = ClosedPipe()
                stream = io.TextIOWrapper(io.BufferedWriter(pipe), encoding="utf-8")
                stderr = io.StringIO()
                stdin = io.TextIOWrapper(io.BytesIO(self.event(hookEventName=event)), encoding="utf-8")
                with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stream), \
                        mock.patch.object(sys, "stderr", stderr), \
                        mock.patch.object(hook, "_antigravity_helpers", side_effect=ImportError("SECRET")):
                    code = hook.main(["--host", "antigravity"])
                pipe.broken = False
                stream.close()
                os.write(descriptor, b"written after the failure")
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), unavailable.format("helpers_unavailable") + "\n")
                self.assertEqual(Path(path).read_bytes(), b"")
            with self.subTest(hook=name, stream="closed stream"):
                # The stream is closed after the hook started and before it answers, so writing the answer raises
                # ValueError; the hook still exits 0. (It is open while the parser is built: Python 3.14's argparse
                # asks stdout whether it is a terminal at that point, which a closed stream refuses.)
                hook = load(f"antigravity_closed_stream_{event}", HOOKS / name)
                closing = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")

                def close_then_fail():
                    closing.close()
                    raise ImportError("SECRET")

                stderr = io.StringIO()
                stdin = io.TextIOWrapper(io.BytesIO(self.event(hookEventName=event)), encoding="utf-8")
                with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", closing), \
                        mock.patch.object(sys, "stderr", stderr), \
                        mock.patch.object(hook, "_antigravity_helpers", side_effect=close_then_fail):
                    code = hook.main(["--host", "antigravity"])
                self.assertTrue(closing.closed)
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), unavailable.format("helpers_unavailable") + "\n")

    def test_fallback_copies_of_discard_output_match_the_helper(self) -> None:
        # Both hooks keep a copy of antigravity_hook._discard_output for the answer written when that module
        # cannot be imported. The copies must not drift from the helper (docstrings and annotations aside).
        import ast

        def shape(path: Path) -> str:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            [function] = [node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == "_discard_output"]
            body = function.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]
            return ast.dump(function.args) + "".join(ast.dump(statement) for statement in body)

        expected = shape(HELPERS)
        for name in ("session_start.py", "session_checkpoint.py"):
            with self.subTest(hook=name):
                self.assertEqual(shape(HOOKS / name), expected)

    def test_checkpoint_passes_only_the_conversation_id_to_the_writer(self) -> None:
        hook = load("antigravity_checkpoint_call_test", HOOKS / "session_checkpoint.py")
        calls = []

        def record(vault, event, **options):
            calls.append((Path(vault), event, options))
            return {}

        code, stdout, stderr = self.run_main(
            hook, self.event(lastUserInput="LAST-INPUT", transcriptPath=str(self.base / "t.jsonl")),
            mock.patch.object(hook, "write_checkpoint", side_effect=record))
        self.assertEqual((code, stdout, stderr), (0, EMPTY, ""))
        self.assertEqual(calls, [(self.vault, "Stop",
                                  {"session_id": "conversation-failure", "pending_references": None})])


if __name__ == "__main__":
    unittest.main()
