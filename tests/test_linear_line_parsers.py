"""Linear line parsers and scanners that replaced super-linear regexes.

The former patterns live only here, as oracles: the lazy-suffix heading and
frontmatter-line patterns, the backtick code-span, wiki-link (lint,
healthcheck, privileged-note and Telegram bridge), Markdown-link and
frontmatter-block patterns that the scanners replaced, and the credential
pattern jev_client.SENSITIVE before four of its fragments were rewritten in
place (callers use only whether it matches). The acceptance gates
borrow the two tiers of VULCANBOOST (Li et al., "VULCANBOOST: Boosting ReDoS Fixes through
Symbolic Representation and Feature Normalization", USENIX Security 2025), not
its DFA machinery: tier A, fixed cases (CommonMark-derived and vault lines)
agree with the oracle; tier B, bounded-exhaustive equivalence with the oracle
over a small alphabet. A generous timing gate on the new code stands in for
"free from ReDoS". The default suite never times the old regexes; set
AGENTIC_VAULT_SLOW_TESTS=1 for the larger exhaustive runs and the old-versus-new
timings.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager, ExitStack, redirect_stderr, redirect_stdout
import io
import itertools
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from typing import Callable, NamedTuple
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import jarvis_bridge  # noqa: E402
import jev_ask  # noqa: E402
import jev_client  # noqa: E402
import vault_egress  # noqa: E402  (lint also loads it lazily)
import vault_healthcheck  # noqa: E402
import vault_judge  # noqa: E402
import vault_links  # noqa: E402
import vault_lint_extensions as lint  # noqa: E402
import vault_provenance  # noqa: E402
import vault_query_expansion  # noqa: E402
import vault_recall  # noqa: E402
import vault_retrieval  # noqa: E402
import vault_ssot  # noqa: E402
import vault_state  # noqa: E402
import vault_warning_policy  # noqa: E402

SLOW = os.environ.get('AGENTIC_VAULT_SLOW_TESTS') == '1'

# --- Oracles: the v0.31.0 patterns, kept only in this file. ---
OLD_ATX = re.compile(r' {0,3}(#{1,6})\s+(.+?)\s*#*$')                # vault_ssot.py:162, vault_state.py:579
OLD_LINT_HEADING = re.compile(r'^ {0,3}#{1,6}\s+(.+?)\s*$')          # vault_lint_extensions.py:141
OLD_LINT_CLOSING = re.compile(r'\s+#+\s*$')                          # vault_lint_extensions.py:143 (re.sub)
OLD_LINT_DATE = re.compile(r'^(created|updated):\s*(.*?)\s*$')       # vault_lint_extensions.py:194
OLD_LINT_COMMENT = re.compile(r'\s+#')                               # vault_lint_extensions.py:197 (re.split)
OLD_HEADER_FIELD = re.compile(r'^([a-z_]+)\s*:\s*(.*?)\s*$')         # vault_provenance.py:436
OLD_RULE_HEADING = re.compile(r'^#{1,3}\s+(.+?)\s*$')                # vault_healthcheck.py:1561
OLD_RULE_PAREN = re.compile(r'\s*\([^)]*\)')                         # vault_healthcheck.py:1570 (re.sub)
OLD_RULE_DASH = re.compile(r'\s*[—-]\s.*$')                          # vault_healthcheck.py:1571 (re.sub)
OLD_TITLE = re.compile(r'^\s*title\s*:\s*(.*?)\s*$', re.IGNORECASE)  # vault_recall.py:40
OLD_RECALL_HEADING = re.compile(r'^\s*#{1,6}\s+(.+?)\s*#*\s*$')      # vault_recall.py:41, vault_query_expansion.py:478
OLD_LINT_CODE = re.compile(r'(`+).*?\1')                                    # vault_lint_extensions.py:257 (re.sub)
OLD_LINT_LINKS = re.compile(r'(?<!!)\[\[([^]\n]+)\]\]|!\[\[([^]\n]+)\]\]')  # vault_lint_extensions.py:258 (finditer)
OLD_KEY_LINK = re.compile(r'\[([^]]+)\]\([^)]*\)')                          # vault_lint_extensions.py:122 (re.sub)
OLD_FRONTMATTER = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)       # vault_healthcheck.py:1254
OLD_CODE_SPAN = re.compile(r'(`+)(.+?)\1')                                  # vault_ssot.py:70 (CODE_SPAN_RE)
OLD_WIKILINK = re.compile(r'\[\[((?:[^\]|#]|\](?!\]))+)(?:[#|][^\]]*)?\]\]')  # vault_healthcheck.py:796 (WIKILINK_RE)
OLD_PRIVILEGED_LINK = re.compile(r'\[\[([^\]\n]+)\]\]')                     # vault_provenance.py:520 (finditer)
OLD_TELEGRAM_LINK = re.compile(r'\[\[([^\]]+)\]\]')                         # jarvis_bridge.py:1237 (re.sub)
# jev_client.py:29 (SENSITIVE), the whole v0.31.0 credential pattern. Every
# caller uses only whether search() finds a match (jev_client._matches_sensitive;
# jev_ask and vault_judge hold the same object), so verdicts are compared.
OLD_SENSITIVE = re.compile(
    r"apikey_[a-f0-9]{32}_[a-f0-9]{64}|"
    r"\b(?:sk[-_][a-zA-Z0-9_-]{16,}|gh[pousr]_[a-zA-Z0-9]{20,}|"
    r"github_pat_[a-zA-Z0-9_]{20,}|AKIA[A-Z0-9]{16}|(?:ts|tsk|typesafe)[_-][a-zA-Z0-9_-]{16,})\b|"
    r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b|"
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"\b(?:authorization\s*[:=]\s*(?:bearer|basic)\s+\S+|"
    r"(?:[A-Z0-9_]*API[_-]?KEY|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"password|passwd|secret)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{4,})|"
    r"\b(?:authorization\s*:\s*bearer|api[_-]?key\s*[:=]|"
    r"password\s*[:=]|secret\s*[:=])\s*[^\s]{8,}|"
    r"(?<![0-9])[0-9]{5,12}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])|"
    r"\bxox[abposr]-[A-Za-z0-9-]{10,}|\bxapp-[0-9]-[A-Za-z0-9-]{10,}|"
    r"hooks\.slack\.com/services/[A-Za-z0-9_/-]{20,}|"
    r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])|"
    r"\b[a-z][a-z0-9+.-]*://[^\s:/?#@]*:[^\s/?#@]+@|"
    r"(?:비밀\s?번호|비번|패스워드|암호|토큰|인증\s?키|액세스\s?키|시크릿|api\s?키)"
    r"\s*[\"']?\s*[:=]\s*[\"']?(?:(?![\"',;])[\x21-\x7e]){4,}", re.IGNORECASE)
# The super-linear fragments of OLD_SENSITIVE: the JWT first part and the URL
# scheme read their run again from every later start, and \s* on both sides of
# an optional quote tried every split of a whitespace run.
OLD_SENSITIVE_FRAGMENTS = (r'\beyJ[a-zA-Z0-9_-]{8,}\.', r'\b[a-z][a-z0-9+.-]*://', '\\s*[\\"\']?\\s*[:=]')


def oracle_atx(text):
    match = OLD_ATX.match(text)
    return (len(match[1]), match[2]) if match else None


def oracle_lint_heading(line):
    match = OLD_LINT_HEADING.match(line)
    return match[1] if match else None


def oracle_closing(text):
    return OLD_LINT_CLOSING.sub('', text)


def oracle_date_field(line):
    match = OLD_LINT_DATE.match(line)
    return (match[1], match[2]) if match else None


def oracle_before_comment(value):
    return OLD_LINT_COMMENT.split(value, maxsplit=1)[0]


def oracle_header_field(line):
    match = OLD_HEADER_FIELD.match(line)
    return (match[1], match[2]) if match else None


def oracle_rule_heading(line):
    match = OLD_RULE_HEADING.match(line)
    return match.group(1) if match else None


def oracle_paren(text):
    return OLD_RULE_PAREN.sub('', text)


def oracle_dash(text):
    return OLD_RULE_DASH.sub('', text)


def oracle_title_value(line):
    match = OLD_TITLE.match(line)
    return match.group(1) if match else None


def oracle_heading_label(line):
    match = OLD_RECALL_HEADING.match(line)
    return match.group(1) if match else None


def oracle_code_removed(line):
    return OLD_LINT_CODE.sub('', line)


def oracle_link_texts(line):
    return [match[1] if match[1] is not None else match[2] for match in OLD_LINT_LINKS.finditer(line)]


def oracle_link_text(text):
    return OLD_KEY_LINK.sub(r'\1', text)


def oracle_split_frontmatter(text):
    # The whole former split_frontmatter, its startswith guard included.
    if not text.startswith('---'):
        return None, text
    match = OLD_FRONTMATTER.match(text)
    return (match.group(1), text[match.end():]) if match else (None, text)


def oracle_code_spans(cell):
    return [(match.start(), match.end(), match[2]) for match in OLD_CODE_SPAN.finditer(cell)]


def oracle_wikilink_matches(text):
    # findall (the healthcheck) returned group 1 of these matches; vault_links read group(1).
    return [(match.start(), match.end(), match[1]) for match in OLD_WIKILINK.finditer(text)]


def oracle_privileged_link_texts(text, limit=128):
    # The privileged-note scan listed every match and kept the first 128.
    return [match[1] for match in list(OLD_PRIVILEGED_LINK.finditer(text))[:limit]]


def oracle_telegram_links(text):
    return OLD_TELEGRAM_LINK.sub(r'<i>\1</i>', text)


def former_verdict(text):
    return OLD_SENSITIVE.search(text) is not None


def verdict(text):
    return jev_client.SENSITIVE.search(text) is not None


# One code point of each kind of whitespace a split line can still hold: ASCII
# space and tab, vertical tab, file separator, NEL, no-break space, line
# separator and ideographic space (bytes.splitlines splits only on CR and LF).
WHITESPACE = ' \t\x0b\x1c\x85\xa0\u2028\u3000'
RECORD = WHITESPACE + '#a가'  # the 11-symbol alphabet of the deep verification
# The scanners' 11-symbol alphabet: what the five scanned patterns treat
# specially (backtick, brackets, parentheses, '!', line feed, '-'), a space, a
# letter and a Hangul syllable. Every other character acts in these patterns
# like the letter, or for the frontmatter pattern's \s like the space.
SCAN = '`[]()!\n- a가'
# The wiki-link scans' 9-symbol alphabet: the characters WIKILINK_RE tells
# apart ('[', ']', '|', '#'), the line feed the privileged-note scan stops at,
# the '!' of an embed, a space, a letter and a Hangul syllable. Every other
# character acts in these two patterns like the letter.
LINKS = '[]|#\n! a가'


class Pair(NamedTuple):
    name: str
    module: object
    attr: str
    oracle: Callable
    runs: tuple  # ((alphabet, maximum length), ...)
    prefixes: tuple = ('',)
    newline_free: bool = True  # False: equal even when the text holds a line feed
    special: str = '#a가'  # non-whitespace symbols for the slow all-whitespace run

    def new(self):
        return getattr(self.module, self.attr)


PAIRS = (
    Pair('ssot ATX heading', vault_ssot, '_atx_heading', oracle_atx, ((RECORD, 6),)),
    Pair('state ATX heading', vault_state, '_atx_heading', oracle_atx, ((RECORD, 6),)),
    Pair('lint heading text', lint, '_heading_text', oracle_lint_heading, ((RECORD, 6),)),
    Pair('lint closing run', lint, '_without_closing_run', oracle_closing,
         ((RECORD, 6), (RECORD + '\n', 5)), newline_free=False),
    Pair('lint date field', lint, '_date_field', oracle_date_field, ((RECORD + ':', 5),),
         prefixes=('', 'created:', 'updated:', 'created :', 'Updated:'), special='#a:'),
    Pair('lint comment split', lint, '_before_comment', oracle_before_comment,
         ((RECORD, 6), (RECORD + '\n', 5)), newline_free=False),
    Pair('provenance header field', vault_provenance, '_header_field', oracle_header_field,
         ((RECORD + ':', 5),), prefixes=('', 'k', 'key:', 'a_b :', 'Key:'), special='#a_:'),
    Pair('rule heading', vault_healthcheck, '_rule_heading', oracle_rule_heading, ((RECORD, 6),)),
    Pair('rule parenthesized note', vault_healthcheck, '_drop_parenthesized', oracle_paren,
         ((' \t\n\xa0\u3000()a', 6),), newline_free=False, special='()a'),
    Pair('rule dash tail', vault_healthcheck, '_drop_dash_tail', oracle_dash,
         ((' \t\xa0\u2028\u3000—-a', 6),), special='—-a'),
    Pair('recall title value', vault_recall, '_title_value', oracle_title_value, ((RECORD + ':', 5),),
         prefixes=('', 'title', ' TITLE :', 'Title\t:', 't\u0131tle:', 't\u0130tle:'), special='#a:'),
    Pair('recall heading label', vault_recall, '_heading_label', oracle_heading_label, ((RECORD, 6),)),
    # The scanners have no line-feed precondition, and each also runs longer
    # over just the characters its pattern distinguishes.
    Pair('lint code removal', lint, '_without_code_spans', oracle_code_removed,
         ((SCAN, 6), ('`a\n', 11)), newline_free=False, special='`a'),
    Pair('lint link texts', lint, '_wiki_link_texts', oracle_link_texts,
         ((SCAN, 6), ('[]!a\n', 8)), newline_free=False, special='[]!a'),
    Pair('lint heading link text', lint, '_without_link_targets', oracle_link_text,
         ((SCAN, 6), ('[]()a\n', 8)), newline_free=False, special='[]()a'),
    Pair('healthcheck frontmatter', vault_healthcheck, 'split_frontmatter', oracle_split_frontmatter,
         (('-\n\r \ta가', 6),), prefixes=('', '---', '---\n', '---\r\n'), newline_free=False, special='-a'),
    Pair('ssot code spans', vault_ssot, '_code_spans', oracle_code_spans,
         ((SCAN, 6), ('`a\n', 11)), newline_free=False, special='`a'),
    # The healthcheck wiki-link scan (full mode, --staged backlinks and
    # vault_links) is compared by match position and group 1.
    Pair('healthcheck wikilinks', vault_healthcheck, '_wikilink_matches', oracle_wikilink_matches,
         ((LINKS, 6), ('[]|#a', 9)), newline_free=False, special='[]|#a'),
    Pair('provenance link texts', vault_provenance, '_wiki_link_texts', oracle_privileged_link_texts,
         ((LINKS, 6), ('[]\na', 10)), newline_free=False, special='[]a'),
    # The opt-in Telegram bridge turns the wiki links of each message line into
    # italics; its pattern tells only '[' and ']' apart from other characters.
    Pair('telegram wiki links', jarvis_bridge, '_italic_wiki_links', oracle_telegram_links,
         ((LINKS, 6), ('[]a', 11)), newline_free=False, special='[]a'),
)
PAIR = {pair.name: pair for pair in PAIRS}
SCANNERS = ('lint code removal', 'lint link texts', 'lint heading link text', 'healthcheck frontmatter',
            'ssot code spans', 'healthcheck wikilinks', 'provenance link texts', 'telegram wiki links')


def strings(alphabet, length, prefixes=('',)):
    for prefix in prefixes:
        for size in range(length + 1):
            for chars in itertools.product(alphabet, repeat=size):
                yield prefix + ''.join(chars)


def mismatches(pair, values, limit=5):
    """Return (count, first mismatches) of new helper against oracle."""
    new, found, count = pair.new(), [], 0
    for value in values:
        count += 1
        expected, actual = pair.oracle(value), new(value)
        if expected != actual and len(found) < limit:
            found.append((value, expected, actual))
    return count, found


# --- Tier A: CommonMark 0.31.2, section 4.2 "ATX headings", examples 62-79. ---
# CommonMark Spec by John MacFarlane, CC BY-SA 4.0. Every input below is a
# paraphrase written for this test (other words, widths and counts) that
# exercises the rule of the cited example; no spec text is copied. `expected`
# is what the former regex captured as (level, raw label); `commonmark` is the
# heading a CommonMark parser builds from the same line, as (level, text
# content after inline parsing), or None. `cm_deviates` marks the examples
# where the regex behaviour, kept on purpose, differs from CommonMark.
COMMONMARK_CASES = (
    dict(example=62, cm_deviates=False,
         lines=('# alpha', '## alpha', '### alpha', '#### alpha', '##### alpha', '###### alpha'),
         expected=((1, 'alpha'), (2, 'alpha'), (3, 'alpha'), (4, 'alpha'), (5, 'alpha'), (6, 'alpha')),
         commonmark=((1, 'alpha'), (2, 'alpha'), (3, 'alpha'), (4, 'alpha'), (5, 'alpha'), (6, 'alpha'))),
    dict(example=63, cm_deviates=False, lines=('####### alpha',), expected=(None,), commonmark=(None,)),
    dict(example=64, cm_deviates=False, lines=('#3 nuts', '#tagged'), expected=(None, None),
         commonmark=(None, None)),
    dict(example=65, cm_deviates=False, lines=('\\## alpha',), expected=(None,), commonmark=(None,)),
    # Inline markup and escapes stay raw in the label (no inline parsing).
    dict(example=66, cm_deviates=True, lines=('# alpha *beta* \\*gamma\\*',),
         expected=((1, 'alpha *beta* \\*gamma\\*'),), commonmark=((1, 'alpha beta *gamma*'),)),
    dict(example=67, cm_deviates=False, lines=('#' + ' ' * 9 + 'alpha' + ' ' * 11,),
         expected=((1, 'alpha'),), commonmark=((1, 'alpha'),)),
    dict(example=68, cm_deviates=False, lines=(' ### alpha', '  ## alpha', '   # alpha'),
         expected=((3, 'alpha'), (2, 'alpha'), (1, 'alpha')),
         commonmark=((3, 'alpha'), (2, 'alpha'), (1, 'alpha'))),
    dict(example=69, cm_deviates=False, lines=('    # alpha',), expected=(None,), commonmark=(None,)),
    dict(example=70, cm_deviates=False, lines=('alpha', '    # beta'), expected=(None, None),
         commonmark=(None, None)),
    dict(example=71, cm_deviates=False, lines=('## alpha ##', ' ###  beta   ###'),
         expected=((2, 'alpha'), (3, 'beta')), commonmark=((2, 'alpha'), (3, 'beta'))),
    dict(example=72, cm_deviates=False, lines=('# alpha ' + '#' * 20, '#### alpha #'),
         expected=((1, 'alpha'), (4, 'alpha')), commonmark=((1, 'alpha'), (4, 'alpha'))),
    # Whitespace after the closing run defeats \s*#*$, so the run stays in the label.
    dict(example=73, cm_deviates=True, lines=('### alpha ###' + ' ' * 4,),
         expected=((3, 'alpha ###'),), commonmark=((3, 'alpha'),)),
    dict(example=74, cm_deviates=False, lines=('### alpha ### z',),
         expected=((3, 'alpha ### z'),), commonmark=((3, 'alpha ### z'),)),
    # The regex strips a closing run that no whitespace precedes.
    dict(example=75, cm_deviates=True, lines=('# alpha#',), expected=((1, 'alpha'),),
         commonmark=((1, 'alpha#'),)),
    # Backslash escapes are not modelled: the trailing '#' run is stripped.
    dict(example=76, cm_deviates=True, lines=('### alpha \\###', '## alpha #\\##', '# alpha \\#'),
         expected=((3, 'alpha \\'), (2, 'alpha #\\'), (1, 'alpha \\')),
         commonmark=((3, 'alpha ###'), (2, 'alpha ###'), (1, 'alpha #'))),
    dict(example=77, cm_deviates=False, lines=('***', '## alpha', '***'),
         expected=(None, (2, 'alpha'), None), commonmark=(None, (2, 'alpha'), None)),
    dict(example=78, cm_deviates=False, lines=('Some text', '# alpha', 'More text'),
         expected=(None, (1, 'alpha'), None), commonmark=(None, (1, 'alpha'), None)),
    # Empty headings: no heading at all, and a lone closing run becomes the label '#'.
    dict(example=79, cm_deviates=True, lines=('### ', '##', '## ##'),
         expected=(None, None, (2, '#')), commonmark=((3, ''), (2, ''), (2, ''))),
)
DEVIATING_EXAMPLES = (66, 73, 75, 76, 79)
# Lines from the deep verification and the vault's own section names.
VAULT_CASES = (
    ('####### a', None), ('    # a', None), ('#a', None), ('# ', None), ('#', None),
    ('#  ', (1, ' ')), ('# #', (1, '#')), ('### ###', (3, '#')), ('# foo#', (1, 'foo')),
    ('# a# #', (1, 'a#')), ('# a #b', (1, 'a #b')), ('#\u3000foo', (1, 'foo')),
    ('## 확정 ###' + ' ' * 3, (2, '확정 ###')), ('## 확정 ###', (2, '확정')),
    ('### Blocked ###', (3, 'Blocked')), ('#\u3000확정', (1, '확정')), ('#' * 7 + ' 확정', None),
    ('## ✅ 확정 — 2026', (2, '✅ 확정 — 2026')), ('## 보류\t', (2, '보류')),
)

# Lines that carry every line boundary str.splitlines knows, mid-line.
BOUNDARIES = '\r', '\r\n', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'


def separated(line):
    """Insert each boundary into a copy of line, after its first character."""
    return [line[:1] + mark + line[1:] for mark in BOUNDARIES]


def generated_lines(count=500, seed=20261027):
    """Deterministic heading-like lines for the default-unchanged comparisons."""
    rng = random.Random(seed)
    pieces = (' ', ' ', '\t', '\xa0', '\u3000', '\x0b', '\x85', '#', '##', 'a', 'b', '확정',
              'Blocked', '차단', '보류', 'confirmed', '✅', '—', '-', ':', '(note)', '(', ')',
              'title:', 'created:', '2026-10-27', '\\')
    lines = []
    for _ in range(count):
        head = rng.choice(('', ' ', '  ', '   ', '    ')) + '#' * rng.randint(0, 7)
        body = ''.join(rng.choice(pieces) for _ in range(rng.randint(0, 7)))
        lines.append(head + rng.choice(('', ' ', '\t', '\u3000')) + body)
    return lines


LINES = tuple(line for case in COMMONMARK_CASES for line in case['lines']) + tuple(
    line for line, _ in VAULT_CASES) + tuple(generated_lines())


def scan_lines(count=400, seed=20261027):
    """Deterministic lines of code spans, links and link-like text for the scanner comparisons."""
    rng = random.Random(seed)
    pieces = ('`', '`', '``', '```', '[[', ']]', '![[', '[', ']', '(', ')', '!', ' ', ' ', 'b', 'b#Alpha one',
              'b#^blk', 'b#x z', 'b#Nope', 'c', 'x/c', 'z/q', '|', '\\', 'a.png', '[Alpha](u)', '가', '\t')
    return [''.join(rng.choice(pieces) for _ in range(rng.randint(0, 10))) for _ in range(count)]


SCAN_LINES = tuple(scan_lines())


def scan_notes(lines):
    """Lint input whose links, code spans and headings reach the three lint scanners."""
    headings = ''.join(f'# {line}\n' for line in lines) + (
        '# [Alpha](u) one\n## `b` [x](y) z\n### [[c]](d)\nSetext [s](t)\n===\nblock ^blk\n')
    links = '[[b#Alpha one]] [[b#^blk]] [[b#x z]] [[b#Nope]] [[c]] [[x/c]] [[z/q]] `[[b#Nope]]`\n'
    return {'a.md': '\n'.join(lines) + '\n' + links, 'b.md': headings, 'x/c.md': 'x\n', 'y/c.md': 'y\n'}


def frontmatter_texts(count=200, seed=20261027):
    """Deterministic note texts around the frontmatter fences, CR and CRLF included."""
    rng = random.Random(seed)
    heads = ('---', '---', '---\n', '---\r\n', '--- \n', '-', '')
    pieces = ('---', '---', '\n', '\n', '\r\n', '\r', ' ', '\t', '\xa0', 'title: "A"', 'type: note', 'type: x',
              'tags: [[y]]', 'a', '-', '...', '[[z]]')
    return [rng.choice(heads) + ''.join(rng.choice(pieces) for _ in range(rng.randint(0, 12)))
            for _ in range(count)]


FRONTMATTER_TEXTS = (
    '---\ntitle: "A"\ntype: note\n---\nbody\n', '---\r\ntitle: "A"\r\n---\r\n\r\nbody\r\n',
    '---\n' + '\n' * 40, '---\n\n---\n', '---\n---\n', '--- \n title: "A"\n---  \n\n\nbody',
    '---\ntitle: "A"\ntags: [[y]]\n---\n', '---\ntype: x\n----\nbody', '---\rtitle: "A"\r---\r',
) + tuple(frontmatter_texts())
HEALTH_CONFIG = {'vault_name': 'Scan vault', 'required_keys': ['title'], 'enums': {'type': ['note']},
                 'deny_zones': [], 'exclude_dirs': ['.git'], 'index_note': '', 'log_note': '', 'log_tags': [],
                 'hot_note': '', 'handoff_note': '', 'ssot_note': '', 'health_report': '00-meta/health.md',
                 'rules_dir': '', 'frontmatter_roots': ['notes']}


def health_vault(root, texts):
    """A vault whose notes/ holds each text with its bytes and line ends as given."""
    (root / '00-meta').mkdir(parents=True)
    (root / '00-meta' / 'vault-config.json').write_text(json.dumps(HEALTH_CONFIG), encoding='utf-8')
    (root / 'notes').mkdir()
    for number, text in enumerate(texts):
        (root / 'notes' / f'n{number}.md').write_bytes(text.encode('utf-8'))
    return root


def full_healthcheck(vault):
    """Run the full-mode healthcheck in process: (exit code, stdout, stderr, report)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, 'argv', ['vault_healthcheck.py', '--vault', str(vault)]), \
            redirect_stdout(out), redirect_stderr(err):
        code = vault_healthcheck.main()
    return code, out.getvalue(), err.getvalue(), (vault / '00-meta' / 'health.md').read_text(encoding='utf-8')


