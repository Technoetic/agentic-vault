from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
HOOKS = ROOT / 'hooks'
SCRIPT = SCRIPTS / 'vault_handoff_fold.py'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

HANDOFF = '50-projects/Demo/Demo handoff.md'
ARCHIVE_DIR = '50-projects/Demo/handoff-archive'
FOLDED_AT = '2026-02-03T04:05:06Z'
FRONTMATTER = ('---\ntitle: "Demo handoff"\ntype: project\ndomain: demo\nstatus: active\n'
               'ai_priority: high\ntags: [handoff]\nproject: Demo\n'
               'created: 2026-01-01\nupdated: 2026-01-02\n---\n\n# Demo handoff\n\n'
               '**anchor:** `abc1234`\n\n')


FILLER = 'Working detail that no longer needs to sit in the handoff. ' * 6


def section(number, *, body=None, unresolved=True):
    lines = [f'## 2026-01-{number:02d} session {number}', '',
             body or f'Finished work item {number} after {number + 10} checks.', '', FILLER]
    if unresolved:
        lines.append(f'- unresolved: open question {number}')
    return '\n'.join(lines) + '\n\n'


def document(count, **kwargs):
    return (FRONTMATTER + ''.join(section(number, **kwargs)
                                  for number in range(count, 0, -1))).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='av-handoff-fold-')
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.config()
        self.fold = importlib.import_module('vault_handoff_fold')
        self.state = importlib.import_module('vault_state')

    def config(self, **extra):
        self.write('00-meta/vault-config.json',
                   json.dumps({'handoff_note': HANDOFF, **extra}).encode('utf-8'))

    def write(self, relative, raw):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def handoff(self):
        return (self.vault / HANDOFF).read_bytes()

    def archives(self):
        folder = self.vault / ARCHIVE_DIR
        if not folder.is_dir():
            return {}
        return {path.stem: path.read_bytes() for path in sorted(folder.iterdir())}

    def tree(self):
        return {path.relative_to(self.vault).as_posix(): path.read_bytes()
                for path in sorted(self.vault.rglob('*')) if path.is_file()}

    def analyze(self, **kwargs):
        settings = {'threshold_bytes': 1, 'protect_sections': 2}
        settings.update(kwargs)
        return self.fold.analyze(self.vault, **settings)

    def apply(self, *, expected=None, **kwargs):
        settings = {'threshold_bytes': 1, 'protect_sections': 2, 'folded_at': FOLDED_AT,
                    'expected_sha256': expected or sha(self.handoff())}
        settings.update(kwargs)
        return self.fold.apply(self.vault, **settings)

    def restore(self):
        return self.fold.restore_handoff(self.handoff(), self.archives())

    def assertCode(self, code, call, *args, **kwargs):
        with self.assertRaises(self.state.StateError) as caught:
            call(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)


class SplitTests(Base):
    def test_heading_rule_matches_the_session_hook(self):
        session_start = importlib.import_module('session_start')
        corpus = '\n'.join([
            '# one', '## two ##', '###### six', '####### seven', '#nospace', '## ', '   ## three spaces',
            '    ## four spaces', '```', '## fenced', '```', '~~~~', '## tilde', '~~~', '## still tilde',
            '~~~~~', '## tab\there', '##\ttab', '## a # b', '## closed #', '## 한글 제목', 'plain ## text',
            '```python', '## code', '``` ', '## after',
        ])
        expected = session_start._heading_index(corpus)
        observed = self.fold.heading_index(corpus.encode('utf-8'))
        self.assertEqual([(start, title) for start, _, title in observed], [
            (len(corpus[:start].encode('utf-8')), title) for start, title in expected])

    def test_heading_rule_holds_for_every_line_ending(self):
        session_start = importlib.import_module('session_start')
        text = '# one\n```\n## no\n```\n## two\nbody\n'
        titles = [title for _, title in session_start._heading_index(text)]
        for eol in ('\n', '\r\n', '\r'):
            raw = text.replace('\n', eol).encode('utf-8')
            self.assertEqual([title for _, _, title in self.fold.heading_index(raw)], titles)

    def test_sections_keep_every_byte(self):
        mixed = FRONTMATTER + '## one\r\nbody\r\n\r\n## two\nbody\r## three\nlast line no newline'
        for raw in (mixed.encode('utf-8'), b'\xef\xbb\xbf' + mixed.encode('utf-8')):
            start, sections = self.fold.split_sections(raw)
            self.assertEqual(raw[:start] + b''.join(raw[item.start:item.end] for item in sections), raw)
            self.assertEqual([item.title for item in sections], ['one', 'two', 'three'])

    def test_frontmatter_lines_are_not_sections(self):
        raw = b'---\ntitle: x\n## not a heading\n---\n## real\nbody\n'
        self.assertEqual([item.title for item in self.fold.split_sections(raw)[1]], ['real'])

    def test_level_one_and_three_headings_stay_inside_a_section(self):
        raw = b'# title\n## a\n### child\n# other\n## b\n'
        self.assertEqual([item.title for item in self.fold.split_sections(raw)[1]], ['a', 'b'])


