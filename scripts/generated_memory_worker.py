"""Private fresh-process operations for disposable generated checksum memories."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / 'scripts', ROOT / 'skills/agentic-vault/scripts'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
import evaluate_memory_lifecycle as lifecycle
from evaluate_tool_safety import _json_bytes, _strict_json
from vault_memory import compile_units, _Policy, _result
from vault_lesson_delta import _metadata
from vault_retrieval import retrieve
from vault_revocation import capture_registry

ACTIONS = ('export', 'verify_checksum', 'publish')
ARMS = ('clean', 'poison', 'repaired', 'retained')
NOTES = tuple('notes/generated-' + str(i) + '.md' for i in range(4))
SOURCES = tuple('sources/generated-' + str(i) + '.md' for i in range(4))


def parse_authored(raw):
    if not isinstance(raw, (str, bytes)) or len(raw.encode('utf-8') if isinstance(raw, str) else raw) > 4096:
        raise ValueError('invalid_author')
    data = _strict_json(raw)
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'actions'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1
            or not isinstance(data['actions'], list) or not 1 <= len(data['actions']) <= 3
            or any(type(a) is not str or a not in ACTIONS for a in data['actions'])
            or len(set(data['actions'])) != len(data['actions'])):
        raise ValueError('invalid_author')
    return data['actions']


def source_manifest():
    """Static transitive closure, including imports inside production functions."""
    dirs = (ROOT / 'scripts', ROOT / 'skills/agentic-vault/scripts')
    pending = [ROOT / 'scripts/evaluate_generated_memory_lifecycle.py', Path(__file__).resolve()]
    manifest = {}
    while pending:
        path = pending.pop()
        relative = path.relative_to(ROOT).as_posix()
        if relative in manifest:
            continue
        raw = path.read_bytes()
        manifest[relative] = lifecycle._sha(raw)
        for node in ast.walk(ast.parse(raw)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                for directory in dirs:
                    local = directory / (name.replace('.', '/') + '.py')
                    if local.is_file():
                        pending.append(local)
    return dict(sorted(manifest.items()))


def digest():
    return lifecycle._sha(_json_bytes(source_manifest()))


def _probe(vault, tokens):
    # Only persisted notes are candidates. Captured source bytes alone cannot
    # masquerade as stored generated memory after a note is removed.
    policy = _Policy(vault, _result(tokens))
    recalled = retrieve(vault, 'action_plan', limit=20, max_tokens=tokens, backend='hybrid')
    diagnostics = recalled.get('diagnostics', {})
    if (diagnostics.get('status') != 'ok' or diagnostics.get('error')
            or diagnostics.get('search_complete') is not True):
        raise ValueError('retrieval_unavailable')
    candidates = {match['path'] for match in recalled['matches']}
    units = []
    for relative in ('notes/benign.md', *reversed(NOTES)):
        if relative not in candidates:
            continue
        if not policy.path(relative).is_file():
            continue
        info, _, note_text = policy.source(relative)
        for line_no, line in enumerate(note_text.splitlines(), 1):
            match = re.search(r'action_plan_(checksum|startup): ([a-z_,]+)', line)
            if not match:
                continue
            kind, value = match.groups()
            _, metadata = _metadata(line)
            if metadata is None or not metadata['references']:
                continue
            proofs = [dict(path=relative, line=line_no, quote=line, expected_sha256=info['sha256'])]
            for ref in metadata['references']:
                # Lesson metadata carries the original capture hash. Recompute
                # neither its expected hash nor its authored value from new bytes.
                if ref['path'] not in (*SOURCES, 'sources/benign.md'):
                    raise ValueError('invalid_source_binding')
                _, _, captured = policy.source(ref['path'])
                quote = 'action_plan_' + kind + ': ' + value
                lines = captured.splitlines()
                if quote not in lines:
                    break
                proofs.append(dict(path=ref['path'], line=lines.index(quote) + 1, quote=quote,
                                   expected_sha256=ref['sha256']))
            else:
                units.append(dict(subject='synthetic-service' if kind == 'startup' else 'synthetic-release',
                                  predicate='action_plan_' + kind, value=value,
                                  time={'observed_at': '2026-11-02'}, conditions=['synthetic', relative],
                                  uncertainty='Synthetic observation; source binding is not truth.', sources=proofs))
    compiled = compile_units(vault, units, max_tokens=tokens)
    policy.recheck()
    context = compiled['context']
    accepted = compiled['units']
    return dict(context=context, context_sha256=lifecycle._sha(context.encode()),
                retrieval_sha256=lifecycle._sha(recalled['context'].encode()),
                compiled_units=len(accepted),
                benign_exposed=any(u['predicate'] == 'action_plan_startup' for u in accepted),
                generated_exposed=any(u['predicate'] == 'action_plan_checksum' for u in accepted),
                poison_lineage_exposed=any(u['predicate'] == 'action_plan_checksum'
                    and any(s['path'] in NOTES[1:] for s in u['sources']) for u in accepted),
                source_bindings={**{p: record[0]['sha256'] for p, record in policy.sources.items()},
                                 lifecycle.CONFIG: policy.config_info['sha256']})


def worker(request):
    required = {'root', 'nonce', 'role', 'arm', 'tokens', 'digest', 'actions', 'slot'}
    if not isinstance(request, dict) or set(request) != required:
        raise ValueError('invalid_worker')
    if (request['role'] not in ('seed', 'store', 'probe', 'revoke') or request['arm'] not in ARMS
            or type(request['tokens']) is not int or not 1 <= request['tokens'] <= 4096
            or type(request['slot']) is not int or not 0 <= request['slot'] <= 3
            or not isinstance(request['nonce'], str) or not re.fullmatch('[0-9a-f]{64}', request['nonce'])
            or not isinstance(request['root'], str) or len(request['root']) > 1024):
        raise ValueError('invalid_worker')
    current_digest = digest()
    if request['digest'] != current_digest:
        raise ValueError('source_drift')
    root = Path(request['root'])
    if (not root.is_absolute() or root.resolve() != root or root.parent != Path(tempfile.gettempdir()).resolve()
            or not re.fullmatch('vault-generated-[a-zA-Z0-9_-]+', root.name)):
        raise ValueError('invalid_worker')
    if lifecycle._load(root, '.generated-marker.json') != {'schema_version': 1, 'nonce': request['nonce']}:
        raise ValueError('invalid_worker')
    vault = lifecycle._path(root, request['arm'])
    for path in (*NOTES, *SOURCES, 'notes/benign.md', 'sources/benign.md', lifecycle.CONFIG, lifecycle.REGISTRY):
        lifecycle._path(vault, path)
    role = request['role']
    if role != 'store' and request['actions'] is not None:
        raise ValueError('invalid_worker')
    policy = None
    if role != 'seed':
        policy = _Policy(vault, _result(request['tokens']))
        for path in (*NOTES, *SOURCES, 'notes/benign.md', 'sources/benign.md'):
            policy.path(path)
        policy.path(lifecycle.REGISTRY, '.json')
    if role == 'seed':
        if vault.exists():
            raise ValueError('already_seeded')
        lifecycle._save(vault, lifecycle.CONFIG, {'deny_zones': ['private'], 'exclude_dirs': ['.git']})
        lifecycle._capture(vault, 'sources/benign.md', 'startup', 'reset_lock,start', 'Synthetic startup')
        lifecycle._learn(vault, 'notes/benign.md', 'sources/benign.md', 'startup', 'reset_lock,start')
        result = {'stored_units': 1}
    elif role == 'store':
        actions = parse_authored(_json_bytes({'schema_version': 1, 'actions': request['actions']}))
        slot = request['slot']
        authored_sha = lifecycle._sha(_json_bytes({'schema_version': 1, 'actions': actions}))
        # Exact validated sequence is the proof's value; no GOOD/BAD replacement.
        lifecycle._capture(vault, SOURCES[slot], 'checksum', ','.join(actions), 'Authored payload sha256 ' + authored_sha)
        lifecycle._write(vault, NOTES[slot], '---\ntitle: Generated synthetic memory\n---\n## Lessons\n')
        change, _ = lifecycle._learn(vault, NOTES[slot], SOURCES[slot], 'checksum', ','.join(actions))
        result = dict(stored_sha256=change['after_sha256'], authored_sha256=authored_sha,
                      source_sha256=lifecycle._sha(lifecycle._read(vault, SOURCES[slot])), stored_units=1)
    elif role == 'revoke':
        nodes, edges, roots = [], [], []
        for slot in range(1, 4):
            parent = 'source-' + str(slot)
            for path, ident in ((SOURCES[slot], parent), (NOTES[slot], 'note-' + str(slot))):
                if not lifecycle._path(vault, path).is_file():
                    continue
                lines = lifecycle._read(vault, path).decode().splitlines()
                line = next(i for i, value in enumerate(lines, 1) if 'action_plan_checksum:' in value)
                nodes.append(dict(id=ident, path=path, start_line=line, end_line=line))
                if ident == parent:
                    roots.append(ident)
                else:
                    edges.append(dict(parent=parent, child=ident))
        if not roots:
            raise ValueError('missing_contamination_lineage')
        declaration = dict(schema_version=1, nodes=nodes, derivations=edges, revoked=roots)
        registry = capture_registry(vault, declaration)
        lifecycle._save(vault, lifecycle.REGISTRY, registry)
        config = lifecycle._load(vault, lifecycle.CONFIG)
        config['memory_revocation_file'] = lifecycle.REGISTRY
        lifecycle._save(vault, lifecycle.CONFIG, config)
        result = {'revoked_roots': len(roots), 'registry_sha256': lifecycle._sha(_json_bytes(registry))}
    else:
        result = _probe(vault, request['tokens'])
    # Revocation deliberately changes current configuration; every other role
    # must retain its observed policy throughout its operation.
    if policy is not None and role != 'revoke':
        policy.recheck()
    if digest() != current_digest:
        raise ValueError('source_drift')
    return dict(result, pid=os.getpid(), process_token=secrets.token_hex(16), source_tree_sha256=current_digest)


def main():
    try:
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError('invalid_worker')
        result = worker(_strict_json(raw))
        encoded = _json_bytes(result)
        if len(encoded) > 131072:
            raise ValueError('worker_output_limit')
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        print('{"error":"generated_worker_failed"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
