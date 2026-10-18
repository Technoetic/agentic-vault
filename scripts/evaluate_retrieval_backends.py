#!/usr/bin/env python3
"""Compare top-five path ranking on public checked-in fixtures, never a real vault.

Optional dependencies are deliberately absent from the default execution path.
This measures source ranking, not LLM answers, authority, temporal policy or a
production speed comparison. Labels are used only after each search finishes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ('recall_hard', 'recall', 'recall_advanced')
SCRIPT_DIR = ROOT / 'skills/agentic-vault/scripts'
COLLECTION = 'av_eval'
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
EMBED_CACHE_FILE = 'hf_ggml-org_embeddinggemma-300M-Q8_0.gguf'
CORE_BINDING_FILES = ('vault_recall.py', 'vault_retrieval.py', 'vault_paths.py',
                      'vault_healthcheck.py', 'vault_query_expansion.py')


class BindingChanged(ValueError):
    pass


def fixture_path(choice: str) -> Path:
    if type(choice) is not str or choice not in FIXTURES:
        raise ValueError('fixture must be an explicit checked-in fixture choice')
    return ROOT / 'tests/fixtures' / choice


def load_modules():
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        legacy = importlib.import_module('vault_recall')
        advanced = importlib.import_module('vault_retrieval')
    finally:
        sys.path.remove(str(SCRIPT_DIR))
    return legacy, advanced


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stream_sha(path: Path) -> str:
    """Bounded streaming hash also supported by Python 3.10."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _guard_fixture(fixture: Path, legacy) -> Path:
    """Check every component from the repository root, not just vault/ itself."""
    relative = fixture.relative_to(ROOT).as_posix()
    legacy.resolve_note_path(ROOT, relative + '/queries.json')
    return legacy.resolve_note_path(ROOT, relative + '/vault/' + legacy.CONFIG_RELPATH)


def freeze_fixture(fixture: Path, legacy) -> dict:
    """Policy-filter before every content read; bind allowed sources and policy."""
    if fixture not in [fixture_path(choice) for choice in FIXTURES]:
        raise ValueError('arbitrary fixture paths are forbidden')
    vault = fixture / 'vault'
    diagnostics = legacy._diagnostics()
    config_path = _guard_fixture(fixture, legacy)
    config_before, stable = legacy._read_regular_bytes(config_path, legacy.MAX_CONFIG_BYTES)
    config = legacy._read_config(vault, diagnostics)
    if not stable or config is None:
        raise BindingChanged('fixture policy is unavailable or unstable')
    deny = tuple(config.get('deny_zones') or ())
    exclude = (*tuple(config.get('exclude_dirs') or ()), '.git')
    documents = {}
    for rel, path in legacy._markdown_paths(vault, deny, exclude, diagnostics):
        _guard_fixture(fixture, legacy)
        current_config, stable = legacy._read_regular_bytes(
            legacy.resolve_note_path(vault, legacy.CONFIG_RELPATH), legacy.MAX_CONFIG_BYTES)
        if not stable or current_config != config_before:
            raise BindingChanged('fixture policy changed before a source read')
        raw = []
        legacy.resolve_note_path(vault, rel, deny)
        text = legacy._read_markdown(path, diagnostics, rel, raw_sink=raw.append)
        if text is None or not raw:
            raise BindingChanged('fixture source could not be read completely')
        legacy.resolve_note_path(vault, rel, deny)
        documents[rel] = {'text': text, 'raw': raw[0], 'sha256': _sha(raw[0])}
    config_after, stable = legacy._read_regular_bytes(_guard_fixture(fixture, legacy), legacy.MAX_CONFIG_BYTES)
    if not stable or config_before != config_after or not diagnostics['search_complete']:
        raise BindingChanged('fixture policy or inventory changed during snapshot')
    query_path = legacy.resolve_note_path(fixture, 'queries.json')
    raw_queries, stable = legacy._read_regular_bytes(query_path, 1024 * 1024)
    if not stable:
        raise BindingChanged('queries changed during snapshot')
    # Reuse independent fixture label validation; never pass these labels to search.
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        labels = importlib.import_module('evaluate_recall')._load_queries(fixture)
    finally:
        sys.path.remove(str(ROOT / 'scripts'))
    check_queries, stable = legacy._read_regular_bytes(query_path, 1024 * 1024)
    if not stable or raw_queries != check_queries:
        raise BindingChanged('queries changed during validation')
    code_paths = [Path(__file__), ROOT / 'scripts/evaluate_recall.py',
                  *(SCRIPT_DIR / name for name in CORE_BINDING_FILES)]
    binding = {'config_sha256': _sha(config_before), 'queries_sha256': _sha(raw_queries),
               'source_sha256': {path: doc['sha256'] for path, doc in documents.items()},
               'code_sha256': {path.relative_to(ROOT).as_posix(): _sha(path.read_bytes()) for path in code_paths}}
    _guard_fixture(fixture, legacy)
    return {'documents': documents, 'queries': labels, 'binding': binding,
            'diagnostics': diagnostics, 'vault': vault}


