#!/usr/bin/env python3
"""Offline loss alarm for a folded handoff: section preservation and one-hop probes.

Each folded section is compared, with check_preservation, against what its stub
reaches by following its wiki link once. Five kinds of probe are then placed on the
shortest route that holds their exact text: the injection window, the handoff, one
followed archive link, or none. This scores literal reachability only. It says that
text is gone, never that an agent can continue the work, and no model is called.

The default run uses the bundled synthetic pack and reads no vault. --vault previews
the configured handoff with a local probe file and writes nothing; archive notes
already linked from the handoff count as one hop.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_handoff_fold as fold
from vault_compile_quality import QualityError, check_preservation
from vault_evidence import EvidenceError, decode, stable_read
from vault_healthcheck import HealthcheckError
from vault_paths import resolve_note_path
from vault_state import StateError

DEFAULT_FIXTURE = ROOT / 'tests/fixtures/handoff_fold/pack.json'
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PROBES = 256
MAX_EXPECTED_CHARS = 1024
KINDS = ('recall', 'artifact', 'continuation', 'decision', 'constraint')
CONTROLS = ('good', 'truncate', 'unlinked')
TIERS = ('injected', 'handoff', 'one_hop', 'lost')
PACK_STEM = 'Synthetic handoff'
FOLDED_AT = '1970-01-01T00:00:00Z'
LIMITATIONS = ('literal_reachability_is_a_loss_alarm_not_a_functional_probe',
               'no_model_scored_probes', 'paraphrased_answers_show_as_lost',
               'one_link_followed_from_the_handoff_only')
_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
_WIKI = re.compile(r'\[\[([^\[\]|#\r\n]+)(?:[#|][^\]\r\n]*)?\]\]')


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError('invalid_pack')
    return value


def _validate(pack, *, inline):
    required = {'schema_version', 'synthetic', 'settings', 'probes'} | ({'handoff'} if inline else set())
    if not isinstance(pack, dict) or set(pack) != required or pack['schema_version'] != 1:
        raise ValueError('invalid_pack')
    if not isinstance(pack['synthetic'], bool):
        raise ValueError('invalid_pack')
    settings = pack['settings']
    keys = {'threshold_bytes', 'protect_sections', 'window_bytes'}
    if not isinstance(settings, dict) or not keys <= set(settings) <= keys | {'newest'}:
        raise ValueError('invalid_pack')
    for key in keys:
        _positive(settings[key])
    if settings.get('newest', 'top') not in fold.ORDERS:
        raise ValueError('invalid_pack')
    if inline and (not isinstance(pack['handoff'], list)
                   or any(not isinstance(line, str) or '\n' in line for line in pack['handoff'])):
        raise ValueError('invalid_pack')
    probes = pack['probes']
    if not isinstance(probes, list) or not 1 <= len(probes) <= MAX_PROBES:
        raise ValueError('invalid_pack')
    seen = set()
    for probe in probes:
        if (not isinstance(probe, dict) or set(probe) != {'id', 'kind', 'expected'}
                or not isinstance(probe['id'], str) or not _ID.fullmatch(probe['id'])
                or probe['id'] in seen or probe['kind'] not in KINDS
                or not isinstance(probe['expected'], str) or not 0 < len(probe['expected']) <= MAX_EXPECTED_CHARS
                or '\n' in probe['expected']):
            raise ValueError('invalid_pack')
        seen.add(probe['id'])
    return pack


def load_pack(path=DEFAULT_FIXTURE):
    """Read and validate a pack file that carries its own handoff lines."""
    resolved = Path(path)
    _, _, raw = stable_read(lambda: resolved, MAX_JSON_BYTES, contents=True)
    return _validate(decode(raw), inline=True)


def _fold_settings(pack, config):
    settings = pack['settings']
    newest = settings.get('newest', 'top')
    fold._check_values(settings['threshold_bytes'], settings['protect_sections'], newest,
                       fold.DEFAULT_ARCHIVE_MAX_BYTES)
    return fold._settings(config, settings['threshold_bytes'], settings['protect_sections'], newest,
                          fold.DEFAULT_ARCHIVE_MAX_BYTES)


def _stubs(handoff):
    """Return {hash prefix: (archive name, stub section bytes)} for one handoff."""
    found = {}
    for section in fold.split_sections(handoff)[1]:
        match = fold._stub_match(handoff, section)
        if match is not None:
            found[match.group(2).decode('ascii')] = (match.group(3).decode('utf-8'),
                                                     handoff[section.start:section.end])
    return found


def _section_report(raw, projected, notes, rows):
    stubs = _stubs(projected)
    counts = {'folded': len(rows), 'preserved': 0, 'with_loss': 0, 'without_facts': 0, 'skipped': 0}
    for row in rows:
        reached = b''
        stub = stubs.get(row['sha256'][:fold.HASH_PREFIX_CHARS])
        if stub is not None:
            name, text = stub
            reached = fold.archive_sections(notes[name]).get(row['sha256'], text) if name in notes else text
        try:
            result = check_preservation(raw[row['start']:row['end']].decode('utf-8'),
                                        reached.decode('utf-8'))
        except QualityError:
            counts['skipped'] += 1
            continue
        if result['status'] == 'complete':
            counts['preserved'] += 1
        elif result['review_reasons'] == ['no_selected_literal_facts']:
            counts['without_facts'] += 1
        else:
            counts['with_loss'] += 1
    return counts


def _note_body(note):
    return b''.join(fold.archive_sections(note).values()).decode('utf-8')


def _probe_report(projected, notes, probes, window_bytes):
    window = projected[:window_bytes].decode('utf-8', errors='ignore')
    handoff = projected.decode('utf-8', errors='replace')
    linked = '\n'.join(_note_body(notes[name]) for name in sorted(set(_WIKI.findall(handoff)))
                       if name in notes)
    rows = []
    for probe in probes:
        routes = (('injected', window), ('handoff', handoff), ('one_hop', linked))
        tier = next((name for name, text in routes if probe['expected'] in text), 'lost')
        rows.append({'id': probe['id'], 'kind': probe['kind'], 'tier': tier})
    summary = {}
    for kind in KINDS:
        chosen = [row for row in rows if row['kind'] == kind]
        if chosen:
            summary[kind] = {'total': len(chosen),
                             **{tier: sum(row['tier'] == tier for row in chosen) for tier in TIERS}}
    return rows, summary


def _report(raw, projected, notes, rows, pack, control):
    sections = _section_report(raw, projected, notes, rows)
    probe_rows, summary = _probe_report(projected, notes, pack['probes'], pack['settings']['window_bytes'])
    lost = [row['id'] for row in probe_rows if row['tier'] == 'lost']
    return {'type': 'handoff_fold_evaluation', 'authority': 'data_only', 'synthetic': pack['synthetic'],
            'control': control, 'passed': not lost and sections['with_loss'] == 0, 'sections': sections,
            'probes': summary, 'probe_rows': probe_rows, 'lost_probes': lost,
            'limitations': list(LIMITATIONS)}


def _require_baseline(text, probes):
    if any(probe['expected'] not in text for probe in probes):
        raise ValueError('probe_not_in_original')


def evaluate(fixture=DEFAULT_FIXTURE, *, control='good'):
    """Fold a pack's handoff in memory and score it. A control breaks the fold on purpose."""
    if control not in CONTROLS:
        raise ValueError('invalid_control')
    pack = load_pack(fixture)
    raw = '\n'.join(pack['handoff']).encode('utf-8')
    _require_baseline(raw.decode('utf-8'), pack['probes'])
    plan = fold.plan_fold(raw, _fold_settings(pack, {}), archive_stem=PACK_STEM, folded_at=FOLDED_AT)
    if plan.status != 'ready':
        raise ValueError('fold_not_ready')
    projected, notes = plan.projected, {note.name: note.raw for note in plan.archives}
    if control == 'truncate':
        projected, notes = raw[:pack['settings']['window_bytes']], {}
    elif control == 'unlinked':
        notes = {}
    return _report(raw, projected, notes, plan.candidates, pack, control)


