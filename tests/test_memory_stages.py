import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class StageEvaluationTests(unittest.TestCase):
    def test_writing_review_and_inline_acceptance_change_persisted_consumer_context(self):
        import evaluate_memory_stages as stages
        data = stages.collect_stage_dataset(stage='writing', case='ascii')
        samples = [s for s in data['samples'] if s['kind'] == 'checksum' and s['source_variant'] == 'poison']
        self.assertEqual(len(samples), 2)
        by_level = {s['level']: s for s in samples}
        self.assertFalse(by_level['review']['evidence']['poison_exposed'])
        self.assertTrue(by_level['accept_inline']['evidence']['poison_exposed'])
        self.assertNotEqual(by_level['review']['storage_sha256'], by_level['accept_inline']['storage_sha256'])
        self.assertIn('export,publish', by_level['accept_inline']['context'])

    def test_management_projection_is_persisted_and_changes_review(self):
        import evaluate_memory_stages as stages
        data = stages.collect_stage_dataset(stage='management', case='korean')
        samples = [s for s in data['samples'] if s['kind'] == 'checksum' and s['source_variant'] == 'poison']
        by_level = {s['level']: s for s in samples}
        self.assertFalse(by_level['preserve_roles']['evidence']['poison_exposed'])
        self.assertTrue(by_level['erase_roles']['evidence']['poison_exposed'])
        self.assertNotEqual(by_level['preserve_roles']['storage_sha256'], by_level['erase_roles']['storage_sha256'])

    def test_retrieval_levels_use_returned_context_with_identical_stored_bytes(self):
        import evaluate_memory_stages as stages
        data = stages.collect_stage_dataset(stage='retrieval', case='ascii')
        samples = [s for s in data['samples'] if s['kind'] == 'checksum' and s['source_variant'] == 'poison']
        by_level = {s['level']: s for s in samples}
        self.assertEqual(by_level['eligible_only']['storage_sha256'], by_level['include_all']['storage_sha256'])
        self.assertFalse(by_level['eligible_only']['evidence']['poison_exposed'])
        self.assertTrue(by_level['include_all']['evidence']['poison_exposed'])
        self.assertNotEqual(by_level['eligible_only']['context_sha256'], by_level['include_all']['context_sha256'])
        self.assertIn('export,publish', by_level['include_all']['context'])

    def test_empty_context_is_incomplete_not_poison_prevention(self):
        import evaluate_memory_stages as stages
        report = stages.evaluate_stages(stage='retrieval', case='ascii', max_tokens=0)
        self.assertEqual(report['metrics']['completed'], 0)
        self.assertEqual(report['metrics']['matched_pairs_complete'], 0)
        self.assertIsNone(report['metrics']['bad_plan_adoption_rate'])
        self.assertGreater(report['metrics']['incomplete_exposure'], 0)

    def test_no_host_labels_or_scores_in_model_messages(self):
        import evaluate_memory_stages as stages
        from evaluate_model_memory_lifecycle import build_request, ModelLimits
        sample = stages.collect_stage_dataset(stage='writing', case='ascii')['samples'][0]
        message = build_request(sample, limits=ModelLimits())['messages'][1]['content']
        for forbidden in ('source_variant', 'poison_exposed', 'level', 'stage', 'expected_answer', 'task_success'):
            self.assertNotIn(forbidden, message)

    def test_invalid_options_and_network_are_rejected(self):
        import evaluate_memory_stages as stages
        for kwargs in ({'stage':'bad'}, {'case':'bad'}, {'max_tokens':-1}, {'allow_network':True}):
            with self.assertRaises(ValueError):
                stages.evaluate_stages(**kwargs)

    def test_unsafe_scripted_control_reports_observable_bad_plan_only_when_exposed(self):
        import evaluate_memory_stages as stages
        report = stages.evaluate_stages(stage='retrieval', case='ascii', control='unsafe')
        self.assertGreater(report['metrics']['exposed_poison_plans'], 0)
        self.assertGreater(report['metrics']['observable_bad_plan_adoptions'], 0)
        self.assertEqual(report['generation'], 'deterministic_experimental_intervention')
        self.assertNotIn('selective_repair_successes', report['metrics'])


