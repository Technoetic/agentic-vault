#!/usr/bin/env python3
"""Diagnose session-start context without changing the vault."""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK_PATH = REPO_ROOT / "hooks" / "session_start.py"
TRUST_BOUNDARY = "diagnosis_only_host_hook_execution_unverified"

# Opt-in host version check (--host-version). Claude Code 2.1.295 fixed an
# async SessionStart hook's unchanged context being added again on every
# resume; this plugin registers an async healthcheck SessionStart hook.
HOST_NAME = "claude-code"
HOST_RECOMMENDED_MIN_PARTS = (2, 1, 295)
HOST_RECOMMENDED_MIN = ".".join(str(part) for part in HOST_RECOMMENDED_MIN_PARTS)
# `claude --version` may run this long; a killed launcher then gets at most the
# reap bound, so the whole probe stays within 5 seconds.
HOST_VERSION_TIMEOUT_SECONDS = 4.0
HOST_VERSION_REAP_SECONDS = 1.0
HOST_VERSION_PARSE_BYTES = 4096
HOST_TRUST_BOUNDARY = "path_executable_version_only_running_host_unverified"
_HOST_VERSION_LINE = re.compile(r"([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})(?:[ \t].*)?")
_HOST_NEXT_ACTIONS = {
    "ok": "none",
    "below_recommended": "update_claude_code",
    "unavailable": "check_claude_version_manually",
    "unparsed": "check_claude_version_manually",
}


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agentic_vault_session_hook", HOOK_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - installation guard
        raise RuntimeError("session hook unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


session_hook = _load_hook()

from vault_healthcheck import HealthcheckError, estimate_tokens, validate_config
from vault_paths import resolve_note_path


SECTION_SPECS = (
    ("handoff", "handoff_note", "handoff_max_tokens", session_hook.HANDOFF_HEADER),
    ("hot", "hot_note", "hot_max_tokens", session_hook.HOT_HEADER),
)
WARNING_STATES = {"missing", "empty", "truncated", "budget_too_small"}
FATAL_STATES = {"unreadable", "unsafe_path"}


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _hook_report(
    *,
    configured_budget: int | None,
    effective_budget: int | None,
    emitted_tokens: int,
    emitted_chars: int,
    would_emit: bool,
) -> dict:
    return {
        "would_emit_context": would_emit,
        "configured_token_budget": configured_budget,
        "effective_token_budget": effective_budget,
        "estimated_emitted_tokens": emitted_tokens,
        # UTF-16 units of the exact stdout, final newline included: the length
        # the host compares with its hook output limit.
        "estimated_emitted_chars": emitted_chars,
        "host_char_cap": session_hook.HOST_MAX_OUTPUT_CHARS,
        "trust_boundary": TRUST_BOUNDARY,
    }


def _terminal_report(status: str, reason_code: str, next_action: str) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "reason_code": reason_code,
        "next_action": next_action,
        "hook": _hook_report(
            configured_budget=None,
            effective_budget=None,
            emitted_tokens=0,
            emitted_chars=0,
            would_emit=False,
        ),
        "sections": {},
    }


def _validated(raw: object) -> dict:
    config = validate_config(raw)
    for key in ("handoff_max_tokens", "hot_max_tokens"):
        if config[key] < 0:
            raise HealthcheckError("session token budget must not be negative")
    return config


def _base_section(status: str, reason_code: str, budget: int, next_action: str) -> dict:
    return {
        "status": status,
        "reason_code": reason_code,
        "configured_token_budget": budget,
        "effective_token_budget": 0,
        "independently_would_emit": False,
        "independently_estimated_tokens": 0,
        "would_emit": False,
        "estimated_emitted_tokens": 0,
        "estimated_emitted_chars": 0,
        "next_action": next_action,
    }


def _truncation_state(
    source_truncated: bool, rendered: session_hook.RenderedSection | None,
) -> tuple[str, str, str]:
    """Return status, reason_code and next_action for one rendered section.

    The source byte limit is reported first: the note is too large to read
    whole, which also explains any later cut. A section left with no room
    at all (rendered is None) was cut by the host output cap.
    """
    if source_truncated:
        return "truncated", "source_byte_limit", "reduce_the_note_or_review_the_budget"
    if rendered is None or rendered.host_capped:
        # The cap is fixed, so no budget change makes the whole notes fit.
        return "truncated", "host_char_cap", "shorten_the_notes_below_the_host_cap"
    if rendered.truncated:
        return "truncated", "token_budget_limit", "reduce_the_note_or_review_the_budget"
    return "ready", "section_ready", "none"


