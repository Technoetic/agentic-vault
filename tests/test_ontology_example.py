"""Execute the published fictitious example rather than trusting guide prose."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'skills/agentic-vault/scripts/vault_ontology_cli.py'


class OntologyExampleTests(unittest.TestCase):
    def test_published_fixture_capture_ambiguity_preflight_and_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary).resolve() / 'vault'
            shutil.copytree(ROOT / 'tests/fixtures/ontology_contracts', vault)
            def run(*args):
                result = subprocess.run([sys.executable, str(CLI), '--vault', str(vault), *args],
                                        capture_output=True, timeout=15)
                return result, json.loads(result.stdout)
            capture, registry = run('capture', '--input', '00-meta/ontology/declarations.json')
            self.assertEqual(capture.returncode, 0, registry)
            (vault / '00-meta/ontology/registry.json').write_bytes(capture.stdout)
            check, data = run('check', '--registry', '00-meta/ontology/registry.json')
            self.assertEqual((check.returncode, data['status']), (0, 'current'))
            search, data = run('search-terms', '--registry', '00-meta/ontology/registry.json', '--text', 'production')
            self.assertEqual((search.returncode, data['status']), (1, 'ambiguous'))
            self.assertEqual(data['context'], [])
            self.assertEqual({x['id'] for x in data['matches']}, {'metric:good', 'metric:planned'})
            plan, data = run('check-answerable', '--registry', '00-meta/ontology/registry.json', '--input', 'complete-plan.json')
            self.assertEqual((plan.returncode, data['status']), (0, 'ready_for_evidence'))
            self.assertEqual({x['record']['id'] for x in data['context']},
                             {'machine:alpha', 'factory:one', 'located_in', 'link:alpha', 'metric:good'})
            missing = json.loads((vault / 'complete-plan.json').read_text(encoding='utf-8'))
            del missing['metrics'][0]['slots']['population']
            (vault / 'missing-plan.json').write_text(json.dumps(missing), encoding='utf-8')
            plan, data = run('check-answerable', '--registry', '00-meta/ontology/registry.json', '--input', 'missing-plan.json')
            self.assertEqual((plan.returncode, data['status']), (1, 'needs_clarification'))
            self.assertEqual(data['context'], [])
            with (vault / 'notes/definitions.md').open('a', encoding='utf-8') as f:
                f.write('A changed definition needs review.\n')
            stale, data = run('get-term', '--registry', '00-meta/ontology/registry.json', '--id', 'metric:good')
            self.assertEqual((stale.returncode, data['status']), (2, 'unavailable'))
            self.assertEqual(data['context'], [])


if __name__ == '__main__':
    unittest.main()
