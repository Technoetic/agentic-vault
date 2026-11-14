from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _winfs import make_junction
except ImportError:
    from tests._winfs import make_junction

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    contracts = importlib.import_module('vault_task_contracts')
except ModuleNotFoundError:
    contracts = None


class TaskContractTests(unittest.TestCase):
    credential_paths = ('input/credentials.json', '.ssh/id_rsa', 'keys/service.pem', '.npmrc')

    def setUp(self):
        self.assertIsNotNone(contracts, 'source-aware task contracts implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.vault.mkdir()
        self.write_json('00-meta/vault-config.json', {'deny_zones': ['private'], 'exclude_dirs': ['excluded']})
        self.write('notes/request.md', 'Create a small reader.\nOnly local notes.\nOpening a note shows its title.\n')
        self.definition = {'version': 1, 'task': 'Local reader', 'fields': [
            self.field('goal', 'goal', 'Create a small reader.'),
            self.field('scope', 'scope', 'Only local notes.'),
            self.field('ac-title', 'acceptance', 'Opening a note shows its title.'),
        ], 'questions': []}
        self.write_json('input/task.json', self.definition)

    def write(self, relative, content):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)
        return path

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value, allow_nan=False))

    def field(self, field_id, kind, text, origin='user_request', status='confirmed', sources=None):
        return dict(id=field_id, kind=kind, text=text, origin=origin, status=status,
                    sources=([{'path': 'notes/request.md', 'excerpt': text}] if sources is None else sources))

    def capture(self, definition=None, path='input/task.json'):
        if definition is not None:
            self.write_json(path, definition)
        return contracts.capture(self.vault, path)

    def test_capture_ready_contract_is_immutable_and_inspect_is_read_only(self):
        captured = self.capture()
        self.assertEqual(captured['status'], 'ready_for_execution_review')
        self.assertEqual(captured['acceptance_ids'], ['ac-title'])
        self.assertEqual(captured['authority'], 'data_only')
        self.assertEqual(len(captured['accepted_fields']), 3)
        record = self.vault / captured['path']
        before = record.read_bytes()
        names = sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob('*'))
        self.assertEqual(contracts.inspect(self.vault, captured['contract_id']), captured)
        self.assertEqual(self.capture(), captured)
        self.assertEqual(record.read_bytes(), before)
        self.assertEqual(sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob('*')), names)
        self.assertEqual(captured['fingerprint'], captured['contract_id'])

    def test_provenance_does_not_accept_repository_facts_or_host_defaults(self):
        definition = copy.deepcopy(self.definition)
        definition['fields'][0]['origin'] = 'host_default'
        definition['fields'][1]['origin'] = 'repository_fact'
        result = self.capture(definition)
        self.assertEqual(result['status'], 'needs_clarification')
        self.assertEqual([f['id'] for f in result['accepted_fields']], ['ac-title'])
        self.assertEqual([f['id'] for f in result['repository_facts']], ['scope'])
        self.assertIn('origin_labels_are_caller_declarations', result['limitations'])

    def test_blocking_questions_and_missing_or_multiple_goals_block_readiness(self):
        for question in ({'id': 'q', 'text': 'Which format?', 'blocking': True, 'status': 'open'},
                         {'id': 'q', 'text': 'Which format?', 'blocking': True, 'status': 'unknown'}):
            definition = copy.deepcopy(self.definition)
            definition['questions'] = [question]
            self.assertEqual(self.capture(definition)['status'], 'needs_clarification')
        definition['questions'][0]['blocking'] = False
        self.assertEqual(self.capture(definition)['status'], 'ready_for_execution_review')
        definition['fields'].append(self.field('goal2', 'goal', 'Create a small reader.'))
        self.assertEqual(self.capture(definition)['status'], 'needs_clarification')
        definition['fields'] = [f for f in definition['fields'] if f['kind'] != 'goal']
        self.assertEqual(self.capture(definition)['status'], 'needs_clarification')

    def test_revision_binds_parent_and_preserves_immutable_old_receipt(self):
        first = self.capture()
        old = (self.vault / first['path']).read_bytes()
        definition = copy.deepcopy(self.definition)
        definition['fields'][2]['origin'] = 'user_confirmation'
        self.write_json('input/revision.json', definition)
        revised = contracts.revise(self.vault, first['contract_id'], 'input/revision.json')
        self.assertNotEqual(revised['contract_id'], first['contract_id'])
        self.assertEqual(revised['parent'], {'contract_id': first['contract_id'],
                                           'receipt_sha256': first['receipt_sha256']})
        self.assertEqual((self.vault / first['path']).read_bytes(), old)
        self.assertEqual(contracts.inspect(self.vault, revised['contract_id']), revised)
        self.assertEqual(contracts.inspect(self.vault, first['contract_id'])['status'], 'stale')
        self.assertEqual(contracts.revise(self.vault, first['contract_id'], 'input/revision.json'), revised)
        definition['task'] = 'Another revision'
        self.write_json('input/branch.json', definition)
        with self.assertRaises(contracts.ContractError):
            contracts.revise(self.vault, first['contract_id'], 'input/branch.json')

    def test_revision_can_explicitly_replace_a_stale_input_without_losing_parent(self):
        first = self.capture()
        self.definition['task'] = 'Revised local reader'
        self.write_json('input/task.json', self.definition)
        self.assertEqual(contracts.inspect(self.vault, first['contract_id'])['status'], 'stale')
        revised = contracts.revise(self.vault, first['contract_id'], 'input/task.json')
        self.assertEqual(revised['status'], 'ready_for_execution_review')
        self.assertEqual(revised['parent']['contract_id'], first['contract_id'])

    def test_any_changed_source_input_config_or_parent_receipt_is_stale(self):
        for target in ('source', 'input', 'config', 'receipt', 'parent'):
            with self.subTest(target=target):
                first = self.capture()
                selected = first
                if target == 'parent':
                    self.write_json('input/revision.json', self.definition)
                    selected = contracts.revise(self.vault, first['contract_id'], 'input/revision.json')
                path = {'source': 'notes/request.md', 'input': 'input/task.json',
                        'config': '00-meta/vault-config.json', 'receipt': first['path'],
                        'parent': first['path']}[target]
                original = (self.vault / path).read_bytes()
                try:
                    self.write(path, original + b' ')
                    if target in ('receipt', 'parent'):
                        value = json.loads(original)
                        value['created_at'] = '2020-01-01T00:00:00+00:00'
                        self.write_json(path, value)
                    result = contracts.inspect(self.vault, selected['contract_id'])
                    self.assertEqual(result['status'], 'stale')
                    self.assertEqual(result['acceptance_ids'], [])
                    self.assertEqual(result['accepted_fields'], [])
                    if target in ('receipt', 'parent'):
                        self.assertIsNone(result['historical_metadata'])
                    else:
                        self.assertEqual(result['historical_metadata']['acceptance_ids'], ['ac-title'])
                finally:
                    self.write(path, original)

    def test_strict_schema_rejects_forged_parent_unknown_fields_bool_version_and_ids(self):
        mutations = [lambda d: d.update(parent=None), lambda d: d.update(version=True),
                     lambda d: d['fields'][0].update(extra='ignored'),
                     lambda d: d['fields'][0].update(id='../bad'),
                     lambda d: d['fields'].append(copy.deepcopy(d['fields'][0])),
                     lambda d: d['questions'].append(dict(id='q', text='Question', blocking=1, status='open')),
                     lambda d: d['fields'][0].update(status='accepted'),
                     lambda d: d['fields'][0].update(sources=[]),
                     lambda d: d['fields'][0]['sources'][0].update(excerpt='Unrecorded user statement.')]
        for mutation in mutations:
            definition = copy.deepcopy(self.definition)
            mutation(definition)
            with self.subTest(definition=definition):
                self.write_json('input/invalid.json', definition)
                with self.assertRaises(contracts.ContractError):
                    contracts.capture(self.vault, 'input/invalid.json')
        for identifier in ('../bad', '0' * 63, 'A' * 64, True):
            with self.assertRaises(contracts.ContractError):
                contracts.inspect(self.vault, identifier)

    def test_proposed_user_fields_and_unconfirmed_assumptions_do_not_count(self):
        definition = copy.deepcopy(self.definition)
        definition['fields'][2]['status'] = 'proposed'
        result = self.capture(definition)
        self.assertEqual(result['status'], 'needs_clarification')
        self.assertEqual(result['acceptance_ids'], [])
        definition['fields'][2].update(origin='host_default', status='confirmed', sources=[])
        self.assertEqual(self.capture(definition)['acceptance_ids'], [])

    def test_unsafe_alias_deny_credentials_reserved_and_oversize_inputs_rejected(self):
        for path in ('../outside.json', 'private/task.json', 'excluded/task.json', '.env',
                     '00-meta/runtime/task.json', 'input/../input/task.json', 'input\\task.json'):
            with self.subTest(path=path), self.assertRaises(contracts.ContractError):
                contracts.capture(self.vault, path)
        self.write('input/oversize.json', ' ' * (contracts.MAX_JSON_BYTES + 1))
        with self.assertRaises(contracts.ContractError):
            contracts.capture(self.vault, 'input/oversize.json')
        self.write('notes/large.md', 'x' * (contracts.MAX_SOURCE_BYTES + 1))
        definition = copy.deepcopy(self.definition)
        definition['fields'][0]['sources'][0] = {'path': 'notes/large.md', 'excerpt': 'x'}
        self.write_json('input/large.json', definition)
        with self.assertRaises(contracts.ContractError):
            contracts.capture(self.vault, 'input/large.json')

    def reject_credential_read(self, operation, path):
        stable_read = contracts.evidence.stable_read
        selected = self.vault / path
        def guarded_read(resolver, *args, **kwargs):
            resolved = resolver()
            self.assertNotEqual(resolved, selected, 'credential path reached the byte reader')
            return stable_read(resolver, *args, **kwargs)
        with mock.patch.object(contracts.evidence, 'stable_read', guarded_read):
            with self.assertRaises(contracts.ContractError):
                operation()

    def test_known_credential_input_paths_are_rejected_before_read(self):
        for path in self.credential_paths:
            with self.subTest(path=path):
                self.write_json(path, self.definition)
                self.reject_credential_read(lambda: contracts.capture(self.vault, path), path)

    def test_known_credential_field_sources_are_rejected_before_read(self):
        text = (self.vault / 'notes/request.md').read_text()
        for path in self.credential_paths:
            self.write(path, text)
            for field_index in range(len(self.definition['fields'])):
                with self.subTest(path=path, field_index=field_index):
                    definition = copy.deepcopy(self.definition)
                    definition['fields'][field_index]['sources'][0]['path'] = path
                    self.write_json('input/credential-source.json', definition)
                    self.reject_credential_read(
                        lambda: contracts.capture(self.vault, 'input/credential-source.json'), path)

    def test_revision_credential_inputs_and_sources_are_rejected_before_read(self):
        first = self.capture()
        for path in self.credential_paths:
            with self.subTest(path=path, kind='input'):
                self.write_json(path, self.definition)
                self.reject_credential_read(
                    lambda: contracts.revise(self.vault, first['contract_id'], path), path)
            with self.subTest(path=path, kind='source'):
                self.write(path, (self.vault / 'notes/request.md').read_text())
                definition = copy.deepcopy(self.definition)
                definition['fields'][0]['sources'][0]['path'] = path
                self.write_json('input/credential-revision.json', definition)
                self.reject_credential_read(lambda: contracts.revise(
                    self.vault, first['contract_id'], 'input/credential-revision.json'), path)

    def test_forged_credential_receipt_bindings_are_stale_without_read_or_history(self):
        first = self.capture()
        original = json.loads((self.vault / first['path']).read_bytes())
        for path in self.credential_paths:
            for binding in ('input', 'source', 'declaration_only'):
                with self.subTest(path=path, binding=binding):
                    receipt = copy.deepcopy(original)
                    payload = receipt['payload']
                    if binding == 'input':
                        self.write_json(path, self.definition)
                        info = {'path': path, 'size': (self.vault / path).stat().st_size,
                                'sha256': contracts.evidence.digest((self.vault / path).read_bytes())}
                        payload['input_source'] = info
                    else:
                        self.write(path, (self.vault / 'notes/request.md').read_text())
                        payload['definition']['fields'][0]['sources'][0]['path'] = path
                        if binding == 'source':
                            payload['source_bindings'].append({'path': path,
                                'size': (self.vault / path).stat().st_size,
                                'sha256': contracts.evidence.digest((self.vault / path).read_bytes())})
                            payload['source_bindings'].sort(key=lambda f: f['path'])
                    identifier = contracts.evidence.digest(contracts.evidence.encode(payload))
                    receipt['id'] = identifier
                    receipt['receipt_sha256'] = contracts.evidence.digest(contracts.evidence.encode(
                        {k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
                    relative = contracts.runs.RUNTIME_DIR + '/task-contract-' + identifier + '.json'
                    self.write_json(relative, receipt)
                    stable_read = contracts.evidence.stable_read
                    def guarded_read(resolver, *args, **kwargs):
                        self.assertNotEqual(resolver(), self.vault / path,
                                            'forged credential binding reached byte reader')
                        return stable_read(resolver, *args, **kwargs)
                    try:
                        with mock.patch.object(contracts.evidence, 'stable_read', guarded_read):
                            result = contracts.inspect(self.vault, identifier)
                        self.assertEqual(result['status'], 'stale')
                        self.assertEqual(result['accepted_fields'], [])
                        self.assertIsNone(result['historical_metadata'])
                    finally:
                        (self.vault / relative).unlink()

    def test_changed_config_deny_policy_prevents_old_bindings_from_being_read(self):
        captured = self.capture()
        self.write_json('00-meta/vault-config.json', {
            'deny_zones': ['private', 'notes'], 'exclude_dirs': ['excluded']})
        stable_read = contracts.evidence.stable_read
        def guarded_read(resolver, *args, **kwargs):
            self.assertNotEqual(resolver(), self.vault / 'notes/request.md',
                                'current deny policy must guard before reading old source')
            return stable_read(resolver, *args, **kwargs)
        with mock.patch.object(contracts.evidence, 'stable_read', guarded_read):
            result = contracts.inspect(self.vault, captured['contract_id'])
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['accepted_fields'], [])
        self.assertIsNone(result['historical_metadata'])
        with self.assertRaises(contracts.ContractError):
            self.capture()

    def test_source_cannot_alias_input_and_source_text_never_executes(self):
        definition = copy.deepcopy(self.definition)
        definition['fields'][0]['sources'][0] = {'path': 'input/self.json', 'excerpt': 'Local reader'}
        self.write_json('input/self.json', definition)
        with self.assertRaises(contracts.ContractError):
            contracts.capture(self.vault, 'input/self.json')
        with mock.patch('subprocess.run', side_effect=AssertionError('no subprocess')):
            self.assertEqual(self.capture()['status'], 'ready_for_execution_review')

    def test_recomputed_receipt_checksum_cannot_change_definition_or_binding(self):
        captured = self.capture()
        path = self.vault / captured['path']
        receipt = json.loads(path.read_bytes())
        receipt['payload']['definition']['fields'][0]['text'] = 'A changed acceptance condition.'
        receipt['receipt_sha256'] = contracts.evidence.digest(contracts.evidence.encode(
            {k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
        self.write_json(captured['path'], receipt)
        self.assertEqual(contracts.inspect(self.vault, captured['contract_id'])['status'], 'stale')
        with self.assertRaises(contracts.ContractError):
            self.capture()

    def test_native_policy_rejects_symlink_and_hardlink_sources(self):
        target = self.vault / 'notes/request.md'
        for link_type in ('symlink', 'hardlink'):
            link = self.vault / ('notes/' + link_type + '.md')
            try:
                if link_type == 'symlink':
                    link.symlink_to(target)
                else:
                    os.link(target, link)
            except OSError:
                if link_type == 'hardlink':
                    self.fail('local test filesystem must support hard links')
                continue  # Windows may not grant symbolic-link creation.
            try:
                definition = copy.deepcopy(self.definition)
                definition['fields'][0]['sources'][0]['path'] = 'notes/' + link.name
                self.write_json('input/linked.json', definition)
                with self.assertRaises(contracts.ContractError):
                    contracts.capture(self.vault, 'input/linked.json')
            finally:
                link.unlink()

    def test_secret_filter_rejects_selected_source_without_echoing_contents(self):
        secret = 'sk-' + 'A' * 48
        self.write('notes/request.md', (self.vault / 'notes/request.md').read_text() + secret)
        with self.assertRaises(contracts.ContractError) as caught:
            self.capture()
        self.assertNotIn(secret, str(caught.exception))

    def test_write_recheck_rejects_a_source_change_without_publishing_receipt(self):
        publish = contracts.runs.Store.publish
        def mutate_then_publish(store, *args, **kwargs):
            self.write('notes/request.md', 'Changed before publication.')
            return publish(store, *args, **kwargs)
        with mock.patch.object(contracts.runs.Store, 'publish', mutate_then_publish):
            with self.assertRaises(contracts.ContractError):
                self.capture()
        directory = self.vault / contracts.runs.RUNTIME_DIR
        self.assertEqual(list(directory.glob('task-contract-*.json')), [])

    def test_receipt_count_bound_never_publishes_an_immediately_stale_contract(self):
        first = self.capture()
        self.definition['task'] = 'Second task'
        self.write_json('input/second.json', self.definition)
        with mock.patch.object(contracts, 'MAX_CONTRACTS', 1):
            with self.assertRaises(contracts.ContractError):
                contracts.capture(self.vault, 'input/second.json')
        self.assertEqual(contracts.inspect(self.vault, first['contract_id'])['status'],
                         'ready_for_execution_review')
        self.assertEqual(len(list((self.vault / contracts.runs.RUNTIME_DIR).glob('task-contract-*.json'))), 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction policy')
    def test_junction_sources_and_runtime_are_rejected(self):
        junction = self.vault / 'linked'
        make_junction(junction, self.vault / 'notes')
        self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)
        definition = copy.deepcopy(self.definition)
        definition['fields'][0]['sources'][0]['path'] = 'linked/request.md'
        self.write_json('input/linked.json', definition)
        with self.assertRaises(contracts.ContractError):
            contracts.capture(self.vault, 'input/linked.json')
        runtime_target = Path(self.tmp.name) / 'runtime-target'
        runtime_target.mkdir()
        runtime = self.vault / contracts.runs.RUNTIME_DIR
        runtime.parent.mkdir(parents=True, exist_ok=True)
        make_junction(runtime, runtime_target)
        self.addCleanup(lambda: runtime.rmdir() if runtime.exists() else None)
        with self.assertRaises(contracts.ContractError):
            self.capture()

    def test_held_native_write_lease_cannot_publish_a_receipt(self):
        with contracts.state.advisory_lock(self.vault, 'task-contracts', wait_seconds=1):
            with self.assertRaises(contracts.ContractError):
                self.capture()
        self.assertEqual(self.capture()['status'], 'ready_for_execution_review')

    def test_cli_inspect_check_alias_and_errors_are_content_free(self):
        captured = self.capture()
        script = str(SCRIPTS / 'vault_task_contracts.py')
        result = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'capture',
                                 '--input', 'input/task.json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['contract_id'], captured['contract_id'])
        for command in ('inspect', 'check'):
            result = subprocess.run([sys.executable, script, '--vault', str(self.vault), command,
                                     captured['contract_id']], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['contract_id'], captured['contract_id'])
        result = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'inspect',
                                 'private-value'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('private-value', result.stdout + result.stderr)
        self.write_json('input/revision.json', self.definition)
        result = subprocess.run([sys.executable, script, '--vault', str(self.vault), 'revise',
                                 captured['contract_id'], '--input', 'input/revision.json'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['parent']['contract_id'], captured['contract_id'])


if __name__ == '__main__':
    unittest.main()
