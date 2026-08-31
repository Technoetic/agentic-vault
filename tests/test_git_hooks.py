from __future__ import annotations

import json
import os
from pathlib import Path
import re
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
README = REPO_ROOT / "README.md"


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

    @staticmethod
    def install_fake_command(fake_bin: Path, name: str, source: str) -> Path:
        command = fake_bin / name
        command.write_text(source, encoding="utf-8", newline="\n")
        command.chmod(0o755)
        return command

    def install_working_python(self, fake_bin: Path, name: str = "python3") -> Path:
        return self.install_fake_command(
            fake_bin,
            name,
            f"#!/bin/sh\nexec \"{shell_path(Path(sys.executable))}\" \"$@\"\n",
        )

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

    def test_precommit_falls_through_broken_python_to_working_python3(self) -> None:
        argv_log = self.vault / "checker-argv.json"
        self.install_checker(
            "import json, os, sys\n"
            "with open(os.environ['ARGV_LOG'], 'w', encoding='utf-8') as stream:\n"
            "    json.dump(sys.argv, stream, ensure_ascii=False)\n"
        )
        fake_bin = self.vault / "fake-bin"
        fake_bin.mkdir()
        self.install_fake_dirname(fake_bin)
        self.install_fake_command(fake_bin, "python", "#!/bin/sh\nexit 127\n")
        self.install_working_python(fake_bin)
        env = os.environ.copy()
        env["PATH"] = shell_path(fake_bin)
        env["ARGV_LOG"] = str(argv_log)

        result = self.run_hook(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(argv_log.read_text(encoding="utf-8"))
        self.assertEqual(argv[1:], ["--vault", ".", "--staged"])

    def test_precommit_falls_through_python_older_than_310(self) -> None:
        argv_log = self.vault / "checker-argv.json"
        self.install_checker(
            "import json, os, sys\n"
            "with open(os.environ['ARGV_LOG'], 'w', encoding='utf-8') as stream:\n"
            "    json.dump(sys.argv, stream, ensure_ascii=False)\n"
        )
        fake_bin = self.vault / "fake-bin"
        fake_bin.mkdir()
        self.install_fake_dirname(fake_bin)
        self.install_fake_command(
            fake_bin,
            "python",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            "  case \"$2\" in *\"3, 10\"*) exit 1 ;; *) exit 0 ;; esac\n"
            "fi\n"
            "exit 72\n",
        )
        self.install_working_python(fake_bin)
        env = os.environ.copy()
        env["PATH"] = shell_path(fake_bin)
        env["ARGV_LOG"] = str(argv_log)

        result = self.run_hook(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(argv_log.read_text(encoding="utf-8"))
        self.assertEqual(argv[1:], ["--vault", ".", "--staged"])

    def test_precommit_uses_windows_py_3_fallback_with_exact_checker_arguments(self) -> None:
        argv_log = self.vault / "checker-argv.json"
        self.install_checker(
            "import json, os, sys\n"
            "with open(os.environ['ARGV_LOG'], 'w', encoding='utf-8') as stream:\n"
            "    json.dump(sys.argv, stream, ensure_ascii=False)\n"
        )
        fake_bin = self.vault / "fake-bin"
        fake_bin.mkdir()
        self.install_fake_dirname(fake_bin)
        self.install_fake_command(
            fake_bin,
            "py",
            "#!/bin/sh\n"
            "[ \"$1\" = \"-3\" ] || exit 73\n"
            "shift\n"
            f"exec \"{shell_path(Path(sys.executable))}\" \"$@\"\n",
        )
        env = os.environ.copy()
        env["PATH"] = shell_path(fake_bin)
        env["ARGV_LOG"] = str(argv_log)

        result = self.run_hook(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(argv_log.read_text(encoding="utf-8"))
        self.assertEqual(argv[1:], ["--vault", ".", "--staged"])

    def test_precommit_fails_closed_when_all_interpreters_are_broken(self) -> None:
        self.install_checker("raise AssertionError('checker must not run')\n")
        fake_bin = self.vault / "fake-bin"
        fake_bin.mkdir()
        self.install_fake_dirname(fake_bin)
        for name in ("python", "python3", "py"):
            self.install_fake_command(fake_bin, name, "#!/bin/sh\nexit 74\n")
        env = os.environ.copy()
        env["PATH"] = shell_path(fake_bin)

        result = self.run_hook(env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.10+", result.stderr)

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
    @staticmethod
    def run_prepush(
        shell: Path,
        remote_url: str,
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        run_env = env.copy()
        run_env["PREPUSH_PATH_UNDER_TEST"] = env["PATH"]
        run_env["PREPUSH_REMOTE_UNDER_TEST"] = remote_url
        return subprocess.run(
            [
                str(shell),
                "-c",
                (
                    "PATH=$PREPUSH_PATH_UNDER_TEST; export PATH; "
                    "hook=$1; set -- origin \"$PREPUSH_REMOTE_UNDER_TEST\"; "
                    '. "$hook"'
                ),
                "pre-push-test",
                shell_path(PRE_PUSH),
            ],
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )

    def test_prepush_classifies_platform_independent_remotes_without_uname(self) -> None:
        shell = find_shell()
        cases = (
            ("absolute POSIX path", "/var/backups/vault.git", 0),
            ("slash-leading backslash UNC path", r"/\server\share\vault.git", 1),
            ("mixed POSIX backslash path", r"/var\backups\vault.git", 1),
            ("local POSIX file URI", "file:///var/backups/vault.git", 0),
            ("local Windows file URI", "file:///C:/backups/vault.git", 0),
            ("encoded uppercase slash UNC file URI", "file:///%2F%2Fserver/share/vault.git", 1),
            ("encoded lowercase slash UNC file URI", "file:///%2f%2fserver/share/vault.git", 1),
            ("encoded uppercase backslash UNC file URI", "file:///%5C%5Cserver/share/vault.git", 1),
            ("encoded mixed-case backslash UNC file URI", "file:///%5c%5Cserver/share/vault.git", 1),
            ("encoded mixed slash UNC file URI", "file:///%2F%5cserver/share/vault.git", 1),
            ("direct backslash UNC file URI", r"file:///\\server\share\vault.git", 1),
            ("direct backslash Windows file URI", r"file:///C:\backups\vault.git", 1),
            ("slash UNC path", "//server/share/vault.git", 1),
            ("backslash UNC path", r"\\server\share\vault.git", 1),
            ("hosted file URI", "file://server/share/vault.git", 1),
            ("UNC-like four-slash file URI", "file:////server/share/vault.git", 1),
            ("single-letter scp remote", "x:path", 1),
            ("scp remote", "git@example.com:team/vault.git", 1),
            ("SSH URL", "ssh://example.com/team/vault.git", 1),
            ("HTTP URL", "http://example.com/team/vault.git", 1),
            ("HTTPS URL", "https://example.com/team/vault.git", 1),
            ("Git URL", "git://example.com/team/vault.git", 1),
            ("empty URL", "", 1),
            ("malformed file URI", "file://", 1),
            ("drive-relative Windows path", r"C:backups\vault.git", 1),
            ("relative path", "backups/vault.git", 1),
        )

        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PATH"] = shell_path(Path(directory))
            for label, remote_url, expected_status in cases:
                with self.subTest(label=label, remote_url=remote_url):
                    result = self.run_prepush(shell, remote_url, env=env)
                    self.assertEqual(result.returncode, expected_status, result.stderr)

    def test_prepush_allows_raw_drive_paths_only_on_confirmed_windows_shells(self) -> None:
        shell = find_shell()
        platforms = (
            ("MINGW64", "MINGW64_NT-10.0-22631", 0, 0),
            ("MSYS", "MSYS_NT-10.0-22631", 0, 0),
            ("Linux", "Linux", 0, 1),
            ("Darwin", "Darwin", 0, 1),
            ("Cygwin", "CYGWIN_NT-10.0-22631", 0, 1),
            ("unknown", "Plan9", 0, 1),
            ("MINGW-like unknown", "MINGW_NOT_WINDOWS", 0, 1),
            ("MSYS-like unknown", "MSYS_NOT_WINDOWS", 0, 1),
            ("uname failure", None, 71, 1),
            ("uname missing", None, None, 1),
        )
        drive_paths = (
            ("uppercase forward-slash root", "C:/"),
            ("uppercase backslash root", "C:\\"),
            ("lowercase forward-slash path", "x:/path"),
            ("lowercase backslash path", r"x:\path"),
        )

        for platform, uname_output, uname_status, expected_status in platforms:
            with tempfile.TemporaryDirectory() as directory:
                fake_bin = Path(directory)
                if uname_status is not None:
                    uname = fake_bin / "uname"
                    if uname_status:
                        source = f"#!/bin/sh\nexit {uname_status}\n"
                    else:
                        source = (
                            "#!/bin/sh\n"
                            "[ \"$1\" = \"-s\" ] || exit 64\n"
                            f"printf '%s\\n' '{uname_output}'\n"
                        )
                    uname.write_text(source, encoding="utf-8", newline="\n")
                    uname.chmod(0o755)

                env = os.environ.copy()
                env["PATH"] = shell_path(fake_bin)
                for path_label, remote_url in drive_paths:
                    with self.subTest(
                        platform=platform,
                        path=path_label,
                        remote_url=remote_url,
                    ):
                        result = self.run_prepush(shell, remote_url, env=env)
                        self.assertEqual(
                            result.returncode,
                            expected_status,
                            result.stderr,
                        )

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

    def test_first_party_healthcheck_callers_preserve_config_report_provenance(self) -> None:
        for command_path in (INIT_COMMAND, UPGRADE_COMMAND):
            execution_lines = [
                line
                for line in command_path.read_text(encoding="utf-8").splitlines()
                if "vault_healthcheck.py" in line and "--vault ." in line
            ]
            with self.subTest(command=command_path.name):
                self.assertEqual(len(execution_lines), 1)
                self.assertNotIn("--output", execution_lines[0])

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

    def test_upgrade_preserves_absent_legacy_frontmatter_scope_markers(self) -> None:
        command = UPGRADE_COMMAND.read_text(encoding="utf-8")

        for literal in (
            "호환 범위 키 예외",
            "`frontmatter_roots`",
            "`frontmatter_exempt_paths`",
            "일반 누락 키 보충에서 제외",
            "둘 중 하나라도 없으면 그 부재를 그대로 보존",
            "자동 추가하지 마라",
            "`fm_exempt_zones`",
            "호환 alias",
            "검사 범위 마이그레이션",
            "명시적으로 승인",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, command)

    def test_upgrade_preserves_legacy_single_briefing_schedule(self) -> None:
        command_lines = UPGRADE_COMMAND.read_text(encoding="utf-8").splitlines()

        expected_lines = (
            "   - **레거시 단일 브리핑 예외**: 기존 `jarvis` 블록에 "
            "`briefing_time`이 있고 `briefing_times`가 없으면, 일반 누락 키 "
            "보충에서 `briefing_times`를 제외해 단일 시각 fallback 일정을 그대로 "
            "보존하라.",
            "   - `briefing_times` 추가는 별도 **브리핑 일정 마이그레이션**이다. "
            "기존 `briefing_time` 값을 배열의 유일한 값으로 사용하는 변경안을 "
            "보여주고, 사용자가 명시적으로 승인한 경우에만 추가하라. 템플릿 "
            "기본값 `07:30`으로 대체하지 마라.",
            "   - 기존 config에 `jarvis` 블록 자체가 없으면 새 블록 전체를 템플릿 "
            "기본값으로 추가하는 기존 동작을 유지하라.",
        )
        for line in expected_lines:
            with self.subTest(line=line):
                self.assertIn(line, command_lines)

    def test_upgrade_requires_confirmation_for_unrelated_hooks_path(self) -> None:
        command = UPGRADE_COMMAND.read_text(encoding="utf-8")

        self.assertIn("git config --get core.hooksPath", command)
        self.assertIn("다른 값", command)
        self.assertIn("명시적 확인", command)
        self.assertIn("유지", command)

    def test_readme_documents_complete_template_keys_and_separates_extensions(self) -> None:
        readme = README.read_text(encoding="utf-8")
        template = json.loads(CONFIG_TEMPLATE.read_text(encoding="utf-8"))
        marker = f"📋 기본 템플릿 설정 키 {len(template)}개 전체"
        self.assertIn(marker, readme)
        details = readme.split(marker, 1)[1].split("</details>", 1)[0]
        extension_marker = "#### 선택 확장(기본 템플릿 미포함)"
        self.assertIn(extension_marker, details)
        template_table, extension_table = details.split(extension_marker, 1)
        documented_keys = re.findall(
            r"^\| `([^`]+)` \|", template_table, flags=re.MULTILINE
        )
        extension_keys = re.findall(
            r"^\| `([^`]+)` \|", extension_table, flags=re.MULTILINE
        )

        self.assertEqual(len(documented_keys), len(template))
        self.assertEqual(set(documented_keys), set(template))
        self.assertTrue(
            {"stale_days", "index_scopes", "anchor_drift_threshold"}.issubset(
                extension_keys
            )
        )
        self.assertIn(
            "`required_keys`와 `enums`는 설정된 `frontmatter_roots` 내부 노트에만 적용된다",
            readme,
        )
        self.assertIn(
            "`frontmatter_roots` 키가 없는 레거시 config의 full 모드는 기존처럼 모든 활성 노트에 적용된다",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