# Source cells of check-support rows: code spans around paths and wiki links,
# openings the regex shortened, and backtick runs without a partner.
SOURCE_CELLS = (
    '`notes/Evidence.md`', '``notes/Evidence.md``', '```notes/Evidence.md``', '`` notes/Evidence.md ``',
    '[[Evidence]] `x`', '`[[Evidence]]`', '`` ` `` [[Evidence]]', '`a`b`notes/Evidence.md`',
    '`' * 9 + 'notes/Evidence.md', '`notes/Missing.md` [[Evidence]]', '`notes/Missing.md`', '````',
    '`notes/Evidence.md', 'notes/Evidence.md', '``[[Evidence]]`', '` `[[Evidence]]` `',
)


def support_vault(root, cells):
    """A vault with one evidence note and a ledger citing it through each source cell twice.

    The source column comes last without a closing pipe, so a cell whose
    backticks leave a code span open still ends at the line end.
    """
    rows = ''.join(f'| Atlas | retention {number} | {value} | 2025-01-01 |  | 2026-10-07 |  | confirmed | {cell}\n'
                   for number, cell in enumerate(cells) for value in ('12개월', '24개월'))
    files = {
        '00-meta/vault-config.json': '{"deny_zones": [], "exclude_dirs": []}',
        'notes/Evidence.md': '---\ntitle: Evidence\n---\n보존 기간은 12개월이다.\n',
        'notes/facts.md': ('| subject | relation | value | valid_from | valid_until | recorded | superseded_by | '
                           'status | source |\n' + '| --- ' * 9 + '|\n' + rows),
    }
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(text.encode('utf-8'))
    return root