def _read_vault_json(policy, relative):
    def resolve():
        path = resolve_note_path(policy.vault, relative, policy.rules)
        if path.suffix.casefold() != '.json':
            raise ValueError('invalid_probe_path')
        return path
    _, _, raw = stable_read(resolve, MAX_JSON_BYTES, contents=True)
    return decode(raw)


def _linked_notes(policy, folder, handoff):
    """Read the archive notes the handoff already links to; an absent one stays absent."""
    notes = {}
    for name, _ in _stubs(handoff).values():
        if name not in notes:
            try:
                notes[name] = fold.read_handoff(policy, f'{folder}/{name}.md')[1]
            except StateError as error:
                if str(error) != 'handoff_missing':
                    raise
    return notes


def evaluate_vault(vault, probes_relative):
    """Score the configured handoff with a local probe file. This writes nothing."""
    policy, relative = fold._load(vault)
    pack = _validate(_read_vault_json(policy, probes_relative), inline=False)
    _, raw = fold.read_handoff(policy, relative)
    folder, stem = fold._archive_layout(policy, relative)
    notes = _linked_notes(policy, folder, raw)
    plan = fold.plan_fold(raw, _fold_settings(pack, policy.config), archive_stem=stem,
                          config=policy.config, folded_at=FOLDED_AT,
                          first_archive_number=fold._next_archive_number(policy, folder, stem))
    if not _stubs(raw):
        _require_baseline(raw.decode('utf-8'), pack['probes'])
    if plan.status != 'ready':
        return _report(raw, raw, notes, (), pack, 'good')
    return _report(raw, plan.projected, {**notes, **{note.name: note.raw for note in plan.archives}},
                   plan.candidates, pack, 'good')


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid_arguments')


def main(argv=None):
    try:
        parser = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                         allow_abbrev=False)
        parser.add_argument('--fixture', type=Path)
        parser.add_argument('--control', choices=CONTROLS)
        parser.add_argument('--vault', type=Path)
        parser.add_argument('--probes', help='vault-relative JSON probe file; needs --vault')
        args = parser.parse_args(argv)
        if args.vault is None:
            if args.probes is not None:
                raise ValueError('invalid_arguments')
            report = evaluate(args.fixture or DEFAULT_FIXTURE, control=args.control or 'good')
        else:
            if args.probes is None or args.control is not None or args.fixture is not None:
                raise ValueError('invalid_arguments')
            report = evaluate_vault(args.vault, args.probes)
    except (ValueError, OSError, StateError, EvidenceError, HealthcheckError, UnicodeError, RecursionError):
        print('{"error": "invalid_input"}', file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
