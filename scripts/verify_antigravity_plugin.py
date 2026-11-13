#!/usr/bin/env python3
"""Install the Antigravity package into a disposable agy home and check it; no model turn.

The package (default `antigravity/`) is copied to a temporary folder, and agy runs only the
four calls in AGY_CALLS on that copy: `--version`, `plugin validate --strict --json`,
`plugin install` and `plugin list --json`. This script itself sets USERPROFILE, HOME,
APPDATA, LOCALAPPDATA, TMP and TEMP inside a temporary home for every call. Validation
passes with no finding of severity error or warning (notes are allowed). Every file of
the installed folder is then compared with the copy byte for byte, and the real
`~/.gemini/config` (names, sizes and modification times) must be unchanged. It never runs
`-p`, `login` or a conversation: Antigravity's FAQ says wrapping agy inside third-party
coding agents is not supported under consumer plans. Exit 0 pass, 1 fail (each failed
check printed), 2 usage error or agy missing.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "agentic-vault"
EXPECTED_SOURCE = "antigravity"
EXPECTED_HOOKS = ["agentic-vault"]
# The only agy calls. "PATH" is one absolute path (the package copy); every other argument
# must match exactly. run_agy refuses anything else before a process starts, so -p, login,
# a conversation, uninstall, enable and disable are never run.
AGY_CALLS = (
    ("--version",),
    ("plugin", "validate", "--strict", "--json", "PATH"),
    ("plugin", "install", "PATH"),
    ("plugin", "list", "--json"),
)
ALLOWED_SUBCOMMANDS = tuple(" ".join(call[:2]) if call[0] == "plugin" else call[0] for call in AGY_CALLS)
# Set by this script (no wrapper script) for every agy call, each inside the disposable home.
HOME_VARIABLES = ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP")
# Left out of the agy environment, so no credential or other configuration folder reaches agy.
DROPPED_PREFIXES = ("GEMINI_", "GOOGLE_", "ANTIGRAVITY_", "AGY_", "CLOUDSDK_")
DROPPED_NAMES = frozenset({"XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                           "CLAUDE_PROJECT_DIR"})
# Bytecode next to the package sources is not part of the package.
COPY_IGNORE = ("__pycache__", "*.pyc")
CALL_TIMEOUT = 180.0
COUNT_KEYS = ("errors", "warnings", "notes")
NOTE_SEVERITIES = frozenset({"note", "info"})
NOT_RUN = "not run because an earlier agy call timed out"


class UsageError(Exception):
    """Exit 2: a bad argument, or an agy that is missing or cannot start."""


class CallRefused(ValueError):
    """An agy call outside AGY_CALLS; it is refused before any process starts."""


def allowed_call(args: Sequence[str]) -> bool:
    """True when args match one AGY_CALLS entry exactly ("PATH": one absolute path)."""
    return any(
        len(args) == len(call) and all(
            isinstance(arg, str) and (os.path.isabs(arg) if part == "PATH" else arg == part)
            for arg, part in zip(args, call))
        for call in AGY_CALLS)


def agy_command(agy: str) -> list[str]:
    """Command prefix for agy; a .py file runs with this Python (tests use a fake agy)."""
    if Path(agy).suffix.lower() == ".py":
        if not Path(agy).is_file():
            raise UsageError(f"agy not found: {agy}")
        return [sys.executable, str(Path(agy).resolve())]
    found = shutil.which(agy) if agy else None
    if not found:
        raise UsageError(f"agy not found: {agy}")
    return [str(Path(found).resolve())]


def isolated_environment(home: Path) -> dict[str, str]:
    """os.environ without the dropped names, with HOME_VARIABLES inside home (folders created)."""
    environment = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith(DROPPED_PREFIXES) and name.upper() not in DROPPED_NAMES}
    roaming, local, temporary = home / "AppData" / "Roaming", home / "AppData" / "Local", home / "tmp"
    for folder in (roaming, local, temporary):
        folder.mkdir(parents=True, exist_ok=True)
    environment.update(zip(HOME_VARIABLES, map(str, (home, home, roaming, local, temporary, temporary))))
    return environment


def stop_owned_processes(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            process.kill()
    else:
        # Each agy call owns a new session; this also ends any child it left behind.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_agy(command: Sequence[str], args: Sequence[str], environment: dict, cwd: Path,
            timeout: float = CALL_TIMEOUT) -> tuple[int | None, str, str]:
    """(exit code, stdout, stderr) of one allowed agy call; the exit code is None on a timeout."""
    if not allowed_call(args):
        raise CallRefused("agy call not allowed: " + " ".join(map(str, args)))
    try:
        process = subprocess.Popen(
            [*command, *args], cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", start_new_session=os.name != "nt")
    except OSError as error:
        raise UsageError(f"agy could not be started: {error}") from error
    with process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "", ""
        finally:
            stop_owned_processes(process)
    return process.returncode, stdout, stderr


def parse_json(text: str) -> tuple[bool, object]:
    """(True, value) when text is one JSON document, possibly after a BOM or leading lines."""
    text = text.lstrip(chr(0xFEFF)).strip()
    decoder = json.JSONDecoder()
    start = 0
    while start < len(text):
        if text[start] in "{[":
            try:
                value, end = decoder.raw_decode(text, start)
            except ValueError:
                pass
            else:
                if not text[end:].strip():
                    return True, value
        newline = text.find("\n", start)
        if newline < 0:
            break
        start = newline + 1
    return False, None


def _nodes(value):
    """Every value inside a parsed JSON document, the document included."""
    stack = [value]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _count(value) -> int:
    if isinstance(value, (list, dict)):
        return len(value)
    if isinstance(value, (int, float)):  # a boolean counts as 0 or 1
        return int(value)
    return 0 if value in (None, "") else 1


def component_counts(value) -> dict | None:
    """agy's component summary as a count per kind, without names or paths."""
    if isinstance(value, dict):
        return {str(kind): _count(item) for kind, item in sorted(value.items())}
    if isinstance(value, list):
        counts: dict[str, int] = {}
        for item in value:
            kind = (item.get("type") or item.get("kind")) if isinstance(item, dict) else None
            kind = kind if isinstance(kind, str) else "items"
            counts[kind] = counts.get(kind, 0) + 1
        return dict(sorted(counts.items()))
    return None


