from __future__ import annotations

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
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
SCRIPT = SCRIPTS / 'vault_lesson_metrics.py'


class LessonMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        self.write('00-meta/vault-config.json', json.dumps({
            'required_keys': ['title'], 'enums': {},
        }).encode())
        self.target = self.write('CLAUDE.md', b'# Rules\nBefore\n')
        self.write('00-meta/candidate.md', b'# Rules\nAfter: retry once\n')
        self.proposals = importlib.import_module('vault_proposals')
        self.evidence = importlib.import_module('vault_evidence')
        result = self.call(self.proposals, 'propose', '--target', 'CLAUDE.md',
                           '--candidate', '00-meta/candidate.md', '--lesson', 'L-001',
                           '--summary', 'Retry recovery')
        self.proposal_id = result['id']
        self.receipt = self.vault / '00-meta/proposals' / (self.proposal_id + '.json')

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def call(self, module, *args, success=True):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = module.main(['--vault', str(self.vault), *args])
        output = json.loads(stream.getvalue())
        if success:
            self.assertEqual(code, 0, output)
        else:
            self.assertNotEqual(code, 0, output)
        return output

    def module(self):
        try:
            return importlib.import_module('vault_lesson_metrics')
        except ModuleNotFoundError:
            self.fail('Lesson metrics API has not been implemented')

    def apply(self):
        self.call(self.proposals, 'apply', self.proposal_id, '--approve')

    def observation(self, **extra):
        return dict(attempt_id='A-001', session_id='S-001', applicability='applicable',
                    outcome='success', verification_status='unverified', evidence_ids=[], **extra)

    def data(self, **extra):
        result = self.observation()
        result.update(extra)
        return result

    def observe(self, **extra):
        return self.module().record_observation(self.vault, self.proposal_id, self.data(**extra))

    def summary(self):
        return self.module().summarize(self.vault, self.proposal_id)

    def evidence_id(self, *, verified=True, pending=False):
        output = self.write('output/check.log', b'Retry succeeded\n')
        record = self.call(self.evidence, 'snapshot', '--task', 'T-001', '--objective',
                           'Check retry', '--artifact', 'CLAUDE.md')
        if not pending:
            report = {'verifier': 'Synthetic verifier', 'checks': [{
                'id': 'C-001', 'claim': 'Retry result observed',
                'status': 'verified' if verified else 'gap', 'method': 'Read test log',
                'observed': 'Retry succeeded', 'evidence': ['output/check.log'],
                'preserve': 'Retry once' if verified else '',
                'next_check': '' if verified else 'Run the test',
            }], 'next_action': 'Review next attempt'}
            self.write('00-meta/report.json', json.dumps(report).encode())
            self.call(self.evidence, 'record', record['id'], '--report', '00-meta/report.json')
        return record['id'], output

    def observation_path(self, result):
        return self.vault / result['path']

    def inventory(self):
        return {p.relative_to(self.vault).as_posix(): p.read_bytes()
                for p in self.vault.rglob('*') if p.is_file()}

    def test_cli_explicit_synthetic_input_records_without_changing_receipt_or_target(self):
        self.apply()
        before = self.inventory()
        self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        run = subprocess.run([sys.executable, str(SCRIPT), '--vault', str(self.vault),
                              'record', '--proposal-id', self.proposal_id, '--input',
                              '00-meta/observation.json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['proposal_id'], self.proposal_id)
        self.assertTrue(self.observation_path(result).is_file())
        self.assertEqual(self.target.read_bytes(), before['CLAUDE.md'])
        self.assertEqual(self.receipt.read_bytes(), before[self.receipt.relative_to(self.vault).as_posix()])
        self.assertEqual(result['authority'], 'observation_data_only')
        summary = self.call(self.module(), 'summarize', '--proposal-id', self.proposal_id)
        self.assertEqual(summary['counts']['eligible_attempts'], 1)
        self.assertEqual(summary['counts']['verified_attempts'], 0)
        self.assertEqual(summary['rates']['verified_success_rate'], None)

    def test_pending_and_rejected_observations_never_gain_effectiveness_credit(self):
        self.observe()
        summary = self.summary()
        self.assertEqual(summary['current_eligibility']['reason'], 'not_applied')
        self.assertEqual(summary['counts']['ineligible_attempts'], 1)
        self.assertEqual(summary['counts']['eligible_attempts'], 0)
        self.apply()
        self.assertEqual(self.summary()['counts']['eligible_attempts'], 0)
        # A separate pending proposal is explicitly rejected; no automatic approval occurs.
        self.write('00-meta/rejected.md', b'# Rules\nRejected alternative\n')
        proposal = self.call(self.proposals, 'propose', '--target', 'CLAUDE.md', '--candidate',
                             '00-meta/rejected.md', '--lesson', 'L-002', '--summary', 'Rejected')['id']
        self.call(self.proposals, 'reject', proposal, '--reason', 'Keep retry rule')
        self.module().record_observation(self.vault, proposal, self.data())
        rejected = self.module().summarize(self.vault, proposal)
        self.assertEqual(rejected['proposal_status'], 'rejected')
        self.assertEqual(rejected['counts']['eligible_attempts'], 0)

    def test_applicability_and_unknown_outcomes_keep_honest_denominators(self):
        self.apply()
        self.observe()
        self.observe(attempt_id='A-002', applicability='not_applicable', outcome='recurrence')
        self.observe(attempt_id='A-003', applicability='unknown', outcome='adverse')
        self.observe(attempt_id='A-004', outcome='unknown')
        self.observe(attempt_id='A-005', outcome='recurrence')
        summary = self.summary()
        self.assertEqual(summary['counts']['observations'], 5)
        self.assertEqual(summary['counts']['eligible_attempts'], 3)
        self.assertEqual(summary['counts']['known_outcome_attempts'], 2)
        self.assertEqual(summary['counts']['applicability'],
                         {'applicable': 3, 'not_applicable': 1, 'unknown': 1})
        self.assertEqual(summary['counts']['outcomes'],
                         {'success': 1, 'recurrence': 1, 'adverse': 0, 'unknown': 1})
        self.assertEqual(summary['rates']['observed_success_rate'], 0.5)

    def test_current_verified_evidence_is_distinct_from_unknown_stale_and_unverified(self):
        self.apply()
        current, output = self.evidence_id()
        valid = self.observe(verification_status='verified', evidence_ids=[current])
        saved = json.loads(self.observation_path(valid).read_bytes())
        self.assertEqual(saved['evidence'][0]['status'], 'current')
        self.assertEqual(saved['verification_status'], 'verified')
        self.assertEqual(self.summary()['counts']['verified_attempts'], 1)
        output.write_bytes(b'Different retry result')
        self.assertEqual(self.summary()['counts']['verified_attempts'], 0)
        self.assertEqual(self.summary()['observations'][0]['evidence'][0]['status'], 'stale')
        self.observe(attempt_id='A-002', verification_status='verified', evidence_ids=['0' * 32])
        pending, _ = self.evidence_id(pending=True)
        self.observe(attempt_id='A-003', verification_status='verified', evidence_ids=[pending])
        unverified, _ = self.evidence_id(verified=False)
        self.observe(attempt_id='A-004', verification_status='verified', evidence_ids=[unverified])
        summary = self.summary()
        statuses = [o['evidence'][0]['status'] for o in summary['observations']]
        self.assertEqual(statuses, ['stale', 'unknown', 'unverified', 'unverified'])
        self.assertEqual(summary['counts']['verified_attempts'], 0)
        self.assertEqual(summary['rates']['verified_success_rate'], None)

    def test_verification_claim_without_evidence_stays_unverified(self):
        self.apply()
        result = self.observe(verification_status='verified')
        self.assertEqual(json.loads(self.observation_path(result).read_bytes())['verification_status'],
                         'unverified')
        self.assertEqual(self.summary()['counts']['verified_attempts'], 0)

    def test_verified_unknown_outcome_is_excluded_from_known_outcome_denominator(self):
        self.apply()
        current, _output = self.evidence_id()
        self.observe(verification_status='verified', evidence_ids=[current], outcome='unknown')
        summary = self.summary()
        self.assertEqual(summary['counts']['verified_attempts'], 1)
        self.assertEqual(summary['counts']['verified_known_outcome_attempts'], 0)
        self.assertEqual(summary['rates']['verified_success_rate'], None)

    def test_current_evidence_does_not_upgrade_an_unverified_observation(self):
        self.apply()
        current, _output = self.evidence_id()
        self.observe(evidence_ids=[current])
        self.assertEqual(self.summary()['counts']['verified_attempts'], 0)

    def test_rebound_evidence_receipt_is_stale_even_when_its_files_are_current(self):
        self.apply()
        current, _output = self.evidence_id()
        self.observe(verification_status='verified', evidence_ids=[current])
        path = self.vault / '00-meta/evidence' / (current + '.json')
        record = json.loads(path.read_bytes())
        record['report']['checks'][0]['observed'] = 'Different host report'
        canonical = json.dumps({k: v for k, v in record.items() if k != 'receipt_sha256'},
                               ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                               allow_nan=False).encode()
        record['receipt_sha256'] = hashlib.sha256(canonical).hexdigest()
        path.write_bytes(json.dumps(record).encode())
        summary = self.summary()
        self.assertEqual(summary['observations'][0]['evidence'][0]['status'], 'stale')
        self.assertEqual(summary['counts']['verified_attempts'], 0)

    def test_summary_does_not_write_any_input_or_observation(self):
        self.apply()
        current, _output = self.evidence_id()
        self.observe(verification_status='verified', evidence_ids=[current])
        before = {p.relative_to(self.vault).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(self.summary()['counts']['verified_attempts'], 1)
        after = {p.relative_to(self.vault).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_drifted_applied_target_cannot_credit_a_new_attempt(self):
        self.apply()
        self.target.write_bytes(b'# Rules\nOperator rolled back\n')
        self.observe()
        summary = self.summary()
        self.assertEqual(summary['current_eligibility']['reason'], 'target_drifted')
        self.assertEqual(summary['counts']['eligible_attempts'], 0)
        self.assertEqual(self.target.read_bytes(), b'# Rules\nOperator rolled back\n')

    def test_duplicate_attempt_across_sessions_is_rejected_without_mutation(self):
        self.observe()
        before = self.inventory()
        with self.assertRaises(self.module().LessonMetricsError):
            self.observe(session_id='S-002')
        self.assertEqual(self.inventory(), before)

    def test_two_cli_writers_cannot_reuse_one_attempt(self):
        self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        command = [sys.executable, str(SCRIPT), '--vault', str(self.vault), 'record',
                   '--proposal-id', self.proposal_id, '--input', '00-meta/observation.json']
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outputs = [first.communicate(timeout=30), second.communicate(timeout=30)]
        self.assertEqual(sorted([first.returncode, second.returncode]), [0, 2], outputs)
        self.assertEqual(self.summary()['counts']['observations'], 1)
        self.assertEqual(self.call(self.proposals, 'inspect', self.proposal_id)['status'], 'proposed')

    def test_observation_uuid_collision_never_overwrites_existing_history(self):
        first = self.observe()
        before = self.inventory()
        module = self.module()
        with mock.patch.object(module.uuid, 'uuid4', return_value=SimpleNamespace(hex=first['id'])):
            with self.assertRaises(module.LessonMetricsError):
                self.observe(attempt_id='A-002')
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.summary()['counts']['observations'], 1)

    def test_existing_lock_blocks_record_and_read_only_summary_preserves_it(self):
        module = self.module()
        lock = self.write('00-meta/proposals/observations/.lock', b'Earlier writer')
        before = self.inventory()
        with self.assertRaises(module.LessonMetricsError):
            self.observe()
        self.assertEqual(self.summary()['counts']['observations'], 0)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(lock.read_bytes(), b'Earlier writer')

    def test_observation_checksum_and_rechecksummed_schema_mutation_fail_closed(self):
        result = self.observe()
        path = self.observation_path(result)
        original = path.read_bytes()
        changed = json.loads(original)
        changed['outcome'] = 'adverse'
        path.write_bytes(json.dumps(changed).encode())
        with self.assertRaises(self.module().LessonMetricsError):
            self.summary()
        changed['outcome'] = 'approved'
        body = {k: v for k, v in changed.items() if k != 'observation_sha256'}
        canonical = json.dumps(body, ensure_ascii=True, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode()
        changed['observation_sha256'] = hashlib.sha256(canonical).hexdigest()
        path.write_bytes(json.dumps(changed).encode())
        with self.assertRaises(self.module().LessonMetricsError):
            self.summary()

    def test_removed_middle_observation_breaks_append_chain(self):
        first = self.observe()
        self.observe(attempt_id='A-002')
        self.observation_path(first).unlink()
        with self.assertRaises(self.module().LessonMetricsError):
            self.summary()

    def test_count_and_byte_bounds_reject_before_appending(self):
        module = self.module()
        with mock.patch.object(module, 'MAX_OBSERVATIONS', 2):
            self.observe()
            self.observe(attempt_id='A-002')
            before = self.inventory()
            with self.assertRaises(module.LessonMetricsError):
                self.observe(attempt_id='A-003')
            self.assertEqual(self.inventory(), before)
        self.write('00-meta/huge.json', b' ' * (module.MAX_INPUT_BYTES + 1))
        output = self.call(module, 'record', '--proposal-id', self.proposal_id,
                           '--input', '00-meta/huge.json', success=False)
        self.assertIsInstance(output['error'], str)
        with mock.patch.object(module, 'MAX_STORE_BYTES', 1):
            with self.assertRaises(module.LessonMetricsError):
                self.summary()

    def test_aggregate_read_budget_bounds_the_entire_operation(self):
        module = self.module()
        before = self.inventory()
        with mock.patch.object(module, 'MAX_READ_BYTES', 1):
            with self.assertRaises(module.LessonMetricsError):
                self.observe()
        self.assertEqual(self.inventory(), before)

    def test_input_and_evidence_ids_are_strict_data_not_commands(self):
        module = self.module()
        for extra in ({'outcome': 'approve'}, {'applicability': True}, {'attempt_id': ''},
                      {'evidence_ids': ['../vault-config']}, {'evidence_ids': ['0' * 32] * 2},
                      {'evidence_ids': [f'{i:032x}' for i in range(17)]},
                      {'session_id': 'x' * 257},
                      {'verification_status': 'approved'}, {'approve': True}):
            with self.subTest(extra=extra):
                with self.assertRaises(module.LessonMetricsError):
                    self.observe(**extra)
        before = self.inventory()
        self.write('00-meta/instruction.json', b'{"attempt_id":"A","attempt_id":"B"}')
        result = self.call(module, 'record', '--proposal-id', self.proposal_id,
                           '--input', '00-meta/instruction.json', success=False)
        self.assertIsInstance(result['error'], str)
        self.assertEqual(self.target.read_bytes(), before['CLAUDE.md'])

    def test_denied_excluded_reserved_and_traversing_cli_inputs_are_never_read(self):
        module = self.module()
        for relative in ('90-assets/private.json', 'node_modules/private.json', '.git/private.json'):
            self.write(relative, json.dumps(self.data()).encode())
        for relative in ('90-assets/private.json', 'node_modules/private.json', '.git/private.json',
                         '../private.json', 'C:/private.json'):
            with self.subTest(relative=relative):
                result = self.call(module, 'record', '--proposal-id', self.proposal_id,
                                   '--input', relative, success=False)
                self.assertIsInstance(result['error'], str)
        for proposal_id in ('../vault-config', '', 'G' * 32):
            with self.assertRaises(module.LessonMetricsError):
                module.summarize(self.vault, proposal_id)

    def test_new_policy_denies_evidence_without_reading_its_artifact(self):
        self.apply()
        evidence_id, output = self.evidence_id()
        config = json.loads((self.vault / '00-meta/vault-config.json').read_bytes())
        config['exclude_dirs'] = ['output']
        self.write('00-meta/vault-config.json', json.dumps(config).encode())
        module = self.module()
        real_open = os.open

        def checked_open(path, *args, **kwargs):
            if Path(path) == output:
                self.fail('Newly excluded artifact was read')
            return real_open(path, *args, **kwargs)

        with mock.patch.object(module.os, 'open', side_effect=checked_open):
            self.observe(verification_status='verified', evidence_ids=[evidence_id])
            summary = self.summary()
        self.assertEqual(summary['counts']['verified_attempts'], 0)
        self.assertEqual(summary['observations'][0]['evidence'][0]['status'], 'unverified')

    def test_source_and_config_races_abort_atomic_publication(self):
        module = self.module()
        self.apply()
        original_target = self.target.read_bytes()
        original_config = (self.vault / '00-meta/vault-config.json').read_bytes()
        real_mkstemp = tempfile.mkstemp
        for kind in ('target', 'config', 'receipt'):
            with self.subTest(kind=kind):
                receipt_before = self.receipt.read_bytes()

                def change_source(*args, **kwargs):
                    fd, path = real_mkstemp(*args, **kwargs)
                    if kind == 'target':
                        self.target.write_bytes(b'PRIVATE concurrent target edit')
                    elif kind == 'config':
                        self.write('00-meta/vault-config.json', original_config + b'\n')
                    else:
                        self.receipt.write_bytes(receipt_before + b'\n')
                    return fd, path

                with mock.patch.object(module.tempfile, 'mkstemp', side_effect=change_source):
                    with self.assertRaises(module.LessonMetricsError):
                        self.observe()
                store = self.vault / '00-meta/proposals/observations' / self.proposal_id
                self.assertEqual(list(store.glob('*.json')) if store.exists() else [], [])
                self.assertEqual(list(store.glob('*.tmp')) if store.exists() else [], [])
                self.target.write_bytes(original_target)
                self.write('00-meta/vault-config.json', original_config)
                self.receipt.write_bytes(receipt_before)

    def test_temporary_observation_tampering_is_detected_before_publication(self):
        module = self.module()
        self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        real_fsync = os.fsync
        changed = False

        def change_temporary(descriptor):
            nonlocal changed
            real_fsync(descriptor)
            directory = self.vault / '00-meta/proposals/observations' / self.proposal_id
            temporary = list(directory.glob('*.tmp')) if directory.exists() else []
            if temporary and not changed:
                temporary[0].write_bytes(b'PRIVATE replacement temporary content')
                changed = True

        with mock.patch.object(module.os, 'fsync', side_effect=change_temporary):
            self.call(module, 'record', '--proposal-id', self.proposal_id,
                      '--input', '00-meta/observation.json', success=False)
        directory = self.vault / '00-meta/proposals/observations' / self.proposal_id
        self.assertEqual(list(directory.glob('*.json')), [])

    def test_directory_scan_remains_bounded_during_publication_race(self):
        module = self.module()
        real_mkstemp = tempfile.mkstemp

        def concurrent_entries(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            directory = Path(kwargs['dir'])
            for index in range(module.MAX_OBSERVATIONS + 1):
                (directory / f'{index:032x}.json').write_bytes(b'{}')
            return result

        with mock.patch.object(module.tempfile, 'mkstemp', side_effect=concurrent_entries):
            with self.assertRaises(module.LessonMetricsError) as captured:
                self.observe()
        self.assertEqual(str(captured.exception), 'observation_count_exceeded')

    def test_evidence_change_during_publication_aborts_instead_of_recording_verified(self):
        module = self.module()
        self.apply()
        current, output = self.evidence_id()
        real_mkstemp = tempfile.mkstemp

        def change_evidence(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            output.write_bytes(b'Concurrent evidence replacement')
            return result

        with mock.patch.object(module.tempfile, 'mkstemp', side_effect=change_evidence):
            with self.assertRaises(module.LessonMetricsError):
                self.observe(verification_status='verified', evidence_ids=[current])
        self.assertEqual(self.summary()['counts']['observations'], 0)

    def test_late_source_or_policy_edit_after_target_hash_read_blocks_record(self):
        module = self.module()
        self.apply()
        original_target = self.target.read_bytes()
        config_path = self.vault / '00-meta/vault-config.json'
        original_config = config_path.read_bytes()
        real_read = module.evidence.stable_read
        for kind in ('target', 'config'):
            with self.subTest(kind=kind):
                target_reads = 0

                def late_edit(resolver, *args, **kwargs):
                    nonlocal target_reads
                    path = resolver()
                    result = real_read(resolver, *args, **kwargs)
                    if path == self.target and not kwargs.get('contents', False):
                        target_reads += 1
                        if target_reads == 2:
                            if kind == 'target':
                                self.target.write_bytes(b'# Rules\nLate concurrent rollback\n')
                            else:
                                config = json.loads(original_config)
                                config['exclude_dirs'] = ['observations']
                                config_path.write_bytes(json.dumps(config).encode())
                    return result

                try:
                    with mock.patch.object(module.evidence, 'stable_read', side_effect=late_edit):
                        with self.assertRaises(module.LessonMetricsError):
                            self.observe()
                    directory = self.vault / '00-meta/proposals/observations' / self.proposal_id
                    self.assertEqual(list(directory.glob('*.json')), [])
                finally:
                    self.target.write_bytes(original_target)
                    config_path.write_bytes(original_config)
                    directory = self.vault / '00-meta/proposals/observations' / self.proposal_id
                    for path in directory.glob('*.json'):
                        path.unlink()

    def test_late_source_or_policy_edit_after_temporary_hash_blocks_record(self):
        module = self.module()
        self.apply()
        original_target = self.target.read_bytes()
        config_path = self.vault / '00-meta/vault-config.json'
        original_config = config_path.read_bytes()
        real_read = module.evidence.stable_read
        for kind in ('target', 'config'):
            with self.subTest(kind=kind):
                changed = False

                def late_edit(resolver, *args, **kwargs):
                    nonlocal changed
                    path = resolver()
                    result = real_read(resolver, *args, **kwargs)
                    if path.name.startswith('.observation-') and not changed:
                        changed = True
                        if kind == 'target':
                            self.target.write_bytes(b'# Rules\nLate concurrent rollback\n')
                        else:
                            config = json.loads(original_config)
                            config['exclude_dirs'] = ['observations']
                            config_path.write_bytes(json.dumps(config).encode())
                    return result

                try:
                    with mock.patch.object(module.evidence, 'stable_read', side_effect=late_edit):
                        with self.assertRaises(module.LessonMetricsError):
                            self.observe()
                    directory = self.vault / '00-meta/proposals/observations' / self.proposal_id
                    self.assertEqual(list(directory.glob('*.json')), [])
                finally:
                    self.target.write_bytes(original_target)
                    config_path.write_bytes(original_config)
                    directory = self.vault / '00-meta/proposals/observations' / self.proposal_id
                    for path in directory.glob('*.json'):
                        path.unlink()

    def test_summary_inventory_read_cannot_stale_final_source_or_policy_check(self):
        module = self.module()
        self.apply()
        self.observe()
        original_target = self.target.read_bytes()
        config_path = self.vault / '00-meta/vault-config.json'
        original_config = config_path.read_bytes()
        real_names = module.Store.names
        for kind in ('target', 'config'):
            with self.subTest(kind=kind):
                scans = 0

                def late_edit(store, proposal_id, **kwargs):
                    nonlocal scans
                    result = real_names(store, proposal_id, **kwargs)
                    scans += 1
                    if scans == 2:
                        if kind == 'target':
                            self.target.write_bytes(b'# Rules\nLate summary rollback\n')
                        else:
                            config = json.loads(original_config)
                            config['exclude_dirs'] = ['observations']
                            config_path.write_bytes(json.dumps(config).encode())
                    return result

                try:
                    with mock.patch.object(module.Store, 'names', new=late_edit):
                        with self.assertRaises(module.LessonMetricsError):
                            self.summary()
                finally:
                    self.target.write_bytes(original_target)
                    config_path.write_bytes(original_config)

    def test_explicit_input_change_during_publication_aborts(self):
        module = self.module()
        source = self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        real_mkstemp = tempfile.mkstemp

        def change_input(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            source.write_bytes(json.dumps(self.data(outcome='adverse')).encode())
            return result

        with mock.patch.object(module.tempfile, 'mkstemp', side_effect=change_input):
            self.call(module, 'record', '--proposal-id', self.proposal_id,
                      '--input', '00-meta/observation.json', success=False)
        self.assertEqual(self.summary()['counts']['observations'], 0)

    def test_atomic_failure_is_private_and_leaves_no_partial_observation(self):
        module = self.module()
        self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        before = self.inventory()
        with mock.patch.object(module.os, 'link', side_effect=OSError('PRIVATE injected error')):
            result = self.call(module, 'record', '--proposal-id', self.proposal_id,
                               '--input', '00-meta/observation.json', success=False)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(self.inventory(), before)

    def test_lock_flush_failure_cleans_up_only_its_own_lock(self):
        module = self.module()
        self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        before = self.inventory()
        with mock.patch.object(module.os, 'fsync', side_effect=OSError('PRIVATE lock error')):
            result = self.call(module, 'record', '--proposal-id', self.proposal_id,
                               '--input', '00-meta/observation.json', success=False)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(self.inventory(), before)

    def test_interrupted_writer_retains_lock_for_explicit_manual_recovery(self):
        module = self.module()
        with mock.patch.object(module.os, 'link', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.observe()
        lock = self.vault / '00-meta/proposals/observations/.lock'
        self.assertTrue(lock.is_file())
        self.assertEqual(self.summary()['counts']['observations'], 0)
        with self.assertRaises(module.LessonMetricsError):
            self.observe()
        lock.unlink()  # Explicit operator cleanup after the interrupted process stopped.
        self.observe()
        self.assertEqual(self.summary()['counts']['observations'], 1)

    def test_hardlinked_input_receipt_and_observation_are_rejected(self):
        module = self.module()
        source = self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        try:
            os.link(source, self.vault / '00-meta/linked.json')
        except OSError:
            self.skipTest('Hardlinks unavailable')
        self.call(module, 'record', '--proposal-id', self.proposal_id,
                  '--input', '00-meta/linked.json', success=False)
        os.link(self.target, self.vault / '00-meta/linked-target.md')
        with self.assertRaises(module.LessonMetricsError):
            self.observe()
        (self.vault / '00-meta/linked-target.md').unlink()
        config = self.vault / '00-meta/vault-config.json'
        os.link(config, self.vault / '00-meta/linked-config.json')
        with self.assertRaises(module.LessonMetricsError):
            self.observe()
        (self.vault / '00-meta/linked-config.json').unlink()
        os.link(self.receipt, self.vault / '00-meta/linked-receipt.json')
        with self.assertRaises(module.LessonMetricsError):
            self.observe()
        (self.vault / '00-meta/linked-receipt.json').unlink()
        result = self.observe()
        os.link(self.observation_path(result), self.vault / '00-meta/linked-observation.json')
        with self.assertRaises(module.LessonMetricsError):
            self.summary()

    def test_symlinked_input_and_observation_directory_are_rejected(self):
        module = self.module()
        source = self.write('00-meta/observation.json', json.dumps(self.data()).encode())
        try:
            (self.vault / '00-meta/linked.json').symlink_to(source)
        except OSError:
            self.skipTest('Symlinks unavailable')
        self.call(module, 'record', '--proposal-id', self.proposal_id,
                  '--input', '00-meta/linked.json', success=False)
        directory = self.vault / '00-meta/proposals/observations'
        external = self.vault / 'output'
        external.mkdir()
        directory.symlink_to(external, target_is_directory=True)
        with self.assertRaises(module.LessonMetricsError):
            self.observe()
        self.assertEqual(list(external.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
