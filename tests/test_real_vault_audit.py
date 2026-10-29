"""scripts/real_vault_audit.py: seeded audit samples and exact hypergeometric miss bounds.

A repository evaluation tool like flaky_repeat.py. draw writes item
references only (path, line, id, match) and records the commitment made
before labelling (seed, k and confidence, with a digest of the whole sample);
bound needs every sampled id labelled and prints counts, the seed and digests,
never ids, labels or note text. plan is arithmetic before any draw: sample
sizes for a target share of N and an exact OC table, from no input file.
Every population here is synthetic; nothing reads a real vault.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from fractions import Fraction
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'scripts' / 'real_vault_audit.py'
SSOT = ROOT / 'skills' / 'agentic-vault' / 'scripts' / 'vault_ssot.py'
FIELDS = ['subject', 'relation', 'value', 'valid_from', 'valid_until', 'recorded', 'source', 'superseded_by', 'status']
MEETING = ('---\ntitle: Meeting\n---\n회의는 2026. 10. 7.에 열렸다. 3Q26 출하, 출시는 2026.08.15.\n'
           '점검 2026.10.01. 당기 실적과 올해 3분기 계획.\n')
# Lines 3-10 of the synthetic ledger: equal, none, none, none, contained, none, abstain, none.
PERIOD_VALUES = ('2026-10-07', '2026년 1분기', '2026년 2분기', '2025년 4분기', '2026년 4분기', '2026-09-30',
                 '당기 출하', '2026-06-30')
NONE_LINES = [4, 5, 6, 8, 10]
SAMPLE_KEYS = {'tool', 'kind', 'version', 'population_id', 'population_kind', 'population_sha256', 'seed', 'N',
               'k', 'confidence', 'draw_method', 'items'}
BOUND_KEYS = {'tool', 'command', 'version', 'N', 'k', 'x', 'confidence', 'alpha', 'seed', 'sample_sha256',
              'population_sha256', 'population_verified', 'upper_misses', 'upper_miss_share', 'method', 'authority'}
PLAN_KEYS = {'tool', 'command', 'version', 'N', 'target_share', 'target_misses', 'ltpd_misses', 'expected_misses',
             'confidence', 'alpha', 'sample_size', 'sample_size_reason', 'strict_ltpd_sample_size',
             'strict_ltpd_sample_size_reason', 'basis', 'warnings', 'authority'}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stats = _load('real_vault_audit_eval_stats_under_test', ROOT / 'scripts' / 'eval_stats.py')


def canonical(value):
    """Independent restatement of the documented canonical JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(path):
    """The documented sample_sha256: SHA-256 of the canonical JSON of the sample file's content."""
    return hashlib.sha256(canonical(json.loads(path.read_bytes())).encode('utf-8')).hexdigest()


def rows(count, prefix='r', path='notes/ledger.md'):
    return [{'id': '%s%03d' % (prefix, n), 'path': path, 'line': n + 3, 'match': 'none'} for n in range(count)]


def ledger(values):
    table = [['Atlas', 'launch', value, '2025-01-01', '', '2026-10-07', '[[Meeting]]', '', 'confirmed']
             for value in values]
    return '\n'.join(['| ' + ' | '.join(FIELDS) + ' |', '| ' + ' | '.join(['---'] * len(FIELDS)) + ' |',
                      *['| ' + ' | '.join(row) + ' |' for row in table]]) + '\n'


