#!/usr/bin/env python3
"""Persist a minimal local checkpoint for PreCompact/SessionEnd, without models."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vault_evidence import EvidenceError, decode
from vault_healthcheck import HealthcheckError
from vault_state import CONFIG_PATH, EVENTS, StateError, _pending, write_checkpoint

MAX_EVENT_BYTES = 64 * 1024


def read_hook_event(stream=None, *, expected_event=None):
    """Read bounded host JSON, ignore transcripts and all unrelated fields."""
    stream = sys.stdin if stream is None else stream
    if stream is None or stream.isatty():
        raise StateError('invalid_checkpoint_event')
    binary = getattr(stream, 'buffer', stream)
    raw = binary.read(MAX_EVENT_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if len(raw) > MAX_EVENT_BYTES:
        raise StateError('invalid_checkpoint_event')
    try:
        data = decode(raw)
    except EvidenceError as exc:
        raise StateError('invalid_checkpoint_event') from exc
    if not isinstance(data, dict):
        raise StateError('invalid_checkpoint_event')
    event = data.get('hook_event_name', expected_event)
    if (not isinstance(event, str) or event not in EVENTS
            or (expected_event is not None and event != expected_event)):
        raise StateError('invalid_checkpoint_event')
    session_id = data.get('session_id')
    if session_id is not None and (not isinstance(session_id, str) or not session_id or len(session_id) > 128):
        raise StateError('invalid_checkpoint_event')
    references = data.get('pending_references')
    try:
        if references is not None:
            references = _pending(references)
    except StateError as exc:
        raise StateError('invalid_checkpoint_event') from exc
    return dict(event=event, session_id=session_id, pending_references=references)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path)
    parser.add_argument('--event', choices=sorted(EVENTS))
    args = parser.parse_args(argv)
    project = os.environ.get('CLAUDE_PROJECT_DIR', '').strip()
    vault = args.vault or (Path(project) if project else Path.cwd())
    # Non-vault invocations neither consume stdin nor produce diagnostics.
    if not (vault / CONFIG_PATH).is_file():
        return 0
    try:
        event = read_hook_event(expected_event=args.event)
    except (StateError, ValueError, OSError, UnicodeError, RecursionError):
        print('agentic-vault: invalid checkpoint event', file=sys.stderr)
        return 0
    try:
        write_checkpoint(vault, event['event'], session_id=event['session_id'],
                         pending_references=event['pending_references'])
    except (StateError, EvidenceError, HealthcheckError, ValueError, OSError, UnicodeError, RuntimeError):
        print('agentic-vault: checkpoint unavailable', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
