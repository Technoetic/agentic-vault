#!/usr/bin/env python3
"""Read-only operator deny-rule monotonicity and hook script SHA-256 drift audit.

Inspired by arXiv:2611.32195v1 ("When Answering Is Administering": silent removal
of operator deny rules in coding-agent settings) and arXiv:2611.01976v1 ("Beyond
Prompt Injection: The Tool Update Gap": post-trust mutations to external hook or
tool scripts outside the LLM context window).

Contract:
- Pure Python 3.10+ standard library only; strictly offline.
- ``check`` and ``diagnose_policy_drift`` are strictly read-only and never modify
  settings, hook scripts, or the vault. Only explicit ``record`` writes the
  sealed baseline at ``00-meta/.vault-policy-baseline.json``.
- Content-free diagnostics: never echoes raw ``permissions.deny`` rule strings,
  hook command lines, environment values, or exception text. Records and reports
  only normalized rule SHA-256 digests, canonical hooks-config SHA-256 digests,
  counts, sanitized relative paths, and script SHA-256 digests.
- Fail-closed on symlinked/junctioned/hardlinked vault roots, settings files,
  baseline files, or hook scripts, on UNC/device paths, and on malformed,
  non-finite, or duplicate-key JSON.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from vault_hook_wiring import (
    GIT_HOOK_RELS,
    GIT_PRE_COMMIT_REL,
    HEALTHCHECK_REL,
    SETTINGS_RELS,
    _extract_handler_targets,
)

try:
    from vault_evidence import plain_path as _plain_path
except ImportError:  # pragma: no cover - standalone fallback
    def _plain_path(path: Path) -> Path:
        return Path(path)


SCHEMA = "agentic-vault/policy-drift/v1"
BASELINE_SCHEMA = "agentic-vault/policy-drift-baseline/v1"
BASELINE_REL = "00-meta/.vault-policy-baseline.json"
TRUST_BOUNDARY = "static_settings_and_hook_hashes_only_runtime_host_unverified"
AUTHORITY = "policy_drift_audit_only"
MAX_BASELINE_BYTES = 1024 * 1024
MAX_SETTINGS_BYTES = 4 * 1024 * 1024
MAX_HOOK_BYTES = 4 * 1024 * 1024
MAX_DENY_RULES_PER_FILE = 2048
MAX_HOOK_TARGETS = 256
MAX_REPORTED_DRIFT = 50
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RECORDED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
EXT_PREFIX_RE = re.compile(r"^<ext:[0-9a-f]{8}>/")
DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
WIN_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:\\")
BASELINE_TOP_KEYS = frozenset({
    "schema",
    "recorded_at",
    "authority",
    "trust_boundary",
    "settings",
    "hooks",
    "baseline_sha256",
})
PLUGIN_HOOK_RELS: tuple[str, ...] = (
    "hooks/session_start.py",
    "hooks/session_checkpoint.py",
    "hooks/antigravity_hook.py",
    "hooks/run_python_hook.sh",
)
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PolicyDriftError(RuntimeError):
    """Content-free operational error code for policy-drift baseline operations."""


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_ATTR)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_sha256(obj: object) -> str:
    encoded = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


EMPTY_HOOKS_CONFIG_SHA256 = _canonical_json_sha256({})


def _rule_digest(rule: str) -> str:
    return _sha256_bytes(rule.strip().encode("utf-8"))


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate_json_key:{key}")
        out[key] = value
    return out


def _reject_nonfinite_json(token: str) -> object:
    raise ValueError(f"nonfinite_json_number:{token}")


def _reject_nonfinite_float(token: str) -> float:
    val = float(token)
    if not math.isfinite(val):
        raise ValueError("nonfinite_json_float")
    return val


def _parse_strict_json(data: bytes) -> object:
    return json.loads(
        data.decode("utf-8-sig"),
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_nonfinite_json,
        parse_float=_reject_nonfinite_float,
    )


def _canonical_baseline_sha256(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "baseline_sha256"}
    return _canonical_json_sha256(body)


def _is_unsafe_raw_path(raw: str) -> bool:
    """Reject NUL bytes, UNC network shares, and Windows device namespaces before any OS I/O."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return True
    if raw.startswith("//"):
        return True
    if raw.startswith("\\\\"):
        # Permit only Windows extended local-drive paths (\\?\C:\...), never UNC or device paths.
        if raw.startswith("\\\\?\\") and WIN_DRIVE_ABS_RE.match(raw[4:]):
            return False
        return True
    return False


def _fs_str(path: Path | str, *, force_extended: bool = False) -> str:
    """Return an OS-safe filesystem string, adding Windows \\\\?\\ prefix when near MAX_PATH."""
    raw = str(path)
    if _is_unsafe_raw_path(raw):
        raise ValueError("unsafe_raw_path")
    plain = str(_plain_path(Path(raw)))
    if _is_unsafe_raw_path(plain):
        raise ValueError("unsafe_raw_path")
    if os.name != "nt":
        return plain
    ab = os.path.normpath(plain) if WIN_DRIVE_ABS_RE.match(plain) else os.path.normpath(os.path.abspath(plain))
    if _is_unsafe_raw_path(ab):
        raise ValueError("unsafe_raw_path")
    if (force_extended or len(ab) >= 240) and WIN_DRIVE_ABS_RE.match(ab):
        return "\\\\?\\" + ab
    return ab