def _diagnose_section(
    vault: Path,
    config: dict,
    *,
    path_key: str,
    budget_key: str,
    header: str,
) -> tuple[dict, session_hook.SectionSource | None]:
    budget = config[budget_key]
    rel_path = config[path_key]
    if budget == 0:
        return _base_section(
            "disabled", "budget_disabled", budget,
            "enable_section_if_context_is_expected",
        ), None
    if not rel_path:
        return _base_section(
            "disabled", "path_disabled", budget,
            "configure_a_note_if_context_is_expected",
        ), None

    try:
        path = resolve_note_path(vault, rel_path, config["deny_zones"])
    except (ValueError, RuntimeError):
        return _base_section(
            "unsafe_path", "note_path_unsafe", budget,
            "choose_a_vault_local_allowed_note",
        ), None
    except OSError:
        return _base_section(
            "unreadable", "note_path_unreadable", budget,
            "restore_note_read_access",
        ), None

    try:
        if not path.is_file():
            section = _base_section(
                "missing", "note_missing", budget,
                "create_the_configured_note_or_disable_the_section",
            )
            section["effective_token_budget"] = budget
            return section, None
        text, source_truncated = session_hook._read_note(path)
    except (OSError, UnicodeError):
        return _base_section(
            "unreadable", "note_unreadable", budget,
            "restore_note_read_access",
        ), None

    if text is None:
        section = _base_section(
            "empty", "note_empty", budget,
            "add_context_or_disable_the_section",
        )
        section["effective_token_budget"] = budget
        return section, None

    source = session_hook.SectionSource(header, text, budget, source_truncated, rel_path)
    # This section alone, under the same token budget and host output cap.
    rendered = session_hook.compose_context([source]).sections[0]
    if rendered is None:
        section = _base_section(
            "budget_too_small", "section_header_exceeds_budget", budget,
            "raise_the_budget_or_disable_the_section",
        )
        section["effective_token_budget"] = budget
        return section, None

    emitted_tokens = estimate_tokens(rendered.text)
    status, reason_code, next_action = _truncation_state(source_truncated, rendered)
    section = _base_section(status, reason_code, budget, next_action)
    section.update(
        effective_token_budget=budget,
        independently_would_emit=True,
        independently_estimated_tokens=emitted_tokens,
        would_emit=True,
        estimated_emitted_tokens=emitted_tokens,
        estimated_emitted_chars=session_hook.utf16_len(rendered.text),
    )
    return section, source


def _isolated_configs(raw: dict) -> tuple[dict, dict] | None:
    neutral = deepcopy(raw)
    for _, path_key, _, _ in SECTION_SPECS:
        neutral[path_key] = ""
    try:
        base = _validated(neutral)
    except (HealthcheckError, TypeError, ValueError):
        return None

    configs: dict[str, dict] = {}
    for name, path_key, _, _ in SECTION_SPECS:
        candidate = deepcopy(neutral)
        if path_key in raw:
            candidate[path_key] = raw[path_key]
        else:
            candidate.pop(path_key, None)
        try:
            configs[name] = _validated(candidate)
        except (HealthcheckError, TypeError, ValueError):
            invalid = deepcopy(base)
            invalid[path_key] = None
            configs[name] = invalid
    return configs["handoff"], configs["hot"]


