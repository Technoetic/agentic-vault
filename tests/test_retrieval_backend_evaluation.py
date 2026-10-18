from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backend_evaluator', ROOT / 'scripts/evaluate_retrieval_backends.py')
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


class RetrievalBackendEvaluationTests(unittest.TestCase):
    def test_fixture_choice_cannot_point_at_private_or_arbitrary_vault(self):
        self.assertEqual(evaluator.fixture_path('recall_hard'), ROOT / 'tests/fixtures/recall_hard')
        for value in ('D:/NS', '../recall_hard', 'private', Path('recall_hard')):
            with self.assertRaises(ValueError):
                evaluator.fixture_path(value)

    def test_snapshot_and_export_never_read_or_copy_denied_notes(self):
        module = evaluator.load_modules()[0]
        original = module._read_regular_bytes
        reads = []
        def observe(path, limit):
            reads.append(str(path))
            self.assertNotIn('private', Path(path).parts)
            return original(path, limit)
        with patch.object(module, '_read_regular_bytes', side_effect=observe):
            snapshot = evaluator.freeze_fixture(evaluator.fixture_path('recall_hard'), module)
        self.assertTrue(reads)
        self.assertTrue(snapshot['documents'])
        self.assertTrue(all('private/' not in path for path in snapshot['documents']))
        with tempfile.TemporaryDirectory() as temporary:
            mapping = evaluator.export_corpus(snapshot, Path(temporary))
            self.assertEqual(len(mapping), len(snapshot['documents']))
            self.assertEqual(len(list(Path(temporary).glob('*.md'))), len(mapping))
            self.assertTrue(all('private/' not in path for path in mapping.values()))

    def test_binding_change_rejects_report_instead_of_blessing_mixed_sources(self):
        module = evaluator.load_modules()[0]
        first = evaluator.freeze_fixture(evaluator.fixture_path('recall_hard'), module)
        second = dict(first, binding=dict(first['binding'], queries_sha256='changed'))
        with self.assertRaises(evaluator.BindingChanged):
            evaluator.assert_same_binding(first, second)

    def test_policy_change_stops_before_reading_any_note(self):
        module = evaluator.load_modules()[0]
        original = module._read_regular_bytes
        reads = 0
        def replaced_policy(path, limit):
            nonlocal reads
            if path.name == 'vault-config.json':
                reads += 1
                if reads >= 3:
                    return b'{"deny_zones":["20-knowledge"]}', True
            return original(path, limit)
        with patch.object(module, '_read_regular_bytes', side_effect=replaced_policy), patch.object(module, '_read_markdown', wraps=module._read_markdown) as note_read:
            with self.assertRaises(evaluator.BindingChanged):
                evaluator.freeze_fixture(evaluator.fixture_path('recall_hard'), module)
        self.assertEqual(note_read.call_count, 0)

    def test_fixture_ancestor_reparse_is_rejected_before_config_read(self):
        legacy = evaluator.load_modules()[0]
        paths = sys.modules['vault_paths']
        original = paths._is_link_or_reparse
        def unsafe_ancestor(path):
            return True if path == ROOT/'tests' else original(path)
        with patch.object(paths, '_is_link_or_reparse', side_effect=unsafe_ancestor), patch.object(legacy, '_read_regular_bytes', wraps=legacy._read_regular_bytes) as reads:
            with self.assertRaises(ValueError):
                evaluator.freeze_fixture(evaluator.fixture_path('recall_hard'), legacy)
        self.assertEqual(reads.call_count, 0)

    def test_fixture_ancestor_is_rechecked_during_snapshot(self):
        legacy = evaluator.load_modules()[0]
        paths = sys.modules['vault_paths']
        original_link = paths._is_link_or_reparse
        original_read = legacy._read_markdown
        replaced = False
        def unsafe_ancestor(path):
            return True if replaced and path == ROOT/'tests' else original_link(path)
        def read_then_replace(*args, **kwargs):
            nonlocal replaced
            result = original_read(*args, **kwargs)
            replaced = True
            return result
        with patch.object(paths, '_is_link_or_reparse', side_effect=unsafe_ancestor), patch.object(legacy, '_read_markdown', side_effect=read_then_replace) as reads:
            with self.assertRaises(ValueError):
                evaluator.freeze_fixture(evaluator.fixture_path('recall_hard'), legacy)
        self.assertEqual(reads.call_count, 1)

    def test_model_stream_hash_does_not_require_python_311_file_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'model.bin'
            raw = b'bounded test bytes' * 100000
            path.write_bytes(raw)
            with patch.object(evaluator.hashlib, 'file_digest', side_effect=AssertionError('Python 3.10 has no file_digest'), create=True):
                self.assertEqual(evaluator._stream_sha(path), evaluator.hashlib.sha256(raw).hexdigest())

    def test_qmd_does_not_inherit_index_path_or_unbounded_embedding_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'package'
            cli = package / 'dist/cli/qmd.js'
            cli.parent.mkdir(parents=True)
            cli.write_text('// fake installed CLI', encoding='utf-8')
            (package/'package.json').write_text(json.dumps({'name':'@tobilu/qmd','version':'test'}), encoding='utf-8')
            state = root / 'state'
            state.mkdir()
            polluted = {'INDEX_PATH':str(root/'outside.sqlite'), 'QMD_EMBED_PARALLELISM':'999', 'QMD_EMBED_CONTEXT_SIZE':'999999999'}
            with patch.dict(evaluator.os.environ, polluted, clear=True), patch.object(evaluator, 'run_command', return_value='[]') as calls:
                evaluator.setup_qmd({'documents':{}}, state, cli, 'node', 'search', 10, False)
            for call in calls.call_args_list:
                env = call.args[2]
                self.assertTrue('INDEX_PATH' not in env, 'external index override must be removed')
                self.assertEqual(env['QMD_EMBED_PARALLELISM'], '1')
                self.assertEqual(env['QMD_EMBED_CONTEXT_SIZE'], '2048')

    def test_no_answer_errors_are_not_successful_empty_results(self):
        labels = [
            dict(id='ko-a', query='q', language='ko', scenario='short', expectation='answerable', expected_paths=['a.md'], relevance={'a.md':3}),
            dict(id='ko-n', query='q', language='ko', scenario='abstention', expectation='no_answer', expected_paths=[], relevance={}),
            dict(id='en-n', query='q', language='en', scenario='abstention', expectation='no_answer', expected_paths=[], relevance={}),
            dict(id='en-a', query='q', language='en', scenario='short', expectation='answerable', expected_paths=['a.md'], relevance={'a.md':3}),
        ]
        outcomes = [dict(paths=['a.md'], error=None, latency_ms=2), dict(paths=['b.md'], error=None, latency_ms=3), dict(paths=[], error='timeout', latency_ms=4), dict(paths=[], error='model_unavailable', latency_ms=5)]
        rows = [evaluator.score_row(label, outcome) for label, outcome in zip(labels, outcomes)]
        summary = evaluator.summarize(rows)
        self.assertEqual(summary['answerable_query_count'], 2)
        self.assertEqual(summary['recall_at_5'], .5)
        self.assertEqual(summary['no_answer']['successful_query_count'], 1)
        self.assertEqual(summary['no_answer']['search_error_count'], 1)
        self.assertEqual(summary['no_answer']['false_positive_rate_on_success'], 1)
        self.assertEqual(summary['error_count'], 2)
        self.assertEqual(summary['error_breakdown'], {'timeout': 1, 'model_or_dependency': 1})
        self.assertEqual(summary['successful_query_count'], 2)
        self.assertEqual(evaluator.partitions(rows)['by_language']['ko']['query_count'], 2)
        self.assertEqual(evaluator.partitions(rows)['by_scenario']['short']['query_count'], 2)

    def test_qmd_path_mapping_is_exact_and_does_not_accept_traversal(self):
        mapping = {'doc000001.md':'20-knowledge/allowed.md'}
        good = [{'file':'qmd://av_eval/doc000001.md', 'score':.5}]
        self.assertEqual(evaluator.qmd_paths(good, mapping), ['20-knowledge/allowed.md'])
        indexed = [{'file':'qmd://av_eval/doc000001.md?index=av_eval', 'score':.5}]
        self.assertEqual(evaluator.qmd_paths(indexed, mapping), ['20-knowledge/allowed.md'])
        for value in ('qmd://other/doc000001.md', 'qmd://av_eval/../doc000001.md', 'qmd://av_eval/%2e%2e/doc000001.md', '/private/note.md', 'qmd://av_eval/unmapped.md', 'qmd://av_eval/doc000001.md?index=other', 'qmd://av_eval/doc000001.md#fragment'):
            with self.assertRaises(ValueError):
                evaluator.qmd_paths([{'file':value}], mapping)

    def test_subprocess_bounded_and_without_shell(self):
        with patch.object(evaluator.subprocess, 'run', return_value=subprocess.CompletedProcess(['node'], 0, '[]', '')) as run:
            self.assertEqual(evaluator.run_command(['node', 'qmd.js'], Path(tempfile.gettempdir()), {}, 10), '[]')
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_default_run_does_not_import_optional_packages_or_call_qmd(self):
        with patch.object(evaluator, 'setup_kiwi', side_effect=AssertionError('optional import')), patch.object(evaluator, 'setup_qmd', side_effect=AssertionError('optional process')):
            result = evaluator.evaluate('recall_hard')
        self.assertEqual(set(result['backends']), {'bm25', 'bm25_hangul2'})
        self.assertEqual(result['binding_status'], 'unchanged')
        for backend in result['backends'].values():
            self.assertEqual(backend['by_language']['ko']['query_count'], 32)
            self.assertEqual(backend['by_language']['en']['query_count'], 32)
            self.assertEqual(backend['summary']['query_count'], 64)

    def test_query_mode_does_not_download_models_without_explicit_flag(self):
        with self.assertRaises(ValueError):
            evaluator.evaluate('recall_hard', qmd_mode='query')
        with self.assertRaises(ValueError):
            evaluator.evaluate('recall_hard', allow_model_download=True)
        with self.assertRaises(ValueError):
            evaluator.evaluate('recall_hard', qmd_model_cache='some-cache')

    def test_kiwi_uses_same_core_formula_without_fusing_another_channel(self):
        legacy, advanced = evaluator.load_modules()
        class FakeKiwi:
            def __init__(self, **kwargs):
                pass
            def tokenize(self, text):
                return [SimpleNamespace(form=word, tag='NNG') for word in legacy._WORD_RE.findall(text)]
        fake = SimpleNamespace(Kiwi=FakeKiwi, __version__='test-version')
        documents = {'a.md': {'text':'motor motor inspection'}, 'b.md': {'text':'motor inspection inspection inspection'}}
        snapshot = {'documents':documents}
        expected = advanced._ranked_paths(advanced._bm25(documents, ('motor',)))[:5]
        with patch.dict(sys.modules, {'kiwipiepy':fake}), patch.object(evaluator.importlib.metadata, 'version', return_value='test-model'):
            search, metadata = evaluator.setup_kiwi(snapshot, advanced)
            self.assertEqual(search('motor'), expected)
            self.assertEqual(metadata['model_package_version'], 'test-model')

    def test_hybrid_cache_validation_happens_before_model_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'package'
            cli = package / 'dist/cli/qmd.js'
            cli.parent.mkdir(parents=True)
            cli.write_text('// fake installed CLI', encoding='utf-8')
            (package/'package.json').write_text(json.dumps({'name':'@tobilu/qmd','version':'test'}), encoding='utf-8')
            cache = root / 'models'
            cache.mkdir()
            (cache/evaluator.EMBED_CACHE_FILE).write_bytes(b'not a GGUF model')
            state = root / 'state'
            state.mkdir()
            with patch.object(evaluator, 'run_command', return_value='[]') as calls:
                with self.assertRaises(ValueError):
                    evaluator.setup_qmd({'documents':{}}, state, cli, 'node', 'query', 10, True, cache)
            self.assertEqual(calls.call_count, 2)
            self.assertTrue(all('embed' not in call.args[0] and 'pull' not in call.args[0] for call in calls.call_args_list))

    def test_report_is_rejected_when_snapshot_changes_during_search(self):
        original = evaluator.freeze_fixture
        count = 0
        def changing(*args):
            nonlocal count
            count += 1
            result = original(*args)
            if count == 2:
                result['binding']['config_sha256'] = 'changed'
            return result
        with patch.object(evaluator, 'freeze_fixture', side_effect=changing):
            with self.assertRaises(evaluator.BindingChanged):
                evaluator.evaluate('recall_hard')


if __name__ == '__main__':
    unittest.main()
