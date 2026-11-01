import copy, importlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))

class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name).resolve()
        self.write('00-meta/vault-config.json', json.dumps({'required_keys':['title'],'enums':{},'deny_zones':['90-assets'],'exclude_dirs':['private']}))
        self.write('notes/owner.md', 'owner'); self.write('notes/source.md', 'evidence')
        self.decl = {'schema_version':1,'claims':[{'claim_id':'c1','note':'notes/owner.md','sources':['notes/source.md']}]}
        self.impact = importlib.import_module('vault_impact')
    def write(self, path, data):
        p = self.vault / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data, encoding='utf8'); return p
    def capture(self): return self.impact.capture_inventory(self.vault, self.decl)
    def test_wrong_vault_type_is_unavailable_without_api_traceback(self):
        baseline = self.capture()
        for vault in (None, 'missing', 42):
            with self.subTest(vault=vault):
                self.assertEqual(self.impact.capture_inventory(vault, self.decl)['status'], 'unavailable')
                self.assertEqual(self.impact.check_inventory(vault, baseline)['status'], 'unavailable')
    def test_current_and_drift(self):
        inv = self.capture(); result = self.impact.check_inventory(self.vault, inv)
        self.assertEqual(result['status'], 'current'); self.assertEqual(result['affected_notes'], [])
        self.assertEqual(result['claims'], [{'claim_id':'c1','note':'notes/owner.md','status':'current','note_status':'current','sources':[{'path':'notes/source.md','status':'current'}]}])
        self.write('notes/source.md', 'changed')
        result = self.impact.check_inventory(self.vault, inv)
        self.assertEqual(result['status'], 'review_required'); self.assertEqual(result['affected_notes'], ['notes/owner.md'])
        self.assertEqual(result['claims'][0]['sources'][0]['status'], 'changed')
        (self.vault / 'notes/source.md').unlink()
        self.assertEqual(self.impact.check_inventory(self.vault, inv)['claims'][0]['sources'][0]['status'], 'missing')
        self.write('notes/owner.md', 'changed owner')
        self.assertEqual(self.impact.check_inventory(self.vault, inv)['claims'][0]['note_status'], 'changed')
    def test_invalid_and_blocked(self):
        inv = self.capture(); bad = copy.deepcopy(inv); bad['claims'][0]['note']['sha256'] = 'Z'*64
        self.assertEqual(self.impact.check_inventory(self.vault, bad)['status'], 'unavailable')
        self.decl['claims'][0]['sources'] = ['../escape.md']
        self.assertEqual(self.impact.capture_inventory(self.vault, self.decl)['status'], 'unavailable')
        self.decl['claims'][0]['sources'] = ['90-assets/secret.md']
        self.assertEqual(self.impact.capture_inventory(self.vault, self.decl)['status'], 'unavailable')
        (self.vault / 'notes/source.md').unlink()
        try: (self.vault / 'notes/source.md').symlink_to(self.vault / 'notes/owner.md')
        except OSError: self.skipTest('symlink unavailable')
        self.assertEqual(self.impact.check_inventory(self.vault, inv)['claims'][0]['sources'][0]['status'], 'blocked')
    def test_cli_invalid_baseline(self):
        self.write('baseline.json', '{}')
        p = subprocess.run([sys.executable, str(SCRIPTS/'vault_impact.py'), '--vault', str(self.vault), 'check', '--input', 'baseline.json'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2); self.assertEqual(json.loads(p.stdout)['status'], 'unavailable')
    def test_reject_duplicate_ids_paths_and_empty(self):
        for mutate in ('empty', 'duplicate_id', 'duplicate_source'):
            data = copy.deepcopy(self.decl)
            if mutate == 'empty': data['claims'] = []
            elif mutate == 'duplicate_id': data['claims'].append(copy.deepcopy(data['claims'][0]))
            else: data['claims'][0]['sources'].append('notes/source.md')
            self.assertEqual(self.impact.capture_inventory(self.vault, data)['status'], 'unavailable')
    def test_config_and_source_drift_during_capture(self):
        from unittest.mock import patch
        original = self.impact._Policy.recheck
        for path in ('notes/source.md', '00-meta/vault-config.json'):
            def mutate(policy):
                with (self.vault / path).open('a', encoding='utf8') as stream: stream.write(' ')
                original(policy)
            with patch.object(self.impact._Policy, 'recheck', mutate):
                self.assertEqual(self.capture()['status'], 'unavailable')
            if path.startswith('notes'): self.write(path, 'evidence')
    def test_config_drift_after_capture_is_unavailable(self):
        inv = self.capture()
        with (self.vault / '00-meta/vault-config.json').open('a', encoding='utf8') as stream: stream.write(' ')
        self.assertEqual(self.impact.check_inventory(self.vault, inv)['status'], 'unavailable')
    def test_cli_success_review_malformed_and_oversize(self):
        def run(command, path):
            return subprocess.run([sys.executable, str(SCRIPTS/'vault_impact.py'), '--vault', str(self.vault), command, '--input', path], capture_output=True, text=True)
        self.write('decl.json', json.dumps(self.decl))
        capture = run('capture', 'decl.json'); self.assertEqual(capture.returncode, 0)
        self.write('baseline.json', capture.stdout)
        self.assertEqual(run('check', 'baseline.json').returncode, 0)
        self.write('notes/source.md', 'different')
        self.assertEqual(run('check', 'baseline.json').returncode, 1)
        for bad in ('', '{', 'x' * (65 * 1024)):
            self.write('baseline.json', bad)
            result = run('check', 'baseline.json'); self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['status'], 'unavailable')
    def test_capture_output_is_checkable_under_json_limit(self):
        long_dir = 'a' * 25
        owner = f'{long_dir}/owner.md'; self.write(owner, 'x')
        sources = [f'{long_dir}/{j}-source.md' for j in range(8)]
        for path in sources: self.write(path, 'x')
        claims = [{'claim_id':str(i), 'note':owner, 'sources':sources} for i in range(128)]
        # Shared guard may reject the file-count limit first; either outcome must round-trip.
        result = self.impact.capture_inventory(self.vault, {'schema_version':1,'claims':claims})
        if result.get('status') != 'unavailable':
            self.assertLessEqual(len(self.impact.encode(result)), 65536)
            self.assertEqual(self.impact.check_inventory(self.vault, result)['status'], 'current')
        # The stored CLI payload includes print's newline, not only JSON bytes.
        import contextlib, io
        from unittest.mock import patch
        budget = len(self.impact.encode(self.capture()))
        self.write('decl.json', json.dumps(self.decl))
        output = io.StringIO()
        with patch.object(self.impact, 'MAX_JSON_BYTES', budget), contextlib.redirect_stdout(output):
            self.impact.main(['--vault', str(self.vault), 'capture', '--input', 'decl.json'])
        self.assertLessEqual(len(output.getvalue().encode('ascii')), budget)
        # Text stdout translates LF to CRLF on Windows; inspect real bytes.
        command = ("import sys;sys.path.insert(0,sys.argv[1]);import vault_impact as m;"
                   "m.MAX_JSON_BYTES=int(sys.argv[2]);"
                   "raise SystemExit(m.main(['--vault',sys.argv[3],'capture','--input','decl.json']))")
        raw = subprocess.run([sys.executable, '-c', command, str(SCRIPTS), str(budget + 1), str(self.vault)], capture_output=True)
        self.assertIn(raw.returncode, (0, 2))
        self.assertLessEqual(len(raw.stdout), budget + 1)
        self.assertIsInstance(json.loads(raw.stdout), dict)
    def test_read_drift_is_unavailable(self):
        from unittest.mock import patch
        inv = self.capture(); original = self.impact._Policy.source
        def drift(policy, path):
            if path == 'notes/source.md': raise self.impact.EvidenceError('file_changed_during_read')
            return original(policy, path)
        with patch.object(self.impact._Policy, 'source', drift):
            self.assertEqual(self.impact.check_inventory(self.vault, inv)['status'], 'unavailable')
    def test_missing_source_restored_during_check_is_unavailable(self):
        from unittest.mock import patch
        inv = self.capture(); (self.vault / 'notes/source.md').unlink()
        original = self.impact._Policy.recheck
        def restore(policy):
            original(policy); self.write('notes/source.md', 'evidence')
        with patch.object(self.impact._Policy, 'recheck', restore):
            self.assertEqual(self.impact.check_inventory(self.vault, inv)['status'], 'unavailable')
