#!/usr/bin/env python3
"""Report optional tool adapters for a vault without changing it.

The only adapter today is Aside (an AI browser with a CLI). The report tells
/vault-init, /vault-upgrade and /vault-doctor what is installed and what may be
offered; it never installs, copies, runs the helper or edits settings itself.
Paths that pass through a symlink or a Windows reparse point (junction) are not
read.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from vault_paths import _is_link_or_reparse


REPO_ROOT = Path(__file__).resolve().parents[3]
ASIDE_ASSETS = REPO_ROOT / "assets" / "adapters" / "aside"
BUNDLED_HELPER = ASIDE_ASSETS / "aside-up.ps1"

CONFIG_REL = "00-meta/vault-config.json"
HELPER_REL = "00-meta/scripts/aside-up.ps1"
GUIDE_REL = "20-knowledge/tools/Aside CLI 운영 가이드.md"
SETTINGS_RELS = (".claude/settings.json", ".claude/settings.local.json")
CLAUSE_BEGIN = "agentic-vault:adapter aside begin"
CLAUSE_END = "agentic-vault:adapter aside end"
CLAUSE_BEGIN_RE = re.compile(r"^[ \t]*<!--[ \t]*agentic-vault:adapter aside begin\b", re.MULTILINE)
CLAUSE_END_RE = re.compile(r"^[ \t]*<!--[ \t]*agentic-vault:adapter aside end[ \t]*-->", re.MULTILINE)
HELPER_STAMP_RE = re.compile(r"agentic-vault:adapter aside-up engine=(\S+)")
VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
HOOK_FILTERS = {"Bash(aside *)", "PowerShell(aside *)"}
# `$CLAUDE_PROJECT_DIR` written for one shell: empty under a PowerShell hook
# shell, which makes `-File "$CLAUDE_PROJECT_DIR/..."` point at the drive root.
SHELL_BOUND_VAR_RE = re.compile(r"(?<!env:)\$CLAUDE_PROJECT_DIR(?!\})")


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _compare(left: str, right: str) -> int:
    a, b = _version(left), _version(right)
    width = max(len(a), len(b))
    a, b = a + (0,) * (width - len(a)), b + (0,) * (width - len(b))
    return (a > b) - (a < b)


def _inside(vault: Path, rel: str) -> Path | None:
    """Return the path when no component is a link or reparse point, else None."""
    current = vault
    try:
        if _is_link_or_reparse(vault):
            return None
    except OSError:
        return None
    for part in Path(rel).parts:
        current = current / part
        try:
            if _is_link_or_reparse(current):
                return None
        except FileNotFoundError:
            break
        except OSError:
            return None
    try:
        (vault / rel).resolve().relative_to(vault.resolve())
    except (OSError, ValueError):
        return None
    return vault / rel


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _stamp(text: str) -> str | None:
    match = HELPER_STAMP_RE.search(text[:400])
    return match.group(1) if match else None


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip("\n")


def helper_status(vault: Path) -> dict:
    bundled_text = _read(BUNDLED_HELPER) or ""
    bundled = _stamp(bundled_text)
    report = {"path": HELPER_REL, "bundled_stamp": bundled, "installed_stamp": None}
    path = _inside(vault, HELPER_REL)
    if path is None:
        return {**report, "state": "unsafe_path"}
    if not path.exists():
        return {**report, "state": "missing"}
    text = _read(path) if path.is_file() else None
    if text is None:
        return {**report, "state": "unreadable"}
    installed = _stamp(text)
    report["installed_stamp"] = installed
    if installed is None or bundled is None or not VERSION_RE.match(installed) or not VERSION_RE.match(bundled):
        return {**report, "state": "unstamped"}
    order = _compare(installed, bundled)
    if order < 0:
        state = "outdated"
    elif order > 0:
        state = "newer"
    else:
        state = "current" if _normalized(text) == _normalized(bundled_text) else "modified"
    return {**report, "state": state}


def clause_status(vault: Path) -> dict:
    path = _inside(vault, "CLAUDE.md")
    if path is None:
        return {"path": "CLAUDE.md", "state": "unsafe_path"}
    if not path.exists():
        return {"path": "CLAUDE.md", "state": "missing"}
    text = _read(path) if path.is_file() else None
    if text is None:
        return {"path": "CLAUDE.md", "state": "unreadable"}
    begins = list(CLAUSE_BEGIN_RE.finditer(text))
    ends = list(CLAUSE_END_RE.finditer(text))
    if not begins and not ends:
        state = "absent"
    elif len(begins) == 1 and len(ends) == 1 and begins[0].start() < ends[0].start():
        state = "present"
    else:
        state = "broken_markers"
    return {"path": "CLAUDE.md", "state": state}


def _hook_handlers(data: object) -> list[dict]:
    handlers: list[dict] = []
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return handlers
    groups = data["hooks"].get("PreToolUse", [])
    for group in groups if isinstance(groups, list) else []:
        items = group.get("hooks", []) if isinstance(group, dict) else []
        for handler in items if isinstance(items, list) else []:
            if isinstance(handler, dict) and isinstance(handler.get("command"), str):
                handlers.append(handler)
    return handlers


def _hook_commands(data: object) -> list[str]:
    return [handler["command"] for handler in _hook_handlers(data)]


def hook_status(vault: Path) -> dict:
    configured: list[str] = []
    unreadable: list[str] = []
    problems: list[str] = []
    for rel in SETTINGS_RELS:
        path = _inside(vault, rel)
        if path is None:
            unreadable.append(rel)
            continue
        if not path.exists():
            continue
        text = _read(path)
        try:
            data = json.loads(text) if text is not None else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            unreadable.append(rel)
            continue
        handlers = [h for h in _hook_handlers(data) if "aside-up.ps1" in h["command"]]
        if not handlers:
            continue
        configured.append(rel)
        for handler in handlers:
            if_filter = handler.get("if")
            if not isinstance(if_filter, str) or if_filter not in HOOK_FILTERS:
                problems.append(f"{rel}: handler without an aside `if` filter")
            command = handler["command"]
            if SHELL_BOUND_VAR_RE.search(command) or "$env:CLAUDE_PROJECT_DIR" not in command:
                problems.append(f"{rel}: command does not resolve the vault through $env:CLAUDE_PROJECT_DIR "
                                "inside PowerShell, so the path depends on the hook shell or working directory")
    if problems:
        state = "needs_review"
    elif configured:
        state = "configured"
    elif unreadable:
        state = "unreadable"
    else:
        state = "absent"
    return {"state": state, "configured_in": configured, "unreadable": unreadable, "problems": problems}


def guide_status(vault: Path) -> dict:
    path = _inside(vault, GUIDE_REL)
    return {"path": GUIDE_REL, "present": bool(path is not None and path.is_file()),
            "unsafe_path": path is None}


def _actions(platform: str, cli: bool, helper: dict, clause: dict, hook: dict, guide: dict) -> list[str]:
    actions: list[str] = []
    if helper["state"] == "outdated":
        actions.append("offer_helper_update")
    elif helper["state"] in {"modified", "unstamped", "newer", "unreadable", "unsafe_path"}:
        actions.append("review_helper")
    if clause["state"] in {"broken_markers", "unreadable", "unsafe_path"}:
        actions.append("review_clause")
    if hook["unreadable"] or hook["problems"]:
        actions.append("review_settings")
    installed_any = clause["state"] not in {"missing", "absent"} or helper["state"] != "missing"
    if cli and not installed_any:
        actions.append("offer_install")
    elif clause["state"] == "present":
        if platform == "windows" and helper["state"] == "missing":
            actions.append("offer_helper")
        if not guide["present"] and not guide["unsafe_path"]:
            actions.append("offer_guide_note")
        if platform == "windows" and helper["state"] == "current" and hook["state"] == "absent":
            actions.append("hook_optional")
    return actions


def diagnose(vault: Path) -> tuple[dict, int]:
    vault = vault.expanduser()
    if not (vault / CONFIG_REL).is_file():
        return {"schema_version": 1, "status": "not_vault", "reason_code": "config_not_found",
                "adapters": {}}, 2
    platform = _platform()
    cli = shutil.which("aside") is not None
    helper = helper_status(vault)
    clause = clause_status(vault)
    hook = hook_status(vault)
    guide = guide_status(vault)
    aside = {
        "cli_on_path": cli,
        "helper_supported": platform == "windows",
        "helper": helper,
        "clause": clause,
        "hook": hook,
        "guide_note": guide,
        "actions": _actions(platform, cli, helper, clause, hook, guide),
    }
    return {"schema_version": 1, "status": "ok", "platform": platform,
            "adapters": {"aside": aside}}, 0


def _format_text(report: dict) -> str:
    if report["status"] != "ok":
        return f"agentic-vault adapters: {report['status']} ({report['reason_code']})"
    aside = report["adapters"]["aside"]
    lines = [
        f"agentic-vault adapters: ok (platform={report['platform']})",
        f"aside.cli_on_path: {str(aside['cli_on_path']).lower()}",
        f"aside.helper: {aside['helper']['state']} "
        f"(installed={aside['helper']['installed_stamp']}, bundled={aside['helper']['bundled_stamp']})",
        f"aside.clause: {aside['clause']['state']}",
        f"aside.hook: {aside['hook']['state']}",
        f"aside.guide_note: {str(aside['guide_note']['present']).lower()}",
        f"aside.actions: {', '.join(aside['actions']) or 'none'}",
    ]
    lines.extend(f"aside.hook.problem: {problem}" for problem in aside["hook"]["problems"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", required=True, type=Path, help="Vault directory to inspect.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    report, exit_code = diagnose(args.vault)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(_format_text(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
