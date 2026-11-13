#!/usr/bin/env python3
"""Persist a minimal local checkpoint for PreCompact/SessionEnd (or Antigravity CLI's Stop), without models."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from vault_evidence import EvidenceError, decode
from vault_healthcheck import HealthcheckError
from vault_state import CONFIG_PATH, EVENTS, StateError, _pending, write_checkpoint

MAX_EVENT_BYTES = 64 * 1024
FAILURE_CODE_RE = re.compile(r'[a-z][a-z0-9_]{0,63}')
# Antigravity CLI (--host antigravity) records its fully idle Stop. The id bound
# is write_checkpoint's session id bound.
STOP_EVENT = 'Stop'
MAX_CONVERSATION_ID_CHARS = 128
# Without --event the event name comes from stdin, and only the events v0.47.0
# took are accepted: a Stop needs --event Stop or --host antigravity.
STDIN_EVENTS = EVENTS - {STOP_EVENT}


def read_hook_event(stream=None, *, expected_event=None):
    """Read bounded host JSON, ignore transcripts and all unrelated fields.

    Without expected_event the input must name PreCompact or SessionEnd
    (STDIN_EVENTS); Stop is accepted only as the expected event.
    """
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
    allowed = STDIN_EVENTS if expected_event is None else EVENTS
    if (not isinstance(event, str) or event not in allowed
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


def failure_code(error):
    """Name a failure by its fixed code; other exception text may quote a path or body."""
    code = str(error) if isinstance(error, (StateError, EvidenceError)) else ''
    return code if FAILURE_CODE_RE.fullmatch(code) else 'unclassified_failure'


def _antigravity_helpers():
    """Import the Antigravity CLI helpers that ship in this hook's folder."""
    folder = str(Path(__file__).resolve().parent)
    if folder not in sys.path:
        sys.path.append(folder)
    import antigravity_hook

    return antigravity_hook


def _discard_output(stream):
    """Point the file descriptor behind stream at the null device; best effort.

    A copy of antigravity_hook._discard_output for the answer written when
    that module cannot be imported.
    """
    try:
        descriptor = stream.fileno()
        null = os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError, ValueError):
        return
    if null == descriptor:  # the descriptor had been closed and now names the null device
        return
    try:
        os.dup2(null, descriptor)
    except OSError:
        pass
    finally:
        try:
            os.close(null)
        except OSError:
            pass


def _write_empty_object():
    """Answer {} when the Antigravity CLI helpers cannot be imported.

    As in antigravity_hook.emit, a host that has stopped reading makes the
    write fail; the failure is dropped and stdout is pointed at the null
    device, so the flush at exit cannot fail again and no traceback names
    the plugin path.
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        buffer = getattr(stream, 'buffer', None)
        if buffer is None:
            stream.write('{}\n')
            stream.flush()
            return
        stream.flush()
        buffer.write(b'{}\n')
        buffer.flush()
    except (OSError, ValueError):
        _discard_output(stream)


def record_antigravity_stop(helpers, explicit_vault=None):
    """Record a Stop checkpoint for a fully idle Antigravity CLI conversation.

    Only an object event whose hookEventName is absent or Stop, whose
    fullyIdle is true and whose conversationId is a valid id counts, in a
    vault resolved as for SessionStart. The record keeps the SHA-256 of the
    conversation id, never the user input, the transcript path or a workspace
    path (vault-relative note paths stay, as in Claude Code checkpoints). The
    package runs several handlers per event; with an integer executionNum the
    claim lets one of them write, and the same conversation and executionNum
    again within 120 seconds writes nothing.
    """
    event = helpers.read_event()
    if (not isinstance(event, dict) or event.get('hookEventName', STOP_EVENT) != STOP_EVENT
            or event.get('fullyIdle') is not True):
        return
    conversation = helpers.valid_id(event.get('conversationId'), MAX_CONVERSATION_ID_CHARS)
    if conversation is None:
        return
    vault, _reason = helpers.resolve_vault(explicit_vault, event)
    if vault is None:
        return
    execution = event.get('executionNum')
    if type(execution) is int and not helpers.claim_once(STOP_EVENT, conversation, str(execution)):
        return
    try:
        write_checkpoint(vault, STOP_EVENT, session_id=conversation, pending_references=None)
    except (StateError, EvidenceError, HealthcheckError, ValueError, OSError, UnicodeError, RuntimeError) as error:
        print(f'agentic-vault: checkpoint unavailable ({failure_code(error)})', file=sys.stderr)


def antigravity_main(explicit_vault=None):
    """Antigravity CLI Stop hook: always {} on stdout and exit 0."""
    try:
        helpers = _antigravity_helpers()
    except Exception:
        print('agentic-vault: checkpoint unavailable (helpers_unavailable)', file=sys.stderr)
        _write_empty_object()
        return 0
    try:
        record_antigravity_stop(helpers, explicit_vault)
    except Exception as error:
        print(f'agentic-vault: checkpoint unavailable ({failure_code(error)})', file=sys.stderr)
    helpers.emit({})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path)
    parser.add_argument('--event', choices=sorted(EVENTS))
    parser.add_argument('--host', choices=('claude', 'antigravity'), default='claude',
                        help='claude (default; Claude Code and Codex events) or antigravity '
                             '(Antigravity CLI Stop event; answers {})')
    args = parser.parse_args(argv)
    if args.host == 'antigravity':
        return antigravity_main(args.vault)
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
    except (StateError, EvidenceError, HealthcheckError, ValueError, OSError, UnicodeError, RuntimeError) as error:
        print(f'agentic-vault: checkpoint unavailable ({failure_code(error)})', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
