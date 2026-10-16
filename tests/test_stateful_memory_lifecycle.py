"""Real lifecycle APIs checked against independent byte/state expectations.

Ordinary unittest discovery skips these optional tests without Hypothesis.
Dedicated (zero-skip) run::

    python tests/test_stateful_memory_lifecycle.py
    python tests/test_stateful_memory_lifecycle.py --seed 20261016
    python tests/test_stateful_memory_lifecycle.py --exploratory --max-examples 100 --steps 50

``--negative-control`` intentionally duplicates an idempotent apply's physical
target replacement in the test adapter. It must fail and shrink; production code
is never changed. No real vault, network, sleep, or external operation is used.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


try:
    from hypothesis import seed as hypothesis_seed, settings, strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test
except ModuleNotFoundError as exc:
    if exc.name != 'hypothesis':
        raise
    HAVE_HYPOTHESIS = False
else:
    HAVE_HYPOTHESIS = True

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
proposals = importlib.import_module('vault_proposals')
operations = importlib.import_module('vault_operations')


if HAVE_HYPOTHESIS:
    GENERATION = settings(max_examples=25, stateful_step_count=25, deadline=None,
                          database=None, derandomize=True)

    class TemporaryVaultMachine(RuleBasedStateMachine):
        def __init__(self):
            super().__init__()
            self.resources = ExitStack()
            self.temp = self.resources.enter_context(tempfile.TemporaryDirectory())
            self.vault = Path(self.temp).resolve()
            self.config = self.vault / '00-meta/vault-config.json'
            self.config.parent.mkdir()
            self.config.write_bytes(b'{"required_keys":["title"],"enums":{}}\n')

        def teardown(self):
            self.resources.close()

    class ProposalLifecycleMachine(TemporaryVaultMachine):
        """Catch stale writes, lost external edits and repeated physical writes.

        A receipt records history: an applied receipt does not imply its bytes
        remain current after another proposal or external edit. Equality is the
        CAS contract, so an external A -> B -> A edit is allowed to become ready.
        """
        duplicate_retries = False

        def __init__(self):
            super().__init__()
            self.target = self.vault / 'CLAUDE.md'
            self.expected = b'# Synthetic rules\r\nOriginal \xed\x95\x9c\xea\xb8\x80\r\n'
            self.target.write_bytes(self.expected)
            self.candidate_file = self.config.parent / 'candidate.md'
            self.records = []
            self.expected_writes = 0
            self.actual_writes = 0
            self.fault = 'none'
            real_replace = os.replace

            def replace(source, destination):
                destination = Path(destination)
                if destination == self.target and self.fault == 'before-target':
                    raise OSError('synthetic target publication failure')
                if (self.fault == 'accounting' and destination.parent.name == 'proposals'
                        and json.loads(Path(source).read_bytes()).get('status')
                        in ('applied', 'rolled_back')):
                    raise OSError('synthetic final accounting failure')
                result = real_replace(source, destination)
                if destination == self.target:
                    self.actual_writes += 1
                return result

            # Replace only this module's os binding, never global os.replace.
            self.resources.enter_context(mock.patch.object(
                proposals, 'os', SimpleNamespace(**{**vars(os), 'replace': replace})))
            self.prepare(0)

        def record(self, slot):
            return self.records[slot % len(self.records)]

        def receipt_bytes(self, record):
            return (self.vault / '00-meta/proposals' / (record['id'] + '.json')).read_bytes()

        def mutate(self, action, record=None, *, preview_hash=None):
            # Match main(): a new Store, cooperative lock, fresh receipt on each
            # mutator, and approval supplied explicitly to rollback.
            store = proposals.Store(self.vault)
            with store.lock():
                if action == 'propose':
                    return proposals.propose(store, SimpleNamespace(
                        target='CLAUDE.md', candidate='00-meta/candidate.md',
                        lesson='L-SYNTHETIC', summary='Generated lifecycle fixture'))
                receipt = store.load(record['id'])
                if action == 'apply':
                    result = proposals.apply(store, receipt)
                    if self.duplicate_retries and result.get('idempotent'):
                        # Deliberately broken *test adapter*: same bytes, extra
                        # physical write. Byte-only assertions would miss this.
                        proposals.atomic_write(self.target, record['candidate'])
                    return result
                return proposals.rollback(store, receipt, approve=True,
                                          preview_hash=preview_hash)

        @rule(value=st.integers(min_value=0, max_value=5))
        def prepare(self, value):
            candidate = ('# Synthetic rules\r\nCandidate %s \U0001f331\r\n' % value).encode('utf-8')
            if candidate == self.expected:
                candidate += b'Another revision\r\n'
            self.candidate_file.write_bytes(candidate)
            result = self.mutate('propose')
            self.records.append(dict(id=result['id'], base=self.expected,
                                     candidate=candidate, status='proposed', preview=None,
                                     preview_config=None))

        @rule(slot=st.integers(min_value=0, max_value=8),
              which=st.sampled_from(['outside', 'base', 'candidate']))
        def external_edit(self, slot, which):
            record = self.record(slot)
            self.expected = {'outside': b'Unrelated external edit\r\n',
                             'base': record['base'], 'candidate': record['candidate']}[which]
            self.target.write_bytes(self.expected)

        @rule(slot=st.integers(min_value=0, max_value=8),
              fault=st.sampled_from(['none', 'before-target', 'accounting']))
        def apply(self, slot, fault):
            record = self.record(slot)
            before = self.receipt_bytes(record)
            status = record['status']
            allowed = ((status == 'proposed' and self.expected == record['base'])
                       or (status == 'applying' and self.expected in
                           (record['base'], record['candidate']))
                       or (status == 'applied' and self.expected == record['candidate']))
            target_write = allowed and status != 'applied' and self.expected != record['candidate']
            failure = allowed and status != 'applied' and (
                fault == 'accounting' or (fault == 'before-target' and target_write))
            self.fault = fault
            try:
                if not allowed or failure:
                    try:
                        self.mutate('apply', record)
                    except (proposals.ProposalError, OSError):
                        pass
                    else:
                        raise AssertionError('stale or faulted apply unexpectedly succeeded')
                else:
                    result = self.mutate('apply', record)
                    assert result['status'] == 'applied'
                    assert bool(result.get('idempotent')) == (status == 'applied')
            finally:
                self.fault = 'none'
            if not allowed:
                assert self.receipt_bytes(record) == before
                return
            if status == 'applied':
                assert self.receipt_bytes(record) == before
                return
            record['status'] = 'applying' if failure else 'applied'
            if target_write and fault != 'before-target':
                self.expected = record['candidate']
                self.expected_writes += 1

        @rule(slot=st.integers(min_value=0, max_value=8))
        def preview(self, slot):
            record = self.record(slot)
            before = self.receipt_bytes(record)
            allowed = ((record['status'] == 'applied' and self.expected == record['candidate'])
                       or (record['status'] == 'rolling_back' and self.expected in
                           (record['base'], record['candidate']))
                       or (record['status'] == 'rolled_back' and self.expected == record['base']))
            # Recovery stays bound to the configuration of the first preview.
            if record['status'] in ('rolling_back', 'rolled_back'):
                allowed = allowed and self.config.read_bytes() == record['preview_config']
            store = proposals.Store(self.vault)
            try:
                result = proposals.rollback_preview(store, store.load(record['id']))
            except proposals.ProposalError:
                assert not allowed
            else:
                assert allowed
                record['preview'] = result['preview_sha256']
                record['preview_config'] = self.config.read_bytes()
                assert result['recovery_available'] == (
                    record['status'] == 'rolling_back' and self.expected == record['base'])
            assert self.receipt_bytes(record) == before

        @rule(slot=st.integers(min_value=0, max_value=8), stale=st.booleans(),
              fault=st.sampled_from(['none', 'before-target', 'accounting']))
        def rollback(self, slot, stale, fault):
            record = self.record(slot)
            before = self.receipt_bytes(record)
            status = record['status']
            allowed = (not stale and record['preview'] is not None
                       and self.config.read_bytes() == record['preview_config']
                       and ((status == 'applied' and self.expected == record['candidate'])
                            or (status == 'rolling_back' and self.expected in
                                (record['base'], record['candidate']))
                            or (status == 'rolled_back' and self.expected == record['base'])))
            target_write = allowed and status != 'rolled_back' and self.expected != record['base']
            failure = allowed and status != 'rolled_back' and (
                fault == 'accounting' or (fault == 'before-target' and target_write))
            token = '0' * 64 if stale or record['preview'] is None else record['preview']
            self.fault = fault
            try:
                if not allowed or failure:
                    try:
                        self.mutate('rollback', record, preview_hash=token)
                    except (proposals.ProposalError, OSError):
                        pass
                    else:
                        raise AssertionError('stale or faulted rollback unexpectedly succeeded')
                else:
                    result = self.mutate('rollback', record, preview_hash=token)
                    assert result['status'] == 'rolled_back'
                    assert bool(result.get('idempotent')) == (status == 'rolled_back')
            finally:
                self.fault = 'none'
            if not allowed or status == 'rolled_back':
                assert self.receipt_bytes(record) == before
                return
            record['status'] = 'rolling_back' if failure else 'rolled_back'
            if target_write and fault != 'before-target':
                self.expected = record['base']
                self.expected_writes += 1

        @rule()
        def change_config_revision(self):
            # Byte revision matters to undo even when parsed policy is identical.
            raw = self.config.read_bytes()
            self.config.write_bytes(raw.rstrip() + (b'\n\n' if raw.endswith(b'\n')
                                                    and not raw.endswith(b'\n\n') else b'\n'))

        @invariant()
        def exact_bytes_status_and_write_budget(self):
            assert self.target.read_bytes() == self.expected, 'lost or overwritten target bytes'
            assert self.actual_writes == self.expected_writes, 'duplicated or missing physical target write'
            assert not (self.vault / '00-meta/proposals/.lock').exists()
            for record in self.records:
                store = proposals.Store(self.vault)
                before = self.receipt_bytes(record)
                receipt = store.load(record['id'])
                assert receipt['status'] == record['status']
                assert receipt['original'].encode('utf-8') == record['base']
                assert receipt['candidate'].encode('utf-8') == record['candidate']
                report = proposals.check(store, receipt)
                current = ((record['status'] == 'proposed' and self.expected == record['base'])
                           or (record['status'] == 'applying' and self.expected in
                               (record['base'], record['candidate']))
                           or (record['status'] == 'applied' and self.expected == record['candidate']))
                assert report['valid'] == current
                assert self.receipt_bytes(record) == before, 'check changed durable accounting'

    class OperationLifecycleMachine(TemporaryVaultMachine):
        """Catch replay claims, expiry writes on inspect and stale reconciliation."""
        def __init__(self):
            super().__init__()
            self.clock = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            self.resources.enter_context(mock.patch.object(
                operations, 'time', SimpleNamespace(time=lambda: self.clock)))
            self.resources.enter_context(mock.patch.object(
                operations, '_now', lambda: datetime.fromtimestamp(
                    self.clock, timezone.utc).isoformat()))
            self.records = {}

        def receipt_bytes(self, record):
            return (self.vault / operations.RUNTIME_DIR /
                    ('operation-' + record['id'] + '.json')).read_bytes()

        def inspect(self, record):
            before = self.receipt_bytes(record)
            result = operations.inspect(self.vault, record['id'])
            assert self.receipt_bytes(record) == before, 'inspection persisted expiry'
            assert 'owner_token' not in result
            return result

        def expired(self, record):
            return self.clock >= record['created'] + 2

        def transition(self, record, state, event, result=None):
            record['state'] = state
            record['events'].append(event)
            record['result'] = result
            record['revision'] += 1

        @rule(slot=st.integers(min_value=0, max_value=2), value=st.integers(0, 2))
        def begin(self, slot, value):
            record = self.records.get(slot)
            if record is not None and record['value'] != value:
                before = self.receipt_bytes(record)
                try:
                    operations.begin(self.vault, 'synthetic-intent-%s' % slot,
                                     'synthetic-action', {'count': value},
                                     ttl_seconds=2, wait_seconds=0)
                except operations.OperationError as exc:
                    assert str(exc) == 'intent_conflict'
                else:
                    raise AssertionError('same intent accepted conflicting parameters')
                assert self.receipt_bytes(record) == before
                return
            before = self.receipt_bytes(record) if record else None
            result = operations.begin(self.vault, 'synthetic-intent-%s' % slot,
                                      'synthetic-action', {'count': value},
                                      ttl_seconds=2, wait_seconds=0)
            if record is None:
                assert result['disposition'] == 'claimed'
                self.records[slot] = dict(id=result['id'], owner=result['owner_token'],
                    value=value, state='pending', created=self.clock, events=['claimed'],
                    result=None, revision=0, proof=None, proof_revision=None)
                return
            if record['state'] == 'pending' and self.expired(record):
                self.transition(record, 'uncertain', 'lease-expired')
            else:
                assert self.receipt_bytes(record) == before, 'retry rewrote stable receipt'
            assert result['disposition'] == {'succeeded': 'reused',
                'not-executed': 'closed'}.get(record['state'], 'blocked')
            assert 'owner_token' not in result, 'retry granted another execution owner'

        @rule(seconds=st.integers(min_value=0, max_value=4))
        def advance_clock(self, seconds):
            self.clock += seconds

        @rule(slot=st.integers(min_value=0, max_value=2), wrong_owner=st.booleans())
        def finish(self, slot, wrong_owner):
            record = self.records.get(slot)
            if record is None:
                return
            before = self.receipt_bytes(record)
            allowed = record['state'] == 'pending' and not wrong_owner
            expired = allowed and self.expired(record)
            metadata = {'effect_count': 1, 'http_status': 202}
            try:
                result = operations.finish(self.vault, record['id'],
                    '0' * 32 if wrong_owner else record['owner'], metadata)
            except operations.OperationError:
                assert not allowed or expired
            else:
                assert allowed and not expired
                assert result['state'] == 'succeeded'
            if expired:
                self.transition(record, 'uncertain', 'lease-expired')
            elif allowed:
                self.transition(record, 'succeeded', 'finished', metadata)
            else:
                assert self.receipt_bytes(record) == before

        @rule(slot=st.integers(min_value=0, max_value=2), wrong_owner=st.booleans())
        def mark_uncertain(self, slot, wrong_owner):
            record = self.records.get(slot)
            if record is None:
                return
            before = self.receipt_bytes(record)
            allowed = record['state'] == 'pending' and not wrong_owner
            try:
                result = operations.mark_uncertain(self.vault, record['id'],
                    '0' * 32 if wrong_owner else record['owner'])
            except operations.OperationError:
                assert not allowed
            else:
                assert allowed and result['state'] == 'uncertain'
            if allowed:
                self.transition(record, 'uncertain', 'caller-unknown')
            else:
                assert self.receipt_bytes(record) == before

        @rule(slot=st.integers(min_value=0, max_value=2))
        def capture_reconciliation_proof(self, slot):
            record = self.records.get(slot)
            if record is not None:
                record['proof'] = self.inspect(record)['receipt_sha256']
                record['proof_revision'] = record['revision']

        @rule(slot=st.integers(min_value=0, max_value=2), saved_proof=st.booleans(),
              success=st.booleans())
        def reconcile(self, slot, saved_proof, success):
            record = self.records.get(slot)
            if record is None:
                return
            before = self.receipt_bytes(record)
            current = self.inspect(record)
            proof = (record['proof'] or '0' * 64) if saved_proof else current['receipt_sha256']
            matching = not saved_proof or record['proof_revision'] == record['revision']
            allowed = record['state'] == 'uncertain' and matching
            outcome = 'succeeded' if success else 'not-executed'
            metadata = {'effect_count': 1} if success else None
            try:
                result = operations.reconcile(self.vault, record['id'], proof,
                                               outcome=outcome, result=metadata)
            except operations.OperationError:
                assert not allowed
            else:
                assert allowed and result['state'] == outcome
            if allowed:
                self.transition(record, outcome, 'reconciled-success' if success
                                else 'reconciled-not-executed', metadata)
            else:
                assert self.receipt_bytes(record) == before

        @invariant()
        def independent_state_history_and_read_only_expiry(self):
            for record in self.records.values():
                report = self.inspect(record)
                assert report['state'] == record['state']
                assert report['result'] == record['result']
                assert [event['event'] for event in report['events']] == record['events']
                assert report['lease_expired'] == (
                    record['state'] == 'pending' and self.expired(record))
                assert report['current'] is True
                if report['state'] == 'succeeded':
                    assert record['events'][-1] in ('finished', 'reconciled-success')
                assert record['events'].count('claimed') == 1, 'operation was claimed twice'

    class TestLifecycleModelAnchors(unittest.TestCase):
        """Guarantee important edges even if a bounded random run misses them."""
        def machine(self, cls):
            machine = cls()
            self.addCleanup(machine.teardown)
            return machine

        def proposal_step(self, machine, method, *args):
            getattr(machine, method)(*args)
            machine.exact_bytes_status_and_write_budget()

        def operation_step(self, machine, method, *args):
            getattr(machine, method)(*args)
            machine.independent_state_history_and_read_only_expiry()

        def test_stale_apply_and_undo_preserve_edits_but_aba_bytes_can_be_ready(self):
            machine = self.machine(ProposalLifecycleMachine)
            step = lambda method, *args: self.proposal_step(machine, method, *args)
            step('external_edit', 0, 'outside')
            step('apply', 0, 'none')  # Stale: preserve the external bytes.
            step('external_edit', 0, 'base')  # A -> B -> A is content-equal.
            step('apply', 0, 'none')
            step('apply', 0, 'none')  # No second physical target write.
            step('preview', 0)
            step('external_edit', 0, 'outside')
            step('rollback', 0, False, 'none')
            self.assertEqual(machine.records[0]['status'], 'applied')  # Historical only.
            step('external_edit', 0, 'candidate')
            step('rollback', 0, False, 'none')
            step('rollback', 0, False, 'none')
            self.assertEqual(machine.actual_writes, 2)

        def test_failed_publication_and_accounting_recover_without_repeating_writes(self):
            machine = self.machine(ProposalLifecycleMachine)
            step = lambda method, *args: self.proposal_step(machine, method, *args)
            step('apply', 0, 'before-target')
            step('external_edit', 0, 'outside')
            step('apply', 0, 'none')
            step('external_edit', 0, 'base')
            step('apply', 0, 'accounting')
            self.assertEqual(machine.records[0]['status'], 'applying')
            step('apply', 0, 'none')
            step('preview', 0)
            step('rollback', 0, False, 'accounting')
            self.assertEqual(machine.records[0]['status'], 'rolling_back')
            step('external_edit', 0, 'outside')
            step('rollback', 0, False, 'none')
            step('external_edit', 0, 'base')
            step('preview', 0)
            step('rollback', 0, False, 'none')
            self.assertEqual(machine.actual_writes, 2)

        def test_undo_proof_is_bound_to_current_configuration_bytes(self):
            machine = self.machine(ProposalLifecycleMachine)
            step = lambda method, *args: self.proposal_step(machine, method, *args)
            step('apply', 0, 'none')
            step('preview', 0)
            step('change_config_revision')
            step('rollback', 0, False, 'none')
            self.assertEqual(machine.records[0]['status'], 'applied')
            step('preview', 0)
            step('rollback', 0, True, 'none')
            step('rollback', 0, False, 'none')
            self.assertEqual(machine.actual_writes, 2)

        def test_expiry_inspection_and_stale_proof_cannot_create_success_or_replay(self):
            machine = self.machine(OperationLifecycleMachine)
            step = lambda method, *args: self.operation_step(machine, method, *args)
            step('begin', 0, 0)
            step('capture_reconciliation_proof', 0)
            step('advance_clock', 3)  # Inspection reports expiry, persists pending.
            self.assertEqual(machine.records[0]['state'], 'pending')
            step('reconcile', 0, True, True)  # Pending cannot reconcile.
            step('begin', 0, 0)  # Persist expiry and fence the old owner.
            step('finish', 0, False)
            step('reconcile', 0, True, True)  # Earlier proof is stale.
            step('reconcile', 0, False, True)
            step('begin', 0, 0)  # Success is reused; no new execution token.
            step('begin', 1, 1)
            step('mark_uncertain', 1, True)
            step('mark_uncertain', 1, False)
            step('reconcile', 1, False, False)
            step('begin', 1, 1)  # Not-executed is permanently closed.
            step('begin', 2, 2)
            step('finish', 2, False)  # Success also has the explicit finish path.
            self.assertEqual([machine.records[i]['state'] for i in range(3)],
                             ['succeeded', 'not-executed', 'succeeded'])

    TestProposalLifecycle = ProposalLifecycleMachine.TestCase
    TestProposalLifecycle.settings = GENERATION
    TestOperationLifecycle = OperationLifecycleMachine.TestCase
    TestOperationLifecycle.settings = GENERATION
else:
    @unittest.skip('optional Hypothesis unavailable; install requirements-dev.txt')
    class TestProposalLifecycle(unittest.TestCase):
        def test_generated_lifecycle(self):
            pass

    @unittest.skip('optional Hypothesis unavailable; install requirements-dev.txt')
    class TestOperationLifecycle(unittest.TestCase):
        def test_generated_lifecycle(self):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-examples', type=int, default=25)
    parser.add_argument('--steps', type=int, default=25)
    randomness = parser.add_mutually_exclusive_group()
    randomness.add_argument('--exploratory', action='store_true',
                            help='use fresh random generation instead of reproducible default')
    randomness.add_argument('--seed', type=int, help='reproduce generation using an explicit seed')
    parser.add_argument('--negative-control', action='store_true')
    args = parser.parse_args()
    if not HAVE_HYPOTHESIS:
        parser.error('Hypothesis required for explicit stateful run: install requirements-dev.txt')
    if args.max_examples < 1 or args.steps < 1:
        parser.error('generation limits must be positive')
    generation = settings(max_examples=args.max_examples, stateful_step_count=args.steps,
                          deadline=None, database=None, derandomize=not args.exploratory)
    if args.negative_control:
        class DuplicateWriteControl(ProposalLifecycleMachine):
            duplicate_retries = True
        if args.seed is not None:
            DuplicateWriteControl = hypothesis_seed(args.seed)(DuplicateWriteControl)
        run_state_machine_as_test(DuplicateWriteControl, settings=generation)
        raise AssertionError('negative control was not detected; increase generation budget')
    anchors = unittest.defaultTestLoader.loadTestsFromTestCase(TestLifecycleModelAnchors)
    if not unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(anchors).wasSuccessful():
        raise SystemExit(1)
    for machine in (ProposalLifecycleMachine, OperationLifecycleMachine):
        if args.seed is not None:
            machine = hypothesis_seed(args.seed)(machine)
        run_state_machine_as_test(machine, settings=generation)
        print('%s: PASS (at most %s examples, %s steps each; %s; zero skips)' %
              (machine.__name__, args.max_examples, args.steps,
               'seed=%s' % args.seed if args.seed is not None else
               ('exploratory' if args.exploratory else 'deterministic')))


if __name__ == '__main__':
    main()