def link_lines(count=240, seed=20261027):
    """Deterministic text of wiki links, broken links and bracket runs for the link-scan callers."""
    rng = random.Random(seed)
    pieces = ('[[', '[[', '[[', ']]', ']]', '[', ']', '|', '#', '!', '\n', ' ', 'n1', 'n2', 'n3', 'gone', 'Gone',
              'missing', 'x/n2', 'n3.md', 'img.png', 'Alpha one', '가', '`', 'risky', 'plain', '[[n2]]',
              '[[risky|r]]', '[[plain#h]]', '[[gone]]', '[[n3.md]]')
    return [''.join(rng.choice(pieces) for _ in range(rng.randint(0, 12))) for _ in range(count)]


LINK_LINES = tuple(link_lines())
# The hot note and notes/n1.md are privileged, so the default lint also runs
# the privileged-note link scan inside the healthcheck.
LINK_CONFIG = {**HEALTH_CONFIG, 'vault_name': 'Link vault', 'index_note': '00-meta/index.md',
               'hot_note': '00-meta/hot.md', 'privileged_notes': ['notes/n1.md']}


def link_notes(lines=LINK_LINES, per_note=12):
    """An index, a hot note and notes holding the link lines, with the targets they name.

    The --staged runs delete gone.md and rename n3.md; risky.md declares a
    forwarded origin, so a privileged note that links to it gets a warning.
    n1, n2 and risky each hold one link whose target only the exact former
    rules read: a lone ']' or a line feed inside the target, and a tail that
    a single ']' does not close.
    """
    def note(title, body, extra=''):
        return f'---\ntitle: "{title}"\ntype: note\n{extra}---\n{body}\n'

    notes = {
        'notes/n1.md': note('N1', '[[risky]] [[n2]] [[gone|x] y'), 'notes/n2.md': note('N2', 'plain [[x]/gone]]'),
        'notes/n3.md': note('N3', '[[n1|back]]'), 'notes/gone.md': note('Gone', '[[n3]]'),
        'notes/risky.md': note('Risky', 'forwarded [[x\n/n3.md]]', 'content_origin: forwarded\n'),
        'notes/plain.md': note('Plain', '[[gone#Part]] [[n3.md|three]]'),
        '00-meta/index.md': note('Index', '\n'.join(lines[:30]) + '\n```\n[[n4]]\n```\n`[[n5]]` [[n1]] [[n2|x]]'),
        '00-meta/hot.md': note('Hot', '\n'.join(lines[30:60])),
    }
    for number, start in enumerate(range(60, len(lines), per_note), 4):
        notes[f'notes/n{number}.md'] = note(f'N{number}', '\n'.join(lines[start:start + per_note]))
    return notes


def write_vault(root, files):
    """Write each file's text as UTF-8 bytes, line ends as given."""
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(text.encode('utf-8'))
    return root


def link_vault(root, per_note=12):
    """A fresh link vault at root, so the report of an earlier run is never read."""
    shutil.rmtree(root, ignore_errors=True)
    return write_vault(root, {'00-meta/vault-config.json': json.dumps(LINK_CONFIG), **link_notes(per_note=per_note)})


def git(vault, *args):
    subprocess.run(['git', *args], cwd=vault, check=True, capture_output=True)


def staged_link_vault(root):
    """A git vault whose index deletes gone.md, renames n3.md and changes the hot note.

    The link lines fill few notes, since --staged starts git processes per note.
    """
    link_vault(root, per_note=90)
    for args in (('init', '-q'), ('config', 'core.autocrlf', 'false'), ('config', 'user.name', 'Linear Test'),
                 ('config', 'user.email', 'linear@example.invalid'), ('add', '--', '.'),
                 ('commit', '-q', '-m', 'seed'), ('rm', '-q', '--', 'notes/gone.md'),
                 ('mv', 'notes/n3.md', 'notes/renamed.md')):
        git(root, *args)
    hot = root / '00-meta' / 'hot.md'
    hot.write_bytes(hot.read_bytes() + b'[[risky]] [[gone]]\n')
    git(root, 'add', '--', '00-meta/hot.md')
    return root


