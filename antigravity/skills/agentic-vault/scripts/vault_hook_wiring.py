#!/usr/bin/env python3
"""Opt-in, read-only hook-wiring target existence audit for vault_doctor.

Inspired by ECC's read-only config-health contract (affaan-m/ECC, MIT),
adapted to Python standard library only for agentic-vault:

- Read-only and fail-soft: never modifies files, never executes any hook,
  never changes the doctor's exit code.
- No leak: never emits raw hook command strings, environment values, stdin
  payloads, or exception messages. Reports only counts, source file names,
  event names, and sanitized missing target paths.
- Honest coverage: commands whose target cannot be resolved unambiguously
  (unset variables, single-quoted placeholders, non-script commands) are
  counted in ``commands_skipped`` rather than guessed.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from vault_session_evidence import _open_regular

try:
    from vault_evidence import fs_path as _fs_path
except ImportError:  # pragma: no cover - standalone fallback
    def _fs_path(path: Path) -> str:
        return str(path)


SCHEMA_VERSION = 1
TRUST_BOUNDARY = "static_path_existence_only_hooks_not_executed"
SETTINGS_RELS = (".claude/settings.json", ".claude/settings.local.json")
GIT_PRE_COMMIT_REL = "00-meta/scripts/git-hooks/pre-commit"
GIT_PRE_PUSH_REL = "00-meta/scripts/git-hooks/pre-push"
GIT_HOOK_RELS = (GIT_PRE_COMMIT_REL, GIT_PRE_PUSH_REL)
HEALTHCHECK_REL = "00-meta/scripts/vault_healthcheck.py"
MAX_SETTINGS_BYTES = 4 * 1024 * 1024
MAX_MISSING_REPORTED = 50
MAX_REPORTED_PATH_CHARS = 160
SCRIPT_SUFFIXES = frozenset({
    ".py", ".sh", ".ps1", ".js", ".cjs", ".mjs", ".bash", ".zsh", ".cmd", ".bat",
})
_UNEXPANDED_VAR_RE = re.compile(r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%")
_INTERPRETERS = frozenset({
    "python", "python3", "py", "sh", "bash", "zsh", "pwsh", "powershell",
    "powershell.exe", "node", "cmd", "cmd.exe", "env",
})


def _sanitize_reported_path(raw: str, vault: Path) -> str:
    """Return a vault-relative or basename-safe display path without leaking host roots."""
    cleaned = raw.replace("\\", "/").strip()
    try:
        resolved_vault = vault.resolve()
        candidate = Path(raw)
        if candidate.is_absolute():
            rel = candidate.resolve(strict=False).relative_to(resolved_vault)
            cleaned = rel.as_posix()
        else:
            cleaned = cleaned.lstrip("./") or cleaned
    except (OSError, ValueError):
        parts = [part for part in cleaned.split("/") if part and not part.endswith(":")]
        cleaned = "/".join(parts[-3:]) if len(parts) > 3 else "/".join(parts)
    if len(cleaned) > MAX_REPORTED_PATH_CHARS:
        cleaned = "..." + cleaned[-(MAX_REPORTED_PATH_CHARS - 3):]
    return cleaned


def _split_command_tokens(command: str) -> tuple[list[str] | None, set[int]]:
    """Quote-aware tokenizer preserving Windows backslashes and tracking single-quoted tokens."""
    tokens: list[str] = []
    single_quoted: set[int] = set()
    buf: list[str] = []
    quote: str | None = None
    saw_single = False
    has_token = False

    for ch in command:
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            has_token = True
            if ch == "'":
                saw_single = True
            continue
        if ch.isspace():
            if has_token:
                idx = len(tokens)
                tokens.append("".join(buf))
                if saw_single:
                    single_quoted.add(idx)
                buf = []
                saw_single = False
                has_token = False
            continue
        buf.append(ch)
        has_token = True

    if quote is not None:
        return None, single_quoted
    if has_token:
        idx = len(tokens)
        tokens.append("".join(buf))
        if saw_single:
            single_quoted.add(idx)
    return tokens, single_quoted


def _looks_like_script_token(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    lower = token.replace("\\", "/").lower()
    if any(lower.endswith(suffix) for suffix in SCRIPT_SUFFIXES):
        return True
    if "00-meta/scripts/" in lower or "/hooks/" in lower or lower.startswith("hooks/"):
        return True
    return False


def _expand_known_vars(token: str, vault: Path, plugin_root: Path | None) -> tuple[str | None, str]:
    """Expand only CLAUDE_PROJECT_DIR and (when known) CLAUDE_PLUGIN_ROOT."""
    expanded = token
    if "${CLAUDE_PROJECT_DIR}" in expanded:
        expanded = expanded.replace("${CLAUDE_PROJECT_DIR}", str(vault))
    if "$CLAUDE_PROJECT_DIR" in expanded:
        expanded = expanded.replace("$CLAUDE_PROJECT_DIR", str(vault))
    if "${CLAUDE_PLUGIN_ROOT}" in expanded or "$CLAUDE_PLUGIN_ROOT" in expanded:
        if plugin_root is None:
            return None, "unset_plugin_root"
        expanded = expanded.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
        expanded = expanded.replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
    if _UNEXPANDED_VAR_RE.search(expanded):
        return None, "unexpanded_variable"
    return expanded, "ok"


def _extract_handler_targets(
    handler: dict,
    vault: Path,
    plugin_root: Path | None,
) -> tuple[list[tuple[Path, str]], bool]:
    """Return resolved candidate script paths and whether this handler had skipped targets."""
    command = handler.get("command")
    if not isinstance(command, str) or not command.strip():
        return [], True
    raw_args = handler.get("args")
    tokens: list[str]
    single_quoted: set[int]
    if isinstance(raw_args, list) and all(isinstance(item, str) for item in raw_args):
        tokens = [command, *raw_args]
        single_quoted = set()
    else:
        parsed, single_quoted = _split_command_tokens(command)
        if parsed is None:
            return [], True
        tokens = parsed
    if not tokens:
        return [], True

    candidates: list[tuple[Path, str]] = []
    skipped = False
    for idx, token in enumerate(tokens):
        base = Path(token.replace("\\", "/")).name.lower()
        if idx == 0 and base in _INTERPRETERS:
            continue
        if not _looks_like_script_token(token):
            continue
        if idx in single_quoted and "$" in token:
            skipped = True
            continue
        expanded, _ = _expand_known_vars(token, vault, plugin_root)
        if expanded is None:
            skipped = True
            continue
        path = Path(expanded)
        resolved_candidate = path if path.is_absolute() else vault / path
        if os.name != "nt" and "\\" in expanded and not _is_file_safe(resolved_candidate):
            alt = Path(expanded.replace("\\", "/"))
            resolved_candidate = alt if alt.is_absolute() else vault / alt
        candidates.append((resolved_candidate, expanded))
    if not candidates:
        return [], True
    return candidates, skipped


def _load_settings(path: Path) -> tuple[dict | None, str]:
    handle, reason = _open_regular(path)
    if handle is None:
        return None, "missing" if reason == "file_not_found" else "unreadable"
    try:
        with handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except OSError:
        return None, "unreadable"
    if len(data) > MAX_SETTINGS_BYTES:
        return None, "too_large"
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError):
        return None, "invalid_json"
    return (doc, "read") if isinstance(doc, dict) else (None, "not_an_object")


def _is_file_safe(path: Path) -> bool:
    try:
        return os.path.isfile(_fs_path(path))
    except OSError:
        return False


def diagnose_hook_wiring(vault: Path, *, plugin_root: Path | None = None) -> dict:
    """Audit configured hook target paths in a vault without running or printing commands."""
    resolved_vault = Path(vault).resolve()
    env_plugin = os.environ.get("CLAUDE_PLUGIN_ROOT")
    effective_plugin = (
        Path(plugin_root).resolve()
        if plugin_root is not None
        else (Path(env_plugin).resolve() if env_plugin else None)
    )

    settings_checked = 0
    commands_checked = 0
    targets_ok = 0
    targets_missing_total = 0
    commands_skipped = 0
    missing_targets: list[dict[str, str]] = []
    settings_errors: list[dict[str, str]] = []

    for rel in SETTINGS_RELS:
        path = resolved_vault / rel
        doc, state = _load_settings(path)
        if state == "missing":
            continue
        settings_checked += 1
        if doc is None:
            settings_errors.append({"source": rel, "reason": state})
            continue
        hooks_map = doc.get("hooks")
        if not isinstance(hooks_map, dict):
            continue
        for event_name, groups in hooks_map.items():
            if not isinstance(event_name, str) or not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                entries = group.get("hooks")
                if not isinstance(entries, list):
                    continue
                for handler in entries:
                    if not isinstance(handler, dict):
                        continue
                    commands_checked += 1
                    candidates, was_skipped = _extract_handler_targets(
                        handler, resolved_vault, effective_plugin,
                    )
                    if not candidates:
                        commands_skipped += 1
                        continue
                    if was_skipped:
                        commands_skipped += 1
                    for target_path, raw_target in candidates:
                        if _is_file_safe(target_path):
                            targets_ok += 1
                        else:
                            targets_missing_total += 1
                            if len(missing_targets) < MAX_MISSING_REPORTED:
                                missing_targets.append({
                                    "source": rel,
                                    "event": event_name,
                                    "missing_path": _sanitize_reported_path(raw_target, resolved_vault),
                                })

    # Only pre-commit requires 00-meta/scripts/vault_healthcheck.py; pre-push alone does not.
    pre_commit_present = _is_file_safe(resolved_vault / GIT_PRE_COMMIT_REL)
    pre_push_present = _is_file_safe(resolved_vault / GIT_PRE_PUSH_REL)
    git_hooks_present = int(pre_commit_present) + int(pre_push_present)
    if pre_commit_present:
        if _is_file_safe(resolved_vault / HEALTHCHECK_REL):
            targets_ok += 1
        else:
            targets_missing_total += 1
            if len(missing_targets) < MAX_MISSING_REPORTED:
                missing_targets.append({
                    "source": GIT_PRE_COMMIT_REL,
                    "event": "git-pre-commit",
                    "missing_path": HEALTHCHECK_REL,
                })

    if settings_errors:
        status = "unreadable_settings"
        reason_code = "settings_unreadable"
        next_action = "repair_vault_settings_json"
    elif targets_missing_total > 0:
        status = "missing_targets"
        reason_code = "hook_target_missing"
        next_action = "restore_missing_hook_scripts_or_update_settings"
    elif settings_checked == 0 and git_hooks_present == 0:
        status = "none"
        reason_code = "no_vault_hooks_configured"
        next_action = "none"
    else:
        status = "ok"
        reason_code = "all_checked_targets_present"
        next_action = "none"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason_code": reason_code,
        "next_action": next_action,
        "trust_boundary": TRUST_BOUNDARY,
        "settings_checked": settings_checked,
        "git_hooks_present": git_hooks_present,
        "commands_checked": commands_checked,
        "targets_ok": targets_ok,
        "targets_missing": targets_missing_total,
        "commands_skipped": commands_skipped,
        "missing_targets": missing_targets,
        "settings_errors": settings_errors,
    }


def text_lines(report: dict) -> list[str]:
    lines = [
        f"hook_wiring: {report['status']} ({report['reason_code']})",
        f"hook_wiring_settings_checked: {report['settings_checked']}",
        f"hook_wiring_commands_checked: {report['commands_checked']}",
        f"hook_wiring_targets_ok: {report['targets_ok']}",
        f"hook_wiring_targets_missing: {report['targets_missing']}",
        f"hook_wiring_commands_skipped: {report['commands_skipped']}",
        f"hook_wiring_trust_boundary: {report['trust_boundary']}",
    ]
    for item in report.get("missing_targets", ()):
        lines.append(
            f"hook_wiring_missing: source={item['source']} event={item['event']} path={item['missing_path']}"
        )
    for err in report.get("settings_errors", ()):
        lines.append(f"hook_wiring_settings_error: source={err['source']} reason={err['reason']}")
    return lines