def assert_same_binding(before: dict, after: dict) -> None:
    if before['binding'] != after['binding']:
        raise BindingChanged('source/query/config/code binding changed; discard mixed-source metrics')


def export_corpus(snapshot: dict, directory: Path) -> dict[str, str]:
    """Copy unchanged allowed bytes into flat IDs; external output has no authority."""
    directory.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for number, (path, document) in enumerate(sorted(snapshot['documents'].items()), 1):
        name = f'doc{number:06d}.md'
        (directory / name).write_bytes(document['raw'])
        mapping[name] = path
    return mapping


def qmd_paths(raw: object, mapping: dict) -> list[str]:
    if not isinstance(raw, list) or len(raw) > 50:
        raise ValueError('qmd results must be a bounded JSON list')
    paths = []
    for row in raw:
        file = row.get('file') if isinstance(row, dict) else None
        prefix = f'qmd://{COLLECTION}/'
        # qmd 2.8 named indexes append exactly ?index=<name>; older releases do
        # not. Accept only these two exact forms, never general URI decoding.
        if isinstance(file, str) and file.endswith(f'?index={COLLECTION}'):
            file = file[:-len(f'?index={COLLECTION}')]
        if not isinstance(file, str) or not file.startswith(prefix) or file[len(prefix):] not in mapping:
            raise ValueError('qmd returned an unmapped or unsafe file URI')
        path = mapping[file[len(prefix):]]
        if path not in paths:
            paths.append(path)
    return paths[:5]


