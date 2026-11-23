#!/usr/bin/env python3
"""Read-only host/OS/adapter capability coverage matrix for vault_doctor.

Selectively adapted from the explicit coverage-matrix contract in
ai3inc/foldboost (commit ff7ad2208c, Apache-2.0), implemented from scratch in
Python standard library only for agentic-vault:

- Treats ``supported``, ``opt_in_available``, ``unsupported``, and
  ``not_measured`` as first-class machine-readable statuses so users never
  confuse an unconfigured opt-in feature, an OS/host-unsupported surface, or
  an unverified live host runtime with a broken vault.
- Strictly read-only, no-leak (never echoes raw host paths, usernames, or note
  contents), symlink/reparse safe, zero network or subprocess calls.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

try:
    from vault_evidence import fs_path as _fs_path
except ImportError:  # pragma: no cover - standalone fallback
    def _fs_path(path: Path) -> str:
        return str(path)

SCHEMA = "agentic-vault/capability-coverage/v1"
TRUST_BOUNDARY = "declared_surface_matrix_and_static_metadata_only"
HOSTS = ("claude", "codex", "antigravity")
PLATFORMS = ("windows", "linux", "darwin")
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _normalize_platform(platform_name: str | None = None) -> str:
    raw = (platform_name if platform_name is not None else sys.platform).lower()
    if raw in ("win32", "windows", "nt"):
        return "windows"
    if raw == "macos" or raw.startswith("darwin"):
        return "darwin"
    if raw.startswith("linux"):
        return "linux"
    return "unknown"


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_ATTR)


def _safe_regular_exists(vault: Path, rel: str) -> str:
    """Return 'present', 'missing', or 'unsafe_path' without reading file bytes."""
    norm = rel.replace("\\", "/").strip("/")
    parts = [part for part in norm.split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return "unsafe_path"
    try:
        root = vault.resolve()
    except OSError:
        return "unsafe_path"
    cur = root
    for part in parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(_fs_path(cur))
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unsafe_path"
        if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            return "unsafe_path"
    target = cur / parts[-1]
    try:
        st = os.lstat(_fs_path(target))
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe_path"
    if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISREG(st.st_mode):
        return "unsafe_path"
    try:
        target.resolve().relative_to(root)
    except (OSError, ValueError):
        return "unsafe_path"
    return "present"


def _row(
    *,
    surface: str,
    host: str,
    os_scope: str,
    status: str,
    vault_state: str,
    reason: str,
) -> dict[str, str]:
    return {
        "surface": surface,
        "host": host,
        "os_scope": os_scope,
        "status": status,
        "vault_state": vault_state,
        "reason": reason,
    }


def diagnose_coverage(
    vault: Path,
    *,
    host: str = "all",
    platform_name: str | None = None,
) -> dict:
    """Return a read-only host x OS x feature coverage matrix for ``vault``."""
    if host not in ("all", *HOSTS):
        raise ValueError(f"unsupported_host:{host}")
    plat = _normalize_platform(platform_name)
    is_windows = plat == "windows"
    is_posix = plat in ("linux", "darwin")
    resolved = Path(vault)

    has_config = _safe_regular_exists(resolved, "00-meta/vault-config.json") == "present"
    has_healthcheck = _safe_regular_exists(resolved, "00-meta/scripts/vault_healthcheck.py") == "present"
    has_precommit = _safe_regular_exists(resolved, "00-meta/scripts/git-hooks/pre-commit") == "present"
    has_install_state = _safe_regular_exists(resolved, "00-meta/.vault-install-state.json") == "present"
    has_deny_guard = _safe_regular_exists(resolved, "00-meta/scripts/deny-guard.py") == "present"
    has_lesson_cues = _safe_regular_exists(resolved, "00-meta/scripts/lesson-cues.py") == "present"
    has_aside_helper = _safe_regular_exists(resolved, "00-meta/scripts/aside-up.ps1") == "present"

    selected_hosts = HOSTS if host == "all" else (host,)
    rows: list[dict[str, str]] = []

    # Cross-host core surfaces.
    rows.append(_row(
        surface="healthcheck_lint",
        host="all",
        os_scope="all",
        status="supported",
        vault_state="active_on_disk" if (has_config and has_healthcheck) else ("configured" if has_config else "absent"),
        reason="stdlib_cli_supported_across_claude_codex_antigravity",
    ))
    rows.append(_row(
        surface="vault_recall",
        host="all",
        os_scope="all",
        status="supported",
        vault_state="active_on_disk" if has_config else "absent",
        reason="deterministic_local_lexical_recall_no_network_dependency",
    ))
    rows.append(_row(
        surface="git_commit_hooks",
        host="all",
        os_scope="all",
        status="opt_in_available",
        vault_state="configured" if has_precommit else "absent",
        reason="fail_closed_pre_commit_and_pre_push_installed_via_vault_init_or_upgrade",
    ))
    rows.append(_row(
        surface="install_state_manifest",
        host="all",
        os_scope="all",
        status="opt_in_available",
        vault_state="configured" if has_install_state else "absent",
        reason="sha256_ownership_manifest_recorded_on_explicit_vault_install_state_record",
    ))
    rows.append(_row(
        surface="backup_snapshot_and_restore",
        host="all",
        os_scope="all",
        status="supported",
        vault_state="active_on_disk" if has_config else "absent",
        reason="atomic_staging_sha256_manifest_and_git_bundle_supported_on_all_os",
    ))
    rows.append(_row(
        surface="backup_posix_dir_fsync",
        host="all",
        os_scope="posix",
        status="supported" if is_posix else ("unsupported" if is_windows else "not_measured"),
        vault_state=(
            ("active_on_disk" if has_config else "absent")
            if is_posix
            else ("not_applicable" if is_windows else "unverified")
        ),
        reason=(
            "posix_parent_directory_fsync_enabled"
            if is_posix
            else ("windows_noop_posix_directory_fsync_only" if is_windows else "unrecognized_platform_os_behavior_not_measured")
        ),
    ))
    rows.append(_row(
        surface="adapter_aside_helper",
        host="all",
        os_scope="windows",
        status="opt_in_available" if is_windows else ("unsupported" if is_posix else "not_measured"),
        vault_state=(
            ("configured" if has_aside_helper else "absent")
            if is_windows
            else ("not_applicable" if is_posix else "unverified")
        ),
        reason=(
            "windows_powershell_helper_for_aside_cli"
            if is_windows
            else ("windows_only_powershell_helper" if is_posix else "unrecognized_platform_os_behavior_not_measured")
        ),
    ))

    # Host-specific hook and adapter surfaces.
    for h in selected_hosts:
        if h == "claude":
            rows.append(_row(
                surface="session_start_hook",
                host=h,
                os_scope="all",
                status="supported",
                vault_state="active_on_disk" if has_config else "absent",
                reason="native_claude_code_session_start_hook_in_hooks_json",
            ))
            rows.append(_row(
                surface="session_checkpoint_hook",
                host=h,
                os_scope="all",
                status="supported",
                vault_state="active_on_disk" if has_config else "absent",
                reason="native_claude_code_stop_hook_in_hooks_json",
            ))
            rows.append(_row(
                surface="adapter_deny_guard",
                host=h,
                os_scope="all",
                status="opt_in_available",
                vault_state="configured" if has_deny_guard else "absent",
                reason="opt_in_claude_pretooluse_deny_zone_guard",
            ))
            rows.append(_row(
                surface="adapter_lesson_cues",
                host=h,
                os_scope="all",
                status="opt_in_available",
                vault_state="configured" if has_lesson_cues else "absent",
                reason="opt_in_claude_pretooluse_advisory_lesson_cues",
            ))
        elif h == "codex":
            rows.append(_row(
                surface="session_start_hook",
                host=h,
                os_scope="all",
                status="unsupported",
                vault_state="not_applicable",
                reason="codex_uses_explicit_shared_skill_invocation_no_session_start_hook",
            ))
            rows.append(_row(
                surface="session_checkpoint_hook",
                host=h,
                os_scope="all",
                status="unsupported",
                vault_state="not_applicable",
                reason="codex_uses_explicit_shared_skill_invocation_no_stop_hook",
            ))
            rows.append(_row(
                surface="adapter_deny_guard",
                host=h,
                os_scope="all",
                status="unsupported",
                vault_state="not_applicable",
                reason="claude_pretooluse_only_not_supported_in_codex",
            ))
            rows.append(_row(
                surface="adapter_lesson_cues",
                host=h,
                os_scope="all",
                status="unsupported",
                vault_state="not_applicable",
                reason="claude_pretooluse_only_not_supported_in_codex",
            ))
        else:  # antigravity
            rows.append(_row(
                surface="session_start_hook",
                host=h,
                os_scope="all",
                status="supported",
                vault_state="active_on_disk" if has_config else "absent",
                reason="generated_antigravity_hooks_json_invokes_session_start_with_host_antigravity",
            ))
            rows.append(_row(
                surface="session_checkpoint_hook",
                host=h,
                os_scope="all",
                status="supported",
                vault_state="active_on_disk" if has_config else "absent",
                reason="generated_antigravity_hooks_json_invokes_session_checkpoint_with_host_antigravity",
            ))
            rows.append(_row(
                surface="adapter_deny_guard",
                host=h,
                os_scope="all",
                status="unsupported",
                vault_state="not_applicable",
                reason="claude_pretooluse_only_not_supported_in_antigravity",
            ))
            rows.append(_row(
                surface="adapter_lesson_cues",
                host=h,
                os_scope="all",
                status="unsupported",
                vault_state="not_applicable",
                reason="claude_pretooluse_only_not_supported_in_antigravity",
            ))
            rows.append(_row(
                surface="antigravity_live_interactive_session",
                host=h,
                os_scope="all",
                status="not_measured",
                vault_state="unverified",
                reason="live_agy_interactive_session_not_executed_by_automated_ci",
            ))

    # Runtime execution boundary explicitly listed as not_measured.
    rows.append(_row(
        surface="host_runtime_hook_execution",
        host="all",
        os_scope="all",
        status="not_measured",
        vault_state="unverified",
        reason="static_diagnosis_never_executes_host_hooks_use_session_evidence_for_observed_runs",
    ))

    counts = {
        "supported": sum(1 for r in rows if r["status"] == "supported"),
        "opt_in_available": sum(1 for r in rows if r["status"] == "opt_in_available"),
        "unsupported": sum(1 for r in rows if r["status"] == "unsupported"),
        "not_measured": sum(1 for r in rows if r["status"] == "not_measured"),
    }
    return {
        "schema": SCHEMA,
        "status": "ok",
        "platform": plat,
        "host_filter": host,
        "vault_initialized": has_config,
        "counts": counts,
        "rows": rows,
        "trust_boundary": TRUST_BOUNDARY,
    }


def text_lines(report: dict) -> list[str]:
    counts = report.get("counts", {})
    lines = [
        (
            f"coverage_matrix: {report.get('status', 'unavailable')} "
            f"(platform={report.get('platform', 'unknown')}, host={report.get('host_filter', 'all')})"
        ),
        (
            "coverage_counts: "
            f"supported={counts.get('supported', 0)} "
            f"opt_in_available={counts.get('opt_in_available', 0)} "
            f"unsupported={counts.get('unsupported', 0)} "
            f"not_measured={counts.get('not_measured', 0)}"
        ),
        f"coverage_trust_boundary: {report.get('trust_boundary', TRUST_BOUNDARY)}",
    ]
    for row in report.get("rows", ()):
        lines.append(
            f"coverage_row: {row['host']}/{row['surface']}={row['status']} "
            f"(vault={row['vault_state']}; {row['reason']})"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path, help="Vault or folder to inspect.")
    parser.add_argument("--host", choices=("all", *HOSTS), default="all")
    parser.add_argument("--format", choices=("text", "json"), default="json")
    args = parser.parse_args(argv)
    report = diagnose_coverage(args.vault, host=args.host)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("\n".join(text_lines(report)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
