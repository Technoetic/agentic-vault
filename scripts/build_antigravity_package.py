#!/usr/bin/env python3
"""Build or check the Antigravity CLI plugin package that is generated from this repository.

The repository root is the Claude Code and Codex plugin. Antigravity CLI reads another layout, so this
script writes a separate folder (default `antigravity/`): a native `plugin.json`, a `hooks.json` whose
handlers run the session hooks with `--host antigravity`, the two shared skills, every command document
as a skill of the same name, copies of the engine scripts, hooks and assets, and the documents those
files link to. Links keep working: a target that is copied into the package stays relative, any other
target becomes a GitHub URL at the release tag. Sections that only make a skill body too long for
Antigravity move to a reference file next to it. The output is deterministic (sorted walks, UTF-8, LF,
no timestamps), so `check` can compare a committed copy byte for byte.

    python scripts/build_antigravity_package.py build [--repo DIR] [--out DIR]
    python scripts/build_antigravity_package.py check [--repo DIR] [--against DIR]

`build` replaces the output folder only when it is absent or holds an earlier generated package. Both
commands exit 1 with a message when the package cannot be built: the hook module is missing, a skill
body is over 23,500 UTF-8 bytes, a description is over 1,024 characters, a Claude placeholder remains
under `skills/`, or a section to move (or the line that ends it) is gone. `check` also exits 1, printing
each difference, when the folder differs from a fresh build. Text files are written with LF; binary files
(a NUL byte or a known image or archive suffix) are copied as they are. The generator writes bytes, not
file modes: when the package folder lies in the repository's git work tree, `check` also compares the git
index mode of every copied file with its source (the git hooks are 100755), names the
`git update-index --chmod` that fixes a difference and exits 1 on any. A file the index does not hold yet
is not compared, and a folder outside the work tree (a release ZIP) is compared by bytes only; the last
line of `check` says which. Standard library only.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Iterable
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/Technoetic/agentic-vault"
SCHEMA_URL = "https://antigravity.google/schemas/v1/plugin.json"
PACKAGE_NAME = "agentic-vault"
DISPLAY_NAME = "Agentic Vault"
PACKAGE_FOLDER = "antigravity"
MANIFEST_DESCRIPTION = (
    "File-based agentic memory: an Obsidian Zettelkasten vault as the agent's persistent state layer, "
    "with session handoff, layered memory and an LLM wiki."
)
SUGGESTED_PROMPTS = (
    "Restore my session context from this vault",
    "Search this vault for notes on a topic",
    "Check this vault's integrity",
)
COPIED_MANIFEST_KEYS = ("author", "homepage", "repository", "license")
MAX_SKILL_BODY_BYTES = 23_500
MAX_DESCRIPTION_CHARS = 1_024
HOOK_FILES = ("session_start.py", "session_checkpoint.py", "antigravity_hook.py", "run_python_hook.sh")
HOOK_MODULE = "hooks/antigravity_hook.py"
# Three handlers per event because the shell Antigravity uses for hook commands on Windows is not
# documented; a claim file in the hooks lets one of them act per event (hooks/antigravity_hook.py).
HOOK_RUNNERS = ("sh ./hooks/run_python_hook.sh", "py -3", "python")
HOOK_EVENTS = (("SessionStart", "session_start.py", 30), ("Stop", "session_checkpoint.py", 15))
SHARED_SKILLS = ("agentic-vault", "webapp-principles")
PLACEHOLDERS = (("${CLAUDE_PLUGIN_ROOT}", "<PLUGIN_ROOT>"), ("$ARGUMENTS", "<ARGUMENTS>"))
DOC_PATH = re.compile(r"docs/(?:security/)?[^/]+\.md")
EXCLUDED_DOC_FOLDERS = frozenset({"releases", "verification", "superpowers", "plans", "screenshots"})
SKIPPED_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv"})
SKIPPED_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
SKIPPED_SUFFIXES = (".pyc", ".pyo", ".tmp", "~")
# Copied byte for byte, never normalised (a NUL byte also marks a file as binary). A logo for a
# marketplace listing, for one, may have no NUL byte and a CRLF in its signature.
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".zip")
GIT_TIMEOUT = 120
# Variables that would make git read a repository or index other than the one holding --repo.
GIT_REPOSITORY_VARIABLES = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                                      "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                                      "GIT_PREFIX"})
# git update-index --chmod option that gives a copy the mode of its source.
CHMOD_FLAGS = {"100755": "+x", "100644": "-x"}
BYTE_ORDER_MARK = chr(0xFEFF)

COMMAND_HEADER = "\n".join((
    "> **Antigravity CLI용 생성 문서.** 원본은 저장소의 `commands/{name}.md`이고 "
    "`scripts/build_antigravity_package.py`가 만든다. 이 파일을 직접 고치지 않는다.",
    "> - `<PLUGIN_ROOT>`: 이 플러그인 설치 폴더의 절대 경로다. 이 SKILL.md가 있는 폴더의 두 단계 위 폴더다"
    "(보통 `~/.gemini/config/plugins/agentic-vault`). 셸 명령에는 펼친 절대 경로를 따옴표로 감싸 넣는다.",
    "> - `<ARGUMENTS>`: 사용자가 슬래시 명령 뒤에 적은 텍스트다. 없으면 이번 요청에서 찾는다.",
    "> - 볼트 루트는 작업 공간에서 `00-meta/vault-config.json`이 있는 폴더다. 볼트를 다루는 엔진 스크립트에 "
    "볼트를 넘길 때는 `--vault <볼트 루트>`를 쓴다. 본문 명령의 인자는 적힌 대로 쓴다.",
    "> - 본문의 `/vault-*` 표기는 이 플러그인의 같은 이름 스킬을 뜻한다. Claude Code 전용 단계"
    "(`.claude/settings.json`·`.claude/settings.local.json` 병합, PreToolUse 훅 설치)는 Antigravity 세션에서 "
    "실행하지 않는다. `.claude/rules/`와 AGENTS.md는 본문대로 만든다.",
))
REFERENCE_ROOT_NOTE = "`<PLUGIN_ROOT>`는 이 플러그인 설치 폴더의 절대 경로로, 이 파일이 있는 폴더의 세 단계 위 폴더다."


class SectionMove:
    """Sections of one source that move to a reference file in the package.

    A section runs from its heading to the next heading of the same or a higher level. `ends`, when given,
    ends it earlier, at the first line after the heading (outside fenced code) for which it is true: text
    that belongs to the parent step but has no heading of its own stays in the skill. The build fails when
    that line is not found before the next heading (`end_wanted` names it in the message).

    A plain class rather than a dataclass, so the module also loads through
    importlib without being registered in sys.modules (as the tests load scripts).
    """

    def __init__(self, *, source: str, target: str, title: str, origin: str, replacement: str, wanted: str,
                 matches: Callable[[int, str], bool], ends: Callable[[str], bool] | None = None,
                 end_wanted: str = "") -> None:
        self.source = source
        self.target = target
        self.title = title
        self.origin = origin
        self.replacement = replacement
        self.wanted = wanted
        self.matches = matches
        self.ends = ends
        self.end_wanted = end_wanted


MOVES = (
    SectionMove(
        source="skills/agentic-vault/SKILL.md",
        target="skills/agentic-vault/references/optional-helpers.md",
        title="# 선택형 도구 안내",
        origin="> 원본은 저장소 `skills/agentic-vault/SKILL.md`의 '선택형' 소절이고, Antigravity 스킬 본문 한도 때문에 "
               "`scripts/build_antigravity_package.py`가 이 파일로 옮겼다. 이 파일을 직접 고치지 않는다.",
        replacement="> 선택형 도구 안내(교훈 검증·묶음 복구·큰 볼트 운영·변경 영향·기억 형성·기억 철회·기록 보존·기억 신뢰 경계)는 "
                    "[references/optional-helpers.md](references/optional-helpers.md)로 옮겼다(Antigravity 스킬 본문 한도). "
                    "해당 도구를 쓸 때 그 파일을 읽는다.",
        wanted="'### 선택형' sections",
        matches=lambda level, title: level == 3 and title.startswith("선택형"),
    ),
    SectionMove(
        source="commands/vault-init.md",
        target="skills/vault-init/references/claude-code-hooks.md",
        title="# vault-init: Claude Code 전용 선택 훅(4-2·4-3)",
        origin="> 원본은 저장소 `commands/vault-init.md`의 4-2·4-3절이고, Claude Code 전용 선택 설치라 "
               "`scripts/build_antigravity_package.py`가 이 파일로 옮겼다. 이 파일을 직접 고치지 않는다. "
               + REFERENCE_ROOT_NOTE,
        replacement="> 4-2(deny zone 가드)와 4-3(교훈 단서 훅)은 Claude Code 전용 선택 설치라 "
                    "[references/claude-code-hooks.md](references/claude-code-hooks.md)로 옮겼다. Antigravity 세션은 "
                    "이 훅을 쓰지 않는다. 같은 볼트를 Claude Code에서도 쓰고 사용자가 따로 승인했을 때만 그 파일의 "
                    "절차를 따른다.",
        wanted="sections '## 4-2.' and '## 4-3.'",
        matches=lambda level, title: level == 2 and title.startswith(("4-2.", "4-3.")),
    ),
    # In commands/vault-upgrade.md this `###` section is followed, without a heading, by the 0.9.0 list and
    # the two verification lines of step 2. The verification lines always stay in the skill body (the move
    # ends at the first of them). The 0.9.0 list moves too, because with it the skill body passes
    # MAX_SKILL_BODY_BYTES; tests/test_antigravity_package.py measures that and fails once it would fit.
    SectionMove(
        source="commands/vault-upgrade.md",
        target="skills/vault-upgrade/references/helper-bundle.md",
        title="# vault-upgrade: 보강 helper bundle과 선택 이행, 0.9.0 안내 목록",
        origin="> 원본은 저장소 `commands/vault-upgrade.md` 2단계의 '보강 helper bundle과 선택 이행' 절과 그 뒤의 "
               "`0.9.0` 안내 목록이고(2단계의 검증 두 줄은 스킬 본문에 남겼다), Antigravity 스킬 본문 한도 때문에 "
               "`scripts/build_antigravity_package.py`가 이 파일로 옮겼다. 이 파일을 직접 고치지 않는다. "
               + REFERENCE_ROOT_NOTE,
        replacement="> 보강 helper bundle과 선택 이행 절차, 그리고 그 뒤에 이어지던 `0.9.0` 안내 목록 4개(Codex 진입점·"
                    "세션 주입 예산·`/vault-recall` 파일 셋·백업 스냅샷)는 "
                    "[references/helper-bundle.md](references/helper-bundle.md)로 옮겼다(Antigravity 스킬 본문 한도). "
                    "그 파일을 읽고 따른 뒤 아래 두 줄로 검증한다.",
        wanted="section '### 보강 helper bundle과 선택 이행'",
        matches=lambda level, title: level == 3 and title == "보강 helper bundle과 선택 이행",
        ends=lambda line: line.startswith("- 볼트가 git 저장소이고"),
        end_wanted="the step 2 verification line '- 볼트가 git 저장소이고 …'",
    ),
)
MOVES_BY_SOURCE = {move.source: move for move in MOVES}
# Stands for the first moved section until the links of the kept text are rewritten.
SENTINEL = "\0agentic-vault-moved-sections\0"

FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
ATX_HEADING = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
# Lines that begin a new block, so an inline code span never runs across them.
BLOCK_START = re.compile(r"^[ \t]*(?:[-*+][ \t]|\d{1,9}[.)](?:[ \t]|$)|#{1,6}(?:[ \t]|$)|>|\|)")
LINK_TAIL = re.compile(r"[ \t]*(?:\n[ \t]*)?(?:(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\))[ \t]*)?\)")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
SAFE_URL_CHARS = "/!$&'*+,;=:@"


class BuildError(Exception):
    """The package cannot be built or written; the message says why (one problem per line)."""


class Package:
    """A generated package held in memory (package path -> bytes) and what the build did.

    skill_bodies: SKILL.md path -> body bytes; moved: the sections each move took; links: package path ->
    counts of links kept relative (and how many of those changed) and links turned into GitHub URLs;
    docs: the documents included; github_missing: GitHub-linked targets the repository does not hold;
    copied: the package paths that are copies of the same repository path (scripts, hooks, assets).
    """

    def __init__(self, *, version: str, files: dict[str, bytes], skill_bodies: dict[str, int], moved: list[dict],
                 links: dict[str, dict[str, int]], docs: list[str], github_missing: list[str],
                 copied: list[str] | None = None) -> None:
        self.version = version
        self.files = files
        self.skill_bodies = skill_bodies
        self.moved = moved
        self.links = links
        self.docs = docs
        self.github_missing = github_missing
        self.copied = list(copied or [])


# --- Markdown scanning -------------------------------------------------------------------------------

def _fence_marker(line: str) -> str | None:
    match = FENCE.match(line)
    if not match:
        return None
    marker = match.group(1)
    # A backtick fence's info string may not contain a backtick; "```x```" is inline code.
    if marker[0] == "`" and "`" in line[match.end():]:
        return None
    return marker


def _closes(line: str, fence: str) -> bool:
    marker = _fence_marker(line)
    return marker is not None and marker[0] == fence[0] and len(marker) >= len(fence) and line.strip() == marker


def _prose_blocks(text: str) -> list[tuple[int, int]]:
    """Character ranges of paragraph-like blocks outside fenced code blocks."""
    blocks: list[tuple[int, int]] = []
    fence: str | None = None
    start: int | None = None
    end = 0
    offset = 0
    for line in text.split("\n"):
        line_start, line_end = offset, offset + len(line)
        offset = line_end + 1
        if fence is not None:
            if _closes(line, fence):
                fence = None
            continue
        marker = _fence_marker(line)
        if marker or not line.strip() or BLOCK_START.match(line):
            if start is not None:
                blocks.append((start, end))
                start = None
            if marker:
                fence = marker
                continue
            if not line.strip():
                continue
            if ATX_HEADING.match(line):
                # A heading is a block of its own line; the next line starts a new block.
                blocks.append((line_start, line_end))
                continue
        if start is None:
            start = line_start
        end = line_end
    if start is not None:
        blocks.append((start, end))
    return blocks


def _code_spans(block: str) -> list[tuple[int, int]]:
    """Ranges of inline code spans: a backtick run up to the next run of the same length."""
    spans: list[tuple[int, int]] = []
    index, size = 0, len(block)
    while index < size:
        if block[index] != "`":
            index += 1
            continue
        run_end = index
        while run_end < size and block[run_end] == "`":
            run_end += 1
        width, scan, close = run_end - index, run_end, -1
        while scan < size:
            if block[scan] != "`":
                scan += 1
                continue
            other = scan
            while other < size and block[other] == "`":
                other += 1
            if other - scan == width:
                close = other
                break
            scan = other
        if close < 0:
            index = run_end
        else:
            spans.append((index, close))
            index = close
    return spans


def _link_destinations(block: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Ranges of the destinations of inline links and images outside code spans."""
    starts = [span[0] for span in spans]

    def in_code(position: int) -> bool:
        index = bisect.bisect_right(starts, position) - 1
        return index >= 0 and position < spans[index][1]

    found: list[tuple[int, int]] = []
    size, index = len(block), 0
    while True:
        opening = block.find("[", index)
        if opening < 0:
            return found
        index = opening + 1
        if in_code(opening) or (opening and block[opening - 1] == "\\"):
            continue
        depth, closing = 0, opening
        while closing < size:
            char = block[closing]
            if char == "\\":
                closing += 2
                continue
            if not in_code(closing):
                if char == "[":
                    depth += 1
                elif char == "]":
                    depth -= 1
                    if depth == 0:
                        break
            closing += 1
        if closing + 1 >= size or block[closing] != "]" or block[closing + 1] != "(":
            continue
        start = closing + 2
        if start < size and block[start] == "<":
            stop = block.find(">", start + 1)
            if stop < 0 or "\n" in block[start:stop]:
                continue
            destination, after = (start + 1, stop), stop + 1
        else:
            nesting, stop = 0, start
            while stop < size:
                char = block[stop]
                if char == "\\":
                    stop += 2
                    continue
                if char.isspace() or ord(char) < 32:
                    break
                if char == "(":
                    nesting += 1
                elif char == ")":
                    if nesting == 0:
                        break
                    nesting -= 1
                stop += 1
            destination, after = (start, min(stop, size)), min(stop, size)
        tail = LINK_TAIL.match(block, after)
        if not tail:
            continue
        found.append(destination)
        index = tail.end()