def run_command(command: list[str], cwd: Path, env: dict, timeout: float) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout, shell=False,
                            encoding='utf-8', errors='replace', stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if len(result.stdout.encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise ValueError('external output exceeds byte limit')
    if result.returncode:
        raise RuntimeError(f'external exit {result.returncode}: {result.stderr[-500:]}')
    return result.stdout


def setup_kiwi(snapshot: dict, advanced) -> tuple:
    start = time.perf_counter()
    import kiwipiepy  # optional import only after explicit --kiwi
    kiwi = kiwipiepy.Kiwi(num_workers=1)
    def tokenize(text):
        return [token.form.casefold() for token in kiwi.tokenize(text)
                if token.form and token.tag not in ('SF', 'SP', 'SSO', 'SSC', 'SE', 'SO', 'SW')]
    tokenized = {path: tokenize(advanced._legacy._normalize(doc['text']))
                 for path, doc in snapshot['documents'].items()}
    # Preserve exactly the current core BM25 formula via its tokenizer callback.
    # Cache document tokenization as separate index setup; query tokens remain timed.
    cache = {advanced._legacy._normalize(snapshot['documents'][path]['text']): tokens
             for path, tokens in tokenized.items()}
    def cached_tokenize(text):
        return cache[text] if text in cache else tokenize(text)
    def search(query):
        terms = tuple(dict.fromkeys(tokenize(advanced._legacy._normalize(query[:1000]))))[:32]
        scores = advanced._bm25(snapshot['documents'], terms, tokenize=cached_tokenize)
        return advanced._ranked_paths(scores)[:5]
    return search, {'dependency_version': kiwipiepy.__version__, 'model_package_version': importlib.metadata.version('kiwipiepy_model'),
                    'setup_ms': (time.perf_counter()-start)*1000,
                    'index': 'Kiwi token cache in memory; same current-core BM25 k1=1.2 b=.75',
                    'latency_scope': 'query tokenization + cached-token BM25; excludes corpus scan and Kiwi/index startup',
                    'model_download': False}


def setup_qmd(snapshot: dict, temporary: Path, cli: Path, node: str, mode: str,
              timeout: float, allow_model_download: bool, model_cache=None) -> tuple:
    if cli.suffix.lower() != '.js' or not cli.is_file():
        raise ValueError('--qmd-cli must be the installed absolute dist/cli/qmd.js file')
    cli = cli.resolve()
    package = cli.parents[2] / 'package.json'
    package_data = json.loads(package.read_text(encoding='utf-8'))
    if package_data.get('name') != '@tobilu/qmd':
        raise ValueError('qmd entry point is not the expected installed package')
    # Pass only the fixed runtime variables Node/Windows need. Existing qmd
    # switches, INDEX_PATH, credentials and proxy settings are not inherited.
    runtime_keys = ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC',
                    'PATHEXT', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'LANG')
    env = {key: os.environ[key] for key in runtime_keys if key in os.environ}
    # Prevent existing global index/config/hooks from entering this experiment.
    env.update(QMD_CONFIG_DIR=str(temporary / 'config'), XDG_CACHE_HOME=str(temporary / 'cache'),
               QMD_DOCTOR_DEVICE_PROBE='0', NO_COLOR='1', QMD_SOURCE_MODE='0',
               QMD_EMBED_PARALLELISM='1', QMD_EMBED_CONTEXT_SIZE='2048')
    if mode == 'query':
        env.update(QMD_FORCE_CPU='1', QMD_LLAMA_GPU='off')
    prefix = [node, str(cli), '--index', COLLECTION]
    corpus = temporary / 'corpus'
    mapping = export_corpus(snapshot, corpus)
    (temporary / 'path-mapping.json').write_text(json.dumps(mapping, indent=2), encoding='utf-8')
    start = time.perf_counter()
    version_output = run_command(prefix + ['--version'], temporary, env, timeout).strip()
    startup_ms = (time.perf_counter()-start)*1000
    start = time.perf_counter()
    run_command(prefix + ['collection', 'add', str(corpus), '--name', COLLECTION, '--mask', '*.md'], temporary, env, timeout)
    index_ms = (time.perf_counter()-start)*1000
    model_setup_ms = 0.0
    model_cache_reused = None
    if mode == 'query':
        if not allow_model_download:
            raise ValueError('query requires explicit --allow-model-download; no automatic model calls')
        start = time.perf_counter()
        if model_cache:
            # Only the explicit public embedding file is reused. No existing
            # qmd index/config/hooks, generation model or reranker is copied.
            legacy = load_modules()[0]
            cached = legacy.resolve_note_path(Path(model_cache), EMBED_CACHE_FILE)
            before = cached.stat()
            if not stat.S_ISREG(before.st_mode) or not 16 <= before.st_size <= 1024 * 1024 * 1024 or before.st_nlink != 1:
                raise ValueError('embedding cache file exceeds bounds or is not regular')
            with cached.open('rb') as handle:
                if handle.read(4) != b'GGUF':
                    raise ValueError('embedding cache is not a GGUF file')
            digest = _stream_sha(cached)
            destination = temporary / 'cache/qmd/models' / EMBED_CACHE_FILE
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, destination)
            after = cached.stat()
            copied_digest = _stream_sha(destination)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest != copied_digest:
                raise BindingChanged('embedding cache changed while copied')
            model_cache_reused = {'path': str(cached), 'sha256': digest, 'bytes': before.st_size}
        # Typed lex+vec with no reranker loads only the embedding model. Avoid
        # qmd pull, which downloads generation/reranking models unnecessarily.
        run_command(prefix + ['embed', '-c', COLLECTION, '--timeout', '2'], temporary, env, timeout)
        model_setup_ms = (time.perf_counter()-start)*1000
    def search(query):
        query = query[:1000]
        text = query if mode == 'search' else f'lex: {query}\nvec: {query}'
        command = prefix + [mode, text, '-c', COLLECTION, '-n', '5', '--min-score', '0', '--json']
        if mode == 'query':
            command += ['--no-rerank', '--no-gpu']
        raw = run_command(command, temporary, env, timeout)
        return qmd_paths(json.loads(raw), mapping)
    return search, {'dependency_version': package_data['version'], 'version_output': version_output,
                    'package_sha256': _sha(package.read_bytes()), 'cli_sha256': _sha(cli.read_bytes()),
                    'startup_probe_ms': startup_ms, 'index_setup_ms': index_ms, 'model_setup_ms': model_setup_ms,
                    'index_bytes_after_setup': sum(path.stat().st_size for path in temporary.rglob('*') if path.is_file()),
                    'model_files': [{'name':path.name, 'bytes':path.stat().st_size}
                                    for path in sorted((temporary/'cache/qmd/models').glob('*.gguf'))],
                    'model_download_permitted': allow_model_download, 'model_cache_reused': model_cache_reused,
                    'model_download': mode == 'query' and model_cache_reused is None,
                    'corpus_path': str(corpus), 'mapping_path': str(temporary / 'path-mapping.json'),
                    'latency_scope': 'one fresh node CLI process per query; startup + indexed search + optional local inference',
                    'filename_policy': 'flat numeric IDs preserve raw source bytes; original filename features are not provided to qmd',
                    'min_score': 0, 'hybrid_strategy': 'typed lex+vec; CPU; no query expansion; no LLM reranker' if mode == 'query' else None,
                    'index': 'isolated fixture-only SQLite; keyword mode calls no models'}


