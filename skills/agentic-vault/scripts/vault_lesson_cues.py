#!/usr/bin/env python3
"""Compile lesson cues from a ledger into one JSON file for the optional hook.

The ledger keeps cues in each row's lesson marker (vault_lesson_delta.py). The
hook runs alone under python -I, so it cannot parse the ledger; it reads the
list this tool compiles. Only rows that are in force are compiled. A status cell
counts when the whole cell is a known in-force form, so retired, withdrawn,
rejected, rolled-back and candidate rows are left out, and so is any status this
tool does not know. Rows inside an HTML comment block or the rejected-drafts
section are left out too. `build` is a dry run and prints the result.
`build --write` replaces only the fixed output file. Neither changes the ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import vault_lesson_delta as delta
import vault_proposals as proposals
import vault_state as state

AUTHORITY = 'observation_data_only'
DEFAULT_LEDGER = '00-meta/lessons.md'
OUTPUT_REL = '00-meta/lesson-cues.json'
# The hook reads no more than this (MAX_FILE_BYTES in lesson-cues.py).
MAX_OUTPUT_BYTES = 256 * 1024
# Ledger status cells that mean the lesson stands. The whole cell has to match,
# so a word that only starts like one of these (관찰중단, 승격제안 기각) stays out.
# A promoted row names its target and anchor inside the brackets, and text there
# can still withdraw it, so a withdrawing word anywhere leaves the row out.
IN_FORCE = re.compile(r'관찰중|승격제안|active|승격완료\((?:검증중)?→[^()]+\)')
WITHDRAWN = re.compile(r'철회|기각|중단|취소|롤백|retired|withdrawn|rejected|rolled', re.IGNORECASE)
STATUS_LABEL = '상태:'
# Drafts kept for the record, not lessons in force (assets/templates/lessons.md).
SECTION = re.compile(r'##\s')
REJECTED_SECTION = re.compile(r'##\s+기각 대장(?:\s|$)')
CUE_FIELDS = ('kind', 'text', 'tools', *delta.CUE_MATCHERS)


class LessonCueError(Exception):
    """A content-free operational error code."""


def _rejected_lines(rows):
    """Line numbers under the rejected-drafts heading, up to the next plain section.

    A lesson heading does not end the section: it may be one of the drafts.
    """
    numbers, inside = set(), False
    for line in rows:
        text = line['text']
        if line['visible'] and not line['comment'] and SECTION.match(text) and not delta.HEADING.fullmatch(text):
            inside = REJECTED_SECTION.match(text) is not None
        if inside:
            numbers.add(line['number'])
    return numbers


def _status(record, rejected):
    """Return in_force, or the skipped key that says why the record stays out."""
    if record['line']['comment']:
        return 'comment'
    if record['line']['number'] in rejected:
        return 'rejected'
    if record['state'] != 'active':
        return 'retired'
    if record['kind'] != 'bullet':
        return 'in_force'  # a heading record has no status cell; its marker is the state
    cell = record['cells'][4][len(STATUS_LABEL):].strip()
    return 'in_force' if IN_FORCE.fullmatch(cell) and not WITHDRAWN.search(cell) else 'status'


def _build(vault, ledger):
    policy = state._Policy(vault)
    ledger = delta._safe_note(policy, ledger)
    info, _, raw = policy.read_note(ledger)
    records, rows, _, ignored = delta._inventory(raw, 'lesson:' + ledger.casefold())
    rejected = _rejected_lines(rows)
    cues, skipped = [], dict(retired=0, status=0, comment=0, rejected=0)
    for record in records:
        if not record.get('cues'):
            continue
        status = _status(record, rejected)
        if status != 'in_force':
            skipped[status] += 1
            continue
        for cue in record['cues']:
            cues.append(dict(id=record['id'], count=record['count'],
                             **{name: cue[name] for name in CUE_FIELDS if name in cue}))
    cues.sort(key=lambda cue: (cue['kind'] != 'guard', -cue['count'], cue['id'], cue['text']))
    return dict(document=dict(version=1, source=ledger, source_sha256=info['sha256'], cues=cues),
                authority=AUTHORITY, output=OUTPUT_REL, compiled=len(cues),
                skipped=skipped, ignored_rows=len(ignored))


def build_cues(vault, ledger=DEFAULT_LEDGER):
    """Return the compiled document and what was left out. Reads only."""
    return _guarded(lambda: _build(vault, ledger))


def _write(vault, ledger):
    result = _build(vault, ledger)
    policy = state._Policy(vault)
    path = policy.checked(OUTPUT_REL)
    data = json.dumps(result['document'], ensure_ascii=False, sort_keys=True, indent=1,
                      allow_nan=False).encode('utf-8') + b'\n'
    if len(data) > MAX_OUTPUT_BYTES:
        raise LessonCueError('lesson_cues_too_large')
    proposals.atomic_write(path, data)
    return dict(result, written=True)


def write_cues(vault, ledger=DEFAULT_LEDGER):
    """Compile and replace the fixed output file. An explicit host action."""
    return _guarded(lambda: _write(vault, ledger))


def _guarded(callback):
    try:
        return callback()
    except LessonCueError:
        raise
    except (delta.LessonDeltaError, state.StateError, proposals.ProposalError) as exc:
        raise LessonCueError(str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as exc:
        raise LessonCueError('invalid_or_unavailable_input') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build')
    build.add_argument('--ledger', default=DEFAULT_LEDGER)
    build.add_argument('--write', action='store_true', help='Replace ' + OUTPUT_REL + '.')
    args = parser.parse_args(argv)
    try:
        result = (write_cues if args.write else build_cues)(args.vault, args.ledger)
    except LessonCueError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
