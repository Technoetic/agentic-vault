#!/usr/bin/env python3
"""Offline original synthetic lifecycle: real readers, simulated memory sink.

No persistent index/outbox, model call, network, or production vault is used.
The negative control deliberately reads an unfiltered direct prefix.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import jarvis_bridge as bridge
import vault_judge as judge
from vault_evidence import EvidenceError, decode, encode
from vault_healthcheck import HealthcheckError
from vault_memory import compile_units
from vault_recall import recall

PACK = ROOT / 'tests/fixtures/revocation_egress/pack.json'
SCOPE = 'fresh_actual_reads_rebuilt_context_and_simulated_sink_only'
REQUIRED_CONSUMERS = frozenset(('vault_recall.recall', 'vault_memory.compile_units',
    'jarvis_bridge._generation_envelope', 'simulated_memory_sink', 'vault_judge._prepare'))
KNOWN_CONSUMERS = REQUIRED_CONSUMERS | {'negative_control_unfiltered_prefix'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_pack():
    raw = PACK.read_bytes()
    if len(raw) > 64 * 1024:
        raise ValueError('fixture limit')
    return decode(raw), sha(raw)


class SyntheticVault:
    """Only repository-authored synthetic bytes in a caller's temporary directory."""
    def __init__(self, root):
        self.root = Path(root)
        self.pack, self.pack_sha256 = load_pack()
        self.config = {'deny_zones': ['private'], 'exclude_dirs': [],
                       'hot_note': '00-meta/hot.md', 'handoff_note': '00-meta/handoff.md',
                       'log_note': '00-meta/log.md', 'hot_max_tokens': 1000,
                       'handoff_max_tokens': 1000,
                       'memory_revocation_file': '00-meta/revocations.json'}
        self.registry = {'schema_version': 1, 'nodes': [],
                         'derivations': self.pack['derivations'], 'revoked': []}
        for source in self.pack['sources']:
            raw = b'\xef\xbb\xbf' + ('\r\n'.join(source['lines']) + '\r\n').encode('utf-8')
            self.write(source['path'], raw)
            self.registry['nodes'].append({key: source[key] for key in
                ('id', 'path', 'start_line', 'end_line')})
        self.write(self.pack['undeclared']['path'], self.pack['undeclared']['text'].encode('utf-8'))
        self.write_config()
        self.refresh_bindings()

    def write(self, relative, raw):
        # The fixed public pack is code-owned. Still forbid path escape here.
        parts = relative.replace('\\', '/').split('/')
        if (not parts or any(part in ('', '.', '..') or ':' in part for part in parts)
                or relative.startswith('/') or parts[0] == '.git'):
            raise ValueError('unsafe fixture path')
        target = self.root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    def write_config(self):
        self.write('00-meta/vault-config.json', encode(self.config))

    def write_registry(self):
        self.write('00-meta/revocations.json', encode(self.registry))

    def refresh_bindings(self):
        # An authored synthetic recapture, not automatic approval of real edits.
        for node in self.registry['nodes']:
            node['sha256'] = sha((self.root / node['path']).read_bytes())
        self.write_registry()

    def revoke(self):
        self.registry['revoked'] = ['root']
        self.write_registry()

    @property
    def markers(self):
        return [marker for source in self.pack['sources'] for marker in source['markers']]

    def unit(self, *, poison=False):
        source = self.pack['sources'][0]
        line = 2 if poison else 3
        quote = source['lines'][line - 1]
        return {'subject': 'Fixture', 'predicate': 'checkpoint', 'value': quote,
                'time': {'valid_from': '2026-11-09'}, 'conditions': [], 'uncertainty': 'reported',
                # compile_units binds physical LF-separated lines, including
                # the CR byte of this deliberately CRLF fixture.
                'sources': [{'path': source['path'], 'line': line, 'quote': quote + '\r'}]}

    def request(self, *, poison=False):
        return {'schema_version': 1, 'task': 'support', 'sources': [{
            'path': '20-knowledge/source.md',
            'excerpt': 'POISON_ROOT fixture instruction' if poison else 'BENIGN_ROOT checkpoint'}],
            'questions': [{'id': 'fixture', 'instructions': 'Is this fixture supported?',
                'choices': {'yes': 'Supported', 'unknown': 'Insufficient evidence'}, 'abstain': 'unknown'}]}


def assess_observations(observations):
    """Unavailable/missing evidence never earns a useful masking success."""
    required = {'outcome', 'consumer', 'poison_visible', 'benign_visible', 'output_sha256'}
    valid = [row for row in observations if isinstance(row, dict) and required <= row.keys()
             and row['outcome'] == 'observed' and type(row['poison_visible']) is bool
             and type(row['benign_visible']) is bool and row['consumer'] in KNOWN_CONSUMERS
             and type(row['output_sha256']) is str
             and re.fullmatch('[0-9a-f]{64}', row['output_sha256'])]
    residual = sum(row['poison_visible'] for row in valid)
    retained = sum(row['benign_visible'] for row in valid)
    consumers = [row['consumer'] for row in valid]
    complete = REQUIRED_CONSUMERS <= set(consumers) and len(consumers) == len(set(consumers))
    return {'observed_count': len(valid), 'unavailable_count': len(observations) - len(valid),
            'residual_count': residual, 'benign_retained_count': retained,
            'required_consumers_observed': complete,
            'safe_useful': complete and bool(valid) and len(valid) == len(observations)
                           and residual == 0 and retained == len(valid)}


