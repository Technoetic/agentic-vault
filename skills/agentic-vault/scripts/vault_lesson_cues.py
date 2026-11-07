#!/usr/bin/env python3
"""Compile lesson cues from a ledger into one JSON file for the optional hook.

The ledger keeps cues in each row's lesson marker (vault_lesson_delta.py). The
hook runs alone under python -I, so it cannot parse the ledger; it reads the
list this tool compiles. Only rows that are in force are compiled: retired,
withdrawn, rejected, rolled-back and candidate rows are left out, and so is any
status this tool does not know. `build` is a dry run and prints the result.
`build --write` replaces only the fixed output file. Neither changes the ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import vault_lesson_delta as delta
import vault_proposals as proposals
import vault_state as state

AUTHORITY = 'observation_data_only'
DEFAULT_LEDGER = '00-meta/lessons.md'
OUTPUT_REL = '00-meta/lesson-cues.json'
# Ledger status cells that mean the lesson stands. Matched at the start of the
# cell because a promoted row carries its target and anchor after the word.
IN_FORCE = ('관찰중', '승격제안', '승격완료', 'active')
STATUS_LABEL = '상태:'
CUE_FIELDS = ('kind', 'text', 'tools', *delta.CUE_MATCHERS)


class LessonCueError(Exception):
    """A content-free operational error code."""


def _status(record):
    """Return in_force, retired or candidate for one ledger record."""
    if record['state'] != 'active':
        return 'retired'
    if record['kind'] != 'bullet':
        return 'in_force'  # a heading record has no status cell; its marker is the state
    cell = record['cells'][4][len(STATUS_LABEL):].strip()
    return 'in_force' if cell.startswith(IN_FORCE) else 'skipped_status'


def _build(vault, ledger):
    policy = state._Policy(vault)
    ledger = delta._safe_note(policy, ledger)
    info, _, raw = policy.read_note(ledger)
    records, _, _, ignored = delta._inventory(raw, 'lesson:' + ledger.casefold())
    cues, retired, skipped = [], 0, 0
    for record in records:
        if not record.get('cues'):
            continue
        status = _status(record)
        if status == 'retired':
            retired += 1
        elif status == 'skipped_status':
            skipped += 1
        else:
            for cue in record['cues']:
                cues.append(dict(id=record['id'], count=record['count'],
                                 **{name: cue[name] for name in CUE_FIELDS if name in cue}))
    cues.sort(key=lambda cue: (cue['kind'] != 'guard', -cue['count'], cue['id'], cue['text']))
    return dict(document=dict(version=1, source=ledger, source_sha256=info['sha256'], cues=cues),
                authority=AUTHORITY, output=OUTPUT_REL, compiled=len(cues),
                skipped=dict(retired=retired, status=skipped), ignored_rows=len(ignored))


def build_cues(vault, ledger=DEFAULT_LEDGER):
    """Return the compiled document and what was left out. Reads only."""
    return _guarded(lambda: _build(vault, ledger))


def _write(vault, ledger):
    result = _build(vault, ledger)
    policy = state._Policy(vault)
    path = policy.checked(OUTPUT_REL)
    data = json.dumps(result['document'], ensure_ascii=False, sort_keys=True, indent=1,
                      allow_nan=False).encode('utf-8') + b'\n'
    proposals.atomic_write(path, data)
    return dict(result, written=True)


def write_cues(vault, ledger=DEFAULT_LEDGER):
    """Compile and replace the fixed output file. An explicit host action."""
    return _guarded(lambda: _write(vault, ledger))


def _guarded(callback):
    try:
        return callback()
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