def _safe_plain_abspath(path: Path | str) -> Path | None:
    """Normalize a local path without resolving symlinks or touching UNC/network shares."""
    raw = str(path)
    if _is_unsafe_raw_path(raw):
        return None
    try:
        plain = str(_plain_path(Path(raw)))
        if os.name == "nt" and WIN_DRIVE_ABS_RE.match(plain):
            ab = os.path.normpath(plain)
        else:
            ab = os.path.normpath(os.path.abspath(plain))
    except (OSError, ValueError):
        return None
    if _is_unsafe_raw_path(ab):
        return None
    return Path(ab)


def _lexical_relative_posix(target: Path, base: Path) -> str | None:
    """Return target's POSIX path relative to base purely lexically (no symlink resolution)."""
    t_ab = _safe_plain_abspath(target)
    b_ab = _safe_plain_abspath(base)
    if t_ab is None or b_ab is None:
        return None
    t_str = str(t_ab)
    b_str = str(b_ab)
    if os.name == "nt":
        t_cmp = os.path.normcase(t_str)
        b_cmp = os.path.normcase(b_str)
        if t_cmp == b_cmp:
            return None
        prefix = b_cmp if b_cmp.endswith("\\") else (b_cmp + "\\")
        if not t_cmp.startswith(prefix):
            return None
        rel_str = t_str[len(prefix):]
        rel_posix = rel_str.replace("\\", "/")
    else:
        try:
            rel_posix = t_ab.relative_to(b_ab).as_posix()
        except ValueError:
            return None
    parts = [p for p in rel_posix.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    return "/".join(parts)


def _check_safe_root(vault: Path) -> tuple[Path | None, str]:
    """Verify the root itself is a real non-symlink/non-reparse local directory."""
    ab = _safe_plain_abspath(vault)
    if ab is None:
        return None, "vault_unsafe"
    try:
        st = os.lstat(_fs_str(ab))
    except FileNotFoundError:
        return None, "vault_not_found"
    except (OSError, ValueError):
        return None, "vault_unreadable"
    if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
        return None, "vault_unsafe"
    try:
        real = Path(os.path.realpath(_fs_str(ab)))
        plain_real = _plain_path(real)
        if _is_unsafe_raw_path(str(plain_real)):
            return None, "vault_unsafe"
        st2 = os.lstat(_fs_str(plain_real))
    except (OSError, ValueError):
        return None, "vault_unreadable"
    if stat.S_ISLNK(st2.st_mode) or _is_reparse(st2) or not stat.S_ISDIR(st2.st_mode):
        return None, "vault_unsafe"
    return plain_real, "ok"


def _is_safe_rel_posix(rel: str) -> bool:
    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel:
        return False
    if rel.startswith("/") or rel.endswith("/") or DRIVE_PREFIX_RE.match(rel):
        return False
    parts = rel.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return False
    return True


def _is_valid_baseline_hook_path(hpath: str) -> bool:
    if not isinstance(hpath, str) or not hpath or len(hpath) > 512 or "\x00" in hpath or "\\" in hpath:
        return False
    if hpath.startswith("<plugin>/"):
        return _is_safe_rel_posix(hpath[len("<plugin>/"):])
    m = EXT_PREFIX_RE.match(hpath)
    if m is not None:
        return _is_safe_rel_posix(hpath[m.end():])
    if hpath.startswith("<"):
        return False
    return _is_safe_rel_posix(hpath)


def _check_safe_rel_path(root: Path, rel: str) -> tuple[Path | None, str]:
    """Verify every component from root to rel is non-symlink/non-reparse and stays inside root."""
    if not isinstance(rel, str) or "\x00" in rel or _is_unsafe_raw_path(rel):
        return None, "invalid_relative_path"
    norm = rel.replace("\\", "/").strip("/")
    if not _is_safe_rel_posix(norm):
        return None, "invalid_relative_path"
    parts = norm.split("/")
    cur = _plain_path(root)
    for part in parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(_fs_str(cur))
        except FileNotFoundError:
            return _plain_path(root) / norm, "missing"
        except (OSError, ValueError):
            return None, "unreadable"
        if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            return None, "unsafe_path"
    target = cur / parts[-1]
    try:
        st = os.lstat(_fs_str(target))
    except FileNotFoundError:
        return target, "missing"
    except (OSError, ValueError):
        return None, "unreadable"
    if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISREG(st.st_mode):
        return None, "unsafe_path"
    if st.st_nlink != 1:
        return None, "unsafe_path"
    if _lexical_relative_posix(target, root) is None:
        return None, "unsafe_path"
    return target, "regular"


def _read_regular_fd(path: Path, limit: int) -> tuple[bytes | None, str]:
    """Read a regular non-symlink, non-reparse, non-hardlink file under a strict byte bound."""
    ab = _safe_plain_abspath(path)
    if ab is None:
        return None, "unsafe_path"
    try:
        fs_target = _fs_str(ab)
        lst = os.lstat(fs_target)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "unreadable"
    if stat.S_ISLNK(lst.st_mode) or _is_reparse(lst) or not stat.S_ISREG(lst.st_mode):
        return None, "unsafe_path"
    if lst.st_nlink != 1:
        return None, "unsafe_path"
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(fs_target, flags)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "unreadable"
    try:
        fst = os.fstat(fd)
        if (
            not stat.S_ISREG(fst.st_mode)
            or fst.st_nlink != 1
            or _is_reparse(fst)
            or (fst.st_dev, fst.st_ino) != (lst.st_dev, lst.st_ino)
        ):
            os.close(fd)
            return None, "unsafe_path"
        if fst.st_size > limit:
            os.close(fd)
            return None, "too_large"
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


def _read_vault_rel(root: Path, rel: str, limit: int) -> tuple[bytes | None, str]:
    target, state = _check_safe_rel_path(root, rel)
    if state != "regular" or target is None:
        return None, state
    return _read_regular_fd(target, limit)


def _extract_deny_digests(doc: dict) -> tuple[list[str] | None, str]:
    """Extract sorted unique SHA-256 digests of permissions.deny rules without retaining raw text."""
    perms = doc.get("permissions")
    if perms is None:
        return [], "ok"
    if not isinstance(perms, dict):
        return None, "invalid_permissions_schema"
    deny = perms.get("deny")
    if deny is None:
        return [], "ok"
    if not isinstance(deny, list):
        return None, "invalid_deny_schema"
    if len(deny) > MAX_DENY_RULES_PER_FILE:
        return None, "deny_rule_limit_exceeded"
    digests: set[str] = set()
    for item in deny:
        if type(item) is not str:
            return None, "invalid_deny_entry"
        stripped = item.strip()
        if stripped:
            digests.add(_rule_digest(stripped))
    return sorted(digests), "ok"


def _target_display_key(
    target_path: Path,
    raw_target: str,
    resolved_vault: Path,
    effective_plugin: Path | None,
) -> tuple[str, bool]:
    """Deterministic, collision-resistant, non-leaking lexical identifier for a hook script.

    Returns ``(display_key, is_safe_raw)``. Never calls ``.resolve()`` so symlinked paths
    keep their own lexical key and cannot collide with legitimate targets before ``lstat``.
    """
    if _is_unsafe_raw_path(raw_target) or _is_unsafe_raw_path(str(target_path)):
        digest8 = _sha256_bytes(raw_target.encode("utf-8", errors="replace"))[:8]
        return f"<unsafe:{digest8}>", False
    rel_v = _lexical_relative_posix(target_path, resolved_vault)
    if rel_v is not None:
        return rel_v, True
    if effective_plugin is not None:
        rel_p = _lexical_relative_posix(target_path, effective_plugin)
        if rel_p is not None:
            return "<plugin>/" + rel_p, True
    ab = _safe_plain_abspath(target_path)
    if ab is None:
        digest8 = _sha256_bytes(raw_target.encode("utf-8", errors="replace"))[:8]
        return f"<unsafe:{digest8}>", False
    norm_id = os.path.normcase(str(ab))
    ext_hash8 = _sha256_bytes(norm_id.encode("utf-8"))[:8]
    tail = raw_target.replace("\\", "/").strip("/")
    safe_parts = [p for p in tail.split("/") if p and p not in (".", "..") and not DRIVE_PREFIX_RE.match(p)]
    tail_clean = "/".join(safe_parts[-3:]) if safe_parts else "script"
    return f"<ext:{ext_hash8}>/{tail_clean}", True


def _check_safe_target_file(
    target_path: Path,
    resolved_vault: Path,
    effective_plugin: Path | None,
) -> tuple[bytes | None, str]:
    """Read a hook script safely; verify intermediate components when inside vault or plugin."""
    if _is_unsafe_raw_path(str(target_path)):
        return None, "unsafe_path"
    for base in (resolved_vault, effective_plugin):
        if base is None:
            continue
        rel = _lexical_relative_posix(target_path, base)
        if rel is not None:
            return _read_vault_rel(base, rel, MAX_HOOK_BYTES)
    return _read_regular_fd(target_path, MAX_HOOK_BYTES)


def _scan_live_state(
    resolved_vault: Path,
    effective_plugin: Path | None,
) -> dict:
    """Collect current settings deny-rule digests, hooks-config hashes, and resolved hook hashes."""
    settings_state: dict[str, dict] = {}
    settings_errors: list[dict[str, str]] = []
    hook_targets: dict[str, dict] = {}
    hook_errors: list[dict[str, str]] = []
    commands_skipped = 0
    hook_target_overflow = False

    def _register_hook(
        display_key: str,
        target_path: Path,
        source: str,
        *,
        safe_raw: bool = True,
        preloaded: tuple[bytes | None, str] | None = None,
    ) -> None:
        nonlocal hook_target_overflow
        if display_key in hook_targets:
            if source not in hook_targets[display_key]["sources"]:
                hook_targets[display_key]["sources"].append(source)
            return
        if len(hook_targets) >= MAX_HOOK_TARGETS:
            hook_target_overflow = True
            return
        if not safe_raw:
            data, state = None, "unsafe_path"
        elif preloaded is not None:
            data, state = preloaded
        else:
            data, state = _check_safe_target_file(target_path, resolved_vault, effective_plugin)
        if data is not None:
            hook_targets[display_key] = {
                "path": display_key,
                "sources": [source],
                "state": "ok",
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            }
        else:
            hook_targets[display_key] = {
                "path": display_key,
                "sources": [source],
                "state": state,
                "sha256": None,
                "bytes": 0,
            }
            if len(hook_errors) < MAX_REPORTED_DRIFT:
                hook_errors.append({"path": display_key, "source": source, "reason": state})

    for rel in SETTINGS_RELS:
        data, state = _read_vault_rel(resolved_vault, rel, MAX_SETTINGS_BYTES)
        if state == "missing":
            continue
        if data is None:
            settings_errors.append({"source": rel, "reason": state})
            continue
        try:
            doc = _parse_strict_json(data)
        except (UnicodeError, ValueError, RecursionError):
            settings_errors.append({"source": rel, "reason": "invalid_json"})
            continue
        if not isinstance(doc, dict):
            settings_errors.append({"source": rel, "reason": "not_an_object"})
            continue

        hooks_raw = doc.get("hooks")
        if hooks_raw is not None and not isinstance(hooks_raw, dict):
            settings_errors.append({"source": rel, "reason": "invalid_hooks_schema"})
            continue
        try:
            hooks_config_sha256 = _canonical_json_sha256(hooks_raw if hooks_raw is not None else {})
        except (TypeError, ValueError, RecursionError):
            settings_errors.append({"source": rel, "reason": "invalid_hooks_schema"})
            continue

        digests, d_state = _extract_deny_digests(doc)
        if digests is None:
            settings_errors.append({"source": rel, "reason": d_state})
        else:
            settings_state[rel] = {
                "source": rel,
                "settings_sha256": _sha256_bytes(data),
                "hooks_config_sha256": hooks_config_sha256,
                "deny_rule_count": len(digests),
                "deny_rule_digests": digests,
            }

        if not isinstance(hooks_raw, dict):
            continue
        for event_name, groups in hooks_raw.items():
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
                    candidates, was_skipped = _extract_handler_targets(
                        handler, resolved_vault, effective_plugin,
                    )
                    if not candidates or was_skipped:
                        commands_skipped += 1
                    for target_path, raw_target in candidates:
                        key, safe_raw = _target_display_key(
                            target_path, raw_target, resolved_vault, effective_plugin,
                        )
                        _register_hook(key, target_path, rel, safe_raw=safe_raw)

    # Managed vault git hooks (and vault_healthcheck.py when pre-commit is present)
    pre_commit_present = False
    for git_rel in GIT_HOOK_RELS:
        data, state = _read_vault_rel(resolved_vault, git_rel, MAX_HOOK_BYTES)
        if state == "missing":
            continue
        if git_rel == GIT_PRE_COMMIT_REL and data is not None:
            pre_commit_present = True
        _register_hook(
            git_rel,
            resolved_vault / git_rel,
            "git-hooks",
            preloaded=(data, state),
        )
    if pre_commit_present:
        _register_hook(HEALTHCHECK_REL, resolved_vault / HEALTHCHECK_REL, GIT_PRE_COMMIT_REL)

    # Bundled plugin hook scripts when plugin_root is known and safe
    if effective_plugin is not None:
        plugin_ok, _ = _check_safe_root(effective_plugin)
        if plugin_ok is not None:
            for p_rel in PLUGIN_HOOK_RELS:
                p_data, p_state = _read_vault_rel(plugin_ok, p_rel, MAX_HOOK_BYTES)
                if p_state == "missing":
                    continue
                _register_hook(
                    "<plugin>/" + p_rel,
                    plugin_ok / p_rel,
                    "plugin-hooks",
                    preloaded=(p_data, p_state),
                )

    for entry in hook_targets.values():
        entry["sources"] = sorted(entry["sources"])

    return {
        "settings": settings_state,
        "settings_errors": settings_errors,
        "hooks": hook_targets,
        "hook_errors": hook_errors,
        "commands_skipped": commands_skipped,
        "hook_target_overflow": hook_target_overflow,
    }


def _write_json_atomic(root: Path, rel: str, payload: dict) -> None:
    if not _is_safe_rel_posix(rel.replace("\\", "/").strip("/")):
        raise PolicyDriftError("invalid_baseline_path")
    parts = rel.replace("\\", "/").strip("/").split("/")
    if len(parts) < 2:
        raise PolicyDriftError("invalid_baseline_path")
    cur = _plain_path(root)
    for part in parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(_fs_str(cur))
        except FileNotFoundError:
            try:
                os.mkdir(_fs_str(cur))
            except (OSError, ValueError) as exc:
                raise PolicyDriftError("baseline_write_failed") from exc
        except (OSError, ValueError) as exc:
            raise PolicyDriftError("unsafe_baseline_directory") from exc
        else:
            if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
                raise PolicyDriftError("unsafe_baseline_directory")
    target = cur / parts[-1]
    try:
        st = os.lstat(_fs_str(target))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise PolicyDriftError("unsafe_baseline_target") from exc
    else:
        if stat.S_ISLNK(st.st_mode) or _is_reparse(st) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise PolicyDriftError("unsafe_baseline_target")
    try:
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        mkstemp_dir = _fs_str(cur, force_extended=(os.name == "nt" and len(str(cur)) >= 180))
        fd, tmp_name = tempfile.mkstemp(prefix=".vault-policy-baseline-", suffix=".tmp", dir=mkstemp_dir)
    except (OSError, ValueError, TypeError) as exc:
        raise PolicyDriftError("baseline_write_failed") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(_fs_str(tmp_name), _fs_str(target))
    except BaseException as exc:
        try:
            os.unlink(_fs_str(tmp_name))
        except (OSError, ValueError):
            pass
        if isinstance(exc, (OSError, ValueError)):
            raise PolicyDriftError("baseline_write_failed") from exc
        raise


def _load_baseline(resolved_vault: Path) -> tuple[dict | None, str, str]:
    data, state = _read_vault_rel(resolved_vault, BASELINE_REL, MAX_BASELINE_BYTES)
    if state == "missing":
        return None, "not_recorded", "not_recorded"
    if data is None:
        return None, state, "invalid"
    try:
        doc = _parse_strict_json(data)
    except (UnicodeError, ValueError, RecursionError):
        return None, "invalid_json", "invalid"
    if not isinstance(doc, dict) or set(doc.keys()) != BASELINE_TOP_KEYS:
        return None, "invalid_schema", "invalid"
    if doc.get("schema") != BASELINE_SCHEMA:
        return None, "invalid_schema", "invalid"
    if doc.get("authority") != AUTHORITY or doc.get("trust_boundary") != TRUST_BOUNDARY:
        return None, "invalid_schema", "invalid"
    recorded_at = doc.get("recorded_at")
    if type(recorded_at) is not str or not RECORDED_AT_RE.fullmatch(recorded_at):
        return None, "invalid_schema", "invalid"
    claimed = doc.get("baseline_sha256")
    if type(claimed) is not str or not HASH_RE.fullmatch(claimed):
        return None, "baseline_digest_mismatch", "mismatch"
    try:
        actual = _canonical_baseline_sha256(doc)
    except (TypeError, ValueError):
        return None, "baseline_digest_mismatch", "mismatch"
    if claimed != actual:
        return None, "baseline_digest_mismatch", "mismatch"

    settings_list = doc.get("settings")
    hooks_list = doc.get("hooks")
    if (
        not isinstance(settings_list, list)
        or not isinstance(hooks_list, list)
        or len(settings_list) > len(SETTINGS_RELS)
        or len(hooks_list) > MAX_HOOK_TARGETS
    ):
        return None, "invalid_schema", "invalid"
    seen_sources: set[str] = set()
    for item in settings_list:
        if not isinstance(item, dict):
            return None, "invalid_schema", "invalid"
        src = item.get("source")
        digests = item.get("deny_rule_digests")
        hcfg = item.get("hooks_config_sha256")
        if type(src) is not str or src not in SETTINGS_RELS or src in seen_sources:
            return None, "invalid_schema", "invalid"
        if type(hcfg) is not str or not HASH_RE.fullmatch(hcfg):
            return None, "invalid_schema", "invalid"
        if not isinstance(digests, list) or len(digests) > MAX_DENY_RULES_PER_FILE:
            return None, "invalid_schema", "invalid"
        if any(type(d) is not str or not HASH_RE.fullmatch(d) for d in digests):
            return None, "invalid_schema", "invalid"
        seen_sources.add(src)
    seen_hooks: set[str] = set()
    for item in hooks_list:
        if not isinstance(item, dict):
            return None, "invalid_schema", "invalid"
        hpath = item.get("path")
        hsha = item.get("sha256")
        if type(hpath) is not str or not _is_valid_baseline_hook_path(hpath) or hpath in seen_hooks:
            return None, "invalid_schema", "invalid"
        if type(hsha) is not str or not HASH_RE.fullmatch(hsha):
            return None, "invalid_schema", "invalid"
        seen_hooks.add(hpath)
    return doc, "ok", "verified"


def _resolve_plugin_root(plugin_root: Path | None) -> tuple[Path | None, str]:
    if plugin_root is not None:
        ok, state = _check_safe_root(Path(plugin_root))
        if ok is None:
            return None, f"plugin_root_{state}"
        return ok, "ok"
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        ok, _ = _check_safe_root(Path(env_root))
        return ok, "ok"
    return None, "ok"


def record_policy_baseline(
    vault: Path,
    *,
    plugin_root: Path | None = None,
) -> dict:
    """Record a sealed, content-free baseline of deny-rule digests and hook script hashes."""
    resolved_vault, r_state = _check_safe_root(Path(vault))
    if resolved_vault is None:
        raise PolicyDriftError(r_state)
    effective_plugin, p_state = _resolve_plugin_root(plugin_root)
    if p_state != "ok":
        raise PolicyDriftError(p_state)
    live = _scan_live_state(resolved_vault, effective_plugin)
    if live["settings_errors"]:
        raise PolicyDriftError("settings_unreadable_or_invalid")
    if live["hook_errors"]:
        raise PolicyDriftError("hook_target_unreadable_or_unsafe")
    if live["hook_target_overflow"]:
        raise PolicyDriftError("hook_target_limit_exceeded")

    settings_entries = [live["settings"][k] for k in sorted(live["settings"])]
    hook_entries = [
        {
            "path": h["path"],
            "sources": h["sources"],
            "sha256": h["sha256"],
            "bytes": h["bytes"],
        }
        for h in sorted(live["hooks"].values(), key=lambda x: x["path"])
    ]
    baseline = {
        "schema": BASELINE_SCHEMA,
        "recorded_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authority": AUTHORITY,
        "trust_boundary": TRUST_BOUNDARY,
        "settings": settings_entries,
        "hooks": hook_entries,
    }
    baseline["baseline_sha256"] = _canonical_baseline_sha256(baseline)
    _write_json_atomic(resolved_vault, BASELINE_REL, baseline)

    return {
        "schema": SCHEMA,
        "action": "record",
        "status": "recorded",
        "reason_code": "baseline_recorded",
        "baseline_path": BASELINE_REL,
        "baseline_sha256": baseline["baseline_sha256"],
        "authority": AUTHORITY,
        "trust_boundary": TRUST_BOUNDARY,
        "settings_recorded": len(settings_entries),
        "deny_rules_recorded": sum(s["deny_rule_count"] for s in settings_entries),
        "hooks_recorded": len(hook_entries),
        "commands_skipped": live["commands_skipped"],
    }


def diagnose_policy_drift(
    vault: Path,
    *,
    plugin_root: Path | None = None,
) -> dict:
    """Read-only comparison of live deny rules, hooks config, and hook scripts against the sealed baseline."""
    resolved_vault, r_state = _check_safe_root(Path(vault))
    if resolved_vault is None:
        return {
            "schema": SCHEMA,
            "status": "error",
            "reason_code": r_state,
            "next_action": "pass_a_regular_vault_directory",
            "authority": AUTHORITY,
            "trust_boundary": TRUST_BOUNDARY,
            "baseline_integrity": "unavailable",
            "baseline_path": BASELINE_REL,
        }

    effective_plugin, p_state = _resolve_plugin_root(plugin_root)
    if p_state != "ok":
        return {
            "schema": SCHEMA,
            "status": "error",
            "reason_code": p_state,
            "next_action": "pass_a_regular_plugin_root_directory",
            "authority": AUTHORITY,
            "trust_boundary": TRUST_BOUNDARY,
            "baseline_integrity": "unavailable",
            "baseline_path": BASELINE_REL,
        }

    baseline, b_state, integrity = _load_baseline(resolved_vault)
    live = _scan_live_state(resolved_vault, effective_plugin)

    live_deny_total = sum(s["deny_rule_count"] for s in live["settings"].values())
    live_hooks_ok = sum(1 for h in live["hooks"].values() if h["state"] == "ok")

    if baseline is None:
        status = "not_recorded" if b_state == "not_recorded" else "invalid_baseline"
        next_action = (
            "run_vault_policy_drift_record_to_capture_baseline"
            if b_state == "not_recorded"
            else "inspect_and_re_record_policy_baseline"
        )
        return {
            "schema": SCHEMA,
            "status": status,
            "reason_code": b_state,
            "next_action": next_action,
            "authority": AUTHORITY,
            "trust_boundary": TRUST_BOUNDARY,
            "baseline_integrity": integrity,
            "baseline_path": BASELINE_REL,
            "live_settings_files": len(live["settings"]),
            "live_deny_rules": live_deny_total,
            "live_hooks_resolved": live_hooks_ok,
            "commands_skipped": live["commands_skipped"],
            "deny_rules_recorded": 0,
            "deny_rules_removed": 0,
            "deny_rules_added": 0,
            "hooks_config_modified": 0,
            "hooks_recorded": 0,
            "hooks_intact": 0,
            "hooks_modified": 0,
            "hooks_missing": 0,
            "hooks_unsafe": 0,
            "hooks_untracked": 0,
            "settings_errors": live["settings_errors"][:MAX_REPORTED_DRIFT],
            "drift_findings": [],
        }

    drift_findings: list[dict[str, object]] = []
    deny_recorded_total = 0
    deny_removed_total = 0
    deny_added_total = 0
    hooks_config_modified = 0

    def _add_finding(finding: dict[str, object]) -> None:
        if len(drift_findings) < MAX_REPORTED_DRIFT:
            drift_findings.append(finding)

    # 1. Operator permissions.deny monotonicity & hooks-config integrity (arXiv:2611.32195v1 / 2611.01976v1)
    baseline_settings_by_src = {s["source"]: s for s in baseline["settings"]}
    error_sources = {e["source"] for e in live["settings_errors"]}
    for src, rec in sorted(baseline_settings_by_src.items()):
        rec_digests = set(rec["deny_rule_digests"])
        rec_hooks_cfg = rec["hooks_config_sha256"]
        deny_recorded_total += len(rec_digests)
        if src in error_sources:
            deny_removed_total += len(rec_digests)
            hooks_config_modified += 1
            _add_finding({
                "kind": "settings_unreadable_or_invalid",
                "source": src,
                "baseline_deny_rules": len(rec_digests),
            })
            continue
        cur = live["settings"].get(src)
        if cur is None:
            had_hooks = rec_hooks_cfg != EMPTY_HOOKS_CONFIG_SHA256
            if rec_digests or had_hooks:
                deny_removed_total += len(rec_digests)
                if had_hooks:
                    hooks_config_modified += 1
                _add_finding({
                    "kind": "settings_file_missing",
                    "source": src,
                    "removed_count": len(rec_digests),
                    "had_hooks_config": had_hooks,
                })
            continue
        cur_digests = set(cur["deny_rule_digests"])
        removed = sorted(rec_digests - cur_digests)
        added = sorted(cur_digests - rec_digests)
        if removed:
            deny_removed_total += len(removed)
            _add_finding({
                "kind": "deny_rules_removed",
                "source": src,
                "removed_count": len(removed),
                "removed_digest_prefixes": [d[:12] for d in removed[:10]],
            })
        if added:
            deny_added_total += len(added)
        if cur["hooks_config_sha256"] != rec_hooks_cfg:
            hooks_config_modified += 1
            _add_finding({
                "kind": "hooks_config_modified",
                "source": src,
                "recorded_sha256_prefix": rec_hooks_cfg[:12],
                "current_sha256_prefix": cur["hooks_config_sha256"][:12],
            })

    for src, cur in sorted(live["settings"].items()):
        if src not in baseline_settings_by_src:
            deny_added_total += cur["deny_rule_count"]
            if cur["hooks_config_sha256"] != EMPTY_HOOKS_CONFIG_SHA256:
                hooks_config_modified += 1
                _add_finding({
                    "kind": "hooks_config_untracked",
                    "source": src,
                    "current_sha256_prefix": cur["hooks_config_sha256"][:12],
                })

    # 2. Resolved hook script SHA-256 drift check (arXiv:2611.01976v1)
    baseline_hooks_by_path = {h["path"]: h for h in baseline["hooks"]}
    hooks_intact = 0
    hooks_modified = 0
    hooks_missing = 0
    hooks_unsafe = 0

    for hpath, rec in sorted(baseline_hooks_by_path.items()):
        cur = live["hooks"].get(hpath)
        if cur is None:
            if hpath.startswith("<plugin>/"):
                if effective_plugin is None:
                    hooks_missing += 1
                    _add_finding({"kind": "plugin_root_unset", "path": hpath})
                    continue
                rel_p = hpath[len("<plugin>/"):]
                data, state = _read_vault_rel(effective_plugin, rel_p, MAX_HOOK_BYTES)
            elif EXT_PREFIX_RE.match(hpath):
                hooks_missing += 1
                _add_finding({"kind": "hook_unwired_from_settings", "path": hpath})
                continue
            else:
                data, state = _read_vault_rel(resolved_vault, hpath, MAX_HOOK_BYTES)

            if state == "missing":
                hooks_missing += 1
                _add_finding({"kind": "hook_script_missing", "path": hpath})
            elif data is None:
                hooks_unsafe += 1
                _add_finding({"kind": "hook_script_unsafe", "path": hpath, "reason": state})
            else:
                hooks_missing += 1
                content_modified = _sha256_bytes(data) != rec["sha256"]
                if content_modified:
                    hooks_modified += 1
                _add_finding({
                    "kind": "hook_unwired_from_settings",
                    "path": hpath,
                    "content_modified": content_modified,
                })
            continue
        if cur["state"] == "missing":
            hooks_missing += 1
            _add_finding({"kind": "hook_script_missing", "path": hpath})
        elif cur["state"] != "ok":
            hooks_unsafe += 1
            _add_finding({"kind": "hook_script_unsafe", "path": hpath, "reason": cur["state"]})
        elif cur["sha256"] != rec["sha256"]:
            hooks_modified += 1
            _add_finding({
                "kind": "hook_script_modified",
                "path": hpath,
                "recorded_sha256_prefix": rec["sha256"][:12],
                "current_sha256_prefix": str(cur["sha256"])[:12],
            })
        else:
            hooks_intact += 1

    untracked_hooks = sorted(
        hpath for hpath in live["hooks"] if hpath not in baseline_hooks_by_path
    )
    for hpath in untracked_hooks:
        cur_u = live["hooks"][hpath]
        if cur_u["state"] != "ok":
            hooks_unsafe += 1
            _add_finding({"kind": "hook_script_unsafe", "path": hpath, "reason": cur_u["state"]})
        else:
            _add_finding({"kind": "hook_script_untracked", "path": hpath})

    if live["hook_target_overflow"]:
        _add_finding({"kind": "hook_target_limit_exceeded", "limit": MAX_HOOK_TARGETS})

    has_drift = bool(
        deny_removed_total > 0
        or hooks_config_modified > 0
        or hooks_modified > 0
        or hooks_missing > 0
        or hooks_unsafe > 0
        or untracked_hooks
        or live["settings_errors"]
        or live["hook_target_overflow"]
    )
    if has_drift:
        status = "drift_detected"
        if deny_removed_total > 0:
            reason_code = "deny_rules_removed"
            next_action = "review_settings_deny_rules_and_hook_diffs"
        elif hooks_modified > 0 or hooks_unsafe > 0:
            reason_code = "hook_script_drift"
            next_action = "inspect_modified_hook_scripts_before_trusting"
        elif hooks_config_modified > 0:
            reason_code = "hooks_config_modified"
            next_action = "inspect_settings_hooks_changes_before_trusting"
        elif hooks_missing > 0:
            reason_code = "hook_script_missing"
            next_action = "restore_missing_hooks_or_re_record_baseline"
        elif live["settings_errors"]:
            reason_code = "settings_unreadable"
            next_action = "repair_vault_settings_json"
        elif live["hook_target_overflow"]:
            reason_code = "hook_target_limit_exceeded"
            next_action = "reduce_or_audit_hook_targets_above_cap"
        else:
            reason_code = "untracked_hook_target"
            next_action = "review_new_hooks_and_re_record_baseline"
    else:
        status = "clean"
        reason_code = "all_recorded_deny_rules_and_hooks_intact"
        next_action = "none"

    return {
        "schema": SCHEMA,
        "status": status,
        "reason_code": reason_code,
        "next_action": next_action,
        "authority": AUTHORITY,
        "trust_boundary": TRUST_BOUNDARY,
        "baseline_integrity": integrity,
        "baseline_path": BASELINE_REL,
        "recorded_at": baseline["recorded_at"],
        "live_settings_files": len(live["settings"]),
        "live_deny_rules": live_deny_total,
        "live_hooks_resolved": live_hooks_ok,
        "commands_skipped": live["commands_skipped"],
        "deny_rules_recorded": deny_recorded_total,
        "deny_rules_removed": deny_removed_total,
        "deny_rules_added": deny_added_total,
        "hooks_config_modified": hooks_config_modified,
        "hooks_recorded": len(baseline_hooks_by_path),
        "hooks_intact": hooks_intact,
        "hooks_modified": hooks_modified,
        "hooks_missing": hooks_missing,
        "hooks_unsafe": hooks_unsafe,
        "hooks_untracked": len(untracked_hooks),
        "settings_errors": live["settings_errors"][:MAX_REPORTED_DRIFT],
        "drift_findings": drift_findings,
    }


def text_lines(report: dict) -> list[str]:
    return [
        f"policy_drift: {report['status']} ({report['reason_code']})",
        f"policy_drift_baseline_integrity: {report.get('baseline_integrity', 'unknown')}",
        f"policy_drift_deny_rules_recorded: {report.get('deny_rules_recorded', 0)}",
        f"policy_drift_deny_rules_removed: {report.get('deny_rules_removed', 0)}",
        f"policy_drift_deny_rules_added: {report.get('deny_rules_added', 0)}",
        f"policy_drift_hooks_config_modified: {report.get('hooks_config_modified', 0)}",
        f"policy_drift_hooks_recorded: {report.get('hooks_recorded', 0)}",
        f"policy_drift_hooks_intact: {report.get('hooks_intact', 0)}",
        f"policy_drift_hooks_modified: {report.get('hooks_modified', 0)}",
        f"policy_drift_hooks_missing: {report.get('hooks_missing', 0)}",
        f"policy_drift_hooks_unsafe: {report.get('hooks_unsafe', 0)}",
        f"policy_drift_hooks_untracked: {report.get('hooks_untracked', 0)}",
        f"policy_drift_trust_boundary: {report.get('trust_boundary', TRUST_BOUNDARY)}",
    ]


def record_text_lines(report: dict) -> list[str]:
    if report.get("status") != "recorded":
        return [
            f"policy_drift_record: {report.get('status', 'error')} ({report.get('reason_code', 'error')})",
            f"policy_drift_trust_boundary: {report.get('trust_boundary', TRUST_BOUNDARY)}",
        ]
    return [
        f"policy_drift_record: {report['status']} ({report['reason_code']})",
        f"policy_drift_baseline_path: {report.get('baseline_path', BASELINE_REL)}",
        f"policy_drift_baseline_sha256: {report.get('baseline_sha256', '')}",
        f"policy_drift_settings_recorded: {report.get('settings_recorded', 0)}",
        f"policy_drift_deny_rules_recorded: {report.get('deny_rules_recorded', 0)}",
        f"policy_drift_hooks_recorded: {report.get('hooks_recorded', 0)}",
        f"policy_drift_trust_boundary: {report.get('trust_boundary', TRUST_BOUNDARY)}",
    ]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="Record sealed baseline of deny-rule digests and hook script hashes.")
    p_rec.add_argument("--vault", required=True, type=Path)
    p_rec.add_argument("--plugin-root", type=Path, default=None)
    p_rec.add_argument("--format", choices=("text", "json"), default="json")

    p_chk = sub.add_parser("check", help="Read-only drift audit against recorded policy baseline.")
    p_chk.add_argument("--vault", required=True, type=Path)
    p_chk.add_argument("--plugin-root", type=Path, default=None)
    p_chk.add_argument("--format", choices=("text", "json"), default="json")

    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            result = record_policy_baseline(args.vault, plugin_root=args.plugin_root)
            rc = 0
        else:
            result = diagnose_policy_drift(args.vault, plugin_root=args.plugin_root)
            if result["status"] in ("clean", "not_recorded"):
                rc = 0
            elif result["status"] == "drift_detected":
                rc = 1
            else:
                rc = 2
    except PolicyDriftError as exc:
        result = {
            "schema": SCHEMA,
            "status": "error",
            "reason_code": str(exc),
            "authority": AUTHORITY,
            "trust_boundary": TRUST_BOUNDARY,
        }
        rc = 2
    except (OSError, ValueError):
        result = {
            "schema": SCHEMA,
            "status": "error",
            "reason_code": "unexpected_io_error",
            "authority": AUTHORITY,
            "trust_boundary": TRUST_BOUNDARY,
        }
        rc = 2

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.command == "check":
        print("\n".join(text_lines(result)))
    else:
        print("\n".join(record_text_lines(result)))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