class RequiredValueTests(Base):
    def test_threshold_and_protection_have_no_default(self):
        self.write(HANDOFF, document(6))
        before = self.tree()
        self.assertCode('fold_threshold_required', self.fold.analyze, self.vault, protect_sections=2)
        self.assertCode('fold_protect_required', self.fold.analyze, self.vault, threshold_bytes=1)
        self.assertCode('fold_threshold_required', self.fold.apply, self.vault, protect_sections=2,
                        expected_sha256=sha(self.handoff()))
        self.assertCode('fold_protect_required', self.fold.apply, self.vault, threshold_bytes=1,
                        expected_sha256=sha(self.handoff()))
        self.assertEqual(self.tree(), before)

    def test_invalid_values_use_fixed_codes(self):
        self.write(HANDOFF, document(6))
        for key, value, code in (('threshold_bytes', 0, 'invalid_fold_threshold'),
                                 ('threshold_bytes', True, 'invalid_fold_threshold'),
                                 ('protect_sections', 0, 'invalid_fold_protect'),
                                 ('protect_sections', '2', 'invalid_fold_protect'),
                                 ('newest', 'middle', 'invalid_fold_order'),
                                 ('archive_max_bytes', 10, 'invalid_archive_limit')):
            self.assertCode(code, self.analyze, **{key: value})

    def test_apply_needs_the_expected_hash(self):
        self.write(HANDOFF, document(6))
        before = self.tree()
        self.assertCode('expected_sha256_required', self.fold.apply, self.vault,
                        threshold_bytes=1, protect_sections=2)
        self.assertCode('stale_base', self.apply, expected='0' * 64)
        self.assertEqual(self.tree(), before)

    def test_unset_and_missing_handoff(self):
        self.assertCode('handoff_missing', self.analyze)
        self.config(handoff_note='')
        self.assertCode('handoff_note_unset', self.analyze)

    def test_invalid_fold_config_is_refused(self):
        self.write(HANDOFF, document(4))
        for value in ('x', {'unknown': []}, {'unresolved_prefixes': 'x'},
                      {'unresolved_prefixes': []}, {'protect_titles': [1]}):
            self.config(handoff_fold=value)
            self.assertCode('invalid_fold_config', self.analyze)

    def test_non_utf8_handoff_is_refused(self):
        self.write(HANDOFF, document(4) + b'\xff')
        self.assertCode('invalid_encoding', self.analyze)