def staged_healthcheck(vault):
    """Run the --staged healthcheck in process: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, 'argv', ['vault_healthcheck.py', '--vault', str(vault), '--staged']), \
            redirect_stdout(out), redirect_stderr(err):
        code = vault_healthcheck.main()
    return code, out.getvalue(), err.getvalue()


PROPOSAL_STEMS = ('n1', 'n2', 'n3', 'gone', 'missing', 'risky', 'plain', 'Alpha one', 'other', 'spare')


def proposal_vault(root, lines=LINK_LINES):
    """A vault whose source note holds the link lines and whose candidates all match one query.

    The last line links spare and other only by the exact former rules, and
    names missing in a tail that a single ']' does not close.
    """
    files = {'00-meta/vault-config.json': json.dumps({'required_keys': ['title'], 'enums': {}}),
             '20-knowledge/source.md': '# Source\n' + '\n'.join(lines) + '\n[[x]/spare]] [[x\n/other.md]] [[missing|x] y\n'}
    files.update({f'20-knowledge/{stem}.md': '# Memory storage\nmemory storage\n' for stem in PROPOSAL_STEMS})
    return write_vault(root, files)


def privileged_notes(lines=LINK_LINES, per_note=4):
    """Privileged notes over the link lines, and notes of each provenance kind they can name."""
    notes = {
        'notes/risky.md': '---\ncontent_origin: forwarded\n---\nforwarded\n',
        'notes/n1.md': '---\nprovenance_trust: untrusted\n---\nuntrusted\n',
        'notes/n2.md': 'plain\n', 'x/n2.md': '---\nprovenance_manifest: "00-meta/provenance/x.json"\n---\nderived\n',
        'notes/n3.md': 'plain\n', 'notes/plain.md': 'plain\n', 'notes/gone.md': 'plain\n',
        # The scan keeps the first 128 links: a risky 128th counts, a risky 129th does not.
        'priv/after127.md': '[[plain]]' * 127 + '[[risky]]\n', 'priv/after128.md': '[[plain]]' * 128 + '[[risky]]\n',
        'priv/brackets.md': '[' * 200 + '\n[[risky|x]]\n',
    }
    for number, start in enumerate(range(0, len(lines), per_note)):
        notes[f'priv/p{number}.md'] = '\n'.join(lines[start:start + per_note]) + '\n'
    config = {'privileged_notes': sorted(path for path in notes if path.startswith('priv/') and path != 'priv/p0.md'),
              'hot_note': 'priv/p0.md'}
    return notes, config


# --- The credential detector: what its comparisons feed it. ---
KELVIN, LONG_S, DOTLESS_I, DOTTED_I = '\u212a', '\u017f', '\u0131', '\u0130'  # IGNORECASE folds them into [a-z]
# Letters (with the 'eyJ' of a JWT), a digit, the punctuation the branches
# name, both quotes, whitespace with a line feed, and a Hangul syllable.
CREDENTIAL_CHARS = 'aeyJ0:/@.+-_="\' \n가'
# Runs before a URL tail: scheme characters ('+', '.' and '-' are the ones that
# are not word characters), word characters outside the scheme class ('_' and
# a Hangul syllable), the Kelvin sign, and characters that end a scheme.
URL_HEAD = ('a', KELVIN, '0', '-', '+', '.', '_', '가', ' ', ':', '/', '@')
URL_TAILS = ('://u:p@h', '://:p@', '://u@h', '://u:@h', 'a://u:p@h', ':/u:p@h', '://u:p', '')
# Runs before the second and third JWT parts: starts (a later one after '-'),
# seven characters and one more toward the eight a first part needs, and what
# ends or splits a run. The tails close a JWT, cut its second part short, or
# end its third part with and without a word boundary.
JWT_HEAD = ('eyJ', 'EyJ', '-eyJ', 'a', 'aaaaaaa', '-', '_', '.', '가', ' ')
JWT_TAILS = ('.' + 'b' * 8 + '.' + 'c' * 8, '.' + 'b' * 7 + '.' + 'c' * 8,
             '.' + 'b' * 8 + '.' + 'c' * 8 + '가', '.' + 'b' * 8 + '.' + 'c' * 8 + '-가')
# Labels, whitespace of several kinds, quotes and separators around a value.
ASSIGNMENT = ('password', 'secret', '암호', 'api 키', ' ', '\t', '\n', '\u3000', '"', "'", ':', '=', 'abcd', ',')
# (text, verdict): one synthetic shape per branch, near misses, the rewritten
# branches' starts and whitespace runs, and IGNORECASE folding. Every value is
# a run of letters or digits written here, not a credential.
JWT_REST = '.' + 'b' * 8 + '.' + 'c' * 8  # the second and third parts of a JWT
CREDENTIAL_CASES = (
    ('sk-' + 'a' * 16, True), ('sk-' + 'a' * 15, False), ('ghp_' + 'a' * 20, True), ('AKIA' + 'A' * 16, True),
    ('apikey_' + 'f' * 32 + '_' + 'e' * 64, True), ('-----BEGIN RSA PRIVATE KEY-----', True),
    ('1' * 9 + ':' + 'A' * 35, True), ('xoxb-' + '1' * 10, True), ('xapp-1-' + 'A' * 10, True),
    ('AIza' + 'S' * 35, True), ('hooks.slack.com/services/' + 'T' * 20, True), ('Authorization: Bearer abcdefgh', True),
    # JWT: a start inside a run follows '-'; one with eight characters after its 'eyJ' is enough.
    ('eyJ' + 'a' * 8 + JWT_REST, True), ('eyJ' + 'a' * 7 + JWT_REST, False), ('-eyJ' * 3 + JWT_REST, True),
    ('eyJ' + 'a' * 8 + '-eyJ' + 'b' * 8 + JWT_REST, True), ('eyJ' + 'a' * 8 + '-eyJ' + 'b' * 7 + JWT_REST, True),
    ('_eyJ' + 'a' * 8 + JWT_REST, False), ('EYJ' + 'a' * 8 + JWT_REST + '-가', True),
    ('eyJ' + 'a' * 8 + JWT_REST + '가', False), ('-eyJ' * 100, False), ('eyJ' + 'a' * 8 + JWT_REST + '-eyJ' * 50, True),
    # URL: a scheme starts at a word boundary, inside a run only after '+', '.' or '-'.
    ('postgres://admin:fake@db', True), ('a-b://u:p@h', True), ('x+y.z-w://u:p@h', True), ('a-1://u:p@h', True),
    ('+a://u:p@h', True), ('1a://u:p@h', False), ('_a://u:p@h', False), ('가a://u:p@h', False), ('-1://u:p@h', False),
    ('a://u@h', False), ('a://u:@h', False), ('a:// u:p@h', False), ('a-' * 50 + '://u:p@h', True),
    ('a-' * 50 + '://u:p', False),
    # Whitespace and one optional quote between a label and ':' or '='.
    ('password = abcd', True), ('password "= abcd', True), ('password' + ' ' * 9 + "'" + '\t' * 9 + ':abcd', True),
    ('password "":abcd', False), ('password\n\n: abcd', True), ('secret' + ' ' * 40, False),
    ('client-secret \u3000=x1y2', True), ('암호 : abcd', True), ('암호\u3000"\u3000=abcd', True), ('비밀 번호: abcd', True),
    ('api 키 = abcd', True), ('암호' + ' ' * 40 + 'abcd', False),
    # IGNORECASE folds the long s, dotless i, dotted capital I and Kelvin sign.
    ('pa' + LONG_S * 2 + 'word=abcd', True), ('author' + DOTLESS_I + 'zation: bearer abcdefgh', True),
    ('api_' + KELVIN + 'ey=abcd', True), (KELVIN + 'a://u:p@h', True), ('a.' + LONG_S + '://u:p@h', True),
    ('x-' + DOTLESS_I + '://u:p@h', True), (DOTTED_I + '://u:p@h', True), ('_' + KELVIN + '://u:p@h', False),
    ('eyJ' + KELVIN * 8 + JWT_REST, True),
)


def joined(heads, tails):
    for head in heads:
        for tail in tails:
            yield head + tail


def credential_texts(count, seed=20261027):
    """Deterministic texts of synthetic credential shapes, near misses and pieces, built in memory.

    Every value is a run of letters, digits or hex digits drawn here; none is a
    real credential.
    """
    rng = random.Random(seed)
    letters, hexdigits = 'abcdefghijklmnopqrstuvwxyz', '0123456789abcdef'

    def run(alphabet, low, high):
        return ''.join(rng.choice(alphabet) for _ in range(rng.randint(low, high)))

    # Lengths straddle each branch's minimum, so both verdicts occur.
    shapes = (
        lambda: 'sk' + rng.choice('-_') + run(letters + '-_', 14, 18),
        lambda: 'gh' + rng.choice('pousr') + '_' + run(letters, 18, 22),
        lambda: 'github_pat_' + run(letters + '_', 18, 22),
        lambda: 'AKIA' + run('ABCDEFGHIJ0123456789', 15, 17),
        lambda: rng.choice(('ts', 'tsk', 'typesafe')) + rng.choice('-_') + run(letters, 14, 18),
        lambda: 'eyJ' + run(letters + '-', 6, 10) + '.' + run(letters, 6, 10) + '.' + run(letters, 6, 10),
        lambda: 'apikey_' + run(hexdigits, 31, 33) + '_' + run(hexdigits, 63, 65),
        lambda: '-----BEGIN ' + rng.choice(('', 'RSA ', 'EC ', 'OPENSSH ')) + 'PRIVATE KEY-----',
        lambda: run('0123456789', 4, 13) + ':' + run(letters + '-_', 34, 36),
        lambda: 'xox' + rng.choice('abposrz') + '-' + run(letters + '-', 8, 12),
        lambda: 'xapp-' + rng.choice('1x') + '-' + run(letters, 8, 12),
        lambda: 'hooks.slack.com/services/' + run(letters + '/', 18, 22),
        lambda: 'AIza' + run(letters + '_-', 34, 36),
        lambda: (run(letters + '+.-', 1, 6) + '://' + run(letters, 0, 4) + rng.choice((':', ''))
                 + run(letters, 0, 4) + rng.choice(('@', '', '/'))),
        lambda: (rng.choice(('password', 'passwd', 'secret', 'api_key', 'MY_API-KEY', 'access-token', 'client_secret',
                             'authorization', 'Authorization')) + run(' \t"\'', 0, 4) + rng.choice(':=')
                 + run(' "\'', 0, 3) + run(letters + ',;', 2, 9)),
        lambda: (rng.choice(('비밀번호', '비밀 번호', '비번', '패스워드', '암호', '토큰', '인증키', '액세스 키', '시크릿', 'api키'))
                 + run(' \u3000"\'', 0, 3) + rng.choice(':=\uff1a') + run(' "', 0, 2) + run(letters + '!,;', 2, 7)),
    )
    # Separators, folding and disguise characters (normalization removes
    # U+200B and folds full-width letters), and runs of the rewritten branches.
    pieces = (' ', '\n', '\t', '-', '.', '+', '_', ':', '/', '@', '"', "'", '=', ',', ';', 'a', 'Z', '0', '가', '암호',
              KELVIN, LONG_S, DOTLESS_I, DOTTED_I, '\u3000', '\xa0', '\u200b', '\uff53\uff4b-', 'eyJ', '://', 'pass',
              'word', 'pa' + LONG_S * 2 + 'word', 'author' + DOTLESS_I + 'zation', 'bearer ', 'basic ', 'a-' * 4,
              '-eyJ' * 3)
    return [''.join(rng.choice(shapes)() if rng.random() < 0.3 else rng.choice(pieces)
                    for _ in range(rng.randint(0, 12))) for _ in range(count)]


CREDENTIAL_TEXTS = tuple(credential_texts(400))


def egress_notes(texts=CREDENTIAL_TEXTS, per_note=4):
    """Notes of the texts, plus assignments that span lines (only the whole-note scan finds them)
    and a path that is itself credential-shaped (its label is replaced)."""
    notes = {f'notes/c{number}.md': '\n'.join(texts[start:start + per_note]) + '\n'
             for number, start in enumerate(range(0, len(texts), per_note))}
    notes.update({'notes/span.md': 'plain\npassword\n= abcd1234\n', 'notes/korean-span.md': '암호\n:\nabcd\n',
                  'notes/sk-' + 'a' * 20 + '.md': 'a-b://u:p@h\n'})
    return notes


def ask_request(text):
    return json.dumps({'schema_version': 1, 'context': {'kind': 'selected_text', 'text': text},
                       'questions': [{'id': 'claim', 'type': 'choice', 'instructions': 'Is it supported?',
                                      'criteria': {'yes': 'Supported', 'unknown': 'Insufficient evidence'},
                                      'abstain': 'unknown'}]}, ensure_ascii=False).encode('utf-8')


def judge_request(excerpt, instructions='Is it supported?'):
    return {'schema_version': 1, 'task': 'support',
            'sources': [{'path': '20-knowledge/evidence.md', 'excerpt': excerpt}],
            'questions': [{'id': 'claim', 'instructions': instructions,
                           'choices': {'yes': 'Supported', 'unknown': 'Insufficient evidence'}, 'abstain': 'unknown'}]}


def judge_vault(root, texts):
    """A vault whose evidence note holds each text on its own lines."""
    return write_vault(root, {'00-meta/vault-config.json': json.dumps({'vault_name': 'Detector vault'}),
                              '20-knowledge/evidence.md': '# Evidence\n' + '\n'.join(texts) + '\n'})


def excerpts(texts):
    """The texts vault_judge accepts as excerpts: not blank, no control characters but tab and line feed."""
    return [text for text in texts if text.strip() and len(text) <= 8192
            and not any(ord(char) < 32 and char not in '\n\t' or ord(char) == 127 for char in text)]


@contextmanager
def recorded(test, pair):
    """Record what a caller passes to the new helper and hold it to the oracle."""
    real, calls = pair.new(), []

    def record(value):
        calls.append(value)
        return real(value)

    with mock.patch.object(pair.module, pair.attr, record):
        yield calls
    test.assertTrue(calls, f'{pair.name}: the caller never reached the helper')
    for value in calls:
        if pair.newline_free:
            test.assertNotIn('\n', value, pair.name)
        test.assertEqual(real(value), pair.oracle(value), (pair.name, value))


# oracles() name for the credential pattern. It is not a Pair: the callers
# search a pattern object rather than call a helper.
DETECTOR = 'credential detector'


@contextmanager
def oracles(*names):
    """Run the callers with the former regexes in place of the new helpers."""
    with ExitStack() as stack:
        for name in names:
            if name == DETECTOR:
                # _matches_sensitive reads the module global on every call; the
                # aliases in jev_ask and vault_judge are never searched.
                stack.enter_context(mock.patch.object(jev_client, 'SENSITIVE', OLD_SENSITIVE))
                continue
            pair = PAIR[name]
            stack.enter_context(mock.patch.object(pair.module, pair.attr, pair.oracle))
        yield


@contextmanager
def recorded_detector(test):
    """Record what the callers search with the credential pattern and hold each verdict to the oracle."""
    real, calls = jev_client.SENSITIVE, []

    class Recording:
        def search(self, text):
            calls.append(text)
            return real.search(text)

    with mock.patch.object(jev_client, 'SENSITIVE', Recording()):
        yield calls
    test.assertTrue(calls, 'the caller never searched with SENSITIVE')
    test.assertEqual([text for text in calls if verdict(text) != former_verdict(text)], [])


def ledger_note(heading):
    return (heading + '\n\n| subject | relation | value | source | status |\n'
            '| --- | --- | --- | --- | --- |\n| Atlas | retention | 12 |  |  |\n')


def tasks_note(headings):
    return ''.join(f'{heading}\n- [ ] Item {number}\n' for number, heading in enumerate(headings)).encode('utf-8')


class TierACasesTests(unittest.TestCase):
    """Tier A: fixed cases agree with the oracle and keep the regex behaviour."""

    def test_commonmark_examples_keep_the_former_regex_results(self):
        self.assertEqual([case['example'] for case in COMMONMARK_CASES], list(range(62, 80)))
        for case in COMMONMARK_CASES:
            for line, expected, commonmark in zip(case['lines'], case['expected'], case['commonmark'],
                                                  strict=True):
                with self.subTest(example=case['example'], line=line):
                    self.assertEqual(oracle_atx(line), expected)
                    self.assertEqual(vault_ssot._atx_heading(line), expected)
                    self.assertEqual(vault_state._atx_heading(line), expected)

    def test_deviations_from_commonmark_are_exactly_the_documented_examples(self):
        def stripped(result):
            return None if result is None else (result[0], result[1].strip())

        deviating = []
        for case in COMMONMARK_CASES:
            differs = any(stripped(expected) != commonmark
                          for expected, commonmark in zip(case['expected'], case['commonmark'], strict=True))
            with self.subTest(example=case['example']):
                self.assertEqual(differs, case['cm_deviates'])
            if differs:
                deviating.append(case['example'])
        self.assertEqual(tuple(deviating), DEVIATING_EXAMPLES)

    def test_vault_cases_keep_the_former_regex_results(self):
        for line, expected in VAULT_CASES:
            with self.subTest(line=line):
                self.assertEqual(oracle_atx(line), expected)
                self.assertEqual(vault_ssot._atx_heading(line), expected)
                self.assertEqual(vault_state._atx_heading(line), expected)

    def test_section_labels_keep_their_ledger_and_task_meaning(self):
        def status(heading):
            return vault_ssot.parse_ledger(ledger_note(heading))['tables'][0]['section_status']

        # A closing run followed by spaces stays in the label, so it is not '확정'.
        self.assertEqual(status('## 확정 ###   '), '')
        self.assertEqual(status('## 확정 ###'), 'confirmed')
        self.assertEqual(status('#\u3000확정'), 'confirmed')
        self.assertEqual(status('####### 확정'), '')
        items = vault_state._items(tasks_note(['### Blocked ###', '## 보류 ##  ', '# 차단#']), 'tasks')
        self.assertEqual([item['state'] for item in items], ['blocked', 'pending', 'blocked'])

    def test_other_line_parsers_keep_their_fixed_cases(self):
        cases = {
            'lint heading text': ('# a', '#  ', '# a ##', '    # a', '#\ta\t'),
            'lint closing run': ('a ##', 'a#', 'a ## ', 'a ## b', ' #', '#'),
            'lint date field': ('created: 2026-10-27', 'updated:', 'created:  x # c  ', 'Created: x'),
            'lint comment split': ('2026-10-27 # note', 'a#b', 'a #b #c', '# a', ' #'),
            'provenance header field': ('key: value', 'a_b :  "x" ', 'Key: v', 'k:', 'k v'),
            'rule heading': ('## Rule', '#### Rule', '#  ', '# Rule (note) — tail', '#Rule'),
            'rule parenthesized note': ('a (b) c', 'a  (b', 'a) (b)', '(x)(y)'),
            'rule dash tail': ('a — b', 'a-b', 'a - b - c', '- a', 'a —'),
            'recall title value': ('title: x', ' TITLE : x ', 'title:', 't\u0131tle: x', 'titles: x'),
            'recall heading label': ('  # a #', '# a', '#  ', '\t## a ## ', '####### a'),
        }
        for name, values in cases.items():
            pair = PAIR[name]
            for value in values:
                with self.subTest(pair=name, value=value):
                    self.assertEqual(pair.new()(value), pair.oracle(value))

    def test_scanners_keep_their_fixed_cases(self):
        # The code-span regexes shortened an opening run that no run of the same
        # length closed: '```a``' closes on two of its three backticks, which
        # neither CommonMark nor a scan that pairs whole runs would do.
        cases = {
            'lint code removal': (
                ('a `b` c', 'a  c'), ('``a`b``', ''), ('```a``', ''), ('`a``', '`'), ('````', ''), ('```', '`'),
                ('`` `a` ``', ''), ('x ``` y', 'x ` y'), ('`a` ``b`` `', '  `'), ('`a\n`', '`a\n`'),
                ('[[x]] `[[y]]`', '[[x]] ')),
            'lint link texts': (
                ('[[a]] ![[b]] [[c#d|e]]', ['a', 'b', 'c#d|e']), ('[[[a]]', ['[a']), ('![[[a]]', ['[a']),
                ('!![[a]]', ['a']), ('[[a]]]', ['a']), ('[[a]][[b]]', ['a', 'b']), ('[[]] [[a]b]]', []),
                ('[[a\n]]', []), ('[[a\n[[b]]', ['b'])),
            'lint heading link text': (
                ('[text](https://x.test/a) tail', 'text tail'), ('[a](b) [c](d)', 'a c'), ('[a]()', 'a'),
                ('[[a](b)', '[a'), ('[a]((b))', 'a)'), ('[a](b\n)', 'a'), ('[a](b', '[a](b'),
                ('[[a]](b)', '[[a]](b)'), ('[](x)', '[](x)'), ('[a] (x)', '[a] (x)')),
            'healthcheck frontmatter': (
                ('---\ntitle: x\n---\nbody', ('title: x', 'body')),
                ('---\r\ntitle: x\r\n---\r\n\r\nbody\r\n', ('title: x\r', 'body\r\n')),
                ('--- \n a\n---  \n\n\nbody', (' a', 'body')), ('---\n\n---\n', ('', '')), ('---\n \n---', (' ', '')),
                ('---\na\n---b', ('a', 'b')), ('---\na\n----\nb', ('a', '-\nb')), ('---\n---\n', (None, '---\n---\n')),
                ('---\n\n\n\n\n', (None, '---\n\n\n\n\n')), ('----\nx\n---\n', (None, '----\nx\n---\n')),
                ('x\n---\n---\n', (None, 'x\n---\n---\n'))),
            'ssot code spans': (
                ('`notes/a.md`', [(0, 12, 'notes/a.md')]), ('`` notes/a.md ``', [(0, 16, ' notes/a.md ')]),
                ('```notes/a.md``', [(0, 15, '`notes/a.md')]), ('`a``b`', [(0, 3, 'a'), (3, 6, 'b')]),
                ('``a`', [(0, 4, '`a')]), ('```', [(0, 3, '`')]), ('````', [(0, 3, '`')]), ('``', []),
                ('`` `x` ``', [(0, 9, ' `x` ')]), ('`a\nb`', [])),
            # WIKILINK_RE kept a lone ']' and line feeds in the target, ended it at
            # '|', '#' or ']]', and let the tail after '|' or '#' run to the first ']'.
            'healthcheck wikilinks': (
                ('[[a]] ![[b|c]] [[d#e]]', [(0, 5, 'a'), (7, 14, 'b'), (15, 22, 'd')]),
                ('[[a]b]]', [(0, 7, 'a]b')]), ('[[[a]]', [(0, 6, '[a')]), ('[[a\nb]]', [(0, 7, 'a\nb')]),
                ('[[a#[[b]]', [(0, 9, 'a')]), ('[[a#b]x]]', []), ('[[a#b]x[[c]]', [(7, 12, 'c')]),
                ('[[a|b\n|c]]', [(0, 10, 'a')]), ('[[#a]]', []), ('[[]]', []), ('[[a]]]', [(0, 5, 'a')]),
                ('[[a', []), ('[[a|b', [])),
            # The privileged-note scan stopped at the first ']' or line feed and kept 128 matches.
            'provenance link texts': (
                ('[[a]] ![[b]] [[c#d|e]]', ['a', 'b', 'c#d|e']), ('[[[a]]', ['[a']), ('[[a]b]]', []),
                ('[[a\n]]', []), ('[[a\n[[b]]', ['b']), ('[[]]', []), ('[[x]]' * 130, ['x'] * 128)),
            # The bridge's substitution closed a '[[' only at the first ']' after it,
            # line feeds included, and left everything else as it was.
            'telegram wiki links': (
                ('[[a]] b [[c|d]]', '<i>a</i> b <i>c|d</i>'), ('[[[a]]', '<i>[a</i>'), ('[[a]b]]', '[[a]b]]'),
                ('[[]] [[a]]]', '[[]] <i>a</i>]'), ('[[a\n]]', '<i>a\n</i>'), ('[[a', '[[a'), ('[[a]', '[[a]'),
                ('&lt;[[x]]&gt; [[y]][[z]]', '&lt;<i>x</i>&gt; <i>y</i><i>z</i>'),
                ('[' * 9 + 'a]]', '<i>' + '[' * 7 + 'a</i>')),
        }
        self.assertEqual(tuple(cases), SCANNERS)
        for name, values in cases.items():
            pair = PAIR[name]
            for value, expected in values:
                with self.subTest(pair=name, value=value):
                    self.assertEqual(pair.oracle(value), expected)
                    self.assertEqual(pair.new()(value), expected)


class TierBEquivalenceTests(unittest.TestCase):
    """Tier B: bounded-exhaustive equality with the former regexes."""

    def test_regex_whitespace_is_the_strip_predicate_on_every_code_point(self):
        # The helpers replace \s+ and \s* with lstrip()/rstrip()/strip(). For str
        # patterns re documents \s as str.isspace; this checks it, and that the
        # argument-free strips use the same test, over all 0x110000 code points.
        everything = ''.join(map(chr, range(0x110000)))
        by_regex = set(re.findall(r'\s', everything))
        self.assertEqual(by_regex, {c for c in everything if c.isspace()})
        self.assertEqual(by_regex, {c for c in everything if not c.strip()})
        self.assertEqual(by_regex, {c for c in everything if not c.lstrip()})
        self.assertEqual(by_regex, {c for c in everything if not c.rstrip()})
        self.assertTrue(set(WHITESPACE) <= by_regex)
        self.assertFalse({'#', 'a', '가', ':', '(', ')', '-', '—'} & by_regex)

    def test_every_helper_equals_its_oracle_on_all_short_strings(self):
        for pair in PAIRS:
            for alphabet, length in pair.runs:
                with self.subTest(pair=pair.name, alphabet=alphabet, length=length):
                    count, found = mismatches(pair, strings(alphabet, length, pair.prefixes))
                    expected_count = len(pair.prefixes) * sum(len(alphabet) ** n for n in range(length + 1))
                    self.assertEqual(count, expected_count)
                    self.assertEqual(found, [])

    def test_wikilink_targets_are_the_former_findall(self):
        # The healthcheck and vault_links call wikilink_targets(), group 1 of
        # each match in order, which is what WIKILINK_RE.findall returned.
        values = itertools.chain(strings(LINKS, 5), LINK_LINES, SCAN_LINES, ['\n'.join(LINK_LINES)])
        self.assertEqual([value for value in values
                          if vault_healthcheck.wikilink_targets(value) != OLD_WIKILINK.findall(value)], [])

    def test_privileged_link_scan_stops_where_the_former_slice_did(self):
        # The scan stops at `limit` texts where the former code listed every
        # match and sliced. Joined link pieces give several matches per string.
        pair = PAIR['provenance link texts']
        found = [(text, limit) for text in strings(('[[', ']]', '[', ']', '\n', 'a'), 6) for limit in range(4)
                 if pair.new()(text, limit) != pair.oracle(text, limit)]
        self.assertEqual(found, [])
        for links in (124, 125, 126, 300):  # 127, 128, 129 and 303 matches
            text = '[[a]x [[b]] [' * 3 + ''.join(f'[[n{i}|x]] ' if i % 7 else f'[[[n{i}]]\n' for i in range(links))
            every = [match[1] for match in OLD_PRIVILEGED_LINK.finditer(text)]
            with self.subTest(links=links):
                self.assertEqual(len(every), links + 3)
                self.assertEqual(pair.new()(text), every[:128])

    def test_heading_forms_differ_once_a_line_feed_is_inside(self):
        # The heading forms differ from their oracles once a line feed is inside
        # ('.' stops there and '$' matches before it), so their callers must never
        # pass one; the tests below prove they cannot.
        for name in ('ssot ATX heading', 'lint heading text', 'rule heading', 'recall heading label',
                     'rule dash tail'):
            pair = PAIR[name]
            with self.subTest(pair=name):
                _, found = mismatches(pair, strings(' \n#a—-', 4), limit=1)
                self.assertTrue(found, name)


def verdict_mismatches(texts, limit=5):
    """Return (count, detected, first texts whose verdict differs from the oracle's)."""
    count = detected = 0
    found = []
    for text in texts:
        count += 1
        expected = former_verdict(text)
        detected += expected
        if verdict(text) != expected and len(found) < limit:
            found.append(text)
    return count, detected, found


class CredentialDetectorEquivalenceTests(unittest.TestCase):
    """The rewritten jev_client.SENSITIVE finds a match in exactly the texts the former one did.

    A missed text would let a credential leave the machine; an extra one would
    change egress warnings and Jev refusals. Callers use only the verdict.
    """

    @classmethod
    def setUpClass(cls):
        everything = ''.join(map(chr, range(0x110000)))
        cls.chars = {pattern: set(re.findall(pattern, everything, re.IGNORECASE))
                     for pattern in (r'\w', r'[a-z]', r'[a-z0-9+.-]', r'[a-zA-Z0-9_-]', 'e|y|j')}

    def test_classes_the_rewritten_branches_rely_on(self):
        # A scheme starts at a word boundary before [a-z]. Inside a run of
        # [a-z0-9+.-] that boundary follows '+', '.' or '-', the class's only
        # characters that are not word characters; inside a run of
        # [a-zA-Z0-9_-] a JWT's 'eyJ' can start only after '-'. IGNORECASE adds
        # the dotted capital I, dotless i, long s and Kelvin sign to [a-z].
        word, letters = self.chars[r'\w'], self.chars[r'[a-z]']
        self.assertEqual(letters - set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'),
                         {DOTTED_I, DOTLESS_I, LONG_S, KELVIN})
        self.assertLessEqual(letters, word)
        self.assertEqual(self.chars[r'[a-z0-9+.-]'] - word, {'+', '.', '-'})
        self.assertEqual(self.chars[r'[a-zA-Z0-9_-]'] - word, {'-'})
        self.assertLessEqual(self.chars['e|y|j'], self.chars[r'[a-zA-Z0-9_-]'] & word)
        self.assertEqual(jev_client.SENSITIVE.flags, OLD_SENSITIVE.flags)

    def test_fixed_cases_keep_the_former_verdicts(self):
        for text, expected in CREDENTIAL_CASES:
            with self.subTest(text=text[:40]):
                self.assertIs(former_verdict(text), expected)
                self.assertIs(verdict(text), expected)

    def test_every_short_string_gets_the_former_verdict(self):
        count, _, found = verdict_mismatches(strings(CREDENTIAL_CHARS, 5))
        self.assertEqual(count, sum(len(CREDENTIAL_CHARS) ** n for n in range(6)))
        self.assertEqual(found, [])

    def test_rewritten_branches_get_the_former_verdict_on_their_pieces(self):
        # Every run before a URL or JWT tail, and every arrangement of labels,
        # whitespace, quotes and separators, through the whole detector.
        for name, texts in (('url', joined(strings(URL_HEAD, 4), URL_TAILS)),
                            ('jwt', joined(strings(JWT_HEAD, 5), JWT_TAILS)),
                            ('assignment', strings(ASSIGNMENT, 5))):
            with self.subTest(domain=name):
                count, detected, found = verdict_mismatches(texts)
                self.assertTrue(0 < detected < count)
                self.assertEqual(found, [])

    def test_random_texts_get_the_former_verdict(self):
        texts = CREDENTIAL_TEXTS + tuple(credential_texts(40_000, seed=1027))
        count, detected, found = verdict_mismatches(texts)
        self.assertTrue(count // 5 < detected < count)
        self.assertEqual(found, [])

    def test_normalized_and_nested_values_get_the_former_verdict(self):
        # _matches_sensitive also searches the NFKC copy without invisible
        # splitters, and contains_sensitive every string of a JSON-like value.
        texts = ('pass\u200bword=abcd1234', '\uff53\uff4b-' + 'a' * 16, '\uff50assword\uff1aabcd') + CREDENTIAL_TEXTS
        values = [{'a': [text], text: 1} for text in texts[:100]] + [{'password': text} for text in texts[:20]]
        current = ([jev_client._matches_sensitive(text) for text in texts],
                   [jev_client.contains_sensitive(value) for value in values])
        with oracles(DETECTOR):
            former = ([jev_client._matches_sensitive(text) for text in texts],
                      [jev_client.contains_sensitive(value) for value in values])
        self.assertEqual(current, former)
        self.assertTrue(any(current[0]) and not all(current[0]))
        self.assertTrue(any(raw != normalized for raw, normalized in zip(
            [verdict(text) for text in texts], current[0])))


class CallerPreconditionTests(unittest.TestCase):
    """Callers pass only text without a line feed, and the helper equals the oracle there."""

    def notes(self):
        lines = [variant for line in LINES[:60] for variant in [line, *separated(line)]]
        return '\n'.join(lines) + '\n'

    def test_ledger_and_task_callers_split_bytes_into_line_feed_free_lines(self):
        text = self.notes()
        with recorded(self, PAIR['ssot ATX heading']):
            vault_ssot.parse_ledger(text)
        with recorded(self, PAIR['state ATX heading']):
            vault_state._items(text.encode('utf-8'), 'tasks')
        with recorded(self, PAIR['state ATX heading']):
            vault_state._items(tasks_note(['## Blocked\r', '# a\x85b', '## 보류\u2028x']), 'tasks')

    def test_lint_callers_pass_str_splitlines_lines(self):
        body = self.notes()
        front = '---\n' + ''.join(f'created: {line}\n' for line in LINES[:40]) + ''.join(
            f'updated:{mark}2026-10-27 # c\n' for mark in BOUNDARIES) + '---\n'
        notes = {'a.md': front + body + '[[b#alpha]]\n', 'b.md': body}
        for name in ('lint heading text', 'lint closing run', 'lint date field', 'lint comment split'):
            with self.subTest(pair=name), recorded(self, PAIR[name]):
                lint.analyze_notes(notes, {})

    def test_provenance_header_lines_come_from_str_splitlines(self):
        text = '---\n' + ''.join(f'key_{n}: {line}\n' for n, line in enumerate(LINES[:30])) + ''.join(
            f'k{mark}: v\n' for mark in BOUNDARIES) + '---\nbody\n'
        with recorded(self, PAIR['provenance header field']):
            vault_provenance._header_fields(text)
        with recorded(self, PAIR['provenance header field']):
            vault_provenance.analyze_notes({'20-knowledge/a.md': text}, {})

    def test_rule_callers_pass_str_splitlines_lines(self):
        body = self.notes() + '## Rule (note) — tail\n### Other - part (x)\n'
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp).resolve()
            (vault / '.claude' / 'rules').mkdir(parents=True)
            (vault / 'CLAUDE.md').write_bytes(body.encode('utf-8'))
            (vault / '.claude' / 'rules' / 'r.md').write_bytes(body.encode('utf-8'))
            for name in ('rule heading', 'rule parenthesized note', 'rule dash tail'):
                with self.subTest(pair=name), recorded(self, PAIR[name]):
                    vault_healthcheck.scan_rules_integrity(vault, vault / '.claude' / 'rules',
                                                           vault / 'CLAUDE.md', 200)

    def test_recall_callers_pass_str_splitlines_lines(self):
        body = self.notes()
        front = '---\n' + ''.join(f'title{mark}: x\n' for mark in BOUNDARIES) + 'title: a  b\n---\n'
        # Lines that are not headings come first, so the title search visits them.
        plain = ''.join(f'#{mark} alpha\n#tag{mark}\n' for mark in BOUNDARIES)
        with recorded(self, PAIR['recall title value']) as calls:
            vault_recall._rank_document('n.md', front + body, 'alpha', ['alpha'])
        self.assertGreater(len(calls), len(BOUNDARIES))
        with recorded(self, PAIR['recall heading label']) as calls:
            vault_recall._rank_document('n.md', plain + body, 'alpha', ['alpha'])
        self.assertGreater(len(calls), len(BOUNDARIES))
        with recorded(self, PAIR['recall heading label']):
            vault_query_expansion.rank_expanded('n.md', plain + body + 'alpha body\n', 'alpha', ['alpha'])
        # Advanced recall titles a candidate without a lexical match, or a note reached through a link,
        # in vault_retrieval._fallback; --expand-query titles alias replacements in _expand_documents
        # (without a mapping it reads no file, so the vault argument is unused).
        for name, text in (('recall title value', front + body), ('recall heading label', plain + body)):
            doc = {'path': 'n.md', 'text': text, 'sha256': '0' * 64}
            with self.subTest(pair=name, caller='_fallback'), recorded(self, PAIR[name]) as calls:
                vault_retrieval._fallback(doc, 'alpha', ['alpha'])
            self.assertGreater(len(calls), len(BOUNDARIES))
            with self.subTest(pair=name, caller='_expand_documents'), recorded(self, PAIR[name]) as calls:
                vault_query_expansion._expand_documents(ROOT, 'alpha', {'n.md': doc})
            self.assertGreater(len(calls), len(BOUNDARIES))
        driven = {('vault_recall', '_rank_document'), ('vault_query_expansion', 'rank_expanded'),
                  ('vault_retrieval', '_fallback'), ('vault_query_expansion', '_expand_documents')}
        # Every shipped function that names a recall title helper is one of the callers driven above,
        # so a new caller fails here until it is driven through recorded() too.
        callers = set()
        for path in (*sorted(SCRIPTS.glob('*.py')), *sorted((ROOT / 'hooks').glob('*.py'))):
            stack = [(ast.parse(path.read_text(encoding='utf-8')), None)]
            while stack:
                node, function = stack.pop()
                for child in ast.iter_child_nodes(node):
                    spelled = (child.id if isinstance(child, ast.Name) else
                               child.attr if isinstance(child, ast.Attribute) else None)
                    if spelled in ('_title', '_title_value', '_heading_label'):
                        callers.add((path.stem, function))
                    stack.append((child, child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                                  else function))
        self.assertEqual(callers, driven | {('vault_recall', '_title')})

    def test_lint_scanners_are_reached_by_the_default_lint(self):
        # The scanners equal their oracles with line feeds too, so their callers
        # need no line-feed precondition; recorded() still holds every value a
        # caller passes to the oracle.
        notes = scan_notes(SCAN_LINES[:80])
        for name in ('lint code removal', 'lint link texts', 'lint heading link text'):
            with self.subTest(pair=name), recorded(self, PAIR[name]):
                lint.analyze_notes(notes, {})

    def test_check_support_passes_each_source_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = support_vault(Path(tmp).resolve() / 'vault', SOURCE_CELLS)
            with recorded(self, PAIR['ssot code spans']) as calls:
                vault_ssot.check_support(vault, 'notes/facts.md')
        self.assertEqual(sorted(set(calls)), sorted(SOURCE_CELLS))

    def test_frontmatter_reaches_full_mode_without_cr_and_staged_mode_with_cr(self):
        # Full mode reads notes in text mode, which turns CR and CRLF into a line
        # feed; --staged decodes the index blob as stored, so a CRLF note keeps
        # its CR. The split equals the oracle either way (held by recorded()).
        if shutil.which('git') is None:
            self.skipTest('git is not available')
        texts = ('---\r\ntitle: "A"\r\n---\r\nbody\r\n', '---\rtitle: "A"\r---\r', '---\ntitle: "A"\n---\n')
        with tempfile.TemporaryDirectory() as tmp:
            vault = health_vault(Path(tmp).resolve() / 'vault', texts)
            with recorded(self, PAIR['healthcheck frontmatter']) as calls:
                full_healthcheck(vault)
            self.assertEqual(len(calls), len(texts))
            self.assertFalse([value for value in calls if '\r' in value])
            for args in (('init', '-q'), ('config', 'core.autocrlf', 'false'), ('add', '--', '.')):
                subprocess.run(['git', *args], cwd=vault, check=True, capture_output=True)
            with recorded(self, PAIR['healthcheck frontmatter']) as calls:
                vault_healthcheck.validate_staged(vault, vault_healthcheck.load_staged_config(vault))
            self.assertEqual(sorted(calls), sorted(texts))

    def test_wikilink_scan_reaches_full_mode_staged_backlinks_and_link_proposals(self):
        # Full mode scans each note without code and the index without fences;
        # --staged scans each index blob with '[[' once a commit deletes or
        # renames a note; vault_links scans the source note as read. The scan
        # has no line-feed precondition, and recorded() holds every value to
        # the oracle.
        def without_code(text):
            return vault_healthcheck.INLINE_CODE_RE.sub('', vault_healthcheck.CODE_FENCE_RE.sub('', text))

        notes = link_notes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'vault'
            with recorded(self, PAIR['healthcheck wikilinks']) as calls:
                full_healthcheck(link_vault(root))
            self.assertLessEqual({without_code(text) for text in notes.values()}, set(calls))
            self.assertIn(vault_healthcheck.CODE_FENCE_RE.sub('', notes['00-meta/index.md']), calls)
            if shutil.which('git') is not None:
                vault = staged_link_vault(root)
                with recorded(self, PAIR['healthcheck wikilinks']) as calls:
                    errors = vault_healthcheck.validate_staged(vault, vault_healthcheck.load_staged_config(vault))
                self.assertIn(without_code(notes['notes/plain.md']), calls)
                self.assertTrue(any(error.startswith('스테이징 차단: notes/plain.md') for error in errors))
            vault = proposal_vault(Path(tmp).resolve() / 'proposals')
            with recorded(self, PAIR['healthcheck wikilinks']) as calls:
                vault_links.propose_links(vault, '20-knowledge/source.md', 'memory storage')
            self.assertIn((vault / '20-knowledge' / 'source.md').read_bytes().decode('utf-8'), calls)

    def test_privileged_notes_reach_the_link_scan(self):
        # privileged_notes and hot_note name the notes; the default lint runs the
        # provenance analysis on every approved original.
        notes, config = privileged_notes()
        privileged = [notes[path] for path in sorted({*config['privileged_notes'], config['hot_note']})]
        with recorded(self, PAIR['provenance link texts']) as calls:
            vault_provenance.analyze_notes(notes, config)
        self.assertEqual(calls, privileged)
        with recorded(self, PAIR['provenance link texts']) as calls:
            lint.analyze_notes(notes, config)
        self.assertEqual(calls, privileged)

    def test_telegram_messages_reach_the_wiki_link_conversion(self):
        # tg_send cuts a message into chunks of at most TG_CHUNK characters and
        # converts each chunk line by line.
        message = '\n'.join(LINK_LINES[:40]) + '\n' + '[' * 5_000 + '[[a]]\n[[b]]'
        with mock.patch.object(jarvis_bridge, 'tg_call', return_value={'ok': True}), \
                mock.patch.object(jarvis_bridge, 'log'), recorded(self, PAIR['telegram wiki links']) as calls:
            self.assertTrue(jarvis_bridge.tg_send('fake-token', 111, message))
        self.assertIn('[' * jarvis_bridge.TG_CHUNK, calls)
        self.assertIn('[[b]]', calls)

    def test_egress_lint_and_jev_paths_search_with_the_detector(self):
        # The egress scan searches each line, then the whole note, and the path
        # label of a finding; jev_ask and vault_judge search each string they
        # would send. recorded_detector() holds every searched text to the oracle.
        note = 'plain line\n' + 'a-' * 40 + '\n' + '-eyJ' * 20 + '\n암호' + ' ' * 40 + '\npassword\n= abcd1234\n'
        self.assertFalse(any(former_verdict(line) for line in note.splitlines()))
        with recorded_detector(self) as calls:
            findings = vault_egress.scan_text(note, 'notes/a.md')
        self.assertEqual([(row['code'], row['line']) for row in findings], [('egress-secret', 0)])
        self.assertLessEqual({*note.splitlines(), note, 'notes/a.md'}, set(calls))
        with recorded_detector(self) as calls:
            lint.analyze_notes({'notes/a.md': note}, {})
        self.assertIn(note, calls)
        texts = excerpts(CREDENTIAL_TEXTS[:12])
        with recorded_detector(self) as calls:
            for text in texts:
                jev_ask.prepare(ask_request(text), api_key='')
        self.assertLessEqual(set(texts), set(calls))
        with tempfile.TemporaryDirectory() as tmp:
            vault = judge_vault(Path(tmp).resolve(), texts)
            with recorded_detector(self) as calls:
                for text in texts:
                    vault_judge.prepare(vault, judge_request(text))
        self.assertLessEqual(set(texts), set(calls))


class DefaultOutputUnchangedTests(unittest.TestCase):
    """Whole caller outputs equal the same runs with the former regexes patched in."""

    def same_with_oracles(self, names, run):
        current = run()
        with oracles(*names):
            former = run()
        self.assertEqual(current, former)
        return current

    def test_ledger_parse_and_check_are_unchanged(self):
        notes = [ledger_note(line) for line in LINES] + [ledger_note('# a') + ledger_note(line) for line in LINES[:80]]
        result = self.same_with_oracles(('ssot ATX heading',), lambda: [
            (vault_ssot.parse_ledger(text)['tables'][0]['section_status'],
             vault_ssot.check_ledger(text, path='notes/facts.md')) for text in notes])
        confirmed = sum(status == 'confirmed' for status, _ in result)
        self.assertTrue(0 < confirmed < len(result))

    def test_handoff_projection_dry_run_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp).resolve()
            files = {
                '00-meta/vault-config.json': json.dumps({'handoff_note': 'notes/handoff.md'}).encode(),
                'notes/handoff.md': b'Narrative\n',
                'notes/log.md': b'- 2026-10-27 | Codex | [ops] fixture\n',
                'notes/tasks.md': tasks_note([*LINES[:120], '### Blocked ###', '## 차단', '# Other']),
            }
            for relative, data in files.items():
                (vault / relative).parent.mkdir(parents=True, exist_ok=True)
                (vault / relative).write_bytes(data)
            result = self.same_with_oracles(('state ATX heading',), lambda: vault_state.project_handoff(
                vault, 'notes/log.md', 'notes/tasks.md', 'notes/handoff.md'))
        states = {item['state'] for item in result['projection']['items']}
        self.assertEqual(states, {'blocked', 'pending'})

    def test_lint_warnings_are_unchanged(self):
        # Chunks keep each run under the 200-warning cap, so no finding is cut.
        headings = '\n'.join(LINES) + '\n'
        chunks = [LINES[start:start + 80] for start in range(0, len(LINES), 80)]
        runs = [{'a.md': '---\n' + ''.join(f'{key}: {line}\n' for line in chunk for key in ('created', 'updated'))
                 + '---\n' + ''.join(f'[[b#{line.strip(" #")}]]\n' for line in chunk), 'b.md': headings}
                for chunk in chunks]
        names = ('lint heading text', 'lint closing run', 'lint date field', 'lint comment split')
        result = self.same_with_oracles(names, lambda: [lint.analyze_notes(notes, {}) for notes in runs])
        codes = {item['code'] for findings in result for item in findings}
        self.assertTrue({'missing-anchor', 'invalid-date'} <= codes)
        self.assertNotIn('lint-incomplete', codes)
        self.assertTrue(any(item['code'] == 'invalid-date' and item['details']['field'] == 'updated'
                            for findings in result for item in findings))

    def test_provenance_header_fields_are_unchanged(self):
        texts = ['---\n' + ''.join(f'{key}: {line}\n' for key in ('provenance_manifest', 'provenance_sources', 'x_y'))
                 + '---\nbody\n' for line in LINES]
        result = self.same_with_oracles(('provenance header field',),
                                        lambda: [vault_provenance._header_fields(text) for text in texts])
        self.assertTrue(any(result))

    def test_rule_duplicate_headings_are_unchanged(self):
        claude = ''.join(f'{line}\n' for line in LINES) + '## Rule (note) — tail\n'
        rules = ''.join(f'{line}\n' for line in LINES[::2]) + '## rule — other\n'
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp).resolve()
            (vault / '.claude' / 'rules').mkdir(parents=True)
            (vault / 'CLAUDE.md').write_text(claude, encoding='utf-8')
            (vault / '.claude' / 'rules' / 'r.md').write_text(rules, encoding='utf-8')
            result = self.same_with_oracles(
                ('rule heading', 'rule parenthesized note', 'rule dash tail'),
                lambda: vault_healthcheck.scan_rules_integrity(vault, vault / '.claude' / 'rules',
                                                               vault / 'CLAUDE.md', 10_000))
        self.assertTrue(result[1])

    def test_recall_titles_and_expanded_ranking_are_unchanged(self):
        documents = ([f'{line}\nalpha body\n' for line in LINES]
                     + [f'---\ntitle: {line}\n---\nalpha body\n' for line in LINES[:150]])
        names = ('recall title value', 'recall heading label')
        result = self.same_with_oracles(names, lambda: [
            (vault_recall._rank_document('n.md', text, 'alpha', ['alpha']),
             vault_query_expansion.rank_expanded('n.md', text, 'alpha', ['alpha'])) for text in documents])
        self.assertTrue(all(ranked is not None for ranked, _ in result))

    def test_lint_scans_keep_the_lint_warnings(self):
        # Chunks keep each run under the 200-warning cap, so no finding is cut.
        runs = [scan_notes(SCAN_LINES[start:start + 40]) for start in range(0, len(SCAN_LINES), 40)]
        names = ('lint code removal', 'lint link texts', 'lint heading link text')
        result = self.same_with_oracles(names, lambda: [lint.analyze_notes(notes, {}) for notes in runs])
        codes = {item['code'] for findings in result for item in findings}
        self.assertTrue({'missing-anchor', 'ambiguous-link', 'missing-link-path'} <= codes)
        self.assertNotIn('lint-incomplete', codes)

    def test_check_support_sources_are_unchanged(self):
        cells = SOURCE_CELLS + tuple(line.strip() for line in SCAN_LINES[:60] if '|' not in line and line.strip())
        references = self.same_with_oracles(('ssot code spans',), lambda: [vault_ssot._references(cell)
                                                                           for cell in cells])
        self.assertEqual({strict for found in references for _, strict in found}, {True, False})
        with tempfile.TemporaryDirectory() as tmp:
            vault = support_vault(Path(tmp).resolve() / 'vault', cells)
            result = self.same_with_oracles(('ssot code spans',),
                                            lambda: vault_ssot.check_support(vault, 'notes/facts.md'))
        self.assertTrue(result['rows_supported'] and result['rows_unsupported'] and result['rows_unresolved'])
        self.assertTrue(result['rows_skipped'])

    def test_healthcheck_frontmatter_verdicts_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = health_vault(Path(tmp).resolve() / 'vault', FRONTMATTER_TEXTS)
            code, _, _, report = self.same_with_oracles(('healthcheck frontmatter',), lambda: full_healthcheck(vault))
        self.assertEqual(code, 1)
        missing = int(re.search(r'## 1\. 프런트매터 누락 — 치명 \((\d+)\)', report)[1])
        self.assertTrue(0 < missing < len(FRONTMATTER_TEXTS))
        config = vault_healthcheck.validate_config(HEALTH_CONFIG)
        errors = self.same_with_oracles(('healthcheck frontmatter',), lambda: [
            vault_healthcheck._staged_schema_errors(f'notes/n{number}.md', text, config)
            for number, text in enumerate(FRONTMATTER_TEXTS)])
        self.assertTrue(any(errors) and not all(errors))

    def test_healthcheck_link_report_is_unchanged(self):
        # The dead-link, orphan and index sections come from the wiki-link scan;
        # section 15 holds the privileged-note warnings of the default lint.
        names = ('healthcheck wikilinks', 'provenance link texts')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'vault'
            code, _, _, report = self.same_with_oracles(names, lambda: full_healthcheck(link_vault(root)))
        self.assertEqual(code, 0)
        counts = {section: int(re.search(rf'^## {section}\. [^\n]*\((\d+)\)$', report, re.M)[1])
                  for section in ('5', '6b', '6c')}
        self.assertTrue(all(counts.values()), counts)
        self.assertIn('[provenance-unresolved]', report)

    def test_staged_backlink_errors_and_warnings_are_unchanged(self):
        # stderr carries the backlink errors of validate_staged and the warnings
        # of the default lint over the index, privileged-note scan included.
        if shutil.which('git') is None:
            self.skipTest('git is not available')
        names = ('healthcheck wikilinks', 'provenance link texts')
        with tempfile.TemporaryDirectory() as tmp:
            vault = staged_link_vault(Path(tmp).resolve() / 'vault')
            code, _, err = self.same_with_oracles(names, lambda: staged_healthcheck(vault))
        referrers = {line.split(' — ')[0].removeprefix('스테이징 차단: ') for line in err.splitlines()
                     if line.startswith('스테이징 차단: ') and '백링크' in line}
        self.assertIn('notes/plain.md', referrers)
        self.assertNotIn('notes/n1.md', referrers)  # its '[[' names neither stem
        self.assertEqual(code, 1)
        self.assertIn('[provenance-unresolved]', err)

    def test_link_proposals_are_unchanged(self):
        def cli():
            out = io.StringIO()
            with redirect_stdout(out):
                code = vault_links.main(['--vault', str(vault), '--source', '20-knowledge/source.md',
                                         '--query', 'memory storage', '--limit', '10', '--format', 'json'])
            return code, out.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            vault = proposal_vault(Path(tmp).resolve() / 'vault')
            result = self.same_with_oracles(('healthcheck wikilinks',), lambda: vault_links.propose_links(
                vault, '20-knowledge/source.md', 'memory storage', limit=10, max_tokens=50_000))
            code, _ = self.same_with_oracles(('healthcheck wikilinks',), cli)
        self.assertEqual((result['diagnostics']['status'], code), ('ok', 0))
        self.assertTrue(result['proposals'])
        self.assertGreater(result['diagnostics']['skipped_existing'], 0)

    def test_privileged_provenance_findings_are_unchanged(self):
        notes, config = privileged_notes()
        names = ('provenance link texts',)
        findings = self.same_with_oracles(names, lambda: vault_provenance.analyze_notes(notes, config))
        flagged = {item['path'] for item in findings if item['code'] == 'provenance-unresolved'}
        self.assertIn('priv/after127.md', flagged)
        self.assertNotIn('priv/after128.md', flagged)
        self.assertTrue(flagged and not set(config['privileged_notes']) <= flagged)
        warnings = self.same_with_oracles(names, lambda: lint.analyze_notes(notes, config))
        self.assertEqual({item['path'] for item in warnings if item['code'] == 'provenance-unresolved'}, flagged)

    def test_telegram_html_and_sent_messages_are_unchanged(self):
        lines = LINK_LINES + SCAN_LINES + ('## [[제목]] **굵게** `코드` a<b & [[x|y]]', '| a | [[b]] |', '|---|---|',
                                           '[' * 300 + 'a]] [[c]')
        html = self.same_with_oracles(('telegram wiki links',), lambda: [
            jarvis_bridge.md_to_telegram_html(text) for text in (*lines, '\n'.join(lines))])
        self.assertTrue(sum('<i>' in item for item in html) > 10)

        def sent():
            calls = []

            def fake_call(_token, _method, **kwargs):
                calls.append(kwargs)
                return {'ok': True}

            with mock.patch.object(jarvis_bridge, 'tg_call', side_effect=fake_call), \
                    mock.patch.object(jarvis_bridge, 'log'):
                status = jarvis_bridge.tg_send('fake-token', 111, '\n'.join(lines) + '\n' + '[' * 5_000 + '[[a]]')
            return status, calls

        status, calls = self.same_with_oracles(('telegram wiki links',), sent)
        self.assertTrue(status)
        self.assertTrue(len(calls) > 2 and any(call.get('parse_mode') == 'HTML' for call in calls))

    def test_egress_and_lint_warnings_are_unchanged(self):
        # Chunks keep each run under the egress (128) and lint (200) warning caps.
        notes = sorted(egress_notes().items())
        chunks = [dict(notes[start:start + 40]) for start in range(0, len(notes), 40)]
        findings = self.same_with_oracles((DETECTOR,), lambda: [vault_egress.analyze_notes(chunk, {})
                                                                for chunk in chunks])
        rows = [row for result in findings for row in result]
        secret = {row['path'] for row in rows if row['code'] == 'egress-secret'}
        self.assertTrue(10 < len(secret) < len(notes))
        self.assertIn('notes/span.md', secret)
        self.assertIn('<redacted-sensitive-path>', secret)
        self.assertTrue(any(row['line'] == 0 for row in rows))
        self.assertNotIn('egress-incomplete', {row['code'] for row in rows})
        warnings = self.same_with_oracles((DETECTOR,), lambda: [lint.analyze_notes(chunk, {}) for chunk in chunks])
        self.assertEqual({row['path'] for result in warnings for row in result if row['code'] == 'egress-secret'},
                         secret)

    def test_jev_ask_and_vault_judge_results_are_unchanged(self):
        # api_key='' keeps jev_ask from reading TYPESAFE_API_KEY; nothing is sent.
        texts = excerpts(CREDENTIAL_TEXTS[:120])
        asked = self.same_with_oracles((DETECTOR,), lambda: [jev_ask.prepare(ask_request(text), api_key='')
                                                             for text in texts])
        self.assertEqual({result.get('error_code', result['status']) for result in asked},
                         {'prepared', 'sensitive_input'})
        with tempfile.TemporaryDirectory() as tmp:
            vault = judge_vault(Path(tmp).resolve(), texts)
            judged = self.same_with_oracles((DETECTOR,), lambda: [
                (vault_judge.prepare(vault, judge_request(text)),
                 vault_judge.prepare(vault, judge_request('# Evidence', instructions='Check ' + text)))
                for text in texts])
        self.assertEqual({result.get('error', result['status']) for pair in judged for result in pair},
                         {'prepared', 'sensitive_input'})

    def test_warning_promotion_replay_is_unchanged(self):
        # The certificate binds the bundled benign corpus results; only the
        # checker file hash may change with this work.
        names = ('ssot ATX heading', 'lint heading text', 'lint closing run', 'lint date field',
                 'lint comment split', 'provenance header field', 'lint code removal', 'lint link texts',
                 'lint heading link text', 'ssot code spans', 'provenance link texts', DETECTOR)
        result = self.same_with_oracles(names, vault_warning_policy.build_promotion_certificate)
        self.assertEqual(result['false_positives'], 0)


