#!/usr/bin/env python3
"""Run the unit test suite under a long temporary folder that ``tempfile`` really uses.

Setting TEMP alone proves nothing: Python's ``tempfile`` skips a folder it cannot
use and silently falls back to another one, so a run that was meant to use a
long TEMP can be an ordinary run. This tool

1. creates a folder whose path has an exact length (``--length``, default 150),
2. proves in a child process that ``tempfile.gettempdir()`` is that folder, and
   stops with exit code 3 when it is not,
3. runs ``python -m unittest`` from the repository root with TEMP, TMP and
   TMPDIR set to the folder and ``AGENTIC_VAULT_REQUIRE_LONG_TEMP`` set to the
   length (the suite's own guard, ``tests/test_000_environment.py``, then fails
   when ``tempfile`` uses another folder),
4. removes the folder (unless ``--keep``) and prints a one-line JSON summary as
   the last line on stderr, also when the suite was interrupted with Ctrl+C.

The exit code is that of the suite; 2 is a usage error (the summary is not
printed then, since no folder was made) or a ``--summary`` file that cannot be
written after a passing suite (after a failing suite the suite's code stays),
and 3 means that the folder is not the one ``tempfile`` uses (nothing was
run). A Windows status code of 2**31 or more (for example 0xC000013A after
Ctrl+C) is passed on unchanged as the process status; the summary records it as
the unsigned number the suite process returned.

Usage:
    python -B scripts/long_temp_suite.py [--length N] [--parent DIR] [--keep] [--summary OUT] [-- ARG ...]

``ARG ...`` are the arguments for ``python -m unittest`` (default ``discover -s tests``).
Python 3.10+ standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
REQUIRE = "AGENTIC_VAULT_REQUIRE_LONG_TEMP"
DEFAULT_LENGTH = 150
DEFAULT_ARGUMENTS = ("discover", "-s", "tests")
NAME_PREFIX = "av-long-temp-"
MAX_NAME = 255  # one path component
EXIT_USAGE = 2
EXIT_NOT_EFFECTIVE = 3
INTERRUPTED = 0xC000013A if os.name == "nt" else 130  # what an interrupted console program returns
INTERRUPT_GRACE = 30  # seconds the suite gets to end by itself after Ctrl+C
PROBE = (
    "import os, sys, tempfile\n"
    "used = os.path.normcase(os.path.abspath(tempfile.gettempdir()))\n"
    "sys.stdout.write('1' if used == os.path.normcase(os.path.abspath(sys.argv[1])) else '0')\n"
)


class UsageError(Exception):
    """The requested folder cannot be made."""


def folder_path(parent: Path, length: int, token: str) -> Path:
    """The folder below parent whose path is exactly `length` characters long."""
    head = NAME_PREFIX + token + "-"
    # A root such as C:\ or / already ends with the separator, so the length is taken from the joined path.
    base = len(str(parent / head)) - len(head)
    padding = length - base - len(head)
    if padding < 1:
        shortest = base + len(head) + 1
        raise UsageError(f"--length {length} is too short for this parent folder (at least {shortest})")
    name = head + "x" * padding
    if len(name) > MAX_NAME:
        longest = base + MAX_NAME
        raise UsageError(f"--length {length} is too long for one folder name below this parent (at most {longest})")
    return parent / name


def child_environment(folder: Path, length: int) -> dict[str, str]:
    """The caller's environment with the three temporary-folder variables and the guard variable set."""
    environment = dict(os.environ)
    for name in ("TEMP", "TMP", "TMPDIR"):
        environment[name] = str(folder)
    environment[REQUIRE] = str(length)
    return environment


def tempfile_uses(folder: Path, environment: dict[str, str]) -> bool:
    """True when a fresh Python process with that environment gets folder from tempfile.gettempdir()."""
    result = subprocess.run([sys.executable, "-c", PROBE, str(folder)], env=environment,
                            capture_output=True, text=True, check=False)
    return result.returncode == 0 and result.stdout == "1"


