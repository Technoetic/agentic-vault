#!/usr/bin/env python3
"""Diagnose session-start context without changing the vault."""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK_PATH = REPO_ROOT / "hooks" / "session_start.py"
TRUST_BOUNDARY = "diagnosis_only_host_hook_execution_unverified"


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
    would_emit: bool,
) -> dict:
    return {
        "would_emit_context": would_emit,
        "configured_token_budget": configured_budget,
        "effective_token_budget": effective_budget,
        "estimated_emitted_tokens": emitted_tokens,
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
        "next_action": next_action,
    }


def _diagnose_section(
    vault: Path,
    config: dict,
    *,
    path_key: str,
    budget_key: str,
    header: str,
) -> tuple[dict, str | None]:
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

    rendered = session_hook._render_section(
        header,
        text,
        budget,
        source_truncated=source_truncated,
    )
    if rendered is None:
        section = _base_section(
            "budget_too_small", "section_header_exceeds_budget", budget,
            "raise_the_budget_or_disable_the_section",
        )
        section["effective_token_budget"] = budget
        return section, None

    emitted_tokens = estimate_tokens(rendered)
    if source_truncated:
        status = "truncated"
        reason_code = "source_byte_limit"
    elif rendered.endswith(session_hook.TRUNCATION_MARKER):
        status = "truncated"
        reason_code = "token_budget_limit"
    else:
        status = "ready"
        reason_code = "section_ready"
    section = _base_section(
        status,
        reason_code,
        budget,
        "reduce_the_note_or_review_the_budget" if status == "truncated" else "none",
    )
    section.update(
        effective_token_budget=budget,
        independently_would_emit=True,
        independently_estimated_tokens=emitted_tokens,
        would_emit=True,
        estimated_emitted_tokens=emitted_tokens,
    )
    return section, rendered


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


def diagnose(vault: Path) -> tuple[dict, int]:
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
    rendered: dict[str, str | None] = {}
    for name, path_key, budget_key, header in SECTION_SPECS:
        config = configs[name]
        if config[path_key] is None:
            budget = config[budget_key]
            sections[name] = _base_section(
                "unsafe_path", "note_path_unsafe", budget,
                "choose_a_vault_local_allowed_note",
            )
            rendered[name] = None
            continue
        sections[name], rendered[name] = _diagnose_section(
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
        joined = ""
        status = "invalid_config"
        reason_code = "section_fatal"
        next_action = "repair_fatal_section_diagnostics"
        exit_code = 2
    else:
        emitted = [rendered[name] for name, *_ in SECTION_SPECS if rendered[name]]
        joined = "\n\n".join(emitted)
        if any(section["status"] in WARNING_STATES for section in sections.values()):
            status = "degraded"
            reason_code = "section_warning"
            next_action = "review_section_diagnostics"
            exit_code = 1
        else:
            status = "ready"
            reason_code = (
                "context_ready" if joined else "context_intentionally_disabled"
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
            emitted_tokens=estimate_tokens(joined) if joined else 0,
            would_emit=bool(joined),
        ),
        "sections": sections,
    }
    return report, exit_code


def _format_text(report: dict) -> str:
    hook = report["hook"]
    lines = [
        f"agentic-vault doctor: {report['status']} ({report['reason_code']})",
        f"next_action: {report['next_action']}",
        f"hook_would_emit_context: {str(hook['would_emit_context']).lower()}",
        f"configured_token_budget: {hook['configured_token_budget']}",
        f"effective_token_budget: {hook['effective_token_budget']}",
        f"estimated_emitted_tokens: {hook['estimated_emitted_tokens']}",
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
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path, help="Vault directory to diagnose.")
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
