from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = REPO_ROOT / "assets" / "git-hooks"
PRE_COMMIT = HOOK_DIR / "pre-commit"
PRE_PUSH = HOOK_DIR / "pre-push"
INIT_COMMAND = REPO_ROOT / "commands" / "vault-init.md"
UPGRADE_COMMAND = REPO_ROOT / "commands" / "vault-upgrade.md"
CONFIG_TEMPLATE = REPO_ROOT / "assets" / "templates" / "vault-config.json"
HEALTHCHECK_ENGINE = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"


def find_shell() -> Path:
    discovered = shutil.which("sh")
    if discovered:
        return Path(discovered)
    git_for_windows = Path(r"C:\Program Files\Git\bin\sh.exe")
    if git_for_windows.is_file():
        return git_for_windows
    raise unittest.SkipTest("POSIX sh is required for hook integration tests")


def shell_path(path: Path) -> str:
    """Return a path that Git for Windows sh and POSIX sh can both consume."""
    resolved = path.resolve()
    drive, tail = os.path.splitdrive(str(resolved))
    if drive:
        return f"/{drive[0].lower()}{tail.replace(os.sep, '/')}"
    return resolved.as_posix()


class PreCommitHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shell = find_shell()
        self.tempdir = tempfile.TemporaryDirectory()
        self.vault = Path(self.tempdir.name)
        self.hook_dir = self.vault / "00-meta" / "scripts" / "git-hooks"
        self.hook_dir.mkdir(parents=True)
        self.hook = self.hook_dir / "pre-commit"
        shutil.copyfile(PRE_COMMIT, self.hook)
        self.hook.chmod(0o755)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def install_checker(self, source: str) -> Path:
        checker = self.vault / "00-meta" / "scripts" / "vault_healthcheck.py"
        checker.write_text(source, encoding="utf-8", newline="\n")
        return checker

    def install_fake_dirname(self, fake_bin: Path) -> None:
        dirname = fake_bin / "dirname"
        dirname.write_text(
            "#!/bin/sh\n"
            "value=$1\n"
            "case \"$value\" in\n"
            "  */*) value=${value%/*}; [ -n \"$value\" ] || value=/ ;;\n"
            "  *) value=. ;;\n"
            "esac\n"
            "printf '%s\\n' \"$value\"\n",
            encoding="utf-8",
            newline="\n",
        )
        dirname.chmod(0o755)

    def run_hook(self, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.shell), shell_path(self.hook)],
            cwd=self.vault,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )

    def test_precommit_propagates_checker_failure(self) -> None:
        self.install_checker("import sys\nsys.exit(23)\n")

        result = self.run_hook()

        self.assertEqual(result.returncode, 23, result.stderr)

    def test_precommit_fails_when_checker_missing(self) -> None:
        result = self.run_hook()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staged 검사기 없음", result.stderr)

    def test_precommit_fails_when_python_is_missing(self) -> None:
        self.install_checker("raise AssertionError('checker must not run')\n")
        fake_bin = self.vault / "fake-bin"
        fake_bin.mkdir()
        self.install_fake_dirname(fake_bin)
        env = os.environ.copy()
        env["PATH"] = shell_path(fake_bin)

        result = self.run_hook(env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python을 찾을 수 없습니다", result.stderr)

    def test_precommit_uses_python3_fallback_and_expected_arguments(self) -> None:
        argv_log = self.vault / "checker-argv.json"
        self.install_checker(
            "import json, os, sys\n"
            "with open(os.environ['ARGV_LOG'], 'w', encoding='utf-8') as stream:\n"
            "    json.dump(sys.argv, stream, ensure_ascii=False)\n"
        )
        fake_bin = self.vault / "fake-bin"
        fake_bin.mkdir()
        self.install_fake_dirname(fake_bin)
        python3 = fake_bin / "python3"
        python3.write_text(
            f"#!/bin/sh\nexec \"{shell_path(Path(sys.executable))}\" \"$@\"\n",
            encoding="utf-8",
            newline="\n",
        )
        python3.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = shell_path(fake_bin)
        env["ARGV_LOG"] = str(argv_log)

        result = self.run_hook(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(argv_log.read_text(encoding="utf-8"))
        self.assertEqual(argv[-3:], ["--vault", ".", "--staged"])

    def test_real_precommit_blocks_invalid_staged_note_with_shipped_engine(self) -> None:
        shutil.copyfile(HEALTHCHECK_ENGINE, self.vault / "00-meta" / "scripts" / "vault_healthcheck.py")
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        config = {
            "vault_name": "실제 훅 테스트",
            "required_keys": ["title"],
            "enums": {},
            "deny_zones": [],
            "exclude_dirs": [".git"],
            "index_note": "",
            "log_note": "",
            "log_tags": [],
            "hot_note": "",
            "handoff_note": "",
            "ssot_note": "",
            "health_report": "00-meta/hook-health.md",
            "rules_dir": "",
            "frontmatter_roots": ["20-knowledge"],
            "frontmatter_exempt_paths": [],
        }
        config_path = self.vault / "00-meta" / "vault-config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        invalid = self.vault / "20-knowledge" / "Invalid.md"
        invalid.parent.mkdir(parents=True)
        invalid.write_text("missing frontmatter\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "00-meta/vault-config.json", "20-knowledge/Invalid.md"],
            cwd=self.vault,
            check=True,
        )

        result = self.run_hook()

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "스테이징 차단: 20-knowledge/Invalid.md — 프런트매터 누락",
            result.stderr,
        )
        self.assertFalse((self.vault / "00-meta" / "hook-health.md").exists())


class HookAssetContractTests(unittest.TestCase):
    def test_hook_files_have_engine_stamp_and_shell_syntax(self) -> None:
        shell = find_shell()
        for hook in (PRE_COMMIT, PRE_PUSH):
            with self.subTest(hook=hook.name):
                self.assertIn("agentic-vault:hook engine=0.8.2", hook.read_text(encoding="utf-8"))
                result = subprocess.run(
                    [str(shell), "-n", str(hook)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_template_config_declares_staged_frontmatter_paths(self) -> None:
        config = json.loads(CONFIG_TEMPLATE.read_text(encoding="utf-8"))

        self.assertEqual(
            config["frontmatter_roots"],
            ["00-meta", "20-knowledge", "30-journal", "40-people", "50-projects"],
        )
        self.assertEqual(
            config["frontmatter_exempt_paths"],
            ["00-meta/scratch", "00-meta/scripts", "10-inbox"],
        )

    def test_init_installs_engine_before_hooks_and_activation(self) -> None:
        command = INIT_COMMAND.read_text(encoding="utf-8")
        engine_source = "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py"
        hook_source = "${CLAUDE_PLUGIN_ROOT}/assets/git-hooks/"
        activation = "git config core.hooksPath 00-meta/scripts/git-hooks"

        self.assertLess(command.index(engine_source), command.index(hook_source))
        self.assertLess(command.index(hook_source), command.index(activation))
        self.assertIn("00-meta/scripts/vault_healthcheck.py", command)
        self.assertIn("다른 `core.hooksPath`", command)
        self.assertIn("명시적", command)

    def test_upgrade_defines_all_stamp_states_and_preserves_custom_files(self) -> None:
        command = UPGRADE_COMMAND.read_text(encoding="utf-8")

        for state in ("누락", "스탬프 없음", "낮음", "동일", "높음"):
            with self.subTest(state=state):
                self.assertIn(state, command)
        self.assertIn("engine=", command)
        self.assertIn("diff", command)
        self.assertIn("자동 덮어쓰기 금지", command)
        self.assertIn("로컬 수정", command)

    def test_upgrade_requires_confirmation_for_unrelated_hooks_path(self) -> None:
        command = UPGRADE_COMMAND.read_text(encoding="utf-8")

        self.assertIn("git config --get core.hooksPath", command)
        self.assertIn("다른 값", command)
        self.assertIn("명시적 확인", command)
        self.assertIn("유지", command)


if __name__ == "__main__":
    unittest.main()
