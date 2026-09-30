# -*- coding: utf-8 -*-
"""Inject bounded handoff and hot-note context at session start."""
from __future__ import annotations

import argparse
from bisect import bisect_left
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "agentic-vault" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from vault_healthcheck import HealthcheckError, estimate_tokens, validate_config
from vault_paths import resolve_note_path


CONFIG_REL = "00-meta/vault-config.json"
MAX_CONFIG_BYTES = 256 * 1024
MAX_NOTE_BYTES = 256 * 1024
# Host transport cap for the whole stdout, in UTF-16 code units (the length a
# JavaScript host measures). Claude Code 2.1.252 replaces hook stdout longer
# than 10,000 units with a saved file and a 2,000-character preview, so the
# rest never reaches the session; the engine keeps a margin below that limit.
# This is not a config key: the token budgets stay the cost budget.
HOST_MAX_OUTPUT_CHARS = 9500
TRUNCATION_MARKER = "\n\n[... truncated ...]"
SECTION_SEPARATOR = "\n\n"
MARKER_MAX_HEADINGS = 5
MARKER_HEADING_CHARS = 40
HANDOFF_HEADER = "=== SESSION HANDOFF (직전 세션 인계) ==="
HOT_HEADER = "=== HOT CONTEXT ==="
INVALID_CONFIG_DIAGNOSTIC = "agentic-vault: invalid session context configuration"

# Code fences, so that '#' comment lines inside fenced code are not listed as
# headings. Both patterns run in linear time on any line.
_FENCE_OPEN = re.compile(r" {0,3}(?:(`{3,})[^`]*|(~{3,}).*)")
_FENCE_CLOSE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")


class SectionSource(NamedTuple):
    """One note ready to render: LF-only text and the limits that apply to it."""

    header: str
    text: str
    token_budget: int
    source_truncated: bool
    source_path: str


class RenderedSection(NamedTuple):
    """One emitted section.

    `truncated` means a truncation marker ends the text. `host_capped` means
    the host output cap cut the section beyond what its token budget allowed.
    """

    text: str
    truncated: bool
    host_capped: bool


class SessionContext(NamedTuple):
    """Rendered sections, aligned with the sources, and their joined text."""

    sections: tuple[RenderedSection | None, ...]
    text: str

    @property
    def stdout(self) -> str:
        """Exactly what the hook writes: LF newlines and one final newline."""
        return self.text + "\n" if self.text else ""


