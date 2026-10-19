"""Smoke test for scripts/bench_directory_listing.py at reduced caps."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / 'scripts' / 'bench_directory_listing.py'


def load_bench():
    spec = importlib.util.spec_from_file_location('bench_directory_listing', BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BenchDirectoryListingTests(unittest.TestCase):
    def test_reduced_caps_report_both_variants_for_every_call(self):
        bench = load_bench()
        output = io.StringIO()
        with mock.patch.object(bench.recall, 'MAX_FILES', 12), \
                mock.patch.object(bench.recall, 'MAX_DIRECTORY_ENTRIES', 40), \
                mock.patch.object(bench.budget, 'MAX_FILES', 8), \
                mock.patch.object(bench.budget, 'MAX_ENTRIES', 64), \
                contextlib.redirect_stdout(output):
            code = bench.main(['--runs', '1', '--directories', '3'])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['recall_vault']['entries'], 39)
        for name, directories in (('recall_expand_query', 6), ('expand_query', 6), ('instruction_budget', 17)):
            with self.subTest(call=name):
                row = report[name]
                self.assertEqual(row['marked_directories'], directories)
                self.assertEqual([len(row['runs_ms'][key]) for key in ('listing', 'baseline')], [1, 1])

    def test_out_of_range_options_are_rejected(self):
        bench = load_bench()
        for argv in (['--runs', '0'], ['--runs', '52'], ['--directories', '0']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    bench.main(argv)


if __name__ == '__main__':
    unittest.main()