def hypergeometric_cdf(population, total, sample, misses) -> Fraction:
    """P(X <= misses) for X ~ Hypergeometric(population, total, sample), summed directly from math.comb."""
    return Fraction(sum(math.comb(total, i) * math.comb(population - total, sample - i)
                        for i in range(min(misses, sample) + 1)), math.comb(population, sample))


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.assertTrue(TOOL.is_file(), 'scripts/real_vault_audit.py is missing')
        self.tool = _load('real_vault_audit_under_test_' + uuid.uuid4().hex[:8], TOOL)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, value):
        path = self.dir / name
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8'))
        return path

    def population(self, items, name='population.json', **extra):
        return self.write(name, {'population_id': 'synthetic ledger rows', 'items': items, **extra})

    def main(self, *args):
        """In-process run: (exit code, parsed stdout)."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.tool.main([str(arg) for arg in args])
        return code, json.loads(stdout.getvalue())

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(TOOL), *[str(arg) for arg in args]],
                              capture_output=True, text=True, encoding='utf-8', timeout=120)

    def draw(self, population, k, seed, name='sample.json', extra=()):
        out = self.dir / name
        code, summary = self.main('draw', '--population', population, '--k', k, '--seed', seed, '--out', out, *extra)
        self.assertEqual(code, 0, summary)
        return out, summary

    def labels(self, sample, misses=(), name='labels.json'):
        ids = [item['id'] for item in json.loads(sample.read_text(encoding='utf-8'))['items']]
        labels = {item_id: 'miss' if item_id in misses or index in misses else 'ok'
                  for index, item_id in enumerate(ids)}
        return self.write(name, {'labels': labels})

    def refused(self, code, *args):
        result = self.main(*args)
        self.assertEqual(result, (2, {'ok': False, 'error': code}))


class DrawTests(AuditCase):
    def test_draw_writes_references_and_the_commitment_only(self):
        population = self.population(rows(50), source_sha256='a' * 64)
        out, summary = self.draw(population, 10, 'C0FFEE')
        sample = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(set(sample), SAMPLE_KEYS | {'source_sha256'})
        self.assertEqual((sample['tool'], sample['kind'], sample['version']), ('real_vault_audit', 'sample', 1))
        self.assertEqual((sample['N'], sample['k'], sample['seed'], sample['confidence']), (50, 10, 'c0ffee', 0.95))
        self.assertEqual((sample['population_kind'], sample['source_sha256']), ('generic', 'a' * 64))
        self.assertEqual(len(sample['items']), 10)
        by_id = {item['id']: item for item in rows(50)}
        for item in sample['items']:
            self.assertEqual(item, by_id[item['id']])
        # The digest covers population_id, the items in id order and source_sha256.
        body = {'population_id': 'synthetic ledger rows', 'items': sorted(rows(50), key=lambda item: item['id']),
                'source_sha256': 'a' * 64}
        self.assertEqual(sample['population_sha256'], hashlib.sha256(canonical(body).encode('utf-8')).hexdigest())
        self.assertEqual(summary, {'tool': 'real_vault_audit', 'command': 'draw', 'version': 1,
                                   'population_kind': 'generic', 'N': 50, 'k': 10, 'confidence': 0.95,
                                   'seed': 'c0ffee', 'population_sha256': sample['population_sha256'],
                                   'sample_sha256': hashlib.sha256(canonical(sample).encode('utf-8')).hexdigest(),
                                   'source_sha256': 'a' * 64})
        self.assertNotIn('labels', out.read_text(encoding='utf-8'))

    def test_the_rank_is_the_documented_sha256_order_on_every_python(self):
        items = rows(8, prefix='q')
        population = self.population(items)
        out, _ = self.draw(population, 3, '1707')
        drawn = [item['id'] for item in json.loads(out.read_text(encoding='utf-8'))['items']]
        expected = sorted(items, key=lambda item: (
            hashlib.sha256(('1707:' + canonical(item)).encode('utf-8')).hexdigest(), item['id']))
        self.assertEqual(drawn, [item['id'] for item in expected[:3]])
        # Pinned: sha256 and canonical JSON do not depend on the interpreter, unlike random.sample.
        self.assertEqual(drawn, ['q006', 'q005', 'q007'])
        self.assertEqual([item['id'] for item in self.tool.draw_items(items, 8, '1707')],
                         ['q006', 'q005', 'q007', 'q000', 'q001', 'q003', 'q004', 'q002'])

    def test_a_seed_fixes_the_draw_and_another_seed_changes_it(self):
        population = self.population(rows(500))
        first, _ = self.draw(population, 20, '1707', name='first.json')
        again, _ = self.draw(population, 20, '1707', name='again.json')
        other, _ = self.draw(population, 20, 'c0ffee', name='other.json')
        self.assertEqual(first.read_bytes(), again.read_bytes())

        def ids(path):
            return [item['id'] for item in json.loads(path.read_text(encoding='utf-8'))['items']]
        self.assertEqual(ids(first)[:3], ['r159', 'r283', 'r101'])
        self.assertEqual(len(set(ids(first)) & set(ids(other))), 1)
        # The file's item order changes neither the draw nor the digest.
        shuffled = self.population(rows(500)[::-1], name='reversed.json')
        reordered, _ = self.draw(shuffled, 20, '1707', name='reordered.json')
        self.assertEqual(reordered.read_bytes(), first.read_bytes())

    def test_without_a_seed_a_fresh_random_one_is_recorded(self):
        population = self.population(rows(30))
        first, one = self.draw_default(population, 'one.json')
        second, two = self.draw_default(population, 'two.json')
        self.assertRegex(one['seed'], r'^[0-9a-f]{32}$')
        self.assertNotEqual(one['seed'], two['seed'])
        self.assertEqual(json.loads(first.read_text(encoding='utf-8'))['seed'], one['seed'])
        self.assertEqual(json.loads(second.read_text(encoding='utf-8'))['seed'], two['seed'])

    def draw_default(self, population, name):
        out = self.dir / name
        code, summary = self.main('draw', '--population', population, '--k', 5, '--out', out)
        self.assertEqual(code, 0, summary)
        return out, summary

    def test_an_existing_sample_is_never_replaced(self):
        population = self.population(rows(30))
        out, _ = self.draw(population, 5, '1707')
        before = out.read_bytes()
        self.refused('output_exists', 'draw', '--population', population, '--k', 5, '--seed', 'beef', '--out', out)
        self.refused('output_exists', 'draw', '--population', population, '--k', 5, '--out', population)
        self.assertEqual(out.read_bytes(), before)

    def test_refused_populations_and_sizes_write_nothing(self):
        sentinel = 'SYNTHETIC_NOTE_TEXT_' + uuid.uuid4().hex
        duplicate = rows(5) + [rows(5)[0]]
        cases = {
            'sample_exceeds_population': ({'population_id': 'p', 'items': rows(5)}, 6, '1707'),
            'invalid_sample_size': ({'population_id': 'p', 'items': rows(5)}, 0, '1707'),
            'duplicate_item_id': ({'population_id': 'p', 'items': duplicate}, 2, '1707'),
            'item_field_not_allowed': ({'population_id': 'p', 'items': [{**rows(1)[0], 'text': sentinel}]}, 1, '1707'),
            'empty_population': ({'population_id': 'p', 'items': []}, 1, '1707'),
            'population_field_not_allowed': ({'population_id': 'p', 'items': rows(2), 'notes': sentinel}, 1, '1707'),
            'invalid_source_sha256': ({'population_id': 'p', 'items': rows(2), 'source_sha256': 'x'}, 1, '1707'),
            'invalid_population_id': ({'population_id': '', 'items': rows(2)}, 1, '1707'),
            'unknown_population_format': ({'items': rows(2)}, 1, '1707'),
            'invalid_seed': ({'population_id': 'p', 'items': rows(2)}, 1, 'not-hex'),
        }
        for field, value in (('value', sentinel), ('excerpt', sentinel), ('label', 'miss')):
            cases['item_field_not_allowed:' + field] = (
                {'population_id': 'p', 'items': [{'id': 'a', field: value}]}, 1, '1707')
        bad_items = ({'path': 'a.md'}, {'id': ''}, {'id': 1}, {'id': 'a\nb'}, {'id': 'a', 'line': 0},
                     {'id': 'a', 'line': True}, {'id': 'a', 'line': '3'}, {'id': 'a', 'match': 'no ne'},
                     {'id': 'a', 'path': '/home/someone/vault/a.md'}, {'id': 'a', 'path': 'C:/vault/a.md'},
                     {'id': 'a', 'path': '\\\\server\\share\\a.md'}, 'a')
        for index, item in enumerate(bad_items):
            cases['invalid_item:%d' % index] = ({'population_id': 'p', 'items': [item]}, 1, '1707')
        for label, (data, k, seed) in cases.items():
            with self.subTest(case=label):
                population = self.write('population-%s.json' % label.replace(':', '-'), data)
                out = self.dir / ('sample-%s.json' % label.replace(':', '-'))
                code, report = self.main('draw', '--population', population, '--k', k, '--seed', seed, '--out', out)
                self.assertEqual((code, report), (2, {'ok': False, 'error': label.split(':')[0]}))
                self.assertFalse(out.exists())
                self.assertNotIn(sentinel, json.dumps(report))

    def test_paths_are_plain_vault_relative_posix_paths(self):
        # One note, one spelling: a backslash spelling of '20-knowledge/노트.md' would be a second item with its
        # own digest, and '..' leaves the vault.
        refused = ('..\\x', '../x.md', 'notes/../x.md', './a.md', 'notes/./a.md', 'notes//a.md', 'notes/a.md/',
                   'notes\\a.md', '20-knowledge\\노트.md', 'notes/a.md.', 'notes /a.md', '.', '..', 'a.md:stream',
                   'notes/a?.md', 'notes/a*.md', 'notes/<a>.md', 'notes/a|b.md', 'notes/"a".md')
        for index, path in enumerate(refused):
            with self.subTest(path=path):
                population = self.population([{'id': 'a', 'path': path}], name='path-%d.json' % index)
                out = self.dir / ('never-%d.json' % index)
                self.refused('invalid_item', 'draw', '--population', population, '--k', 1, '--seed', '1707',
                             '--out', out)
                self.assertFalse(out.exists())
        accepted =('a.md', 'notes/sub/a b.md', '20-knowledge/노트.md', '.hidden/a.md', 'notes/a..b.md')
        items = [{'id': 'p%d' % index, 'path': path} for index, path in enumerate(accepted)]
        out, _ = self.draw(self.population(items, name='accepted.json'), len(items), '1707')
        self.assertEqual(sorted(item['path'] for item in json.loads(out.read_text(encoding='utf-8'))['items']),
                         sorted(accepted))

    def test_unreadable_and_malformed_files_are_refused(self):
        cases = {
            'population_invalid_json': b'{"population_id": "p", "items": [',
            'population_invalid_json:repeated': b'{"population_id": "p", "population_id": "q", "items": []}',
            'population_invalid_json:nan': b'{"population_id": "p", "items": [{"id": "a", "line": NaN}]}',
            'population_invalid_json:latin1': '{"population_id": "\u00e9", "items": []}'.encode('latin-1'),
        }
        for index, (label, raw) in enumerate(cases.items()):
            with self.subTest(case=label):
                population = self.write('raw-%d.json' % index, raw)
                self.refused(label.split(':')[0], 'draw', '--population', population, '--k', 1, '--out',
                             self.dir / 'never.json')
        self.refused('population_unreadable', 'draw', '--population', self.dir / 'missing.json', '--k', 1,
                     '--out', self.dir / 'never.json')
        self.refused('population_unreadable', 'draw', '--population', self.dir, '--k', 1, '--out',
                     self.dir / 'never.json')
        self.tool.MAX_POPULATION_BYTES = 64
        self.refused('population_too_large', 'draw', '--population', self.population(rows(5)), '--k', 1,
                     '--out', self.dir / 'never.json')
        self.assertFalse((self.dir / 'never.json').exists())

    def test_a_utf16_population_saved_by_powershell_loads(self):
        data = {'population_id': '합성 대장 행', 'items': rows(12, path='노트/대장.md')}
        utf8 = self.write('utf8.json', data)
        utf16 = self.write('utf16.json', '\ufeff'.encode('utf-16-le')
                           + json.dumps(data, ensure_ascii=False).replace('\n', '\r\n').encode('utf-16-le'))
        first, _ = self.draw(utf8, 4, '1707', name='from-utf8.json')
        second, _ = self.draw(utf16, 4, '1707', name='from-utf16.json')
        self.assertEqual(first.read_bytes(), second.read_bytes())


class UniformDrawTests(AuditCase):
    def test_inclusion_frequencies_match_k_over_n(self):
        items = [{'id': 'i%d' % n} for n in range(10)]
        counts = Counter(item['id'] for seed in range(3000)
                         for item in self.tool.draw_items(items, 3, format(seed, '032x')))
        # Each item is in a sample with probability 3/10: 900 of 3000, standard deviation about 25.
        spread = (3000 * 0.3 * 0.7) ** 0.5
        for item in items:
            with self.subTest(item=item['id']):
                self.assertLess(abs(counts[item['id']] - 900), 5 * spread)

    def test_every_subset_is_equally_likely(self):
        items = [{'id': 'i%d' % n} for n in range(6)]
        counts = Counter(frozenset(item['id'] for item in self.tool.draw_items(items, 3, format(seed, '032x')))
                         for seed in range(4000))
        self.assertEqual(len(counts), 20)
        expected = 4000 / 20
        chi_square = sum((count - expected) ** 2 / expected for count in counts.values())
        self.assertLess(chi_square, 43.82)  # 0.999 quantile of chi-square with 19 degrees of freedom


class CheckSupportPopulationTests(AuditCase):
    def check_support(self, entries, **extra):
        sentinel = 'SYNTHETIC_CELL_' + uuid.uuid4().hex
        return sentinel, {
            'authority': 'warning_only_not_fact_verification', 'cited_notes': [], 'incomplete': [],
            'ledger_table_only': [], 'method': 'normalized_literal_presence_in_cited_note_body',
            'normalized_periods': entries, 'path': 'notes/facts.md',
            'period_convention': {'fiscal_start': None, 'scope': 'synthetic'},
            'rows_checked': len(entries), 'rows_skipped': 0, 'rows_supported': 0,
            'rows_supported_ledger_table_only': 0, 'rows_unresolved': 1, 'rows_unsupported': 0,
            'source_sha256': 'b' * 64,
            'warnings': [{'code': 'ssot-source-unresolved', 'line': 9, 'path': 'notes/facts.md',
                          'details': {'reference': sentinel, 'reason': 'missing'}}], **extra}

    def test_only_rows_with_normalized_match_none_are_drawn(self):
        entries = [{'line': 3, 'normalized_match': 'equal', 'via': ['notes/Meeting.md']},
                   {'line': 4, 'normalized_match': 'none'}, {'line': 5, 'normalized_match': 'none'},
                   {'line': 6, 'normalized_match': 'abstain', 'reason': 'no_year'},
                   {'line': 7, 'normalized_match': 'contained', 'via': ['notes/Meeting.md']},
                   {'line': 8, 'normalized_match': 'none', 'incomplete': ['period_text_limit']}]
        sentinel, result = self.check_support(entries)
        out, summary = self.draw(self.write('support.json', result), 3, '1707')
        text = out.read_text(encoding='utf-8')
        sample = json.loads(text)
        self.assertEqual((sample['N'], sample['k'], sample['population_kind']),
                         (3, 3, 'check_support_normalized_periods'))
        self.assertEqual((sample['population_id'], sample['source_sha256']),
                         ('check-support normalized_match=none', 'b' * 64))
        self.assertEqual(sorted(item['line'] for item in sample['items']), [4, 5, 8])
        for item in sample['items']:
            self.assertEqual(item, {'id': 'notes/facts.md:%d' % item['line'], 'path': 'notes/facts.md',
                                    'line': item['line'], 'match': 'none'})
        self.assertEqual(summary['source_sha256'], 'b' * 64)
        for absent in (sentinel, 'warnings', 'via', 'incomplete', 'Meeting'):
            self.assertNotIn(absent, text)

    def test_a_result_without_period_rows_is_refused(self):
        _, result = self.check_support([])
        del result['normalized_periods']
        cases = {
            'normalize_periods_missing': result,
            'empty_population': self.check_support([{'line': 3, 'normalized_match': 'equal', 'via': []}])[1],
            'invalid_check_support': self.check_support([{'line': 3, 'normalized_match': 'maybe'}])[1],
            'invalid_check_support:line': self.check_support([{'line': '3', 'normalized_match': 'none'}])[1],
            'invalid_check_support:path': self.check_support([{'line': 3, 'normalized_match': 'none'}],
                                                             path='/abs/facts.md')[1],
            'invalid_check_support:backslash': self.check_support([{'line': 3, 'normalized_match': 'none'}],
                                                                  path='notes\\facts.md')[1],
            'invalid_check_support:parent': self.check_support([{'line': 3, 'normalized_match': 'none'}],
                                                               path='../facts.md')[1],
            'invalid_source_sha256': self.check_support([{'line': 3, 'normalized_match': 'none'}],
                                                        source_sha256=None)[1],
            'duplicate_item_id': self.check_support([{'line': 3, 'normalized_match': 'none'}] * 2)[1],
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                out = self.dir / ('never-%s.json' % label.replace(':', '-'))
                self.refused(label.split(':')[0], 'draw', '--population', self.write('support.json', data),
                             '--k', 1, '--out', out)
                self.assertFalse(out.exists())

    def test_end_to_end_from_a_vault_ssot_check_support_run(self):
        vault = self.dir / 'vault'
        files = {'00-meta/vault-config.json': '{"deny_zones": ["private"], "exclude_dirs": ["excluded"]}',
                 'notes/Meeting.md': MEETING, 'notes/facts.md': ledger(PERIOD_VALUES)}
        for relative, text in files.items():
            (vault / relative).parent.mkdir(parents=True, exist_ok=True)
            (vault / relative).write_bytes(text.encode('utf-8'))
        before = {relative: (vault / relative).read_bytes() for relative in files}
        completed = subprocess.run([sys.executable, '-B', str(SSOT), '--vault', str(vault), 'check-support',
                                    '--path', 'notes/facts.md', '--normalize-periods'],
                                   capture_output=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', 'replace'))
        result = json.loads(completed.stdout)
        self.assertEqual([entry['line'] for entry in result['normalized_periods']
                          if entry['normalized_match'] == 'none'], NONE_LINES)
        saved = self.write('support.json', completed.stdout)
        # Windows PowerShell 5.1 '>' saves UTF-16 LE with a BOM and CRLF.
        utf16 = self.write('support-utf16.json', '\ufeff'.encode('utf-16-le')
                           + completed.stdout.decode('utf-8').replace('\n', '\r\n').encode('utf-16-le'))
        result_tool = self.cli('draw', '--population', saved, '--k', 3, '--seed', '1707', '--out',
                               self.dir / 'sample.json')
        self.assertEqual(result_tool.returncode, 0, result_tool.stdout + result_tool.stderr)
        out = self.dir / 'sample.json'
        again, _ = self.draw(utf16, 3, '1707', name='sample-utf16.json')
        self.assertEqual(out.read_bytes(), again.read_bytes())
        sample = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual((sample['N'], sample['source_sha256']), (5, result['source_sha256']))
        self.assertLessEqual({item['line'] for item in sample['items']}, set(NONE_LINES))
        for literal in ('분기', '2026', '회의', 'Atlas', 'Meeting'):
            self.assertNotIn(literal, out.read_text(encoding='utf-8'))
        labels = self.labels(out, misses={0})
        bounded = self.cli('bound', '--sample', out, '--labels', labels, '--population', utf16)
        self.assertEqual(bounded.returncode, 0, bounded.stdout + bounded.stderr)
        report = json.loads(bounded.stdout)
        self.assertEqual((report['N'], report['k'], report['x'], report['population_verified']), (5, 3, 1, True))
        self.assertEqual(report['upper_misses'], stats.hypergeometric_upper_bound(1, 3, 5)['upper_misses'])
        self.assertEqual(report['source_sha256'], result['source_sha256'])
        self.assertEqual({relative: (vault / relative).read_bytes() for relative in files}, before)


class BoundTests(AuditCase):
    def sample(self, count=500, k=20, seed='1707'):
        tag = '%d-%d-%s' % (count, k, seed)
        population = self.population(rows(count), name='population-%s.json' % tag)
        out, _ = self.draw(population, k, seed, name='sample-%s.json' % tag)
        return population, out

    def test_the_report_is_the_exact_bound_with_counts_and_no_labels(self):
        population, out = self.sample()
        labels = self.labels(out)
        code, report = self.main('bound', '--sample', out, '--labels', labels)
        self.assertEqual(code, 0)
        self.assertEqual(set(report), BOUND_KEYS)
        self.assertEqual((report['N'], report['k'], report['x'], report['upper_misses']), (500, 20, 0, 68))
        self.assertEqual((report['upper_miss_share'], report['confidence'], report['alpha']), (0.136, 0.95, 0.05))
        self.assertEqual((report['method'], report['authority']),
                         (stats.HYPERGEOMETRIC_METHOD, 'human_judgement_input_not_gate'))
        self.assertEqual((report['seed'], report['population_verified']), ('1707', False))
        self.assertEqual(report['population_sha256'], json.loads(out.read_text(encoding='utf-8'))['population_sha256'])
        self.assertEqual(report['sample_sha256'], digest(out))
        result = self.cli('bound', '--sample', out, '--labels', labels, '--population', population)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        verified = json.loads(result.stdout)
        self.assertEqual(verified, {**report, 'population_verified': True})
        for item in json.loads(out.read_text(encoding='utf-8'))['items']:
            self.assertNotIn(item['id'], result.stdout)
        for word in ('"miss"', '"ok"', 'notes/ledger.md'):
            self.assertNotIn(word, result.stdout)

    def test_misses_and_the_confidence_level_move_the_bound(self):
        population, out = self.sample()
        code, report = self.main('bound', '--sample', out, '--labels', self.labels(out, misses={5}))
        self.assertEqual((code, report['x'], report['upper_misses']), (0, 1, 106))
        lower, _ = self.draw(population, 20, '1707', name='sample-0.9.json', extra=('--confidence', '0.9'))
        code, report = self.main('bound', '--sample', lower, '--labels', self.labels(lower, name='ok.json'))
        self.assertEqual((code, report['confidence'], report['alpha']), (0, 0.9, 0.1))
        self.assertEqual(report['upper_misses'],
                         stats.hypergeometric_upper_bound(0, 20, 500, confidence=0.9)['upper_misses'])
        self.assertLess(report['upper_misses'], 68)
        _, everything = self.sample(count=40, k=40, seed='beef')
        code, report = self.main('bound', '--sample', everything, '--labels',
                                 self.labels(everything, misses={0, 1, 2}, name='all.json'))
        self.assertEqual((code, report['x'], report['upper_misses'], report['upper_miss_share']), (0, 3, 3, 0.075))

    def test_the_confidence_is_fixed_by_draw_before_labelling(self):
        population = self.population(rows(30))
        out, summary = self.draw(population, 5, '1707', extra=('--confidence', '0.9'))
        sample = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual((summary['confidence'], sample['confidence']), (0.9, 0.9))
        labels = self.labels(out, misses={0})
        code, report = self.main('bound', '--sample', out, '--labels', labels)
        self.assertEqual((code, report['confidence'], report['alpha'], report['x']), (0, 0.9, 0.1, 1))
        self.assertEqual(report['upper_misses'],
                         stats.hypergeometric_upper_bound(1, 5, 30, confidence=0.9)['upper_misses'])
        # --confidence only checks: the recorded level passes however it is written.
        self.assertEqual(self.main('bound', '--sample', out, '--labels', labels, '--confidence', '0.90'), (0, report))
        # A level chosen once x is known is refused, in either direction (here 16 misses at 0.9, 18 at 0.95).
        self.assertEqual((report['upper_misses'], stats.hypergeometric_upper_bound(1, 5, 30)['upper_misses']), (16, 18))
        self.refused('confidence_mismatch', 'bound', '--sample', out, '--labels', labels, '--confidence', '0.95')
        default, _ = self.draw(population, 5, '1707', name='default.json')
        self.refused('confidence_mismatch', 'bound', '--sample', default, '--labels',
                     self.labels(default, misses={0}, name='default-labels.json'), '--confidence', '0.9')
        for value in ('0.5', '1', '1.5', '-0.95', 'nan', 'inf'):
            with self.subTest(draw_confidence=value):
                self.refused('invalid_confidence', 'draw', '--population', population, '--k', 5,
                             '--confidence', value, '--out', self.dir / 'never.json')
        self.refused('invalid_arguments', 'draw', '--population', population, '--k', 5, '--confidence', 'high',
                     '--out', self.dir / 'never.json')
        self.assertFalse((self.dir / 'never.json').exists())
        cases = [{key: value for key, value in sample.items() if key != 'confidence'}]
        cases += [{**sample, 'confidence': value} for value in (0.5, 1, 1.0, '0.9', None, True)]
        for index, data in enumerate(cases):
            with self.subTest(sample_confidence=data.get('confidence', 'missing')):
                self.refused('invalid_sample', 'bound', '--sample', self.write('confidence-%d.json' % index, data),
                             '--labels', labels)

    def test_an_edited_sample_is_trusted_as_written_and_shows_in_its_digest(self):
        population = self.population(rows(30))
        out, summary = self.draw(population, 5, '1707')
        sample = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(summary['sample_sha256'], digest(out))
        labels = self.labels(out, misses={0})
        code, report = self.main('bound', '--sample', out, '--labels', labels, '--population', population)
        self.assertEqual((code, report['x'], report['population_verified']), (0, 1, True))
        self.assertEqual(report['sample_sha256'], summary['sample_sha256'])
        # The digest covers the content, so a copy saved as UTF-16 with CRLF (PowerShell '>', git autocrlf) matches.
        copy = self.write('copy.json', '﻿'.encode('utf-16-le')
                          + out.read_text(encoding='utf-8').replace('\n', '\r\n').encode('utf-16-le'))
        self.assertEqual(self.main('bound', '--sample', copy, '--labels', labels),
                         (0, {**report, 'population_verified': False}))
        # Without --population the sample is trusted as written: dropping the missed item and lowering k gives x = 0.
        dropped = self.write('dropped.json', {**sample, 'k': 4, 'items': sample['items'][1:]})
        dropped_labels = self.labels(dropped, name='dropped-labels.json')
        code, trusted = self.main('bound', '--sample', dropped, '--labels', dropped_labels)
        self.assertEqual((code, trusted['x'], trusted['population_verified']), (0, 0, False))
        self.assertNotEqual(trusted['sample_sha256'], summary['sample_sha256'])
        self.refused('sample_mismatch', 'bound', '--sample', dropped, '--labels', dropped_labels,
                     '--population', population)
        # The recomputed draw covers the seed and the items, not k or the confidence: cutting the last-ranked item
        # or relaxing the level passes --population, and only the digest differs from the one draw printed.
        cut = self.write('cut.json', {**sample, 'k': 4, 'items': sample['items'][:4]})
        relaxed = self.write('relaxed.json', {**sample, 'confidence': 0.9})
        for edited, edited_labels in ((cut, self.labels(cut, misses={0}, name='cut-labels.json')), (relaxed, labels)):
            with self.subTest(sample=edited.name):
                code, passed = self.main('bound', '--sample', edited, '--labels', edited_labels,
                                         '--population', population)
                self.assertEqual((code, passed['population_verified']), (0, True))
                self.assertNotEqual(passed['sample_sha256'], summary['sample_sha256'])

    def test_flagged_true_positives_add_recall_bounds_with_their_basis(self):
        _, out = self.sample()
        labels = self.labels(out)
        code, report = self.main('bound', '--sample', out, '--labels', labels, '--flagged-true-positives', 100)
        self.assertEqual(code, 0)
        self.assertEqual(report['flagged_true_positives'], 100)
        self.assertEqual((report['lower_recall'], report['upper_missed_share']), (100 / 168, 68 / 168))
        self.assertIn('whole flagged set', report['flagged_true_positives_basis'])
        code, plain = self.main('bound', '--sample', out, '--labels', labels)
        self.assertEqual({key: value for key, value in report.items() if key in plain}, plain)
        self.assertFalse({'flagged_true_positives', 'lower_recall', 'upper_missed_share'} & set(plain))
        _, census = self.sample(count=10, k=10, seed='beef')
        code, report = self.main('bound', '--sample', census, '--labels', self.labels(census, name='census.json'),
                                 '--flagged-true-positives', 0)
        self.assertEqual((code, report['upper_misses'], report['lower_recall'], report['upper_missed_share']),
                         (0, 0, None, None))

    def test_labels_cover_every_sampled_id_exactly_once(self):
        _, out = self.sample(count=30, k=5)
        ids = [item['id'] for item in json.loads(out.read_text(encoding='utf-8'))['items']]
        full = {item_id: 'ok' for item_id in ids}
        cases = {
            'labels_incomplete': {'labels': {item_id: 'ok' for item_id in ids[1:]}},
            'labels_extra': {'labels': {**full, 'r999': 'ok'}},
            'invalid_label_value': {'labels': {**full, ids[0]: 'maybe'}},
            'invalid_label_value:empty': {'labels': {**full, ids[0]: ''}},
            'invalid_labels': full,
            'invalid_labels:list': {'labels': ids},
            'invalid_labels:extra-key': {'labels': full, 'note': 'x'},
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                self.refused(label.split(':')[0], 'bound', '--sample', out, '--labels', self.write('l.json', data))
        repeated = ('{"labels": {%s, "%s": "miss"}}' % (', '.join('"%s": "ok"' % item_id for item_id in ids), ids[0]))
        self.refused('labels_invalid_json', 'bound', '--sample', out, '--labels',
                     self.write('r.json', repeated.encode()))

    def test_a_changed_sample_or_population_is_refused(self):
        population, out = self.sample(count=30, k=5)
        sample = json.loads(out.read_text(encoding='utf-8'))
        drawn = {item['id'] for item in sample['items']}
        outsider = next(item for item in rows(30) if item['id'] not in drawn)
        swapped = {**sample, 'items': [outsider, *sample['items'][1:]]}
        labels = self.write('swapped-labels.json', {'labels': {item['id']: 'ok' for item in swapped['items']}})
        self.refused('sample_mismatch', 'bound', '--sample', self.write('swapped.json', swapped), '--labels', labels,
                     '--population', population)
        self.refused('population_mismatch', 'bound', '--sample', out, '--labels', self.labels(out),
                     '--population', self.population(rows(31), name='grown.json'))
        reseeded = {**sample, 'seed': 'beef'}
        self.refused('sample_mismatch', 'bound', '--sample', self.write('reseeded.json', reseeded),
                     '--labels', self.labels(out), '--population', population)
        tampered = {
            'item_field_not_allowed': {**sample, 'items': [{**sample['items'][0], 'label': 'ok'},
                                                           *sample['items'][1:]]},
            'invalid_sample': {**sample, 'labels': {}},
            'invalid_sample:k': {**sample, 'k': 4},
            'invalid_sample:n': {**sample, 'N': 4},
            'invalid_sample:tool': {**sample, 'tool': 'other'},
            'invalid_sample:method': {**sample, 'draw_method': 'hand-picked'},
            'invalid_sample:version': {**sample, 'version': True},
            'invalid_sample:seed': {**sample, 'seed': 'XYZ'},
            'duplicate_item_id': {**sample, 'items': [sample['items'][0]] * 5},
        }
        for label, data in tampered.items():
            with self.subTest(case=label):
                self.refused(label.split(':')[0], 'bound', '--sample', self.write('t.json', data),
                             '--labels', self.labels(out))

    def test_option_values_are_validated(self):
        _, out = self.sample(count=30, k=5)
        labels = self.labels(out)
        for value in ('0.5', '1', '1.5', '-0.95', 'nan', 'inf'):
            with self.subTest(confidence=value):
                self.refused('invalid_confidence', 'bound', '--sample', out, '--labels', labels,
                             '--confidence', value)
        self.refused('invalid_true_positives', 'bound', '--sample', out, '--labels', labels,
                     '--flagged-true-positives', -1)
        for args in (('--confidence', 'high'), ('--flagged-true-positives', '1.5')):
            with self.subTest(args=args):
                self.refused('invalid_arguments', 'bound', '--sample', out, '--labels', labels, *args)
        self.refused('sample_unreadable', 'bound', '--sample', self.dir / 'missing.json', '--labels', labels)


class PlanTests(AuditCase):
    FIVE_PERCENT = ('--population-size', 500, '--target-share', 0.05)

    def plan(self, *args):
        code, report = self.main('plan', *args)
        self.assertEqual(code, 0, report)
        return report

    def test_sizes_for_a_five_percent_target_among_500_items(self):
        for misses, (size, strict) in {0: (54, 56), 1: (84, 87), 2: (110, 114), 3: (135, 139)}.items():
            with self.subTest(expected_misses=misses):
                report = self.plan(*self.FIVE_PERCENT, '--expected-misses', misses)
                self.assertEqual(set(report), PLAN_KEYS)
                self.assertEqual({key: report[key] for key in PLAN_KEYS - {'basis', 'warnings'}}, {
                    'tool': 'real_vault_audit', 'command': 'plan', 'version': 1, 'N': 500, 'target_share': 0.05,
                    'target_misses': 25, 'ltpd_misses': 25, 'expected_misses': misses, 'confidence': 0.95,
                    'alpha': 0.05, 'sample_size': size, 'sample_size_reason': None,
                    'strict_ltpd_sample_size': strict, 'strict_ltpd_sample_size_reason': None,
                    'authority': 'human_judgement_input_not_gate'})
                # bound's own test passes at the planned size and fails one item earlier.
                self.assertLessEqual(stats.hypergeometric_upper_bound(misses, size, 500)['upper_misses'], 25)
                self.assertGreater(stats.hypergeometric_upper_bound(misses, size - 1, 500)['upper_misses'], 25)
                # The consumer's risk at K = 25, from an independent fraction, first reaches 1/20 at the strict size.
                self.assertLessEqual(hypergeometric_cdf(500, 25, strict, misses), Fraction(1, 20))
                self.assertGreater(hypergeometric_cdf(500, 25, strict - 1, misses), Fraction(1, 20))
        defaults = self.plan(*self.FIVE_PERCENT)
        self.assertEqual(defaults, self.plan(*self.FIVE_PERCENT, '--expected-misses', 0, '--confidence', 0.95))
        self.assertEqual((defaults['basis'], defaults['warnings']),
                         (self.tool.PLAN_BASIS, list(self.tool.PLAN_WARNINGS)))
        self.assertIn('NIST/SEMATECH e-Handbook of Statistical Methods', defaults['basis'])
        warnings = ' '.join(defaults['warnings'])
        for phrase in ('not a guarantee', 'uniform random draw', 'complete and correct labels', 'population fixed',
                       'before labelling', 'do not redraw or change k', 'sample_sha256', 'N in the draw summary'):
            with self.subTest(warning=phrase):
                self.assertIn(phrase, warnings)
        lower = self.plan(*self.FIVE_PERCENT, '--confidence', 0.9)
        self.assertEqual((lower['alpha'], lower['sample_size'], lower['strict_ltpd_sample_size']), (0.1, 42, 43))

    def test_the_oc_table_is_exact_at_the_planned_size(self):
        report = self.plan(*self.FIVE_PERCENT, '--oc', '5,25,26,0,500, 25')
        self.assertEqual(set(report), PLAN_KEYS | {'oc', 'oc_basis'})
        self.assertEqual((report['sample_size'], report['oc_basis']), (54, self.tool.OC_BASIS))
        self.assertEqual([point['K'] for point in report['oc']], [5, 25, 26, 0, 500, 25])
        for point in report['oc']:
            with self.subTest(K=point['K']):
                self.assertEqual(set(point), {'K', 'acceptance_probability'})
                self.assertEqual(point['acceptance_probability'], float(hypergeometric_cdf(500, point['K'], 54, 0)))
        self.assertEqual([round(point['acceptance_probability'], 6) for point in report['oc']],
                         [0.563334, 0.053264, 0.047209, 1.0, 0.0, 0.053264])
        # 54 items are no '5% LTPD at 5% consumer's risk' plan: 25 misses pass with probability 0.053264.
        # With 5 misses (1%) the audit still reports the target missed with probability about 0.44.
        for misses, size, accepted in ((1, 84, 0.801821), (2, 110, 0.926561)):
            with self.subTest(expected_misses=misses):
                report = self.plan(*self.FIVE_PERCENT, '--expected-misses', misses, '--oc', 5)
                self.assertEqual(report['sample_size'], size)
                self.assertEqual(round(report['oc'][0]['acceptance_probability'], 6), accepted)
        self.assertFalse({'oc', 'oc_basis'} & set(self.plan(*self.FIVE_PERCENT)))

    def test_at_the_planned_size_x_or_fewer_misses_is_exactly_what_passes(self):
        for misses in range(4):
            with self.subTest(expected_misses=misses):
                size = self.plan(*self.FIVE_PERCENT, '--expected-misses', misses)['sample_size']
                passing = [x for x in range(size + 1)
                           if stats.hypergeometric_upper_bound(x, size, 500)['upper_misses'] <= 25]
                self.assertEqual(passing, list(range(misses + 1)))

    def test_the_share_is_read_from_its_decimal_text(self):
        # Float products would move these boundaries.
        self.assertEqual((math.floor(0.29 * 100), math.floor(0.57 * 100), math.ceil(0.07 * 100)), (28, 56, 8))
        cases = {(100, 0.29): (29, 29, 9, 9), (100, 0.57): (57, 57, 4, 4), (100, 0.07): (7, 7, 31, 34),
                 (499, 0.05): (24, 25, 55, 55),
                 # P(X = 0 | 1 miss) is (500 - k) / 500: bound needs < 1/20 (k = 476), the strict size <= 1/20 (475).
                 (500, 0.001): (0, 1, 476, 475)}
        for (size, share), expected in cases.items():
            with self.subTest(N=size, share=share):
                report = self.plan('--population-size', size, '--target-share', share)
                self.assertEqual((report['target_misses'], report['ltpd_misses'], report['sample_size'],
                                  report['strict_ltpd_sample_size']), expected)

    def test_the_large_lot_binomial_plan_is_larger_for_500_items(self):
        # NIST/SEMATECH 6.2.3.2 takes N as very large next to n and uses the binomial: at p = 0.05 with a 5%
        # consumer's risk it asks for 59, 93 and 124 items for c = 0, 1 and 2, above the exact 56, 87 and 114.
        share, risk = Fraction(1, 20), Fraction(1, 20)

        def binomial_size(c):
            return next(n for n in range(c + 1, 1000) if sum(
                math.comb(n, d) * share ** d * (1 - share) ** (n - d) for d in range(c + 1)) <= risk)
        self.assertEqual([binomial_size(c) for c in range(3)], [59, 93, 124])
        self.assertEqual([self.plan(*self.FIVE_PERCENT, '--expected-misses', misses)['strict_ltpd_sample_size']
                          for misses in range(3)], [56, 87, 114])
        # With 200,000 items both exact sizes are the binomial 59.
        report = self.plan('--population-size', 200_000, '--target-share', 0.05)
        self.assertEqual((report['sample_size'], report['strict_ltpd_sample_size']), (59, 59))

    def test_the_documented_bounds_come_back_as_planned_sizes(self):
        # K_U(0, 20) = 68, K_U(0, 40) = 34 and K_U(1, 20) = 106 among 500 items (bound tests above).
        for share, misses, size in ((0.136, 0, 20), (0.068, 0, 40), (0.212, 1, 20)):
            with self.subTest(share=share, expected_misses=misses):
                report = self.plan('--population-size', 500, '--target-share', share, '--expected-misses', misses)
                self.assertEqual(report['sample_size'], size)
                self.assertEqual(stats.hypergeometric_upper_bound(misses, size, 500)['upper_misses'],
                                 report['target_misses'])

    def test_a_size_without_a_solution_is_null_with_a_reason_and_exit_code_0(self):
        cases = {
            # More expected misses than the target: no sample size passes, and neither does the strict size.
            (500, 0.05, 26): (None, 'expected_misses_exceed_target', None, 'expected_misses_reach_ltpd'),
            # x = target = ltpd: only a census passes bound, and P(X <= x | K = x) is 1 for every k.
            (500, 0.05, 25): (500, None, None, 'expected_misses_reach_ltpd'),
            # Beyond the 5,000 items draw takes.
            (200_000, 0.0005, 0): (None, 'exceeds_draw_maximum', None, 'exceeds_draw_maximum'),
            (200_000, 0.5, 6_000): (None, 'exceeds_draw_maximum', None, 'exceeds_draw_maximum'),
        }
        for (size, share, misses), expected in cases.items():
            with self.subTest(N=size, share=share, expected_misses=misses):
                result = self.cli('plan', '--population-size', size, '--target-share', share,
                                  '--expected-misses', misses, '--oc', 0)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual((report['sample_size'], report['sample_size_reason'],
                                  report['strict_ltpd_sample_size'], report['strict_ltpd_sample_size_reason']),
                                 expected)
                self.assertEqual(report['oc'],
                                 None if expected[0] is None else [{'K': 0, 'acceptance_probability': 1.0}])
        self.assertEqual(self.plan(*self.FIVE_PERCENT, '--expected-misses', 500)['sample_size_reason'],
                         'expected_misses_exceed_target')

    def test_the_bisection_equals_linear_scans_on_small_populations(self):
        # Every N up to 40, five shares and x up to 3: the smallest k from max(x, 1) to N, scanned with independent
        # fractions. bound's K_U is at most target_misses exactly when P(X <= x | N, target_misses + 1, k) < alpha,
        # and target_misses + 1 <= N as the share is below 1; the strict size takes K = ltpd_misses. A linear scan
        # of bound's own test must give the same sample_size. Below draw's maximum a size is null only through x.
        cases, nulls, mismatches = 0, 0, []
        with mock.patch.object(self.tool, '_load_eval_stats', return_value=stats):
            for size in range(1, 41):
                for share in ('0.01', '0.05', '0.1', '0.29', '0.5'):
                    exact = Fraction(share)
                    target = exact.numerator * size // exact.denominator
                    ltpd = -(-exact.numerator * size // exact.denominator)
                    for misses in range(min(size, 3) + 1):
                        scan = range(max(misses, 1), size + 1)
                        bounds = {k: stats.hypergeometric_upper_bound(misses, k, size)['upper_misses'] for k in scan}
                        expected = (
                            next((k for k in scan
                                  if hypergeometric_cdf(size, target + 1, k, misses) < Fraction(1, 20)), None),
                            next((k for k in scan if hypergeometric_cdf(size, ltpd, k, misses) <= Fraction(1, 20)),
                                 None),
                            None if misses <= target else 'expected_misses_exceed_target',
                            None if misses < ltpd else 'expected_misses_reach_ltpd')
                        scanned = next((k for k in scan if bounds[k] <= target), None)
                        report = self.tool.plan(argparse.Namespace(
                            population_size=size, target_share=float(share), expected_misses=misses,
                            confidence=0.95, oc=None))
                        actual = (report['sample_size'], report['strict_ltpd_sample_size'],
                                  report['sample_size_reason'], report['strict_ltpd_sample_size_reason'])
                        cases += 1
                        nulls += (expected[0] is None) + (expected[1] is None)
                        if (actual != expected or scanned != expected[0]
                                or (report['target_misses'], report['ltpd_misses']) != (target, ltpd)):
                            mismatches.append((size, share, misses, actual, expected, scanned))
        self.assertEqual(mismatches, [])
        # 785 plans with 1,570 sizes, 584 of them null: both branches are covered.
        self.assertEqual((cases, nulls), (785, 584))

    def test_a_planned_draw_and_its_bound(self):
        planned = self.plan(*self.FIVE_PERCENT)
        population = self.population(rows(500))
        out, summary = self.draw(population, planned['sample_size'], '1707')
        self.assertEqual((summary['N'], summary['k']), (planned['N'], 54))
        # No miss bounds the misses at 25 = target_misses; one miss at 40, above it.
        for misses, upper in ((set(), 25), ({7}, 40)):
            with self.subTest(x=len(misses)):
                labels = self.labels(out, misses=misses, name='labels-%d.json' % len(misses))
                code, report = self.main('bound', '--sample', out, '--labels', labels, '--population', population)
                self.assertEqual((code, report['k'], report['x'], report['population_verified']),
                                 (0, 54, len(misses), True))
                self.assertEqual(report['upper_misses'], upper)

    def test_invalid_plan_arguments_are_refused_with_content_free_codes(self):
        cases = [('invalid_population_size', ('--population-size', value, '--target-share', 0.05))
                 for value in (0, -5, 200_001)]
        cases += [('invalid_target_share', ('--population-size', 500, '--target-share', value))
                  for value in ('0', '0.0', '1', '1.5', '-0.05', 'nan', 'inf')]
        cases += [('invalid_expected_misses', (*self.FIVE_PERCENT, '--expected-misses', value)) for value in (-1, 501)]
        cases += [('invalid_confidence', (*self.FIVE_PERCENT, '--confidence', value)) for value in ('0.5', '1', 'nan')]
        cases += [('invalid_oc', (*self.FIVE_PERCENT, '--oc', value))
                  for value in ('', 'a', '5,', ',5', '5,,25', '5;25', '1.5', '-1', '501', '0x10', '1_0',
                                '\N{FULLWIDTH DIGIT FIVE}', ','.join(['1'] * 101))]
        cases += [('invalid_arguments', args) for args in (
            (), ('--population-size', 500), ('--target-share', 0.05),
            ('--population-size', 'many', '--target-share', 0.05),
            ('--population-size', 500, '--target-share', 'small'), (*self.FIVE_PERCENT, '--expected-misses', '1.5'),
            (*self.FIVE_PERCENT, '--confidence', 'high'), (*self.FIVE_PERCENT, '--oc'), (*self.FIVE_PERCENT, 'extra'))]
        for code, args in cases:
            with self.subTest(code=code, args=args):
                self.refused(code, 'plan', *args)
        self.assertEqual(len(self.plan(*self.FIVE_PERCENT, '--oc', ','.join(['1'] * 100))['oc']), 100)
        self.assertEqual(self.plan('--population-size', 200_000, '--target-share', 0.5)['N'], 200_000)

    def test_plan_opens_no_input_file_and_writes_no_output_file(self):
        # plan still loads scripts/eval_stats.py, as draw and bound do. importlib reads it through io.open_code,
        # which this open patch does not see, and without -B Python may write its bytecode cache.
        with mock.patch('builtins.open', side_effect=AssertionError('plan opened a file')):
            code, report = self.main('plan', *self.FIVE_PERCENT, '--oc', 5)
        self.assertEqual((code, report['sample_size']), (0, 54))
        result = subprocess.run([sys.executable, '-B', str(TOOL), 'plan', '--population-size', '500',
                                 '--target-share', '0.05'], cwd=self.dir, capture_output=True, text=True,
                                encoding='utf-8', timeout=120)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(json.loads(result.stdout)['sample_size'], 54)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_plan_usage_errors_exit_2_without_echo_and_help_works(self):
        # Before the dispatch named plan, every command but draw ran bound: a traceback and exit code 1.
        sentinel = 'SYNTHETIC_ECHO_SENTINEL_' + uuid.uuid4().hex
        for args, code in ((('plan',), 'invalid_arguments'), (('plan', '--' + sentinel), 'invalid_arguments'),
                           (('plan', '--population-size', sentinel, '--target-share', 0.05), 'invalid_arguments'),
                           (('plan', '--population-size', 500, '--target-share', sentinel), 'invalid_arguments'),
                           (('plan', *self.FIVE_PERCENT, '--oc', sentinel), 'invalid_oc')):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': code})
                self.assertNotIn(sentinel, result.stdout + result.stderr)
        result = self.cli('plan', *self.FIVE_PERCENT)
        self.assertEqual((result.returncode, result.stderr, json.loads(result.stdout)['command']), (0, '', 'plan'))
        result = self.cli('plan', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ('--population-size', '--target-share', '--expected-misses', '--confidence', '--oc'):
            with self.subTest(option=option):
                self.assertIn(option, result.stdout)
        result = self.cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('{draw,bound,plan}', result.stdout)


class IsolationTests(AuditCase):
    def test_usage_errors_exit_2_without_echo_and_help_works(self):
        sentinel = 'SYNTHETIC_ECHO_SENTINEL_' + uuid.uuid4().hex
        for args in ((), ('audit',), ('draw', '--' + sentinel), ('draw', '--population', sentinel),
                     ('draw', '--population', sentinel, '--k', sentinel, '--out', sentinel),
                     ('bound', '--sample', sentinel)):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': 'invalid_arguments'})
                self.assertNotIn(sentinel, result.stdout + result.stderr)
        for args in (('--help',), ('draw', '--help'), ('bound', '--help')):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--flagged-true-positives', self.cli('bound', '--help').stdout)
        self.assertIn('--confidence', self.cli('draw', '--help').stdout)

    def test_not_wired_into_hooks_ci_skills_or_plugin_resources(self):
        watched = [ROOT / '.github' / 'workflows' / 'tests.yml', ROOT / 'scripts' / 'verify_codex_plugin.py',
                   ROOT / 'tests' / 'test_codex_verification.py']
        for folder in ('hooks', 'skills', 'commands', 'assets'):
            watched += [path for path in (ROOT / folder).rglob('*') if path.is_file()]
        for path in watched:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertNotIn(b'real_vault_audit', path.read_bytes())


if __name__ == '__main__':
    unittest.main()
