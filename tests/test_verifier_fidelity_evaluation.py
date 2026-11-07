"""Mutation fidelity of the literal preservation check, measured on synthetic notes.

The fixed table below is the observed behaviour of check_preservation and
preserve-check on the bundled notes. It pins what the checker catches and what
it does not; it is not a claim about real notes.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_verifier_fidelity.py'
DOC = ROOT / 'docs/verifier-fidelity.md'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(module)
    return module


# category: (trials, library flagged, preserve-check flagged, propose blocked, declared flagged or None)
EXPECTED = {
    'value_change': (36, 36, 36, 0, None),
    'value_change_behind_broken_fence': (12, 12, 12, 0, None),
    'value_change_unextracted': (12, 0, 0, 0, 12),
    'unit_change_listed': (14, 14, 14, 0, None),
    'unit_change_unlisted': (10, 0, 0, 0, 10),
    'swap': (6, 0, 0, 0, 6),
    # The miss: a declared sentence still matches after 'Do not' is put in front of it.
    'negation_insertion': (12, 0, 0, 0, 11),
    'fact_free_paragraph_deletion': (18, 0, 0, 0, 18),
    'tail_truncation_with_facts': (18, 18, 18, 0, None),
    'tail_truncation_fact_free': (18, 0, 0, 0, None),
    'frontmatter_destruction': (66, 0, 0, 42, None),
    'frontmatter_destruction_exempt_target': (66, 0, 0, 0, None),
    'rewrite_literals_kept': (30, 0, 0, 0, None),
    # The two clear cases: 5kg to 5KG, and a label rewritten around the same name.
    'rewrite_literals_reformatted': (18, 16, 16, 0, None),
}


class EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev = load(SCRIPT, 'verifier_fidelity_eval') if SCRIPT.exists() else None
        cls.report = cls.ev.evaluate() if cls.ev else None
        cls.rows = {row['category']: row for row in cls.report['rows']} if cls.report else {}

    def setUp(self):
        self.assertIsNotNone(self.ev, 'evaluate_verifier_fidelity.py has not been implemented')

    def test_fixed_table_of_observed_behaviour(self):
        observed = {}
        for name, row in self.rows.items():
            declared = row['declared']['flagged'] if row['declared'] else None
            observed[name] = (row['trials'], row['library']['flagged'], row['preserve_check']['flagged'],
                              row['preserve_check']['blocked'], declared)
        self.assertEqual(observed, EXPECTED)

    def test_scope_is_reported_per_row_and_never_pooled_across_scopes(self):
        scopes = {name: row['scope'] for name, row in self.rows.items()}
        self.assertEqual({name for name, scope in scopes.items() if scope == 'in_scope'},
                         {'value_change', 'value_change_behind_broken_fence', 'unit_change_listed',
                          'tail_truncation_with_facts'})
        self.assertEqual({name for name, scope in scopes.items() if scope == 'false_alarm'},
                         {'rewrite_literals_kept', 'rewrite_literals_reformatted'})
        self.assertEqual(scopes['frontmatter_destruction'], 'out_of_scope')
        self.assertEqual(set(self.report['summary']), {'in_scope', 'out_of_scope', 'false_alarm'})
        for scope, block in self.report['summary'].items():
            self.assertEqual(block['trials'], sum(row['trials'] for row in self.rows.values()
                                                  if row['scope'] == scope))
        self.assertTrue(all(row['scope_reason'] for row in self.rows.values() if row['scope'] != 'in_scope'))

    def test_every_rate_carries_the_exact_bound_from_eval_stats(self):
        stats = load(ROOT / 'scripts/eval_stats.py', 'fidelity_eval_stats')
        for name, row in self.rows.items():
            for column in ('library', 'preserve_check'):
                data = row[column]
                missed = data['flagged'] if row['scope'] == 'false_alarm' else data['survived']
                with self.subTest(name=name, column=column):
                    self.assertEqual(data['events'], missed)
                    self.assertEqual(data['event_rate_upper_95'],
                                     stats.clopper_pearson_upper_bound(missed, row['trials'], confidence=0.95))
                    self.assertEqual(data['flagged'] + data['survived'] + data['blocked'] + data['unmeasured'],
                                     row['trials'])

    def test_only_notes_that_pass_unchanged_are_mutated(self):
        self.assertEqual(self.report['notes']['measured'], {'en': 3, 'ko': 3})
        excluded = self.report['excluded_notes']
        self.assertEqual(len(excluded), 2)
        for row in excluded:
            self.assertEqual(row['review_reasons'], ['no_selected_literal_facts'])
            self.assertEqual(row['mutants_flagged'], 0, 'unable to measure is not a catch')
            self.assertEqual(row['mutants_complete'], 0, 'a declaration must not complete 0 of 0')
            self.assertGreater(row['mutants_unmeasured'], 0)

    def test_unchanged_note_never_counts_as_a_false_alarm(self):
        for name, row in self.rows.items():
            self.assertEqual(row['library']['unmeasured'], 0, name)

    def test_every_mutant_changes_the_note(self):
        for note in self.ev.NOTES:
            original = self.ev.render(note)
            for mutant in self.ev.mutants(note):
                self.assertNotEqual(mutant['text'], original, mutant['id'])

    def test_mutants_have_unique_stable_ids(self):
        ids = [mutant['id'] for note in self.ev.NOTES for mutant in self.ev.mutants(note)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_report_is_deterministic_and_has_no_clock_or_paths(self):
        again = self.ev.evaluate()
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.report, sort_keys=True))
        text = json.dumps(self.report)
        self.assertNotIn(str(ROOT), text)
        self.assertNotIn('seconds', text)

    def test_controls_prove_the_table_can_move(self):
        silent = self.ev.evaluate(control='always_complete')
        for row in silent['rows']:
            self.assertEqual(row['library']['flagged'], 0, row['category'])
        self.assertGreater(silent['summary']['in_scope']['library']['events'], 0)
        loud = self.ev.evaluate(control='always_review')
        self.assertEqual(loud['notes']['measured'], {'en': 0, 'ko': 0})
        self.assertTrue(all(row['trials'] == 0 and row['library']['event_rate'] is None for row in loud['rows']))

    def test_authority_says_this_is_not_safety_evidence(self):
        self.assertEqual(self.report['authority'], 'synthetic_measurement_not_safety_evidence')
        self.assertEqual(self.report['model_calls'], 0)

    def test_frontmatter_cannot_be_declared_as_a_claim(self):
        note = self.ev.NOTES[0]
        quality = self.ev.load_quality()
        with self.assertRaisesRegex(quality.QualityError, 'claim_not_in_original'):
            quality.check_preservation(self.ev.render(note), self.ev.render(note),
                                       required_claims=['title: ' + note['title']])

    def test_cli_json_survives_a_cp949_pipe(self):
        environment = {**os.environ, 'PYTHONIOENCODING': 'cp949', 'PYTHONUTF8': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, str(SCRIPT), '--json'], capture_output=True, env=environment,
                              timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.decode('ascii')), self.report)

    def test_cli_table_shows_every_row_and_rejects_bad_arguments_without_echo(self):
        done = subprocess.run([sys.executable, '-B', str(SCRIPT)], capture_output=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        table = done.stdout.decode('utf-8')
        for name in EXPECTED:
            self.assertIn(name, table)
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--nope-secret'], capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 2)
        self.assertNotIn(b'nope-secret', done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout), {'ok': False, 'error': 'invalid_arguments'})


class DocumentTests(unittest.TestCase):
    def test_document_table_matches_the_measurement(self):
        ev = load(SCRIPT, 'verifier_fidelity_eval_doc')
        report = ev.evaluate()
        text = DOC.read_text(encoding='utf-8')
        cells = {}
        for line in text.splitlines():
            match = re.match(r'\| `([a-z_]+)` \| (\w+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|', line)
            if match:
                cells[match.group(1)] = (match.group(2), *map(int, match.groups()[2:]))
        self.assertEqual(cells, {row['category']: (row['scope'], row['trials'], row['library']['flagged'],
                                                   row['preserve_check']['flagged'],
                                                   row['preserve_check']['blocked'])
                                 for row in report['rows']})

    def test_document_states_the_limits(self):
        text = ' '.join(DOC.read_text(encoding='utf-8').split())
        self.assertIn('not evidence of real-world safety', text)
        self.assertIn('https://arxiv.org/abs/2607.19865', text)
        self.assertIn('reprotest', text)


if __name__ == '__main__':
    unittest.main()
