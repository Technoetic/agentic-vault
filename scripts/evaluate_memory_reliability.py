#!/usr/bin/env python3
"""Exercise opt-in assembly and procedural recall in a fresh synthetic vault.

Native receipts contain measured synthetic operations and host observations.
This is a functional example, not model evaluation or a research reproduction.
No external command, model, network, user vault or proposed procedure executes.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'agentic-vault' / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts'))

PLAIN_PATH_LIMIT = 240  # the margin below Windows MAX_PATH (260) that vault_evidence.fs_path uses
# What the deepest entries of the synthetic vault, its run records, add to the path of the vault.
RUN_RECORD_LENGTH = len('/00-meta/.agentic-vault/runtime/run-') + 64 + len('-completed.json')


def _extended(path):
    """The Windows extended-length form of an absolute path; any other path, and any other system, unchanged."""
    text = str(path)
    if os.name != 'nt' or text.startswith('\\\\?\\'):
        return text
    if re.match(r'^[A-Za-z]:\\', text):
        return '\\\\?\\' + text
    if text.startswith('\\\\'):
        return '\\\\?\\UNC\\' + text[2:]
    return text


@contextmanager
def _temporary_vault():
    """A fresh temporary folder for the synthetic vault, removed afterwards.

    On Windows without long-path support the run records pass the plain path limit when the
    temporary folder is long: tempfile leaves them in place and fails on the folder that holds
    them. The folder is then removed through its extended-length form. Where no run record can
    reach the limit, and on other systems, a failed cleanup raises as before.
    """
    holder = tempfile.TemporaryDirectory(prefix='av-memory-reliability-')
    try:
        yield holder.name
    finally:
        try:
            holder.cleanup()
        except OSError:
            deep = _extended(holder.name)
            if deep == holder.name or len(holder.name) + RUN_RECORD_LENGTH < PLAIN_PATH_LIMIT:
                raise
            shutil.rmtree(deep)


def evaluate_reliability():
    import vault_assembly as assembly
    import vault_procedures as procedures
    import vault_runs as runs
    from evaluate_memory_workflows import execute

    cases = []
    fs_path = runs.evidence.fs_path  # a run record can lie beyond the plain Windows path limit
    with _temporary_vault() as temporary:
        vault = Path(temporary)

        def write(relative, content):
            path = vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if isinstance(content, str) else json.dumps(
                content, ensure_ascii=False, allow_nan=False), encoding='utf-8')

        write('00-meta/vault-config.json', {'deny_zones': ['private'], 'exclude_dirs': ['excluded']})
        columns = ['subject', 'relation', 'value', 'valid_from', 'valid_until',
                   'recorded', 'source', 'superseded_by', 'status', 'scope', 'polarity']
        row = ['Pump-A', 'inspection_interval', '30 days', '2026-01-01', '',
               '2026-01-02', '[[Inspection policy]]', '', 'confirmed', 'demo', 'positive']

        def ledger(rows):
            return ('# Synthetic ledger\n| ' + ' | '.join(columns) + ' |\n| '
                    + ' | '.join('---' for _ in columns) + ' |\n'
                    + ''.join('| ' + ' | '.join(values) + ' |\n' for values in rows))

        write('notes/ledger.md', ledger([row]))
        write('notes/Inspection policy.md', '# Fictional policy\nPump-A: inspect every 30 days.\n')
        store = runs.Store(vault)
        info, _ = store.fingerprint('notes/ledger.md', markdown=True)
        authority = [{'path': info['path'], 'sha256': info['sha256']}]
        request = {'version': 1, 'query': {'subject': 'Pump-A', 'relation': 'inspection_interval',
                   'scope': 'demo', 'as_of': '2026-10-22'}, 'sources': authority,
                   'candidates': [{'path': 'notes/ledger.md', 'line': 4}],
                   'limitations': ['Synthetic host-selected structured rows; no semantic inference.']}
        write('input/assembly.json', request)
        selected = assembly.assemble(vault, 'input/assembly.json', authorized_sources=authority)
        cases.append({'id': 'assembly_selected', 'passed': selected['status'] == 'selected'
                      and selected['value'] == '30 days', 'value': selected['value']})

        conflicting = list(row)
        conflicting[2] = '7 days'
        write('notes/ledger.md', ledger([row, conflicting]))
        info, _ = store.fingerprint('notes/ledger.md', markdown=True)
        authority = [{'path': info['path'], 'sha256': info['sha256']}]
        request['sources'] = authority
        request['candidates'].append({'path': 'notes/ledger.md', 'line': 5})
        write('input/assembly.json', request)
        conflict = assembly.assemble(vault, 'input/assembly.json', authorized_sources=authority)
        cases.append({'id': 'assembly_conflict', 'passed': conflict['status'] == 'abstain'
                      and 'conflicting_current_values' in conflict['reasons']})

        write('notes/startup.md', '# Synthetic startup candidate\nReset the lock.\nStart the machine.\n')
        write('notes/instructions.md', '# Synthetic task\nUse explicit preconditions.\n')
        fixture = {'version': 1, 'repetitions': 2, 'cases': [
            {'id': category, 'category': category, 'prompt': 'Synthetic startup ' + category,
             'assertions': [{'id': 'boundary', 'description': 'No unsupported actions', 'hard': True},
                            {'id': 'completed', 'description': 'Machine is running', 'hard': False}]}
            for category in sorted(runs.CATEGORIES)]}
        write('input/fixture.json', fixture)
        write('input/tools.json', {'version': 1, 'tools': []})
        prepared = runs.prepare(vault, 'notes/startup.md', ['notes/instructions.md'],
                                'input/fixture.json', 'input/tools.json', model='synthetic-control',
                                provider='offline', token_budget=1500)
        outcomes = []
        for case in fixture['cases']:
            for repetition in (1, 2):
                observation = execute('startup', ['reset_lock', 'start'], {'locked': True})
                evidence_path = 'output/' + case['id'] + '-' + str(repetition) + '.json'
                write(evidence_path, observation)
                outcomes.append({'case_id': case['id'], 'repetition': repetition, 'status': 'complete',
                    'assertions': [{'id': 'boundary', 'passed': not observation['unsafe_action_attempted']},
                                   {'id': 'completed', 'passed': observation['completed']}],
                    'evidence': [evidence_path]})
        write('input/observations.json', {'version': 1, 'run_id': prepared['id'],
              'manifest_sha256': prepared['id'], 'outcomes': outcomes})
        runs.record(vault, prepared['id'], 'input/observations.json')
        manifest = runs.inspect(vault, prepared['id'])['manifest']
        conditions = {'model': manifest['model'], 'provider': manifest['provider'],
                      'token_budget': manifest['token_budget'],
                      'tools_sha256': runs.evidence.digest(runs.evidence.encode(manifest['tools']))}
        procedure = {'id': 'startup', 'task_class': 'startup', 'cues': ['startup locked machine'],
            'preconditions': ['Synthetic state is locked; permission must be checked by the caller.'],
            'steps': [{'id': 'reset', 'instruction': 'Reset the lock.'},
                      {'id': 'start', 'instruction': 'Start the machine.'}],
            'edges': [{'before': 'reset', 'after': 'start'}], 'candidate': manifest['candidate'],
            'instructions': manifest['sources'], 'run_ids': [prepared['id']], 'conditions': conditions}
        write('input/procedures.json', {'version': 1, 'procedures': [procedure]})

        def lookup(query='startup locked machine', reuse_conditions=None):
            result = procedures.lookup(vault, 'input/procedures.json', 'startup', query,
                                       conditions=conditions if reuse_conditions is None else reuse_conditions)
            if not result['requires_review'] or result['applies_changes']:
                raise AssertionError('lookup must remain advisory and read-only')
            return [item['id'] for item in result['matches']]

        def inventory():
            return {p.relative_to(vault).as_posix(): fs_path(p).read_bytes()
                    for p in vault.rglob('*') if fs_path(p).is_file()}

        before = inventory()
        ids = lookup()
        cases.append({'id': 'procedure_match', 'passed': ids == ['startup'], 'ids': ids})
        no_match = lookup('orchid telescope')
        cases.append({'id': 'procedure_no_match', 'passed': no_match == [], 'ids': no_match})
        mismatched = lookup(reuse_conditions={**conditions, 'model': 'different-control'})
        cases.append({'id': 'procedure_conditions_mismatch', 'passed': mismatched == [], 'ids': mismatched})
        after = inventory()
        cases.append({'id': 'read_only_lookup', 'passed': before == after})
        write('notes/startup.md', '# Changed after observations\nStart without a reset.\n')
        stale = lookup()
        cases.append({'id': 'procedure_stale', 'passed': stale == [], 'ids': stale})
    return {'ok': all(case['passed'] for case in cases), 'version': 1,
            'backend': 'deterministic-synthetic', 'model_calls': 0, 'network_calls': 0,
            'temporary_vault_only': True, 'cases': cases,
            'interpretation': 'Synthetic functional checks, not measured model performance or research reproduction.'}


def main():
    try:
        report = evaluate_reliability()
    except Exception:
        print(json.dumps({'ok': False, 'error': 'synthetic_example_failed'}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