def summarize_validation(data) -> dict:
    """Counts of `agy plugin validate --json` findings by severity, and its component counts.

    Any object with a string "severity" is a finding: "error" counts as an error, "note" and
    "info" as notes and every other severity as a warning, so an unknown severity fails the
    check. Numbers, booleans or lists under "errors", "warnings" and "notes" at the top level
    or in "summary" can only raise those counts.
    """
    counts = dict.fromkeys(COUNT_KEYS, 0)
    for node in _nodes(data):
        if isinstance(node, dict) and isinstance(node.get("severity"), str):
            severity = node["severity"].strip().lower()
            key = "errors" if severity == "error" else "notes" if severity in NOTE_SEVERITIES else "warnings"
            counts[key] += 1
    if isinstance(data, dict):
        for holder in (data, data.get("summary")):
            if not isinstance(holder, dict):
                continue
            for key in COUNT_KEYS:
                value = holder.get(key)
                if isinstance(value, (list, dict)):
                    value = len(value)
                if isinstance(value, int):  # bool too: true counts as one
                    counts[key] = max(counts[key], int(value))
    components = component_counts(data.get("components")) if isinstance(data, dict) else None
    return {**counts, "components": components}


def plugin_entries(data) -> list[dict]:
    """Plugin objects of `agy plugin list --json`: a top-level list, or "plugins" as a list or a map."""
    if isinstance(data, dict):
        data = data.get("plugins")
        if isinstance(data, dict):
            data = [{"name": name, **entry} for name, entry in data.items() if isinstance(entry, dict)]
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def _names(value) -> list[str] | None:
    """Sorted names from a list of strings or of objects with a string "name"; else None."""
    if not isinstance(value, list):
        return None
    names = [item.get("name") if isinstance(item, dict) else item for item in value]
    return sorted(names) if all(isinstance(name, str) for name in names) else None


def _enabled(entry: dict) -> bool | None:
    if isinstance(entry.get("enabled"), bool):
        return entry["enabled"]
    if isinstance(entry.get("disabled"), bool):
        return not entry["disabled"]
    return None


def check_listing(data, version: str | None, skills: list[str]) -> tuple[dict | None, list[str]]:
    """(listed, problems) for the agentic-vault entry of `agy plugin list --json`."""
    matches = [entry for entry in plugin_entries(data) if entry.get("name") == PLUGIN_NAME]
    if len(matches) != 1:
        return None, [f"{PLUGIN_NAME} listed {len(matches)} times, expected once"]
    entry = matches[0]
    listed = {"name": PLUGIN_NAME, "version": entry.get("version"), "source": entry.get("source"),
              "enabled": _enabled(entry), "skills": _names(entry.get("skills")),
              "hooks": _names(entry.get("hooks"))}
    problems = []
    if listed["version"] != version:
        problems.append(f"version {listed['version']!r}, expected the package version {version!r}")
    if listed["source"] != EXPECTED_SOURCE:
        problems.append(f"source {listed['source']!r}, expected {EXPECTED_SOURCE!r}")
    if listed["enabled"] is not True:
        problems.append("not enabled" if listed["enabled"] is False else "enabled state not reported")
    if listed["skills"] is None:
        problems.append("skills are not a list of names")
    elif listed["skills"] != sorted(skills):
        missing = sorted(set(skills) - set(listed["skills"]))
        extra = sorted(set(listed["skills"]) - set(skills))
        problems.append(f"skills differ from the package skill folders (missing {missing}, extra {extra})")
    if listed["hooks"] != EXPECTED_HOOKS:
        problems.append(f"hooks {listed['hooks']!r}, expected {EXPECTED_HOOKS!r}")
    return listed, problems


