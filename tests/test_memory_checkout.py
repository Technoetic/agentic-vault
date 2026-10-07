"""Source-bound quote fixtures must remain usable after a real Windows checkout."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'skills/agentic-vault/scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0,str(SCRIPTS))
from vault_memory import compile_units


@unittest.skipUnless(shutil.which('git'), 'Git checkout integration requires git')
class MemoryCheckoutTests(unittest.TestCase):
    def test_autocrlf_checkout_preserves_literal_fixture_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            result=subprocess.run(['git','-c','core.autocrlf=true','checkout-index',
                                   '--prefix='+root.as_posix()+'/', '-f','--',
                                   'tests/fixtures/memory_units/robot.md',
                                   'tests/fixtures/memory_units/units.json'],
                                  cwd=ROOT,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
            fixture=root/'tests/fixtures/memory_units'
            vault=root/'vault'
            (vault/'00-meta').mkdir(parents=True)
            (vault/'00-meta/vault-config.json').write_text(json.dumps({'deny_zones':['private'],'exclude_dirs':['.git']}),encoding='utf-8')
            (vault/'20-knowledge').mkdir()
            (vault/'20-knowledge/robot.md').write_bytes((fixture/'robot.md').read_bytes())
            units=json.loads((fixture/'units.json').read_text(encoding='utf-8'))
            compiled=compile_units(vault,units,3000)
            self.assertEqual(len(compiled['units']),1,compiled['diagnostics'])
            self.assertTrue(compiled['context'])
