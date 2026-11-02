from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
SCRIPT = SCRIPTS / 'vault_revocation.py'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
revocation = importlib.import_module('vault_revocation') if SCRIPT.is_file() else None


class RevocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.config = {'required_keys': ['title'], 'enums': {},
                       'deny_zones': ['90-assets', '10-inbox/_processed'],
                       'exclude_dirs': ['private']}
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        self.original = '# Mixed\r\nwithdrawn A\r\nbenign fact\r\nwithdrawn alias\r\nlast benign'
        self.write('20-knowledge/mixed.md', self.original)
        self.write('20-knowledge/derived.md', '# Derived\nderived target\nretained evidence\n')
        self.declarations = {'schema_version': 1, 'nodes': [
            {'id': 'target', 'path': '20-knowledge/mixed.md', 'start_line': 2, 'end_line': 2},
            {'id': 'alias', 'path': '20-knowledge/mixed.md', 'start_line': 4, 'end_line': 4},
            {'id': 'derived', 'path': '20-knowledge/derived.md', 'start_line': 2, 'end_line': 2},
        ], 'derivations': [{'parent': 'target', 'child': 'alias'},
                           {'parent': 'alias', 'child': 'derived'}], 'revoked': ['target']}

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        return path

    def module(self):
        self.assertIsNotNone(revocation, 'source-bound revocation implementation is missing')
        return revocation

    def capture(self, declarations=None):
        return self.module().capture_registry(self.vault, declarations or self.declarations)

    def policy(self, registry=None):
        self.write('00-meta/revocations.json', json.dumps(registry or self.capture()))
        return self.module().RevocationPolicy(
            self.vault, {**self.config, 'memory_revocation_file': '00-meta/revocations.json'})

    def test_disabled_policy_performs_no_filesystem_io_and_preserves_text(self):
        module = self.module()
        with mock.patch.object(Path, 'resolve', side_effect=AssertionError('filesystem read')):
            policy = module.RevocationPolicy(Path('missing-vault'), {})
            self.assertFalse(policy.enabled)
            self.assertEqual(policy.filter_text('../untouched', 'literal\r\nbody'), 'literal\r\nbody')
            policy.recheck()
        self.assertEqual(policy.affected_paths, set())
        self.assertEqual(policy.revoked_ids, set())

    def test_capture_binds_exact_bytes_and_never_modifies_files(self):
        before = {p.relative_to(self.vault).as_posix(): p.read_bytes()
                  for p in self.vault.rglob('*') if p.is_file()}
        captured = self.capture()
        self.assertEqual(captured['nodes'][0]['sha256'], hashlib.sha256(self.original.encode()).hexdigest())
        self.assertNotIn('sha256', self.declarations['nodes'][0])
        after = {p.relative_to(self.vault).as_posix(): p.read_bytes()
                 for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_revocation_closes_derivation_graph_and_preserves_unrelated_lines(self):
        policy = self.policy()
        self.assertEqual(policy.revoked_ids, {'target', 'alias', 'derived'})
        self.assertEqual(policy.filter_text('20-knowledge/mixed.md', self.original),
                         '# Mixed\r\n\r\nbenign fact\r\n\r\nlast benign')
        self.assertEqual(policy.filter_path('20-knowledge/derived.md'),
                         '# Derived\n\nretained evidence\n')
        policy.recheck()
        summary = json.dumps(policy.summary())
        self.assertNotIn('withdrawn A', summary)
        self.assertEqual(policy.affected_paths, {'20-knowledge/mixed.md', '20-knowledge/derived.md'})

    def test_cycles_and_overlapping_spans_use_bounded_union(self):
        declarations = copy.deepcopy(self.declarations)
        declarations['nodes'][0]['end_line'] = 3
        declarations['nodes'][1]['start_line'] = 3
        declarations['derivations'].append({'parent': 'derived', 'child': 'target'})
        policy = self.policy(self.capture(declarations))
        self.assertEqual(policy.filter_path('20-knowledge/mixed.md'), '# Mixed\r\n\r\n\r\n\r\nlast benign')

    def test_final_line_and_unicode_separator_are_not_additional_lines(self):
        self.write('20-knowledge/mixed.md', 'keep\nremove\u2028still same line')
        declarations = copy.deepcopy(self.declarations)
        declarations['nodes'] = [declarations['nodes'][0]]
        declarations['derivations'] = []
        policy = self.policy(self.capture(declarations))
        self.assertEqual(policy.filter_path('20-knowledge/mixed.md'), 'keep\n')

    def test_stale_affected_document_fails_closed_even_if_target_line_unchanged(self):
        policy = self.policy()
        changed = self.original + '\nnew benign'
        self.write('20-knowledge/mixed.md', changed)
        with self.assertRaises(self.module().RevocationError):
            policy.filter_text('20-knowledge/mixed.md', changed)

    def test_declared_scope_leaves_undeclared_similar_text_untouched(self):
        self.write('20-knowledge/undeclared.md', 'withdrawn A remains undeclared\n')
        policy = self.policy()
        self.assertEqual(policy.filter_path('20-knowledge/undeclared.md'),
                         'withdrawn A remains undeclared\n')
        self.assertIn('explicit', policy.summary()['scope'])

    @unittest.skipUnless(os.name == 'nt', 'case-insensitive Windows path alias')
    def test_case_variant_filter_path_cannot_bypass_revoked_binding(self):
        policy = self.policy()
        self.assertEqual(policy.filter_path('20-KNOWLEDGE/MIXED.md'),
                         '# Mixed\r\n\r\nbenign fact\r\n\r\nlast benign')

    def test_recheck_budget_includes_hash_rereads(self):
        self.write('20-knowledge/mixed.md', '# Mixed\n' + 'x' * 18000 + '\nbenign\nalias\n')
        captured = self.capture()
        with mock.patch.object(self.module(), 'MAX_BINDING_BYTES', 32 * 1024):
            policy = self.policy(captured)
            policy.filter_path('20-knowledge/mixed.md')
            with self.assertRaises(self.module().RevocationError):
                policy.recheck()

    def test_source_file_count_bounds_zero_byte_inputs(self):
        policy = self.policy()
        with mock.patch.object(self.module(), 'MAX_SOURCE_FILES', 2, create=True):
            for i in range(3):
                self.write('20-knowledge/empty' + str(i) + '.md', '')
            policy.filter_path('20-knowledge/empty0.md')
            policy.filter_path('20-knowledge/empty1.md')
            with self.assertRaises(self.module().RevocationError):
                policy.filter_path('20-knowledge/empty2.md')

    @unittest.skipIf(os.name == 'nt', 'requires distinct case-sensitive filesystem paths')
    def test_distinct_case_sensitive_files_each_consume_source_limit(self):
        policy = self.policy()
        with mock.patch.object(self.module(), 'MAX_SOURCE_FILES', 2):
            for name in ('AAA', 'AAa', 'AaA'):
                self.write('20-knowledge/' + name + '.md', '')
            policy.filter_path('20-knowledge/AAA.md')
            policy.filter_path('20-knowledge/AAa.md')
            with self.assertRaises(self.module().RevocationError):
                policy.filter_path('20-knowledge/AaA.md')

    def test_capture_rejects_source_changed_during_later_hash_read(self):
        module = self.module()
        original = module.stable_read
        calls = 0

        def swapping_read(resolver, limit, **options):
            nonlocal calls
            record = original(resolver, limit, **options)
            calls += 1
            if calls == 4:
                self.write('20-knowledge/mixed.md', self.original + '\nchanged')
            return record

        with mock.patch.object(module, 'stable_read', side_effect=swapping_read):
            with self.assertRaises(module.RevocationError):
                self.capture()

    def test_final_recheck_detects_registry_swap_during_source_read(self):
        policy = self.policy()
        policy.filter_path('20-knowledge/mixed.md')
        module = self.module()
        original = module.stable_read
        calls = 0

        def swapping_read(resolver, limit, **options):
            nonlocal calls
            record = original(resolver, limit, **options)
            calls += 1
            if calls == 2:
                self.write('00-meta/revocations.json', '{}')
            return record

        with mock.patch.object(module, 'stable_read', side_effect=swapping_read):
            with self.assertRaises(module.RevocationError):
                policy.recheck()

    def test_supplied_text_must_match_binding_and_current_file(self):
        policy = self.policy()
        with self.assertRaises(self.module().RevocationError):
            policy.filter_text('20-knowledge/mixed.md', self.original.replace('benign fact', 'fabricated'))

    def test_recheck_rejects_source_or_registry_swap(self):
        for change in ('source', 'registry'):
            with self.subTest(change=change):
                self.write('20-knowledge/mixed.md', self.original)
                policy = self.policy()
                policy.filter_path('20-knowledge/mixed.md')
                if change == 'source':
                    self.write('20-knowledge/mixed.md', self.original.replace('withdrawn A', 'new target!'))
                else:
                    self.write('00-meta/revocations.json', '{}')
                with self.assertRaises(self.module().RevocationError):
                    policy.recheck()

    def test_missing_malformed_or_oversized_configured_registry_fails_closed(self):
        module = self.module()
        config = {**self.config, 'memory_revocation_file': '00-meta/missing.json'}
        with self.assertRaises(module.RevocationError):
            module.RevocationPolicy(self.vault, config)
        for payload in ('{}', '{"schema_version":1,"schema_version":1}', '[' * 1000,
                        ' ' * (64 * 1024 + 1)):
            self.write('00-meta/missing.json', payload)
            with self.subTest(payload=payload[:32]):
                with self.assertRaises(module.RevocationError):
                    module.RevocationPolicy(self.vault, config)

    def test_registry_types_graph_refs_duplicates_and_inconsistent_bindings_rejected(self):
        valid = self.capture()
        malformed = []
        for key, value in [('schema_version', True), ('revoked', ['unknown']), ('revoked', ['target', 'target'])]:
            value_registry = copy.deepcopy(valid)
            value_registry[key] = value
            malformed.append(value_registry)
        for key, value in [('start_line', True), ('end_line', 0), ('sha256', 'A' * 64),
                           ('path', '20-knowledge/mixed.txt'), ('extra', 'no')]:
            value_registry = copy.deepcopy(valid)
            value_registry['nodes'][0][key] = value
            malformed.append(value_registry)
        duplicate = copy.deepcopy(valid)
        duplicate['nodes'].append(copy.deepcopy(duplicate['nodes'][0]))
        malformed.append(duplicate)
        different_hash = copy.deepcopy(valid)
        different_hash['nodes'][1]['sha256'] = '0' * 64
        malformed.append(different_hash)
        case_alias = copy.deepcopy(valid)
        case_alias['nodes'][1]['path'] = '20-knowledge/MIXED.md'
        malformed.append(case_alias)
        unknown = copy.deepcopy(valid)
        unknown['derivations'][0]['child'] = 'unknown'
        malformed.append(unknown)
        for registry in malformed:
            with self.subTest(registry=registry):
                with self.assertRaises(self.module().RevocationError):
                    self.policy(registry)

    def test_capture_bounds_reject_past_eof_and_boolean_lines(self):
        for start, end in ((0, 1), (1, 6), (2, 1), (True, 2), (1, False)):
            declaration = copy.deepcopy(self.declarations)
            declaration['nodes'][0].update(start_line=start, end_line=end)
            with self.subTest(start=start, end=end):
                with self.assertRaises(self.module().RevocationError):
                    self.capture(declaration)

    def test_registry_out_of_bounds_is_rejected_before_text_emission(self):
        registry = self.capture()
        registry['nodes'][0]['end_line'] = 9999
        with self.assertRaises(self.module().RevocationError):
            self.policy(registry).filter_path('20-knowledge/mixed.md')

    def test_denied_excluded_unsafe_and_wrong_extension_paths_are_rejected(self):
        for path in ('90-assets/secret.md', 'private/secret.md', '10-inbox/_processed/secret.md',
                     '.git/secret.md', '../secret.md', '20-knowledge/mixed.md:secret',
                     '20-knowledge/mixed.json', '20-knowledge/secret\u2028.md'):
            declaration = copy.deepcopy(self.declarations)
            declaration['nodes'][0]['path'] = path
            with self.subTest(path=path):
                with self.assertRaises(self.module().RevocationError):
                    self.capture(declaration)
        for path in ('90-assets/registry.json', 'private/registry.json', '../outside.json',
                     '00-meta/revocations.md'):
            with self.assertRaises(self.module().RevocationError):
                self.module().RevocationPolicy(self.vault, {**self.config, 'memory_revocation_file': path})

    def test_hardlinks_and_symlinks_cannot_bind_or_supply_registry(self):
        outside = self.write('outside.md', 'outside secret')
        for kind in ('hardlink', 'symlink'):
            linked = self.vault / ('20-knowledge/' + kind + '.md')
            try:
                (os.link if kind == 'hardlink' else os.symlink)(outside, linked)
            except (OSError, NotImplementedError):
                continue
            declaration = copy.deepcopy(self.declarations)
            declaration['nodes'][0]['path'] = linked.relative_to(self.vault).as_posix()
            with self.subTest(kind=kind):
                with self.assertRaises(self.module().RevocationError):
                    self.capture(declaration)
        registry_source = self.write('00-meta/safe.json', json.dumps(self.capture()))
        for kind in ('hardlink', 'symlink'):
            registry_link = self.vault / ('00-meta/registry-' + kind + '.json')
            try:
                (os.link if kind == 'hardlink' else os.symlink)(registry_source, registry_link)
            except (OSError, NotImplementedError):
                continue
            with self.subTest(registry_kind=kind):
                with self.assertRaises(self.module().RevocationError):
                    self.module().RevocationPolicy(self.vault, {
                        **self.config, 'memory_revocation_file': registry_link.relative_to(self.vault).as_posix()})

    def test_graph_and_host_input_limits_fail_without_serializing_unbounded_objects(self):
        too_many = copy.deepcopy(self.declarations)
        too_many['nodes'] = [dict(too_many['nodes'][0], id='node' + str(i)) for i in range(129)]
        cyclic = copy.deepcopy(self.declarations)
        cyclic['extra'] = cyclic
        for value in (too_many, cyclic):
            with self.assertRaises(self.module().RevocationError):
                self.capture(value)
        edges = copy.deepcopy(self.declarations)
        edges['derivations'] = [{'parent': 'target', 'child': 'alias'}] * 257
        with self.assertRaises(self.module().RevocationError):
            self.capture(edges)

    def test_cli_capture_check_json_only_without_writes_or_secret_errors(self):
        self.module()
        self.write('00-meta/input.json', json.dumps(self.declarations))
        before = {p.relative_to(self.vault).as_posix(): p.read_bytes()
                  for p in self.vault.rglob('*') if p.is_file()}
        completed = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault),
                                    'capture', '--input', '00-meta/input.json'],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        registry = json.loads(completed.stdout)
        self.assertIn('sha256', registry['nodes'][0])
        self.assertEqual(completed.stderr, '')
        after = {p.relative_to(self.vault).as_posix(): p.read_bytes()
                 for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.write('00-meta/input.json', json.dumps(registry))
        checked = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault),
                                  'check', '--input', '00-meta/input.json'],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['revoked_count'], 3)
        self.write('00-meta/input.json', '{"private-password":"do not print"}')
        rejected = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault),
                                   'check', '--input', '00-meta/input.json'],
                                  capture_output=True, text=True, check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn('private-password', rejected.stdout + rejected.stderr)
        json.loads(rejected.stdout)

    def boundary_declarations(self, registry_bytes):
        source = b'one source line\n'
        self.write('20-knowledge/source.md', source)
        ids = ['n%03d_' % index + 'x' * 123 for index in range(128)]
        registry = {'schema_version': 1, 'nodes': [
            {'id': node_id, 'path': '20-knowledge/source.md', 'start_line': 1,
             'end_line': 1, 'sha256': hashlib.sha256(source).hexdigest()}
            for node_id in ids],
            'derivations': [{'parent': ids[index], 'child': ids[index + 1]}
                            for index in range(108)], 'revoked': [ids[0]]}
        compact = lambda value: json.dumps(value, ensure_ascii=True, sort_keys=True,
                                           separators=(',', ':')).encode('utf-8')
        excess = len(compact(registry)) - registry_bytes
        self.assertGreaterEqual(excess, 0)
        # These three IDs are absent from edges/revoked, so each removed byte
        # changes serialized size by exactly one without changing the graph.
        for index in (125, 126, 127):
            reduction = min(123, excess)
            if reduction:
                registry['nodes'][index]['id'] = registry['nodes'][index]['id'][:-reduction]
            excess -= reduction
        self.assertEqual(excess, 0)
        self.assertEqual(len(compact(registry)), registry_bytes)
        for node in registry['nodes']:
            del node['sha256']
        return registry, compact(registry)

    def test_cli_capture_boundary_registries_roundtrip_without_transport_padding(self):
        self.module()
        for expected_bytes in (65535, 65536):
            with self.subTest(registry_bytes=expected_bytes):
                _declarations, encoded = self.boundary_declarations(expected_bytes)
                self.write('00-meta/declarations.json', encoded)
                captured = subprocess.run(
                    [sys.executable, str(SCRIPT), '--vault', str(self.vault), 'capture',
                     '--input', '00-meta/declarations.json'], capture_output=True, check=False)
                self.assertEqual(captured.returncode, 0, captured.stderr)
                self.assertEqual(len(json.loads(captured.stdout)['nodes']), 128)
                self.write('00-meta/captured.json', captured.stdout)
                checked = subprocess.run(
                    [sys.executable, str(SCRIPT), '--vault', str(self.vault), 'check',
                     '--input', '00-meta/captured.json'], capture_output=True, check=False)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertEqual(len(captured.stdout), expected_bytes)
                self.assertEqual(captured.stderr, b'')
                self.assertEqual(checked.stderr, b'')
                self.assertEqual(json.loads(checked.stdout)['node_count'], 128)

    def test_capture_rejects_first_oversized_registry_without_relaxing_input_cap(self):
        declarations, encoded = self.boundary_declarations(65537)
        with self.assertRaises(self.module().RevocationError):
            self.capture(declarations)
        self.write('00-meta/declarations.json', encoded)
        captured = subprocess.run(
            [sys.executable, str(SCRIPT), '--vault', str(self.vault), 'capture',
             '--input', '00-meta/declarations.json'], capture_output=True, check=False)
        self.assertNotEqual(captured.returncode, 0)
        self.assertEqual(json.loads(captured.stdout), {'error': 'revocation_validation_failed'})
        self.assertEqual(captured.stderr, b'')

    def test_cli_rejects_source_swap_during_final_input_checks(self):
        module = self.module()
        captured = self.capture()
        original = module._Reader.sweep
        for command in ('capture', 'check'):
            with self.subTest(command=command):
                self.write('20-knowledge/mixed.md', self.original)
                self.write('00-meta/input.json', json.dumps(
                    self.declarations if command == 'capture' else captured))
                changed = False

                def swapping_sweep(reader, records):
                    nonlocal changed
                    result = original(reader, records)
                    if set(records) == {'00-meta/vault-config.json', '00-meta/input.json'} and not changed:
                        changed = True
                        self.write('20-knowledge/mixed.md', self.original + '\nchanged')
                    return result

                output = io.StringIO()
                with mock.patch.object(module._Reader, 'sweep', new=swapping_sweep), redirect_stdout(output):
                    result = module.main(['--vault', str(self.vault), command,
                                          '--input', '00-meta/input.json'])
                self.assertTrue(changed, 'fixture did not reach final input checks')
                self.assertNotEqual(result, 0)
                self.assertIn('error', json.loads(output.getvalue()))


if __name__ == '__main__':
    unittest.main()