def diagnose(vault: Path, *, instructions: bool = False) -> tuple[dict, int]:
    config_candidate = vault / session_hook.CONFIG_REL
    try:
        if not config_candidate.is_file():
            return _terminal_report(
                "not_vault", "config_not_found",
                "run_with_a_vault_containing_00_meta_vault_config_json",
            ), 0
        config_path = resolve_note_path(vault, session_hook.CONFIG_REL)
    except ValueError:
        return _terminal_report(
            "invalid_config", "config_path_unsafe", "restore_a_regular_vault_config_file",
        ), 2
    except (OSError, RuntimeError):
        return _terminal_report(
            "invalid_config", "config_unreadable", "restore_vault_config_read_access",
        ), 2

    try:
        raw_bytes, oversized = session_hook._read_bounded(
            config_path, session_hook.MAX_CONFIG_BYTES
        )
    except OSError:
        return _terminal_report(
            "invalid_config", "config_unreadable", "restore_vault_config_read_access",
        ), 2
    if oversized:
        return _terminal_report(
            "invalid_config", "config_too_large", "reduce_vault_config_below_the_read_limit",
        ), 2
    try:
        raw_text = raw_bytes.decode("utf-8-sig")
    except UnicodeError:
        return _terminal_report(
            "invalid_config", "config_encoding_invalid", "save_vault_config_as_utf_8",
        ), 2
    try:
        raw = json.loads(raw_text)
    except (ValueError, RecursionError):
        return _terminal_report(
            "invalid_config", "config_json_invalid", "repair_vault_config_json",
        ), 2

    full_config: dict | None
    try:
        full_config = session_hook._load_config(config_path)
    except (HealthcheckError, OSError, RuntimeError, UnicodeError, ValueError):
        full_config = None

    if full_config is not None:
        configs = {"handoff": full_config, "hot": full_config}
    elif isinstance(raw, dict):
        isolated = _isolated_configs(raw)
        if isolated is None:
            return _terminal_report(
                "invalid_config", "config_schema_invalid", "repair_vault_config_schema",
            ), 2
        configs = {"handoff": isolated[0], "hot": isolated[1]}
    else:
        return _terminal_report(
            "invalid_config", "config_schema_invalid", "repair_vault_config_schema",
        ), 2

    sections: dict[str, dict] = {}
    sources: dict[str, session_hook.SectionSource | None] = {}
    for name, path_key, budget_key, header in SECTION_SPECS:
        config = configs[name]
        if config[path_key] is None:
            budget = config[budget_key]
            sections[name] = _base_section(
                "unsafe_path", "note_path_unsafe", budget,
                "choose_a_vault_local_allowed_note",
            )
            sources[name] = None
            continue
        sections[name], sources[name] = _diagnose_section(
            vault,
            config,
            path_key=path_key,
            budget_key=budget_key,
            header=header,
        )

    fatal = full_config is None or any(
        section["status"] in FATAL_STATES for section in sections.values()
    )
    configured_budget = sum(
        sections[name]["configured_token_budget"] for name, *_ in SECTION_SPECS
    )
    if fatal:
        for section in sections.values():
            section["effective_token_budget"] = 0
            section["would_emit"] = False
            section["estimated_emitted_tokens"] = 0
            section["estimated_emitted_chars"] = 0
        context = session_hook.compose_context([])
        status = "invalid_config"
        reason_code = "section_fatal"
        next_action = "repair_fatal_section_diagnostics"
        exit_code = 2
    else:
        # The hook's own composition: the host output cap applies to the
        # sections together, exactly as they would be emitted.
        ordered = [sources[name] for name, *_ in SECTION_SPECS]
        context = session_hook.compose_context(ordered)
        for (name, *_), source, rendered in zip(SECTION_SPECS, ordered, context.sections):
            if source is None:
                continue
            section_status, section_reason, section_action = _truncation_state(
                source.source_truncated, rendered
            )
            text = "" if rendered is None else rendered.text
            sections[name].update(
                status=section_status,
                reason_code=section_reason,
                next_action=section_action,
                would_emit=rendered is not None,
                estimated_emitted_tokens=estimate_tokens(text),
                estimated_emitted_chars=session_hook.utf16_len(text),
            )
        if any(section["status"] in WARNING_STATES for section in sections.values()):
            status = "degraded"
            reason_code = "section_warning"
            next_action = "review_section_diagnostics"
            exit_code = 1
        else:
            status = "ready"
            reason_code = (
                "context_ready" if context.text else "context_intentionally_disabled"
            )
            next_action = "none"
            exit_code = 0

    report = {
        "schema_version": 1,
        "status": status,
        "reason_code": reason_code,
        "next_action": next_action,
        "hook": _hook_report(
            configured_budget=configured_budget,
            effective_budget=(
                0 if fatal else sum(s["effective_token_budget"] for s in sections.values())
            ),
            emitted_tokens=estimate_tokens(context.text) if context.text else 0,
            emitted_chars=session_hook.utf16_len(context.stdout),
            would_emit=bool(context.text),
        ),
        "sections": sections,
    }
    if instructions:
        from vault_instruction_budget import diagnose_instructions
        from vault_evidence import EvidenceError, stable_read
        # The opt-in reader uses descriptor checks for the config as well as
        # instruction files. Legacy Doctor behavior remains unchanged.
        config = full_config if full_config is not None else raw
        try:
            denied = (*full_config["deny_zones"], ".git") if full_config is not None else (".git",)
            resolver = lambda: resolve_note_path(vault, session_hook.CONFIG_REL, denied)
            initial, _mark, current_bytes = stable_read(resolver, session_hook.MAX_CONFIG_BYTES, contents=True)
            if current_bytes != raw_bytes:
                raise EvidenceError("configuration_changed")
            inventory = diagnose_instructions(vault, config)
            after, _mark, _data = stable_read(resolver, session_hook.MAX_CONFIG_BYTES)
            if after != initial:
                inventory["status"] = "partial"
                if len(inventory["issues"]) < inventory["limits"]["issues"]:
                    inventory["issues"].append({"code": "configuration_changed"})
            report["instructions"] = inventory
        except (EvidenceError, OSError, ValueError, RuntimeError):
            report["instructions"] = diagnose_instructions(vault, None)
        if report["instructions"]["status"] != "complete" and exit_code == 0:
            exit_code = 1
    return report, exit_code


