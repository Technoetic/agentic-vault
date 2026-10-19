#!/usr/bin/env python3
"""Repeat one unittest id N times and report x/N failures with an exact upper bound.

    python scripts/flaky_repeat.py test_vault_runs.VaultRunsTests.test_x -n 300
    python scripts/flaky_repeat.py MODULE.CLASS.TEST -n 30 --fresh-process --json

Each run loads the test again and runs it once: in this process by default, or
in a new interpreter per run with --fresh-process. Runs are counted as passed,
failed, error or skipped. Failures and errors are failure events; skipped runs
are left out of the denominator. The one-sided 95% Clopper-Pearson upper bound
comes from scripts/eval_stats.py.

Wording rules built into the report: zero failures read "not observed in N
runs (rate <= p)", never a claim that the test is repaired, and a bound wider
than WIDE_MARGIN above the observed rate is called wide. Failure signatures keep
the exception class and the first message line with paths replaced by <path>;
at most MAX_SIGNATURES distinct signatures are kept. The tool measures; it is
not a gate. Exit code 0 when the runs happened (whatever x is), 2 for usage
errors, which are reported as content-free codes.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVAL_STATS_PATH = ROOT / 'scripts' / 'eval_stats.py'
DEFAULT_PATH = ROOT / 'tests'
CONFIDENCE = 0.95
MAX_RUNS = 10000
DEFAULT_RUNS = 30
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600
MAX_SIGNATURES = 10
MAX_SIGNATURE_CHARS = 200
# A bound more than one percentage point above the observed rate is reported as wide.
WIDE_MARGIN = 0.01
TEST_ID_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,99}(?:\.[A-Za-z_][A-Za-z0-9_]{0,99}){1,9}')
_PATH_RE = re.compile(
    r'\\\\[^\s\'"]+'                                  # UNC and \\?\ extended paths
    r'|(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\'"]*'       # drive paths
    r'|(?<![\w.~])~?/(?:[^\s\'"/]+/)+[^\s\'"]*')      # absolute POSIX paths with a directory
_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}')


class UsageError(Exception):
    """A content-free usage error code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: ARG002 - never echo the rejected value
        raise UsageError('invalid_arguments')


class _Discard:
    """Swallow output written by the test under repetition."""

    def write(self, text):
        return len(text)

    def flush(self):
        pass


