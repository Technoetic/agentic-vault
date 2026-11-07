#!/usr/bin/env python3
"""Read-only recurrence classes from a Markdown mistakes note.

Writers leave explicit signals in free text: recurrence markers, "same class"
references and ordinals. This tool joins entries on those signals first, then
offers syllable-bigram Jaccard groups over the remaining entries as candidates.
A plain "related" reference joins nothing: such links connect most of a real
note into one component. Numbers are never masked, because they carry the
strongest signal. The output is observation data for a person to read. It does
not write a file, change a ledger, or prove that two entries share a cause.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys

import vault_lesson_delta as delta
from vault_recall import _normalize
from vault_retrieval import _hangul2_tokens
import vault_state as state

AUTHORITY = 'observation_data_only'
DEFAULT_LEDGER = '00-meta/lessons.md'
DEFAULT_ID_PREFIX = 'M'
# Not derived from data: no labelled sample ships with the engine, and the report
# says so. A vault should set its own value from entries a person has grouped.
DEFAULT_THRESHOLD = 0.35
UNLEDGERED_COUNT = 3
MAX_ENTRIES = 1024
MIN_TOKENS = 4
# Only a large note has enough entries to tell a template label from a topic word.
DF_FILTER_MIN_ENTRIES = 8
DF_FILTER_SHARE = 0.5

_PREFIX = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,23}')
_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')
_DATE = re.compile(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)')
_RECUR_WORD = r'재발(?!\s*(?:방지|조짐|률|율))|recurr(?:ed|ence)'
_RECUR = re.compile(_RECUR_WORD, re.IGNORECASE)
_RECUR_MARKER = re.compile(r'^(?:\s*(?:[-*]\s+)?[^\w\s*]{0,3}\*\*|#{3,6}\s+)(?:' + _RECUR_WORD + ')',
                           re.IGNORECASE)
_CLASS = re.compile(r'(?:(?:같은|동일(?:한)?)\s*(?:부류|계열|유형|구조)(?!\s*[가는이은]?\s*아[니님])'
                    r'|same\s+(?:class|family|pattern))', re.IGNORECASE)
_CLAUSE = re.compile(r'(?<=[.!?。])\s+|;\s*')
_RELATED = re.compile(r'(?:관련|참고)[^:：]{0,12}[:：]|(?:related|see also)\s*:', re.IGNORECASE)
_ORDINAL = re.compile(r'(\d{1,4}|두|세|네|다섯)\s*(?:번째|회째)')
_ORDINAL_WORDS = {'두': 2, '세': 3, '네': 4, '다섯': 5}
_REPEAT = re.compile(_RECUR_WORD + '|반복', re.IGNORECASE)


class LessonClusterError(Exception):
    """A content-free operational error code."""


class _Entry:
    def __init__(self, identifier):
        self.id = identifier
        self.title_dates = []
        self.body_dates = []
        self.marker_dates = []
        self.markers = 0
        self.ordinal = 0
        self.refs = []
        self.text = []

    @property
    def base_count(self):
        return 1 + self.markers

    def dates(self):
        return self.title_dates + self.marker_dates or self.body_dates[:1]


def _dates(text):
    found = []
    for value in _DATE.findall(text):
        try:
            date.fromisoformat(value)
        except ValueError:
            continue
        found.append(value)
    return found


class _Note:
    """One pass over the visible lines. Fenced examples never count."""

    def __init__(self, raw, prefix):
        self.identifier = rf'(?<![A-Za-z0-9_-]){re.escape(prefix)}-\d{{1,10}}(?![0-9])'
        self.ids = re.compile(self.identifier)
        self.entry_heading = re.compile(rf'^({self.identifier})(?=$|[\s:·—–(-])(.*)$')
        self.entry_bullet = re.compile(
            rf'^[-*]\s+(?:\*\*({self.identifier})(?=[\s:·—–(*-])|({self.identifier})(?=\s*[:·—–-]))(.*)$')
        self.number_edge = re.compile(
            rf'({self.identifier}(?:\s*[·,/]\s*{self.identifier})*)(?:의|와|과)?\s*(?:{_RECUR_WORD}|계열|부류)',
            re.IGNORECASE)
        self.entries = {}
        self.duplicates = set()
        rows = [line for line in delta._lines(raw) if line['visible']]
        heading_ids = {match[1] for line in rows
                       for heading in [_HEADING.match(line['text'])] if heading and len(heading[1]) in (2, 3)
                       for match in [self.entry_heading.match(heading[2])] if match}
        current = None
        for line in rows:
            text = line['text']
            heading = _HEADING.match(text)
            match = self.entry_heading.match(heading[2]) if heading and len(heading[1]) in (2, 3) else None
            if match:
                current = self._open(match[1], match[2], text, len(heading[1]) == 3)
                continue
            if heading and len(heading[1]) <= 2:
                current = None
                continue
            bullet = self.entry_bullet.match(text)
            identifier = (bullet[1] or bullet[2]) if bullet else None
            if identifier and identifier not in heading_ids:
                current = self._open(identifier, bullet[3], text, False)
                continue
            if current is not None:
                self._scan(current, text)

    def _open(self, identifier, title, text, nested):
        entry = self.entries.get(identifier)
        if entry is None:
            entry = self.entries[identifier] = _Entry(identifier)
            if len(self.entries) > MAX_ENTRIES:
                raise LessonClusterError('lesson_entry_limit')
            entry.title_dates.extend(_dates(title))
        elif _RECUR.search(title):
            entry.markers += 1
            entry.marker_dates.extend(_dates(title))
        elif not nested:
            self.duplicates.add(identifier)
        self._clauses(entry, title)
        return entry

    def _scan(self, entry, text):
        if _RECUR_MARKER.match(text):
            entry.markers += 1
            entry.marker_dates.extend(_dates(text))
        else:
            entry.body_dates.extend(_dates(text))
        self._clauses(entry, text)

    def _clauses(self, entry, text):
        for clause in _CLAUSE.split(text):
            related = _RELATED.search(clause)
            kept = clause[:related.start()] if related else clause
            if not kept.strip():
                continue
            entry.text.append(kept)
            refs = (self.ids.findall(kept) if _CLASS.search(kept)
                    else [ref for match in self.number_edge.finditer(kept) for ref in self.ids.findall(match[1])])
            entry.refs.extend(ref for ref in refs if ref != entry.id)
            if _REPEAT.search(kept):
                for value in _ORDINAL.findall(kept):
                    number = _ORDINAL_WORDS.get(value) or int(value)
                    entry.ordinal = max(entry.ordinal, number)


class _Components:
    def __init__(self, names):
        self.parent = {name: name for name in names}

    def find(self, name):
        while self.parent[name] != name:
            self.parent[name] = self.parent[self.parent[name]]
            name = self.parent[name]
        return name

    def union(self, left, right):
        left, right = sorted((self.find(left), self.find(right)))
        self.parent[right] = left

    def groups(self):
        result = {}
        for name in self.parent:
            result.setdefault(self.find(name), []).append(name)
        return list(result.values())


def _tokens(entries):
    sets = {entry.id: set(_hangul2_tokens(_normalize(' '.join(entry.text)))) for entry in entries}
    if len(sets) >= DF_FILTER_MIN_ENTRIES:
        counts = {}
        for tokens in sets.values():
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
        common = {token for token, count in counts.items() if count > len(sets) * DF_FILTER_SHARE}
        sets = {name: tokens - common for name, tokens in sets.items()}
    return {name: tokens for name, tokens in sets.items() if len(tokens) >= MIN_TOKENS}


def _jaccard(left, right):
    return len(left & right) / len(left | right)


def _candidates(entries, threshold):
    """Complete-link groups: every pair inside a group clears the threshold.

    Single-link union would chain unrelated entries through a middle one, which
    is the failure that a plain reference graph already shows on a real note.
    """
    tokens = _tokens(entries)
    scores = {pair: _jaccard(tokens[pair[0]], tokens[pair[1]])
              for pair in itertools.combinations(sorted(tokens), 2)}
    groups = {name: [name] for name in tokens}
    for (left, right), score in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
        if score < threshold:
            break
        if groups[left] is groups[right]:
            continue
        members = groups[left] + groups[right]
        if all(scores[tuple(sorted(pair))] >= threshold for pair in itertools.combinations(members, 2)):
            for name in members:
                groups[name] = members
    unique = {id(group): group for group in groups.values() if len(group) > 1}
    return [(sorted(group), min(scores[tuple(sorted(pair))] for pair in itertools.combinations(group, 2)))
            for group in unique.values()]


def _ledger(policy, path, ids):
    report = dict(path=path, state='available', ignored_rows=0)
    try:
        path = delta._safe_note(policy, path)
        if not policy.note(path).exists():
            return dict(report, state='missing'), None
        info, _, raw = policy.read_note(path)
        records, _, _, ignored = delta._inventory(raw, 'lesson:' + path.casefold())
    except (delta.LessonDeltaError, state.StateError) as exc:
        return dict(report, state='invalid', error=str(exc)), None
    except (OSError, ValueError, UnicodeError):
        return dict(report, state='invalid', error='invalid_or_unavailable_input'), None
    report.update(path=path, sha256=info['sha256'], ignored_rows=len(ignored))
    mentions = {}
    for line in [record['line'] for record in records] + ignored:
        for identifier in ids.findall(line['text']):
            mentions.setdefault(identifier, set()).add(line['number'] + 1)
    return report, mentions


def _class(members, status, mentions, similarity=None):
    ids = sorted(entry.id for entry in members)
    summed = sum(entry.base_count for entry in members)
    count = max(summed, max(entry.ordinal for entry in members))
    dates = sorted(value for entry in members for value in entry.dates())
    lines = sorted({line for name in ids for line in (mentions or {}).get(name, ())})
    linked = None if mentions is None else bool(lines)
    item = dict(class_id='lc-' + hashlib.sha256('\0'.join(ids).encode('utf-8')).hexdigest()[:12],
                status=status, ids=ids, count=count, count_is_candidate=count > summed,
                first_seen=dates[0] if dates else None, last_seen=dates[-1] if dates else None,
                ledger_linked=linked, ledger_lines=lines,
                unledgered=linked is False and count >= UNLEDGERED_COUNT)
    if similarity is not None:
        item['similarity'] = round(similarity, 3)
    return item


def _cluster(vault, mistakes, ledger, threshold, id_prefix):
    if not isinstance(id_prefix, str) or not _PREFIX.fullmatch(id_prefix):
        raise LessonClusterError('invalid_id_prefix')
    custom = threshold is not None
    if custom and (type(threshold) not in (int, float) or not 0 < threshold <= 1):
        raise LessonClusterError('invalid_threshold')
    value = threshold if custom else DEFAULT_THRESHOLD
    policy = state._Policy(vault)
    mistakes = delta._safe_note(policy, mistakes)
    info, _, raw = policy.read_note(mistakes)
    note = _Note(raw, id_prefix)
    known = note.entries
    components = _Components(known)
    unresolved = 0
    for entry in known.values():
        for ref in entry.refs:
            if ref in known:
                components.union(entry.id, ref)
            else:
                unresolved += 1
    ledger_report, mentions = _ledger(policy, ledger, note.ids)
    classes, remaining = [], []
    for group in components.groups():
        members = [known[name] for name in group]
        if len(members) > 1 or members[0].markers or members[0].ordinal:
            classes.append(_class(members, 'explicit', mentions))
        else:
            remaining.extend(members)
    joined = set()
    for ids, similarity in _candidates(remaining, value):
        classes.append(_class([known[name] for name in ids], 'candidate', mentions, similarity))
        joined.update(ids)
    classes.sort(key=lambda item: (-item['count'], item['ids']))
    return dict(version=1, authority=AUTHORITY, source=dict(path=mistakes, sha256=info['sha256']),
                ledger=ledger_report,
                threshold=dict(value=value, source='caller' if custom else 'default_uncalibrated'),
                entries=len(known), singletons=len(remaining) - len(joined), classes=classes,
                diagnostics=dict(duplicate_ids=sorted(note.duplicates), unresolved_references=unresolved))


def cluster_lessons(vault, mistakes, ledger=DEFAULT_LEDGER, *, threshold=None, id_prefix=DEFAULT_ID_PREFIX):
    """Return recurrence classes for a mistakes note. Reads only; never writes."""
    try:
        return _cluster(vault, mistakes, ledger, threshold, id_prefix)
    except LessonClusterError:
        raise
    except (delta.LessonDeltaError, state.StateError) as exc:
        raise LessonClusterError(str(exc)) from exc
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise LessonClusterError('invalid_or_unavailable_input') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vault', type=Path, required=True)
    parser.add_argument('--mistakes', required=True, help='Vault-relative mistakes note.')
    parser.add_argument('--ledger', default=DEFAULT_LEDGER, help='Vault-relative lesson ledger.')
    parser.add_argument('--threshold', type=float, help='Jaccard threshold for candidate groups.')
    parser.add_argument('--id-prefix', default=DEFAULT_ID_PREFIX, help='Letters before the hyphen in entry numbers.')
    args = parser.parse_args(argv)
    try:
        result = cluster_lessons(args.vault, args.mistakes, args.ledger, threshold=args.threshold,
                                 id_prefix=args.id_prefix)
    except LessonClusterError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
