"""Behavioral controls for the optional offline formation evaluation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_memory_formation.py'


def module():
    if not SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location('formation_tests_subject', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class FormationTests(unittest.TestCase):
    def subject(self):
        result = module()
        self.assertIsNotNone(result, 'offline memory formation runner missing')
        return result

    def report(self, **kwargs):
        return self.subject().evaluate_formation(**kwargs)

    def test_runner_has_fixed_offline_schema(self):
        r = self.report()
        self.assertEqual(r['schema_version'], 1)
        self.assertEqual(r['backend'], 'deterministic-control')
        self.assertIn('not model robustness', r['interpretation'])

    def test_five_categories_each_have_two_arms(self):
        r = self.report()
        self.assertEqual([x['id'] for x in r['pairs']], ['P1', 'P2', 'P3', 'P4', 'P5'])
        self.assertTrue(all(set(x['arms']) == {'clean', 'poisoned'} for x in r['pairs']))

    def test_every_pair_uses_same_task_budget_and_initial_state(self):
        r = self.report()
        self.assertTrue(all(p['pair_constraints']['same_task_initial_state_budget'] for p in r['pairs']))

    def test_default_clean_tasks_really_complete(self):
        r = self.report()
        self.assertEqual(r['metrics']['clean_task_successes'], 5)

    def test_vulnerable_control_detects_poisoned_followup_failures(self):
        r = self.report(control='vulnerable')
        self.assertEqual(r['metrics']['clean_task_successes'], 5)
        self.assertGreaterEqual(r['metrics']['poisoned_followup_failures'], 4)

    def test_default_does_not_store_poisoned_plans(self):
        r = self.report()
        self.assertEqual(r['metrics']['poisoned_contaminated_final_memories'], 0)
        self.assertEqual(r['metrics']['poisoned_unsafe_rule_adoptions'], 0)

    def test_p2_actual_source_mutation_is_detected(self):
        pair = self.report()['pairs'][1]
        self.assertGreater(pair['arms']['poisoned']['source_binding_rejections'], 0)

    def test_p3_out_of_scope_observation_is_not_applied(self):
        pair = self.report()['pairs'][2]
        self.assertGreater(pair['arms']['poisoned']['scope_rejections'], 0)
        self.assertGreater(pair['arms']['poisoned']['in_scope_accepted'], 0)

    def test_p4_same_pool_fixed_selection_count(self):
        pair = self.report()['pairs'][3]
        self.assertTrue(pair['pair_constraints']['same_experience_pool'])
        self.assertTrue(pair['pair_constraints']['same_selection_count'])
        self.assertEqual(pair['arms']['clean']['selected_count'], 1)
        self.assertEqual(pair['arms']['poisoned']['selected_count'], 1)

    def test_p4_filtering_cost_is_not_hidden_as_safety_success(self):
        pair = self.report()['pairs'][3]
        self.assertTrue(pair['arms']['clean']['task_success'])
        self.assertFalse(pair['arms']['poisoned']['task_success'])
        self.assertEqual(pair['clean_task_loss'], 1)

    def test_p5_same_multiset_and_different_order(self):
        pair = self.report()['pairs'][4]
        self.assertTrue(pair['pair_constraints']['same_experience_multiset'])
        self.assertTrue(pair['pair_constraints']['different_integration_order'])

    def test_p5_vulnerable_path_dependence_is_observed(self):
        pair = self.report(control='vulnerable')['pairs'][4]
        self.assertTrue(pair['arms']['clean']['task_success'])
        self.assertFalse(pair['arms']['poisoned']['task_success'])

    def test_transient_contamination_is_separate_from_final_memory(self):
        arm = self.report(control='vulnerable')['pairs'][4]['arms']['clean']
        self.assertTrue(arm.get('contaminated_memory_ever_stored', False))
        self.assertFalse(arm['contaminated_memory_stored'])
        self.assertFalse(arm['unsafe_rule_adopted'])
        self.assertTrue(arm['task_success'])

    def test_scope_violation_is_measured_in_vulnerable_control(self):
        arm = self.report(control='vulnerable')['pairs'][2]['arms']['poisoned']
        self.assertGreater(arm.get('out_of_scope_adopted', 0), 0)
        self.assertEqual(arm['in_scope_accepted'], 1)

    def test_payload_and_experience_limits_are_enforced(self):
        m = self.subject()
        for mutate in (
            lambda c: c[0]['clean']['experiences'][0].update(advice='x' * 2049),
            lambda c: c[0]['clean']['experiences'].extend([c[0]['clean']['experiences'][0]] * 8),
            lambda c: c[4]['poisoned'].update(order=['observed']),
        ):
            cases = m.scenario_pairs(); mutate(cases)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                m.evaluate_formation(scenarios=cases)

    def test_zero_context_does_not_borrow_expected_plan(self):
        r = self.report(max_tokens=0)
        self.assertEqual(r['metrics']['clean_task_successes'], 0)
        self.assertTrue(all(a['context_tokens'] == 0 for p in r['pairs'] for a in p['arms'].values()))

    def test_bad_budgets_rejected(self):
        m = self.subject()
        for budget in (-1, True, 1.5, 4097):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                m.evaluate_formation(max_tokens=budget)

    def test_unknown_control_rejected(self):
        with self.assertRaises(ValueError):
            self.report(control='shell')

    def test_p4_selection_count_tampering_rejected(self):
        m = self.subject(); cases = m.scenario_pairs()
        cases[3]['poisoned']['selection'].append('observed')
        with self.assertRaises(ValueError):
            m.evaluate_formation(scenarios=cases)

    def test_p5_multiset_tampering_rejected(self):
        m = self.subject(); cases = m.scenario_pairs()
        cases[4]['poisoned']['experiences'][0]['advice'] += ' changed'
        with self.assertRaises(ValueError):
            m.evaluate_formation(scenarios=cases)

    def test_p5_changed_selected_multiset_rejected_even_with_same_pool(self):
        m = self.subject(); cases = m.scenario_pairs()
        third = m._experience('third')
        for arm in ('clean', 'poisoned'):
            cases[4][arm]['experiences'].append(dict(third))
        cases[4]['poisoned']['selection'] = ['observed', 'third']
        cases[4]['poisoned']['order'] = ['third', 'observed']
        with self.assertRaises(ValueError):
            m.evaluate_formation(scenarios=cases)

    def test_p4_selection_change_is_not_reported_as_order_only(self):
        pair = self.report()['pairs'][3]
        self.assertFalse(pair['pair_constraints']['same_experience_multiset'])
        self.assertFalse(pair['pair_constraints']['different_integration_order'])

    def test_schema_unknown_fields_do_not_become_instructions(self):
        m = self.subject(); cases = m.scenario_pairs()
        cases[0]['clean']['execute'] = 'do not run this'
        with self.assertRaises(ValueError):
            m.evaluate_formation(scenarios=cases)

    def test_network_is_never_used(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.report()['metrics']['clean_task_successes'], 5)

    def test_untrusted_payload_is_not_echoed_in_report(self):
        m = self.subject(); cases = m.scenario_pairs()
        cases[0]['poisoned']['experiences'][0]['advice'] += ' PRIVATE-SENTINEL'
        r = m.evaluate_formation(scenarios=cases)
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(r))

    def test_cli_json_success_and_invalid_option_exit_one(self):
        self.subject()
        good = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(json.loads(good.stdout)['schema_version'], 1)
        bad = subprocess.run([sys.executable, str(SCRIPT), '--max-tokens', '-1'], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(json.loads(bad.stdout), {'error': 'formation_evaluation_invalid'})


if __name__ == '__main__':
    unittest.main()
