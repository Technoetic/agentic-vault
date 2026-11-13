#!/usr/bin/env python3
"""Opt-in, read-only view of where this plugin's on or off state comes from.

``vault_doctor.py --plugin-scope`` answers three questions about one vault and
never changes it or the doctor's exit code:

- Which of the settings files it can read turns this plugin on or off. Claude
  Code documents six sources of ``enabledPlugins`` and merges them key by key, so
  a value removed from a high source lets a lower one take effect. The user,
  project and local files are read here; ``managed``, ``flag`` and ``add_dir``
  stay ``unknown``, so a winner among the files is not the merged value.
- Whether the vault's own settings also register SessionStart hooks while the
  plugin is on. Only the number of hook entries is taken; a command is neither
  read nor printed, so nothing here depends on how a shell would parse one. A
  count shows a possible double injection; ``--session-evidence`` shows a real one.
- Whether the vault's git hook copy of the health check, at its one fixed path,
  has an engine stamp and how old that stamp is. No other path is looked at, and
  whether some hook runs that copy on every session is not read here either.

``--plugin-scope-host`` also runs ``claude plugin list --json`` in the vault
(fixed argument list, no shell, bounded time) as a cross-check of the merged
value. Any failure ends as ``host_unavailable``.

Only fixed words, version numbers and counts leave this module: never another
plugin's key or value, an environment value, a command, a path or exception text.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from vault_adapters import CONFIG_REL, VERSION_RE, _inside, _read, _stamp, helper_status
from vault_paths import zone_matches
from vault_session_evidence import _open_regular


SCHEMA_VERSION = 1
TRUST_BOUNDARY = "settings_files_only_other_sources_unknown"
PLUGIN_NAME = "agentic-vault"

# Highest precedence first, from the Claude Code plugin loading documentation
# (docs/reliability.md cites it). The value that applies to an id is the one from
# the highest source that mentions it.
SOURCE_ORDER = ("managed", "flag", "local", "project", "user", "add_dir")
PROJECT_RELS = {"local": ".claude/settings.local.json", "project": ".claude/settings.json"}
MAX_SETTINGS_BYTES = 4 * 1024 * 1024
MISSING_REASONS = {"file_not_found": "missing", "not_a_regular_file": "not_a_regular_file"}

HEALTHCHECK_REL = "00-meta/scripts/vault_healthcheck.py"
HEALTHCHECK_STAMP_RE = re.compile(r"agentic-vault:healthcheck engine=(\S+)")
BUNDLED_HEALTHCHECK = Path(__file__).resolve().with_name("vault_healthcheck.py")

# `claude plugin list --json` took under one second on one Windows PC, 2026-11-07.
HOST_LIST_ARGUMENTS = ("plugin", "list", "--json")
HOST_LIST_TIMEOUT_SECONDS = 8.0
HOST_LIST_REAP_SECONDS = 1.0
MAX_HOST_OUTPUT_BYTES = 4 * 1024 * 1024
HOST_ENVIRONMENT = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

# Reported in this order; the first one names the report's reason and next action.
FINDINGS = {
    "plugin_and_vault_session_hooks": "keep_one_session_context_hook",
    "host_merged_value_differs": "check_the_sources_that_are_not_read_here",
    "healthcheck_copy_unstamped": "align_the_healthcheck_copy_with_vault_upgrade",
    "healthcheck_copy_outdated": "align_the_healthcheck_copy_with_vault_upgrade",
}
_ON_OFF = ("enabled", "disabled")


def _is_this_plugin(plugin_id: object) -> bool:
    """True for ``agentic-vault@<any marketplace>``, the id form of an installed plugin."""
    if not isinstance(plugin_id, str):
        return False
    name, separator, marketplace = plugin_id.partition("@")
    return name == PLUGIN_NAME and bool(separator and marketplace)


def _load(path: Path) -> tuple[dict | None, str]:
    """One settings document and its closed file state; never its content or error."""
    handle, reason = _open_regular(path)
    if handle is None:
        return None, MISSING_REASONS.get(reason, "unreadable")
    try:
        with handle:
            data = handle.read(MAX_SETTINGS_BYTES + 1)
    except OSError:
        return None, "unreadable"
    if len(data) > MAX_SETTINGS_BYTES:
        return None, "too_large"
    try:
        document = json.loads(data.decode("utf-8-sig"))
    except UnicodeError:
        return None, "invalid_encoding"
    except (ValueError, RecursionError):
        return None, "invalid_json"
    return (document, "read") if isinstance(document, dict) else (None, "not_an_object")


def _deny_zones(vault: Path) -> tuple[str, ...]:
    """The vault's own deny zones; none when its config cannot be read here."""
    path = _inside(vault, CONFIG_REL)
    document = None if path is None else _load(path)[0]
    zones = document.get("deny_zones") if document is not None else None
    return tuple(zone for zone in zones if isinstance(zone, str)) if isinstance(zones, list) else ()


def _denied(rel: str, zones: tuple[str, ...]) -> bool:
    parts = tuple(rel.split("/"))
    return any(zone_matches(parts, zone) for zone in zones)


