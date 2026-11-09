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
            self.envelope = None
            self.prepared_generation = None
            self.simulated_sink = []
            self.extra_markers = []
            self.derived_count = 0

        def teardown(self):
            self.temp.cleanup()

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
                envelope = evaluation.bridge._generation_envelope(self.fixture.root,
                    {'_include_activity': True}, 'BENIGN')
            except (ValueError, evaluation.EvidenceError, evaluation.HealthcheckError, OSError):
                assert not self.registry_valid or (self.revoked and not self.hot_binding_valid)
                self.envelope = None
            else:
                assert self.registry_valid
                self.assert_observed_context(envelope.payload)
                self.envelope = envelope
                self.prepared_generation = self.generation

        @rule()
        def simulated_send(self):
            if self.envelope is None:
                return  # No observation; never counted as a successful send.
            try:
                self.envelope.recheck()
            except (ValueError, evaluation.EvidenceError, evaluation.HealthcheckError, OSError):
                assert self.generation != self.prepared_generation
            else:
                assert self.generation == self.prepared_generation, 'stale envelope was reused'
                self.assert_observed_context(self.envelope.payload)
                self.simulated_sink.append(self.envelope.payload)

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
                self.fixture.write('00-meta/hot.md', path.read_bytes() + b'BENIGN_EDIT\r\n')
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
                text, _ = evaluation.bridge._read_context_prefix(self.fixture.root / '00-meta/hot.md')
            else:
                text = evaluation.bridge._generation_prompt(self.fixture.root, {}, 'BENIGN')
            self.assert_observed_context(text)

    TestEgressLifecycle = EgressLifecycleMachine.TestCase
    TestEgressLifecycle.settings = GENERATION
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
    run = lambda: run_state_machine_as_test(SelectedMachine, settings=settings(
        max_examples=options.max_examples, stateful_step_count=options.steps,
        deadline=None, database=None))
    seed(options.seed)(run)()
    print('Generated egress lifecycle passed: actual readers, simulated sink; seed=' + str(options.seed))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