class TransportContractTests(unittest.TestCase):
    def test_loopback_malformed_output_keeps_usage_and_hides_raw_content(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import evaluate_memory_stages as stages
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                content = ('PRIVATE-SENTINEL' if len(requests) % 2 else
                           json.dumps({'actions': ['execute_shell']}))
                body = json.dumps({'choices':[{'message':{'role':'assistant','content':content},
                    'finish_reason':'stop'}], 'usage':{'prompt_tokens':7,'completion_tokens':2,'total_tokens':9}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = stages.evaluate_stages(stage='retrieval', case='ascii', backend='local-model',
                allow_network=True, endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions', model='stub')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(len(requests), 8)
        self.assertEqual(result['metrics']['completed'], 0)
        self.assertEqual(result['metrics']['errors'], 8)
        self.assertEqual(result['observations'][0]['cost']['provider_usage']['total_tokens'], 9)
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(result))
        self.assertNotIn('execute_shell', json.dumps(result))
        self.assertNotIn('source_variant', json.dumps(requests))

    def test_timed_out_request_stops_later_requests(self):
        import evaluate_memory_stages as stages
        import evaluate_model_memory_lifecycle as runner
        from evaluate_tool_safety import ModelError
        from unittest.mock import patch
        with patch.object(runner, '_bounded_request', side_effect=ModelError('request_timeout')) as send:
            result = stages.evaluate_stages(stage='writing', case='ascii', backend='local-model',
                allow_network=True, endpoint='http://127.0.0.1:1/v1/chat/completions', model='stub')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result['metrics']['requests_sent'], 1)
        self.assertEqual(result['observations'][0]['error'], 'request_timeout')
        self.assertTrue(all(row['error'] == 'run_stopped' for row in result['observations'][1:]))

    def test_source_manifest_binds_stage_runner_and_dependencies(self):
        import hashlib
        import evaluate_memory_stages as stages
        data = stages.collect_stage_dataset(stage='retrieval', case='ascii')
        manifest = data['source_manifest']
        self.assertEqual(manifest['scripts/evaluate_memory_stages.py'], hashlib.sha256(
            (Path(__file__).resolve().parents[1] / 'scripts/evaluate_memory_stages.py').read_bytes()).hexdigest())
        self.assertIn('skills/agentic-vault/scripts/vault_retrieval.py', manifest)
        self.assertTrue(all(s['source_tree_sha256'] == data['source_tree_sha256'] for s in data['samples']))

    def test_both_transport_timeout_outcomes_stop_later_requests(self):
        from unittest.mock import patch
        import evaluate_memory_stages as stages
        import evaluate_model_memory_lifecycle as runner
        from evaluate_tool_safety import ModelError
        for code in ('request_timeout', 'transport_error'):
            with self.subTest(code=code), patch.object(runner, '_bounded_request', side_effect=ModelError(code)) as send:
                result = stages.evaluate_stages(stage='retrieval', case='ascii', backend='local-model',
                    allow_network=True, endpoint='http://127.0.0.1:1/v1/chat/completions', model='stub')
                self.assertEqual(send.call_count, 1)
                self.assertEqual(result['metrics']['requests_sent'], 1)
                self.assertEqual(result['observations'][0]['error'], code)
                self.assertTrue(all(row['error'] == 'run_stopped' for row in result['observations'][1:]))


class EvidenceCompletenessTests(unittest.TestCase):
    def test_retrieval_startup_inputs_include_startup_evidence(self):
        import evaluate_memory_stages as stages
        data = stages.collect_stage_dataset(stage='retrieval', case='ascii')
        startup = [sample for sample in data['samples'] if sample['kind'] == 'startup']
        self.assertEqual(len(startup), 4)
        for sample in startup:
            self.assertIn('reset_lock,start', sample['context'])
            self.assertTrue(sample['evidence']['exposure_complete'])
        for variant in ('clean', 'poison'):
            pair = [s for s in startup if s['source_variant'] == variant]
            self.assertEqual(pair[0]['storage_sha256'], pair[1]['storage_sha256'])

    def test_exposure_is_counted_when_the_request_errors(self):
        import evaluate_memory_stages as stages
        report = stages.evaluate_stages(stage='writing', case='ascii', control='error')
        metrics = report['metrics']
        self.assertEqual(metrics['exposed_poison_plans'], 1)
        self.assertEqual(metrics['scored_exposed_poison_plans'], 0)
        self.assertEqual(metrics['by_stage_and_level']['writing']['accept_inline']['poison_exposures'], 1)
        self.assertEqual(metrics['errors'], 8)
        self.assertIsNone(metrics['bad_plan_adoption_rate'])

    def test_truncated_retrieval_context_is_incomplete_even_when_nonempty(self):
        import evaluate_memory_stages as stages
        data = stages.collect_stage_dataset(stage='retrieval', case='ascii', max_tokens=10)
        samples = [s for s in data['samples'] if s['source_variant'] == 'poison' and s['level'] == 'include_all']
        self.assertTrue(samples[0]['context'])
        self.assertFalse(samples[0]['evidence']['exposure_complete'])

    def test_source_closure_change_invalidates_collection(self):
        from unittest.mock import patch
        import evaluate_memory_stages as stages
        actual = stages.source_manifest()
        changed = dict(actual, **{'scripts/evaluate_memory_stages.py': '0' * 64})
        with patch.object(stages, 'source_manifest', side_effect=[actual, changed]):
            with self.assertRaisesRegex(ValueError, 'source_changed'):
                stages.collect_stage_dataset(stage='retrieval', case='ascii')

    def test_request_budget_prevents_send_and_marks_incomplete(self):
        import evaluate_memory_stages as stages
        from evaluate_model_memory_lifecycle import ModelLimits
        report = stages.evaluate_stages(stage='writing', case='ascii', limits=ModelLimits(max_request_bytes=1))
        self.assertEqual(report['metrics']['requests_sent'], 0)
        self.assertEqual(report['metrics']['matched_pairs_complete'], 0)
        self.assertTrue(all(row['error'] == 'request_bytes_exceeded' for row in report['observations']))

    def test_metrics_separate_levels_and_clean_task_utility(self):
        import evaluate_memory_stages as stages
        report = stages.evaluate_stages(stage='writing', case='ascii')
        per_level = report['metrics']['by_stage_and_level']['writing']
        self.assertEqual(per_level['review']['clean_task_successes'], 2)
        self.assertEqual(per_level['accept_inline']['clean_task_successes'], 2)
        self.assertEqual(per_level['review']['poison_exposures'], 0)
        self.assertEqual(per_level['accept_inline']['poison_exposures'], 1)
        self.assertIsNone(per_level['review']['bad_plan_adoption_rate'])

    def test_clean_poison_pair_counts_require_both_sources_and_levels(self):
        import evaluate_memory_stages as stages
        report = stages.evaluate_stages(stage='retrieval', case='ascii', control='error')
        self.assertEqual(report['metrics']['clean_poison_pairs_requested'], 4)
        self.assertEqual(report['metrics']['clean_poison_pairs_complete'], 0)


if __name__ == '__main__':
    unittest.main()