def score_row(label: dict, outcome: dict) -> dict:
    paths = outcome['paths'][:5]
    expected = label['expected_paths']
    relevance = label.get('relevance', {path: 1 for path in expected})
    ideal = sorted(relevance.values(), reverse=True)[:5]
    idcg = sum((2**grade-1)/math.log2(rank+2) for rank, grade in enumerate(ideal))
    dcg = sum((2**relevance.get(path, 0)-1)/math.log2(rank+2) for rank, path in enumerate(paths))
    return {'id': label['id'], 'language': label['language'], 'scenario': label['scenario'],
            'expectation': label['expectation'], 'paths': paths, 'error': outcome['error'],
            'latency_ms': outcome['latency_ms'],
            'recall_at_5': len(set(paths).intersection(expected))/len(expected) if expected else None,
            'ndcg_at_5': dcg/idcg if idcg else None,
            'forbidden_returned': sorted(set(paths).intersection(label.get('forbidden_paths', []))),
            'stale_returned': sorted(set(paths).intersection(label.get('stale_paths', [])))}


def _mean(values):
    return sum(values)/len(values) if values else None


def summarize(rows: list[dict]) -> dict:
    answered = [row for row in rows if row['expectation'] == 'answerable']
    successful = [row for row in rows if row['error'] is None]
    no_answer = [row for row in rows if row['expectation'] == 'no_answer']
    successful_no_answer = [row for row in no_answer if row['error'] is None]
    false_positives = sum(bool(row['paths']) for row in successful_no_answer)
    latencies = sorted(row['latency_ms'] for row in successful)
    errors = [row['error'] for row in rows if row['error'] is not None]
    error_kinds = {}
    for error in errors:
        kind = ('timeout' if 'TimeoutExpired' in error or error == 'timeout' else
                'model_or_dependency' if any(word in error.casefold() for word in ('model', 'module', 'import', 'kiwi')) else
                'unsafe_external_output' if 'unmapped or unsafe' in error else 'search_or_setup')
        error_kinds[kind] = error_kinds.get(kind, 0)+1
    return {'query_count': len(rows), 'answerable_query_count': len(answered),
            'successful_query_count': len(successful), 'error_count': len(rows)-len(successful),
            'error_breakdown': error_kinds,
            'recall_at_5': _mean([row['recall_at_5'] for row in answered]),
            'ndcg_at_5': _mean([row['ndcg_at_5'] for row in answered]),
            'metric_error_policy': 'answerable errors count as zero; no-answer search errors excluded from successful empty/false-positive rates',
            'latency_mean_ms_successful': _mean(latencies),
            'latency_p95_ms_successful': latencies[max(0, math.ceil(.95*len(latencies))-1)] if latencies else None,
            'forbidden_exposure_query_count': sum(bool(row['forbidden_returned']) for row in rows),
            'stale_exposure_query_count': sum(bool(row['stale_returned']) for row in rows),
            'no_answer': {'query_count': len(no_answer), 'successful_query_count': len(successful_no_answer),
                          'search_error_count': len(no_answer)-len(successful_no_answer),
                          'false_positive_query_count': false_positives,
                          'false_positive_rate_on_success': false_positives/len(successful_no_answer) if successful_no_answer else None,
                          'successful_empty_query_count': sum(not row['paths'] for row in successful_no_answer)}}


