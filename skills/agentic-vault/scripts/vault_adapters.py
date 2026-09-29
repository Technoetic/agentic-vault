#!/usr/bin/env python3
"""Report optional tool adapters for a vault without changing it.

The only adapter today is Aside (an AI browser with a CLI). The report tells
/vault-init, /vault-upgrade and /vault-doctor what is installed and what may be
offered; it never installs, copies, runs the helper or edits settings itself.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ASIDE_ASSETS = REPO_ROOT / "assets" / "adapters" / "aside"
BUNDLED_HELPER = ASIDE_ASSETS / "aside-up.ps1"

CONFIG_REL = "00-meta/vault-config.json"
HELPER_REL = "00-meta/scripts/aside-up.ps1"
GUIDE_REL = "20-knowledge/tools/Aside CLI 운영 가이드.md"
SETTINGS_RELS = (".claude/settings.json", ".claude/settings.local.json")
CLAUSE_BEGIN = "agentic-vault:adapter aside begin"
CLAUSE_END = "agentic-vault:adapter aside end"
HELPER_STAMP_RE = re.compile(r"agentic-vault:adapter aside-up engine=(\d+(?:\.\d+)*)")


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


def _inside(vault: Path, rel: str) -> Path | None:
    """Return the path when it stays inside the vault without links, else None."""
    path = vault / rel
    current = vault
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return None
    try:
        path.resolve().relative_to(vault.resolve())
    except (OSError, ValueError):
        return None
    return path


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
    text = _read(path)
    if text is None or not path.is_file():
        return {**report, "state": "unreadable"}
    installed = _stamp(text)
    report["installed_stamp"] = installed
    if installed is None or bundled is None:
        return {**report, "state": "unstamped"}
    if _version(installed) < _version(bundled):
        state = "outdated"
    elif _version(installed) > _version(bundled):
        state = "newer"
    else:
        state = "current" if _normalized(text) == _normalized(bundled_text) else "modified"
    return {**report, "state": state}


def clause_status(vault: Path) -> dict:
    path = _inside(vault, "CLAUDE.md")
    text = _read(path) if path is not None and path.is_file() else None
    if text is None:
        return {"path": "CLAUDE.md", "state": "missing" if path is not None else "unsafe_path"}
    begin, end = text.count(CLAUSE_BEGIN), text.count(CLAUSE_END)
    if begin == 0 and end == 0:
        state = "absent"
    elif begin == 1 and end == 1 and text.index(CLAUSE_BEGIN) < text.index(CLAUSE_END):
        state = "present"
    else:
        state = "broken_markers"
    return {"path": "CLAUDE.md", "state": state}


def _hook_commands(data: object) -> list[str]:
    commands: list[str] = []
    if not isinstance(data, dict):
        return commands
    groups = data.get("hooks", {}).get("PreToolUse", []) if isinstance(data.get("hooks"), dict) else []
    for group in groups if isinstance(groups, list) else []:
        handlers = group.get("hooks", []) if isinstance(group, dict) else []
        for handler in handlers if isinstance(handlers, list) else []:
            if isinstance(handler, dict) and isinstance(handler.get("command"), str):
                commands.append(handler["command"])
    return commands


def hook_status(vault: Path) -> dict:
    configured: list[str] = []
    unreadable: list[str] = []
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
        if any("aside-up.ps1" in command for command in _hook_commands(data)):
            configured.append(rel)
    state = "configured" if configured else ("unreadable" if unreadable else "absent")
    return {"state": state, "configured_in": configured, "unreadable": unreadable}


def guide_status(vault: Path) -> dict:
    path = _inside(vault, GUIDE_REL)
    return {"path": GUIDE_REL, "present": bool(path is not None and path.is_file())}


def _actions(platform: str, cli: bool, helper: dict, clause: dict, hook: dict, guide: dict) -> list[str]:
    actions: list[str] = []
    if helper["state"] == "outdated":
        actions.append("offer_helper_update")
    elif helper["state"] in {"modified", "unstamped", "newer", "unreadable", "unsafe_path"}:
        actions.append("review_helper")
    if clause["state"] == "broken_markers":
        actions.append("review_clause_markers")
    if hook["state"] == "unreadable":
        actions.append("review_settings")
    installed_any = clause["state"] == "present" or helper["state"] != "missing"
    if cli and not installed_any:
        actions.append("offer_install")
    elif clause["state"] == "present":
        if platform == "windows" and helper["state"] == "missing":
            actions.append("offer_helper")
        if not guide["present"]:
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
    return "\n".join([
        f"agentic-vault adapters: ok (platform={report['platform']})",
        f"aside.cli_on_path: {str(aside['cli_on_path']).lower()}",
        f"aside.helper: {aside['helper']['state']} "
        f"(installed={aside['helper']['installed_stamp']}, bundled={aside['helper']['bundled_stamp']})",
        f"aside.clause: {aside['clause']['state']}",
        f"aside.hook: {aside['hook']['state']}",
        f"aside.guide_note: {str(aside['guide_note']['present']).lower()}",
        f"aside.actions: {', '.join(aside['actions']) or 'none'}",
    ])


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