def manifest_version(package: Path) -> tuple[str | None, str | None]:
    """(version, problem) from the package's own plugin.json."""
    try:
        manifest = json.loads((package / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "plugin.json is missing or not JSON"
    if not isinstance(manifest, dict) or manifest.get("name") != PLUGIN_NAME:
        return None, f"plugin.json does not name {PLUGIN_NAME}"
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        return None, "plugin.json has no version"
    return version, None


def skill_folders(package: Path) -> list[str]:
    """Sorted names of the package's skill folders."""
    skills = package / "skills"
    return sorted(entry.name for entry in skills.iterdir() if entry.is_dir()) if skills.is_dir() else []


def files_under(folder: Path) -> set[str]:
    """Relative POSIX paths of every file under folder."""
    return {Path(current, name).relative_to(folder).as_posix()
            for current, _directories, files in os.walk(folder) for name in files}


def compare_trees(installed: Path, copy: Path) -> list[dict]:
    """Each file present on one side only or with different bytes, as {"path", "problem"}."""
    if not installed.is_dir():
        return [{"path": ".", "problem": "missing"}]
    present, expected = files_under(installed), files_under(copy)
    mismatches = []
    for relative in sorted(present | expected):
        if relative not in present:
            problem = "missing"
        elif relative not in expected:
            problem = "extra"
        elif (installed / relative).read_bytes() != (copy / relative).read_bytes():
            problem = "differs"
        else:
            continue
        mismatches.append({"path": relative, "problem": problem})
    return mismatches


def fingerprint(folder: Path) -> list[tuple] | None:
    """(relative path, type, size, modification time) of folder and everything under it.

    Reads names and metadata only, never file contents; None when folder is absent.
    """
    try:
        top = os.lstat(folder)
    except (FileNotFoundError, NotADirectoryError):
        return None
    entries = [(".", stat.S_IFMT(top.st_mode), 0, top.st_mtime_ns)]
    for current, directories, files in os.walk(folder):
        for name in (*directories, *files):
            path = os.path.join(current, name)
            relative = Path(path).relative_to(folder).as_posix()
            try:
                info = os.lstat(path)
            except OSError:
                entries.append((relative, "unreadable", 0, 0))
                continue
            size = info.st_size if stat.S_ISREG(info.st_mode) else 0
            entries.append((relative, stat.S_IFMT(info.st_mode), size, info.st_mtime_ns))
    return sorted(entries, key=lambda entry: entry[0])


def _first_line(text: str) -> str | None:
    return next((line.strip() for line in text.splitlines() if line.strip()), None)


def _excerpt(text: str, root: Path) -> str:
    """': <last line of agy's error output>' with the temporary folder shown as <temp>, or ''."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[-1].replace(str(root), "<temp>")
    return ": " + (line if len(line) <= 200 else line[:197] + "...")


def _verify_in(root: Path, command: list[str], package: Path, timeout: float) -> dict:
    """The report of every check except the real-home fingerprint, run inside root."""
    copy = root / "package" / PLUGIN_NAME
    shutil.copytree(package, copy, ignore=shutil.ignore_patterns(*COPY_IGNORE))
    home = root / "home"
    work = root / "work"
    work.mkdir()
    environment = isolated_environment(home)
    failures: list[str] = []
    package_version, problem = manifest_version(copy)
    if problem:
        failures.append(f"package_version: {problem}")
    stalled = False

    def call(check: str, *args: str) -> tuple[int, str, str] | None:
        """One agy call; None (with the failure recorded) after a timeout."""
        nonlocal stalled
        if stalled:
            failures.append(f"{check}: {NOT_RUN}")
            return None
        returncode, stdout, stderr = run_agy(command, args, environment, work, timeout)
        if returncode is None:
            stalled = True
            failures.append(f"{check}: agy {' '.join(args[:2])} timed out after {timeout:g} s")
            return None
        return returncode, stdout, stderr

    agy_version = None
    result = call("agy_version", "--version")
    if result is not None:
        returncode, stdout, stderr = result
        if returncode == 0:
            agy_version = _first_line(stdout) or _first_line(stderr)
        if returncode != 0 or not agy_version:
            failures.append(f"agy_version: agy --version exited {returncode}"
                            + ("" if returncode else " without a version") + _excerpt(stderr, root))

    validate = {"errors": None, "warnings": None, "notes": None, "components": None}
    result = call("validate", "plugin", "validate", "--strict", "--json", str(copy))
    if result is not None:
        returncode, stdout, stderr = result
        parsed, data = parse_json(stdout)
        exited, excerpt = f"agy plugin validate exited {returncode}", _excerpt(stderr, root)
        issue = None
        if not parsed:
            issue = "output is not JSON" + (f" ({exited}{excerpt})" if returncode else "")
        else:
            validate = summarize_validation(data)
            if validate["errors"] or validate["warnings"]:
                issue = f"{validate['errors']} error(s) and {validate['warnings']} warning(s)"
            elif returncode and not validate["notes"]:
                # Pass is no error or warning finding (notes allowed, even if --strict exits
                # non-zero for them); a non-zero exit that no finding explains still fails.
                issue = f"{exited} without a finding{excerpt}"
        if issue:
            failures.append(f"validate: {issue}")

    install_rc = None
    result = call("install_rc", "plugin", "install", str(copy))
    if result is not None:
        install_rc, _stdout, stderr = result
        if install_rc != 0:
            failures.append(f"install_rc: agy plugin install exited {install_rc}{_excerpt(stderr, root)}")

    listed = None
    result = call("listed", "plugin", "list", "--json")
    if result is not None:
        returncode, stdout, stderr = result
        parsed, data = parse_json(stdout)
        problems = []
        if returncode != 0:
            problems.append(f"agy plugin list exited {returncode}{_excerpt(stderr, root)}")
        if not parsed:
            problems.append("output is not JSON")
        else:
            listed, found = check_listing(data, package_version, skill_folders(copy))
            problems.extend(found)
        if problems:
            failures.append("listed: " + "; ".join(problems))

    mismatches = compare_trees(home / ".gemini" / "config" / "plugins" / PLUGIN_NAME, copy)
    if mismatches:
        shown = ", ".join(f"{item['path']} ({item['problem']})" for item in mismatches[:5])
        more = f" and {len(mismatches) - 5} more" if len(mismatches) > 5 else ""
        failures.append(f"bytes_equal: {len(mismatches)} mismatch(es): {shown}{more}")

    return {"agy_version": agy_version, "package_version": package_version, "validate": validate,
            "install_rc": install_rc, "listed": listed, "bytes_equal": not mismatches,
            "mismatches": mismatches, "real_home_unchanged": None, "passed": False,
            "failures": failures}


def verify(agy: str, package: Path, *, keep: bool = False,
           timeout: float = CALL_TIMEOUT) -> tuple[dict, Path | None]:
    """Check package in a disposable agy home: (report, the kept temporary folder or None)."""
    command = agy_command(agy)
    package = Path(package)
    if not package.is_dir():
        raise UsageError(f"package folder not found: {package}")
    real_config = Path.home() / ".gemini" / "config"
    before = fingerprint(real_config)
    kept = None
    if keep:
        kept = Path(tempfile.mkdtemp(prefix="agentic-vault-agy-")).resolve()
        print(f"kept: {kept}", file=sys.stderr)  # before the checks, so an error still names it
        report = _verify_in(kept, command, package, timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="agentic-vault-agy-", ignore_cleanup_errors=True) as temporary:
            report = _verify_in(Path(temporary).resolve(), command, package, timeout)
        if os.path.exists(temporary):
            print(f"agentic-vault Antigravity verification: could not remove {temporary}", file=sys.stderr)
    report["real_home_unchanged"] = fingerprint(real_config) == before
    if not report["real_home_unchanged"]:
        report["failures"].append("real_home_unchanged: the real ~/.gemini/config changed during verification")
    report["passed"] = not report["failures"]
    return report, kept


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agy", required=True, metavar="PATH",
                        help="agy executable; a .py path runs with this Python (tests use a fake agy)")
    parser.add_argument("--package", type=Path, metavar="DIR",
                        help="generated package folder (default: <repo>/antigravity)")
    parser.add_argument("--json", type=Path, metavar="OUT", dest="json_out",
                        help="also write the JSON report to OUT")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary package copy and home, and print where they are")
    args = parser.parse_args(argv)
    try:
        report, kept = verify(args.agy, args.package or ROOT / "antigravity", keep=args.keep)
    except UsageError as error:
        print(f"agentic-vault Antigravity verification: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"agentic-vault Antigravity verification failed: {error}", file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2)
    if args.json_out is not None:
        try:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_bytes(text.encode("utf-8") + b"\n")
        except OSError as error:
            print(f"agentic-vault Antigravity verification: cannot write {args.json_out}: {error}",
                  file=sys.stderr)
            return 2
    print(text)
    for failure in report["failures"]:
        print(f"FAIL {failure}", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