def observe(fixture, consumer, text, *, phase='after_revoke', outcome='observed'):
    return {'phase': phase, 'session': 2 if phase == 'after_revoke' else 1,
            'consumer': consumer, 'outcome': outcome,
            'poison_visible': any(marker in text for marker in fixture.markers),
            'benign_visible': 'BENIGN_' in text,
            'registry_sha256': sha((fixture.root / '00-meta/revocations.json').read_bytes()),
            'sources': [{'path': n['path'], 'sha256': n['sha256']} for n in fixture.registry['nodes']],
            'output_sha256': sha(text.encode('utf-8'))}


def evaluate_revocation_egress(*, negative_control=False):
    with tempfile.TemporaryDirectory() as temporary:
        fixture = SyntheticVault(temporary)
        config = {'_include_activity': True}
        observations, simulated_sink = [], []
        before = bridge._generation_envelope(fixture.root, config, 'BENIGN')
        before.recheck()
        simulated_sink.append(before.payload)
        _before_meta, before_jev = judge._prepare(fixture.root, fixture.request(poison=True))
        baseline_observations = [observe(fixture, 'jarvis_bridge._generation_envelope',
            before.payload, phase='before_revoke'), observe(fixture, 'vault_judge._prepare',
            '\n'.join(before_jev[2]['evidence']), phase='before_revoke')]
        historical_digest = sha(simulated_sink[0].encode('utf-8'))
        fixture.revoke()
        stale_refused = False
        try:
            before.recheck()
        except (ValueError, EvidenceError, OSError, HealthcheckError):
            stale_refused = True
        rebuilt = recall(fixture.root, 'BENIGN POISON_ROOT DERIVED_HOT FRAGMENT_ONE DERIVED_LOG')
        observations.append(observe(fixture, 'vault_recall.recall', rebuilt['context'],
            outcome='observed' if rebuilt['diagnostics']['status'] == 'ok' else 'unavailable'))
        compiled = compile_units(fixture.root, [fixture.unit(poison=True), fixture.unit()])
        observations.append(observe(fixture, 'vault_memory.compile_units', compiled['context']))
        current = bridge._generation_envelope(fixture.root, config, 'BENIGN')
        observations.append(observe(fixture, 'jarvis_bridge._generation_envelope', current.payload))
        current.recheck()
        simulated_sink.append(current.payload)
        observations.append(observe(fixture, 'simulated_memory_sink', simulated_sink[-1]))
        prepared, context = judge._prepare(fixture.root, fixture.request())
        observations.append(observe(fixture, 'vault_judge._prepare', '\n'.join(context[2]['evidence']),
            outcome='observed' if prepared['status'] == 'prepared' else 'unavailable'))
        poison_result = judge.prepare(fixture.root, fixture.request(poison=True))
        poison_refused = (poison_result['status'] == 'unverified'
                          and poison_result.get('error') == 'excerpt_not_found')
        outside = recall(fixture.root, 'UNDECLARED_COPY')
        outside_visible = 'UNDECLARED_COPY' in outside['context']
        if negative_control:
            raw_prefix, _truncated = bridge._read_context_prefix(fixture.root / '00-meta/hot.md')
            observations.append(observe(fixture, 'negative_control_unfiltered_prefix', raw_prefix))
        assessment = assess_observations(observations)
        checks = (assessment['safe_useful'] and stale_refused and poison_refused and outside_visible
                  and all(row['poison_visible'] for row in baseline_observations)
                  and historical_digest == sha(simulated_sink[0].encode('utf-8')))
        return {'schema_version': 1, 'status': 'passed' if checks else 'failed', 'scope': SCOPE,
                'fixture_sha256': fixture.pack_sha256, 'assessment': assessment,
                'stale_envelope_refused': stale_refused, 'revoked_jev_excerpt_refused': poison_refused,
                'previous_simulated_send_retained': historical_digest == sha(simulated_sink[0].encode('utf-8')),
                'undeclared_copy_visible_outside_scope': outside_visible,
                'simulated_send_count': len(simulated_sink), 'observations': observations,
                'baseline_observations': baseline_observations,
                'trace_sha256': sha(encode(baseline_observations + observations)),
                'limits': ['No real network, model, database or persistent index/outbox.',
                           'Declared lexical spans only; no automatic semantic descendant discovery.',
                           'Earlier simulated sends remain present; no retroactive withdrawal.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative-control', action='store_true')
    options = parser.parse_args(argv)
    report = evaluate_revocation_egress(negative_control=options.negative_control)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