TIMING_SIZES = (8_000, 16_000, 32_000, 64_000)
# Seconds for every shape and size of one helper (measured on this PC: under 2 ms
# for a line parser, up to about 20 ms for a scanner), or of one credential
# detector shape (15-33 ms at the median of 20 runs, 44 ms at most).
TIMING_BUDGET = 0.25
SHAPES = {
    'ssot ATX heading': (lambda n: '# a' + ' ' * n + 'b', lambda n: '# ' + ' ' * n,
                         lambda n: '# a' + ' #' * n, lambda n: '#' + '\u3000' * n + 'x'),
    'lint heading text': (lambda n: '# a' + ' ' * n + 'b', lambda n: '# ' + ' ' * n,
                          lambda n: '#' + '\u3000' * n + 'x'),
    'lint closing run': (lambda n: 'a' + ' ' * n + 'b', lambda n: 'a' + ' ' * n + '#b',
                         lambda n: 'a' + ' #' * n),
    'lint date field': (lambda n: 'created: a' + ' ' * n + 'b', lambda n: 'updated:' + ' ' * n,
                        lambda n: 'created: a' + '\u3000' * n + 'b'),
    'lint comment split': (lambda n: 'a' + ' ' * n + 'b', lambda n: 'a' + '\t' * n + 'x#'),
    'provenance header field': (lambda n: 'key: a' + ' ' * n + 'b', lambda n: 'key:' + ' ' * n,
                                lambda n: 'k' * n + ' ' * n + 'x'),
    'rule heading': (lambda n: '# a' + ' ' * n + 'b', lambda n: '### ' + ' ' * n,
                     lambda n: '#' + '\u3000' * n + 'x'),
    'rule parenthesized note': (lambda n: 'a' + ' ' * n + 'b', lambda n: '(' * n,
                                lambda n: 'a' + ' ' * n + '(b'),
    'rule dash tail': (lambda n: 'a' + ' ' * n + 'b', lambda n: 'a' + '\u3000' * n + '-x',
                       lambda n: '-' * n),
    'recall title value': (lambda n: 'title: a' + ' ' * n + 'b', lambda n: 'title:' + ' ' * n,
                           lambda n: ' ' * n + 'title: x'),
    'recall heading label': (lambda n: '# a' + ' ' * n + 'b', lambda n: '# a' + ' #' * n,
                             lambda n: '#' + '\u3000' * n + 'x', lambda n: ' ' * n + '# x'),
    # The first shape of each scanner is the one the former regex was slowest on.
    # Links that fail or match one after another run a link scanner's loop once
    # per link, on each of its two paths.
    'lint code removal': (lambda n: '`' * n, lambda n: '``a' * (n // 3),
                          lambda n: '`' * (n // 2) + 'a' + '`' * (n // 2 - 1)),
    'lint link texts': (lambda n: '[' * n, lambda n: '[[a' * (n // 3), lambda n: '![[a]]' * (n // 6),
                        lambda n: '[[a]x' * (n // 5)),
    'lint heading link text': (lambda n: '[' * n, lambda n: '[a](' * (n // 4), lambda n: '[' * (n - 2) + ']x',
                               lambda n: '[a]x' * (n // 4), lambda n: '[a](b)' * (n // 6)),
    'healthcheck frontmatter': (lambda n: '---' + '\n' * n, lambda n: '---' + ' \n' * (n // 2),
                                lambda n: '---\n' + '\n' * n + '---\n'),
    'ssot code spans': (lambda n: '`' * n, lambda n: '``a' * (n // 3),
                        lambda n: '`' * (n // 2) + 'a' + '`' * (n // 2 - 1)),
    # Every '[[' of the second shape fails at the same tail; the tails of the
    # third all reach its one ']'; the last matches once per link.
    'healthcheck wikilinks': (lambda n: '[' * n, lambda n: '[' * (n - 3) + '|]x', lambda n: '[[a#' * (n // 4) + ']',
                              lambda n: '[[a]]' * (n // 5)),
    'provenance link texts': (lambda n: '[' * n, lambda n: '[[a]x' * (n // 5), lambda n: '[[a]]' * (n // 5)),
    # Every '[[' of the second shape stops at the same ']' and fails there.
    'telegram wiki links': (lambda n: '[' * n, lambda n: '[' * (n - 2) + ']x', lambda n: '[[a' * (n // 3),
                            lambda n: '[[a]x' * (n // 5), lambda n: '[[]' * (n // 3), lambda n: '[[a]]' * (n // 5)),
}
SHAPES['state ATX heading'] = SHAPES['ssot ATX heading']
# The credential pattern's slowest shapes before the rewrite (scheme runs, JWT
# starts, whitespace after a label) and a long JWT first part. It is a
# 13-branch alternation tried at every position, about 0.2 µs per character on
# this PC even where nothing matches, so each shape gets the budget on its own;
# the former pattern needs more at N = 16,000 already.
DETECTOR_SHAPES = (lambda n: 'a-' * (n // 2), lambda n: 'a.' * (n // 2), lambda n: '+Z' * (n // 2),
                   lambda n: '-eyJ' * (n // 4), lambda n: 'eyJ' + 'a' * n, lambda n: 'password' + ' ' * n,
                   lambda n: 'access_token' + '\u3000' * n, lambda n: '암호' + '\n' * n)


class TimingGateTests(unittest.TestCase):
    """Free from the former super-linear cost; only the new helpers are timed."""

    def elapsed(self, function, shapes):
        inputs = [shape(size) for size in TIMING_SIZES for shape in shapes]
        started = time.perf_counter()
        for value in inputs:
            function(value)
            if time.perf_counter() - started > TIMING_BUDGET:
                break  # an old, super-linear form fails here instead of running on
        return time.perf_counter() - started

    def test_every_helper_stays_under_the_budget_up_to_64k(self):
        self.assertEqual(set(SHAPES), set(PAIR))
        for pair in PAIRS:
            with self.subTest(pair=pair.name):
                self.assertLess(self.elapsed(pair.new(), SHAPES[pair.name]), TIMING_BUDGET)

    def test_credential_detector_stays_under_the_budget_up_to_64k(self):
        for shape in DETECTOR_SHAPES:
            with self.subTest(shape=shape(12)):
                self.assertLess(self.elapsed(jev_client.SENSITIVE.search, (shape,)), TIMING_BUDGET)

    def test_credential_detector_memory_does_not_grow_with_a_run(self):
        # re keeps backtracking state for every pass of a group loop such as
        # (?:(?![+.-][a-z])[a-z0-9+.-])*: about 2.2 MB for one 32,768-character
        # run on this PC, 20 MB at 262,144. The rewritten branches repeat only
        # single characters, so a long run needs no more memory than a short one.
        for text in ('a' * 32_768, 'eyJ' + 'a' * 32_768, 'a-1' * 10_923, 'eyJ' + 'a-' * 16_384):
            with self.subTest(text=text[:12]):
                tracemalloc.start()
                try:
                    jev_client.SENSITIVE.search(text)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 1 << 20)

    def test_link_scans_read_each_character_a_bounded_number_of_times(self):
        # str.find runs in C: a wiki-link scan that searched for the ']' of a
        # tail again from every '[[' read the text once per link, yet took only
        # about 20 ms at 64k on this PC, under the budget. Count what find() reads.
        class CountingText(str):
            read = 0

            def find(self, sub, start=0, end=None):
                found = super().find(sub, start, end)
                self.read += max(0, (found if found >= 0 else len(self)) - start)
                return found

        for name in ('healthcheck wikilinks', 'provenance link texts', 'telegram wiki links'):
            for shape in SHAPES[name]:
                text = CountingText(shape(64_000))
                with self.subTest(pair=name, text=text[:12]):
                    PAIR[name].new()(text)
                    self.assertLessEqual(text.read, 2 * len(text))


def shipped_code_strings():
    """(file:line, value) of each string constant in shipped Python code, docstrings excluded."""
    for directory in (SCRIPTS, ROOT / 'hooks'):
        for path in sorted(directory.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    yield f'{path.name}:{node.lineno}', node.value


PYTHON_DIRECTORIES = ('skills/agentic-vault/scripts', 'hooks', 'scripts', 'tests')


def repository_python_files(root=ROOT):
    """The Python files git tracks in the checkout at root, or else those in its shipped and test directories.

    Not root.rglob('*.py'): a main checkout also holds ignored trees
    (.worktrees/, .superpowers/) with older copies of the scripts and scratch
    files that do not parse. The git listing counts only at the top of a work
    tree, so a copy unpacked inside another repository reads its directories.
    """
    try:
        top = not subprocess.run(['git', 'rev-parse', '--show-prefix'], cwd=root, check=True,
                                 capture_output=True).stdout.strip()
        listed = subprocess.run(['git', 'ls-files', '-z', '--', '*.py'], cwd=root, check=True,
                                capture_output=True).stdout.decode('utf-8')
    except (OSError, subprocess.CalledProcessError):
        top = False
    paths = ([root / name for name in listed.split('\0') if name] if top else
             [path for directory in PYTHON_DIRECTORIES for path in (root / directory).glob('*.py')])
    return sorted(path for path in paths if path.is_file())


def name_uses(paths, name, root=ROOT):
    """'file:line' of each name, attribute or import alias spelled name in the Python files at paths."""
    found = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            spelled = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                       else node.name if isinstance(node, ast.alias) else None)
            if spelled == name:
                found.append(f'{path.relative_to(root).as_posix()}:{node.lineno}')
    return found


class NoLazySuffixLeftTests(unittest.TestCase):
    def test_no_shipped_regex_keeps_a_lazy_group_before_an_optional_whitespace_tail(self):
        # Docstrings and comments may quote the former patterns; code may not.
        shape = re.compile(r'\?\)(?:#\*)*\\s\*(?:\\s\*|#\*)*\$')
        self.assertEqual([where for where, value in shipped_code_strings() if shape.search(value)], [])

    def test_no_shipped_regex_keeps_a_former_scanned_pattern(self):
        # Nor a backtick run closed by a backreference, which retries every
        # opening length against every later position.
        former = {OLD_LINT_CODE.pattern, OLD_LINT_LINKS.pattern, OLD_KEY_LINK.pattern, OLD_FRONTMATTER.pattern,
                  OLD_CODE_SPAN.pattern, OLD_WIKILINK.pattern, OLD_PRIVILEGED_LINK.pattern, OLD_TELEGRAM_LINK.pattern}
        shape = re.compile(r'\(`\+\).*\\[1-9]')
        self.assertTrue(shape.search(OLD_LINT_CODE.pattern) and shape.search(OLD_CODE_SPAN.pattern))
        self.assertEqual([where for where, value in shipped_code_strings()
                          if value in former or shape.search(value)], [])

    def test_no_shipped_regex_keeps_a_former_credential_fragment(self):
        # Nor a JWT first part or URL scheme that every later start reads again,
        # or \s* on both sides of an optional quote.
        self.assertEqual([OLD_SENSITIVE.pattern.count(fragment) for fragment in OLD_SENSITIVE_FRAGMENTS], [1, 1, 2])
        self.assertEqual([(where, fragment) for where, value in shipped_code_strings()
                          for fragment in OLD_SENSITIVE_FRAGMENTS if fragment in value], [])

    def test_no_python_file_uses_the_removed_wikilink_regex(self):
        # vault_links imported WIKILINK_RE; every importer now calls
        # wikilink_targets(), and no module keeps the name for another to import.
        self.assertFalse(hasattr(vault_healthcheck, 'WIKILINK_RE'))
        files = repository_python_files()
        self.assertLessEqual({SCRIPTS / 'vault_healthcheck.py', SCRIPTS / 'vault_links.py'}, set(files))
        self.assertEqual(name_uses(files, 'WIKILINK_RE'), [])


class RepositoryPythonFilesTests(unittest.TestCase):
    # A main checkout keeps ignored worktrees and release probes with older
    # copies that still name WIKILINK_RE, and scratch files that do not parse.
    LAYOUT = {
        '.gitignore': '.worktrees/\n.superpowers/\nunpacked/\n',
        'hooks/hook.py': 'from vault_healthcheck import WIKILINK_RE\n',
        'ci/check.py': 'vault_healthcheck.WIKILINK_RE\n',
        '.worktrees/old/skills/agentic-vault/scripts/vault_links.py': 'from vault_healthcheck import WIKILINK_RE\n',
        '.superpowers/probe/review.py': 'def broken(:\n',
        'unpacked/skills/agentic-vault/scripts/vault_links.py': 'from vault_healthcheck import WIKILINK_RE\n',
        'unpacked/tests/probe/review.py': 'def broken(:\n',
    }

    def layout(self, root):
        for name, text in self.LAYOUT.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding='utf-8')
        return root

    def uses(self, root):
        return name_uses(repository_python_files(root), 'WIKILINK_RE', root)

    def test_a_checkout_reads_only_the_files_git_tracks(self):
        if shutil.which('git') is None:
            self.skipTest('git is not available')
        with tempfile.TemporaryDirectory() as tmp:
            root = self.layout(Path(tmp).resolve() / 'repo')
            for args in (('init', '-q'), ('add', '--', '.')):
                git(root, *args)
            (root / 'scripts').mkdir()
            (root / 'scripts' / 'draft.py').write_text('def broken(:\n', encoding='utf-8')  # untracked
            self.assertEqual(self.uses(root), ['ci/check.py:1', 'hooks/hook.py:1'])
            # A copy unpacked inside a repository that ignores it reads its own directories.
            self.assertEqual(self.uses(root / 'unpacked'), ['skills/agentic-vault/scripts/vault_links.py:1'])

    def test_without_a_checkout_it_reads_the_shipped_and_test_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.uses(self.layout(Path(tmp).resolve() / 'plain')), ['hooks/hook.py:1'])


@unittest.skipUnless(SLOW, 'set AGENTIC_VAULT_SLOW_TESTS=1 for the larger runs')
class SlowEquivalenceTests(unittest.TestCase):
    def test_every_whitespace_code_point_at_a_shorter_length(self):
        spaces = ''.join(c for c in map(chr, range(0x110000)) if c.isspace())
        for pair in PAIRS:
            alphabet = (spaces if not pair.newline_free else spaces.replace('\n', '')) + pair.special
            length = 4 if pair.prefixes == ('',) else 3
            with self.subTest(pair=pair.name):
                _, found = mismatches(pair, strings(alphabet, length, pair.prefixes))
                self.assertEqual(found, [])

    def test_each_default_alphabet_one_symbol_longer(self):
        for pair in PAIRS:
            alphabet, length = pair.runs[0]
            with self.subTest(pair=pair.name):
                _, found = mismatches(pair, strings(alphabet, length + 1, pair.prefixes))
                self.assertEqual(found, [])

    def test_scanners_on_their_distinguished_characters_at_greater_lengths(self):
        for name, (alphabet, length, prefixes) in {
                'lint code removal': ('`a\n', 14, ('',)), 'ssot code spans': ('`a\n', 14, ('',)),
                'lint link texts': ('[]!a\n', 10, ('',)), 'lint heading link text': ('[]()a\n', 9, ('',)),
                'healthcheck frontmatter': ('-\n\r a', 10, ('---',)),
                'healthcheck wikilinks': ('[]|#a', 11, ('',)), 'provenance link texts': ('[]\na', 12, ('',)),
                'telegram wiki links': ('[]a', 14, ('',))}.items():
            pair = PAIR[name]
            with self.subTest(pair=name):
                _, found = mismatches(pair, strings(alphabet, length, prefixes))
                self.assertEqual(found, [])

    def test_credential_detector_on_larger_domains(self):
        for name, texts in (('characters', strings(CREDENTIAL_CHARS, 6)),
                            ('url', joined(strings(URL_HEAD, 5), URL_TAILS)),
                            ('jwt', joined(strings(JWT_HEAD, 6), JWT_TAILS)),
                            ('assignment', strings(ASSIGNMENT, 6)),
                            ('random', credential_texts(400_000, seed=20261028))):
            with self.subTest(domain=name):
                _, _, found = verdict_mismatches(texts)
                self.assertEqual(found, [])

    def test_report_former_and_new_detector_times(self):
        report = io.StringIO()
        for shape in DETECTOR_SHAPES:
            for size in (8_000, 16_000, 32_000):
                value = shape(size)
                started = time.perf_counter()
                OLD_SENSITIVE.search(value)
                former = time.perf_counter() - started
                started = time.perf_counter()
                jev_client.SENSITIVE.search(value)
                new = time.perf_counter() - started
                print(f'credential {shape(12)[:14]!r:26} N={size:>6}  former {former:9.4f} s  new {new:.6f} s',
                      file=report)
                self.assertLess(new, TIMING_BUDGET)
        # The default lint's egress scan searches the line and then the whole note.
        for size in (16_000, 32_000):
            note = 'a-' * (size // 2)
            started = time.perf_counter()
            with oracles(DETECTOR):
                former_rows = vault_egress.scan_text(note, 'notes/a.md')
            former = time.perf_counter() - started
            started = time.perf_counter()
            rows = vault_egress.scan_text(note, 'notes/a.md')
            new = time.perf_counter() - started
            self.assertEqual(rows, former_rows)
            print(f'egress scan_text, one-line note       N={size:>6}  former {former:9.4f} s  new {new:.6f} s',
                  file=report)
        sys.stderr.write('\n' + report.getvalue())

    def test_report_former_and_new_times(self):
        report = io.StringIO()
        # The former forms were cubic for these; CODE_SPAN_RE read cells of at most 4 KiB.
        sizes = {'recall heading label': (1_000, 2_000), 'lint code removal': (2_000, 4_000, 8_000),
                 'ssot code spans': (1_024, 2_048, 4_096)}
        for pair in PAIRS:
            if pair.name == 'state ATX heading':
                continue
            shape = SHAPES[pair.name][0]
            for size in sizes.get(pair.name, (8_000, 16_000, 32_000, 64_000) if 'ATX' in pair.name
                                  else (8_000, 16_000, 32_000)):
                value = shape(size)
                started = time.perf_counter()
                pair.oracle(value)
                former = time.perf_counter() - started
                started = time.perf_counter()
                pair.new()(value)
                new = time.perf_counter() - started
                print(f'{pair.name:26} N={size:>6}  former {former:9.4f} s  new {new:.6f} s', file=report)
                self.assertLess(new, TIMING_BUDGET)
        sys.stderr.write('\n' + report.getvalue())


if __name__ == '__main__':
    unittest.main()
