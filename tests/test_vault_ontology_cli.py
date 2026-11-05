import copy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
CLI = SCRIPTS / 'vault_ontology_cli.py'


class OntologyCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name).resolve()
        self.write('00-meta/vault-config.json', json.dumps({
            'required_keys': ['title'], 'enums': {},
            'deny_zones': ['90-assets'], 'exclude_dirs': ['private']}))
        self.write('notes/definitions.md', 'Reviewed machine and good output definitions.\n')
        proof = {'path': 'notes/definitions.md', 'line': 1,
                 'quote': 'Reviewed machine and good output definitions.'}
        self.declarations = {
            'schema_version': 1, 'ontology_id': 'demo', 'version': '1',
            'types': ['machine'],
            'entities': [{'id': 'machine:a', 'type': 'machine', 'label': 'Machine A',
                          'aliases': [], 'review': 'reviewed', 'sources': [proof]}],
            'relations': [], 'links': [],
            'metrics': [{'id': 'good_output', 'label': 'Good output', 'aliases': [],
                         'definition': 'Count good pieces per shift', 'unit': 'piece',
                         'grain': 'shift', 'population': 'good_only', 'aggregation': 'sum',
                         'review': 'reviewed', 'sources': [proof]}]}
        self.write('00-meta/ontology/declarations.json', json.dumps(self.declarations))

    def write(self, path, text):
        p = self.vault / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8', newline='\n')
        return p

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), '--vault', str(self.vault), *args],
                              capture_output=True, timeout=15)

    def capture(self):
        result = self.run_cli('capture', '--input', '00-meta/ontology/declarations.json')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['type'], 'ontology_registry')
        self.write('00-meta/ontology/registry.json', result.stdout.decode('ascii'))
        return value

    def test_capture_check_lookup_and_no_write(self):
        snapshot = {p.relative_to(self.vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.vault.rglob('*') if p.is_file()}
        captured = self.run_cli('capture', '--input', '00-meta/ontology/declarations.json')
        self.assertEqual(captured.returncode, 0, captured.stderr)
        self.assertNotIn(b'\r', captured.stdout)
        self.assertEqual(snapshot, {p.relative_to(self.vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in self.vault.rglob('*') if p.is_file()})
        self.write('00-meta/ontology/registry.json', captured.stdout.decode('ascii'))
        result = self.run_cli('check', '--registry', '00-meta/ontology/registry.json')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'], 'current')
        search = self.run_cli('search-terms', '--registry', '00-meta/ontology/registry.json', '--text', 'good output')
        self.assertEqual(search.returncode, 0, search.stdout)
        self.assertEqual(json.loads(search.stdout)['status'], 'matched')
        term = self.run_cli('get-term', '--registry', '00-meta/ontology/registry.json', '--id', 'machine:a')
        self.assertEqual(term.returncode, 0, term.stdout)

    def test_preflight_complete_then_missing_slot(self):
        self.capture()
        request = {'schema_version': 1, 'entities': ['machine:a'], 'relations': [],
                   'metrics': [{'id': 'good_output', 'slots': {'unit': 'piece', 'grain': 'shift',
                               'population': 'good_only', 'aggregation': 'sum',
                               'period': {'start': '2026-01-01', 'end': '2026-01-02'}}}]}
        self.write('request.json', json.dumps(request))
        result = self.run_cli('check-answerable', '--registry', '00-meta/ontology/registry.json', '--input', 'request.json')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'], 'ready_for_evidence')
        del request['metrics'][0]['slots']['population']
        self.write('request.json', json.dumps(request))
        result = self.run_cli('check-answerable', '--registry', '00-meta/ontology/registry.json', '--input', 'request.json')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(json.loads(result.stdout)['context'], [])

    def test_source_change_clears_lookup(self):
        self.capture()
        self.write('notes/definitions.md', 'Changed meaning.\n')
        result = self.run_cli('get-term', '--registry', '00-meta/ontology/registry.json', '--id', 'machine:a')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['context'], [])

    def test_denied_runtime_and_duplicate_key_inputs(self):
        self.capture()
        for path in ('90-assets/input.json', 'private/input.json', '00-meta/.agentic-vault/runtime/input.json'):
            self.write(path, '{}')
            result = self.run_cli('check', '--registry', path)
            self.assertEqual(result.returncode, 2, path)
            self.assertEqual(json.loads(result.stdout)['status'], 'unavailable')
        self.write('bad.json', '{"schema_version":1,"schema_version":1}')
        result = self.run_cli('check', '--registry', 'bad.json')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['context'], [])

    def test_unknown_operation_is_json_failure(self):
        result = self.run_cli('delete-everything')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'unavailable')
        self.assertNotIn(b'Traceback', result.stderr)

    def test_final_json_replacement_is_detected(self):
        self.capture()
        module = importlib.import_module('vault_ontology_cli')
        query = importlib.import_module('vault_ontology_query')
        original = query.search_terms
        def replace(*args):
            result = original(*args)
            self.write('00-meta/ontology/registry.json', '{}')
            return result
        with patch.object(query, 'search_terms', replace):
            result = module.run_operation(self.vault, 'search-terms', registry_path='00-meta/ontology/registry.json', text='good output')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['context'], [])

    def test_source_change_during_final_json_check_is_detected(self):
        self.capture()
        module = importlib.import_module('vault_ontology_cli')
        original = module._recheck_inputs
        changed = False
        def change(*args):
            nonlocal changed
            if not changed:
                self.write('notes/definitions.md', 'Changed after lookup.\n')
                changed = True
            return original(*args)
        with patch.object(module, '_recheck_inputs', change):
            result = module.run_operation(self.vault, 'search-terms', registry_path='00-meta/ontology/registry.json', text='good output')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['context'], [])

    def test_query_input_replaced_during_later_source_hash_is_detected(self):
        self.capture()
        module = importlib.import_module('vault_ontology_cli')
        memory = importlib.import_module('vault_memory')
        self.write('request.json', json.dumps({'schema_version': 1,
                   'entities': ['machine:a'], 'relations': [], 'metrics': []}))
        original_check, original_read = module._recheck_inputs, memory.stable_read
        json_done, changed = False, False
        def checked(*args):
            nonlocal json_done
            result = original_check(*args)
            json_done = True
            return result
        def source_read(resolver, *args, **kwargs):
            nonlocal changed
            if (json_done and not changed and resolver().suffix == '.md'
                    and not kwargs.get('contents', False)):
                self.write('request.json', '{}')
                changed = True
            return original_read(resolver, *args, **kwargs)
        with patch.object(module, '_recheck_inputs', checked), patch.object(memory, 'stable_read', source_read):
            result = module.run_operation(self.vault, 'check-answerable',
                     registry_path='00-meta/ontology/registry.json', input_path='request.json')
        self.assertTrue(changed)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['context'], [])


if __name__ == '__main__':
    unittest.main()