def partitions(rows: list[dict]) -> dict:
    return {'by_language': {value: summarize([row for row in rows if row['language'] == value])
                            for value in sorted({row['language'] for row in rows})},
            'by_scenario': {value: summarize([row for row in rows if row['scenario'] == value])
                            for value in sorted({row['scenario'] for row in rows})}}


def evaluate(fixture='recall_hard', *, kiwi=False, qmd_cli=None, qmd_mode='search',
             node=None, timeout=60, allow_model_download=False, artifact_dir=None,
             qmd_model_cache=None) -> dict:
    if qmd_mode not in ('search', 'query') or type(kiwi) is not bool:
        raise ValueError('invalid backend options')
    if qmd_mode == 'query' and (not qmd_cli or not allow_model_download):
        raise ValueError('query needs explicit CLI and model-download permission')
    if allow_model_download and qmd_mode != 'query':
        raise ValueError('model-download flag is only valid for explicit query mode')
    if qmd_model_cache and (qmd_mode != 'query' or not qmd_cli):
        raise ValueError('model cache is only valid for explicit qmd query mode')
    if not 1 <= timeout <= 300:
        raise ValueError('timeout must be between 1 and 300 seconds')
    path = fixture_path(fixture)
    legacy, advanced = load_modules()
    start = time.perf_counter()
    snapshot = freeze_fixture(path, legacy)
    snapshot_ms = (time.perf_counter()-start)*1000
    backends = {}
    searchers = {}
    for name, bigrams in (('bm25', None), ('bm25_hangul2', 'hangul2')):
        def search(query, bigrams=bigrams):
            result = advanced.retrieve(snapshot['vault'], query, limit=5, max_tokens=0, backend='bm25', lexical_ngrams=bigrams)
            diagnostics = result['diagnostics']
            if diagnostics['status'] != 'ok' or not diagnostics['search_complete']:
                raise RuntimeError('core search incomplete: '+diagnostics['status'])
            return [match['path'] for match in result['matches']]
        searchers[name] = search
        backends[name] = {'setup_ms': 0, 'index': 'current core in-memory per-query index',
                          'latency_scope': 'core policy/config + full allowed corpus scan + BM25/RRF ranking; no rendering'}
    temporary_context = None
    try:
        if kiwi:
            try:
                searchers['kiwi_bm25'], backends['kiwi_bm25'] = setup_kiwi(snapshot, advanced)
            except Exception as error:
                backends['kiwi_bm25'] = {'setup_error': f'{type(error).__name__}: {error}'}
        if qmd_cli:
            parent = Path(artifact_dir).resolve() if artifact_dir else None
            if parent and (parent == ROOT or ROOT in parent.parents):
                raise ValueError('qmd exported corpus must stay outside the repository worktree')
            if parent:
                parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix='av-eval-qmd-', dir=parent))
            temporary_context = temporary
            qmd_name = 'qmd_'+qmd_mode
            try:
                node_command = node or shutil.which('node')
                if not node_command:
                    raise ValueError('node executable not found')
                searchers[qmd_name], backends[qmd_name] = setup_qmd(snapshot, temporary, Path(qmd_cli), node_command, qmd_mode, timeout, allow_model_download, qmd_model_cache)
            except Exception as error:
                backends[qmd_name] = {'setup_error': f'{type(error).__name__}: {error}', 'corpus_path': str(temporary/'corpus')}
        for name, metadata in backends.items():
            rows = []
            for label in snapshot['queries']:
                start = time.perf_counter()
                outcome = {'paths': [], 'error': metadata.get('setup_error')}
                if name in searchers:
                    try:
                        _guard_fixture(path, legacy)
                        outcome['paths'] = searchers[name](label['query'])
                    except Exception as error:
                        outcome['error'] = f'{type(error).__name__}: {error}'
                outcome['latency_ms'] = (time.perf_counter()-start)*1000
                rows.append(score_row(label, outcome))
            metadata.update(summary=summarize(rows), per_query=rows, **partitions(rows))
        after = freeze_fixture(path, legacy)
        assert_same_binding(snapshot, after)
        return {'schema_version': 1, 'fixture': fixture, 'top_k': 5,
                'binding_status': 'unchanged', 'binding_before': snapshot['binding'], 'binding_after': after['binding'],
                'allowed_source_count': len(snapshot['documents']), 'snapshot_setup_ms': snapshot_ms,
                'backends': backends, 'protocol': {'labels': 'evaluation only, never passed to retrieval',
                'options': 'no query expansion, temporal filtering, graph expansion, answer generation or threshold tuning',
                'code_binding_scope': 'runner, label validator, and five transitively imported core files; unrelated vault tools excluded',
                'randomness': 'no sampling or seed; deterministic lexical/embedding ranking; single ordered pass',
                'latency': 'successful queries only, one pass, no warm-up; scopes differ and are reported per backend',
                'no_answer_limit': 'nonempty candidates are false positives, not asserted answers; no model abstention or production threshold claim',
                'export': 'only policy-allowed checked-in fixture Markdown bytes; original path mapping validated exactly'},
                'settings': {'kiwi': kiwi, 'qmd_mode': qmd_mode if qmd_cli else None,
                             'timeout_seconds': timeout, 'allow_model_download': allow_model_download}}
    finally:
        # Explicit artifact directories keep fixture-only setup for review.
        if temporary_context is not None and artifact_dir is None:
            shutil.rmtree(temporary_context)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', choices=FIXTURES, default='recall_hard')
    parser.add_argument('--kiwi', action='store_true')
    parser.add_argument('--qmd-cli', type=Path, help='installed absolute dist/cli/qmd.js')
    parser.add_argument('--node', help='node executable path (avoids Windows .cmd shells)')
    parser.add_argument('--qmd-mode', choices=('search', 'query'), default='search')
    parser.add_argument('--allow-model-download', action='store_true', help='explicit query-mode local model setup only')
    parser.add_argument('--qmd-model-cache', type=Path, help='explicit existing public embedding cache directory; copy only embeddinggemma GGUF')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--artifact-dir', type=Path, help='fixture-only corpus/cache outside the worktree')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.fixture, kiwi=args.kiwi, qmd_cli=args.qmd_cli, node=args.node,
                          qmd_mode=args.qmd_mode, timeout=args.timeout,
                          allow_model_download=args.allow_model_download, artifact_dir=args.artifact_dir,
                          qmd_model_cache=args.qmd_model_cache)
    except (ValueError, OSError) as error:
        print(json.dumps({'status':'invalid_evaluation', 'error':str(error)}, ensure_ascii=False))
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered+'\n', encoding='utf-8')
        print(json.dumps({'output':str(args.output), 'backends': {name: data['summary'] for name, data in report['backends'].items()}}, ensure_ascii=False))
    else:
        print(rendered)
    return 0 if all(data['summary']['error_count'] == 0 for data in report['backends'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
