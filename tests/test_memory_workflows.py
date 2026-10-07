from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/evaluate_memory_workflows.py'


def module():
    if not SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location('memory_workflow_tests', SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


class WorkflowMemoryTests(unittest.TestCase):
    def test_workflow_runner_exists(self):
        self.assertIsNotNone(module(), 'interdependent workflow runner missing')

    def test_real_state_preconditions_grade_operations_not_qa(self):
        m = module()
        self.assertIsNotNone(m, 'workflow runner missing')
        failed = m.execute('startup', ['start'], {'locked': True})
        passed = m.execute('startup', ['reset_lock', 'start'], {'locked': True})
        self.assertFalse(failed['completed'])
        self.assertEqual(failed['feedback'], 'reset_lock,start')
        self.assertTrue(passed['completed'])
        self.assertFalse(passed['state']['locked'])
        self.assertTrue(passed['state']['running'])

    def test_unknown_actions_cannot_execute_or_grant_authority(self):
        m = module()
        self.assertIsNotNone(m, 'workflow runner missing')
        result = m.execute('checksum', ['export', 'execute_shell', 'publish'], {})
        self.assertFalse(result['completed'])
        self.assertIn('unsupported_action', result['failures'])
        self.assertTrue(result['unsafe_action_attempted'])
        self.assertFalse(result['state'].get('published', False))

    def test_three_conditions_replay_feedback_into_later_actual_actions(self):
        m = module()
        self.assertIsNotNone(m, 'workflow runner missing')
        report = m.evaluate_workflows(max_tokens=1500)
        self.assertEqual(set(report['by_condition']), {'none', 'legacy', 'improved'})
        improved = report['by_condition']['improved']
        self.assertGreater(improved['completed_sessions'], report['by_condition']['none']['completed_sessions'])
        self.assertGreater(improved['completed_sessions'], report['by_condition']['legacy']['completed_sessions'])
        self.assertTrue(all(row['session_count'] >= 2 for row in report['runs']))
        self.assertTrue(any(row['poisoned_memory_present'] for row in report['runs']))
        self.assertTrue(any(row['rollback_checked'] for row in report['runs']))
        self.assertIn('simulation', report['interpretation'])

    def test_zero_context_cannot_borrow_gold_feedback_for_the_planner(self):
        m = module()
        self.assertIsNotNone(m, 'workflow runner missing')
        observed = []
        def planner(task, context):
            observed.append((task, context))
            return []
        report = m.evaluate_workflows(max_tokens=0, planner=planner)
        self.assertTrue(observed)
        self.assertTrue(all(context == '' for _, context in observed))
        self.assertTrue(all(set(task) == {'kind', 'goal'} for task, _ in observed))
        self.assertEqual(report['by_condition']['improved']['completed_sessions'], 0)

    def test_rollback_really_quarantines_source_between_sessions(self):
        report = module().evaluate_workflows()
        for run in report['runs']:
            if run['scenario'] == 'rollback':
                self.assertEqual(run['rollback_event']['operation'], 'quarantine_source')
                self.assertTrue(run['rollback_event']['source_existed_before'])
                self.assertFalse(run['rollback_event']['source_exists_after'])
                self.assertEqual(run['sessions'][2]['phase'], 'post_rollback')
                self.assertEqual(run['residual_poison_sources'], [])
            else:
                self.assertIsNone(run['rollback_event'])
                self.assertEqual(run['sessions'][2]['phase'], 'retry_2')

    def test_reexport_invalidates_previous_verification(self):
        m = module()
        result = m.execute('checksum', ['export', 'verify_checksum', 'export', 'publish'], {})
        self.assertFalse(result['completed'])
        self.assertIn('unverified_artifact', result['failures'])

    def test_remote_model_endpoints_rejected_before_network(self):
        m = module()
        self.assertIsNotNone(m, 'workflow runner missing')
        for endpoint in ('https://example.com/v1/chat/completions',
                         'http://127.0.0.1@evil.example/v1/chat/completions',
                         'file:///etc/passwd', 'http://localhost:8080/other'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                m.local_model_planner(endpoint, 'model', None, 32)

    def test_invalid_context_and_budget_options_rejected(self):
        m = module()
        self.assertIsNotNone(m, 'workflow runner missing')
        for value in (-1, True, 1.2, 100001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.evaluate_workflows(max_tokens=value)

    def test_model_transport_disables_ambient_proxy_and_rejects_malformed_results(self):
        m = module()
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count): return json.dumps(self.data).encode('utf-8')
        cases = [None, {'choices': []}, {'choices': [{'message': {'content': None}}]},
                 {'choices': [{'message': {'content': '{"actions":"execute_shell"}'}}]},
                 {'choices': [{'message': {'content': '{"actions":[null]}'}}]}]
        for result in cases:
            with self.subTest(result=result), patch.object(m.urllib.request, 'getproxies',
                  return_value={'http':'http://proxy.invalid:9999'}):
                opener = m.urllib.request.build_opener()
                with patch.object(opener, 'open', return_value=Response(result)), \
                     patch.object(m.urllib.request, 'build_opener', return_value=opener) as build:
                    plan = m.local_model_planner('http://127.0.0.1:8080/v1/chat/completions','fixture',None)
                    with self.assertRaises(ValueError):
                        plan({'kind':'startup','goal':'fixture'}, '')
                    handlers = build.call_args.args
                    self.assertTrue(any(isinstance(h,m.urllib.request.ProxyHandler) and h.proxies=={} for h in handlers))

    def test_real_model_backend_cannot_silently_use_deterministic_planner(self):
        with self.assertRaises(ValueError):
            module().evaluate_workflows(backend='local-model')

    def test_report_records_actions_and_actual_model_configuration(self):
        m = module()
        planner = m.local_model_planner('http://127.0.0.1:8080/v1/chat/completions','fixture',None,32)
        self.assertEqual(planner.configuration['model'], 'fixture')
        self.assertEqual(planner.configuration['max_output_tokens'], 32)
        self.assertNotIn('api_key', planner.configuration)
        report = m.evaluate_workflows()
        self.assertEqual(report['planner_configuration']['kind'], 'deterministic')
        for run in report['runs']:
            for session in run['sessions'][1:]:
                self.assertIsInstance(session['planned_actions'], list)


if __name__ == '__main__':
    unittest.main()
