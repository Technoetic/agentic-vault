#!/usr/bin/env python3
"""Reviewable, revision-bound partial changes to Markdown lesson records.

References are explicit Markdown source bindings plus optional observation and
receipt identifiers. Identifiers link host reports; they do not verify outcomes,
identify an approver, or authorize a promotion. Proposals and returned receipts
are local accounting data, not signatures. Prepare is read-only. Apply uses the
same note lock as P3 and changes only selected record lines/metadata.
"""
from __future__ import annotations

import argparse
from datetime import date
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys

import vault_evidence as evidence
import vault_proposals as proposals
import vault_state as state

MAX_INPUT_BYTES = 512 * 1024
MAX_OPERATIONS = 64
MAX_TEXT_BYTES = 4096
MAX_REFERENCES = 8
MAX_COUNT = 1000000
META = 'agentic-vault:lesson'
MARKER = re.compile(r'\s*<!-- agentic-vault:lesson (\{.*\}) -->\s*$')
HEADING = re.compile(r'^(#{2,6}\s+)(' + state.ITEM_ID_RE.pattern + r')(\s+(?:—|–|-)\s+)(.+)$')
EXPLICIT = re.compile(r'^\[(' + state.ITEM_ID_RE.pattern + r')\]\s+')
HASH = re.compile(r'[0-9a-f]{64}')
REF_ID = re.compile(r'[0-9a-f]{32}')
AUTHORITY = 'observation_data_only'