def link_destinations(text: str) -> list[tuple[int, int]]:
    """Offsets of every inline link or image destination outside fenced code and inline code."""
    found: list[tuple[int, int]] = []
    for start, end in _prose_blocks(text):
        block = text[start:end]
        for first, last in _link_destinations(block, _code_spans(block)):
            found.append((start + first, start + last))
    return found


def _outside_fences(lines: list[str]) -> list[bool]:
    """For each line, whether it lies outside fenced code blocks (fence lines count as inside)."""
    flags: list[bool] = []
    fence: str | None = None
    for line in lines:
        if fence is not None:
            if _closes(line, fence):
                fence = None
            flags.append(False)
            continue
        marker = _fence_marker(line)
        if marker:
            fence = marker
        flags.append(not marker)
    return flags


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, title) of ATX headings outside fenced code blocks."""
    found: list[tuple[int, int, str]] = []
    for index, (line, outside) in enumerate(zip(lines, _outside_fences(lines))):
        match = ATX_HEADING.match(line) if outside else None
        if match:
            found.append((index, len(match.group(1)), (match.group(2) or "").strip()))
    return found


def _cut_sections(body: str, move: SectionMove) -> tuple[str, list[str]]:
    """Remove the matching sections; the first one leaves SENTINEL behind. Returns (kept, sections)."""
    lines = body.split("\n")
    headings = _headings(lines)
    outside = _outside_fences(lines)
    ranges: list[tuple[int, int]] = []
    for position, (index, level, title) in enumerate(headings):
        if not move.matches(level, title) or (ranges and index < ranges[-1][1]):
            continue
        end = len(lines)
        for later, later_level, _ in headings[position + 1:]:
            if later_level <= level:
                end = later
                break
        if move.ends is not None:
            stop = next((number for number in range(index + 1, end) if outside[number] and move.ends(lines[number])),
                        None)
            if stop is None:
                raise BuildError(f"{move.source}: {move.wanted} does not end at {move.end_wanted} before the next "
                                 "heading of its level or above; update scripts/build_antigravity_package.py")
            end = stop
        ranges.append((index, end))
    if not ranges:
        raise BuildError(f"{move.source}: no {move.wanted} to move; update scripts/build_antigravity_package.py")
    kept: list[str] = []
    sections: list[str] = []
    cursor = 0
    for number, (start, end) in enumerate(ranges):
        kept.extend(lines[cursor:start])
        if number == 0:
            kept.extend((SENTINEL, ""))
        sections.append("\n".join(lines[start:end]).strip("\n"))
        cursor = end
    kept.extend(lines[cursor:])
    return "\n".join(kept), sections


# --- Sources ------------------------------------------------------------------------------------------

def _is_binary(data: bytes, relative: str) -> bool:
    return b"\0" in data or relative.lower().endswith(BINARY_SUFFIXES)


def _normalise(data: bytes, relative: str) -> bytes:
    """LF line ends for text; a file with a NUL byte or a known binary suffix is kept as it is."""
    return data if _is_binary(data, relative) else data.replace(b"\r\n", b"\n")


def _decode(data: bytes, relative: str) -> str:
    if b"\0" in data:
        raise BuildError(f"{relative}: expected UTF-8 text, found a NUL byte")
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError as error:
        raise BuildError(f"{relative}: not UTF-8 ({error.reason} at byte {error.start})") from None


def _read_text(repo: Path, relative: str) -> str:
    return _decode((repo / relative).read_bytes(), relative)


def _walk(base: Path, label: str) -> list[str]:
    """Sorted POSIX paths of the files under base, without caches and editor or OS clutter."""
    if not base.is_dir():
        raise BuildError(f"{label} is missing")
    found: list[str] = []
    for directory, dirnames, filenames in os.walk(base):
        current = Path(directory)
        for name in dirnames:
            if (current / name).is_symlink():
                raise BuildError(f"{label}: symbolic link {(current / name).relative_to(base).as_posix()} is not copied")
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIRS)
        for name in filenames:
            if name in SKIPPED_FILES or name.endswith(SKIPPED_SUFFIXES):
                continue
            path = current / name
            if path.is_symlink():
                raise BuildError(f"{label}: symbolic link {path.relative_to(base).as_posix()} is not copied")
            found.append(path.relative_to(base).as_posix())
    return sorted(found)


def _split_frontmatter(text: str, where: str) -> tuple[str, list[str], str]:
    """Return (the frontmatter block with both delimiter lines, the lines inside it, the text after it)."""
    text = text.removeprefix(BYTE_ORDER_MARK)
    lines = text.split("\n")
    if lines[0] != "---":
        raise BuildError(f"{where}: no frontmatter")
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return "\n".join(lines[:index + 1]) + "\n", lines[1:index], "\n".join(lines[index + 1:])
    raise BuildError(f"{where}: frontmatter is not closed")


def _field(lines: list[str], key: str, where: str) -> str | None:
    """A single-line frontmatter value: plain, double-quoted (JSON-compatible) or single-quoted."""
    found: str | None = None
    for index, line in enumerate(lines):
        if not line.startswith(key + ":"):
            continue
        if found is not None:
            raise BuildError(f"{where}: {key} appears twice in the frontmatter")
        found = line[len(key) + 1:].strip()
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if not found or found[0] in "|>" or following[:1] in (" ", "\t"):
            raise BuildError(f"{where}: {key} must be a single-line value")
    if found is None:
        return None
    if found.startswith('"'):
        try:
            value = json.loads(found)
        except ValueError:
            raise BuildError(f"{where}: {key} is not a valid double-quoted value") from None
        if not isinstance(value, str):
            raise BuildError(f"{where}: {key} is not a string")
        return value
    if found.startswith("'"):
        if len(found) < 2 or not found.endswith("'"):
            raise BuildError(f"{where}: {key} is not a valid single-quoted value")
        return found[1:-1].replace("''", "'")
    return found


def skill_body_bytes(text: str) -> int:
    """UTF-8 bytes of a SKILL.md body after the frontmatter, leading and trailing newlines trimmed."""
    return len(_split_frontmatter(text, "SKILL.md")[2].strip("\n").encode("utf-8"))


def _substitute(text: str) -> str:
    for placeholder, replacement in PLACEHOLDERS:
        text = text.replace(placeholder, replacement)
    return text


def _relative(target: str, base_dir: str) -> str:
    target_parts = [part for part in target.split("/") if part]
    base_parts = [part for part in base_dir.split("/") if part]
    common = 0
    while common < min(len(target_parts), len(base_parts)) and target_parts[common] == base_parts[common]:
        common += 1
    return "/".join([".."] * (len(base_parts) - common) + target_parts[common:]) or "."


def _quote_path(path: str) -> str:
    return quote(path, safe=SAFE_URL_CHARS)


def _resolve(source: str, target: str) -> tuple[str, str, bool] | None:
    """(repository path, '#fragment', names a folder) for a relative link target; None otherwise."""
    if not target or target.startswith(("#", "/", "\\")) or SCHEME.match(target):
        return None
    path_text, mark, anchor = target.partition("#")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(path_text)))
    if resolved == ".." or resolved.startswith("../"):
        raise BuildError(f"{source}: link target leaves the repository: {target}")
    return resolved, mark + anchor, path_text.endswith("/")


class _Linker:
    """Rewrites link targets for the place a text takes in the package (decision D6)."""

    def __init__(self, repo: Path, version: str, commands: list[str], planned: set[str], docs: set[str]) -> None:
        self.repo = repo
        self.version = version
        self.commands = set(commands)
        self.planned = planned
        self.planned_dirs = {posixpath.dirname(path) for path in planned}
        for folder in list(self.planned_dirs):
            while folder:
                folder = posixpath.dirname(folder)
                self.planned_dirs.add(folder)
        self.planned_dirs.discard("")
        self.docs = docs
        self.hooks = {f"hooks/{name}" for name in HOOK_FILES}
        self.counts: dict[str, dict[str, int]] = {}
        self.missing: set[str] = set()
        self._complete: dict[str, bool] = {}

    def _folder_is_complete(self, folder: str) -> bool:
        if folder not in self._complete:
            source = self.repo / folder
            self._complete[folder] = source.is_dir() and all(
                f"{folder}/{name}" in self.planned for name in _walk(source, folder))
        return self._complete[folder]

    def package_path(self, resolved: str) -> str | None:
        """Where a repository path lives in the package, or None when it is not part of the package."""
        command = re.fullmatch(r"commands/([^/]+)\.md", resolved)
        if command and command.group(1) in self.commands:
            return f"skills/{command.group(1)}/SKILL.md"
        top = resolved.split("/", 1)[0]
        if top in ("skills", "assets") or resolved in self.hooks or resolved in self.docs:
            if resolved in self.planned:
                return resolved
            if resolved in self.planned_dirs and self._folder_is_complete(resolved):
                return resolved
        return None

    def rewrite(self, text: str, source: str, location: str) -> str:
        """Rewrite the relative links of text from source (repository path) for location (package path)."""
        counts = self.counts.setdefault(location, {"relative": 0, "relative_changed": 0, "github": 0})
        pieces: list[str] = []
        last = 0
        for start, end in link_destinations(text):
            target = text[start:end]
            parsed = _resolve(source, target)
            if parsed is None:
                continue
            resolved, fragment, folder = parsed
            mapped = self.package_path(resolved)
            if mapped is not None:
                relative = _relative(mapped, posixpath.dirname(location))
                new = _quote_path(relative + ("/" if folder and not relative.endswith("/") else "")) + fragment
                counts["relative"] += 1
                counts["relative_changed"] += new != target
            elif resolved == ".":
                new = f"{REPO_URL}/tree/v{self.version}{fragment}"
                counts["github"] += 1
            else:
                on_disk = self.repo / resolved
                kind = "tree" if folder or on_disk.is_dir() else "blob"
                if not on_disk.exists():
                    self.missing.add(f"{source}: {target}")
                new = f"{REPO_URL}/{kind}/v{self.version}/{_quote_path(resolved)}{fragment}"
                counts["github"] += 1
            pieces.extend((text[last:start], new))
            last = end
        pieces.append(text[last:])
        return "".join(pieces)

    def copy_problems(self, text: str, path: str) -> list[str]:
        """Relative links of a byte-for-byte copy must find the same path in the package."""
        problems = []
        for start, end in link_destinations(text):
            target = text[start:end]
            parsed = _resolve(path, target)
            if parsed is not None and self.package_path(parsed[0]) != parsed[0]:
                problems.append(f"{path}: link {target} would break in the package (the file is copied unchanged)")
        return problems


def _docs_closure(repo: Path, seeds: dict[str, str]) -> list[str]:
    """docs/*.md and docs/security/*.md linked from the seeds, transitively (never the history folders)."""
    included: set[str] = set()
    queue = sorted(seeds)
    texts = dict(seeds)
    while queue:
        path = queue.pop(0)
        text = texts[path] if path in texts else _read_text(repo, path)
        for start, end in link_destinations(text):
            parsed = _resolve(path, text[start:end])
            if parsed is None:
                continue
            resolved = parsed[0]
            if (resolved in included or not DOC_PATH.fullmatch(resolved)
                    or resolved.split("/")[1] in EXCLUDED_DOC_FOLDERS or not (repo / resolved).is_file()):
                continue
            included.add(resolved)
            queue.append(resolved)
    return sorted(included)


def _check_skill_sources(repo: Path) -> None:
    skills = repo / "skills"
    if not skills.is_dir():
        raise BuildError("skills/ is missing")
    entries = sorted(entry.name for entry in skills.iterdir()
                     if entry.name not in SKIPPED_DIRS and entry.name not in SKIPPED_FILES)
    if entries != sorted(SHARED_SKILLS):
        raise BuildError(f"skills/ holds {', '.join(entries)}; the generator packages exactly "
                         f"{', '.join(SHARED_SKILLS)} (update scripts/build_antigravity_package.py)")
    for relative in _walk(skills / "agentic-vault", "skills/agentic-vault"):
        if relative == "SKILL.md" or relative.startswith("scripts/") or re.fullmatch(r"references/[^/]+\.md", relative):
            continue
        raise BuildError(f"skills/agentic-vault/{relative}: unexpected file (update scripts/build_antigravity_package.py)")
    for relative in _walk(skills / "webapp-principles", "skills/webapp-principles"):
        if relative != "SKILL.md":
            raise BuildError(f"skills/webapp-principles/{relative}: unexpected file "
                             "(update scripts/build_antigravity_package.py)")


# --- Generated files ----------------------------------------------------------------------------------

def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _manifest(claude: dict, version: str) -> dict:
    manifest: dict = {
        "$schema": SCHEMA_URL,
        "name": PACKAGE_NAME,
        "displayName": DISPLAY_NAME,
        "version": version,
        "description": MANIFEST_DESCRIPTION,
        "suggestedPrompts": list(SUGGESTED_PROMPTS),
    }
    for key in COPIED_MANIFEST_KEYS:
        if key in claude:
            manifest[key] = claude[key]
    if "keywords" in claude:
        manifest["keywords"] = [str(keyword).lower() for keyword in claude["keywords"]]
    return manifest


def _hooks() -> dict:
    return {PACKAGE_NAME: {
        event: [{"type": "command", "command": f"{runner} ./hooks/{script} --host antigravity", "timeout": timeout}
                for runner in HOOK_RUNNERS]
        for event, script, timeout in HOOK_EVENTS
    }}


def _readme(version: str, commands: int) -> str:
    install = f"agy plugin install {REPO_URL}/tree/v{version}/{PACKAGE_FOLDER}"
    guide = f"{REPO_URL}/blob/v{version}/docs/antigravity.md"
    return "\n".join((
        "# agentic-vault — Antigravity CLI 패키지",
        "",
        f"이 폴더는 [agentic-vault]({REPO_URL})를 Antigravity CLI(`agy`)에 설치하는 플러그인 패키지다. "
        f"명령 {commands}개가 같은 이름의 스킬로 들어 있다. `hooks.json`의 훅은 세션을 시작할 때 볼트의 인계를 주입하고, "
        "에이전트가 완전히 멈췄을 때(Stop 입력의 `fullyIdle`이 참일 때)만 checkpoint를 남기도록 만들었다. "
        "이 훅 동작은 실제 agy 대화 세션으로는 확인하지 않았다.",
        "",
        f"설치(v{version} 태그 고정):",
        "",
        "```text",
        install,
        "```",
        "",
        "이 폴더는 생성물이다. 직접 고치지 말고 저장소 루트에서 `python scripts/build_antigravity_package.py build`로 "
        f"다시 만든다. 설치·확인 방법과 한계는 [docs/antigravity.md]({guide})에 있다.",
        "",
        "## English",
        "",
        f"This folder is the Antigravity CLI (`agy`) plugin package of [agentic-vault]({REPO_URL}). The {commands} "
        "commands are skills with the same names. The hooks in `hooks.json` are built to inject the vault handoff "
        "when a session starts and to write a checkpoint only when the agent is fully idle (a Stop input with "
        "`fullyIdle` true). This hook behavior is not yet confirmed in a real agy conversation.",
        "",
        f"Install (pinned to the v{version} tag):",
        "",
        "```text",
        install,
        "```",
        "",
        "This folder is generated; do not edit it. Rebuild it from the repository root with "
        "`python scripts/build_antigravity_package.py build`. Installation, a manual check and the limits: "
        f"[docs/antigravity.md]({guide}).",
        "",
    ))


# --- Build --------------------------------------------------------------------------------------------

def _moved_report(move: SectionMove, sections: list[str], reference: str) -> dict:
    return {
        "source": move.source,
        "target": move.target,
        "sections": [{"heading": section.split("\n", 1)[0], "bytes": len(section.encode("utf-8"))}
                     for section in sections],
        "target_bytes": len(reference.encode("utf-8")),
    }


def _reference(move: SectionMove, sections: list[str], linker: _Linker) -> str:
    text = f"{move.title}\n\n{move.origin}\n\n" + "\n\n".join(sections) + "\n"
    return _substitute(linker.rewrite(text, move.source, move.target))


def build_package(repo: Path) -> Package:
    """Generate every package file in memory; raise BuildError listing each problem."""
    repo = Path(repo).resolve()
    try:
        claude = json.loads((repo / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise BuildError(f".claude-plugin/plugin.json cannot be read: {error}") from None
    version = claude.get("version") if isinstance(claude, dict) else None
    if not isinstance(version, str) or not version:
        raise BuildError(".claude-plugin/plugin.json has no version")
    if not (repo / HOOK_MODULE).is_file():
        raise BuildError(f"{HOOK_MODULE} is missing from the repository; the Antigravity hooks run through it, "
                         "so the package cannot be built without it")
    for name in HOOK_FILES:
        if not (repo / "hooks" / name).is_file():
            raise BuildError(f"hooks/{name} is missing from the repository")
    commands = sorted(path.stem for path in (repo / "commands").glob("*.md") if path.is_file())
    if not commands:
        raise BuildError("commands/*.md not found")
    if set(commands) & set(SHARED_SKILLS):
        raise BuildError("a command has the name of a shared skill")
    _check_skill_sources(repo)
    for move in MOVES:
        if not (repo / move.source).is_file():
            raise BuildError(f"{move.source} is missing")

    copies: dict[str, bytes] = {}
    for top in ("assets", "skills/agentic-vault/scripts"):
        for relative in _walk(repo / top, top):
            copies[f"{top}/{relative}"] = _normalise((repo / top / relative).read_bytes(), relative)
    for name in HOOK_FILES:
        copies[f"hooks/{name}"] = _normalise((repo / "hooks" / name).read_bytes(), name)
    references = sorted(path.name for path in (repo / "skills/agentic-vault/references").glob("*.md")
                        if path.is_file())

    seeds: dict[str, str] = {f"commands/{name}.md": _read_text(repo, f"commands/{name}.md") for name in commands}
    for relative in ([f"skills/{name}/SKILL.md" for name in SHARED_SKILLS]
                     + [f"skills/agentic-vault/references/{name}" for name in references]):
        seeds[relative] = _read_text(repo, relative)
    for relative, data in copies.items():
        if relative.endswith(".md"):
            seeds[relative] = _decode(data, relative)
    docs = _docs_closure(repo, seeds)

    planned = set(copies) | {"plugin.json", "hooks.json", "README.md"} | set(docs)
    planned |= {f"skills/{name}/SKILL.md" for name in (*SHARED_SKILLS, *commands)}
    planned |= {f"skills/agentic-vault/references/{name}" for name in references}
    planned |= {move.target for move in MOVES}
    linker = _Linker(repo, version, commands, planned, set(docs))
    problems: list[str] = []
    moved: list[dict] = []
    files: dict[str, bytes] = dict(copies)
    files["plugin.json"] = _json_bytes(_manifest(claude, version))
    files["hooks.json"] = _json_bytes(_hooks())
    files["README.md"] = _readme(version, len(commands)).encode("utf-8")

    for name in SHARED_SKILLS:
        source = f"skills/{name}/SKILL.md"
        block, fields, body = _split_frontmatter(seeds[source], source)
        if _field(fields, "name", source) != name:
            problems.append(f"{source}: frontmatter name must be {name}")
        description = _field(fields, "description", source) or ""
        if not 1 <= len(description) <= MAX_DESCRIPTION_CHARS:
            problems.append(f"{source}: description is {len(description)} characters (limit {MAX_DESCRIPTION_CHARS})")
        move = MOVES_BY_SOURCE.get(source)
        if move is not None:
            body, sections = _cut_sections(body, move)
            reference = _reference(move, sections, linker)
            files[move.target] = reference.encode("utf-8")
            moved.append(_moved_report(move, sections, reference))
        body = linker.rewrite(body, source, source)
        if move is not None:
            body = body.replace(SENTINEL, move.replacement)
        files[source] = (block + _substitute(body).rstrip("\n") + "\n").encode("utf-8")

    for name in references:
        relative = f"skills/agentic-vault/references/{name}"
        files[relative] = _substitute(linker.rewrite(seeds[relative], relative, relative)).encode("utf-8")

    for name in commands:
        source = f"commands/{name}.md"
        location = f"skills/{name}/SKILL.md"
        _, fields, body = _split_frontmatter(seeds[source], source)
        description = _field(fields, "description", source)
        if description is None:
            raise BuildError(f"{source}: no description in the frontmatter")
        hint = _field(fields, "argument-hint", source)
        if hint:
            description = f"{description} 입력: {hint}"
        if not 1 <= len(description) <= MAX_DESCRIPTION_CHARS:
            problems.append(f"{location}: description is {len(description)} characters "
                            f"(limit {MAX_DESCRIPTION_CHARS})")
        body = body.lstrip("\n")
        move = MOVES_BY_SOURCE.get(source)
        if move is not None:
            body, sections = _cut_sections(body, move)
            reference = _reference(move, sections, linker)
            files[move.target] = reference.encode("utf-8")
            moved.append(_moved_report(move, sections, reference))
        body = linker.rewrite(body, source, location)
        if move is not None:
            body = body.replace(SENTINEL, move.replacement)
        header = COMMAND_HEADER.replace("{name}", name)
        files[location] = (
            "---\n"
            f"name: {name}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            "disable-model-invocation: true\n"
            "---\n\n"
            f"{header}\n\n{_substitute(body).strip(chr(10))}\n"
        ).encode("utf-8")

    for relative in docs:
        files[relative] = linker.rewrite(_read_text(repo, relative), relative, relative).encode("utf-8")

    for relative in copies:
        if relative.endswith(".md"):
            problems.extend(linker.copy_problems(seeds[relative], relative))
    bodies: dict[str, int] = {}
    for relative in sorted(files):
        if not relative.startswith("skills/") or not relative.endswith(".md"):
            continue
        text = files[relative].decode("utf-8", errors="replace")
        for placeholder, _ in PLACEHOLDERS:
            if placeholder in text:
                problems.append(f"{relative}: {placeholder} remains")
        if re.fullmatch(r"skills/[^/]+/SKILL\.md", relative):
            bodies[relative] = skill_body_bytes(text)
            if bodies[relative] > MAX_SKILL_BODY_BYTES:
                problems.append(f"{relative}: body is {bodies[relative]} bytes after the transforms "
                                f"(limit {MAX_SKILL_BODY_BYTES})")
    if set(files) != planned:
        problems.append("internal: generated files differ from the planned layout: "
                        + ", ".join(sorted(set(files) ^ planned)))
    if problems:
        raise BuildError("\n".join(sorted(set(problems))))
    return Package(version=version, files=dict(sorted(files.items())), skill_bodies=bodies, moved=moved,
                   links=dict(sorted(linker.counts.items())), docs=docs, github_missing=sorted(linker.missing),
                   copied=sorted(copies))


# --- Output -------------------------------------------------------------------------------------------

def _write_tree(files: dict[str, bytes], root: Path) -> None:
    for relative in sorted(files):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[relative])


def _read_tree(root: Path) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in _walk(root, str(root))}


def _is_generated_package(folder: Path) -> bool:
    manifest = folder / "plugin.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("name") == PACKAGE_NAME and data.get("$schema") == SCHEMA_URL


def write_package(files: dict[str, bytes], out: Path) -> None:
    """Write the package to out, replacing it only when it is absent or an earlier generated package."""
    out = Path(os.path.abspath(out))
    if out.exists() or out.is_symlink():
        if out.is_symlink() or not out.is_dir():
            raise BuildError(f"refusing to replace {out}: it is not a folder")
        if not _is_generated_package(out):
            raise BuildError(f"refusing to replace {out}: only an absent folder or an earlier generated package "
                             f"(plugin.json with name \"{PACKAGE_NAME}\" and the Antigravity $schema) is replaced")
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-build-", dir=out.parent))
    try:
        _write_tree(files, staging)
        if out.exists():
            previous = Path(tempfile.mkdtemp(prefix=f".{out.name}-previous-", dir=out.parent))
            previous.rmdir()
            os.replace(out, previous)
            try:
                os.replace(staging, out)
            except OSError:
                os.replace(previous, out)
                raise
            shutil.rmtree(previous)
        else:
            os.replace(staging, out)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def compare(files: dict[str, bytes], against: Path) -> list[str]:
    """Build into a temporary folder and list every difference with the package folder against."""
    if not against.is_dir():
        raise BuildError(f"package folder not found: {against}")
    with tempfile.TemporaryDirectory(prefix="agentic-vault-antigravity-check-") as temporary:
        fresh = Path(temporary) / PACKAGE_FOLDER
        _write_tree(files, fresh)
        built = _read_tree(fresh)
    existing = _read_tree(against)
    differences = []
    for relative in sorted(set(built) | set(existing)):
        if relative not in existing:
            differences.append(f"missing from package folder: {relative}")
        elif relative not in built:
            differences.append(f"only in package folder: {relative}")
        elif built[relative] != existing[relative]:
            differences.append(f"differs: {relative}")
    return differences


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    """Run git in cwd with no terminal input; None when git cannot be started or does not answer."""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in GIT_REPOSITORY_VARIABLES}
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, env=environment,
                              stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None


def _index_path(prefix: str, relative: str) -> str:
    return f"{prefix}/{relative}" if prefix else relative


def mode_differences(copied: Iterable[str], repo: Path, against: Path) -> tuple[list[str], str]:
    """Compare the git index mode of each copy in the package folder with the mode of its source.

    Returns (one `mode differs:` line per copy, a `git modes:` line that says what was compared). The
    comparison runs only when the package folder lies inside the git work tree that holds repo; a release
    ZIP or a folder elsewhere is compared by bytes alone. A copy or source that the git index does not hold
    (a new file not added yet) is skipped. Raises BuildError when git fails inside that work tree.
    """
    found = _git(repo, "rev-parse", "--show-toplevel")
    if found is None:
        return [], "git modes: not compared (git is not available)"
    if found.returncode != 0 or not found.stdout.strip():
        return [], f"git modes: not compared (git finds no work tree at {repo})"
    top = Path(found.stdout.decode("utf-8", errors="replace").strip()).resolve()
    try:
        repo_prefix = repo.resolve().relative_to(top).as_posix()
        against_prefix = Path(os.path.abspath(against)).resolve().relative_to(top).as_posix()
    except ValueError:
        return [], f"git modes: not compared ({os.path.abspath(against)} is outside the git work tree {top})"
    repo_prefix = "" if repo_prefix == "." else repo_prefix
    if against_prefix in (".", repo_prefix):
        return [], "git modes: not compared (the package folder is the repository itself)"
    listed = _git(top, "ls-files", "--stage", "-z")
    if listed is None or listed.returncode != 0:
        raise BuildError(f"git ls-files --stage failed in {top}; the git modes of the copies cannot be compared")
    modes: dict[str, str] = {}
    for record in listed.stdout.split(b"\0"):
        info, tab, path = record.partition(b"\t")
        fields = info.split(b" ")
        if tab and len(fields) == 3 and fields[2] == b"0":
            modes[path.decode("utf-8", errors="replace")] = fields[0].decode("ascii", errors="replace")
    lines: list[str] = []
    compared = 0
    for relative in sorted(copied):
        copy_path = _index_path(against_prefix, relative)
        source_mode, copy_mode = modes.get(_index_path(repo_prefix, relative)), modes.get(copy_path)
        if source_mode is None or copy_mode is None:
            continue
        compared += 1
        if source_mode == copy_mode:
            continue
        flag = CHMOD_FLAGS.get(source_mode) if copy_mode in CHMOD_FLAGS else None
        fix = f": git update-index --chmod={flag} {copy_path}" if flag else ": no git update-index --chmod fixes this"
        lines.append(f"mode differs: {relative} (package copy {copy_mode}, source {source_mode}){fix}")
    if not compared:
        return [], "git modes: not compared (no copied file is in the git index together with its source)"
    where = f"git modes: compared {compared} copied files with their sources in the git index of {top}"
    return lines, where + ("; run the git update-index commands above there" if lines else "")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="build_antigravity_package.py",
                                     description="Build or check the generated Antigravity CLI package.")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="write the package folder")
    build.add_argument("--repo", type=Path, default=ROOT, help="repository root (default: this checkout)")
    build.add_argument("--out", type=Path, help="output folder (default: <repo>/antigravity)")
    check = commands.add_parser("check", help="compare a package folder with a fresh build")
    check.add_argument("--repo", type=Path, default=ROOT, help="repository root (default: this checkout)")
    check.add_argument("--against", type=Path, help="package folder (default: <repo>/antigravity)")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        package = build_package(repo)
        if args.command == "build":
            out = args.out if args.out is not None else repo / PACKAGE_FOLDER
            write_package(package.files, out)
            skills = sum(1 for relative in package.files if re.fullmatch(r"skills/[^/]+/SKILL\.md", relative))
            print(f"built Antigravity package v{package.version}: {len(package.files)} files, {skills} skills, "
                  f"{len(package.docs)} docs -> {os.path.abspath(out)}")
            return 0
        against = args.against if args.against is not None else repo / PACKAGE_FOLDER
        differences = compare(package.files, against)
        mode_lines, mode_note = mode_differences(package.copied, repo, against)
        differences += mode_lines
    except BuildError as error:
        for line in str(error).splitlines():
            print(f"build_antigravity_package: error: {line}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"build_antigravity_package: error: {error}", file=sys.stderr)
        return 1
    for line in differences:
        print(line)
    if differences:
        print(f"{len(differences)} difference(s) between a fresh build and {os.path.abspath(against)}")
        print(mode_note)
        return 1
    print(f"identical: {len(package.files)} files in {os.path.abspath(against)}")
    print(mode_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