def _project_file(vault: Path, rel: str, zones: tuple[str, ...]) -> tuple[dict | None, str]:
    if _denied(rel, zones):
        return None, "denied"
    path = _inside(vault, rel)
    return (None, "unsafe_path") if path is None else _load(path)


def _user_file() -> tuple[dict | None, str]:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        folder = Path(configured) if configured else Path.home() / ".claude"
    except RuntimeError:  # no home folder is known
        return None, "unreadable"
    return _load(folder / "settings.json")


def _plugin_value(document: dict | None, state: str) -> str:
    """enabled, disabled, unset or unknown; only this plugin's keys are looked at."""
    if state == "missing":
        return "unset"
    if document is None:
        return "unknown"
    plugins = document.get("enabledPlugins")
    if plugins is None:
        return "unset"
    if not isinstance(plugins, dict):
        return "unknown"
    keys = [key for key in plugins if _is_this_plugin(key)]
    if not keys:
        return "unset"
    values = {plugins[key] if isinstance(plugins[key], bool) else None for key in keys}
    if values == {True}:
        return "enabled"
    return "disabled" if values == {False} else "unknown"


def _file_winner(sources: dict) -> dict:
    """The highest file that mentions the plugin; one that cannot be told stops the walk."""
    for scope in SOURCE_ORDER:
        source = sources[scope]
        if source["file"] != "not_read" and source["value"] != "unset":
            return {"scope": scope, "value": source["value"]}
    return {"scope": "none", "value": "unset"}


def _session_hook_count(document: dict | None, state: str) -> int | None:
    """SessionStart hook entries of one file, or None when that cannot be told.

    Entries are counted as objects: no key inside an entry is read.
    """
    if state == "missing":
        return 0
    if document is None:
        return None
    hooks = document.get("hooks")
    if hooks is None:
        return 0
    if not isinstance(hooks, dict):
        return None
    groups = hooks.get("SessionStart")
    if groups is None:
        return 0
    if not isinstance(groups, list):
        return None
    count = 0
    for group in groups:
        entries = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(entries, list):
            return None
        count += sum(isinstance(entry, dict) for entry in entries)
    return count


def _host_block(status: str, reason_code: str, enabled: str | None = None) -> dict:
    return {"status": status, "reason_code": reason_code, "enabled": enabled,
            "comparison": "not_compared"}


