from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPT_DIR))
import vault_recall as recall


def restore_mtime(path, mtime_ns):
    """Put a directory time back and require it to stay there briefly."""
    for _ in range(50):
        os.utime(path, ns=(mtime_ns, mtime_ns))
        time.sleep(0.002)
        if os.stat(path).st_mtime_ns == mtime_ns:
            time.sleep(0.005)
            if os.stat(path).st_mtime_ns == mtime_ns:
                return
    raise AssertionError('directory time could not be restored')


class QueryExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.write('00-meta/vault-config.json', json.dumps({'deny_zones': ['private'], 'exclude_dirs': []}))
        self.write('20-knowledge/Isolation procedure.md', '---\naliases: ["LOTO", "격리절차"]\n---\n# Isolation procedure\nIsolation procedure requires locking before inspection.\n')

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def expansion(self):
        self.assertIsNotNone(importlib.util.find_spec('vault_query_expansion'), 'bounded expansion module must exist')
        return importlib.import_module('vault_query_expansion')

    def test_disabled_expansion_does_not_read_and_default_results_remain_exact(self):
        expansion = self.expansion()
        with patch.object(recall, '_read_config', side_effect=AssertionError('disabled mode reads nothing')):
            result = expansion.expand_query(self.vault, 'LOTO')
        self.assertEqual(result['terms'], ['loto'])
        self.assertEqual(result['expansions'], [])
        self.assertEqual(recall.recall(self.vault, 'LOTO'), recall._legacy_recall(self.vault, 'LOTO'))

    def test_alias_literals_already_match_but_expansion_returns_canonical_claim(self):
        literal = recall.recall(self.vault, 'LOTO')
        expanded = recall.recall(self.vault, 'LOTO', expand_query=True)
        self.assertEqual(literal['matches'][0]['path'], '20-knowledge/Isolation procedure.md')
        self.assertNotIn('locking before inspection', literal['context'])
        self.assertIn('locking before inspection', expanded['context'])
        proof = expanded['diagnostics']['query_expansion']['expansions'][0]
        self.assertEqual(proof['source_kind'], 'frontmatter_alias')
        self.assertEqual(proof['source_path'], '20-knowledge/Isolation procedure.md')
        self.assertEqual(len(proof['source_sha256']), 64)
        self.assertEqual(expanded['diagnostics']['query_expansion']['terms'][0], 'loto')

    def test_particle_spacing_mapping_and_all_bounds_are_deterministic(self):
        expansion = self.expansion()
        mapping = self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        first = expansion.expand_query(self.vault, '격리 절차를', enabled=True, mapping='00-meta/query-map.txt')
        second = expansion.expand_query(self.vault, '격리 절차를', enabled=True, mapping='00-meta/query-map.txt')
        self.assertEqual(first, second)
        self.assertIn('isolation', first['terms'])
        mapped = expansion.expand_query(self.vault, 'trip', enabled=True, mapping='00-meta/query-map.txt')
        self.assertEqual(mapped['expansions'][0]['source_kind'], 'plain_mapping')
        self.assertEqual(mapped['mapping_bytes_read'], mapping.stat().st_size*2)
        self.assertLessEqual(len(mapped['terms']), recall.MAX_QUERY_TERMS)
        self.assertIn('limits', mapped)

    def test_ambiguous_aliases_abstain_from_expansion_and_never_resolve_wikilinks(self):
        expansion = self.expansion()
        self.write('20-knowledge/Alternative.md', '---\naliases: ["LOTO"]\n---\n# Alternative\nAlternative unrelated record.\n')
        result = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(result['terms'], ['loto'])
        self.assertEqual(result['ambiguities'][0]['trigger'], 'loto')
        self.write('20-knowledge/Seed.md', '# Unique seed\nuniquegraphseed [[LOTO]]\n')
        graph = recall.recall(self.vault, 'uniquegraphseed', expand_links=1, expand_query=True)
        self.assertEqual([m['path'] for m in graph['matches']], ['20-knowledge/Seed.md'])
        self.assertEqual(graph['diagnostics']['graph_unresolved'], 1)

    def test_denied_malformed_and_unsafe_mapping_never_read_or_execute(self):
        expansion = self.expansion()
        self.write('private/mapping.txt', 'trip => confidential\n')
        for rel in ('private/mapping.txt', '../outside.txt', '00-meta/.agentic-vault/runtime/mapping.txt'):
            with self.subTest(rel=rel):
                result = expansion.expand_query(self.vault, 'trip', enabled=True, mapping=rel)
                self.assertEqual(result['status'], 'invalid_mapping')
                self.assertEqual(result['mapping_bytes_read'], 0)
        self.write('00-meta/query-map.txt', 'trip => isolation\ntrip => alternative\n')
        self.assertTrue(expansion.expand_query(self.vault, 'trip', enabled=True, mapping='00-meta/query-map.txt')['ambiguities'])
        self.write('00-meta/query-map.txt', '__import__("os").system("echo unsafe")\n')
        self.assertEqual(expansion.expand_query(self.vault, 'trip', enabled=True, mapping='00-meta/query-map.txt')['status'], 'invalid_mapping')
        self.assertEqual(recall.recall(self.vault, 'trip', expand_query=True, query_mapping='00-meta/query-map.txt')['context'], '')

    def test_reserved_engine_runtime_is_excluded_even_with_empty_config(self):
        self.write('00-meta/.agentic-vault/runtime/checkpoint.md', '# runtimeonlyneedle\nLOTO runtimeonlyneedle\n')
        self.write('20-knowledge/runtime/Business.md', '# businessruntime\nbusinessruntime allowed\n')
        self.assertEqual(recall.recall(self.vault, 'runtimeonlyneedle')['matches'], [])
        self.assertEqual(recall.recall(self.vault, 'runtimeonlyneedle', backend='bm25')['matches'], [])
        self.assertTrue(recall.recall(self.vault, 'businessruntime')['matches'])
        self.assertEqual(self.expansion().expand_query(self.vault, 'runtimeonlyneedle', enabled=True)['expansions'], [])

    def test_cli_expansion_flags_are_real_and_zero_budget_stays_zero(self):
        args = recall._parser().parse_args(['--vault', str(self.vault), '--query', 'LOTO', '--expand-query', '--query-mapping', '00-meta/query-map.txt'])
        self.assertTrue(args.expand_query)
        self.assertEqual(args.query_mapping, '00-meta/query-map.txt')
        self.assertEqual(recall.recall(self.vault, 'LOTO', expand_query=True, max_tokens=0)['context'], '')

    def test_mapping_reads_share_total_budget_and_changes_discard_context(self):
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n' + '#comment\n'*20)
        note_bytes = (self.vault / '20-knowledge/Isolation procedure.md').stat().st_size
        with patch.object(recall, 'MAX_TOTAL_READ_BYTES', note_bytes+10):
            result = recall.recall(self.vault, 'trip', expand_query=True, query_mapping='00-meta/query-map.txt')
        self.assertLessEqual(result['diagnostics']['bytes_read'], note_bytes+10)
        self.assertEqual(result['context'], '')
        render = recall._render_context
        def mutate(matches, max_tokens, diagnostics):
            self.write('00-meta/query-map.txt', 'trip => alternative material\n')
            return render(matches, max_tokens, diagnostics)
        with patch.object(recall, '_render_context', side_effect=mutate):
            changed = recall.recall(self.vault, 'trip', expand_query=True, query_mapping='00-meta/query-map.txt')
        self.assertEqual(changed['context'], '')
        self.assertEqual(changed['diagnostics']['status'], 'source_changed')

    def test_expansion_alias_sources_are_provenance_dependencies_even_when_not_returned(self):
        self.write('20-knowledge/Isolation procedure.md', '---\naliases: ["LOTO"]\n---\n# Isolation procedure\nTopic overview.\n')
        self.write('20-knowledge/A detailed procedure.md', '# Isolation procedure Isolation procedure\nLOTO: Isolation procedure Isolation procedure Isolation procedure requires locking before inspection.\n')
        render = recall._render_context
        def mutate(matches, max_tokens, diagnostics):
            self.assertEqual(matches[0]['path'], '20-knowledge/A detailed procedure.md')
            self.write('20-knowledge/Isolation procedure.md', '# Changed after expansion\nNo alias remains.\n')
            return render(matches, max_tokens, diagnostics)
        with patch.object(recall, '_render_context', side_effect=mutate):
            changed = recall.recall(self.vault, 'LOTO', limit=1, expand_query=True)
        self.assertEqual(changed['context'], '')
        self.assertEqual(changed['diagnostics']['status'], 'source_changed')

    def test_alias_bounds_denied_notes_and_incomplete_inventory_cannot_assert_uniqueness(self):
        expansion = self.expansion()
        self.write('private/Secret.md', '---\naliases: ["secretneedle"]\n---\n# Secret\nSecret material.\n')
        self.assertEqual(expansion.expand_query(self.vault, 'secretneedle', enabled=True)['expansions'], [])
        self.write('20-knowledge/Broken.md', '---\naliases: ["'+ 'x'*257 +'\"]\n---\n# Broken\n')
        self.assertTrue(expansion.expand_query(self.vault, 'LOTO', enabled=True)['errors'])
        with patch.object(recall, 'MAX_FILES', 1):
            partial = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(partial['expansions'], [])
        self.assertFalse(partial['inventory_diagnostics']['search_complete'])

    def test_compact_equivalent_aliases_are_ambiguous_and_standalone_sources_are_fresh(self):
        expansion = self.expansion()
        self.write('20-knowledge/Alternative.md', '---\naliases: ["격리 절차"]\n---\n# Alternative\nAlternative record.\n')
        result = expansion.expand_query(self.vault, '격리절차', enabled=True)
        self.assertEqual(result['expansions'], [])
        self.assertTrue(result['ambiguities'])
        self.write('20-knowledge/Alternative.md', '# Alternative\nNo alias.\n')
        read_config = recall._read_config
        calls = []
        def mutate(vault, diagnostics):
            calls.append(1)
            if len(calls) == 2:
                self.write('20-knowledge/Isolation procedure.md', '# Altered\nNo aliases remain.\n')
            return read_config(vault, diagnostics)
        with patch.object(recall, '_read_config', side_effect=mutate):
            changed = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(changed['expansions'], [])
        self.assertEqual(changed['status'], 'source_changed')

    def test_last_mapping_read_cannot_leave_an_earlier_alias_source_stale(self):
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        verify = expansion.verify_mapping
        def mutate(*args, **kwargs):
            result = verify(*args, **kwargs)
            self.write('20-knowledge/Isolation procedure.md', '# Changed after mapping verification\n')
            return result
        with patch.object(expansion, 'verify_mapping', side_effect=mutate):
            changed = recall.recall(self.vault, 'LOTO', expand_query=True, query_mapping='00-meta/query-map.txt')
        self.assertEqual(changed['context'], '')
        self.assertEqual(changed['diagnostics']['status'], 'source_changed')

    def test_review_r1_standalone_later_reads_cannot_leave_alias_provenance_stale(self):
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        verify = expansion.verify_mapping
        def mutate_mapping(*args, **kwargs):
            result = verify(*args, **kwargs)
            self.write('20-knowledge/Isolation procedure.md', '# Retired\nAlias and procedure removed.\n')
            return result
        with patch.object(expansion, 'verify_mapping', side_effect=mutate_mapping):
            changed = expansion.expand_query(self.vault, 'LOTO', enabled=True, mapping='00-meta/query-map.txt')
        self.assertEqual(changed['status'], 'source_changed')
        self.assertEqual(changed['terms'], ['loto'])
        self.assertEqual(changed['expansions'], [])

    def test_review_r1_final_config_read_cannot_leave_alias_provenance_stale(self):
        expansion = self.expansion()
        reader = recall._read_config
        calls = []
        def mutate_config(*args, **kwargs):
            result = reader(*args, **kwargs)
            calls.append(1)
            if len(calls) == 3:
                self.write('20-knowledge/Isolation procedure.md', '# Retired\nAlias and procedure removed.\n')
            return result
        with patch.object(recall, '_read_config', side_effect=mutate_config):
            changed = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(changed['status'], 'source_changed')
        self.assertEqual(changed['terms'], ['loto'])
        self.assertEqual(changed['expansions'], [])

    def test_review_r2_changed_unreturned_note_invalidates_whole_alias_inventory(self):
        expansion = self.expansion()
        self.write('20-knowledge/Other.md', '# Unrelated\nNothing relevant.\n')
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        verify = expansion.verify_mapping
        def mutate_other(*args, **kwargs):
            result = verify(*args, **kwargs)
            self.write('20-knowledge/Other.md', '---\naliases: ["LOTO"]\n---\n# Other\nOther unrelated record.\n')
            return result
        for integrated in (False, True):
            with self.subTest(integrated=integrated):
                self.write('20-knowledge/Other.md', '# Unrelated\nNothing relevant.\n')
                with patch.object(expansion, 'verify_mapping', side_effect=mutate_other):
                    if integrated:
                        result = recall.recall(self.vault, 'LOTO', expand_query=True, query_mapping='00-meta/query-map.txt')
                        self.assertEqual(result['context'], '')
                        self.assertEqual(result['diagnostics']['status'], 'source_changed')
                        changed = result['diagnostics']['query_expansion']
                    else:
                        changed = expansion.expand_query(self.vault, 'LOTO', enabled=True, mapping='00-meta/query-map.txt')
                self.assertEqual(changed['terms'], ['loto'])
                self.assertEqual(changed['expansions'], [])
                fresh = expansion.expand_query(self.vault, 'LOTO', enabled=True)
                self.assertEqual(fresh['expansions'], [])
                self.assertEqual(fresh['ambiguities'][0]['reason'], 'multiple_destinations')

    def test_review_r2_new_competing_note_invalidates_directory_generation_without_rescan(self):
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        verify = expansion.verify_mapping
        scan = recall._markdown_paths
        calls = []
        def count_scan(*args, **kwargs):
            calls.append(1)
            return scan(*args, **kwargs)
        def mutate_new(*args, **kwargs):
            result = verify(*args, **kwargs)
            self.write('20-knowledge/New competitor.md', '---\naliases: ["LOTO"]\n---\n# Competing destination\n')
            return result
        with patch.object(expansion, 'verify_mapping', side_effect=mutate_new), patch.object(recall, '_markdown_paths', side_effect=count_scan):
            changed = recall.recall(self.vault, 'LOTO', expand_query=True, query_mapping='00-meta/query-map.txt')
        self.assertEqual(calls, [1])
        self.assertEqual(changed['context'], '')
        self.assertEqual(changed['diagnostics']['status'], 'source_changed')
        self.assertEqual(changed['diagnostics']['query_expansion']['expansions'], [])

    def test_review_r1_policy_generation_is_checked_after_standalone_final_read(self):
        expansion = self.expansion()
        reader = recall._read_config
        calls = []
        def mutate_config(*args, **kwargs):
            result = reader(*args, **kwargs)
            calls.append(1)
            if len(calls) == 3:
                self.write('00-meta/vault-config.json', json.dumps({'deny_zones': ['20-knowledge'], 'exclude_dirs': []}))
            return result
        with patch.object(recall, '_read_config', side_effect=mutate_config):
            changed = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(changed['status'], 'policy_changed')
        self.assertEqual(changed['terms'], ['loto'])
        self.assertEqual(changed['expansions'], [])

    def test_review_r3_integrated_policy_replacement_during_final_inventory_abstains(self):
        expansion = self.expansion()
        verify = expansion.verify_inventory
        def replace_policy(*args, **kwargs):
            result = verify(*args, **kwargs)
            self.write('00-meta/vault-config.json', json.dumps({'deny_zones': ['20-knowledge/Isolation procedure.md'], 'exclude_dirs': []}))
            return result
        with patch.object(expansion, 'verify_inventory', side_effect=replace_policy):
            changed = recall.recall(self.vault, 'LOTO', expand_query=True)
        self.assertEqual(changed['diagnostics']['status'], 'policy_changed')
        self.assertEqual(changed['context'], '')
        self.assertEqual(changed['matches'], [])
        self.assertFalse(changed['diagnostics']['search_complete'])

    # Listing fingerprint (limit A11). Directory times can miss an entry change
    # (coarse or lagging Windows directory times). Only dev/ino/mtime are kept
    # in directory marks here so the mtime restore below reproduces that miss
    # on every platform (POSIX ctime/size would otherwise reveal the change).
    def masked_directory_marks(self, expansion):
        original = expansion.inventory_mark
        seen = {}
        def mark(path, *, directory=False):
            value = original(path, directory=directory)
            if not directory:
                return value
            seen.setdefault(Path(path).name, value[3])
            return (value[0], value[1], 0, value[3], 0)
        return patch.object(expansion, 'inventory_mark', side_effect=mark), seen

    def listing_mutations(self):
        folder = self.vault / '20-knowledge'
        return {
            'add': lambda: self.write('20-knowledge/New competitor.md', '---\naliases: ["LOTO"]\n---\n# Competing destination\n'),
            'remove': lambda: (folder / 'Spare.txt').unlink(),
            'rename': lambda: (folder / 'Spare.txt').rename(folder / 'Spare renamed.txt'),
        }

    def reset_listing_fixture(self):
        folder = self.vault / '20-knowledge'
        for name in ('New competitor.md', 'Spare renamed.txt', 'Extra.txt'):
            if (folder / name).exists():
                (folder / name).unlink()
        self.write('20-knowledge/Spare.txt', 'attachment\n')

    def run_with_listing_change(self, expansion, mutate, *, integrated):
        folder = self.vault / '20-knowledge'
        marks, seen = self.masked_directory_marks(expansion)
        verify = expansion.verify_mapping
        def mutate_listing(*args, **kwargs):
            result = verify(*args, **kwargs)
            mutate()
            restore_mtime(folder, seen['20-knowledge'])
            return result
        with marks, patch.object(expansion, 'verify_mapping', side_effect=mutate_listing):
            if integrated:
                result = recall.recall(self.vault, 'LOTO', expand_query=True, query_mapping='00-meta/query-map.txt')
                return result['diagnostics']['status'], result['diagnostics']['query_expansion']
            changed = expansion.expand_query(self.vault, 'LOTO', enabled=True, mapping='00-meta/query-map.txt')
            return changed['status'], changed

    def test_listing_change_behind_restored_directory_time_is_diagnosed(self):
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        for kind, mutate in self.listing_mutations().items():
            for integrated in (False, True):
                with self.subTest(kind=kind, integrated=integrated):
                    self.reset_listing_fixture()
                    status, changed = self.run_with_listing_change(expansion, mutate, integrated=integrated)
                    checked = changed['inventory_verification']
                    # A listing-only difference invalidates like a metadata change.
                    self.assertEqual(status, 'source_changed')
                    self.assertEqual(checked['status'], 'source_changed')
                    self.assertEqual(changed['expansions'], [])
                    self.assertEqual(changed['terms'], ['loto'])
                    self.assertEqual(checked['changed_directory'], {'path': '20-knowledge', 'change': 'listing'})
                    self.assertEqual(checked['listing_diagnostics'],
                                     [{'code': 'directory_listing_changed', 'path': '20-knowledge'}])
                    self.assertIn('one bounded listing per marked directory', checked['method'])

    def test_directory_change_kinds_are_distinguished(self):
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        self.reset_listing_fixture()
        folder = self.vault / '20-knowledge'
        def touch():
            os.utime(folder, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        verify = expansion.verify_mapping
        def mutate(*args, **kwargs):
            result = verify(*args, **kwargs)
            touch()
            return result
        with patch.object(expansion, 'verify_mapping', side_effect=mutate):
            changed = expansion.expand_query(self.vault, 'LOTO', enabled=True, mapping='00-meta/query-map.txt')
        checked = changed['inventory_verification']
        self.assertEqual(changed['status'], 'source_changed')
        self.assertEqual(checked['changed_directory'], {'path': '20-knowledge', 'change': 'metadata'})
        self.assertEqual(checked['listing_diagnostics'], [])
        verify_inventory = expansion.verify_inventory
        def unreadable(*args, **kwargs):
            with patch('os.scandir', side_effect=PermissionError('listing denied')):
                return verify_inventory(*args, **kwargs)
        with patch.object(expansion, 'verify_inventory', side_effect=unreadable):
            denied = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(denied['inventory_verification']['changed_directory'], {'path': '.', 'change': 'unavailable'})
        unchanged = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(unchanged['status'], 'ok')
        self.assertNotIn('changed_directory', unchanged['inventory_verification'])

    def test_unchanged_listing_records_no_listing_diagnostic(self):
        expansion = self.expansion()
        self.write('20-knowledge/Spare.txt', 'attachment\n')
        result = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        checked = result['inventory_verification']
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(checked['listing_diagnostics'], [])
        self.assertEqual(checked['directories_checked'], 3)

    def test_unreadable_or_over_cap_listing_at_check_time_fails_closed(self):
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        self.reset_listing_fixture()
        verify = expansion.verify_inventory
        def unreadable(*args, **kwargs):
            with patch('os.scandir', side_effect=PermissionError('listing denied')):
                return verify(*args, **kwargs)
        with patch.object(expansion, 'verify_inventory', side_effect=unreadable):
            denied = expansion.expand_query(self.vault, 'LOTO', enabled=True)
        self.assertEqual(denied['status'], 'source_changed')
        self.assertEqual(denied['expansions'], [])
        scanned = expansion.expand_query(self.vault, 'LOTO', enabled=True,
                                         mapping='00-meta/query-map.txt')['inventory_diagnostics']['entries_scanned']
        with patch.object(recall, 'MAX_DIRECTORY_ENTRIES', scanned):
            unchanged = expansion.expand_query(self.vault, 'LOTO', enabled=True, mapping='00-meta/query-map.txt')
            self.assertEqual(unchanged['status'], 'ok')
            status, changed = self.run_with_listing_change(
                expansion, lambda: self.write('20-knowledge/Extra.txt', 'x\n'), integrated=False)
        self.assertEqual(status, 'source_changed')
        self.assertEqual(changed['inventory_verification']['status'], 'source_changed')
        self.assertEqual(changed['expansions'], [])

    def test_check_lists_each_marked_directory_once_without_rescan(self):
        expansion = self.expansion()
        verify = expansion.verify_inventory
        real_scandir = os.scandir
        listed = []
        def counting_scandir(path, *args, **kwargs):
            listed.append(os.path.normcase(os.fspath(path)))
            return real_scandir(path, *args, **kwargs)
        def counted(*args, **kwargs):
            with patch('os.scandir', side_effect=counting_scandir):
                return verify(*args, **kwargs)
        scan = recall._markdown_paths
        calls = []
        def count_scan(*args, **kwargs):
            calls.append(1)
            return scan(*args, **kwargs)
        with patch.object(expansion, 'verify_inventory', side_effect=counted), \
                patch.object(recall, '_markdown_paths', side_effect=count_scan):
            result = recall.recall(self.vault, 'LOTO', expand_query=True)
        checked = result['diagnostics']['query_expansion']['inventory_verification']
        self.assertEqual(calls, [1])
        self.assertEqual(len(listed), checked['directories_checked'])
        self.assertEqual(len(set(listed)), len(listed))
        self.assertGreater(len(listed), 0)

    def test_same_name_recreation_is_not_a_listing_change(self):
        # The name set cannot see a delete-and-recreate under the same name.
        # A note is still caught by its document stamp (inode/ctime); a non-note
        # file is outside the alias inventory and is documented as not caught.
        expansion = self.expansion()
        self.write('00-meta/query-map.txt', 'trip => isolation procedure\n')
        self.write('20-knowledge/Other.md', '# Unrelated\nNothing relevant.\n')
        folder = self.vault / '20-knowledge'
        def recreate(name):
            path = folder / name
            data, times = path.read_bytes(), path.stat()
            path.unlink()
            path.write_bytes(data)
            os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
        for name, expected in (('Other.md', 'source_changed'), ('Spare.txt', 'ok')):
            with self.subTest(name=name):
                self.reset_listing_fixture()
                status, changed = self.run_with_listing_change(expansion, lambda: recreate(name), integrated=False)
                self.assertEqual(status, expected)
                self.assertEqual(changed['inventory_verification']['listing_diagnostics'], [])


if __name__ == '__main__':
    unittest.main()