def _host_report(status: str, reason_code: str, version: str | None = None) -> dict:
    return {
        "host": HOST_NAME,
        "status": status,
        "reason_code": reason_code,
        "version": version,
        "recommended_min": HOST_RECOMMENDED_MIN,
        "next_action": _HOST_NEXT_ACTIONS[status],
        "trust_boundary": HOST_TRUST_BOUNDARY,
    }


def _is_windows() -> bool:
    return os.name == "nt"


def _found_only_in_current_directory(executable: str) -> bool:
    """True when a bare-name lookup hit the implicit current-directory search.

    Windows ``shutil.which`` may search the current directory before PATH, so
    a ``claude`` placed in the working directory (for example the vault root)
    is not run unless that directory is an explicit PATH entry. Same rule as
    the Jarvis bridge's claude resolution.
    """
    current = os.path.normcase(os.path.abspath(os.getcwd()))
    if os.path.normcase(os.path.dirname(executable)) != current:
        return False
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if os.path.normcase(os.path.abspath(entry or os.curdir)) == current:
            return False
    return True


def _resolve_host_executable() -> tuple[str | None, str]:
    found = shutil.which("claude")
    if not found:
        return None, "executable_not_found"
    executable = os.path.abspath(found)
    if _found_only_in_current_directory(executable):
        return None, "current_directory_executable_refused"
    # CreateProcess hands .cmd/.bat launchers to cmd.exe, which re-parses the
    # command line; only a native executable runs, as in the Jarvis bridge.
    if _is_windows() and os.path.splitext(executable)[1].lower() != ".exe":
        return None, "windows_non_exe_launcher_not_run"
    return executable, "resolved"


