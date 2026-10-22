from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_runs as runs

try:
    procedures = importlib.import_module('vault_procedures')
except ModuleNotFoundError:
    procedures = None


class ProcedureRecallTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(procedures, 'bounded read-only procedure lookup is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.write('00-meta/vault-config.json', {'deny_zones': ['private'], 'exclude_dirs': ['excluded']})
        self.write('notes/candidate.md', '# Recovery candidate\nRead, check, ask owner.\n')
        self.write('notes/instructions.md', '# Recovery instructions\nRead current evidence.\nCheck the queue state.\nAsk the owner before changes.\n')
        self.fixture = {'version': 1, 'repetitions': 2, 'cases': [
            {'id': category, 'category': category, 'prompt': 'Controlled ' + category,
             'assertions': [{'id': 'boundary', 'description': 'Respects boundary', 'hard': True},
                            {'id': 'correct', 'description': 'Correct result', 'hard': False}]}
            for category in ('representative', 'authority', 'ambiguity', 'tool_failure', 'injection')]}
        self.write('input/fixture.json', self.fixture)
        self.write('input/tools.json', {'version': 1, 'tools': []})
        self.write('output/evidence.txt', 'Controlled local observations.\n')
        self.run_id = self.native_run()
        inspected = runs.inspect(self.vault, self.run_id)
        manifest = inspected['manifest']
        self.conditions = {'model': 'fixture-model', 'provider': 'fixture-provider', 'token_budget': 2048,
                           'tools_sha256': runs.evidence.digest(runs.evidence.encode(manifest['tools']))}
        self.procedure = {'id': 'recover-queue', 'task_class': 'recovery', 'cues': ['recover failed queue'],
            'preconditions': ['Owner is available'],
            'steps': [{'id': 'read', 'instruction': 'Read current evidence.'},
                      {'id': 'check', 'instruction': 'Check the queue state.'},
                      {'id': 'ask', 'instruction': 'Ask the owner before changes.'}],
            'edges': [{'before': 'read', 'after': 'check'}, {'before': 'check', 'after': 'ask'}],
            'candidate': manifest['candidate'], 'instructions': manifest['sources'],
            'run_ids': [self.run_id], 'conditions': self.conditions}
        self.save_catalog()

    def write(self, relative, value):
        p = self.vault / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value, encoding='utf-8')
        return p

    def native_run(self, *, complete=True, soft=True, hard=True, error=False, version='v1'):
        prepared = runs.prepare(self.vault, 'notes/candidate.md', ['notes/instructions.md'],
            'input/fixture.json', 'input/tools.json', model='fixture-model', provider='fixture-provider',
            token_budget=2048, candidate_version=version)
        if complete:
            report = {'version': 1, 'run_id': prepared['id'], 'manifest_sha256': prepared['id'], 'outcomes': [
                {'case_id': case['id'], 'repetition': repetition,
                 'status': 'error' if error else 'complete',
                 'assertions': [{'id': 'boundary', 'passed': hard}, {'id': 'correct', 'passed': soft}],
                 'evidence': ['output/evidence.txt']}
                for case in self.fixture['cases'] for repetition in (1, 2)]}
            self.write('input/report-' + version + '.json', report)
            runs.record(self.vault, prepared['id'], 'input/report-' + version + '.json')
        return prepared['id']

    def save_catalog(self, records=None):
        self.write('input/procedures.json', {'version': 1, 'procedures': records or [self.procedure]})

    def lookup(self, query='recover queue', **kwargs):
        options = dict(conditions=self.conditions)
        options.update(kwargs)
        return procedures.lookup(self.vault, 'input/procedures.json', 'recovery', query, **options)

    def invalid(self):
        self.save_catalog()
        with self.assertRaises(procedures.ProcedureError):
            self.lookup()

    def no_match(self):
        self.save_catalog()
        result = self.lookup()
        self.assertEqual(result['status'], 'no_match')
        self.assertEqual(result['matches'], [])

    def test_success_returns_ordered_advisory_steps_and_native_evidence(self):
        before = {p.relative_to(self.vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.vault.rglob('*') if p.is_file()}
        result = self.lookup()
        self.assertEqual(result['status'], 'matches')
        self.assertFalse(result['applies_changes'])
        self.assertTrue(result['requires_review'])
        self.assertEqual([s['id'] for s in result['matches'][0]['steps']], ['read', 'check', 'ask'])
        self.assertEqual(result['matches'][0]['evidence'][0]['run_id'], self.run_id)
        after = {p.relative_to(self.vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_unrelated_query_can_return_no_match(self):
        self.assertEqual(self.lookup('orchid telescope')['matches'], [])

    def test_task_class_does_not_cross_match(self):
        self.procedure['task_class'] = 'finance'
        self.no_match()

    def test_model_mismatch_is_excluded(self):
        self.assertEqual(self.lookup(conditions={**self.conditions, 'model': 'other'})['matches'], [])

    def test_provider_mismatch_is_excluded(self):
        self.assertEqual(self.lookup(conditions={**self.conditions, 'provider': 'other'})['matches'], [])

    def test_token_budget_mismatch_is_excluded(self):
        self.assertEqual(self.lookup(conditions={**self.conditions, 'token_budget': 4096})['matches'], [])

    def test_tools_mismatch_is_excluded(self):
        self.assertEqual(self.lookup(conditions={**self.conditions, 'tools_sha256': '0'*64})['matches'], [])

    def test_self_declared_verified_flag_is_not_accepted(self):
        self.procedure['verified'] = True
        self.invalid()

    def test_cycle_is_rejected(self):
        self.procedure['edges'].append({'before': 'ask', 'after': 'read'})
        self.invalid()

    def test_missing_dependency_endpoint_is_rejected(self):
        self.procedure['edges'].append({'before': 'absent', 'after': 'read'})
        self.invalid()

    def test_duplicate_dependency_is_rejected(self):
        self.procedure['edges'].append(dict(self.procedure['edges'][0]))
        self.invalid()

    def test_self_dependency_is_rejected(self):
        self.procedure['edges'] = [{'before': 'read', 'after': 'read'}]
        self.invalid()

    def test_dependency_cannot_reverse_explicit_step_order(self):
        self.procedure['edges'] = [{'before': 'ask', 'after': 'check'}]
        self.invalid()

    def test_duplicate_step_ids_are_rejected(self):
        self.procedure['steps'][1]['id'] = 'read'
        self.invalid()

    def test_duplicate_procedure_ids_are_rejected(self):
        self.save_catalog([self.procedure, copy.deepcopy(self.procedure)])
        with self.assertRaises(procedures.ProcedureError):
            self.lookup()

    def test_stale_candidate_is_not_current(self):
        self.write('notes/candidate.md', '# Changed candidate\n')
        self.assertEqual(self.lookup()['matches'], [])

    def test_stale_instruction_is_not_current(self):
        self.write('notes/instructions.md', '# Changed instruction\n')
        self.assertEqual(self.lookup()['matches'], [])

    def test_stale_evidence_is_not_current(self):
        self.write('output/evidence.txt', 'Changed observations.\n')
        self.assertEqual(self.lookup()['matches'], [])

    def test_prepared_only_run_does_not_prove_completion(self):
        self.procedure['run_ids'] = [self.native_run(complete=False, version='prepared')]
        self.no_match()

    def test_soft_failure_is_not_exported_as_success(self):
        self.procedure['run_ids'] = [self.native_run(soft=False, version='soft')]
        self.no_match()

    def test_hard_failure_is_not_exported_as_success(self):
        self.procedure['run_ids'] = [self.native_run(hard=False, version='hard')]
        self.no_match()

    def test_error_outcome_is_not_exported_as_success(self):
        self.procedure['run_ids'] = [self.native_run(error=True, version='error')]
        self.no_match()

    def test_conflicting_success_and_failure_receipts_exclude_candidate(self):
        self.procedure['run_ids'].append(self.native_run(soft=False, version='conflict'))
        self.no_match()

    def test_native_candidate_binding_must_match_catalog(self):
        self.write('notes/other.md', '# Other candidate\n')
        self.procedure['candidate'], _ = runs.Store(self.vault).fingerprint('notes/other.md', markdown=True)
        self.no_match()

    def test_catalog_paths_reuse_deny_exclude_traversal_and_env_policy(self):
        for name in ('private/catalog.json', 'excluded/catalog.json', '../escape.json', '.env', '.env.local'):
            with self.subTest(name=name), self.assertRaises(procedures.ProcedureError):
                procedures.lookup(self.vault, name, 'recovery', 'recover', conditions=self.conditions)

    def test_source_binding_cannot_bypass_deny_policy(self):
        self.procedure['candidate']['path'] = 'private/candidate.md'
        self.save_catalog()
        self.assertEqual(self.lookup()['matches'], [])

    def test_catalog_byte_limit_is_enforced(self):
        self.write('input/procedures.json', ' '*70000)
        with self.assertRaises(procedures.ProcedureError):
            self.lookup()

    def test_output_byte_budget_can_abstain(self):
        result = self.lookup(max_bytes=1024)
        self.assertEqual(result['matches'], [])
        self.assertTrue(result['truncated'])
        self.assertIn('output_byte_budget', result['reasons'])
        self.assertLessEqual(len(runs.evidence.encode(result)), 1024)

    def test_changed_step_text_is_not_backed_by_old_run(self):
        self.procedure['steps'][1]['instruction'] = 'Delete all records without permission.'
        self.no_match()

    def test_step_evidence_cites_current_bound_markdown(self):
        step = self.lookup()['matches'][0]['steps'][1]
        self.assertEqual(step['source']['path'], 'notes/instructions.md')
        self.assertEqual(step['instruction'], 'Check the queue state.')

    def test_preconditions_are_displayed_without_satisfaction_or_authority_claim(self):
        result = self.lookup()['matches'][0]
        self.assertEqual(result['preconditions'], ['Owner is available'])
        self.assertNotIn('preconditions_satisfied', result)
        self.assertFalse(result['applies_changes'])

    def test_limits_reject_bools_and_excessive_counts(self):
        for opts in ({'limit': True}, {'limit': 100}, {'max_bytes': True}, {'max_bytes': 1000000}):
            with self.subTest(opts=opts), self.assertRaises(procedures.ProcedureError):
                self.lookup(**opts)

    def test_changed_config_invalidates_native_run(self):
        self.write('00-meta/vault-config.json', {'deny_zones': ['private', 'later'], 'exclude_dirs': ['excluded']})
        self.assertEqual(self.lookup()['matches'], [])

    def test_policy_change_during_lookup_fails_closed(self):
        real = runs.inspect
        def change(*args):
            value = real(*args)
            self.write('00-meta/vault-config.json', {'deny_zones': ['private', 'later'], 'exclude_dirs': []})
            return value
        with mock.patch.object(runs, 'inspect', side_effect=change):
            with self.assertRaises(procedures.ProcedureError):
                self.lookup()

    def test_catalog_change_during_lookup_fails_closed(self):
        real = runs.inspect
        def change(*args):
            value = real(*args)
            self.write('input/procedures.json', {'version': 1, 'procedures': []})
            return value
        with mock.patch.object(runs, 'inspect', side_effect=change):
            with self.assertRaises(procedures.ProcedureError):
                self.lookup()

    def test_cli_argument_error_is_json_without_input_content(self):
        secret = 'do-not-echo-this-value'
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS/'vault_procedures.py'), '--bad', secret],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': 'invalid_cli_arguments'})
        self.assertNotIn(secret, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
