"""One git environment for tests, so a developer's git settings stay out of synthetic repositories.

A global commit.gpgsign=true, for one, fails every fixture commit on a machine
where signing asks for a key. Repository variables left by a calling hook would
point a fixture at the wrong repository.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

CLEARED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
           "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")
ISOLATED = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git_environment() -> dict:
    """The current environment with git's global and system settings cut off."""
    environment = {key: value for key, value in os.environ.items() if key not in CLEARED}
    environment.update(ISOLATED)
    return environment


def isolate_git(case, ceiling: Path | None = None) -> None:
    """Apply git_environment() to os.environ for one test, so code under test sees it too.

    `ceiling` stops git's own search for a repository at that folder, which keeps a
    "no repository here" test true when the temporary folder sits inside a repository.
    """
    values = dict(ISOLATED)
    if ceiling is not None:
        values["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    environment = mock.patch.dict(os.environ, values)
    environment.start()
    case.addCleanup(environment.stop)
    for name in CLEARED:
        os.environ.pop(name, None)
