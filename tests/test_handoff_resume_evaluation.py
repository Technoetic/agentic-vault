import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/evaluate_handoff_resume.py'


class HandoffResumeTests(unittest.TestCase):
    def cli(self, *args, status=0):
        run = subprocess.run([sys.executable, '-B', str(SCRIPT), *map(str, args)],
                             capture_output=True, encoding='utf-8')
        self.assertEqual(run.returncode, status, run.stderr or run.stdout)
        return json.loads(run.stdout)

    def test_demo_detects_both_bad_handoffs_with_a_fresh_resumer(self):
        report = self.cli('demo')
        self.assertEqual(report['evidence'], 'scripted_subprocesses')
        self.assertTrue(report['passed'])
        self.assertEqual(report['process_stages'], ['uninterrupted', 'checkpoint', 'interrupt',
                                                   'resume', 'resume', 'resume'])
        self.assertTrue(report['uninterrupted']['passed'])
        self.assertTrue(report['interruption']['passed'])
        controls = report['resumed']
        self.assertTrue(controls['correct']['passed'])
        self.assertEqual(controls['correct'], report['uninterrupted'])
        self.assertFalse(controls['wrong_task']['checks']['task'])
        self.assertTrue(controls['stale_source']['checks']['task'])
        self.assertFalse(controls['stale_source']['checks']['source'])
        self.assertFalse(controls['stale_source']['checks']['total'])

    def test_independent_artifacts_score_by_contents_and_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            pair = Path(tmp) / 'pair'
            self.cli('prepare', pair)
            for name in ('a-r1.json', 'a-r2.json', 'b-r2.json'):
                self.assertEqual((pair / 'uninterrupted' / name).read_bytes(),
                                 (pair / 'resumed' / name).read_bytes())
            focus = {'task': 'batch-a', 'source': 'a-r2.json',
                     'accepted_ids': ['a17', 'a31'], 'total': 12}
            other = {'task': 'batch-b', 'source': 'b-r2.json',
                     'accepted_ids': ['b11', 'b19'], 'total': 11}
            for arm in ('uninterrupted', 'resumed'):
                (pair / arm / 'focus-result.json').write_text(json.dumps(focus), encoding='utf-8')
            absent = self.cli('score', pair, status=1)
            self.assertEqual(absent['interruption']['status'], 'missing')
            self.assertFalse(absent['passed'])
            result_path = pair / 'resumed' / 'interrupt-result.json'
            result_path.write_text(json.dumps(other), encoding='utf-8')
            report = self.cli('score', pair, '--resumed-seconds', '34.25')
            self.assertEqual(report['evidence'], 'externally_supplied_artifacts')
            self.assertTrue(report['passed'])
            self.assertEqual(report['reported_seconds'], {'uninterrupted': None, 'resumed': 34.25})
            output = pair / 'resumed' / 'focus-result.json'
            output.write_text(json.dumps(dict(focus, total=11)), encoding='utf-8')
            bad = self.cli('score', pair, status=1)
            self.assertTrue(bad['resumed']['checks']['source'])
            self.assertFalse(bad['resumed']['checks']['total'])
            output.write_text('{', encoding='utf-8')
            self.assertEqual(self.cli('score', pair, status=1)['resumed']['status'], 'invalid')

    def test_prepare_never_overwrites_and_time_must_be_finite(self):
        with tempfile.TemporaryDirectory() as tmp:
            pair = Path(tmp) / 'pair'
            self.cli('prepare', pair)
            kept = pair / 'resumed' / 'focus-result.json'
            kept.write_text('original', encoding='utf-8')
            self.cli('prepare', pair, status=1)
            self.assertEqual(kept.read_text(encoding='utf-8'), 'original')
            run = subprocess.run([sys.executable, '-B', str(SCRIPT), 'score', str(pair),
                                  '--uninterrupted-seconds', 'nan'], capture_output=True)
            self.assertEqual(run.returncode, 2)
