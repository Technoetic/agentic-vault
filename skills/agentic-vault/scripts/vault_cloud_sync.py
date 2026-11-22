#!/usr/bin/env python3
"""Read-only cloud-sync path heuristics and first-run capability summary.

Uses only lexical path segments, explicit environment variables and file
existence checks; performs no network calls, no content reads of secret files,
and never echoes raw paths, usernames or credential values in diagnostics.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import shutil
import stat

from vault_paths import _is_link_or_reparse, resolve_note_path


MAX_CONFIG_BYTES = 256 * 1024
CLOUD_SYNC_TRUST_BOUNDARY = "lexical_path_and_env_heuristics_only"
SUMMARY_TRUST_BOUNDARY = "local_metadata_heuristics_only_host_runtime_unverified"
_ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
_GOOGLE_DRIVE_SEGMENTS = frozenset({
    "google drive",
    "my drive",
    "shared drives",
    "내 드라이브",
    "공유 드라이브",
})
_ICLOUD_SEGMENTS = frozenset({
    "icloud drive",
    "com~apple~clouddocs",
})


def _normalized_abs(raw: str | Path) -> Path:
    expanded = os.path.expanduser(str(raw))
    return Path(os.path.normcase(os.path.abspath(expanded)))


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def detect_cloud_sync_path(
    path: Path | str, *, env: Mapping[str, str] | None = None
) -> dict:
    """Heuristically classify whether `path` sits inside a known cloud-synced folder.

    Returns fixed labels only (`detected`, `provider`, `signal`) and never echoes
    raw paths or environment values.
    """
    source_env = os.environ if env is None else env
    try:
        normalized = _normalized_abs(path)
    except (OSError, TypeError, ValueError):
        return {"detected": False, "provider": None, "signal": "none"}

    for var in _ONEDRIVE_ENV_VARS:
        root_value = source_env.get(var)
        if not isinstance(root_value, str) or not root_value.strip():
            continue
        try:
            env_root = _normalized_abs(root_value)
        except (OSError, TypeError, ValueError):
            continue
        if len(env_root.parts) > 1 and _is_within(normalized, env_root):
            return {"detected": True, "provider": "onedrive", "signal": "env_root"}

    parts = [part.casefold() for part in normalized.parts]
    for index, segment in enumerate(parts):
        if segment == "onedrive" or segment.startswith("onedrive - "):
            return {"detected": True, "provider": "onedrive", "signal": "path_segment"}
        if segment in _GOOGLE_DRIVE_SEGMENTS or segment.startswith("googledrive"):
            return {"detected": True, "provider": "google_drive", "signal": "path_segment"}
        if segment == "dropbox" or segment.startswith("dropbox ("):
            return {"detected": True, "provider": "dropbox", "signal": "path_segment"}
        if segment in _ICLOUD_SEGMENTS:
            return {"detected": True, "provider": "icloud", "signal": "path_segment"}
        if (
            segment == "mobile documents"
            and index > 0
            and parts[index - 1] == "library"
        ):
            return {"detected": True, "provider": "icloud", "signal": "path_segment"}

    return {"detected": False, "provider": None, "signal": "none"}


def _read_backup_target(vault: Path) -> str | None:
    """Safely read only the `backup_target` string from vault config, if present."""
    try:
        config_path = resolve_note_path(vault, "00-meta/vault-config.json", (".git",))
        info = config_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            return None
        raw = config_path.read_bytes()
        if len(raw) > MAX_CONFIG_BYTES:
            return None
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    target = parsed.get("backup_target")
    return target.strip() if isinstance(target, str) and target.strip() else None


def _local_artifact_signals(vault: Path) -> list[str]:
    """Report fixed labels for local-only files present in the vault; never read them."""
    signals: list[str] = []
    checks = (
        ((".claude", "settings.local.json"), "claude_settings_local"),
        ((".env",), "dotenv_file"),
    )
    try:
        if _is_link_or_reparse(vault):
            return signals
    except OSError:
        return signals
    for parts, label in checks:
        current = vault
        unsafe = False
        for part in parts:
            current = current / part
            try:
                if _is_link_or_reparse(current):
                    unsafe = True
                    break
            except OSError:
                unsafe = True
                break
        if unsafe:
            continue
        try:
            info = current.lstat()
            if stat.S_ISREG(info.st_mode):
                signals.append(label)
        except OSError:
            continue
    return signals


def diagnose_cloud_sync(
    vault: Path, *, env: Mapping[str, str] | None = None
) -> dict:
    """Read-only destination sync check for a vault and its configured backup_target."""
    vault = Path(vault)
    vault_sync = detect_cloud_sync_path(vault, env=env)
    backup_target = _read_backup_target(vault)
    if backup_target is None:
        backup_sync = {"configured": False, "detected": False, "provider": None, "signal": "none"}
    else:
        target_path = Path(backup_target)
        resolved_target = target_path if target_path.is_absolute() else vault / target_path
        backup_sync = {"configured": True, **detect_cloud_sync_path(resolved_target, env=env)}
    local_signals = _local_artifact_signals(vault)
    synced = bool(vault_sync["detected"] or backup_sync["detected"])
    return {
        "schema_version": 1,
        "status": "cloud_synced" if synced else "local",
        "vault_sync": vault_sync,
        "backup_target_sync": backup_sync,
        "local_artifact_signals": local_signals,
        "next_action": (
            "review_sensitive_notes_and_local_settings_before_cloud_sync"
            if synced
            else "none"
        ),
        "trust_boundary": CLOUD_SYNC_TRUST_BOUNDARY,
    }


def summarize_capabilities(
    vault: Path,
    doctor_report: dict,
    *,
    env: Mapping[str, str] | None = None,
) -> dict:
    """Build a concise first-run capability digest (ready now vs optional setup)."""
    source_env = os.environ if env is None else env
    vault = Path(vault)
    status = str(doctor_report.get("status", "unknown"))
    sync_info = detect_cloud_sync_path(vault, env=source_env)

    ready_now: list[str] = []
    optional_setup: list[str] = []

    if status == "not_vault":
        ready_now.append("direct_jev_questions_if_authorized")
        ready_now.append("vault_init_scaffolding")
        optional_setup.append("initialize_vault_with_vault_init")
        next_step = "run_vault_init_to_create_a_vault_in_this_folder"
    elif status in ("invalid_vault", "invalid_config", "unavailable"):
        ready_now.append("read_only_diagnostics")
        optional_setup.append("repair_vault_path_or_config_before_session_hooks")
        next_step = str(doctor_report.get("next_action") or "repair_vault_configuration")
    else:
        ready_now.append("session_handoff_and_hot_context")
        ready_now.append("local_markdown_recall_and_lint")
        if status == "degraded":
            optional_setup.append("review_degraded_handoff_or_hot_sections")
            next_step = "review_section_diagnostics_or_run_vault_lint"
        else:
            next_step = "start_capturing_notes_or_run_vault_lint"
        if not (vault / ".git").exists():
            optional_setup.append("enable_local_git_and_integrity_hooks")
        if not (vault / "00-meta" / ".vault-install-state.json").is_file():
            optional_setup.append("record_install_state_for_drift_tracking")
        if _read_backup_target(vault) is None:
            optional_setup.append("configure_local_backup_target")

    if bool(source_env.get("TYPESAFE_API_KEY")):
        ready_now.append("jev_direct_judgment_credential_present")
    else:
        optional_setup.append("set_typesafe_api_key_for_optional_jev_judgments")

    if shutil.which("aside") is not None:
        ready_now.append("aside_cli_detected_on_path")
    else:
        optional_setup.append("install_aside_cli_if_browser_automation_needed")

    if sync_info["detected"]:
        optional_setup.append("review_cloud_sync_boundary_for_sensitive_notes")

    return {
        "schema_version": 1,
        "vault_status": status,
        "cloud_sync_detected": bool(sync_info["detected"]),
        "cloud_sync_provider": sync_info["provider"],
        "ready_now": ready_now,
        "optional_setup": optional_setup,
        "next_step": next_step,
        "trust_boundary": SUMMARY_TRUST_BOUNDARY,
    }


def text_lines(report: dict) -> list[str]:
    vault_sync = report.get("vault_sync", {})
    backup_sync = report.get("backup_target_sync", {})
    signals = report.get("local_artifact_signals", [])
    return [
        f"cloud_sync: {report.get('status', 'unavailable')} "
        f"(vault_provider={vault_sync.get('provider') or 'none'}, "
        f"backup_provider={backup_sync.get('provider') or 'none'})",
        f"cloud_sync_local_artifacts: {', '.join(signals) if signals else 'none'}",
        f"cloud_sync_next_action: {report.get('next_action', 'none')}",
        f"cloud_sync_trust_boundary: {report.get('trust_boundary', CLOUD_SYNC_TRUST_BOUNDARY)}",
    ]


def summary_text_lines(summary: dict) -> list[str]:
    ready = summary.get("ready_now", [])
    optional = summary.get("optional_setup", [])
    return [
        f"summary_vault_status: {summary.get('vault_status', 'unknown')} "
        f"(cloud_sync={str(bool(summary.get('cloud_sync_detected'))).lower()})",
        f"summary_ready_now: {', '.join(ready) if ready else 'none'}",
        f"summary_optional_setup: {', '.join(optional) if optional else 'none'}",
        f"summary_next_step: {summary.get('next_step', 'none')}",
        f"summary_trust_boundary: {summary.get('trust_boundary', SUMMARY_TRUST_BOUNDARY)}",
    ]
