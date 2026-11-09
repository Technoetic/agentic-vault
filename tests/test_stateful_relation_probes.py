"""Generated synthetic record edits with an independently maintained state oracle.

No network, models, company files or persistent database are used. Ordinary
discovery may skip without the optional dev dependency; the direct command
requires Hypothesis and exits nonzero if it is absent::

    python tests/test_stateful_relation_probes.py
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

try:
    from hypothesis import settings, strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
except ModuleNotFoundError as exc:
    if exc.name != 'hypothesis':
        raise
    HAVE_HYPOTHESIS = False
else:
    HAVE_HYPOTHESIS = True

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from vault_relation_probes import check_relation_records
from vault_compile_quality import check_preservation


if HAVE_HYPOTHESIS:
    class RelationRecordMachine(RuleBasedStateMachine):
        """A restored original may pass; missing/changed/copied records may not."""
        def __init__(self):
            super().__init__()
            self.original = ('Caf\u00e9 A quantity 10 Pa.\nExcept emergency.\n\n'
                             'Caf\u00e9 B quantity 20 Pa.\nExcept emergency.\n')
            self.probes = [{'id': key, 'anchor': 'Caf\u00e9 ' + key, 'scope': 'paragraph'} for key in ('A', 'B')]
            self.states = {'A': 'original', 'B': 'original'}
            self.order = ['A', 'B']
            self.decomposed = False
            self.crlf = False
            self.frontmatter = False

        @rule(key=st.sampled_from(('A', 'B')),
              state=st.sampled_from(('original', 'swapped', 'negated', 'exception_removed', 'missing', 'copied')))
        def edit_record(self, key, state):
            self.states[key] = state

        @rule()
        def reorder_records(self):
            self.order.reverse()

        @rule(decomposed=st.booleans(), crlf=st.booleans(), frontmatter=st.booleans())
        def presentation(self, decomposed, crlf, frontmatter):
            self.decomposed, self.crlf, self.frontmatter = decomposed, crlf, frontmatter

        @invariant()
        def observe_current_records_and_preservation_wrapper(self):
            parts = []
            for key in self.order:
                state = self.states[key]
                if state == 'missing':
                    continue
                quantity = {'A': 10, 'B': 20}[key]
                if state == 'swapped':
                    quantity = {'A': 20, 'B': 10}[key]
                record = f'Caf\u00e9 {key} quantity {quantity} Pa.'
                if state == 'negated':
                    record += ' Not required.'
                if state != 'exception_removed':
                    record += '\nExcept emergency.'
                parts.append(record)
                if state == 'copied':
                    parts.append(record)
            candidate = '\n\n'.join(parts) + '\n'
            if self.frontmatter:
                candidate = '\ufeff---\ntitle: Caf\u00e9 A and Caf\u00e9 B\n---\n' + candidate
            if self.decomposed:
                candidate = candidate.replace('\u00e9', 'e\u0301')
            if self.crlf:
                candidate = candidate.replace('\n', '\r\n')
            observed = check_relation_records(self.original, candidate, self.probes)
            expected_reasons = {'original': None, 'missing': 'candidate_missing', 'copied': 'candidate_ambiguous',
                                'swapped': 'record_changed', 'negated': 'record_changed',
                                'exception_removed': 'record_changed'}
            for row in observed['relation_rows']:
                state = self.states[row['id']]
                assert row['reason'] == expected_reasons[state]
                assert row['preserved'] is (state == 'original')
                assert row['original_count'] == 1
                assert row['candidate_count'] == {'missing': 0, 'copied': 2}.get(state, 1)
            assert observed['status'] == ('complete' if all(state == 'original' for state in self.states.values())
                                          else 'needs_review')
            wrapped = check_preservation(self.original, candidate, relation_probes=self.probes)
            assert wrapped['relation_rows'] == observed['relation_rows']
            if observed['status'] == 'needs_review':
                assert wrapped['status'] == 'needs_review'

    TestRelationRecordLifecycle = RelationRecordMachine.TestCase
    TestRelationRecordLifecycle.settings = settings(max_examples=30, stateful_step_count=30, deadline=None,
                                                   database=None, derandomize=True)
else:
    @unittest.skip('Hypothesis optional dev dependency is not installed')
    class TestRelationRecordLifecycle(unittest.TestCase):
        def test_requires_hypothesis(self):
            pass


if __name__ == '__main__':
    if not HAVE_HYPOTHESIS:
        raise SystemExit('Hypothesis is required for the direct stateful test command')
    unittest.main()
