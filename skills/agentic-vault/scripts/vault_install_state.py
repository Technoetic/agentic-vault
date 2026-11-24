#!/usr/bin/env python3
"""Deterministic install-state manifest, drift status, and ownership-guarded uninstall.

Inspired by ECC's install-state and ownership-guard lifecycle contract
(affaan-m/ECC, MIT), written in Python standard library only for
agentic-vault vaults:

- Distinguishes plugin-managed stamped artifacts (``managed``) from
  user-owned or customized files (``user_owned`` / ``modified_by_user``).
- ``record`` writes ``00-meta/.vault-install-state.json`` atomically with
  per-file SHA-256 digests and ownership classification.
- ``status`` performs a read-only drift comparison against the vault on disk.
- ``uninstall`` is dry-run by default and, even with ``--apply``, removes ONLY
  files that satisfy ALL of: (1) canonical managed surface allowlist (never
  user-owned paths or arbitrary notes even if the manifest is tampered with),
  (2) matching engine stamp on the file's current bytes, (3) exact recorded
  SHA-256 digest match, and (4) regular non-symlink, non-hardlink file inside
  the vault. User notes, ``CLAUDE.md``, ``00-meta/vault-config.json``,
  ``00-meta/lessons.md``, and any user-edited managed file are always preserved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

try:
    from vault_evidence import fs_path as _fs_path
except ImportError:  # pragma: no cover - standalone fallback
    def _fs_path(path: Path) -> str:
        return str(path)


SCHEMA = "agentic-vault/install-state/v1"
STATE_REL = "00-meta/.vault-install-state.json"
MAX_STATE_BYTES = 1024 * 1024
MAX_SURFACE_BYTES = 4 * 1024 * 1024
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# Canonical engine-managed surfaces and their stamp markers (aligned with
# commands/vault-upgrade.md). Files at these paths without a matching stamp
# are classified as user_owned and never deleted by uninstall.
MANAGED_SURFACES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("00-meta/scripts/vault_healthcheck.py", re.compile(r"agentic-vault:healthcheck engine=([^\s]+)")),
    ("00-meta/scripts/git-hooks/pre-commit", re.compile(r"agentic-vault:hook engine=([^\s]+)")),
    ("00-meta/scripts/git-hooks/pre-push", re.compile(r"agentic-vault:hook engine=([^\s]+)")),
    ("00-meta/scripts/aside-up.ps1", re.compile(r"agentic-vault:adapter aside-up engine=([^\s]+)")),
    ("00-meta/scripts/deny-guard.py", re.compile(r"agentic-vault:adapter deny-guard engine=([^\s]+)")),
    ("00-meta/scripts/ps-parse.ps1", re.compile(r"agentic-vault:adapter ps-parse engine=([^\s]+)")),
    ("00-meta/scripts/lesson-cues.py", re.compile(r"agentic-vault:adapter lesson-cues engine=([^\s]+)")),
    ("AGENTS.md", re.compile(r"agentic-vault:generated")),
)
_MANAGED_SURFACE_MAP: dict[str, re.Pattern[str]] = dict(MANAGED_SURFACES)
# Older sealed manifests can list this vault-local command. Ignore that entry after validating the seal.
RETIRED_COMMAND_REL = ".claude/commands/aside-repl.md"
RULE_GLOB_DIR = ".claude/rules"
RULE_NAME_RE = re.compile(r"^vault-[a-z0-9-]+\.md$")
RULE_STAMP_RE = re.compile(r"agentic-vault:rule engine=([^\s]+)")

# Always user-owned: tracked for visibility, never eligible for removal.
USER_OWNED_SURFACES: tuple[str, ...] = (
    "CLAUDE.md",
    "00-meta/vault-config.json",
    "00-meta/lessons.md",
)
_USER_OWNED_SET: frozenset[str] = frozenset(USER_OWNED_SURFACES)
PRUNABLE_EMPTY_DIRS: tuple[str, ...] = (
    "00-meta/scripts/git-hooks",
    "00-meta/scripts",
    ".claude/rules",
)
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class InstallStateError(RuntimeError):
    """Raised when a vault path or install-state manifest fails safety checks."""


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_ATTR)


def _managed_stamp_pattern(rel: str) -> re.Pattern[str] | None:
    """Return the required engine stamp regex iff rel is a canonical managed surface."""
    norm = rel.replace("\\", "/").strip("/")
    if not norm or norm in _USER_OWNED_SET:
        return None
    if norm in _MANAGED_SURFACE_MAP:
        return _MANAGED_SURFACE_MAP[norm]
    if norm.startswith(f"{RULE_GLOB_DIR}/"):
        tail = norm[len(RULE_GLOB_DIR) + 1:]
        if "/" not in tail and RULE_NAME_RE.match(tail):
            return RULE_STAMP_RE
    return None


def _check_safe_vault_dir(vault: Path, rel_dir: str) -> tuple[Path | None, str]:
    """Verify every directory component from vault to rel_dir is non-symlink/non-reparse."""
    norm = rel_dir.replace("\\", "/").strip("/")
    parts = [part for part in norm.split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return None, "invalid_relative_path"
    root = vault.resolve()
    cur = root
    for part in parts:
        cur = cur / part
        try:
            st = os.lstat(_fs_path(cur))
        except FileNotFoundError:
            return cur, "missing"
        except OSError:
            return None, "unreadable"
        if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            return None, "unsafe_path"
    try:
        cur.resolve().relative_to(root)
    except (OSError, ValueError):
        return None, "unsafe_path"
    return cur, "dir"


def _check_safe_vault_path(vault: Path, rel: str) -> tuple[Path | None, str]:
    """Verify every component from vault to rel is non-symlink/non-reparse and stays inside vault."""
    norm = rel.replace("\\", "/").strip("/")
    parts = [part for part in norm.split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return None, "invalid_relative_path"
    root = vault.resolve()
    cur = root
    for part in parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(_fs_path(cur))
        except FileNotFoundError:
            return root / norm, "missing"
        except OSError:
            return None, "unreadable"
        if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            return None, "unsafe_path"
    target = cur / parts[-1]
    try:
        st = os.lstat(_fs_path(target))
    except FileNotFoundError:
        return target, "missing"
    except OSError:
        return None, "unreadable"
    if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISREG(st.st_mode):
        return None, "unsafe_path"
    if st.st_nlink != 1:
        return None, "unsafe_path"
    try:
        target.resolve().relative_to(root)
    except (OSError, ValueError):
        return None, "unsafe_path"
    return target, "regular"


def _read_regular_bytes(vault: Path, rel: str, limit: int = MAX_SURFACE_BYTES) -> tuple[bytes | None, str]:
    target, state = _check_safe_vault_path(vault, rel)
    if state != "regular" or target is None:
        return None, state
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(_fs_path(target), flags)
    except OSError:
        return None, "unreadable"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            os.close(fd)
            return None, "unsafe_path"
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None, "unreadable"
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None, "unreadable"
    if len(data) > limit:
        return None, "too_large"
    return data, "ok"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_manifest_sha256(doc: dict) -> str:
    """Compute canonical SHA-256 of an install-state manifest excluding ``manifest_sha256``."""
    body = {key: value for key, value in doc.items() if key != "manifest_sha256"}
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate_json_key:{key}")
        out[key] = value
    return out


def _reject_nonfinite_json(token: str) -> object:
    raise ValueError(f"nonfinite_json_number:{token}")


def _detect_stamp(data: bytes, pattern: re.Pattern[str]) -> tuple[bool, str | None]:
    head = data[:4096].decode("utf-8-sig", errors="replace")
    match = pattern.search(head)
    if match is None:
        return False, None
    return True, (match.group(1) if match.lastindex else "generated")


def _discover_surfaces(vault: Path) -> list[dict]:
    """Scan canonical managed and user-owned vault surfaces without modifying anything."""
    entries: list[dict] = []
    seen: set[str] = set()

    for rel, stamp_re in MANAGED_SURFACES:
        seen.add(rel)
        data, state = _read_regular_bytes(vault, rel)
        if state == "missing":
            continue
        if data is None:
            entries.append({"path": rel, "ownership": "unknown", "state": state})
            continue
        stamped, stamp_ver = _detect_stamp(data, stamp_re)
        entries.append({
            "path": rel,
            "ownership": "managed" if stamped else "user_owned",
            "stamp": stamp_ver,
            "sha256": _sha256_bytes(data),
            "bytes": len(data),
        })

    # Discover stamped .claude/rules/vault-*.md files safely (verify every ancestor component).
    rules_dir, dir_state = _check_safe_vault_dir(vault, RULE_GLOB_DIR)
    if dir_state == "dir" and rules_dir is not None:
        for child in sorted(rules_dir.iterdir(), key=lambda p: p.name):
            if not RULE_NAME_RE.match(child.name):
                continue
            rel = f"{RULE_GLOB_DIR}/{child.name}"
            if rel in seen:
                continue
            seen.add(rel)
            data, state = _read_regular_bytes(vault, rel)
            if data is None:
                entries.append({"path": rel, "ownership": "unknown", "state": state})
                continue
            stamped, stamp_ver = _detect_stamp(data, RULE_STAMP_RE)
            entries.append({
                "path": rel,
                "ownership": "managed" if stamped else "user_owned",
                "stamp": stamp_ver,
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            })

    for rel in USER_OWNED_SURFACES:
        if rel in seen:
            continue
        seen.add(rel)
        data, state = _read_regular_bytes(vault, rel)
        if state == "missing":
            continue
        if data is None:
            entries.append({"path": rel, "ownership": "user_owned", "state": state})
            continue
        entries.append({
            "path": rel,
            "ownership": "user_owned",
            "stamp": None,
            "sha256": _sha256_bytes(data),
            "bytes": len(data),
        })

    entries.sort(key=lambda item: item["path"])
    return entries


def _write_json_atomic(vault: Path, rel: str, payload: dict) -> None:
    target, state = _check_safe_vault_path(vault, rel)
    if target is None or state in ("unsafe_path", "invalid_relative_path", "unreadable"):
        raise InstallStateError(f"unsafe_state_path:{rel}")
    parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
    if parent_rel:
        target.parent.mkdir(parents=True, exist_ok=True)
        _, pstate = _check_safe_vault_dir(vault, parent_rel)
        if pstate != "dir":
            raise InstallStateError(f"unsafe_parent_dir:{rel}")
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".vault-install-state-", suffix=".tmp", dir=_fs_path(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, _fs_path(target))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def record_install_state(
    vault: Path,
    *,
    host: str = "all",
    engine_version: str = "0.57.0",
) -> dict:
    """Record current managed and user-owned surfaces into 00-meta/.vault-install-state.json."""
    resolved = Path(vault).resolve()
    _, meta_state = _check_safe_vault_dir(resolved, "00-meta")
    if meta_state != "dir":
        raise InstallStateError("not_a_vault")
    entries = _discover_surfaces(resolved)
    manifest = {
        "schema": SCHEMA,
        "recorded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": host,
        "engine_version": engine_version,
        "entries": entries,
    }
    manifest["manifest_sha256"] = _canonical_manifest_sha256(manifest)
    _write_json_atomic(resolved, STATE_REL, manifest)
    managed_count = sum(1 for item in entries if item.get("ownership") == "managed")
    user_count = sum(1 for item in entries if item.get("ownership") == "user_owned")
    return {
        "schema": SCHEMA,
        "action": "record",
        "state_path": STATE_REL,
        "engine_version": engine_version,
        "host": host,
        "manifest_sha256": manifest["manifest_sha256"],
        "managed_count": managed_count,
        "user_owned_count": user_count,
        "entries": entries,
    }


def _load_manifest(vault: Path) -> tuple[dict | None, str, str]:
    data, state = _read_regular_bytes(vault, STATE_REL, limit=MAX_STATE_BYTES)
    if state == "missing":
        return None, "not_recorded", "not_recorded"
    if data is None:
        return None, state, "invalid"
    try:
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeError, ValueError, RecursionError):
        return None, "invalid_json", "invalid"
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("entries"), list):
        return None, "invalid_schema", "invalid"
    seen_paths: set[str] = set()
    for entry in doc["entries"]:
        if not isinstance(entry, dict):
            return None, "invalid_schema", "invalid"
        rel = entry.get("path")
        if type(rel) is not str or not rel.strip() or rel in seen_paths:
            return None, "invalid_schema", "invalid"
        seen_paths.add(rel)
    if "manifest_sha256" in doc:
        claimed = doc["manifest_sha256"]
        if type(claimed) is not str or not HASH_RE.fullmatch(claimed):
            return None, "manifest_digest_mismatch", "mismatch"
        try:
            actual = _canonical_manifest_sha256(doc)
        except (TypeError, ValueError):
            return None, "manifest_digest_mismatch", "mismatch"
        if claimed != actual:
            return None, "manifest_digest_mismatch", "mismatch"
        return doc, "ok", "verified"
    return doc, "ok", "unsealed_legacy"


def diagnose_install_state(vault: Path) -> dict:
    """Read-only comparison between recorded install-state and current vault files."""
    resolved = Path(vault).resolve()
    manifest, state, integrity = _load_manifest(resolved)
    discovered = _discover_surfaces(resolved)
    discovered_by_path = {item["path"]: item for item in discovered}

    if manifest is None:
        untracked_managed = [
            item["path"] for item in discovered if item.get("ownership") == "managed"
        ]
        near_misses: list[dict[str, str]] = []
        if state != "not_recorded":
            near_misses.append({"path": STATE_REL, "mismatch": state})
        for path in untracked_managed:
            near_misses.append({"path": path, "mismatch": "untracked_managed"})
        return {
            "schema": SCHEMA,
            "status": "not_recorded" if state == "not_recorded" else "invalid_state",
            "reason_code": state,
            "manifest_integrity": integrity,
            "state_path": STATE_REL,
            "recorded_engine_version": None,
            "managed_intact": 0,
            "managed_modified": 0,
            "managed_missing": 0,
            "user_owned_count": sum(1 for item in discovered if item.get("ownership") == "user_owned"),
            "untracked_managed": untracked_managed,
            "near_misses": near_misses,
            "items": [],
        }

    recorded_paths: set[str] = set()
    items: list[dict] = []
    near_misses = []
    managed_intact = 0
    managed_modified = 0
    managed_missing = 0
    user_owned_count = 0

    for rec in manifest["entries"]:
        rel = rec["path"]
        if rel == RETIRED_COMMAND_REL:
            continue
        recorded_paths.add(rel)
        ownership = rec.get("ownership", "user_owned")
        recorded_sha = rec.get("sha256")
        data, read_state = _read_regular_bytes(resolved, rel)

        if ownership == "user_owned":
            user_owned_count += 1
            items.append({
                "path": rel,
                "ownership": "user_owned",
                "status": "present" if data is not None else read_state,
            })
            continue

        if ownership != "managed":
            items.append({"path": rel, "ownership": str(ownership), "status": read_state})
            continue

        stamp_re = _managed_stamp_pattern(rel)
        if stamp_re is None:
            items.append({"path": rel, "ownership": "managed", "status": "not_managed_surface"})
            near_misses.append({"path": rel, "mismatch": "not_managed_surface"})
            managed_modified += 1
            continue

        if read_state == "missing":
            managed_missing += 1
            items.append({"path": rel, "ownership": "managed", "status": "missing"})
            near_misses.append({"path": rel, "mismatch": "missing_file"})
        elif data is None:
            managed_modified += 1
            items.append({"path": rel, "ownership": "managed", "status": read_state})
            near_misses.append({"path": rel, "mismatch": read_state})
        else:
            stamped, _ = _detect_stamp(data, stamp_re)
            if stamped and recorded_sha and _sha256_bytes(data) == recorded_sha:
                managed_intact += 1
                items.append({"path": rel, "ownership": "managed", "status": "intact"})
            else:
                managed_modified += 1
                items.append({"path": rel, "ownership": "managed", "status": "modified_by_user"})
                near_misses.append({
                    "path": rel,
                    "mismatch": "missing_engine_stamp" if not stamped else "sha256_mismatch",
                })

    untracked_managed = sorted(
        path for path, info in discovered_by_path.items()
        if info.get("ownership") == "managed" and path not in recorded_paths
    )
    for path in untracked_managed:
        near_misses.append({"path": path, "mismatch": "untracked_managed"})
    if managed_modified > 0 or managed_missing > 0 or untracked_managed:
        overall = "drift_detected"
        reason = "managed_surface_drift"
    else:
        overall = "clean"
        reason = "all_recorded_managed_intact"

    return {
        "schema": SCHEMA,
        "status": overall,
        "reason_code": reason,
        "manifest_integrity": integrity,
        "state_path": STATE_REL,
        "recorded_engine_version": manifest.get("engine_version"),
        "managed_intact": managed_intact,
        "managed_modified": managed_modified,
        "managed_missing": managed_missing,
        "user_owned_count": user_owned_count,
        "untracked_managed": untracked_managed,
        "near_misses": near_misses,
        "items": items,
    }


def uninstall_managed(vault: Path, *, apply: bool = False) -> dict:
    """Plan (and with apply=True execute) removal of ONLY verified canonical managed files."""
    resolved = Path(vault).resolve()
    manifest, state, _integrity = _load_manifest(resolved)
    if manifest is None:
        return {
            "schema": SCHEMA,
            "action": "uninstall",
            "applied": False,
            "status": "refused",
            "reason_code": state,
            "removable": [],
            "removed": [],
            "preserved": [],
            "pruned_dirs": [],
            "state_removed": False,
        }

    removable: list[tuple[str, str, re.Pattern[str], dict]] = []
    preserved: list[dict[str, str]] = []
    remaining_entries: list[dict] = []

    for rec in manifest["entries"]:
        rel = rec["path"]
        if rel == RETIRED_COMMAND_REL:
            continue
        ownership = rec.get("ownership", "user_owned")
        recorded_sha = rec.get("sha256")

        if ownership != "managed":
            preserved.append({"path": rel, "reason": "user_owned"})
            remaining_entries.append(rec)
            continue

        # Never trust a tampered manifest to delete user-owned or arbitrary paths.
        stamp_re = _managed_stamp_pattern(rel)
        if stamp_re is None:
            preserved.append({"path": rel, "reason": "not_managed_surface"})
            remaining_entries.append(rec)
            continue

        data, read_state = _read_regular_bytes(resolved, rel)
        if read_state == "missing":
            preserved.append({"path": rel, "reason": "already_missing"})
            continue
        if data is None:
            preserved.append({"path": rel, "reason": read_state})
            remaining_entries.append(rec)
            continue
        stamped, _ = _detect_stamp(data, stamp_re)
        if not stamped:
            preserved.append({"path": rel, "reason": "missing_engine_stamp"})
            remaining_entries.append(rec)
            continue
        if not isinstance(recorded_sha, str) or _sha256_bytes(data) != recorded_sha:
            preserved.append({"path": rel, "reason": "modified_by_user"})
            remaining_entries.append(rec)
            continue
        removable.append((rel, recorded_sha, stamp_re, rec))

    removed: list[str] = []
    pruned_dirs: list[str] = []
    state_removed = False

    if apply:
        for rel, recorded_sha, stamp_re, rec in removable:
            # Re-verify regular file, stamp, and SHA-256 right before unlink.
            fresh_data, fresh_state = _read_regular_bytes(resolved, rel)
            if fresh_data is None or _sha256_bytes(fresh_data) != recorded_sha or not _detect_stamp(fresh_data, stamp_re)[0]:
                preserved.append({"path": rel, "reason": "changed_before_unlink"})
                remaining_entries.append(rec)
                continue
            target, check_state = _check_safe_vault_path(resolved, rel)
            if check_state != "regular" or target is None:
                preserved.append({"path": rel, "reason": "changed_before_unlink"})
                remaining_entries.append(rec)
                continue
            os.unlink(_fs_path(target))
            removed.append(rel)

        for rel_dir in PRUNABLE_EMPTY_DIRS:
            dir_path, dir_state = _check_safe_vault_dir(resolved, rel_dir)
            if dir_state != "dir" or dir_path is None:
                continue
            try:
                if not any(dir_path.iterdir()):
                    os.rmdir(_fs_path(dir_path))
                    pruned_dirs.append(rel_dir)
            except OSError:
                pass

        has_preserved_managed = any(
            item.get("ownership") == "managed" and _managed_stamp_pattern(str(item.get("path", ""))) is not None
            for item in remaining_entries
        )
        if not has_preserved_managed:
            st_target, st_state = _check_safe_vault_path(resolved, STATE_REL)
            if st_state == "regular" and st_target is not None:
                os.unlink(_fs_path(st_target))
                state_removed = True
        else:
            updated = dict(manifest)
            updated["entries"] = remaining_entries
            updated["manifest_sha256"] = _canonical_manifest_sha256(updated)
            _write_json_atomic(resolved, STATE_REL, updated)

    return {
        "schema": SCHEMA,
        "action": "uninstall",
        "applied": bool(apply),
        "status": "applied" if apply else "dry_run",
        "reason_code": "managed_removed" if apply else "dry_run_only",
        "removable": [rel for rel, _, _, _ in removable],
        "removed": removed,
        "preserved": preserved,
        "pruned_dirs": pruned_dirs,
        "state_removed": state_removed,
    }


def text_lines(report: dict) -> list[str]:
    lines = [
        f"install_state: {report['status']} ({report['reason_code']})",
        f"install_state_manifest_integrity: {report.get('manifest_integrity', 'unknown')}",
        f"install_state_engine_version: {report.get('recorded_engine_version')}",
        f"install_state_managed_intact: {report.get('managed_intact', 0)}",
        f"install_state_managed_modified: {report.get('managed_modified', 0)}",
        f"install_state_managed_missing: {report.get('managed_missing', 0)}",
        f"install_state_user_owned: {report.get('user_owned_count', 0)}",
        f"install_state_untracked_managed: {len(report.get('untracked_managed', ()))}"
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="Record current managed and user-owned vault surfaces.")
    p_rec.add_argument("--vault", required=True, type=Path)
    p_rec.add_argument("--host", choices=("claude", "codex", "antigravity", "all"), default="all")
    p_rec.add_argument("--engine-version", default="0.57.0")
    p_rec.add_argument("--format", choices=("text", "json"), default="json")

    p_stat = sub.add_parser("status", help="Inspect drift between recorded install-state and disk.")
    p_stat.add_argument("--vault", required=True, type=Path)
    p_stat.add_argument("--format", choices=("text", "json"), default="json")

    p_un = sub.add_parser("uninstall", help="Dry-run or apply ownership-guarded uninstall of managed files.")
    p_un.add_argument("--vault", required=True, type=Path)
    p_un.add_argument("--apply", action="store_true", help="Actually unlink unmodified managed files.")
    p_un.add_argument("--format", choices=("text", "json"), default="json")

    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            result = record_install_state(
                args.vault, host=args.host, engine_version=args.engine_version,
            )
            rc = 0
        elif args.command == "status":
            result = diagnose_install_state(args.vault)
            rc = 0 if result["status"] in ("clean", "not_recorded") else 1
        else:
            result = uninstall_managed(args.vault, apply=args.apply)
            rc = 0 if result["status"] in ("dry_run", "applied") else 2
    except InstallStateError as exc:
        err = {"schema": SCHEMA, "status": "error", "reason_code": str(exc)}
        print(json.dumps(err, ensure_ascii=False, sort_keys=True))
        return 2

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "status":
        print("\n".join(text_lines(result)))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
