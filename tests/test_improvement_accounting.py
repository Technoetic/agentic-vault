from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    from _winfs import PLAIN_PATH_LIMIT, extended
except ImportError:  # run as tests.<module> from the repository root
    from tests._winfs import PLAIN_PATH_LIMIT, extended

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
ENGINE = ROOT / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    example = importlib.import_module('evaluate_improvement_accounting')
except ModuleNotFoundError:
    example = None


class ImprovementAccountingIntegrationTests(unittest.TestCase):
    def test_four_arm_example_retains_costs_unknowns_and_evaluator_guard(self):
        self.assertIsNotNone(example, 'improvement accounting demonstration is missing')
        result = example.evaluate()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['native_runs'], 4)
        self.assertEqual(result['cost_receipts'], 4)
        self.assertTrue(result['shared_evaluator'])
        self.assertTrue(result['discarded_attempt_retained'])
        self.assertTrue(result['unknown_cost_is_null'])
        self.assertTrue(result['evaluator_mutation_rejected'])
        self.assertTrue(result['complete_cost_comparison'])
        self.assertTrue(result['incomplete_cost_comparison_withheld'])
        self.assertTrue(result['requires_review'])
        self.assertFalse(result['applies_changes'])
        self.assertEqual(result['measurements'], 'synthetic_host_reports_only')


class TemporaryParentTests(unittest.TestCase):
    """The examples keep tempfile's default folder unless their deepest receipt reaches the plain path limit."""

    @classmethod
    def setUpClass(cls):
        cls.experiments = importlib.import_module('evaluate_improvement_experiments')
        if str(ENGINE) not in sys.path:
            sys.path.insert(0, str(ENGINE))
        cls.evidence = importlib.import_module('vault_evidence')
        runtime_dir = importlib.import_module('vault_runs').RUNTIME_DIR
        cls.cases = {
            'experiments': cls.experiments.deepest_receipt(runtime_dir, 'av-improvement-example-' + 8 * '0'),
            'accounting': cls.experiments.deepest_receipt(runtime_dir, 'tmp' + 8 * '0', 'vault'),
        }

    def chosen(self, length, below):
        """A temporary folder path of that length and the `dir` an example chooses for it."""
        root = 'C:\\' if os.name == 'nt' else '/'
        folder = root + 'x' * (length - len(root))
        with mock.patch.object(self.experiments.tempfile, 'gettempdir', return_value=folder):
            return folder, self.experiments.temporary_parent(self.evidence, below)

    def test_the_deepest_receipt_is_an_improvement_record_in_the_runtime_folder(self):
        for name, below in self.cases.items():
            with self.subTest(example=name):
                self.assertEqual(below.name, 'improvement-record-' + 64 * '0' + '.json')
                self.assertEqual(below.parent.as_posix().split('/')[-3:], ['00-meta', '.agentic-vault', 'runtime'])

    def test_the_default_folder_is_kept_while_the_deepest_receipt_stays_below_the_plain_limit(self):
        for name, below in self.cases.items():
            needed = 1 + len(str(below))
            for length in (33, PLAIN_PATH_LIMIT - needed - 1):
                with self.subTest(example=name, length=length):
                    self.assertIsNone(self.chosen(length, below)[1])

    def test_the_extended_length_form_is_chosen_at_the_plain_limit_and_only_on_windows(self):
        for name, below in self.cases.items():
            needed = 1 + len(str(below))
            for length in (PLAIN_PATH_LIMIT - needed, 150):
                with self.subTest(example=name, length=length):
                    folder, chosen = self.chosen(length, below)
                    self.assertEqual(chosen, str(extended(folder)) if os.name == 'nt' else None)


class LongTemporaryFolderTests(unittest.TestCase):
    """The accounting example ends with exit code 0 as a command under a temporary folder that puts its
    receipts beyond the plain Windows path limit, and leaves nothing behind in it. TemporaryParentTests pins
    the folder decision that both improvement examples share."""

    FOLDER_LENGTH = 170

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix='av-impr-')).resolve()
        self.addCleanup(lambda: shutil.rmtree(extended(base), ignore_errors=True))
        padding = self.FOLDER_LENGTH - len(str(base)) - 1
        self.folder = base / ('t' * max(padding, 1))
        self.folder.mkdir()

    def run_example(self, name):
        environment = dict(os.environ, TEMP=str(self.folder), TMP=str(self.folder), TMPDIR=str(self.folder))
        return subprocess.run([sys.executable, '-X', 'utf8', '-B', str(SCRIPTS / name)], env=environment,
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)

    def test_the_accounting_example_passes_and_leaves_the_temporary_folder_empty(self):
        # One example as a command keeps the cost of this test low on the CI runners; the decision of
        # temporary_parent that both examples use is pinned by TemporaryParentTests.
        self.assertGreaterEqual(len(str(self.folder)), self.FOLDER_LENGTH)
        # tempfile skips a folder it cannot use: prove that a child with this environment uses this one.
        environment = dict(os.environ, TEMP=str(self.folder), TMP=str(self.folder), TMPDIR=str(self.folder))
        probe = subprocess.run([sys.executable, '-c', 'import tempfile; print(tempfile.gettempdir())'],
                               env=environment, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(os.path.normcase(probe.stdout.strip()), os.path.normcase(str(self.folder)))
        for name in ('evaluate_improvement_accounting.py',):
            with self.subTest(example=name):
                result = self.run_example(name)
                self.assertEqual(result.returncode, 0, result.stderr[-600:])
                self.assertEqual(os.listdir(self.folder), [])


if __name__ == '__main__':
    unittest.main()
