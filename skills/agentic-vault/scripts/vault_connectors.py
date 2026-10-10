#!/usr/bin/env python3
"""Read-only, stage-separated Jev and Jarvis diagnostics; offline by default.

An explicit Jev probe sends one fixed public synthetic request through the
existing bounded, fixed-origin adapter. The caller owns prior transmission
authorization. Credential availability is never successful authentication.
Jarvis diagnostics never launch its bridge or Claude and never read login
stores, .env files, notes or arbitrary provider URLs.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import re
import sys
from types import ModuleType

from vault_evidence import EvidenceError, regular, stable_read
from vault_paths import resolve_note_path
from vault_state import CONFIG_PATH, StateError, _Policy


SCRIPTS_DIR = Path(__file__).absolute().parent
MAX_TIMEOUT = 10.0
DEFAULT_TIMEOUT = 5.0
MODEL = "jev-1.13.0"
MAX_ADAPTER_BYTES = 512 * 1024
CONNECTOR_MODULES = {"jev": ("jev_client", "jev_ask"), "jarvis": ("jarvis_bridge",)}
_MODULE_HASHES = {}
ENV_NAMES = (
    "TYPESAFE_API_KEY", "JARVIS_TELEGRAM_TOKEN", "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_BASE_URL",
)
PROBE_INPUT = {
    "schema_version": 1,
    "context": {"kind": "user_input", "text": "This is a public connector health-check fixture."},
    "questions": [{
        "id": "connector_health", "type": "noul",
        "instructions": "Does the context identify this request as a connector health check?",
    }],
}
PROBE_ERRORS = frozenset({"authentication", "rate_limit", "timeout", "transport",
                          "malformed_response", "missing_api_key", "invalid_input",
                          "sensitive_input", "input_too_large"})


class ConnectorsError(Exception):
    """Only fixed diagnostic codes cross the command boundary."""


def _stage(state, reason_code, *, checked=None):
    result = {"state": state, "reason_code": reason_code}
    if checked is not None:
        result["checked"] = checked
    return result


def _terminal(status, reason_code, next_action):
    return {"schema_version": 1, "status": status, "reason_code": reason_code,
            "next_action": next_action, "mode": "offline", "probe_requested": None,
            "network_attempted": False, "connectors": {}}


def _installation(*names):
    """Inspect only metadata of fixed installed script names, never execute them."""
    try:
        for name in names:
            path = resolve_note_path(SCRIPTS_DIR, name)
            regular(path.lstat())
    except FileNotFoundError:
        return _stage("missing", "installed_script_missing")
    except (EvidenceError, ValueError, OSError, RuntimeError):
        return _stage("unsafe", "installed_script_unsafe")
    return _stage("available", "installed_scripts_present")


def _load_connector(connector):
    """Load only fixed, fully checked optional sources; no .pyc or PATH lookup.

    All dependency bytes pass the shared stable read and link/hardlink guards
    before any module executes. A missing/unsafe dependency is a connector
    result, never a startup failure. Existing imports from these exact checked
    paths may be reused; changed sources invalidate this helper's cached import.
    """
    names = CONNECTOR_MODULES[connector]
    installed = _installation(*(name + ".py" for name in names))
    if installed["state"] != "available":
        return installed, {}
    sources = []
    try:
        for name in names:
            path = resolve_note_path(SCRIPTS_DIR, name + ".py")
            info, _mark, raw = stable_read(
                lambda name=name: resolve_note_path(SCRIPTS_DIR, name + ".py"),
                MAX_ADAPTER_BYTES, contents=True)
            sources.append((name, path, info["sha256"], raw))
    except FileNotFoundError:
        return _stage("missing", "installed_script_missing"), {}
    except (EvidenceError, ValueError, OSError, RuntimeError):
        return _stage("unsafe", "installed_script_unsafe"), {}
    loaded = {}
    try:
        for name, path, source_hash, raw in sources:
            cached = sys.modules.get(name)
            if (not isinstance(cached, ModuleType)
                    or Path(getattr(cached, "__file__", "")).absolute() != path.absolute()
                    or _MODULE_HASHES.get((name, str(path)), source_hash) != source_hash
                    or name == "jev_ask" and getattr(cached, "jev_client", None) is not loaded["jev_client"]):
                cached = None
            if cached is None:
                # Compile the descriptor-checked snapshot rather than reopening
                # a path through an import loader (or following a cached .pyc).
                module = ModuleType(name)
                module.__file__ = str(path)
                module.__package__ = ""
                prior = sys.modules.get(name)
                sys.modules[name] = module
                try:
                    exec(compile(raw, str(path), "exec"), module.__dict__)
                except BaseException:
                    if prior is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = prior
                    raise
                cached = module
            _MODULE_HASHES[(name, str(path))] = source_hash
            loaded[name] = cached
    except Exception:
        return _stage("unknown", "installed_dependency_unavailable"), {}
    return installed, loaded


def _credential(value, *, telegram=False):
    if value is None or value == "":
        return _stage("missing", "environment_credential_missing")
    if (not isinstance(value, str) or len(value) > 4096
            or any(not 33 <= ord(char) <= 126 for char in value)):
        return _stage("invalid", "environment_credential_invalid")
    if telegram:
        # This is the bridge's exact token shape, independent of whether its
        # optional module is installed/importable. It proves only availability.
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", value, flags=re.ASCII) is None:
            return _stage("invalid", "environment_credential_invalid")
    return _stage("available", "environment_credential_present_unverified")


def _jarvis_config(config, bridge):
    """Use the bridge's pure parsers after the existing bounded policy read.

    load_jarvis_config reads the filesystem itself, so it is deliberately not
    called here. Its enabled configuration contract is mirrored with the same
    defaults and parsers; diagnostic output never includes configured values.
    """
    if "jarvis" not in config:
        return _stage("absent", "jarvis_configuration_absent"), None
    block = config["jarvis"]
    if not isinstance(block, dict) or type(block.get("enabled")) is not bool:
        return _stage("invalid", "jarvis_configuration_invalid"), None
    if not block["enabled"]:
        return _stage("disabled", "jarvis_disabled"), None
    if bridge is None:
        return _stage("unknown", "installed_dependency_unavailable"), None
    cfg = {**bridge.DEFAULTS, **block}
    try:
        ids = bridge.parse_telegram_user_ids(cfg["telegram_user_ids"])
        bridge.parse_briefing_slots(block)
        for name in ("butler_interval_hours", "qa_timeout_sec"):
            number = cfg[name]
            if (type(number) not in (int, float) or number <= 0
                    or not math.isfinite(number)):
                raise ValueError
        limit = cfg["qa_hourly_limit"]
        if type(limit) is not int or limit <= 0:
            raise ValueError
        command = cfg["claude_cmd"]
        if (not isinstance(command, str) or not command.strip()
                or bridge._ARGV_CONTROL_CHARACTERS.search(command)):
            raise ValueError
    except (bridge.JarvisConfigError, KeyError, TypeError, ValueError, OverflowError):
        return _stage("invalid", "jarvis_configuration_invalid"), None
    if not ids:
        return _stage("incomplete", "jarvis_allowlist_empty"), cfg
    return _stage("configured", "jarvis_enabled_with_allowlist"), cfg


def _claude_installation(command, bridge):
    # resolve_claude_executable only looks up and validates an executable. It
    # does not run it. Never return the path or command from untrusted config.
    if bridge is None:
        return _stage("unknown", "installed_dependency_unavailable")
    try:
        bridge.resolve_claude_executable(command)
    except bridge.ClaudeLaunchError as error:
        if error.reason == "not-found":
            return _stage("missing", "claude_cli_missing")
        return _stage("unsupported", "claude_cli_launcher_unsupported")
    except (OSError, ValueError, RuntimeError):
        return _stage("unknown", "claude_cli_metadata_unavailable")
    return _stage("available", "claude_cli_launchable_unverified")


def _jarvis(config, environment):
    bridge, modules = _load_connector("jarvis")
    adapter = modules.get("jarvis_bridge")
    configured, cfg = _jarvis_config(config, adapter)
    command = cfg["claude_cmd"] if cfg is not None else "claude"
    claude = _claude_installation(command, adapter)
    if bridge["state"] != "available":
        installed = bridge.copy()
    elif claude["state"] == "available":
        installed = _stage("available", "bridge_and_claude_cli_present")
    else:
        installed = _stage("incomplete", "jarvis_claude_cli_unavailable")
    telegram = _credential(environment.get("JARVIS_TELEGRAM_TOKEN"), telegram=True)
    api = _credential(environment.get("ANTHROPIC_API_KEY"))
    token = _credential(environment.get("ANTHROPIC_AUTH_TOKEN"))
    external_provider = any(environment.get(name) not in (None, "", "0", "false")
                            for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                                         "CLAUDE_CODE_USE_FOUNDRY"))
    if external_provider or environment.get("ANTHROPIC_BASE_URL"):
        claude_api = _stage("unknown", "bare_claude_provider_unsupported")
    elif "available" in (api["state"], token["state"]):
        claude_api = _stage("available", "bare_claude_environment_credential_unverified")
    elif "invalid" in (api["state"], token["state"]):
        claude_api = _stage("invalid", "environment_credential_invalid")
    else:
        claude_api = _stage("missing", "bare_claude_api_credentials_required")
    credential_states = {telegram["state"], claude_api["state"]}
    if "invalid" in credential_states:
        credentials = _stage("invalid", "jarvis_environment_credential_invalid")
    elif "unknown" in credential_states:
        credentials = _stage("unknown", "jarvis_credential_provider_unsupported")
    elif credential_states == {"available"}:
        credentials = _stage("available", "jarvis_environment_credentials_unverified")
    elif "available" in credential_states:
        credentials = _stage("partial", "jarvis_environment_credentials_partial")
    else:
        credentials = _stage("missing", "jarvis_environment_credentials_missing")
    if configured["state"] == "invalid":
        action = "repair_jarvis_configuration"
    elif configured["state"] == "incomplete":
        action = "configure_jarvis_telegram_allowlist"
    elif configured["state"] in ("disabled", "absent"):
        action = "none_if_jarvis_is_intentionally_disabled"
    elif installed["state"] != "available":
        action = "review_bridge_and_native_claude_installation"
    elif credentials["state"] != "available":
        action = "provide_telegram_and_bare_claude_api_credentials"
    else:
        action = "verify_jarvis_separately_under_existing_authorization"
    return {
        "provider": "telegram_and_bare_claude", "probe_supported": False,
        "installed": installed, "components": {"bridge": bridge, "claude_cli": claude},
        "configured": configured, "credential_available": credentials,
        "credentials": {"telegram_token": telegram, "bare_claude_api": claude_api},
        "authentication": _stage("not_checked", "offline_default", checked=False),
        "operation": _stage("not_checked", "offline_default", checked=False),
        "authentication_contract": "bare_mode_requires_api_auth_subscription_login_unchecked",
        "proof_limit": "installation_configuration_and_environment_presence_only",
        "next_action": action,
    }


def _jev(environment):
    installed = _installation("jev_ask.py", "jev_client.py")
    credential = _credential(environment.get("TYPESAFE_API_KEY"))
    if installed["state"] != "available":
        action = "restore_supported_jev_adapter_installation"
    elif credential["state"] == "missing":
        action = "provide_typesafe_api_key_in_process_environment"
    elif credential["state"] == "invalid":
        action = "repair_typesafe_api_key_in_process_environment"
    else:
        action = "explicitly_probe_jev_under_existing_authorization"
    return {
        "provider": "typesafe_systemone", "model": MODEL, "probe_supported": True,
        "installed": installed, "configured": _stage("configured", "fixed_builtin_provider_contract"),
        "credential_available": credential,
        "authentication": _stage("not_checked", "offline_default", checked=False),
        "operation": _stage("not_checked", "offline_default", checked=False),
        "proof_limit": "installation_and_environment_presence_only", "next_action": action,
    }


def _probe_jev(connector, environment, timeout, transport):
    if connector["installed"]["state"] != "available":
        reason = "installed_adapter_unavailable"
    elif connector["credential_available"]["state"] != "available":
        reason = "credential_unavailable"
    else:
        reason = None
    if reason:
        for name in ("authentication", "operation"):
            connector[name] = _stage("not_checked", reason, checked=False)
        return False, False
    installed, modules = _load_connector("jev")
    connector["installed"] = installed
    if installed["state"] != "available":
        for name in ("authentication", "operation"):
            connector[name] = _stage("not_checked", "installed_adapter_unavailable", checked=False)
        connector["next_action"] = "restore_supported_jev_adapter_installation"
        return False, False
    result = modules["jev_ask"].run(json.dumps(PROBE_INPUT, allow_nan=False).encode("utf-8"),
                         allow_network=True, api_key=environment["TYPESAFE_API_KEY"],
                         timeout=timeout, transport=transport)
    attempted = result.get("network_attempted") is True
    if result.get("status") in ("reviewed", "needs_review") and attempted:
        connector["authentication"] = _stage("passed", "validated_provider_response", checked=True)
        connector["operation"] = _stage("passed", "validated_synthetic_judgment", checked=True)
        connector["proof_limit"] = "public_synthetic_request_only"
        connector["next_action"] = "none_for_this_synthetic_probe"
        return True, True
    code = result.get("error_code")
    reason = code if code in PROBE_ERRORS else "transport"
    connector["authentication"] = _stage(
        "failed" if attempted and reason == "authentication" else "unknown" if attempted else "not_checked",
        reason, checked=attempted)
    connector["operation"] = _stage("failed" if attempted else "not_checked", reason, checked=attempted)
    connector["proof_limit"] = "public_synthetic_request_only" if attempted else "probe_not_attempted"
    connector["next_action"] = (
        "repair_typesafe_api_credentials" if reason == "authentication"
        else "review_probe_failure_before_any_new_authorized_attempt")
    return attempted, False


def diagnose(vault, *, probe=None, timeout=DEFAULT_TIMEOUT, env=None, transport=None):
    """Return (schema-v1 report, exit code); no writes or ambient network.

    probe accepts only None, 'jev' or 'jarvis'. 'jarvis' reports unsupported;
    'jev' makes one synthetic call when safe prerequisites are present. The
    fixed-origin production transport forbids redirects/retries and bounds
    response bytes at 64 KiB and elapsed time at timeout (0 < timeout <= 10).
    env and transport are programmatic test injections, never CLI commands.
    A transport receives payload bytes, key and timeout and must honor bounds.
    Exit 0 means diagnosis completed, not that every connection works; 1 means
    an explicit probe failed/was unavailable, and 2 rejects arguments/config.
    """
    try:
        if (probe not in (None, "jev", "jarvis") or type(timeout) not in (int, float)
                or not 0 < timeout <= MAX_TIMEOUT or not math.isfinite(timeout)
                or transport is not None and not callable(transport)
                or env is not None and not isinstance(env, Mapping)):
            raise ConnectorsError("invalid_arguments")
        vault = Path(vault).expanduser()
        path = resolve_note_path(vault, CONFIG_PATH, (".git",))
        try:
            path.lstat()
        except FileNotFoundError:
            return _terminal("not_vault", "config_not_found", "choose_an_existing_vault"), 2
        policy = _Policy(vault)
        source = os.environ if env is None else env
        # Read only the fixed relevant environment fields, never enumerate or
        # persist environment values or consult .env/login/credential files.
        environment = {name: source.get(name) for name in ENV_NAMES}
        connectors = {"jev": _jev(environment), "jarvis": _jarvis(policy.config, environment)}
        report = {"schema_version": 1, "status": "diagnosed", "mode": "probe" if probe else "offline",
                  "probe_requested": probe, "network_attempted": False,
                  "connectors": connectors, "next_action": "review_connector_stages"}
        if probe == "jev":
            # A changed policy cancels the call, rather than using an earlier
            # safe config read to justify a later action after changes.
            policy.fresh()
            connectors["jev"]["installed"] = _installation("jev_ask.py", "jev_client.py")
            attempted, passed = _probe_jev(connectors["jev"], environment, timeout, transport)
            report["network_attempted"] = attempted
            if not passed:
                report["status"] = "probe_failed"
                return report, 1
        elif probe == "jarvis":
            for name in ("authentication", "operation"):
                connectors["jarvis"][name] = _stage("not_checked", "probe_unsupported", checked=False)
            report["status"] = "probe_failed"
            return report, 1
        return report, 0
    except ConnectorsError:
        return _terminal("invalid_arguments", "invalid_arguments", "use_supported_cli_arguments"), 2
    except (StateError, EvidenceError, ValueError, OSError, RuntimeError, TypeError, OverflowError):
        return _terminal("invalid_config", "unsafe_or_invalid_configuration",
                         "restore_a_regular_bounded_valid_vault_configuration"), 2


def _format_text(report):
    lines = [f"agentic-vault connectors: {report['status']} (mode={report['mode']})",
             f"network_attempted: {str(report['network_attempted']).lower()}"]
    for name, connector in report["connectors"].items():
        for stage in ("installed", "configured", "credential_available", "authentication", "operation"):
            item = connector[stage]
            lines.append(f"{name}.{stage}: {item['state']} ({item['reason_code']})")
        lines.append(f"{name}.next_action: {connector['next_action']}")
    lines.append(f"next_action: {report['next_action']}")
    return "\n".join(lines)


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ConnectorsError("invalid_arguments")


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        for option in ("--vault", "--format", "--probe", "--timeout"):
            if sum(item == option or item.startswith(option + "=") for item in args) > 1:
                raise ConnectorsError("invalid_arguments")
        parser = _Parser(description=__doc__.splitlines()[0], allow_abbrev=False)
        parser.add_argument("--vault", required=True, type=Path)
        parser.add_argument("--format", choices=("text", "json"), default="text")
        parser.add_argument("--probe", choices=("jev", "jarvis"), default=None,
                            help="Explicit previously authorized synthetic probe; Jarvis is unsupported.")
        parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                            help="Probe deadline in seconds (0 < timeout <= 10; default 5).")
        options = parser.parse_args(args)
        report, code = diagnose(options.vault, probe=options.probe, timeout=options.timeout)
    except (ConnectorsError, ValueError, TypeError, OverflowError):
        # Even invalid provider strings and URL/key-bearing arguments are never
        # reflected by argparse. Errors are always a content-free JSON object.
        report = _terminal("invalid_arguments", "invalid_arguments", "use_supported_cli_arguments")
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 2
    if options.format == "json":
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
    else:
        print(_format_text(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
