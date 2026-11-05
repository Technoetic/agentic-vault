from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
ontology = importlib.import_module('vault_ontology') if (SCRIPTS / 'vault_ontology.py').is_file() else None


class OntologyCoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ontology, 'vault_ontology.py is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.config = {'required_keys': ['title'], 'enums': {},
                       'deny_zones': ['90-assets'], 'exclude_dirs': ['private']}
        self.write_config()
        self.source = self.write('20-knowledge/definitions.md', '# Definitions\nAlice works for Lab.\nRevenue is net sales.\n')
        self.proof = {'path': '20-knowledge/definitions.md', 'line': 2,
                      'quote': 'Alice works for Lab.'}
        self.declaration = {
            'schema_version': 1, 'ontology_id': 'demo', 'version': '1.0',
            'types': ['person', 'organization'],
            'entities': [self.entity('alice', 'person', 'Alice'),
                         self.entity('lab', 'organization', 'Lab')],
            'relations': [{'id': 'works_for', 'label': 'works for', 'domain': 'person',
                           'range': 'organization', 'review': 'reviewed',
                           'sources': [copy.deepcopy(self.proof)]}],
            'links': [{'id': 'employment', 'subject': 'alice', 'predicate': 'works_for',
                       'object': 'lab', 'review': 'reviewed', 'sources': [copy.deepcopy(self.proof)]}],
            'metrics': [{'id': 'revenue', 'label': 'Revenue', 'aliases': ['Net sales'],
                         'definition': 'Revenue is net sales.', 'unit': 'USD', 'grain': 'month',
                         'population': 'all sales', 'aggregation': 'sum', 'review': 'candidate',
                         'sources': [{'path': self.proof['path'], 'line': 3,
                                      'quote': 'Revenue is net sales.'}]}],
        }
        self.declaration_path = '00-meta/ontology.json'
        self.save()

    def entity(self, ident, kind, label):
        return {'id': ident, 'type': kind, 'label': label, 'aliases': [],
                'review': 'reviewed', 'sources': [copy.deepcopy(self.proof)]}

    def write(self, relative, data):
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        return target

    def write_config(self):
        self.write('00-meta/vault-config.json', json.dumps(self.config))

    def save(self, declaration=None, relative=None):
        return self.write(relative or self.declaration_path, json.dumps(
            self.declaration if declaration is None else declaration, ensure_ascii=False))

    def capture(self):
        return ontology.capture_registry(self.vault, self.declaration_path)

    def assert_unavailable(self, result):
        self.assertEqual(result['status'], 'unavailable', result)
        self.assertEqual(result['authority'], 'data_only')
        self.assertEqual(result['context'], [])
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['gaps'], [])
        self.assertNotIn('entities', result)
        self.assertNotIn('registry_sha256', result)
        self.assertNotIn('Alice works', json.dumps(result))

    def resign(self, snapshot):
        body = {key: value for key, value in snapshot.items() if key != 'registry_sha256'}
        snapshot['registry_sha256'] = hashlib.sha256(json.dumps(
            body, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode()).hexdigest()
        return snapshot

    def report(self, registry):
        return {'type': 'ontology_contract_report', 'schema_version': 1,
                'authority': 'data_only', 'registry_sha256': registry['registry_sha256'],
                'status': 'matched', 'context': [{'id': 'alice'}], 'matches': [], 'gaps': []}

    def test_capture_is_deterministic_bound_to_raw_bytes_and_never_writes(self):
        before = {p.relative_to(self.vault).as_posix(): p.read_bytes()
                  for p in self.vault.rglob('*') if p.is_file()}
        result = self.capture()
        self.assertEqual(set(result), {'type', 'schema_version', 'authority', 'ontology_id',
                         'version', 'types', 'entities', 'relations', 'links', 'metrics',
                         'declaration', 'config_sha256', 'registry_sha256'})
        self.assertEqual(result['type'], 'ontology_registry')
        self.assertEqual(result['authority'], 'data_only')
        self.assertEqual(result['types'], ['person', 'organization'])
        self.assertEqual(result['metrics'][0]['review'], 'candidate')
        proof = result['entities'][0]['sources'][0]
        self.assertEqual(proof['quote'], 'Alice works for Lab.')
        self.assertEqual(proof['span_sha256'], hashlib.sha256(b'Alice works for Lab.').hexdigest())
        self.assertEqual(proof['binding'], {'path': self.proof['path'],
                         'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         'size': len(self.source.read_bytes())})
        self.assertEqual(result['declaration']['sha256'], hashlib.sha256(
                         (self.vault / self.declaration_path).read_bytes()).hexdigest())
        self.assertEqual(result, self.capture())
        self.assertEqual(before, {p.relative_to(self.vault).as_posix(): p.read_bytes()
                                 for p in self.vault.rglob('*') if p.is_file()})

    def test_check_returns_current_digest_without_claiming_answer_data(self):
        captured = self.capture()
        result = ontology.check_registry(self.vault, captured)
        self.assertEqual(result, {'type': 'ontology_contract_report', 'schema_version': 1,
                         'authority': 'data_only', 'registry_sha256': captured['registry_sha256'],
                         'status': 'current', 'context': [], 'matches': [], 'gaps': []})

    def test_definition_edit_is_stale_even_when_version_label_is_unchanged(self):
        captured = self.capture()
        self.declaration['metrics'][0]['definition'] = 'Gross sales.'
        self.save()
        self.assert_unavailable(ontology.check_registry(self.vault, captured))

    def test_source_edit_outside_quote_invalidates_entire_registry(self):
        captured = self.capture()
        self.source.write_bytes(self.source.read_bytes() + b'Other annotation.\n')
        self.assert_unavailable(ontology.check_registry(self.vault, captured))

    def test_config_change_or_missing_declaration_invalidates_registry(self):
        captured = self.capture()
        self.config['exclude_dirs'].append('other')
        self.write_config()
        self.assert_unavailable(ontology.check_registry(self.vault, captured))
        self.config['exclude_dirs'].pop()
        self.write_config()
        (self.vault / self.declaration_path).unlink()
        self.assert_unavailable(ontology.check_registry(self.vault, captured))

    def test_recomputed_self_hash_cannot_authorize_forged_cached_annotation(self):
        captured = self.capture()
        captured['entities'][0]['label'] = 'FORGED'
        self.assert_unavailable(ontology.check_registry(self.vault, self.resign(captured)))

    def test_bad_snapshot_shape_digest_and_bool_version_fail_closed(self):
        captured = self.capture()
        for mutate in (lambda s: s.update(extra=True),
                       lambda s: s.update(schema_version=True),
                       lambda s: s.update(registry_sha256='0' * 64),
                       lambda s: s['entities'][0]['sources'][0].update(extra=True),
                       lambda s: s['declaration'].update(size=True)):
            snapshot = copy.deepcopy(captured)
            mutate(snapshot)
            self.assert_unavailable(ontology.check_registry(self.vault, snapshot))

    def test_bad_root_record_source_and_primitive_shapes_are_rejected(self):
        for mutate in (lambda d: d.update(extra=True),
                       lambda d: d.update(schema_version=True),
                       lambda d: d.update(version=2),
                       lambda d: d.update(types=[]),
                       lambda d: d['entities'][0].update(type='unknown'),
                       lambda d: d['entities'][0].update(review='approved'),
                       lambda d: d['entities'][0].update(aliases='Alice'),
                       lambda d: d['entities'][0].update(extra=True),
                       lambda d: d['entities'][0].update(sources=[]),
                       lambda d: d['entities'][0]['sources'][0].update(line=True),
                       lambda d: d['metrics'][0].update(aggregation={'sql': 'select 1'})):
            declaration = copy.deepcopy(self.declaration)
            mutate(declaration)
            self.save(declaration)
            self.assert_unavailable(self.capture())

    def test_global_id_type_and_triple_duplicates_are_rejected(self):
        for mutate in (lambda d: d['types'].append('person'),
                       lambda d: d['entities'][0].update(id='Alice'),
                       lambda d: d['metrics'][0].update(id='alice'),
                       lambda d: d['links'].append({**d['links'][0], 'id': 'another'})):
            declaration = copy.deepcopy(self.declaration)
            mutate(declaration)
            self.save(declaration)
            self.assert_unavailable(self.capture())

    def test_relation_direction_unknown_ids_and_type_mismatch_are_rejected(self):
        for mutate in (lambda d: d['links'][0].update(subject='lab', object='alice'),
                       lambda d: d['links'][0].update(predicate='alice'),
                       lambda d: d['links'][0].update(object='missing'),
                       lambda d: d['relations'][0].update(domain='unknown')):
            declaration = copy.deepcopy(self.declaration)
            mutate(declaration)
            self.save(declaration)
            self.assert_unavailable(self.capture())

    def test_overlapping_labels_aliases_and_candidate_links_are_preserved(self):
        self.declaration['entities'][1]['aliases'] = ['ALICE']
        self.declaration['links'][0]['review'] = 'candidate'
        self.save()
        captured = self.capture()
        self.assertEqual(captured['entities'][1]['aliases'], ['ALICE'])
        self.assertEqual(captured['links'][0]['review'], 'candidate')

    def test_quotes_use_literal_lf_lines_and_crlf_is_not_normalized(self):
        self.write('20-knowledge/literal.md', b'first\r\ncaf\xc3\xa9\r\nlast\n')
        self.declaration['entities'][0]['sources'] = [
            {'path': '20-knowledge/literal.md', 'line': 2, 'quote': 'caf\u00e9\r'}]
        self.save()
        captured = self.capture()
        self.assertEqual(captured['entities'][0]['sources'][0]['quote'], 'caf\u00e9\r')
        for quote in ('caf\u00e9', 'cafe\u0301\r', 'last\n'):
            self.declaration['entities'][0]['sources'][0]['quote'] = quote
            self.save()
            self.assert_unavailable(self.capture())

    def test_source_expected_hash_and_whole_line_mismatch_are_rejected(self):
        for patch in ({'expected_sha256': '0' * 64}, {'quote': 'works for'}, {'line': 100}):
            declaration = copy.deepcopy(self.declaration)
            declaration['entities'][0]['sources'][0].update(patch)
            self.save(declaration)
            self.assert_unavailable(self.capture())

    def test_json_decoder_rejects_duplicates_nonfinite_deep_and_large_inputs(self):
        invalid = ('{"schema_version":1,"schema_version":1}', '{"value":NaN}',
                   '[' * 5000 + '0' + ']' * 5000, ' ' * (65536 + 1))
        for raw in invalid:
            self.write(self.declaration_path, raw)
            self.assert_unavailable(self.capture())

    def test_record_type_source_and_output_budgets_fail_closed(self):
        variations = []
        extra = copy.deepcopy(self.declaration)
        extra['entities'] = [self.entity('e' + str(n), 'person', 'Label') for n in range(129)]
        variations.append(extra)
        extra = copy.deepcopy(self.declaration)
        extra['types'] = ['type' + str(n) for n in range(33)]
        variations.append(extra)
        extra = copy.deepcopy(self.declaration)
        extra['entities'][0]['sources'] *= 5
        variations.append(extra)
        extra = copy.deepcopy(self.declaration)
        extra['entities'][0]['label'] = '\u2603' * 12000
        variations.append(extra)
        for declaration in variations:
            self.save(declaration)
            self.assert_unavailable(self.capture())

    def test_denied_excluded_git_and_runtime_declaration_or_source_are_blocked(self):
        for prefix in ('90-assets', 'private', '.git', '00-meta/.agentic-vault/runtime'):
            relative = prefix + '/ontology.json'
            self.save(relative=relative)
            self.assert_unavailable(ontology.capture_registry(self.vault, relative))
            source = prefix + '/source.md'
            self.write(source, 'Alice works for Lab.\n')
            declaration = copy.deepcopy(self.declaration)
            declaration['entities'][0]['sources'] = [{'path': source, 'line': 1,
                                                       'quote': 'Alice works for Lab.'}]
            self.save(declaration)
            self.assert_unavailable(self.capture())

    def test_runtime_guard_runs_before_json_or_markdown_content_reads(self):
        original_read = ontology.stable_read
        observed = []
        def read(resolver, *args, **kwargs):
            resolved = resolver()
            observed.append(resolved.relative_to(self.vault).as_posix())
            return original_read(resolver, *args, **kwargs)
        runtime = '00-meta/.agentic-vault/runtime/ontology.json'
        self.save(relative=runtime)
        with mock.patch.object(ontology, 'stable_read', side_effect=read):
            self.assert_unavailable(ontology.capture_registry(self.vault, runtime))
        self.assertNotIn(runtime, observed)

    def test_hardlinked_inputs_and_sources_are_rejected(self):
        linked = self.vault / '00-meta/linked.json'
        try:
            os.link(self.vault / self.declaration_path, linked)
        except OSError as exc:
            self.skipTest('hardlinks unavailable: ' + str(exc))
        self.assert_unavailable(ontology.capture_registry(self.vault, '00-meta/linked.json'))
        linked.unlink()
        os.link(self.source, self.vault / '20-knowledge/linked.md')
        self.assert_unavailable(self.capture())

    def test_symlink_declaration_or_source_is_rejected(self):
        linked = self.vault / '00-meta/linked.json'
        try:
            linked.symlink_to(self.vault / self.declaration_path)
        except OSError:
            self.skipTest('symlinks unavailable')
        self.assert_unavailable(ontology.capture_registry(self.vault, '00-meta/linked.json'))
        linked.unlink()
        (self.vault / '20-knowledge/linked.md').symlink_to(self.source)
        self.declaration['entities'][0]['sources'][0]['path'] = '20-knowledge/linked.md'
        self.save()
        self.assert_unavailable(self.capture())

    def install_revocation(self):
        import vault_revocation
        declarations = {'schema_version': 1, 'nodes': [
            {'id': 'withdrawn', 'path': self.proof['path'], 'start_line': 2, 'end_line': 2}],
            'derivations': [], 'revoked': ['withdrawn']}
        registry = vault_revocation.capture_registry(self.vault, declarations)
        self.write('00-meta/revocations.json', json.dumps(registry))
        self.config['memory_revocation_file'] = '00-meta/revocations.json'
        self.write_config()

    def test_revoked_selected_or_unselected_record_invalidates_whole_registry(self):
        captured = self.capture()
        self.install_revocation()
        self.assert_unavailable(self.capture())
        self.assert_unavailable(ontology.check_registry(self.vault, captured))

    def test_runtime_revocation_registry_is_rejected_before_read(self):
        self.install_revocation()
        raw = (self.vault / '00-meta/revocations.json').read_bytes()
        self.config['memory_revocation_file'] = '00-meta/.agentic-vault/runtime/revocations.json'
        self.write(self.config['memory_revocation_file'], raw)
        self.write_config()
        self.assert_unavailable(self.capture())

    def test_builder_races_on_source_declaration_or_config_clear_all_context(self):
        for target in ('source', 'declaration', 'config'):
            self.save()
            self.write_config()
            self.source.write_bytes(b'# Definitions\nAlice works for Lab.\nRevenue is net sales.\n')
            captured = self.capture()
            def builder(registry):
                result = self.report(registry)
                if target == 'source':
                    self.source.write_bytes(self.source.read_bytes() + b'changed\n')
                elif target == 'declaration':
                    changed = copy.deepcopy(self.declaration)
                    changed['version'] = '1.1'
                    self.save(changed)
                else:
                    self.write('00-meta/vault-config.json', json.dumps({**self.config, 'stale_days': 1}))
                return result
            self.assert_unavailable(ontology._with_registry(self.vault, captured, builder))

    def test_builder_outputs_are_bounded_and_cannot_drop_data_only_metadata(self):
        captured = self.capture()
        builders = (lambda r: {'status': 'matched'},
                    lambda r: {**self.report(r), 'authority': 'certified'},
                    lambda r: {**self.report(r), 'registry_sha256': '0' * 64},
                    lambda r: {**self.report(r), 'schema_version': True},
                    lambda r: {**self.report(r), 'context': ['x' * 65536]},
                    lambda r: {**self.report(r), 'context': [float('nan')]})
        for builder in builders:
            self.assert_unavailable(ontology._with_registry(self.vault, captured, builder))

    def test_valid_large_gap_report_is_retained_within_json_budget(self):
        captured = self.capture()
        def builder(registry):
            return {**self.report(registry), 'status': 'needs_clarification', 'context': [],
                    'gaps': [{'code': 'missing_slot'} for _ in range(192)]}
        result = ontology._with_registry(self.vault, captured, builder)
        self.assertEqual(result['status'], 'needs_clarification', result)
        self.assertEqual(len(result['gaps']), 192)

    def test_arbitrary_builder_failure_returns_no_exception_or_context(self):
        captured = self.capture()
        def builder(registry):
            raise Exception('SECRET failed source text')
        self.assert_unavailable(ontology._with_registry(self.vault, captured, builder))

    def test_source_file_and_unique_source_count_limits_are_enforced(self):
        self.source.write_bytes(b'Alice works for Lab.\n' + b'x' * 524288)
        self.assert_unavailable(self.capture())
        self.source.write_bytes(b'Alice works for Lab.\nRevenue is net sales.\n')
        self.declaration['entities'] = []
        self.declaration['relations'] = []
        self.declaration['links'] = []
        self.declaration['metrics'] = []
        for number in range(65):
            relative = '20-knowledge/file' + str(number) + '.md'
            self.write(relative, 'Evidence.\n')
            record = self.entity('e' + str(number), 'person', 'Label')
            record['sources'] = [{'path': relative, 'line': 1, 'quote': 'Evidence.'}]
            self.declaration['entities'].append(record)
        self.save()
        self.assert_unavailable(self.capture())

    def test_compiled_output_is_rejected_when_bindings_exceed_input_budget(self):
        quote = 'x' * 300
        self.source.write_bytes((quote + '\n').encode())
        self.declaration['relations'] = []
        self.declaration['links'] = []
        self.declaration['metrics'] = []
        self.declaration['entities'] = []
        for number in range(100):
            record = self.entity('e' + str(number), 'person', 'Label')
            record['sources'] = [{'path': self.proof['path'], 'line': 1, 'quote': quote}]
            self.declaration['entities'].append(record)
        path = self.save()
        self.assertLess(len(path.read_bytes()), 65536)
        self.assert_unavailable(self.capture())

    def test_same_metadata_source_edit_is_detected_by_raw_hash(self):
        captured = self.capture()
        before = self.source.stat()
        def builder(registry):
            self.source.write_bytes(self.source.read_bytes().replace(b'Alice', b'Other'))
            os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
            return self.report(registry)
        self.assert_unavailable(ontology._with_registry(self.vault, captured, builder))

    def test_declaration_race_after_first_stable_read_is_detected(self):
        original_read = ontology.stable_read
        changed = False
        def read(resolver, *args, **kwargs):
            nonlocal changed
            result = original_read(resolver, *args, **kwargs)
            if not changed and resolver() == self.vault / self.declaration_path:
                changed = True
                self.write(self.declaration_path, '{}')
            return result
        with mock.patch.object(ontology, 'stable_read', side_effect=read):
            self.assert_unavailable(self.capture())

    def test_cyclic_and_extreme_host_objects_fail_closed_before_encoding(self):
        cycle = {}
        cycle['self'] = cycle
        for snapshot in (cycle, {'x': 1 << 100000}, {'x': object()}):
            self.assert_unavailable(ontology.check_registry(self.vault, snapshot))


if __name__ == '__main__':
    unittest.main()