def _stop_host_probe(process: subprocess.Popen) -> None:
    try:
        process.kill()
        # A launcher's child can hold the pipe open: never wait without a bound.
        process.communicate(timeout=HOST_VERSION_REAP_SECONDS)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _run_host_version(executable: str) -> tuple[str, bytes]:
    """Run ``<executable> --version``: fixed argv, no shell, bounded time."""
    # Windows: never open a console window for the probe, as the Jarvis bridge
    # does for claude; other platforms keep the Popen default of 0.
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _is_windows() else 0
    try:
        process = subprocess.Popen(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except (OSError, ValueError):
        return "launch_failed", b""
    try:
        stdout, _ = process.communicate(timeout=HOST_VERSION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _stop_host_probe(process)
        return "timeout", b""
    except BaseException:
        _stop_host_probe(process)
        raise
    if process.returncode != 0:
        return "nonzero_exit", b""
    return "completed", stdout or b""


def _parse_host_version(stdout: bytes) -> tuple[int, int, int] | None:
    """Read ``x.y.z`` at the start of the first non-empty output line."""
    text = stdout[:HOST_VERSION_PARSE_BYTES].decode("utf-8", errors="replace").strip()
    if not text:
        return None
    match = _HOST_VERSION_LINE.fullmatch(text.splitlines()[0].strip())
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def diagnose_host_version() -> dict:
    """Compare the ``claude`` on PATH with the recommended Claude Code minimum.

    Opt-in and fail-soft: runs only ``claude --version`` locally, never raises
    for an ordinary error and never affects the doctor exit code. The result
    describes the executable found on PATH, which need not be the host process
    that runs this plugin's hooks.
    """
    try:
        executable, reason_code = _resolve_host_executable()
        if executable is None:
            return _host_report("unavailable", reason_code)
        outcome, stdout = _run_host_version(executable)
        if outcome != "completed":
            return _host_report("unavailable", outcome)
        version = _parse_host_version(stdout)
        if version is None:
            return _host_report("unparsed", "version_unparsed")
        text = ".".join(str(part) for part in version)
        if version >= HOST_RECOMMENDED_MIN_PARTS:
            return _host_report("ok", "version_at_or_above_recommended_min", text)
        return _host_report("below_recommended", "version_below_recommended_min", text)
    except Exception:  # fail-soft by contract: this check never breaks the doctor
        return _host_report("unavailable", "probe_failed")


def _format_text(report: dict) -> str:
    hook = report["hook"]
    lines = [
        f"agentic-vault doctor: {report['status']} ({report['reason_code']})",
        f"next_action: {report['next_action']}",
        f"hook_would_emit_context: {str(hook['would_emit_context']).lower()}",
        f"configured_token_budget: {hook['configured_token_budget']}",
        f"effective_token_budget: {hook['effective_token_budget']}",
        f"estimated_emitted_tokens: {hook['estimated_emitted_tokens']}",
        f"estimated_emitted_chars: {hook['estimated_emitted_chars']}",
        f"host_char_cap: {hook['host_char_cap']}",
        f"trust_boundary: {hook['trust_boundary']}",
    ]
    for name in ("handoff", "hot"):
        section = report["sections"].get(name)
        if section is not None:
            lines.append(
                f"{name}: {section['status']} ({section['reason_code']}); "
                f"would_emit={str(section['would_emit']).lower()}; "
                f"configured_budget={section['configured_token_budget']}; "
                f"effective_budget={section['effective_token_budget']}"
            )
    if "instructions" in report:
        inventory = report["instructions"]
        lines.extend([
            f"instructions: {inventory['status']}",
            f"instruction_unique_files: {inventory['totals']['unique_files']}",
            f"instruction_characters: {inventory['totals']['characters']}",
            f"instruction_runtime_loading: {inventory['runtime_loading']}",
        ])
    if "host_version" in report:
        host = report["host_version"]
        lines.extend([
            f"host_version: {host['status']} ({host['reason_code']})",
            f"host: {host['host']}",
            f"host_detected_version: {host['version']}",
            f"host_recommended_min: {host['recommended_min']}",
            f"host_next_action: {host['next_action']}",
            f"host_trust_boundary: {host['trust_boundary']}",
        ])
    if "session_evidence" in report:
        from vault_session_evidence import text_lines
        lines.extend(text_lines(report["session_evidence"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path, help="Vault directory to diagnose.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--instructions", action="store_true", help="Include a bounded metadata-only instruction inventory; runtime loading is unverified.")
    parser.add_argument(
        "--host-version", action="store_true",
        help=(
            "Also run the local `claude --version` (fixed argument, no shell, waits at most "
            f"5 seconds) and compare it with the recommended Claude Code {HOST_RECOMMENDED_MIN}. "
            "Fail-soft; never changes the exit code."
        ),
    )
    parser.add_argument(
        "--session-evidence", type=Path, metavar="TRANSCRIPT_JSONL",
        help=(
            "Also read this one Claude Code session transcript (.jsonl; no directory scan) for "
            "SessionStart hook records, instruction loads, first-turn prompt tokens and API error "
            "codes. Prints counts, lengths and status words only, never content. The format is "
            "host-internal and undocumented. Fail-soft; never changes the exit code."
        ),
    )
    parser.add_argument(
        "--session-instruction-paths", action="store_true",
        help=(
            "With --session-evidence, also list the instruction file paths the transcript "
            "records; they can contain account names."
        ),
    )
    args = parser.parse_args(argv)
    if args.session_instruction_paths and args.session_evidence is None:
        parser.error("--session-instruction-paths requires --session-evidence")

    report, exit_code = diagnose(args.vault, instructions=args.instructions)
    if args.host_version:
        # Reported beside the vault diagnosis; the exit code stays the vault's.
        report["host_version"] = diagnose_host_version()
    if args.session_evidence is not None:
        # Loaded only on request; the exit code stays the vault's here as well.
        from vault_session_evidence import diagnose_session_evidence
        report["session_evidence"] = diagnose_session_evidence(
            args.session_evidence, include_paths=args.session_instruction_paths,
        )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(_format_text(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
