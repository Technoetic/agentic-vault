"""scripts/real_vault_audit.py: seeded audit samples and exact hypergeometric miss bounds.

A repository evaluation tool like flaky_repeat.py. draw writes item
references only (path, line, id, match) and records the commitment made
before labelling (seed, k and confidence, with a digest of the whole sample);
bound needs every sampled id labelled and prints counts, the seed and digests,
never ids, labels or note text. Every population here is synthetic; nothing
reads a real vault.
"""
from __future__ import annotations

from collections import Counter
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
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