class LessonDeltaError(Exception):
    """A content-free operational error code."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(value):
    if (not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > MAX_TEXT_BYTES
            or any(ord(c) < 32 for c in value) or '<!--' in value or '-->' in value
            or ' | ' in value):
        raise LessonDeltaError('invalid_lesson_text')
    return value


def _references(value):
    if not isinstance(value, list) or len(value) > MAX_REFERENCES:
        raise LessonDeltaError('invalid_references')
    result = []
    for ref in value:
        if (not isinstance(ref, dict) or not {'path', 'sha256'} <= set(ref)
                or set(ref) - {'path', 'sha256', 'observation_id', 'receipt_id'}):
            raise LessonDeltaError('invalid_reference')
        if not isinstance(ref['path'], str) or len(ref['path']) > 1024:
            raise LessonDeltaError('invalid_reference')
        if not isinstance(ref['sha256'], str) or not HASH.fullmatch(ref['sha256']):
            raise LessonDeltaError('invalid_reference_hash')
        for name in ('observation_id', 'receipt_id'):
            if name in ref and (not isinstance(ref[name], str) or not REF_ID.fullmatch(ref[name])):
                raise LessonDeltaError('invalid_reference_id')
        if ref not in result:
            result.append(dict(ref))
    return result


def _metadata(text):
    match = MARKER.search(text)
    if not match:
        if META in text:
            raise LessonDeltaError('malformed_lesson_metadata')
        return text, None
    data = evidence.decode(match[1].encode())
    if (not isinstance(data, dict) or set(data) != {'id', 'count', 'state', 'references'}
            or not isinstance(data['id'], str) or not state.ITEM_ID_RE.fullmatch(data['id'])
            or type(data['count']) is not int or not 1 <= data['count'] <= MAX_COUNT
            or data['state'] not in ('active', 'retired')):
        raise LessonDeltaError('invalid_lesson_metadata')
    data['references'] = _references(data['references'])
    return text[:match.start()], data


def _marker(record):
    data = {k: record[k] for k in ('id', 'count', 'state', 'references')}
    return '<!-- ' + META + ' ' + encode(data).decode('utf-8') + ' -->'


def _lines(raw):
    offset, fence = 0, ''
    for number, line in enumerate(raw.splitlines(keepends=True)):
        start, offset = offset, offset + len(line)
        text = line.decode('utf-8').rstrip('\r\n')
        ending = line[len(text.encode('utf-8')):].decode('ascii')
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', text)
        visible = not fence
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ''
            visible = False
        elif match and (match[1][0] != '`' or '`' not in match[2]):
            fence, visible = match[1], False
        yield dict(number=number, start=start, end=offset - len(ending), text=text,
                   ending=ending, visible=visible)


def _inventory(raw, namespace):
    rows, registry, entries = list(_lines(raw)), {}, []
    for index, line in enumerate(rows):
        if not line['visible']:
            continue
        text, metadata = _metadata(line['text']) if META in line['text'] and not line['text'].lstrip().startswith('<!--') else (line['text'], None)
        heading = HEADING.fullmatch(text)
        cells = text.split(' | ')
        bullet = (len(cells) == 5 and re.fullmatch(r'- \d{4}-\d{2}-\d{2}', cells[0])
                  and re.fullmatch(r'횟수 \d+', cells[1]) and cells[3].startswith('근거:')
                  and cells[4].startswith('상태:'))
        if not heading and not bullet:
            continue
        if heading:
            identifier, lesson = heading[2], heading[4]
            next_line = rows[index + 1] if index + 1 < len(rows) else None
            meta_line = None
            if next_line and next_line['visible'] and next_line['text'].startswith('<!-- ' + META):
                _, metadata = _metadata(next_line['text'])
                meta_line = next_line
            record = dict(kind='heading', line=line, heading_prefix=''.join(heading.groups()[:3]),
                          meta_line=meta_line, text=lesson, count=1, state='active', references=[])
        else:
            explicit = EXPLICIT.match(cells[2])
            lesson = cells[2][explicit.end():] if explicit else cells[2]
            identifier = explicit[1] if explicit else None
            record = dict(kind='bullet', line=line, cells=cells, explicit=explicit[0] if explicit else '',
                          text=lesson, count=int(cells[1][3:]), state='active', references=[])
            if not 1 <= record['count'] <= MAX_COUNT:
                raise LessonDeltaError('invalid_lesson_counter')
        if metadata:
            if identifier is not None and identifier != metadata['id']:
                raise LessonDeltaError('lesson_id_mismatch')
            if bullet and record['count'] != metadata['count']:
                raise LessonDeltaError('lesson_counter_mismatch')
            identifier = metadata['id']
            record.update(metadata)
        _text(record['text'])
        if identifier is None:
            identifier = state.stable_item_id(namespace, record['text'], registry=registry)
        record['id'] = identifier
        if any(existing['id'] == identifier for existing in entries):
            raise LessonDeltaError('duplicate_lesson_id')
        entries.append(record)
        if len(entries) > 256:
            raise LessonDeltaError('lesson_count_limit')
    return entries, rows, registry


def _operations(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_OPERATIONS:
        raise LessonDeltaError('invalid_operations')
    if len(encode(value)) > MAX_INPUT_BYTES // 2:
        raise LessonDeltaError('operations_too_large')
    result = []
    for op in value:
        if not isinstance(op, dict) or op.get('op') not in ('ADD', 'INC', 'EDIT', 'RETIRE'):
            raise LessonDeltaError('invalid_operation')
        allowed = {'ADD': {'op', 'text', 'date', 'references'},
                   'INC': {'op', 'id', 'amount', 'references'},
                   'EDIT': {'op', 'id', 'text', 'references'},
                   'RETIRE': {'op', 'id', 'references'}}[op['op']]
        if set(op) - allowed:
            raise LessonDeltaError('invalid_operation_fields')
        normalized = dict(op)
        if op['op'] == 'ADD':
            if not isinstance(op.get('date'), str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', op['date']):
                raise LessonDeltaError('explicit_date_required')
            try:
                date.fromisoformat(op['date'])
            except ValueError as exc:
                raise LessonDeltaError('invalid_date') from exc
        else:
            if not isinstance(op.get('id'), str) or not state.ITEM_ID_RE.fullmatch(op['id']):
                raise LessonDeltaError('invalid_lesson_id')
        if op['op'] in ('ADD', 'EDIT'):
            normalized['text'] = _text(op.get('text'))
        if op['op'] == 'INC':
            amount = op.get('amount', 1)
            if type(amount) is not int or not 1 <= amount <= 1000:
                raise LessonDeltaError('invalid_increment')
            normalized['amount'] = amount
        normalized['references'] = _references(op.get('references', []))
        result.append(normalized)
    return result


def _safe_note(policy, path):
    result = policy.note(path)
    relative = result.relative_to(policy.root).as_posix()
    if relative.casefold().startswith(('00-meta/proposals/', '00-meta/evidence/')):
        raise LessonDeltaError('reserved_store_path')
    return relative


def _prepare(vault, path, operations):
    policy = state._Policy(vault)
    path = _safe_note(policy, path)
    info, _, original = policy.read_note(path)
    namespace = 'lesson:' + path.casefold()
    inventory, lines, registry = _inventory(original, namespace)
    operations = _operations(operations)
    records = {r['id']: dict(r) for r in inventory}
    changed, added, changes, references = {}, [], [], []
    for op in operations:
        if op['op'] == 'ADD':
            if any(' '.join(op['text'].split()) == ' '.join(r['text'].split()) for r in records.values()):
                raise LessonDeltaError('duplicate_lesson_text')
            identifier = state.stable_item_id(namespace, op['text'], registry=registry)
            if identifier in records:
                raise LessonDeltaError('id_collision')
            record = dict(id=identifier, text=op['text'], date=op['date'], count=1, state='active',
                          references=[], kind='new')
            records[identifier], added = record, added + [record]
            if len(records) > 256:
                raise LessonDeltaError('lesson_count_limit')
        else:
            identifier = op['id']
            if identifier not in records:
                raise LessonDeltaError('unknown_lesson_id')
            record = records[identifier]
            if record['state'] == 'retired' and op['op'] != 'RETIRE':
                raise LessonDeltaError('retired_lesson')
            if op['op'] == 'INC':
                record['count'] += op['amount']
                if record['count'] > MAX_COUNT:
                    raise LessonDeltaError('lesson_counter_limit')
            elif op['op'] == 'EDIT':
                record['text'] = op['text']
            else:
                record['state'] = 'retired'
        record['references'] = _references(record['references'] + op['references'])
        changed[identifier] = record
        changes.append({'op': op['op'], 'id': identifier, 'count': record['count'], 'state': record['state']})
        for ref in op['references']:
            canonical = _safe_note(policy, ref['path'])
            refinfo, _, _ = policy.read_note(canonical)
            if refinfo['sha256'] != ref['sha256']:
                raise LessonDeltaError('reference_changed')
            bound = {**ref, 'path': canonical}
            if bound not in references:
                references.append(bound)
    # Previously linked sources are preserved as history. Only newly submitted
    # references are current inputs and bind this operation; no outcome is inferred.
    edits = []
    newline = next((line['ending'] for line in lines if line['ending']), '\n')
    for record in changed.values():
        if record['kind'] == 'new':
            continue
        line = record['line']
        if record['kind'] == 'bullet':
            cells = list(record['cells'])
            cells[1] = '횟수 ' + str(record['count'])
            cells[2] = record['explicit'] + record['text']
            if record['state'] == 'retired':
                cells[4] = '상태: retired'
            replacement = ' | '.join(cells) + ' ' + _marker(record)
            edits.append(dict(start=line['start'], end=line['end'], replacement=replacement))
        else:
            title = record['heading_prefix'] + record['text']
            if title != line['text']:
                edits.append(dict(start=line['start'], end=line['end'], replacement=title))
            meta = record['meta_line']
            if meta:
                edits.append(dict(start=meta['start'], end=meta['end'], replacement=_marker(record)))
            else:
                insert = line['end'] + len(line['ending'])
                prefix = '' if line['ending'] else newline
                edits.append(dict(start=insert, end=insert, replacement=prefix + _marker(record) + newline))
    if added:
        headers = [line for line in lines if line['visible'] and line['text'] in ('## 대장', '## Lessons')]
        if len(headers) != 1:
            raise LessonDeltaError('unique_lesson_ledger_required')
        header = headers[0]
        offset = header['end'] + len(header['ending'])
        prefix = '' if header['ending'] else newline
        text = ''.join('- ' + r['date'] + ' | 횟수 ' + str(r['count']) + ' | ' + r['text']
                       + ' | 근거: host-provided references | 상태: ' + r['state'] + ' '
                       + _marker(r) + newline for r in added)
        edits.append(dict(start=offset, end=offset, replacement=prefix + text))
    edits.sort(key=lambda edit: (edit['start'], edit['end']))
    # A new row can be inserted exactly where the first existing row is edited.
    # Combine those edits at that byte offset instead of issuing overlap-prone
    # edits or rewriting the ledger section.
    merged = []
    for edit in edits:
        if merged and merged[-1]['start'] == edit['start']:
            previous = merged[-1]
            if previous['end'] != previous['start']:
                raise LessonDeltaError('overlapping_record_edits')
            previous['end'] = edit['end']
            previous['replacement'] += edit['replacement']
        else:
            merged.append(dict(edit))
    edits = merged
    candidate, _ = state._edited(original, edits)
    # Delta syntax does not grant ownership of engine-generated rules or blocks.
    # Reuse the proposal engine's pure marker checks without normalizing or
    # requiring a schema migration of otherwise untouched legacy frontmatter.
    before, after = original.decode('utf-8'), candidate.decode('utf-8')
    rule_dirs = {'.claude/rules', policy.config.get('rules_dir', '').casefold()}
    target = policy.note(path)
    if (proposals.ENGINE_RE.search(before) or proposals.ENGINE_RE.search(after)
            or (target.name.casefold().startswith('vault-')
                and any(directory and path.casefold().startswith(directory + '/') for directory in rule_dirs))):
        raise LessonDeltaError('engine_owned')
    if proposals.managed_blocks(before) != proposals.managed_blocks(after):
        raise LessonDeltaError('managed_block_changed')
    if candidate == original:
        raise LessonDeltaError('no_change')
    # Rehash all operation inputs and configuration after processing. P3 rechecks
    # newly linked sources and the exact config again immediately before replace.
    for ref in references:
        current, _, _ = policy.read_note(ref['path'])
        if current['sha256'] != ref['sha256']:
            raise LessonDeltaError('reference_changed')
    current, _, _ = policy.read_note(path)
    if current['sha256'] != info['sha256']:
        raise LessonDeltaError('stale_base')
    policy.fresh()
    result = dict(version=1, target=path, base_sha256=info['sha256'],
                  config_sha256=policy.config_sha256, operations=operations, edits=edits,
                  candidate_sha256=_sha(candidate), changes=changes, references=references,
                  inventory=[{k: r[k] for k in ('id', 'text', 'count', 'state')} for r in inventory],
                  authority=AUTHORITY,
                  diff=''.join(difflib.unified_diff(original.decode().splitlines(keepends=True),
                                                   candidate.decode().splitlines(keepends=True),
                                                   fromfile=path + ' (base)', tofile=path + ' (candidate)')))
    result['id'] = _sha(encode(result))[:32]
    result['proposal_sha256'] = _sha(encode(result))
    if len(encode(result)) > MAX_INPUT_BYTES:
        raise LessonDeltaError('proposal_too_large')
    return result


def _safe(callback):
    try:
        return callback()
    except LessonDeltaError:
        raise
    except (state.StateError, proposals.ProposalError) as exc:
        raise LessonDeltaError(str(exc)) from exc
    except (evidence.EvidenceError, OSError, ValueError, TypeError, KeyError, UnicodeError,
            RecursionError, OverflowError) as exc:
        raise LessonDeltaError('invalid_or_unavailable_input') from exc


def prepare_delta(vault, path, operations):
    """Read-only dry run; return exact byte edits, hashes and a reviewable diff."""
    return _safe(lambda: _prepare(vault, path, operations))


def apply_delta(vault, proposal, *, approve=False):
    """Recompute the exact host-selected operations, then apply an approved delta."""
    if approve is not True:
        raise LessonDeltaError('explicit_approval_required')
    def apply():
        if not isinstance(proposal, dict) or len(encode(proposal)) > MAX_INPUT_BYTES:
            raise LessonDeltaError('invalid_proposal')
        if proposal.get('proposal_sha256') != _sha(encode({k: v for k, v in proposal.items() if k != 'proposal_sha256'})):
            raise LessonDeltaError('proposal_integrity_mismatch')
        current = _prepare(vault, proposal.get('target'), proposal.get('operations'))
        if current != proposal:
            raise LessonDeltaError('proposal_changed_or_stale')
        source_hashes = {}
        for ref in current['references']:
            if ref['path'] in source_hashes and source_hashes[ref['path']] != ref['sha256']:
                raise LessonDeltaError('conflicting_reference_hash')
            source_hashes[ref['path']] = ref['sha256']
        result = state.patch_note(vault, current['target'], current['base_sha256'], current['edits'],
                                  source_hashes=source_hashes,
                                  expected_config_sha256=current['config_sha256'])
        return dict(result, proposal_id=current['id'], proposal_sha256=current['proposal_sha256'],
                    explicit_approval=True, changes=current['changes'], references=current['references'],
                    authority=AUTHORITY)
    return _safe(apply)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--path', required=True)
    apply = commands.add_parser('apply')
    apply.add_argument('--approve', action='store_true')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise LessonDeltaError('input_too_large')
        payload = evidence.decode(raw)
        result = (prepare_delta(args.vault, args.path, payload) if args.command == 'prepare'
                  else apply_delta(args.vault, payload, approve=args.approve))
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
        return 0
    except (LessonDeltaError, evidence.EvidenceError, OSError, ValueError, RecursionError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, LessonDeltaError) else 'invalid_or_unavailable_input'
        print(encode({'ok': False, 'error': code}).decode('utf-8'))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