def utf16_len(text: str) -> int:
    """Return the length the host measures: UTF-16 code units, as in JavaScript."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


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
    text = raw.decode("utf-8-sig", errors="replace")
    # One newline form, so the measured length is what the host receives.
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return (text or None), oversized


def _heading_title(line: str) -> str:
    """Return the title of an ATX heading line, or "" for any other line.

    A heading has at most three spaces of indent, one to six '#', then a
    space, a tab or the end of the line; a closing run of '#' after a space
    is dropped. Plain string operations keep this linear on any input.
    """
    body = line.lstrip(" ")
    if len(line) - len(body) > 3:
        return ""
    level = len(body) - len(body.lstrip("#"))
    rest = body[level:]
    if not 1 <= level <= 6 or rest[:1] not in ("", " ", "\t"):
        return ""
    title = rest.strip(" \t")
    unclosed = title.rstrip("#")
    if not unclosed:
        return ""
    if unclosed != title and unclosed[-1] in " \t":
        title = unclosed
    return " ".join(title.split())


def _heading_index(text: str) -> list[tuple[int, str]]:
    """Return (line start, title) for each Markdown heading outside code fences."""
    headings: list[tuple[int, str]] = []
    fence = ""
    next_start = 0
    for line in text.split("\n"):
        start, next_start = next_start, next_start + len(line) + 1
        if fence:
            closing = _FENCE_CLOSE.fullmatch(line)
            if (
                closing is not None
                and closing.group(1)[0] == fence[0]
                and len(closing.group(1)) >= len(fence)
            ):
                fence = ""
            continue
        opening = _FENCE_OPEN.fullmatch(line)
        if opening is not None:
            fence = opening.group(1) or opening.group(2)
            continue
        title = _heading_title(line)
        if title:
            headings.append((start, title))
    return headings


def _rich_marker(source_path: str, shown: Sequence[str], omitted_count: int) -> str:
    """Name the note, the omitted headings and the topic search for the rest."""
    clauses = [f"... truncated: {source_path}" if source_path else "... truncated"]
    if shown:
        titles = " | ".join(
            title if len(title) <= MARKER_HEADING_CHARS
            else title[: MARKER_HEADING_CHARS - 1] + "…"
            for title in shown
        )
        extra = omitted_count - len(shown)
        clauses.append(
            f"omitted headings: {titles}" + (f" (+{extra} more)" if extra > 0 else "")
        )
    clauses.append("for a topic run /vault-recall <topic> (Codex: recall) ...")
    return "\n\n[" + "; ".join(clauses) + "]"


def _truncate_to_fit(
    prefix: str,
    text: str,
    fits: Callable[[str], bool],
    marker_for: Callable[[int], str],
    high: int,
) -> str | None:
    """Keep the longest fitting head of text, then cut back to a whole line.

    The binary search only moves `low` to cuts that fit. The cut then moves
    back to the last newline inside the kept head; a head without a newline
    keeps the character cut. None means this marker does not fit.
    """
    if not fits(prefix + marker_for(0)):
        return None
    low = 0
    while low < high:
        midpoint = (low + high + 1) // 2
        if fits(prefix + text[:midpoint] + marker_for(midpoint)):
            low = midpoint
        else:
            high = midpoint - 1
    boundary = text.rfind("\n", 0, low)
    if boundary < 0:
        return prefix + text[:low] + marker_for(low)
    candidate = prefix + text[:boundary].rstrip() + marker_for(boundary)
    # A heading moved into the omitted part can lengthen a marker that lists it.
    return candidate if fits(candidate) else None


def _render_section(
    header: str,
    text: str,
    token_budget: int,
    *,
    source_truncated: bool,
    max_chars: int | None = None,
    source_path: str = "",
) -> tuple[str | None, bool]:
    """Render one section and report whether a truncation marker ends it.

    A candidate fits when its estimated tokens stay within token_budget and,
    when max_chars is given, its UTF-16 length stays within max_chars. A cut
    section ends with the marker that names the note and the omitted
    headings, or with the short TRUNCATION_MARKER when that one does not fit.
    The text is None when not even the header and the short marker fit.
    """
    if token_budget <= 0:
        return None, False
    prefix = header + "\n"

    def fits(candidate: str) -> bool:
        return estimate_tokens(candidate) <= token_budget and (
            max_chars is None or utf16_len(candidate) <= max_chars
        )

    full = prefix + text
    if not source_truncated and fits(full):
        return full, False

    headings = _heading_index(text)
    starts = [start for start, _ in headings]
    titles = [title for _, title in headings]

    def rich_marker(cut: int) -> str:
        first = bisect_left(starts, cut)
        return _rich_marker(
            source_path,
            titles[first:first + MARKER_MAX_HEADINGS],
            len(titles) - first,
        )

    def short_marker(cut: int) -> str:
        return TRUNCATION_MARKER

    # A head of n characters is at least n UTF-16 units long.
    high = len(text) if max_chars is None else min(len(text), max_chars)
    for marker_for in (rich_marker, short_marker):
        rendered = _truncate_to_fit(prefix, text, fits, marker_for, high)
        if rendered is not None:
            return rendered, True
    return None, False


def _render_source(source: SectionSource, max_chars: int | None) -> RenderedSection | None:
    text, truncated = _render_section(
        source.header,
        source.text,
        source.token_budget,
        source_truncated=source.source_truncated,
        max_chars=max_chars,
        source_path=source.source_path,
    )
    if text is None:
        return None
    return RenderedSection(text, truncated, max_chars is not None)


def _share_chars(
    needs: Sequence[int],
    weights: Sequence[int],
    floors: Sequence[int],
    available: int,
) -> list[int]:
    """Split `available` UTF-16 units among sections in proportion to weight.

    A section that needs no more than its share keeps its full length and
    the room it leaves goes to the others. A share below a section's floor
    (its header and the short marker) is raised to that floor, so no header
    is lost. The hook's two short headers keep the floors far below the cap.
    """
    caps = list(needs)
    active = list(range(len(needs)))
    remaining = available
    while active:
        total = sum(weights[index] for index in active)
        shares = {index: remaining * weights[index] // total for index in active}
        settled = [index for index in active if needs[index] <= shares[index]]
        if not settled:
            settled = [index for index in active if shares[index] < floors[index]]
            for index in settled:
                caps[index] = floors[index]
        if not settled:
            for index in active:
                caps[index] = shares[index]
            break
        for index in settled:
            remaining -= caps[index]
            active.remove(index)
    return caps


def _joined(sections: Sequence[RenderedSection | None]) -> SessionContext:
    return SessionContext(
        tuple(sections),
        SECTION_SEPARATOR.join(section.text for section in sections if section is not None),
    )


def compose_context(sources: Sequence[SectionSource | None]) -> SessionContext:
    """Render the sections within their token budgets and the host output cap.

    Output that fits HOST_MAX_OUTPUT_CHARS is returned as rendered. Otherwise
    the room left after the separators and the final newline is shared by
    token budget (see _share_chars), and each section longer than its share
    is rendered again under that character cap. Both the hook and the doctor
    use this function, so the doctor reports exactly what the hook emits.
    """
    rendered = [None if source is None else _render_source(source, None) for source in sources]
    context = _joined(rendered)
    if utf16_len(context.stdout) <= HOST_MAX_OUTPUT_CHARS:
        return context

    present = [index for index, section in enumerate(rendered) if section is not None]
    available = HOST_MAX_OUTPUT_CHARS - 1 - len(SECTION_SEPARATOR) * (len(present) - 1)
    needs = [utf16_len(rendered[index].text) for index in present]
    floors = [
        min(need, utf16_len(sources[index].header + "\n" + TRUNCATION_MARKER))
        for index, need in zip(present, needs)
    ]
    weights = [sources[index].token_budget for index in present]
    caps = _share_chars(needs, weights, floors, available)
    for index, need, cap in zip(present, needs, caps):
        if need > cap:
            rendered[index] = _render_source(sources[index], cap)
    return _joined(rendered)


def _configured_source(
    vault: Path,
    config: dict,
    *,
    path_key: str,
    budget_key: str,
    header: str,
) -> SectionSource | None:
    budget = config[budget_key]
    rel_path = config[path_key]
    if budget == 0 or not rel_path:
        return None
    path = resolve_note_path(vault, rel_path, config["deny_zones"])
    text, source_truncated = _read_note(path)
    if text is None:
        return None
    return SectionSource(header, text, budget, source_truncated, rel_path)


def _write_stdout(text: str) -> None:
    """Write UTF-8 bytes as is: no platform newline translation (CRLF)."""
    stream = sys.stdout
    if stream is None:
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
        stream.flush()
        return
    stream.flush()
    buffer.write(text.encode("utf-8", errors="replace"))
    buffer.flush()


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
        sources = [
            _configured_source(
                vault,
                config,
                path_key="handoff_note",
                budget_key="handoff_max_tokens",
                header=HANDOFF_HEADER,
            ),
            _configured_source(
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

    context = compose_context(sources)
    if context.stdout:
        _write_stdout(context.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
