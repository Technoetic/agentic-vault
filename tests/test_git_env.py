"""The tests' git isolation keeps a developer's global settings out of fixtures."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

try:
    from _git_env import git_environment, isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import git_environment, isolate_git


@unittest.skipUnless(shutil.which("git"), "git is required")
class IsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.hostile = self.base / "gitconfig"
        self.hostile.write_text("[commit]\n\tgpgsign = true\n", encoding="utf-8")

    def signing(self, env):
        return subprocess.run(["git", "config", "--get", "commit.gpgsign"], cwd=self.base, env=env,
                              capture_output=True, timeout=30, check=False).stdout.strip()

    def test_a_global_signing_setting_reaches_git_without_the_isolation(self):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": str(self.hostile)}
        self.assertEqual(self.signing(env), b"true")

    def test_the_environment_cuts_global_settings_and_repository_variables(self):
        values = {"GIT_CONFIG_GLOBAL": str(self.hostile), "GIT_DIR": "elsewhere"}
        with mock.patch.dict(os.environ, values):
            env = git_environment()
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(self.signing(env), b"")

    def test_isolate_git_covers_code_that_reads_os_environ_and_ends_with_the_test(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.hostile), "GIT_DIR": "elsewhere"}):
            case = unittest.TestCase()
            isolate_git(case, ceiling=self.base)
            self.assertEqual(os.environ["GIT_CEILING_DIRECTORIES"], str(self.base))
            self.assertNotIn("GIT_DIR", os.environ)
            self.assertEqual(self.signing(dict(os.environ)), b"")
            case.doCleanups()
            self.assertEqual(os.environ["GIT_DIR"], "elsewhere")
            self.assertEqual(os.environ["GIT_CONFIG_GLOBAL"], str(self.hostile))

    def test_the_ceiling_hides_a_repository_above_the_folder(self):
        subprocess.run(["git", "init", "-q"], cwd=self.base, env=git_environment(), check=True,
                       capture_output=True, timeout=30)
        inner = self.base / "outer" / "inner"
        inner.mkdir(parents=True)
        with mock.patch.dict(os.environ, {}):
            case = unittest.TestCase()
            isolate_git(case, ceiling=self.base / "outer")
            self.addCleanup(case.doCleanups)
            found = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=inner, capture_output=True,
                                   timeout=30, check=False)
        self.assertNotEqual(found.returncode, 0)


if __name__ == "__main__":
    unittest.main()
