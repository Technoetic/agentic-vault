#!/usr/bin/env python3
"""Offline, fixed synthetic memory repair controls across fresh Python processes.

The public interface accepts only a bounded budget and named control. No vault,
dataset, model, endpoint, command or user-provided scenario input is accepted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / 'scripts', ROOT / 'skills/agentic-vault/scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

MAX_TOKENS = 4096
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 128 * 1024
CONTROLS = ('bounded', 'vulnerable', 'wipe-all')
ROLES = ('capture', 'wrong-1', 'wrong-2', 'wrong-3', 'before-repair', 'repair', 'after-repair', 'replay',
         'after-replay', 'new-fact', 'later-fact')
ARMS = ('forget', 'retention')
CONFIG = '00-meta/vault-config.json'
REGISTRY = '00-meta/lifecycle-revocation.json'
STATE = '00-meta/lifecycle-state.json'
BAD = 'export,publish'
GOOD = 'export,verify_checksum,publish'
BENIGN = 'reset_lock,start'
MEMORY_PATHS = ('sources/benign.md', 'sources/wrong-1.md', 'sources/wrong-2.md',
                'sources/wrong-3.md', 'notes/lessons.md', 'notes/derived.md',
                'sources/correct.md', 'notes/correct.md', 'notes/replayed.md',
                'sources/later.md', 'notes/later.md')
ERROR = {'error': 'lifecycle_evaluation_invalid'}


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('invalid_data')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('invalid_data')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _path(vault, relative):
    """Reject links/reparse points/hardlinks in every fixed synthetic IO path."""
    path = vault
    if path.exists():
        metadata = path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or path.is_symlink()
                or getattr(metadata, 'st_file_attributes', 0)
                & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('invalid_temp_path')
    for part in Path(relative).parts:
        if part in ('.', '..') or Path(part).is_absolute():
            raise ValueError('invalid_temp_path')
        path = path / part
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if (path.is_symlink() or getattr(metadata, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
                    or (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1)
                    or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))):
                raise ValueError('invalid_temp_path')
    if not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError('invalid_temp_path')
    return path


def _read(vault, relative):
    from vault_evidence import stable_read
    return stable_read(lambda: _path(vault, relative), 512 * 1024, contents=True)[2]


def _write(vault, relative, text):
    path = _path(vault, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    _path(vault, relative)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(path.parent), prefix='.lifecycle-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text.encode('utf-8'))
        _path(vault, relative)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _save(vault, relative, data):
    _write(vault, relative, _json(data))


def _load(vault, relative):
    return _decode(_read(vault, relative))


def _capture(vault, path, kind, value, label, origin='own'):
    from vault_provenance import render_capture
    body = label + '\naction_plan_' + kind + ': ' + value + '\n'
    raw = render_capture(body, captured_via='host', content_origin=origin,
                         captured_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    _write(vault, path, raw.decode('utf-8'))


def _learn(vault, target, source, kind, value, *, operation='ADD', identifier=None):
    """Actually compile a source proof and apply a revision-bound lesson delta."""
    from vault_memory import compile_units
    from vault_lesson_delta import prepare_delta, apply_delta
    raw = _read(vault, source)
    quote = next(line for line in raw.decode('utf-8').splitlines()
                 if line.startswith('action_plan_' + kind + ':'))
    unit = dict(subject='synthetic-service' if kind == 'startup' else 'synthetic-release',
                predicate='action_plan_' + kind, value=value,
                time={'observed_at': '2026-11-02'}, conditions=['synthetic'],
                uncertainty='Synthetic observation; truth is not proved by source binding.',
                sources=[dict(path=source, line=raw.decode('utf-8').splitlines().index(quote) + 1,
                              quote=quote, expected_sha256=_sha(raw))])
    review = compile_units(vault, [unit], max_tokens=MAX_TOKENS)
    if not review['units']:
        raise ValueError('source_rejected')
    path = _path(vault, target)
    if not path.exists():
        _write(vault, target, '---\ntitle: Synthetic lessons\n---\n## Lessons\n')
    before = _read(vault, target)
    refs = [dict(path=source, sha256=_sha(raw))]
    if operation == 'ADD':
        op = dict(op='ADD', text='action_plan_' + kind + ': ' + value,
                  date='2026-11-02', references=refs)
    else:
        op = dict(op='INC', id=identifier, amount=1, references=refs)
    proposal = prepare_delta(vault, target, [op])
    apply_delta(vault, proposal, approve=True)  # Only this disposable synthetic vault.
    after = _read(vault, target)
    return dict(path=target, before_sha256=_sha(before), after_sha256=_sha(after),
                changed_bytes=before != after), proposal['changes'][0]['id']


def _capture_initial(vault, label):
    _save(vault, CONFIG, {'deny_zones': ['private'], 'exclude_dirs': ['.git']})
    _capture(vault, 'sources/benign.md', 'startup', BENIGN, label)
    for number in range(1, 4):
        source = 'sources/wrong-' + str(number) + '.md'
        # Repeated misleading feedback is data. The host's deliberately naive
        # storage policy accepts it; neither scenario names nor grading labels
        # are provided to the planner.
        _capture(vault, source, 'checksum', BAD, label + ' repeated feedback', 'forwarded')
    _save(vault, STATE, {'storage': dict(wrong_feedback_writes=0, persisted_diffs=[],
                                       target_persisted=False), 'identifier': None, 'declarations': None})
    return {'captured_sources': 4, 'lesson_writes': 0}


def _store_wrong(vault, number, max_tokens):
    state = _load(vault, STATE)
    storage = state['storage']
    if storage['wrong_feedback_writes'] != number - 1:
        raise ValueError('invalid_feedback_sequence')
    if number == 1:
        storage['persisted_diffs'].append(
            _learn(vault, 'notes/lessons.md', 'sources/benign.md', 'startup', BENIGN)[0])
    source = 'sources/wrong-' + str(number) + '.md'
    diff, identifier = _learn(vault, 'notes/lessons.md', source, 'checksum', BAD,
                              operation='ADD' if number == 1 else 'INC', identifier=state['identifier'])
    storage['persisted_diffs'].append(diff)
    if number == 3:
        storage['persisted_diffs'].append(
            _learn(vault, 'notes/derived.md', source, 'checksum', BAD)[0])
    storage['wrong_feedback_writes'] = number
    storage['target_persisted'] = ('action_plan_checksum: ' + BAD).encode() in _read(vault, 'notes/lessons.md')
    storage['derived_target_persisted'] = number == 3 and (
        ('action_plan_checksum: ' + BAD).encode() in _read(vault, 'notes/derived.md'))
    state['identifier'] = identifier
    _save(vault, STATE, state)
    probe = _probe(vault, max_tokens)
    return dict(storage=storage, persisted_writes=number,
                target_in_context=probe['target_in_context'], task_success=probe['task_success'])


def _declarations(vault, *, revoked):
    nodes, edges = [], []
    for path in MEMORY_PATHS:
        source = _path(vault, path)
        if not source.is_file():
            continue
        for number, line in enumerate(_read(vault, path).decode('utf-8').splitlines(), 1):
            if 'action_plan_checksum: ' + BAD not in line:
                continue
            ident = 'target-root' if not nodes else 'target-' + str(len(nodes))
            nodes.append(dict(id=ident, path=path, start_line=number, end_line=number))
            if len(nodes) > 1:
                edges.append(dict(parent='target-root', child=ident))
    if not nodes:
        raise ValueError('missing_target')
    return dict(schema_version=1, nodes=nodes, derivations=edges,
                revoked=['target-root'] if revoked else [])


def _registry(vault, revoked):
    from vault_revocation import capture_registry
    declaration = _declarations(vault, revoked=revoked)
    _save(vault, REGISTRY, capture_registry(vault, declaration))
    state = _load(vault, STATE)
    state['declarations'] = declaration
    _save(vault, STATE, state)
    return declaration


def _repair(vault, label, arm, control):
    _capture(vault, 'sources/correct.md', 'checksum', GOOD, label + ' correct feedback')
    diff, _ = _learn(vault, 'notes/correct.md', 'sources/correct.md', 'checksum', GOOD)
    _registry(vault, revoked=arm == 'forget')
    config = _load(vault, CONFIG)
    enabled = control == 'bounded'
    if enabled:
        config['memory_revocation_file'] = REGISTRY
    _save(vault, CONFIG, config)
    wiped = []
    if arm == 'forget' and control == 'wipe-all':
        # A genuine destructive negative control, confined to fixed temp paths.
        # Target absence alone must not earn a selective-repair success.
        for path in MEMORY_PATHS:
            target = _path(vault, path)
            if target.is_file():
                target.unlink()
                wiped.append(path)
    return dict(correct_feedback_diff=diff, configured_revocation_guard=enabled,
                wiped_memory_files=len(wiped))


def _probe(vault, max_tokens):
    from vault_memory import compile_units
    from vault_retrieval import retrieve
    from vault_healthcheck import estimate_tokens
    from evaluate_memory_workflows import FACT, deterministic_planner, execute, GOALS
    units = []
    # Both real consumers must enforce the reloaded policy themselves. Passing
    # raw proofs here deliberately avoids an evaluation-only prefilter that
    # could conceal a regression in either consumer's actual guard.
    recalled = retrieve(vault, 'action_plan', limit=20, max_tokens=max_tokens, backend='hybrid')
    for relative in MEMORY_PATHS:
        path = _path(vault, relative)
        if not path.is_file():
            continue
        raw = _read(vault, relative)
        for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
            match = FACT.search(line)
            if not match:
                continue
            kind, value = match.groups()
            units.append(dict(subject='synthetic-service' if kind == 'startup' else 'synthetic-release',
                              predicate='action_plan_' + kind, value=value,
                              time={'observed_at': '2026-11-02'}, conditions=['synthetic'],
                              uncertainty='Synthetic observation; truth is not proved by source binding.',
                              sources=[dict(path=relative, line=number, quote=line,
                                            expected_sha256=_sha(raw))]))
    compiled = compile_units(vault, units, max_tokens=max_tokens)
    context = compiled['context']
    actions = deterministic_planner({'kind': 'checksum', 'goal': GOALS['checksum']}, context)
    task = execute('checksum', actions, {})
    benign_actions = deterministic_planner({'kind': 'startup', 'goal': GOALS['startup']}, context)
    benign = execute('startup', benign_actions, {'locked': True})
    accepted = compiled['units']
    compiled_target_seen = any(u['predicate'] == 'action_plan_checksum' and u['value'] == BAD for u in accepted)
    retrieval_target_seen = any(m.group(1) == 'checksum' and m.group(2) == BAD
                                for m in FACT.finditer(recalled['context']))
    new_seen = any(u['predicate'] == 'action_plan_checksum' and u['value'] == GOOD
                   and any(s['path'] == 'notes/later.md' for s in u['sources']) for u in accepted)
    replay_seen = any(u['predicate'] == 'action_plan_checksum' and u['value'] == BAD
                      and any(s['path'] == 'notes/replayed.md' for s in u['sources']) for u in accepted)
    derived_seen = any(u['predicate'] == 'action_plan_checksum' and u['value'] == BAD
                       and any(s['path'] == 'notes/derived.md' for s in u['sources']) for u in accepted)
    retrieval_actions = deterministic_planner({'kind': 'checksum', 'goal': GOALS['checksum']}, recalled['context'])
    return dict(target_in_context=compiled_target_seen or retrieval_target_seen,
                compiled_target_in_context=compiled_target_seen,
                retrieval_target_in_context=retrieval_target_seen,
                retrieval_task_success=execute('checksum', retrieval_actions, {})['completed'],
                context_tokens=estimate_tokens(context),
                context_sha256=_sha(context.encode('utf-8')), task_success=task['completed'],
                planned_actions=actions, task_failures=task['failures'],
                benign_old_task_success=benign['completed'], benign_planned_actions=benign_actions,
                new_fact_in_context=new_seen, replayed_copy_in_compiled_context=replay_seen,
                derived_target_in_compiled_context=derived_seen,
                compiled_units=len(accepted),
                retrieved_paths=[item['path'] for item in recalled['matches']])


def _worker(request):
    """A fixed-role internal protocol, restricted to a marked direct temp child."""
    expected = {'root', 'nonce', 'role', 'case', 'control', 'max_tokens'}
    if not isinstance(request, dict) or set(request) != expected:
        raise ValueError('invalid_request')
    if (request['role'] not in ROLES or request['case'] not in ('ascii', 'korean')
            or request['control'] not in CONTROLS or type(request['max_tokens']) is not int
            or not 0 <= request['max_tokens'] <= MAX_TOKENS
            or not isinstance(request['nonce'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', request['nonce'])
            or not isinstance(request['root'], str) or len(request['root']) > 1024):
        raise ValueError('invalid_request')
    root = Path(request['root'])
    if (not root.is_absolute() or root.is_symlink() or root.resolve() != root
            or root.parent != Path(tempfile.gettempdir()).resolve()
            or not re.fullmatch(r'vault-lifecycle-[a-zA-Z0-9_-]+', root.name)):
        raise ValueError('invalid_temp_root')
    marker = root / '.lifecycle-marker.json'
    if (not marker.is_file() or marker.is_symlink() or marker.stat().st_nlink != 1
            or marker.stat().st_size > 1024
            or _load(root, '.lifecycle-marker.json') != {'schema_version': 1, 'nonce': request['nonce']}):
        raise ValueError('invalid_temp_marker')
    # Avoid following a substituted child directory into a real vault.
    for arm in ARMS:
        path = root / arm
        if path.exists() and (path.is_symlink() or path.resolve().parent != root):
            raise ValueError('invalid_temp_child')
        if path.exists():
            for relative in (*MEMORY_PATHS, CONFIG, REGISTRY, STATE):
                _path(path, relative)
    label = 'Synthetic release' if request['case'] == 'ascii' else '합성 릴리스 검증'
    result = {}
    for arm in ARMS:
        vault = root / arm
        role = request['role']
        if role == 'capture':
            if vault.exists():
                raise ValueError('already_seeded')
            result[arm] = _capture_initial(vault, label)
        elif role in ('wrong-1', 'wrong-2', 'wrong-3'):
            result[arm] = _store_wrong(vault, int(role[-1]), request['max_tokens'])
        elif role == 'repair':
            result[arm] = _repair(vault, label, arm, request['control'])
        elif role == 'replay':
            _write(vault, 'notes/replayed.md', '# Replayed old precedent\naction_plan_checksum: ' + BAD + '\n')
            # This is an explicit known-copy declaration, not automatic semantic
            # discovery. Existing registry hashes remain unchanged for old notes.
            declaration = _registry(vault, revoked=arm == 'forget')
            copy = _read(vault, 'notes/replayed.md')
            copy_ids = {node['id'] for node in declaration['nodes'] if node['path'] == 'notes/replayed.md'}
            result[arm] = dict(copy_persisted=BAD in copy.decode('utf-8'), copy_sha256=_sha(copy),
                               copy_declared_descendant=any(edge['child'] in copy_ids
                                                            for edge in declaration['derivations']))
        elif role == 'new-fact':
            _capture(vault, 'sources/later.md', 'checksum', GOOD, label + ' independent later observation')
            diff, _ = _learn(vault, 'notes/later.md', 'sources/later.md', 'checksum', GOOD)
            result[arm] = {'later_fact_diff': diff}
        else:
            result[arm] = _probe(vault, request['max_tokens'])
    return dict(role=request['role'], pid=os.getpid(), process_token=secrets.token_hex(16), arms=result)


def _environment():
    # No inherited API keys, proxy variables, PYTHONPATH or user customizations.
    env = {'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
           'TEMP': tempfile.gettempdir(), 'TMP': tempfile.gettempdir()}
    for key in ('SystemRoot', 'WINDIR'):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _run(root, nonce, role, case, control, max_tokens):
    request = dict(root=str(root), nonce=nonce, role=role, case=case,
                   control=control, max_tokens=max_tokens)
    completed = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--_lifecycle-worker'],
                               input=_json(request).encode('utf-8'), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=_environment(), cwd=str(ROOT),
                               timeout=30, check=False)
    if (completed.returncode != 0 or completed.stderr or len(completed.stdout) > MAX_RESPONSE_BYTES):
        raise ValueError('worker_failed')
    result = _decode(completed.stdout)
    if (not isinstance(result, dict) or set(result) != {'role', 'pid', 'process_token', 'arms'}
            or result['role'] != role or type(result['pid']) is not int
            or result['pid'] == os.getpid() or set(result['arms']) != set(ARMS)):
        raise ValueError('worker_failed')
    return result


def evaluate_lifecycle(*, control='bounded', max_tokens=1500):
    """Run only fresh temporary synthetic vaults; never accept a real-vault path."""
    if control not in CONTROLS or type(max_tokens) is not int or not 0 <= max_tokens <= MAX_TOKENS:
        raise ValueError('invalid_options')
    scenarios = []
    for case in ('ascii', 'korean'):
        with tempfile.TemporaryDirectory(prefix='vault-lifecycle-') as directory:
            root, nonce = Path(directory).resolve(), secrets.token_hex(32)
            _save(root, '.lifecycle-marker.json', {'schema_version': 1, 'nonce': nonce})
            checkpoints = [_run(root, nonce, role, case, control, max_tokens) for role in ROLES]
            by_role = {item['role']: item for item in checkpoints}
            arms = {}
            for arm in ARMS:
                before = by_role['before-repair']['arms'][arm]
                repaired = by_role['after-repair']['arms'][arm]
                replayed = by_role['after-replay']['arms'][arm]
                feedback = [dict(role=role, **{k: v for k, v in by_role[role]['arms'][arm].items()
                                                if k != 'storage'}) for role in ('wrong-1', 'wrong-2', 'wrong-3')]
                arms[arm] = dict(storage=by_role['wrong-3']['arms'][arm]['storage'], before_repair=before,
                                 feedback_checkpoints=feedback,
                                 **by_role['repair']['arms'][arm], after_repair=repaired,
                                 replay=by_role['replay']['arms'][arm], after_replay=replayed,
                                 later_fact=by_role['later-fact']['arms'][arm],
                                 repair_recovery_lag_sessions=1 if repaired['task_success'] else None,
                                 benign_recovery_lag_sessions=0 if repaired['benign_old_task_success'] else None)
            paired = arms['forget']['storage'] == arms['retention']['storage']
            scenarios.append(dict(id=case, arms=arms,
                                  pair_constraints={'identical_persisted_initial_lessons': paired,
                                                    'same_task_budget_and_feedback': True},
                                  checkpoints=[{k: v for k, v in item.items() if k != 'arms'} for item in checkpoints]))
    rows = [case['arms']['forget'] for case in scenarios]
    metrics = dict(
        stored_target_controls=sum(c['arms']['retention']['storage']['target_persisted'] for c in scenarios),
        residual_target_exposures=sum(r['after_repair']['target_in_context'] for r in rows),
        reintroduction_exposures=sum(r['after_replay']['target_in_context'] for r in rows),
        repaired_task_successes=sum(r['after_repair']['task_success'] for r in rows),
        benign_preservation_failures=sum(not r['after_repair']['benign_old_task_success'] for r in rows),
        later_fact_relearned=sum(r['later_fact']['new_fact_in_context'] for r in rows),
        selective_repair_successes=sum(r['storage']['target_persisted']
                                      and not r['after_repair']['target_in_context']
                                      and r['after_repair']['task_success']
                                      and r['after_repair']['benign_old_task_success']
                                      and not r['after_replay']['target_in_context'] for r in rows))
    return dict(schema_version=1, evaluation='cross-process-memory-lifecycle-repair',
                backend='deterministic-cross-process-control', control=control, coordinator_pid=os.getpid(),
                interpretation='Fixed synthetic functional controls, not model robustness, an actual LLM agent, or official benchmark reproduction. Known copies are explicitly declared; no automatic semantic discovery. Wrong feedback is deliberately stored before repair. Source proofs establish provenance, not truth.',
                limits=dict(max_context_tokens=max_tokens, max_scenarios=2, arms_per_scenario=2,
                            fresh_processes_per_scenario=len(ROLES), max_worker_input_bytes=MAX_REQUEST_BYTES,
                            real_vault_input=False, network=False, external_models=False),
                core_paths=['capture', 'compile_units', 'lesson_delta', 'revocation', 'retrieve', 'workflow_execute'],
                metrics=metrics, scenarios=scenarios)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv == ['--_lifecycle-worker']:
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError('invalid_request')
            result = _worker(_decode(raw))
        else:
            class Parser(argparse.ArgumentParser):
                def error(self, message):
                    raise ValueError('invalid_options')
            parser = Parser(description=__doc__, allow_abbrev=False)
            parser.add_argument('--control', choices=CONTROLS, default='bounded')
            parser.add_argument('--max-tokens', type=int, default=1500)
            args = parser.parse_args(argv)
            result = evaluate_lifecycle(control=args.control, max_tokens=args.max_tokens)
        print(_json(result))
        return 0
    except Exception:
        # Operational/domain failures share a content-free boundary. Never echo
        # worker payloads, exception strings, paths, or stored observations.
        print(_json(ERROR))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