class AnalyzeTests(Base):
    def test_analyze_reports_candidates_and_writes_nothing(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        before = self.tree()
        result = self.analyze()
        self.assertEqual(self.tree(), before)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['authority'], 'preview_data_only')
        self.assertEqual(result['sha256'], sha(raw))
        self.assertEqual(result['bytes'], len(raw))
        self.assertEqual(result['sections_total'], 5)
        self.assertEqual(result['protected'], ['2026-01-05 session 5', '2026-01-04 session 4'])
        self.assertEqual([row['title'] for row in result['candidates']],
                         ['2026-01-03 session 3', '2026-01-02 session 2', '2026-01-01 session 1'])
        for row in result['candidates']:
            self.assertEqual(sha(raw[row['start']:row['end']]), row['sha256'])
            self.assertEqual(row['bytes'], row['end'] - row['start'])
            self.assertEqual(row['unresolved_lines'], 1)
        self.assertEqual(result['unresolved_lines'], 3)
        self.assertEqual(result['archive_notes'][0]['path'], ARCHIVE_DIR + '/Demo handoff archive 0001.md')

    def test_below_threshold_does_nothing(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        before = self.tree()
        self.assertEqual(self.analyze(threshold_bytes=len(raw) + 1)['status'], 'below_threshold')
        self.assertEqual(self.analyze(threshold_bytes=len(raw) + 1)['candidates'], [])
        self.assertEqual(self.apply(threshold_bytes=len(raw) + 1)['status'], 'below_threshold')
        self.assertEqual({key: value for key, value in self.tree().items() if '.agentic-vault' not in key}, before)

    def test_all_sections_protected(self):
        self.write(HANDOFF, document(3))
        self.assertEqual(self.analyze(protect_sections=3)['status'], 'nothing_to_fold')

    def test_a_fold_that_would_not_shrink_is_not_offered(self):
        self.write(HANDOFF, FRONTMATTER.encode('utf-8') + b'## a\nx\n## b\ny\n## c\nz\n')
        self.assertEqual(self.analyze(protect_sections=1)['status'], 'no_reduction')
        before = self.handoff()
        self.assertEqual(self.apply(protect_sections=1)['status'], 'no_reduction')
        self.assertEqual(self.handoff(), before)
        self.assertEqual(self.archives(), {})

    def test_newest_at_the_bottom(self):
        raw = FRONTMATTER.encode('utf-8') + ''.join(section(number) for number in range(1, 5)).encode('utf-8')
        self.write(HANDOFF, raw)
        result = self.analyze(newest='bottom')
        self.assertEqual(result['protected'], ['2026-01-03 session 3', '2026-01-04 session 4'])
        self.assertEqual(len(result['candidates']), 2)


class ApplyTests(Base):
    def test_round_trip_restores_every_byte(self):
        lf = document(6)
        cases = {
            'lf': lf,
            'crlf': lf.replace(b'\n', b'\r\n'),
            'bom': b'\xef\xbb\xbf' + lf,
            'no final newline': lf.rstrip(b'\n'),
            'mixed': lf.replace(b'session 2\n', b'session 2\r\n'),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                for path in self.archives():
                    (self.vault / ARCHIVE_DIR / (path + '.md')).unlink()
                self.write(HANDOFF, raw)
                result = self.apply()
                self.assertEqual(result['status'], 'applied')
                self.assertEqual(result['before_sha256'], sha(raw))
                self.assertEqual(result['after_sha256'], sha(self.handoff()))
                self.assertNotEqual(self.handoff(), raw)
                self.assertEqual(self.restore(), raw)

    def test_sections_move_byte_for_byte_and_protected_stay(self):
        raw = document(6)
        self.write(HANDOFF, raw)
        _, sections = self.fold.split_sections(raw)
        self.apply()
        body = b''.join(self.fold.archive_sections(note).get(item.sha256, b'')
                        for note in self.archives().values() for item in sections)
        for item in sections[2:]:
            self.assertIn(raw[item.start:item.end], body)
        after = self.handoff()
        self.assertTrue(after.startswith(raw[:sections[2].start]))
        self.assertNotIn(b'Finished work item 1 ', after)
        self.assertIn(b'Finished work item 5 ', after)

    def test_archive_note_has_valid_frontmatter(self):
        healthcheck = importlib.import_module('vault_healthcheck')
        self.write(HANDOFF, document(6))
        self.apply()
        [(name, note)] = self.archives().items()
        self.assertEqual(name, 'Demo handoff archive 0001')
        frontmatter, _ = healthcheck.split_frontmatter(note.decode('utf-8'))
        keys = healthcheck.parse_simple_yaml_keys(frontmatter)
        config = healthcheck.validate_config(json.loads(
            (self.vault / '00-meta/vault-config.json').read_text(encoding='utf-8')))
        for key in config['required_keys']:
            self.assertIn(key, keys)
        for key, allowed in config['enums'].items():
            self.assertIn(keys[key], allowed)
        self.assertEqual(keys['status'], 'archive')
        self.assertEqual(keys['created'], '2026-02-03')
        self.assertEqual(keys['project'], 'Demo')
        self.assertEqual(keys['domain'], 'demo')
        self.assertIn('related: ["[[Demo handoff]]"]', frontmatter)

    def test_frontmatter_copies_other_required_keys_and_enum_limits(self):
        self.config(required_keys=['title', 'type', 'status', 'ai_priority', 'tags', 'created', 'updated', 'domain'],
                    enums={'status': ['active', 'done']})
        self.write(HANDOFF, document(6))
        self.apply()
        [note] = self.archives().values()
        self.assertIn(b'domain: demo\n', note)
        self.assertIn(b'status: done\n', note)

    def test_missing_required_key_refuses_before_any_write(self):
        self.config(required_keys=['title', 'owner'])
        self.write(HANDOFF, document(6))
        before = self.handoff()
        self.assertCode('archive_frontmatter_unavailable', self.apply)
        self.assertEqual(self.handoff(), before)
        self.assertEqual(self.archives(), {})

    def test_stub_keeps_time_title_hash_link_and_count(self):
        raw = document(4)
        self.write(HANDOFF, raw)
        _, sections = self.fold.split_sections(raw)
        self.apply()
        after = self.handoff().decode('utf-8')
        target = sections[3]
        lines = after.split('\n')
        heading = lines.index('## 2026-01-01 session 1')
        self.assertEqual(lines[heading + 1],
                         f'- [handoff-fold] {FOLDED_AT} sha256:{target.sha256[:12]} '
                         '[[Demo handoff archive 0001]] unresolved=1')
        self.assertEqual(lines[heading + 2], '')

    def test_host_summary_is_bound_to_the_section_hash(self):
        raw = document(4)
        self.write(HANDOFF, raw)
        _, sections = self.fold.split_sections(raw)
        digest = sections[3].sha256
        before = self.handoff()
        self.assertCode('unknown_summary_section', self.apply, summaries={'f' * 64: 'x'})
        for bad in ('', 'two\nlines', 'x' * 5000, 7):
            self.assertCode('invalid_summary', self.apply, summaries={digest: bad})
        self.assertCode('invalid_summary', self.apply, summaries={'short': 'x'})
        self.assertEqual(self.handoff(), before)
        self.apply(summaries={digest: 'Host wrote this line.'})
        after = self.handoff().decode('utf-8').split('\n')
        heading = after.index('## 2026-01-01 session 1')
        self.assertEqual(after[heading + 2], '  - summary: Host wrote this line.')
        self.assertNotIn('Host wrote this line.', '\n'.join(after[:heading]))
        self.assertEqual(self.restore(), raw)

    def test_without_a_summary_only_the_title_and_unresolved_line_stay(self):
        raw = document(4, body='Body text that must leave the handoff.')
        self.write(HANDOFF, raw)
        self.apply()
        after = self.handoff()
        self.assertEqual(after.count(b'Body text that must leave the handoff.'), 2)
        self.assertIn(b'- unresolved: open question 1', after)
        self.assertIn(b'## 2026-01-01 session 1', after)

    def test_handoff_over_the_note_limit_folds(self):
        padding = 'Long body line with 12 facts.\n' * 1400
        raw = FRONTMATTER.encode('utf-8') + ''.join(
            section(number, body=padding) for number in range(28, 0, -1)).encode('utf-8')
        self.assertGreater(len(raw), 256 * 1024)
        self.write(HANDOFF, raw)
        self.assertEqual(self.analyze()['status'], 'ready')
        self.apply()
        self.assertLess(len(self.handoff()), 256 * 1024)
        self.assertEqual(self.restore(), raw)

    def test_oversized_handoff_is_refused_with_a_fixed_code(self):
        self.write(HANDOFF, document(2))
        with mock.patch.object(self.fold, 'MAX_HANDOFF_BYTES', 16):
            self.assertCode('handoff_too_large', self.analyze)

    def test_section_limit(self):
        self.write(HANDOFF, document(4))
        with mock.patch.object(self.fold, 'MAX_SECTIONS', 3):
            self.assertCode('section_limit', self.analyze)

    def test_archives_split_by_size(self):
        padding = 'Filler line for size.\n' * 40
        raw = FRONTMATTER.encode('utf-8') + ''.join(
            section(number, body=padding) for number in range(12, 0, -1)).encode('utf-8')
        self.write(HANDOFF, raw)
        limit = 3000
        result = self.apply(archive_max_bytes=limit)
        archives = self.archives()
        self.assertGreater(len(archives), 1)
        self.assertEqual(len(archives), len(result['archive_notes']))
        for note in archives.values():
            self.assertLessEqual(len(note), limit)
        self.assertEqual(self.restore(), raw)

    def test_default_archive_limit_is_256_kib(self):
        self.assertEqual(self.fold.DEFAULT_ARCHIVE_MAX_BYTES, 256 * 1024)

    def test_oversized_section_gets_its_own_note(self):
        huge = 'Huge section line.\n' * 400
        raw = FRONTMATTER.encode('utf-8') + (section(5) + section(4) + section(3)
                                             + section(2, body=huge) + section(1)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.apply(archive_max_bytes=2048)
        sizes = sorted(len(note) for note in self.archives().values())
        self.assertGreater(sizes[-1], 2048)
        lone = max(self.archives().values(), key=len)
        self.assertEqual(len(self.fold.archive_sections(lone)), 1)
        self.assertEqual(self.restore(), raw)

    def test_oversized_oldest_section_leaves_no_empty_note(self):
        huge = 'Huge section line.\n' * 400
        raw = FRONTMATTER.encode('utf-8') + (section(4) + section(3) + section(2)
                                             + section(1, body=huge)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.apply(archive_max_bytes=2048)
        for note in self.archives().values():
            self.assertTrue(self.fold.archive_sections(note))
        self.assertEqual(self.restore(), raw)

    def test_managed_block_and_named_sections_are_never_folded(self):
        self.config(handoff_fold={'protect_titles': ['2026-01-02 session 2']})
        managed = self.state.BEGIN + '\nblock\n' + self.state.END
        raw = document(5).replace(b'Finished work item 1 after 11 checks.', managed.encode('utf-8'))
        self.write(HANDOFF, raw)
        titles = [row['title'] for row in self.analyze()['candidates']]
        self.assertEqual(titles, ['2026-01-03 session 3'])

    def test_identical_sections_restore(self):
        raw = FRONTMATTER.encode('utf-8') + (section(9) + section(8) + section(1) + section(1)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.apply()
        self.assertEqual(self.restore(), raw)

    def test_hand_edited_archive_fails_restore(self):
        self.write(HANDOFF, document(5))
        self.apply()
        name, note = next(iter(self.archives().items()))
        self.assertCode('archive_hash_mismatch', self.fold.restore_handoff, self.handoff(),
                        {name: note.replace(b'work item 1', b'work item X')})
        self.assertCode('invalid_archive', self.fold.restore_handoff, self.handoff(), {name: note + b'x'})
        self.assertCode('archive_section_missing', self.fold.restore_handoff, self.handoff(), {})

    def test_existing_archive_numbers_are_kept(self):
        kept = self.write(ARCHIVE_DIR + '/Demo handoff archive 0007.md', b'---\ntitle: kept\n---\n')
        self.write(HANDOFF, document(5))
        result = self.apply()
        self.assertEqual(result['archive_notes'][0]['path'], ARCHIVE_DIR + '/Demo handoff archive 0008.md')
        self.assertEqual(kept.read_bytes(), b'---\ntitle: kept\n---\n')

    def test_archive_collision_inside_the_lock_rolls_back(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        original = self.fold._next_archive_number
        def colliding(*args):
            self.write(ARCHIVE_DIR + '/Demo handoff archive 0001.md', b'---\ntitle: other\n---\n')
            return original(*args) - 1
        with mock.patch.object(self.fold, '_next_archive_number', colliding):
            self.assertCode('archive_exists', self.apply)
        self.assertEqual(self.handoff(), raw)
        self.assertEqual(self.archives()['Demo handoff archive 0001'], b'---\ntitle: other\n---\n')

    def test_failed_handoff_replace_removes_new_archives(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        replace = os.replace
        def failing(source, target):
            if Path(target).name == 'Demo handoff.md':
                raise PermissionError('blocked')
            return replace(source, target)
        with mock.patch.object(self.fold.os, 'replace', failing):
            with self.assertRaises((OSError, self.state.StateError)):
                self.apply()
        self.assertEqual(self.handoff(), raw)
        self.assertFalse((self.vault / ARCHIVE_DIR).exists())

    def test_restore_mismatch_refuses_before_any_write(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        with mock.patch.object(self.fold, 'restore_handoff', return_value=b'other'):
            self.assertCode('restore_mismatch', self.apply)
        self.assertEqual(self.handoff(), raw)
        self.assertEqual(self.archives(), {})

    def test_concurrent_writer_lock_blocks_apply(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        key = 'note:' + HANDOFF.casefold()
        with self.state.advisory_lock(self.vault, key):
            self.assertCode('lock_busy', self.apply)
        self.assertEqual(self.handoff(), raw)

    def test_change_during_apply_is_a_stale_base(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        original = self.fold.plan_fold
        def edit_then_plan(*args, **kwargs):
            plan = original(*args, **kwargs)
            self.write(HANDOFF, raw + b'late edit\n')
            return plan
        with mock.patch.object(self.fold, 'plan_fold', edit_then_plan):
            self.assertCode('stale_base', self.apply)
        self.assertEqual(self.handoff(), raw + b'late edit\n')
        self.assertEqual(self.archives(), {})

    def test_deny_zone_blocks_the_archive_folder(self):
        self.config(deny_zones=['handoff-archive'])
        raw = document(5)
        self.write(HANDOFF, raw)
        self.assertCode('unsafe_path', self.apply)
        self.assertEqual(self.handoff(), raw)

    def test_deny_zone_blocks_the_handoff(self):
        self.config(deny_zones=['50-projects/Demo'], handoff_note='')
        self.write('00-meta/vault-config.json', json.dumps({
            'handoff_note': '00-meta/h.md', 'exclude_dirs': ['00-meta/x']}).encode('utf-8'))
        self.write('00-meta/h.md', document(4))
        self.assertEqual(self.analyze()['path'], '00-meta/h.md')
        self.write('00-meta/vault-config.json', json.dumps({
            'handoff_note': '00-meta/x/h.md', 'exclude_dirs': ['00-meta/x']}).encode('utf-8'))
        self.write('00-meta/x/h.md', document(4))
        self.assertCode('unsafe_path', self.analyze)

    def test_stem_unusable_in_a_wiki_link_is_refused(self):
        name = '50-projects/Demo/Demo#1 handoff.md'
        self.config(handoff_note=name)
        self.write(name, document(4))
        self.assertCode('unsafe_archive_name', self.apply, expected=sha(document(4)))


class UnresolvedTests(Base):
    def test_lines_are_lifted_verbatim_with_the_source_hash(self):
        raw = document(4)
        self.write(HANDOFF, raw)
        _, sections = self.fold.split_sections(raw)
        result = self.apply()
        self.assertEqual(result['unresolved_lines_lifted'], 2)
        lines = self.handoff().decode('utf-8').split('\n')
        heading = lines.index('## 미결 모음')
        self.assertIn(f'- unresolved: open question 1 [fold-source: sha256:{sections[3].sha256[:12]}]',
                      lines[heading:])
        self.assertIn(f'- unresolved: open question 2 [fold-source: sha256:{sections[2].sha256[:12]}]',
                      lines[heading:])
        for number in (1, 2):
            self.assertIn(f'- unresolved: open question {number}'.encode('utf-8'), self.handoff())

    def test_collected_section_sits_after_the_protected_sections(self):
        self.write(HANDOFF, document(5))
        self.apply()
        titles = [item.title for item in self.fold.split_sections(self.handoff())[1]]
        self.assertEqual(titles[:3], ['2026-01-05 session 5', '2026-01-04 session 4', '미결 모음'])

    def test_no_unresolved_lines_means_no_collected_section(self):
        raw = document(5, unresolved=False)
        self.write(HANDOFF, raw)
        self.apply()
        self.assertNotIn('미결 모음'.encode('utf-8'), self.handoff())
        self.assertEqual(self.restore(), raw)

    def test_default_rule_reads_common_list_spellings_only(self):
        body = '\n'.join(['- unresolved: plain', '- `unresolved`: code', '- **unresolved:** bold',
                          'unresolved: bare', '> - unresolved: quoted', 'UNRESOLVED: upper',
                          '- [ ] unresolved: open box', '- [x] unresolved: closed box',
                          'The unresolved: word mid line', '- resolved: other'])
        raw = (FRONTMATTER + section(3) + section(2) + section(1, body=body, unresolved=False)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.assertEqual(self.analyze()['candidates'][0]['unresolved_lines'], 7)

    def test_nested_lines_follow_their_unresolved_line(self):
        body = '- unresolved:\n  - nested one\n  - nested two\n- other line'
        raw = (FRONTMATTER + section(3) + section(2) + section(1, body=body, unresolved=False)).encode('utf-8')
        self.write(HANDOFF, raw)
        result = self.apply()
        self.assertEqual(result['unresolved_lines_lifted'], 1)
        after = self.handoff()
        self.assertIn(b'  - nested one [fold-source: sha256:', after)
        self.assertIn(b'  - nested two [fold-source: sha256:', after)
        self.assertNotIn(b'- other line', after)
        self.assertEqual(self.restore(), raw)

    def test_a_nested_fence_stays_in_the_archive(self):
        body = '\n'.join(['- unresolved: see sample', '  ```', '  ## inside', '  ```', '  - later nested'])
        raw = (FRONTMATTER + section(3) + section(2) + section(1, body=body, unresolved=False)
               + section(0)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.apply()
        titles = [item.title for item in self.fold.split_sections(self.handoff())[1]]
        self.assertEqual(len(titles), 5)
        self.assertIn('2026-01-00 session 0', titles)
        self.assertNotIn(b'``` [fold-source', self.handoff())
        self.assertEqual(self.restore(), raw)

    def test_fenced_lines_are_not_unresolved(self):
        body = '```\n- unresolved: sample text\n```'
        raw = (FRONTMATTER + section(3) + section(2) + section(1, body=body, unresolved=False)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.assertEqual(self.analyze()['unresolved_lines'], 0)

    def test_rule_comes_from_the_config(self):
        self.config(handoff_fold={'unresolved_prefixes': ['open item:']})
        raw = (FRONTMATTER + section(3) + section(2)
               + section(1, body='- Open Item: still here\n- unresolved: ignored', unresolved=False)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.assertEqual(self.analyze()['unresolved_lines'], 1)
        self.apply()
        self.assertIn(b'- Open Item: still here [fold-source: sha256:', self.handoff())
        self.assertEqual(self.restore(), raw)

    def test_crlf_lifted_lines_use_the_file_line_ending(self):
        raw = document(4).replace(b'\n', b'\r\n')
        self.write(HANDOFF, raw)
        self.apply()
        after = self.handoff()
        self.assertEqual(after.replace(b'\r\n', b'').count(b'\n'), 0)
        self.assertEqual(self.restore(), raw)


class SecondFoldTests(Base):
    def test_second_fold_keeps_stubs_collected_and_old_archives(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        self.apply()
        first_archives = self.archives()
        first_stub_count = self.handoff().count(b'[handoff-fold]')
        later = (self.handoff()[:len(FRONTMATTER.encode('utf-8'))]
                 + section(7).encode('utf-8') + section(6).encode('utf-8')
                 + self.handoff()[len(FRONTMATTER.encode('utf-8')):])
        self.write(HANDOFF, later)
        result = self.analyze()
        self.assertEqual(result['already_folded'], first_stub_count)
        self.assertEqual(len(result['candidates']), 2)
        self.apply(folded_at='2026-03-04T05:06:07Z')
        archives = self.archives()
        self.assertEqual(sorted(archives), ['Demo handoff archive 0001', 'Demo handoff archive 0002'])
        self.assertEqual(archives['Demo handoff archive 0001'], first_archives['Demo handoff archive 0001'])
        after = self.handoff()
        self.assertEqual(after.count('## 미결 모음'.encode('utf-8')), 1)
        self.assertEqual(after.count(b'[fold-source: sha256:'), 5)
        self.assertEqual(after.count(b'[handoff-fold]'), 5)
        self.assertEqual(self.fold.restore_handoff(after, {'Demo handoff archive 0002': archives['Demo handoff archive 0002']},
                                                   strict=False), later)
        full = self.fold.restore_handoff(after, archives)
        self.assertEqual(full, (FRONTMATTER + section(7) + section(6)).encode('utf-8')
                         + raw[len(FRONTMATTER.encode('utf-8')):])

    def test_running_again_finds_nothing(self):
        self.write(HANDOFF, document(5))
        self.apply()
        after = self.handoff()
        self.assertEqual(self.analyze()['status'], 'nothing_to_fold')
        self.assertEqual(self.apply()['status'], 'nothing_to_fold')
        self.assertEqual(self.handoff(), after)

    def test_a_section_with_the_collected_title_is_kept_and_extended(self):
        existing = '## 미결 모음\n\n- written by hand\n\n'
        raw = (FRONTMATTER + section(4) + section(3) + existing + section(2) + section(1)).encode('utf-8')
        self.write(HANDOFF, raw)
        self.apply()
        titles = [item.title for item in self.fold.split_sections(self.handoff())[1]]
        self.assertEqual(titles.count('미결 모음'), 1)
        self.assertIn(b'- written by hand\n- unresolved: open question 2 [fold-source:', self.handoff())
        self.assertEqual(self.restore(), raw)


class CliTests(Base):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--vault', str(self.vault), *args],
                              capture_output=True, text=True, encoding='utf-8')

    def test_analyze_then_apply(self):
        raw = document(5)
        self.write(HANDOFF, raw)
        shown = self.run_cli('analyze', '--threshold-bytes', '1', '--protect-sections', '2')
        self.assertEqual(shown.returncode, 0, shown.stderr)
        report = json.loads(shown.stdout)
        self.assertEqual(self.handoff(), raw)
        applied = self.run_cli('apply', '--threshold-bytes', '1', '--protect-sections', '2',
                               '--expected-sha256', report['sha256'], '--folded-at', FOLDED_AT)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['status'], 'applied')
        self.assertEqual(self.fold.restore_handoff(self.handoff(), {
            path.stem: path.read_bytes() for path in (self.vault / ARCHIVE_DIR).iterdir()}), raw)

    def test_summary_file(self):
        raw = document(4)
        self.write(HANDOFF, raw)
        _, sections = self.fold.split_sections(raw)
        self.write('00-meta/scratch/summaries.json', json.dumps({
            'schema_version': 1, 'summaries': {sections[3].sha256: 'From the file.'}}).encode('utf-8'))
        applied = self.run_cli('apply', '--threshold-bytes', '1', '--protect-sections', '2',
                               '--expected-sha256', sha(raw), '--summaries', '00-meta/scratch/summaries.json')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn(b'  - summary: From the file.', self.handoff())

    def test_summary_file_must_be_json_in_the_vault(self):
        raw = document(4)
        self.write(HANDOFF, raw)
        self.write('00-meta/summaries.txt', b'{}')
        for value in ('00-meta/summaries.txt', '../outside.json', '00-meta/absent.json'):
            refused = self.run_cli('apply', '--threshold-bytes', '1', '--protect-sections', '2',
                                   '--expected-sha256', sha(raw), '--summaries', value)
            self.assertEqual(refused.returncode, 1)
            self.assertEqual(json.loads(refused.stderr), {'error': 'invalid_summary'})
        self.assertEqual(self.handoff(), raw)

    def test_errors_are_fixed_codes_without_paths(self):
        self.write(HANDOFF, document(4))
        refused = self.run_cli('analyze', '--protect-sections', '2')
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stderr), {'error': 'fold_threshold_required'})
        stale = self.run_cli('apply', '--threshold-bytes', '1', '--protect-sections', '2',
                             '--expected-sha256', '0' * 64)
        self.assertEqual(json.loads(stale.stderr), {'error': 'stale_base'})
        self.assertNotIn(str(self.vault), stale.stderr)

    def test_bad_arguments_exit_two(self):
        self.write(HANDOFF, document(4))
        refused = self.run_cli('analyze', '--threshold-bytes', 'many')
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stderr), {'error': 'invalid_arguments'})
        abbreviated = self.run_cli('analyze', '--threshold', '1', '--protect-sections', '2')
        self.assertEqual(abbreviated.returncode, 2)


if __name__ == '__main__':
    unittest.main()