def _stop(process: subprocess.Popen) -> None:
    try:
        process.kill()
        # A launcher's child can hold the pipe open: never wait without a bound.
        process.communicate(timeout=HOST_LIST_REAP_SECONDS)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _run_host_list(executable: str, vault: Path) -> tuple[str, bytes]:
    """Run ``<executable> plugin list --json`` in the vault: no shell, bounded time."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [executable, *HOST_LIST_ARGUMENTS],
            cwd=str(vault),
            env={**os.environ, **HOST_ENVIRONMENT},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except (OSError, ValueError):
        return "launch_failed", b""
    try:
        stdout, _ = process.communicate(timeout=HOST_LIST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _stop(process)
        return "timeout", b""
    except BaseException:
        _stop(process)
        raise
    if process.returncode != 0:
        return "nonzero_exit", b""
    if stdout is not None and len(stdout) > MAX_HOST_OUTPUT_BYTES:
        return "output_too_large", b""
    return "completed", stdout or b""


def _host_value(stdout: bytes) -> dict:
    """Read this plugin's ``enabled`` from the host's list; nothing else is kept."""
    try:
        document = json.loads(stdout.decode("utf-8-sig"))
    except (ValueError, RecursionError):  # UnicodeError is a ValueError
        return _host_block("host_unavailable", "output_unparsed")
    if not isinstance(document, list):
        return _host_block("host_unavailable", "output_unparsed")
    values = []
    for item in document:
        if isinstance(item, dict) and _is_this_plugin(item.get("id")):
            enabled = item.get("enabled")
            values.append(enabled if isinstance(enabled, bool) else None)
    if not values:
        return _host_block("not_listed", "plugin_not_listed")
    if set(values) == {True}:
        return _host_block("ok", "listed", "enabled")
    return _host_block("ok", "listed", "disabled" if set(values) == {False} else "unknown")


def _host_check(vault: Path, resolve_host: Callable | None) -> dict:
    if resolve_host is None:
        return _host_block("host_unavailable", "executable_not_found")
    try:
        executable, reason_code = resolve_host()
        if executable is None:
            return _host_block("host_unavailable", reason_code)
        outcome, stdout = _run_host_list(executable, vault)
    except Exception:  # fail-soft by contract: a fixed word, no exception text
        return _host_block("host_unavailable", "probe_failed")
    if outcome != "completed":
        return _host_block("host_unavailable", outcome)
    return _host_value(stdout)


def _engine(stamp: object) -> str | None:
    """A stamp that is a version number; any other text of the file is not printed."""
    return stamp if isinstance(stamp, str) and VERSION_RE.match(stamp) else None


def _healthcheck_copy(vault: Path, zones: tuple[str, ...]) -> dict:
    bundled = _engine(_stamp(_read(BUNDLED_HEALTHCHECK) or "", HEALTHCHECK_STAMP_RE))
    if _denied(HEALTHCHECK_REL, zones):
        return {"state": "denied", "installed_engine": None, "bundled_engine": bundled}
    status = helper_status(vault, HEALTHCHECK_REL, BUNDLED_HEALTHCHECK, HEALTHCHECK_STAMP_RE)
    state = status["state"]
    if bundled is None and state == "unstamped":
        state = "not_compared"  # a damaged install says nothing about the copy
    return {"state": state, "installed_engine": _engine(status["installed_stamp"]),
            "bundled_engine": bundled}


def _findings(*, value: str, session_hooks: dict, comparison: str, copy_state: str) -> list[str]:
    found = set()
    if value == "enabled" and any(session_hooks.values()):
        found.add("plugin_and_vault_session_hooks")
    if comparison == "differs":
        found.add("host_merged_value_differs")
    if copy_state in ("unstamped", "outdated"):
        found.add(f"healthcheck_copy_{copy_state}")
    return [finding for finding in FINDINGS if finding in found]


def _report(vault: Path, host: bool, resolve_host: Callable | None) -> dict:
    zones = _deny_zones(vault)
    documents = {
        "local": _project_file(vault, PROJECT_RELS["local"], zones),
        "project": _project_file(vault, PROJECT_RELS["project"], zones),
        "user": _user_file(),
    }
    sources = {}
    for scope in SOURCE_ORDER:
        if scope in documents:
            document, state = documents[scope]
            sources[scope] = {"value": _plugin_value(document, state), "file": state}
        else:
            sources[scope] = {"value": "unknown", "file": "not_read"}
    winner = _file_winner(sources)
    session_hooks = {
        scope: _session_hook_count(*documents[scope]) for scope in ("project", "local")
    }
    host_report = (
        _host_check(vault, resolve_host) if host
        else _host_block("not_requested", "not_requested")
    )
    host_value = host_report["enabled"]
    if host_value in _ON_OFF and winner["value"] in _ON_OFF:
        host_report["comparison"] = "agrees" if host_value == winner["value"] else "differs"
    copy = _healthcheck_copy(vault, zones)
    findings = _findings(
        # The host's merged value, when it gave one, decides over the files alone.
        value=host_value if host_value in _ON_OFF else winner["value"],
        session_hooks=session_hooks, comparison=host_report["comparison"],
        copy_state=copy["state"],
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "attention" if findings else "ok",
        "reason_code": findings[0] if findings else "no_findings",
        "next_action": FINDINGS[findings[0]] if findings else "none",
        "trust_boundary": TRUST_BOUNDARY,
        "precedence": list(SOURCE_ORDER),
        "sources": sources,
        "file_winner": winner,
        "session_hooks": session_hooks,
        "host": host_report,
        "healthcheck_copy": copy,
        "findings": findings,
    }


def diagnose_plugin_scope(vault: Path, *, host: bool = False,
                          resolve_host: Callable | None = None) -> dict:
    """Report the scopes of this plugin's state for one vault.

    Opt-in and fail-soft: never raises for an ordinary error and never puts a
    path or exception text in the report. ``resolve_host`` returns ``(executable,
    reason_code)``; the doctor passes its own rule for finding ``claude``.
    """
    try:
        return _report(Path(vault), host, resolve_host)
    except Exception:  # fail-soft by contract: no exception text, which may quote content
        return {
            "schema_version": SCHEMA_VERSION, "status": "unknown", "reason_code": "probe_failed",
            "next_action": "check_the_plugin_install", "trust_boundary": TRUST_BOUNDARY,
        }


def _text(value: object) -> str:
    return "unknown" if value is None else str(value)


def text_lines(report: dict) -> list[str]:
    """The doctor's plain-text lines: the same closed values as the JSON."""
    lines = [
        f"plugin_scope: {report['status']} ({report['reason_code']})",
        f"plugin_scope_trust_boundary: {report['trust_boundary']}",
    ]
    if "sources" in report:
        for scope in SOURCE_ORDER:
            source = report["sources"][scope]
            lines.append(
                f"plugin_scope_source: scope={scope}; value={source['value']}; file={source['file']}"
            )
        winner = report["file_winner"]
        hooks = report["session_hooks"]
        host = report["host"]
        copy = report["healthcheck_copy"]
        lines.extend([
            f"plugin_scope_file_winner: scope={winner['scope']}; value={winner['value']}",
            f"plugin_scope_session_hooks: project={_text(hooks['project'])}; "
            f"local={_text(hooks['local'])}",
            f"plugin_scope_host: {host['status']} ({host['reason_code']})",
            f"plugin_scope_host_value: enabled={_text(host['enabled'])}; "
            f"comparison={host['comparison']}",
            f"plugin_scope_healthcheck_copy: state={copy['state']}; "
            f"installed_engine={_text(copy['installed_engine'])}; "
            f"bundled_engine={_text(copy['bundled_engine'])}",
        ])
        lines.extend(f"plugin_scope_finding: {finding}" for finding in report["findings"])
    lines.append(f"plugin_scope_next_action: {report['next_action']}")
    return lines
