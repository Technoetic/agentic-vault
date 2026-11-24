"""Generated orders through actual readers and guarded envelopes; no live sends.

Direct execution requires Hypothesis (no silent skip). ``--negative-control``
intentionally uses the real unfiltered prefix reader and must fail and shrink.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

try:
    from hypothesis import seed, settings, strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test
except ModuleNotFoundError as exc:
    if exc.name != 'hypothesis':
        raise
    HAVE_HYPOTHESIS = False
else:
    HAVE_HYPOTHESIS = True

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('egress_lifecycle_evaluation', ROOT / 'scripts/evaluate_revocation_egress.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


if HAVE_HYPOTHESIS:
    GENERATION = settings(max_examples=25, stateful_step_count=20, deadline=None,
                          database=None, derandomize=True)

    class EgressLifecycleMachine(RuleBasedStateMachine):
        unfiltered_reader = False

        def __init__(self):
            super().__init__()
            self.temp = tempfile.TemporaryDirectory()
            self.fixture = evaluation.SyntheticVault(self.temp.name)
            self.generation = 0
            self.revoked = False
            self.registry_valid = True
            self.hot_binding_valid = True
            self.prepared_evidence = None
            self.prepared_generation = None
            self.simulated_sink = []
            self.extra_markers = []
            self.derived_count = 0

        def teardown(self):
            try:
                self.terminal_checkpoint()
            finally:
                self.temp.cleanup()

        def terminal_checkpoint(self):
            """Every generated trace ends with measured current consumer outcomes.

            A refusal is evidence of unavailability, not useful masking. Rebuild
            current context here; pending stale envelopes remain tested by rules.
            """
            rows = []
            def record(consumer, text, outcome='observed'):
                if outcome == 'observed':
                    self.assert_observed_context(text)
                rows.append(evaluation.observe(self.fixture, consumer, text, outcome=outcome,
                    phase='after_revoke' if self.revoked else 'before_revoke'))

            retrieved = evaluation.recall(self.fixture.root, 'BENIGN')
            record('vault_recall.recall', retrieved['context'],
                   'observed' if retrieved['diagnostics']['status'] == 'ok' else 'unavailable')
            compiled = evaluation.compile_units(self.fixture.root,
                [self.fixture.unit(poison=True), self.fixture.unit()])
            record('vault_memory.compile_units', compiled['context'],
                   'observed' if compiled['context'] else 'unavailable')
            try:
                evidence, guard = self.fixture.prepare_evidence()
            except evaluation.judge.EXPECTED_ERRORS:
                assert not self.registry_valid or (self.revoked and not self.hot_binding_valid)
                record('vault_judge._prepare', '', 'unavailable')
                record('simulated_memory_sink', '', 'refused')
            else:
                record('vault_judge._prepare', evidence)
                guard.recheck()
                self.simulated_sink.append(evidence)
                record('simulated_memory_sink', self.simulated_sink[-1])
            self.terminal_observations = rows
            self.terminal_assessment = evaluation.assess_observations(rows)
            assert len(rows) == len(evaluation.REQUIRED_CONSUMERS)
            available = self.registry_valid and (not self.revoked or self.hot_binding_valid)
            # Before withdrawal, declared fixture markers are intentionally
            # visible. That exposure is a baseline, not a masking success.
            assert self.terminal_assessment['safe_useful'] == (self.revoked and available)

        def assert_observed_context(self, text):
            assert text and 'BENIGN_' in text, 'missing benign observation is not safe useful success'
            if self.revoked:
                for marker in self.fixture.markers + self.extra_markers:
                    assert marker not in text, 'declared withdrawn fixture reached current context'

        @rule()
        def revoke_root(self):
            if not self.revoked:
                self.fixture.revoke()
                self.revoked = True
                self.registry_valid = True
                self.generation += 1

        @rule()
        def declare_transformed_derivative(self):
            if self.derived_count >= 3:
                return
            self.derived_count += 1
            ident = 'extra' + str(self.derived_count)
            marker = 'TRANSFORMED_' + ident
            path = '20-knowledge/' + ident + '.md'
            raw = ('# Synthetic derivative\n' + marker + '\nBENIGN_' + ident + '\n').encode()
            self.fixture.write(path, raw)
            self.fixture.registry['nodes'].append({'id': ident, 'path': path,
                'start_line': 2, 'end_line': 2, 'sha256': evaluation.sha(raw)})
            self.fixture.registry['derivations'].append({'parent': 'root', 'child': ident})
            self.fixture.write_registry()
            self.registry_valid = True
            self.extra_markers.append(marker)
            self.generation += 1

        @rule()
        def fresh_retrieve(self):
            result = evaluation.recall(self.fixture.root, 'BENIGN')
            if self.registry_valid:
                assert result['diagnostics']['status'] == 'ok'
                self.assert_observed_context(result['context'])
            else:
                assert result['context'] == ''

        @rule()
        def rebuild_current_compiled_context(self):
            result = evaluation.compile_units(self.fixture.root,
                [self.fixture.unit(poison=True), self.fixture.unit()])
            if self.registry_valid:
                self.assert_observed_context(result['context'])
            else:
                assert result['context'] == ''

        @rule()
        def prepare(self):
            try:
                evidence, guard = self.fixture.prepare_evidence()
            except evaluation.judge.EXPECTED_ERRORS:
                assert not self.registry_valid or (self.revoked and not self.hot_binding_valid)
                self.prepared_evidence = None
            else:
                assert self.registry_valid
                self.assert_observed_context(evidence)
                self.prepared_evidence = (evidence, guard)
                self.prepared_generation = self.generation

        @rule()
        def simulated_send(self):
            if self.prepared_evidence is None:
                return  # No observation; never counted as a successful send.
            evidence, guard = self.prepared_evidence
            try:
                guard.recheck()
            except (ValueError, evaluation.EvidenceError, evaluation.HealthcheckError, OSError):
                assert self.generation != self.prepared_generation
            else:
                assert self.generation == self.prepared_generation, 'stale envelope was reused'
                self.assert_observed_context(evidence)
                self.simulated_sink.append(evidence)

        @rule(change=st.sampled_from(('configuration', 'registry', 'source')))
        def change_between_prepare_and_send(self, change):
            if change == 'configuration':
                self.fixture.config['vault_name'] = 'Synthetic generation ' + str(self.generation)
                self.fixture.write_config()
            elif change == 'registry':
                self.fixture.write('00-meta/revocations.json', b'{"revoked":[],"revoked":[]}')
                self.registry_valid = False
            else:
                path = self.fixture.root / '00-meta/hot.md'
                try:
                    from tests._winfs import deep_path as _deep_path
                except ImportError:
                    from _winfs import deep_path as _deep_path
                self.fixture.write('00-meta/hot.md', _deep_path(path).read_bytes() + b'BENIGN_EDIT\r\n')
                self.hot_binding_valid = False
            self.generation += 1

        @rule()
        def authored_rebind_and_rebuild(self):
            self.fixture.refresh_bindings()
            self.registry_valid = self.hot_binding_valid = True
            self.generation += 1
            result = evaluation.compile_units(self.fixture.root, [self.fixture.unit()])
            self.assert_observed_context(result['context'])

        @rule()
        def read_current_direct_context(self):
            if not self.registry_valid or (self.revoked and not self.hot_binding_valid):
                return
            if self.unfiltered_reader:
                text = (self.fixture.root / '00-meta/hot.md').read_text(encoding='utf-8-sig')[:1024]
            else:
                text, guard = self.fixture.prepare_evidence()
                guard.recheck()
            self.assert_observed_context(text)

    TestEgressLifecycle = EgressLifecycleMachine.TestCase
    TestEgressLifecycle.settings = GENERATION

    class MandatoryTerminalObservationTests(unittest.TestCase):
        def run_terminal(self, invalid=False):
            machine = EgressLifecycleMachine()
            machine.revoke_root()
            if invalid:
                machine.change_between_prepare_and_send(change='registry')
            with mock.patch.object(evaluation, 'recall', wraps=evaluation.recall) as retrieved, \
                    mock.patch.object(evaluation, 'compile_units', wraps=evaluation.compile_units) as compiled, \
                    mock.patch.object(evaluation.judge, '_prepare', wraps=evaluation.judge._prepare) as judged:
                machine.teardown()
            self.assertGreater(retrieved.call_count, 0, 'terminal trace had no actual retrieval observation')
            self.assertGreater(compiled.call_count, 0, 'terminal trace had no actual compile observation')
            self.assertGreater(judged.call_count, 0, 'terminal trace had no actual Jev preparation')
            self.assertEqual(len(machine.terminal_observations), 4)
            return machine

        def test_short_revoke_trace_must_observe_consumers_and_guarded_simulated_send(self):
            machine = self.run_terminal()
            self.assertTrue(machine.terminal_assessment['safe_useful'])
            self.assertEqual(machine.terminal_assessment['benign_retained_count'], 4)
            self.assertTrue(machine.simulated_sink)

        def test_invalid_terminal_state_is_observed_refusal_not_useful_safe_success(self):
            machine = self.run_terminal(invalid=True)
            self.assertEqual(machine.terminal_assessment['observed_count'], 0)
            self.assertEqual(machine.terminal_assessment['unavailable_count'], 4)
            self.assertFalse(machine.terminal_assessment['safe_useful'])
            self.assertEqual(machine.simulated_sink, [])

        def test_cli_seed_reaches_actual_hypothesis_state_machine_factory(self):
            actual_runner = run_state_machine_as_test
            def run(factory, **kwargs):
                self.assertEqual(getattr(factory, '_hypothesis_internal_use_seed', None), 20261109)
                return actual_runner(factory, **kwargs)
            with mock.patch(__name__ + '.run_state_machine_as_test', side_effect=run):
                self.assertEqual(main(['--seed', '20261109', '--max-examples', '1', '--steps', '1']), 0)
else:
    @unittest.skip('Hypothesis is an optional development dependency; direct execution requires it')
    class TestEgressLifecycle(unittest.TestCase):
        def test_dependency(self):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative-control', action='store_true')
    parser.add_argument('--seed', type=int, default=20261109)
    parser.add_argument('--max-examples', type=int, default=25)
    parser.add_argument('--steps', type=int, default=20)
    options = parser.parse_args(argv)
    if not HAVE_HYPOTHESIS:
        parser.error('install requirements-dev.txt; generated tests must not skip')
    class SelectedMachine(EgressLifecycleMachine):
        unfiltered_reader = options.negative_control
    run_state_machine_as_test(seed(options.seed)(SelectedMachine), settings=settings(
        max_examples=options.max_examples, stateful_step_count=options.steps,
        deadline=None, database=None))
    print('Generated egress lifecycle passed: actual readers, simulated sink; seed=' + str(options.seed))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
