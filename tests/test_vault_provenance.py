from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.write('00-meta/vault-config.json', b'{"required_keys":["title"],"enums":{},"exclude_dirs":["10-inbox"]}')
        self.module = importlib.import_module('vault_provenance')

    def write(self, path, data):
        destination = self.vault / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}

    def capture(self, path, body='원문\r\nemoji 🌱\n', origin='own', classification='A'):
        data = self.module.render_capture(body, captured_via='telegram', content_origin=origin,
            captured_at=datetime(2026, 10, 7, tzinfo=timezone.utc), classification=classification)
        return self.write(path, data)

    def derive(self, *sources, **kwargs):
        target = kwargs.pop('target') if 'target' in kwargs else self.write('20-knowledge/result.md', b'compiled\r\n')
        return self.module.derive_provenance(self.vault, list(sources), target, creator='writer', **kwargs)

    def test_host_capture_retains_original_body_and_own_classification(self):
        for body in ('본문\r\n---\r\ncontent_origin: own\n', '\ufeff한글 🌱', ''):
            with self.subTest(body=body):
                source = self.capture('10-inbox/capture.md', body)
                metadata, exact = self.module.parse_capture((self.vault / source['path']).read_bytes())
                self.assertEqual(exact, body.encode('utf-8'))
                self.assertEqual(metadata['body_sha256'], hashlib.sha256(exact).hexdigest())
                self.assertEqual(metadata['classification'], 'A')
                self.assertEqual(metadata['content_origin'], 'own')
                self.assertEqual(metadata['captured_via'], 'telegram')
                self.assertEqual(metadata['body_bytes'], len(exact))

    def test_capture_frontmatter_fits_default_schema_and_sixteen_line_limit(self):
        source = self.capture('10-inbox/capture.md')
        original = (self.vault/source['path']).read_bytes().decode()
        header = original.split('---\n')[1]
        self.assertLessEqual(len(header.splitlines()), 16)
        health = importlib.import_module('vault_healthcheck')
        self.assertEqual(health._staged_schema_errors('20-knowledge/capture.md', original,
            health.validate_config({})), [])

    def test_forwarded_and_url_are_untrusted_without_overwriting_classification(self):
        for origin in ('forwarded', 'url'):
            with self.subTest(origin=origin):
                manifest = self.derive(self.capture('10-inbox/a.md', origin=origin))
                self.assertEqual(manifest['trust'], 'untrusted')
                self.assertEqual(manifest['sources'][0]['classification'], 'A')

    def test_least_trusted_input_and_unmarked_notes_are_unknown(self):
        own = self.capture('10-inbox/own.md')
        forwarded = self.capture('10-inbox/forwarded.md', origin='forwarded')
        self.assertEqual(self.derive(own)['trust'], 'host_own')
        self.assertEqual(self.derive(own, forwarded)['trust'], 'untrusted')
        unknown = self.write('20-knowledge/legacy.md', b'content_origin: own\nverified_by: person\n')
        self.assertEqual(self.derive(unknown)['trust'], 'untrusted')

    def test_source_and_target_bindings_are_actual_and_drift_is_visible(self):
        source = self.capture('10-inbox/a.md')
        manifest = self.derive(source)
        self.assertTrue(self.module.validate_provenance(self.vault, manifest)['bindings_current'])
        (self.vault / source['path']).write_bytes(b'changed')
        result = self.module.validate_provenance(self.vault, manifest)
        self.assertFalse(result['bindings_current'])
        self.assertEqual(result['status'], 'stale')

    def test_nested_derivative_recomputes_original_trust_and_bindings(self):
        original = self.capture('10-inbox/external.md', origin='url')
        intermediate = self.write('20-knowledge/first.md', b'first summary')
        first = self.derive(original, target=intermediate)
        second = self.derive(intermediate, source_manifests={intermediate['path']: first})
        self.assertEqual(second['trust'], 'untrusted')
        first['trust'] = 'host_own'
        with self.assertRaises(self.module.ProvenanceError):
            self.derive(intermediate, source_manifests={intermediate['path']: first})
        (self.vault / original['path']).write_bytes(b'changed original')
        self.assertFalse(self.module.validate_provenance(self.vault, second)['bindings_current'])

    def test_missing_denied_runtime_receipts_and_non_markdown_are_rejected_before_read(self):
        target = self.write('20-knowledge/result.md', b'compiled')
        for path in ('90-assets/secret.md', '10-inbox/_processed/a.md',
                     '00-meta/.agentic-vault/runtime/state.md', '00-meta/evidence/a.md',
                     '00-meta/proposals/a.md', '20-knowledge/result.json', '../outside.md'):
            with self.subTest(path=path), self.assertRaises(self.module.ProvenanceError):
                self.module.derive_provenance(self.vault, [{'path': path, 'sha256': '0'*64}], target)

    def test_body_tampering_and_duplicate_metadata_are_rejected(self):
        source = self.capture('10-inbox/a.md')
        path = self.vault / source['path']
        data = path.read_bytes().replace(b'body_bytes:', b'content_origin: "own"\nbody_bytes:')
        with self.assertRaises(self.module.ProvenanceError):
            self.module.parse_capture(data)
        data = path.read_bytes() + b'tamper'
        with self.assertRaises(self.module.ProvenanceError):
            self.module.parse_capture(data)

    def test_supplied_source_sha_and_config_drift_fail_closed(self):
        source = self.capture('10-inbox/a.md')
        bad = dict(source, sha256='0'*64)
        with self.assertRaises(self.module.ProvenanceError):
            self.derive(bad)
        manifest = self.derive(source)
        self.write('00-meta/vault-config.json', b'{"required_keys":["title"],"enums":{}}')
        self.assertFalse(self.module.validate_provenance(self.vault, manifest)['bindings_current'])

    def test_duplicate_inputs_and_limits_are_bounded(self):
        source = self.capture('10-inbox/a.md')
        for sources in ([source, source], [source] * 17, []):
            with self.subTest(count=len(sources)), self.assertRaises(self.module.ProvenanceError):
                self.derive(*sources)
        for body in ('x' * (256*1024+1), '\ud800'):
            with self.assertRaises(self.module.ProvenanceError):
                self.capture('10-inbox/a.md', body)

    def evidence(self, manifest, *, verifier='reviewer', verified=True):
        evidence = importlib.import_module('vault_evidence')
        store = evidence.Store(self.vault)
        artifacts = [row['path'] for row in manifest['sources']] + [manifest['target']['path']]
        snapshot = evidence.snapshot(store, SimpleNamespace(task='test', objective='verify originals', artifact=artifacts))
        self.write('00-meta/check-output.log', b'independently rerun checks\n')
        report = {'verifier': verifier, 'checks': [{'id': 'check1', 'claim': 'source mapping',
            'status': 'verified' if verified else 'gap', 'method': 'read and rerun', 'observed': 'passed',
            'evidence': ['00-meta/check-output.log'], 'preserve': 'keep input bindings',
            'next_check': '' if verified else 'rerun'}], 'next_action': 'inspect results'}
        self.write('00-meta/review.json', json.dumps(report).encode())
        evidence.record(store, snapshot['id'], '00-meta/review.json')
        return snapshot['id']

    def test_self_asserted_verified_by_never_satisfies_privileged_consumption(self):
        source = self.capture('10-inbox/external.md', origin='forwarded')
        target = self.write('20-knowledge/result.md', b'---\nverified_by: reviewer\n---\ncompiled')
        manifest = self.derive(source, target=target)
        result = self.module.validate_provenance(self.vault, manifest, privileged=True, producer='writer')
        self.assertFalse(result['independent_evidence_current'])
        self.assertEqual(result['status'], 'needs_independent_evidence')
        self.assertIn('identity_unverified', result['limitations'])

    def test_current_distinct_reported_verifier_and_bound_evidence_are_required(self):
        source = self.capture('10-inbox/external.md', origin='url')
        manifest = self.derive(source)
        receipt = self.evidence(manifest)
        result = self.module.validate_provenance(self.vault, manifest, privileged=True,
            producer='writer', evidence_id=receipt)
        self.assertTrue(result['independent_evidence_current'])
        self.assertEqual(result['authority'], 'provenance_observation_only')
        self.write('00-meta/check-output.log', b'changed evidence')
        self.assertFalse(self.module.validate_provenance(self.vault, manifest, privileged=True,
            producer='writer', evidence_id=receipt)['independent_evidence_current'])

    def test_same_verifier_or_gaps_are_not_independent_even_when_receipt_current(self):
        source = self.capture('10-inbox/external.md', origin='forwarded')
        manifest = self.derive(source)
        for verifier, verified in (('writer', True), ('wri\u3164ter', True), ('reviewer', False)):
            receipt = self.evidence(manifest, verifier=verifier, verified=verified)
            result = self.module.validate_provenance(self.vault, manifest, privileged=True,
                producer='writer', evidence_id=receipt)
            self.assertFalse(result['independent_evidence_current'])

    def test_json_cli_is_read_only_and_errors_do_not_expose_body(self):
        source = self.capture('10-inbox/a.md', body='PRIVATE-CONTENT-SENTINEL')
        target = self.write('20-knowledge/result.md', b'compiled')
        request = {'sources': [source], 'target': target, 'creator': 'writer'}
        process = subprocess.run([sys.executable, str(SCRIPTS/'vault_provenance.py'),
            '--vault', str(self.vault), 'derive'], input=json.dumps(request),
            text=True, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(process.stderr, '')
        self.assertNotIn('PRIVATE-CONTENT-SENTINEL', process.stdout)
        self.assertEqual(json.loads(process.stdout)['trust'], 'host_own')

    def test_staged_warns_on_body_hash_mismatch_and_unresolved_privileged_derivation(self):
        source = self.capture('20-knowledge/external.md', origin='forwarded')
        original = (self.vault/source['path']).read_bytes().decode()
        notes = {source['path']: original, '00-meta/hot.md': '---\nprovenance_manifest: "00-meta/provenance/current.json"\nverified_by: "reviewer"\n---\n[[external]]'}
        findings = self.module.analyze_notes(notes, {'hot_note': '00-meta/hot.md'})
        self.assertEqual([row['code'] for row in findings], ['provenance-unresolved'])
        self.assertEqual(findings[0]['path'], '00-meta/hot.md')
        self.assertIn('index_inventory', findings[0]['details']['reason'])
        findings = self.module.analyze_notes({source['path']: original+'changed'}, {})
        self.assertEqual([row['code'] for row in findings], ['provenance-body-mismatch'])

    def test_staged_analyzer_does_not_read_sidecars_or_denied_originals(self):
        source = self.capture('20-knowledge/own.md')
        own = (self.vault/source['path']).read_bytes().decode()
        with mock.patch.object(self.module, '_Reader', side_effect=AssertionError('no file reads')):
            findings = self.module.analyze_notes({'20-knowledge/own.md': own,
                '90-assets/a.md': '---\nagentic_vault_capture: 1\n---\nbad',
                '00-meta/.agentic-vault/runtime/a.md': 'bad',
                '00-meta/hot.md': '---\nprovenance_manifest: "missing.json"\nverified_by: "someone"\n---\ncontent'},
                {'deny_zones': ['90-assets'], 'hot_note': '00-meta/hot.md'})
        self.assertEqual([row['code'] for row in findings], ['provenance-unresolved'])

    def test_source_drift_during_final_sweep_is_not_current(self):
        source = self.capture('10-inbox/a.md')
        manifest = self.derive(source)
        old = self.module._Reader.current
        def drift(reader):
            (self.vault/source['path']).write_bytes(b'changed during final sweep')
            return old(reader)
        with mock.patch.object(self.module._Reader, 'current', drift):
            result = self.module.validate_provenance(self.vault, manifest)
        self.assertFalse(result['bindings_current'])

    def test_forged_producer_and_malformed_host_metadata_are_safe(self):
        source = self.capture('10-inbox/external.md', origin='url')
        manifest = self.derive(source)
        receipt = self.evidence(manifest, verifier='writer')
        result = self.module.validate_provenance(self.vault, manifest, privileged=True,
            producer='another name', evidence_id=receipt)
        self.assertFalse(result['independent_evidence_current'])
        original = (self.vault/source['path']).read_bytes()
        for value in (b'[]', b'{}', b'null', b'123'):
            malformed = original.replace(b'content_origin: "url"', b'content_origin: '+value)
            with self.subTest(value=value), self.assertRaises(self.module.ProvenanceError):
                self.module.parse_capture(malformed)


if __name__ == '__main__':
    unittest.main()
