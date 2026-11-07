#!/usr/bin/env python3
"""Fold old handoff sections into archive notes with no byte discarded.

analyze is the default and reads only. apply needs the handoff hash from analyze
and moves every section outside the protected set, byte for byte, into new size-
bounded archive notes. A short stub stays in place, and unresolved lines are lifted
into one collected section with the hash of the section they came from. Host
summaries are optional and bound to section hashes; without one, the title and the
unresolved lines are all that stay. Nothing runs without a caller-chosen threshold
and protected-section count, and no hook or scheduled task calls this tool.

Sections are split on bytes, so no line ending changes. apply first rebuilds the
original from its own output and refuses on any difference. Archive notes are never
rewritten. Only cooperating writers observe the lock, and the archive notes and the
handoff are separate atomic replaces, not one transaction. A failed run removes the
archive notes it created; a killed run can leave unreferenced ones behind.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, NamedTuple

from vault_evidence import EvidenceError, decode, regular, stable_read
from vault_healthcheck import HealthcheckError
from vault_paths import resolve_note_path
from vault_state import (BEGIN, END, StateError, _Policy, _atomic, _check_lease,
                         _cleanup_temporary, _hash, _lease_guard, advisory_lock)

SECTION_LEVEL = 2
# The handoff is over the shared note limit on the first run, which is the case
# this tool exists for, so its read names its own larger limit.
MAX_HANDOFF_BYTES = 32 * 1024 * 1024
MAX_SETTING_BYTES = 1024 * 1024 * 1024
MAX_SECTIONS = 16384
DEFAULT_ARCHIVE_MAX_BYTES = 256 * 1024
MIN_ARCHIVE_MAX_BYTES = 1024
MAX_SUMMARY_FILE_BYTES = 4 * 1024 * 1024
MAX_SUMMARY_CHARS = 2048
MAX_RULE_ITEMS = 32
MAX_RULE_CHARS = 128
MAX_RULE_KEY_CHARS = 256
MAX_FRONTMATTER_LINES = 64
HASH_PREFIX_CHARS = 12
ARCHIVE_FOLDER = 'handoff-archive'
COLLECTED_TITLE = '미결 모음'
DEFAULT_UNRESOLVED_PREFIXES = ('unresolved:',)
ORDERS = ('top', 'bottom')
PREVIEW_TIME = '1970-01-01T00:00:00Z'
BOM = b'\xef\xbb\xbf'
WIKI_UNSAFE = frozenset('[]|#^')
OWN_FRONTMATTER_KEYS = frozenset(('title', 'type', 'status', 'ai_priority', 'tags', 'related',
                                  'created', 'updated'))
COPIED_FRONTMATTER_KEYS = ('domain', 'project')

_EOL = re.compile(rb'\r\n|\n|\r')
# Both fence patterns and the heading rule below follow hooks/session_start.py.
# tests/test_vault_handoff_fold.py fails when the two readings differ.
_FENCE_OPEN = re.compile(r' {0,3}(?:(`{3,})[^`]*|(~{3,}).*)')
_FENCE_CLOSE = re.compile(r' {0,3}(`{3,}|~{3,})[ \t]*')
_RULE_LEAD = re.compile(r'[ \t]*(?:>[ \t]*)*(?:[-*+][ \t]+)?(?:\[ \][ \t]+)?')
_STUB = re.compile(rb'- \[handoff-fold\] (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) sha256:([0-9a-f]{12}) '
                   rb'\[\[([^\[\]|#^\r\n]+)\]\] unresolved=(\d{1,7})')
_SOURCE_TAIL = re.compile(rb' \[fold-source: sha256:([0-9a-f]{12})\]\Z')
_FRAME_BEGIN = re.compile(rb'<!-- agentic-vault:handoff-fold begin sha256=([0-9a-f]{64}) bytes=(\d{1,12}) -->\n')
_FOLDED_AT = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
_FIELD = re.compile(rb'([A-Za-z_][A-Za-z0-9_-]*):')


class Section(NamedTuple):
    """Half-open byte range of one level-two section."""

    start: int
    end: int
    heading_end: int
    title: str
    sha256: str


class Settings(NamedTuple):
    threshold_bytes: int
    protect_sections: int
    newest: str
    archive_max_bytes: int
    unresolved_prefixes: tuple[str, ...]
    protect_titles: frozenset[str]


class ArchiveNote(NamedTuple):
    name: str
    raw: bytes
    sections: int


class Plan(NamedTuple):
    status: str
    sections_total: int
    already_folded: int
    protected: tuple[str, ...]
    candidates: tuple[dict, ...]
    projected: bytes
    archives: tuple[ArchiveNote, ...]
    unresolved_lines: int


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _lines(raw, start=0, end=None):
    """Yield (line start, content end, next line start) with endings kept out of the content."""
    end = len(raw) if end is None else end
    position = start
    while position < end:
        match = _EOL.search(raw, position, end)
        if match is None:
            yield position, end, end
            return
        yield position, match.start(), match.end()
        position = match.end()


def _line(raw, start, end):
    return raw[start:end].decode('utf-8', errors='replace')


class _Fence:
    """Track one code fence; step returns True for a line outside every fence."""

    def __init__(self):
        self.marker = ''

    def step(self, line):
        if self.marker:
            closing = _FENCE_CLOSE.fullmatch(line)
            if (closing is not None and closing.group(1)[0] == self.marker[0]
                    and len(closing.group(1)) >= len(self.marker)):
                self.marker = ''
            return False
        opening = _FENCE_OPEN.fullmatch(line)
        if opening is not None:
            self.marker = opening.group(1) or opening.group(2)
            return False
        return True


def _heading(line):
    """Return (level, title) of an ATX heading, or (0, '') for any other line."""
    body = line.lstrip(' ')
    if len(line) - len(body) > 3:
        return 0, ''
    level = len(body) - len(body.lstrip('#'))
    rest = body[level:]
    if not 1 <= level <= 6 or rest[:1] not in ('', ' ', '\t'):
        return 0, ''
    title = rest.strip(' \t')
    unclosed = title.rstrip('#')
    if not unclosed:
        return 0, ''
    if unclosed != title and unclosed[-1] in ' \t':
        title = unclosed
    return level, ' '.join(title.split())


def heading_index(raw, start=0):
    """Return (byte offset, level, title) for each heading outside code fences."""
    headings = []
    fence = _Fence()
    for line_start, content_end, _ in _lines(raw, start):
        line = _line(raw, line_start, content_end)
        if line_start == 0:
            line = line.removeprefix('\ufeff')
        if not fence.step(line):
            continue
        level, title = _heading(line)
        if title:
            headings.append((line_start, level, title))
    return headings


def _frontmatter_end(raw):
    """Return the offset after a closed leading frontmatter block, or 0."""
    start = len(BOM) if raw.startswith(BOM) else 0
    opening = True
    for count, (line_start, content_end, next_start) in enumerate(_lines(raw, start)):
        text = raw[line_start:content_end].strip()
        if opening:
            if text != b'---':
                return 0
            opening = False
        elif text == b'---':
            return next_start
        if count > MAX_FRONTMATTER_LINES:
            break
    return 0


def split_sections(raw):
    """Return (end of the preamble, sections); together they cover every byte."""
    starts = [(offset, title) for offset, level, title in heading_index(raw, _frontmatter_end(raw))
              if level == SECTION_LEVEL]
    if len(starts) > MAX_SECTIONS:
        raise StateError('section_limit')
    sections = []
    for index, (start, title) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(raw)
        heading_end = next(_lines(raw, start, end))[2]
        sections.append(Section(start, end, heading_end, title, _sha(raw[start:end])))
    return (starts[0][0] if starts else len(raw)), sections


def _stub_match(raw, section):
    first = next(_lines(raw, section.heading_end, section.end), None)
    return None if first is None else _STUB.fullmatch(raw, first[0], first[1])


def _is_unresolved(line, prefixes):
    lead = _RULE_LEAD.match(line).end()
    key = line[lead:lead + MAX_RULE_KEY_CHARS].replace('`', '').replace('*', '').casefold()
    return key.startswith(prefixes)


def _indent(line):
    return len(line) - len(line.lstrip(' \t'))


def _unresolved(raw, section, prefixes):
    """Return (matched line count, lines to lift); deeper lines follow their match."""
    rows = list(_lines(raw, section.heading_end, section.end))
    fence = _Fence()
    lifted = []
    matched = 0
    index = 0
    while index < len(rows):
        start, end, _ = rows[index]
        index += 1
        line = _line(raw, start, end)
        if not fence.step(line) or not _is_unresolved(line, prefixes):
            continue
        matched += 1
        lifted.append(raw[start:end])
        depth = _indent(line)
        while index < len(rows):
            start, end, _ = rows[index]
            line = _line(raw, start, end)
            # A tagged fence line could no longer close, and would hide every
            # later heading, so a fence stays in the archive with what follows it.
            if not line.strip() or _indent(line) <= depth or _FENCE_OPEN.fullmatch(line):
                break
            lifted.append(raw[start:end])
            index += 1
    return matched, lifted


def _eol_of(raw, start, end=None):
    first = next(_lines(raw, start, end), None)
    return raw[first[1]:first[2]] if first is not None and first[1] != first[2] else b'\n'


def _frame(raw, digest):
    size = str(len(raw)).encode('ascii')
    return (b'<!-- agentic-vault:handoff-fold begin sha256=' + digest.encode('ascii') + b' bytes=' + size
            + b' -->\n' + raw + _frame_end(digest))


def _frame_end(digest):
    return b'\n<!-- agentic-vault:handoff-fold end sha256=' + digest.encode('ascii') + b' -->\n'


def archive_sections(note):
    """Return {full hash: section bytes} from one archive note, checking every frame."""
    start = _frontmatter_end(note)
    if start == 0:
        raise StateError('invalid_archive')
    sections = {}
    while start < len(note):
        begin = _FRAME_BEGIN.match(note, start)
        if begin is None:
            raise StateError('invalid_archive')
        digest = begin.group(1).decode('ascii')
        end = begin.end() + int(begin.group(2))
        body = note[begin.end():end]
        if len(body) != int(begin.group(2)) or _sha(body) != digest:
            raise StateError('archive_hash_mismatch')
        closing = _frame_end(digest)
        if not note.startswith(closing, end):
            raise StateError('invalid_archive')
        sections[digest] = body
        start = end + len(closing)
    return sections


def _without_lifted(raw, section, restored):
    """Drop lifted lines whose source was restored, and the section if nothing else stays."""
    kept = []
    for start, end, next_start in _lines(raw, section.start, section.end):
        tail = _SOURCE_TAIL.search(raw, start, end)
        if tail is None or tail.group(1) not in restored:
            kept.append(raw[start:next_start])
    result = b''.join(kept)
    eol = _eol_of(raw, section.start, section.end)
    return b'' if result == raw[section.start:section.heading_end] + eol + eol else result


def restore_handoff(handoff, notes: Mapping[str, bytes], *, strict=True):
    """Rebuild the handoff from a fold result and {archive name: note bytes}.

    Each stub follows its own wiki link to one note. strict refuses a stub whose
    note or frame is absent; without it such a stub stays, which is how one fold
    round is undone on its own. Host summaries are derived text and do not return.
    """
    frames = {}
    digests = {}
    for name, note in notes.items():
        frames[name] = archive_sections(note)
        for digest in frames[name]:
            digests.setdefault((name, digest[:HASH_PREFIX_CHARS]), set()).add(digest)
    preamble_end, sections = split_sections(handoff)
    expanded = {}
    restored = set()
    for section in sections:
        match = _stub_match(handoff, section)
        if match is None:
            continue
        name = match.group(3).decode('utf-8')
        found = digests.get((name, match.group(2).decode('ascii')), ())
        if len(found) > 1:
            raise StateError('ambiguous_archive_hash')
        if found:
            expanded[section] = frames[name][next(iter(found))]
            restored.add(match.group(2))
        elif strict:
            raise StateError('archive_section_missing')
    parts = [handoff[:preamble_end]]
    for section in sections:
        if section in expanded:
            parts.append(expanded[section])
        elif section.title == COLLECTED_TITLE and _stub_match(handoff, section) is None:
            parts.append(_without_lifted(handoff, section, restored))
        else:
            parts.append(handoff[section.start:section.end])
    return b''.join(parts)


def _frontmatter_fields(raw):
    """Return {key: raw lines} for the top-level keys of the handoff frontmatter."""
    end = _frontmatter_end(raw)
    fields = {}
    current = None
    for start, content_end, _ in _lines(raw, 0, end):
        line = raw[start:content_end]
        field = _FIELD.match(line)
        if field is not None:
            current = field.group(1).decode('ascii')
            fields[current] = [line]
        elif current is not None and line[:1] in (b' ', b'\t', b'-') and line.strip() != b'---':
            fields[current].append(line)
    return fields


def _enum_value(config, key, preferred):
    allowed = (config.get('enums') or {}).get(key)
    if not isinstance(allowed, list) or not allowed or preferred in allowed:
        return preferred
    return allowed[-1]


def _archive_frontmatter(name, stem, date, config, fields):
    lines = ['---', f'title: "{name}"', f'type: {_enum_value(config, "type", "project")}',
             f'status: {_enum_value(config, "status", "archive")}',
             f'ai_priority: {_enum_value(config, "ai_priority", "archive")}',
             'tags: [handoff, archive]', f'related: ["[[{stem}]]"]']
    head = '\n'.join(lines).encode('utf-8') + b'\n'
    extra = [key for key in config.get('required_keys', ()) if key not in OWN_FRONTMATTER_KEYS]
    extra.extend(key for key in COPIED_FRONTMATTER_KEYS if key in fields and key not in extra)
    for key in extra:
        if key not in fields:
            raise StateError('archive_frontmatter_unavailable')
        head += b'\n'.join(fields[key]) + b'\n'
    return head + f'created: {date}\nupdated: {date}\n---\n'.encode('ascii')


def _pack(chosen, raw, header_for, limit):
    """Fill size-bounded notes in order; a section over the limit gets a note of its own."""
    notes = [[]]
    size = len(header_for(1))
    for section in chosen:
        frame = _frame(raw[section.start:section.end], section.sha256)
        if notes[-1] and size + len(frame) > limit:
            notes.append([])
            size = len(header_for(len(notes)))
        notes[-1].append((section, frame))
        size += len(frame)
    return notes


def _stub_bytes(raw, section, folded_at, name, count, summary):
    first = next(_lines(raw, section.start, section.end))
    eol = raw[first[1]:first[2]] or b'\n'
    line = (b'- [handoff-fold] ' + folded_at.encode('ascii') + b' sha256:'
            + section.sha256[:HASH_PREFIX_CHARS].encode('ascii') + b' [[' + name.encode('utf-8')
            + b']] unresolved=' + str(count).encode('ascii'))
    text = raw[section.start:first[1]] + eol + line + eol
    if summary is not None:
        text += b'  - summary: ' + summary.encode('utf-8') + eol
    return text + eol


def _tagged(lines, digest, eol):
    tag = b' [fold-source: sha256:' + digest[:HASH_PREFIX_CHARS].encode('ascii') + b']'
    return b''.join(line + tag + eol for line in lines)


def _collected_edit(raw, sections, first_candidate, lifted):
    """Return one (start, end, bytes) edit that adds the lifted lines."""
    eol = _eol_of(raw, 0)
    existing = next((item for item in sections
                     if item.title == COLLECTED_TITLE and _stub_match(raw, item) is None), None)
    if existing is None:
        heading = ('## ' + COLLECTED_TITLE).encode('utf-8') + eol + eol
        return first_candidate.start, first_candidate.start, heading + lifted + eol
    position = existing.heading_end
    for start, end, next_start in _lines(raw, existing.start, existing.end):
        if raw[start:end].strip():
            position = next_start
    missing_eol = raw[position - 1:position] not in (b'\n', b'\r')
    return position, position, (eol if missing_eol else b'') + lifted


def plan_fold(raw, settings, *, archive_stem, first_archive_number=1, folded_at=PREVIEW_TIME,
              summaries=None, config=None):
    """Compute one fold in memory. This reads and writes no file."""
    config = {} if config is None else config
    summaries = {} if summaries is None else summaries
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise StateError('invalid_encoding') from exc
    _, sections = split_sections(raw)
    stubs = sum(_stub_match(raw, item) is not None for item in sections)
    regular_sections = [item for item in sections
                        if _stub_match(raw, item) is None and item.title != COLLECTED_TITLE]
    fresh = (regular_sections[:settings.protect_sections] if settings.newest == 'top'
             else regular_sections[-settings.protect_sections:])
    fresh_set = set(fresh)
    pinned = {item for item in regular_sections
              if item.title.casefold() in settings.protect_titles
              or BEGIN.encode('ascii') in raw[item.start:item.end]
              or END.encode('ascii') in raw[item.start:item.end]}
    kept = fresh_set | pinned
    protected = tuple(item.title for item in regular_sections if item in kept)
    below = len(raw) < settings.threshold_bytes
    candidates = [] if below else [item for item in regular_sections if item not in kept]
    rows = []
    lifted = {}
    for section in candidates:
        count, lines = _unresolved(raw, section, settings.unresolved_prefixes)
        lifted[section] = (count, lines)
        rows.append({'title': section.title, 'start': section.start, 'end': section.end,
                     'bytes': section.end - section.start, 'sha256': section.sha256,
                     'unresolved_lines': count})
    unresolved = sum(count for count, _ in lifted.values())

    def outcome(status, projected=b'', archives=()):
        return Plan(status, len(sections), stubs, protected, tuple(rows), projected, tuple(archives), unresolved)

    if below:
        return outcome('below_threshold')
    if not candidates:
        return outcome('nothing_to_fold')
    if set(summaries) - {item.sha256 for item in candidates}:
        raise StateError('unknown_summary_section')

    fields = _frontmatter_fields(raw)

    def name_for(number):
        return f'{archive_stem} archive {first_archive_number + number - 1:04d}'

    def header_for(number):
        return _archive_frontmatter(name_for(number), archive_stem, folded_at[:10], config, fields)

    chosen = candidates[::-1] if settings.newest == 'top' else candidates
    archives = []
    note_of = {}
    for number, frames in enumerate(_pack(chosen, raw, header_for, settings.archive_max_bytes), 1):
        name = name_for(number)
        archives.append(ArchiveNote(name, header_for(number) + b''.join(frame for _, frame in frames), len(frames)))
        note_of.update((section, name) for section, _ in frames)

    edits = []
    collected = []
    for section in candidates:
        count, lines = lifted[section]
        edits.append((section.start, section.end, _stub_bytes(
            raw, section, folded_at, note_of[section], count, summaries.get(section.sha256))))
        collected.append(_tagged(lines, section.sha256, _eol_of(raw, 0)))
    if any(collected):
        edits.append(_collected_edit(raw, sections, candidates[0], b''.join(collected)))
    edits.sort(key=lambda edit: (edit[0], edit[1]))
    projected = []
    position = 0
    for start, end, replacement in edits:
        projected.extend((raw[position:start], replacement))
        position = end
    projected.append(raw[position:])
    joined = b''.join(projected)
    if len(joined) >= len(raw):
        return outcome('no_reduction')
    return outcome('ready', joined, archives)


def _settings(config, threshold_bytes, protect_sections, newest, archive_max_bytes):
    fold = config.get('handoff_fold', {})
    if not isinstance(fold, dict) or set(fold) - {'unresolved_prefixes', 'protect_titles'}:
        raise StateError('invalid_fold_config')
    prefixes = fold.get('unresolved_prefixes', list(DEFAULT_UNRESOLVED_PREFIXES))
    titles = fold.get('protect_titles', [])
    for values, least in ((prefixes, 1), (titles, 0)):
        if (not isinstance(values, list) or not least <= len(values) <= MAX_RULE_ITEMS
                or any(not isinstance(value, str) or not value.strip() or len(value) > MAX_RULE_CHARS
                       for value in values)):
            raise StateError('invalid_fold_config')
    return Settings(threshold_bytes, protect_sections, newest, archive_max_bytes,
                    tuple(value.casefold() for value in prefixes),
                    frozenset(value.casefold() for value in titles))


def _check_values(threshold_bytes, protect_sections, newest, archive_max_bytes):
    if threshold_bytes is None:
        raise StateError('fold_threshold_required')
    if protect_sections is None:
        raise StateError('fold_protect_required')
    if type(threshold_bytes) is not int or not 1 <= threshold_bytes <= MAX_SETTING_BYTES:
        raise StateError('invalid_fold_threshold')
    if type(protect_sections) is not int or not 1 <= protect_sections <= MAX_SECTIONS:
        raise StateError('invalid_fold_protect')
    if newest not in ORDERS:
        raise StateError('invalid_fold_order')
    if (type(archive_max_bytes) is not int
            or not MIN_ARCHIVE_MAX_BYTES <= archive_max_bytes <= MAX_SETTING_BYTES):
        raise StateError('invalid_archive_limit')


def _load(vault):
    policy = _Policy(vault)
    relative = policy.config.get('handoff_note')
    if not relative:
        raise StateError('handoff_note_unset')
    return policy, relative


def read_handoff(policy, relative):
    """Read the handoff under an explicit large limit; return (info, bytes)."""
    try:
        info, _, raw = stable_read(lambda: policy.note(relative), MAX_HANDOFF_BYTES, contents=True)
    except FileNotFoundError as exc:
        raise StateError('handoff_missing') from exc
    except EvidenceError as exc:
        code = 'handoff_too_large' if str(exc) == 'input_too_large' else 'unsafe_or_changed_source'
        raise StateError(code) from exc
    except (ValueError, OSError, RuntimeError) as exc:
        raise StateError('unsafe_or_changed_source') from exc
    return info, raw


def _archive_layout(policy, relative):
    """Return (archive folder as a vault path, stem) after checking both."""
    path = Path(relative)
    stem = path.stem
    if any(char in WIKI_UNSAFE for char in stem):
        raise StateError('unsafe_archive_name')
    folder = path.parent.joinpath(ARCHIVE_FOLDER).as_posix()
    policy.note(f'{folder}/{stem} archive 0001.md')
    return folder, stem


def _next_archive_number(policy, folder, stem):
    pattern = re.compile(re.escape(stem.casefold()) + r' archive (\d{4,9})\.md')
    path = policy.vault.joinpath(*folder.split('/'))
    if not path.is_dir():
        return 1
    with os.scandir(path) as entries:
        matches = [pattern.fullmatch(entry.name.casefold()) for entry in entries]
    return max((int(match.group(1)) for match in matches if match is not None), default=0) + 1


def analyze(vault, *, threshold_bytes=None, protect_sections=None, newest='top',
            archive_max_bytes=DEFAULT_ARCHIVE_MAX_BYTES):
    """Report fold candidates and the handoff hash. This writes nothing."""
    _check_values(threshold_bytes, protect_sections, newest, archive_max_bytes)
    policy, relative = _load(vault)
    settings = _settings(policy.config, threshold_bytes, protect_sections, newest, archive_max_bytes)
    info, raw = read_handoff(policy, relative)
    folder, stem = _archive_layout(policy, relative)
    plan = plan_fold(raw, settings, archive_stem=stem, config=policy.config,
                     first_archive_number=_next_archive_number(policy, folder, stem))
    return {'type': 'handoff_fold_analysis', 'authority': 'preview_data_only', 'status': plan.status,
            'path': policy.note(relative).relative_to(policy.root).as_posix(),
            'sha256': info['sha256'], 'bytes': info['size'],
            'threshold_bytes': threshold_bytes, 'protect_sections': protect_sections, 'newest': newest,
            'archive_max_bytes': archive_max_bytes, 'sections_total': plan.sections_total,
            'already_folded': plan.already_folded, 'protected': list(plan.protected),
            'candidates': list(plan.candidates), 'unresolved_lines': plan.unresolved_lines,
            'projected_bytes': len(plan.projected) if plan.status == 'ready' else info['size'],
            'archive_notes': [{'path': f'{folder}/{note.name}.md', 'bytes': len(note.raw), 'sections': note.sections}
                              for note in plan.archives] if plan.status == 'ready' else [],
            'limitations': ['point_in_time_observation', 'summaries_are_derived_and_unverified',
                            'rule_based_unresolved_lines']}


def _check_summaries(summaries):
    if summaries is None:
        return {}
    if not isinstance(summaries, dict) or len(summaries) > MAX_SECTIONS:
        raise StateError('invalid_summary')
    for digest, text in summaries.items():
        _hash(digest, 'invalid_summary')
        if (not isinstance(text, str) or not text.strip() or len(text) > MAX_SUMMARY_CHARS
                or text != text.strip() or any(ord(char) < 32 or char == '\x7f' for char in text)):
            raise StateError('invalid_summary')
    return summaries


def _stamp(folded_at):
    if folded_at is None:
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if not isinstance(folded_at, str) or not _FOLDED_AT.fullmatch(folded_at):
        raise StateError('invalid_folded_at')
    return folded_at


def _write_note(policy, lease, relative, target, data, check):
    """Stage data and replace target under the lease; check runs right before the replace."""
    def replace(temporary):
        with _lease_guard(policy, lease):
            current = policy.fresh()
            _check_lease(current, lease)
            checked = current.note(relative)
            if checked != target:
                raise StateError('unsafe_path')
            check(current, checked)
            regular(temporary.lstat())
            policy.fresh()
            os.replace(temporary, checked)
    _atomic(target, data, replace,
            cleanup_guard=lambda temporary: _cleanup_temporary(policy, relative, temporary))


def _remove_created(paths, folder):
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass
    if folder is not None:
        try:
            folder.rmdir()
        except OSError:
            pass


def apply(vault, *, expected_sha256=None, threshold_bytes=None, protect_sections=None, newest='top',
          archive_max_bytes=DEFAULT_ARCHIVE_MAX_BYTES, summaries=None, folded_at=None):
    """Fold one exact handoff revision; refuses on a hash that is not current.

    summaries is {section sha256: one line of host text}. A summary for a section
    outside the current candidates is refused rather than dropped.
    """
    _check_values(threshold_bytes, protect_sections, newest, archive_max_bytes)
    if expected_sha256 is None:
        raise StateError('expected_sha256_required')
    _hash(expected_sha256)
    summaries = _check_summaries(summaries)
    stamp = _stamp(folded_at)
    policy, relative = _load(vault)
    settings = _settings(policy.config, threshold_bytes, protect_sections, newest, archive_max_bytes)
    target = policy.note(relative)
    canonical = target.relative_to(policy.root).as_posix()
    folder, stem = _archive_layout(policy, canonical)
    with advisory_lock(vault, 'note:' + canonical.casefold(), wait_seconds=1) as lease:
        info, raw = read_handoff(policy, canonical)
        if info['sha256'] != expected_sha256:
            raise StateError('stale_base')
        plan = plan_fold(raw, settings, archive_stem=stem, config=policy.config, folded_at=stamp,
                         summaries=summaries, first_archive_number=_next_archive_number(policy, folder, stem))
        if plan.status != 'ready':
            return {'status': plan.status, 'path': canonical, 'before_sha256': expected_sha256,
                    'after_sha256': expected_sha256, 'folded_sections': 0, 'archive_notes': []}
        rebuilt = restore_handoff(plan.projected, {note.name: note.raw for note in plan.archives}, strict=False)
        if rebuilt != raw:
            raise StateError('restore_mismatch')

        def absent(_current, checked):
            if os.path.lexists(checked):
                raise StateError('archive_exists')

        def unchanged(current, _checked):
            latest, _ = read_handoff(current, canonical)
            if latest['sha256'] != expected_sha256:
                raise StateError('stale_base')

        folder_path = policy.vault.joinpath(*folder.split('/'))
        made_folder = None if folder_path.exists() else folder_path
        created = []
        reported = []
        mode = stat.S_IMODE(target.lstat().st_mode)
        try:
            folder_path.mkdir(exist_ok=True)
            for note in plan.archives:
                path = f'{folder}/{note.name}.md'
                note_target = policy.note(path)
                _write_note(policy, lease, path, note_target, note.raw, absent)
                created.append(note_target)
                os.chmod(note_target, mode)
                reported.append({'path': path, 'sha256': _sha(note.raw), 'bytes': len(note.raw),
                                 'sections': note.sections})
            _write_note(policy, lease, canonical, target, plan.projected, unchanged)
        except BaseException:
            _remove_created(created, made_folder)
            raise
        return {'status': 'applied', 'path': canonical, 'before_sha256': expected_sha256,
                'after_sha256': _sha(plan.projected), 'bytes_before': len(raw),
                'bytes_after': len(plan.projected), 'folded_sections': len(plan.candidates),
                'protected_sections': len(plan.protected), 'unresolved_lines_lifted': plan.unresolved_lines,
                'folded_at': stamp, 'archive_notes': reported}


def _read_summaries(vault, relative):
    """Read {schema_version, summaries} from an explicit JSON file inside the vault."""
    policy = _Policy(vault)

    def resolve():
        path = resolve_note_path(policy.vault, relative, policy.rules)
        if path.suffix.casefold() != '.json':
            raise ValueError('invalid_summary_path')
        return path
    try:
        _, _, raw = stable_read(resolve, MAX_SUMMARY_FILE_BYTES, contents=True)
        value = decode(raw)
    except (EvidenceError, ValueError, OSError, RuntimeError) as exc:
        raise StateError('invalid_summary') from exc
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'summaries'}
            or value['schema_version'] != 1):
        raise StateError('invalid_summary')
    return value['summaries']


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StateError('invalid_arguments')


def _parser():
    parser = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                     allow_abbrev=False)
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('analyze', 'apply'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('--threshold-bytes', type=int)
        command.add_argument('--protect-sections', type=int)
        command.add_argument('--newest', choices=ORDERS, default='top')
        command.add_argument('--archive-max-bytes', type=int, default=DEFAULT_ARCHIVE_MAX_BYTES)
        if name == 'apply':
            command.add_argument('--expected-sha256')
            command.add_argument('--summaries', help='vault-relative JSON file of host summaries')
            command.add_argument('--folded-at', help='UTC time as YYYY-MM-DDTHH:MM:SSZ; defaults to now')
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        settings = {'threshold_bytes': args.threshold_bytes, 'protect_sections': args.protect_sections,
                    'newest': args.newest, 'archive_max_bytes': args.archive_max_bytes}
        if args.command == 'analyze':
            result = analyze(args.vault, **settings)
        else:
            summaries = None if args.summaries is None else _read_summaries(args.vault, args.summaries)
            result = apply(args.vault, expected_sha256=args.expected_sha256, summaries=summaries,
                           folded_at=args.folded_at, **settings)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except StateError as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2 if str(exc) == 'invalid_arguments' else 1
    except (EvidenceError, HealthcheckError, ValueError, OSError, UnicodeError, RecursionError):
        print('{"error":"handoff_fold_unavailable"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