def _load_eval_stats():
    spec = importlib.util.spec_from_file_location('flaky_repeat_eval_stats', EVAL_STATS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def signature(exc_type, exc_value):
    """Exception class plus the first message line, paths replaced, bounded."""
    name = getattr(exc_type, '__name__', '')
    name = name if _NAME_RE.fullmatch(name or '') else 'Exception'
    try:
        text = str(exc_value)
    except Exception:  # noqa: BLE001 - an unprintable message keeps the class only
        text = ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first = _PATH_RE.sub('<path>', lines[0]) if lines else ''
    value = name + ': ' + first if first else name
    return value[:MAX_SIGNATURE_CHARS]


class _Result(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.outcome = 'pass'
        self.signature = None

    def _event(self, outcome, err):
        if self.outcome != 'error':
            self.outcome = outcome
        if self.signature is None:
            self.signature = signature(err[0], err[1])

    def addError(self, test, err):
        super().addError(test, err)
        self._event('error', err)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._event('fail', err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._event('fail' if issubclass(err[0], test.failureException) else 'error', err)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._event('fail', (AssertionError, AssertionError('unexpected success')))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.outcome == 'pass':
            self.outcome = 'skip'


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _load_one(test_id):
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(test_id)
    except Exception as exc:  # noqa: BLE001 - any import or lookup failure is a usage error
        raise UsageError('invalid_test_id') from exc
    tests = list(_flatten(suite))
    if (getattr(loader, 'errors', None) or len(tests) != 1
            or type(tests[0]).__name__ in ('_FailedTest', 'ModuleImportFailure')):
        raise UsageError('invalid_test_id')
    return suite


def run_once(test_id):
    """Load and run the test once in this process; return (outcome, signature)."""
    sink = _Discard()
    with redirect_stdout(sink), redirect_stderr(sink):
        suite = _load_one(test_id)
        result = _Result()
        suite.run(result)
    return result.outcome, result.signature


def _child(test_id, paths, timeout):
    command = [sys.executable, str(Path(__file__).resolve()), '--run-once', test_id]
    for path in paths:
        command += ['--path', path]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 'error', 'TimeoutExpired'
    lines = completed.stdout.decode('utf-8', 'replace').strip().splitlines()
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        value = None
    if completed.returncode == 2 and isinstance(value, dict) and value.get('error') == 'invalid_test_id':
        raise UsageError('invalid_test_id')
    if (completed.returncode != 0 or not isinstance(value, dict)
            or value.get('outcome') not in ('pass', 'fail', 'error', 'skip')):
        return 'error', 'ChildExit: %d' % completed.returncode
    sig = value.get('signature')
    return value['outcome'], sig[:MAX_SIGNATURE_CHARS] if isinstance(sig, str) else None


def repeat(test_id, runs, *, fresh_process=False, paths=(), timeout=DEFAULT_TIMEOUT):
    counts = {'pass': 0, 'fail': 0, 'error': 0, 'skip': 0}
    signatures, not_shown = {}, 0
    started = time.monotonic()
    for _ in range(runs):
        if fresh_process:
            outcome, sig = _child(test_id, paths, timeout)
        else:
            outcome, sig = run_once(test_id)
        counts[outcome] += 1
        if outcome in ('fail', 'error'):
            sig = sig or 'unknown'
            if sig in signatures:
                signatures[sig] += 1
            elif len(signatures) < MAX_SIGNATURES:
                signatures[sig] = 1
            else:
                not_shown += 1
    return counts, signatures, not_shown, time.monotonic() - started


def summarize(events, trials, skipped, bounds):
    """The report sentence; the wording rules live here."""
    if not trials:
        return 'no executed runs (%d skipped); no rate bound.' % skipped
    upper = bounds['clopper_pearson_upper']
    method = 'one-sided 95% Clopper-Pearson, scripts/eval_stats.py'
    if events:
        text = '%d/%d failures; failure rate <= %.4f (%s).' % (events, trials, upper, method)
    else:
        text = ('0/%d failures; not observed in %d runs (rate <= %.4f, %s). '
                'Absence in %d runs is not proof of absence.' % (trials, trials, upper, method, trials))
    if upper - bounds['observed_rate'] > WIDE_MARGIN:
        text += (' The bound is wide at N=%d: it is %.4f above the observed rate; '
                 'hundreds of targeted repeats narrow it.' % (trials, upper - bounds['observed_rate']))
    if skipped:
        text += ' %d skipped runs are not in the denominator.' % skipped
    return text


def build_report(test_id, runs, fresh_process, counts, signatures, not_shown, seconds):
    events = counts['fail'] + counts['error']
    trials = runs - counts['skip']
    bounds = (_load_eval_stats().rate_upper_bounds(events, trials, confidence=CONFIDENCE)
              if trials else None)
    return {
        'tool': 'flaky_repeat', 'version': 1, 'test_id': test_id,
        'mode': 'fresh_process' if fresh_process else 'in_process',
        'runs': runs, 'passed': counts['pass'], 'failed': counts['fail'],
        'errors': counts['error'], 'skipped': counts['skip'],
        'failure_events': events, 'trials': trials, 'confidence': CONFIDENCE,
        'bounds': bounds,
        'wide_bound': (bounds['clopper_pearson_upper'] - bounds['observed_rate'] > WIDE_MARGIN
                       if bounds else None),
        'summary': summarize(events, trials, counts['skip'], bounds),
        'signatures': [{'signature': key, 'count': value} for key, value in
                       sorted(signatures.items(), key=lambda item: (-item[1], item[0]))],
        'signatures_not_shown': not_shown,
        'elapsed_seconds': round(seconds, 3),
    }


def _text(report):
    lines = ['flaky_repeat: %s (%s)' % (report['test_id'], report['mode'].replace('_', '-')),
             'runs %d: passed %d, failed %d, errors %d, skipped %d' % (
                 report['runs'], report['passed'], report['failed'], report['errors'],
                 report['skipped']),
             report['summary']]
    if report['signatures']:
        lines.append('failure signatures (at most %d distinct):' % MAX_SIGNATURES)
        lines += ['  %dx %s' % (item['count'], item['signature']) for item in report['signatures']]
    if report['signatures_not_shown']:
        lines.append('  %d further failure events not shown' % report['signatures_not_shown'])
    return '\n'.join(lines)


def _parser():
    parser = _Parser(description=__doc__.split('\n\n')[0],
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('test_id', help='one unittest id, e.g. module.Class.test_name')
    parser.add_argument('-n', '--runs', type=int, default=DEFAULT_RUNS,
                        help='number of runs, 1-%d (default %d)' % (MAX_RUNS, DEFAULT_RUNS))
    parser.add_argument('--fresh-process', action='store_true',
                        help='run each repeat in a new interpreter')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
                        help='seconds per fresh-process run (default %d)' % DEFAULT_TIMEOUT)
    parser.add_argument('--path', action='append', default=None,
                        help='directory added to sys.path (default: tests/); repeatable')
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    parser.add_argument('--run-once', action='store_true', help=argparse.SUPPRESS)
    return parser


def _fail(code):
    print(json.dumps({'ok': False, 'error': code}))
    return 2


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if not TEST_ID_RE.fullmatch(args.test_id):
            raise UsageError('invalid_test_id')
        if not 1 <= args.runs <= MAX_RUNS or not 0 < args.timeout <= MAX_TIMEOUT:
            raise UsageError('invalid_arguments')
        paths = [str(Path(path).resolve()) for path in (args.path or [str(DEFAULT_PATH)])]
        for path in reversed(paths):
            if path not in sys.path:
                sys.path.insert(0, path)
        if args.run_once:
            stdout = sys.stdout
            outcome, sig = run_once(args.test_id)
            stdout.write(json.dumps({'outcome': outcome, 'signature': sig}) + '\n')
            return 0
        counts, signatures, not_shown, seconds = repeat(
            args.test_id, args.runs, fresh_process=args.fresh_process, paths=paths,
            timeout=args.timeout)
    except UsageError as error:
        return _fail(str(error))
    report = build_report(args.test_id, args.runs, args.fresh_process, counts, signatures,
                          not_shown, seconds)
    print(json.dumps(report, sort_keys=True) if args.json else _text(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
