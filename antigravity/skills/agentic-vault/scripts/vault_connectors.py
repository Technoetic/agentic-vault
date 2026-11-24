#!/usr/bin/env python3
"""Read-only Jev connector diagnostics; offline by default.

An explicit Jev probe sends one fixed public synthetic request through the
existing bounded, fixed-origin adapter. The caller owns prior transmission
authorization. Credential availability is never successful authentication.
The diagnosis uses the process environment and never reads login stores, .env
files, notes or arbitrary provider URLs.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
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
JEV_MODULES = ("jev_client", "jev_ask")
_MODULE_HASHES = {}
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


def _load_jev():
    """Load only fixed, fully checked optional sources; no .pyc or PATH lookup.

    All dependency bytes pass the shared stable read and link/hardlink guards
    before any module executes. A missing/unsafe dependency is a connector
    result, never a startup failure. Existing imports from these exact checked
    paths may be reused; changed sources invalidate this helper's cached import.
    """
    installed = _installation(*(name + ".py" for name in JEV_MODULES))
    if installed["state"] != "available":
        return installed, {}
    sources = []
    try:
        for name in JEV_MODULES:
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


def _credential(value):
    if value is None or value == "":
        return _stage("missing", "environment_credential_missing")
    if (not isinstance(value, str) or len(value) > 4096
            or any(not 33 <= ord(char) <= 126 for char in value)):
        return _stage("invalid", "environment_credential_invalid")
    return _stage("available", "environment_credential_present_unverified")


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
    installed, modules = _load_jev()
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

    probe accepts None or 'jev'. The explicit Jev probe makes one synthetic
    call when safe prerequisites are present. The fixed-origin production
    transport forbids redirects/retries and bounds
    response bytes at 64 KiB and elapsed time at timeout (0 < timeout <= 10).
    env and transport are programmatic test injections, never CLI commands.
    A transport receives payload bytes, key and timeout and must honor bounds.
    Exit 0 means diagnosis completed, not that every connection works; 1 means
    an explicit probe failed/was unavailable, and 2 rejects arguments/config.
    """
    try:
        if (probe not in (None, "jev") or type(timeout) not in (int, float)
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
        # Read only the required credential, never enumerate or
        # persist environment values or consult .env/login/credential files.
        environment = {"TYPESAFE_API_KEY": source.get("TYPESAFE_API_KEY")}
        connectors = {"jev": _jev(environment)}
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
        parser.add_argument("--probe", choices=("jev",), default=None,
                            help="Explicit previously authorized synthetic Jev probe.")
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
