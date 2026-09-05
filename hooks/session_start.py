# -*- coding: utf-8 -*-
"""Inject bounded handoff and hot-note context at session start."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from vault_healthcheck import HealthcheckError, estimate_tokens, validate_config
from vault_paths import resolve_note_path


CONFIG_REL = "00-meta/vault-config.json"
MAX_CONFIG_BYTES = 256 * 1024
MAX_NOTE_BYTES = 256 * 1024
TRUNCATION_MARKER = "\n\n[... truncated ...]"
HANDOFF_HEADER = "=== SESSION HANDOFF (직전 세션 인계) ==="
HOT_HEADER = "=== HOT CONTEXT ==="
INVALID_CONFIG_DIAGNOSTIC = "agentic-vault: invalid session context configuration"


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _read_bounded(path: Path, byte_limit: int) -> tuple[bytes, bool]:
    with path.open("rb") as handle:
        data = handle.read(byte_limit + 1)
    return data[:byte_limit], len(data) > byte_limit


def _load_config(path: Path) -> dict:
    raw_bytes, oversized = _read_bounded(path, MAX_CONFIG_BYTES)
    if oversized:
        raise ValueError("config exceeds byte limit")
    raw = json.loads(raw_bytes.decode("utf-8-sig"))
    config = validate_config(raw)
    for key in ("handoff_max_tokens", "hot_max_tokens"):
        if config[key] < 0:
            raise HealthcheckError(f"{key} must not be negative")
    return config


def _read_note(path: Path) -> tuple[str | None, bool]:
    if not path.is_file():
        return None, False
    raw, oversized = _read_bounded(path, MAX_NOTE_BYTES)
    text = raw.decode("utf-8-sig", errors="replace").strip()
    return (text or None), oversized


def _render_section(
    header: str,
    text: str,
    token_budget: int,
    *,
    source_truncated: bool,
) -> str | None:
    if token_budget <= 0:
        return None
    prefix = header + "\n"
    full = prefix + text
    if not source_truncated and estimate_tokens(full) <= token_budget:
        return full

    if estimate_tokens(prefix + TRUNCATION_MARKER) > token_budget:
        return None

    low = 0
    high = len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = prefix + text[:midpoint] + TRUNCATION_MARKER
        if estimate_tokens(candidate) <= token_budget:
            low = midpoint
        else:
            high = midpoint - 1
    return prefix + text[:low] + TRUNCATION_MARKER


def _configured_section(
    vault: Path,
    config: dict,
    *,
    path_key: str,
    budget_key: str,
    header: str,
) -> str | None:
    budget = config[budget_key]
    rel_path = config[path_key]
    if budget == 0 or not rel_path:
        return None
    path = resolve_note_path(vault, rel_path, config["deny_zones"])
    text, source_truncated = _read_note(path)
    if text is None:
        return None
    return _render_section(
        header,
        text,
        budget,
        source_truncated=source_truncated,
    )


def main(argv: list[str] | None = None) -> int:
    _utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault", type=Path,
        help="Vault directory (defaults to CLAUDE_PROJECT_DIR, then the current directory).",
    )
    args = parser.parse_args(argv)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    try:
        vault = args.vault
        if vault is None:
            vault = Path(project_dir) if project_dir else Path.cwd()
        config_candidate = vault / CONFIG_REL
        if not config_candidate.is_file():
            return 0
        config_path = resolve_note_path(vault, CONFIG_REL)
        config = _load_config(config_path)
        sections = [
            _configured_section(
                vault,
                config,
                path_key="handoff_note",
                budget_key="handoff_max_tokens",
                header=HANDOFF_HEADER,
            ),
            _configured_section(
                vault,
                config,
                path_key="hot_note",
                budget_key="hot_max_tokens",
                header=HOT_HEADER,
            ),
        ]
    except (HealthcheckError, OSError, RuntimeError, UnicodeError, ValueError):
        print(INVALID_CONFIG_DIAGNOSTIC, file=sys.stderr)
        return 0

    rendered = [section for section in sections if section]
    if rendered:
        print("\n\n".join(rendered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