def long_paths_enabled() -> int | None:
    """The Windows LongPathsEnabled value, or None when it cannot be read or the system is not Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
        return int(value)
    except (ImportError, OSError, ValueError, TypeError):
        return None


def _extended(path: Path) -> str:
    """The Windows extended-length form of an absolute path, so entries beyond the plain limit can be removed."""
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if len(text) > 2 and text[1] == ":" and text[2] == "\\":
        return "\\\\?\\" + text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return text


def remove_folder(folder: Path) -> bool:
    """Remove the folder with everything in it; True when nothing is left."""
    target = _extended(folder)

    def retry_writable(function, path, _error):
        try:
            os.chmod(path, 0o700)
            function(path)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(target, onexc=retry_writable)
    else:
        shutil.rmtree(target, onerror=retry_writable)
    return not os.path.lexists(target)


def run_suite(arguments: list[str], environment: dict[str, str]) -> int:
    """python -m unittest with the caller's interpreter flags, from the repository root."""
    # _args_from_interpreter_flags is the helper multiprocessing uses to start a child like its parent
    # (-B, -X utf8, -W ...), so the suite runs under the flags this tool was started with.
    command = [sys.executable, *subprocess._args_from_interpreter_flags(), "-m", "unittest", *arguments]
    process = subprocess.Popen(command, cwd=ROOT, env=environment)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Ctrl+C reaches the suite as well: let it end and report its own status.
        try:
            return process.wait(timeout=INTERRUPT_GRACE)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.kill()
            process.wait()
            return INTERRUPTED


def exit_status(returncode: int) -> int:
    """The process status for returncode: a Windows status of 2**31 or more as the signed number that
    SystemExit accepts and Windows turns back into the same status; any other code unchanged."""
    if os.name == "nt" and returncode >= 2 ** 31:
        return returncode - 2 ** 32
    return returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the unit test suite under a long temporary folder that tempfile really uses.")
    parser.add_argument("--length", type=int, default=DEFAULT_LENGTH, metavar="N",
                        help=f"exact length of the temporary folder path (default {DEFAULT_LENGTH})")
    parser.add_argument("--parent", type=Path, metavar="DIR",
                        help="folder the temporary folder is created in (default: the current temporary folder)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder afterwards")
    parser.add_argument("--summary", type=Path, metavar="OUT", help="also write the JSON summary to OUT")
    parser.add_argument("arguments", nargs="*", metavar="ARG",
                        help="arguments for python -m unittest, after -- (default: discover -s tests)")
    options = parser.parse_args(argv)
    arguments = list(options.arguments) or list(DEFAULT_ARGUMENTS)
    try:
        parent = (options.parent or Path(tempfile.gettempdir())).resolve()
        if not parent.is_dir():
            raise UsageError("the parent folder does not exist")
        folder = folder_path(parent, options.length, uuid.uuid4().hex[:8])
        if options.summary is not None:
            try:  # appending checks that the file can be written without emptying it
                options.summary.parent.mkdir(parents=True, exist_ok=True)
                with open(options.summary, "ab"):
                    pass
            except OSError as error:
                raise UsageError(f"cannot write the summary: {error.strerror}") from error
        try:
            os.mkdir(folder)
        except OSError as error:
            raise UsageError(f"the folder of {options.length} characters cannot be created: {error.strerror}") from error
    except UsageError as error:
        print(f"long_temp_suite: {error}", file=sys.stderr)
        return EXIT_USAGE
    environment = child_environment(folder, options.length)
    summary = {"temp_length": len(str(folder)), "tempfile_uses_temp": False,
               "long_paths_enabled": long_paths_enabled(), "returncode": EXIT_NOT_EFFECTIVE,
               "python": sys.version.split()[0], "platform": sys.platform}
    try:
        summary["tempfile_uses_temp"] = tempfile_uses(folder, environment)
        if summary["tempfile_uses_temp"]:
            summary["returncode"] = run_suite(arguments, environment)
        else:
            print("long_temp_suite: tempfile does not use the folder of "
                  f"{options.length} characters; nothing was run", file=sys.stderr)
    finally:
        if not options.keep:
            try:
                removed = remove_folder(folder)
            except OSError:
                removed = False
            if not removed:
                print("long_temp_suite: the temporary folder could not be removed completely", file=sys.stderr)
    text = json.dumps(summary, sort_keys=True)
    written = True
    if options.summary is not None:
        try:
            options.summary.write_bytes(text.encode("utf-8") + b"\n")
        except OSError as error:
            written = False
            print(f"long_temp_suite: cannot write the summary: {error.strerror}", file=sys.stderr)
    if options.keep:
        print(f"long_temp_suite: kept {folder}", file=sys.stderr)
    print(text, file=sys.stderr)
    return exit_status(summary["returncode"] if written or summary["returncode"] else EXIT_USAGE)


if __name__ == "__main__":
    raise SystemExit(main())
