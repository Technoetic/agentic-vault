#!/usr/bin/env python3
"""Compare selected tests across environment variations and name the variation behind a difference.

    python scripts/env_vary.py test_vault_runs.VaultRunsTests.test_x
    python scripts/env_vary.py test_session_start test_vault_recall --seed 7 --json
    python scripts/env_vary.py test_vault_recall --variation utf8_mode --variation tz

Development tool only: nothing in the plugin imports it. It runs selected tests
in fresh interpreters and needs an explicit selection, because a full suite
under every variation multiplies the run time.

The steps follow the check_auto procedure of reprotest:

1. Run the control at least twice. A test whose result differs between controls
   is unstable, so it leaves the comparison and is listed. If no test is left,
   the run stops with verdict control_unstable.
2. Run the control's tests with every selected variation on.
3. If that differs, shuffle the variation names and switch them on one at a
   time, cumulatively. A variation whose step shows a difference is a culprit
   and is switched off again; any other stays on. The result depends on the
   order, so the seed and the shuffled order are always reported.

A difference is a changed test outcome, a test that disappears or appears, or a
changed skip state or skip reason, so a test that passes only because it was
skipped shows up with its id and reason. Tests skipped in the control prove
nothing and are listed as always_skipped. The all-on run also reports whether
each variation visibly took effect in the child: a variation that changed
nothing is listed in ineffective_variations, because its quiet result says
nothing. Probes are digests, so no path enters the report.

Exit code 0 when the runs happened (whatever they found), 2 for usage errors and
for a control run that cannot finish, which are reported as content-free codes.
This tool measures; it is not a gate.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import json
import locale
import os
from pathlib import Path
import random
import re
import secrets
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
FLAKY_REPEAT_PATH = ROOT / 'scripts' / 'flaky_repeat.py'
DEFAULT_PATHS = (ROOT / 'tests', ROOT)
VARIATIONS = ('utf8_mode', 'env_minimal', 'home', 'cwd', 'temp', 'tz')
# A POSIX TZ string read by both the C runtime and glibc. No real zone sits at
# UTC-11:30, so the variation still moves the clock on a machine set to UTC.
TZ_VALUE = 'AVT+11:30'
ESSENTIAL_ENV = frozenset({'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP',
                           'TMPDIR', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'LD_LIBRARY_PATH',
                           'DYLD_LIBRARY_PATH'})
HOME_ENV = ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME')
PROBE_KEYS = {'utf8_mode': 'utf8_mode', 'env_minimal': 'env_size', 'home': 'home', 'cwd': 'cwd', 'temp': 'temp',
              'tz': 'timezone'}
SELECTOR_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,99}(?:\.[A-Za-z_][A-Za-z0-9_]{0,99}){0,9}')
MAX_SELECTORS = 64
MAX_TESTS = 500
MIN_CONTROL_RUNS = 2
DEFAULT_CONTROL_RUNS = 2
MAX_CONTROL_RUNS = 16
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600
MAX_REASON_CHARS = 200
RANK = {'pass': 0, 'skip': 1, 'fail': 2, 'error': 3}


class UsageError(Exception):
    """A content-free usage error code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: ARG002 - never echo the rejected value
        raise UsageError('invalid_arguments')


def _load_flaky_repeat():
    spec = importlib.util.spec_from_file_location('env_vary_flaky_repeat', FLAKY_REPEAT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shuffled(names, seed):
    """The accumulation order for a seed; reported because the result depends on it."""
    order = list(names)
    random.Random(seed).shuffle(order)
    return order


def build_environment(names, base, work, control_cwd):
    """(environment, cwd) with the named variations on. Their order does not matter."""
    if any(name not in VARIATIONS for name in names):
        raise UsageError('invalid_variation')
    enabled = set(names)
    env, cwd = dict(base), control_cwd
    if 'env_minimal' in enabled:
        env = {key: value for key, value in env.items() if key.upper() in ESSENTIAL_ENV}
    # Every run, control included: no run leaves bytecode behind.
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if 'utf8_mode' in enabled:
        # Explicit, not removed: UTF-8 mode is the default from Python 3.15.
        env['PYTHONUTF8'] = '0'
    if 'home' in enabled:
        home = Path(work) / 'home'
        home.mkdir(parents=True, exist_ok=True)
        env.update({name: str(home) for name in HOME_ENV})
        env.pop('HOMEDRIVE', None)
        env.pop('HOMEPATH', None)
    if 'cwd' in enabled:
        folder = Path(work) / 'cwd 작업 폴더'
        folder.mkdir(parents=True, exist_ok=True)
        cwd = str(folder)
    if 'temp' in enabled:
        folder = Path(work) / 'temp 임시 폴더'
        folder.mkdir(parents=True, exist_ok=True)
        env.update({name: str(folder) for name in ('TEMP', 'TMP', 'TMPDIR')})
    if 'tz' in enabled:
        env['TZ'] = TZ_VALUE
    return env, cwd


def _difference(test_id, control, varied):
    row = {'test_id': test_id, 'control': control['outcome'] if control else 'absent',
           'varied': varied['outcome'] if varied else 'absent'}
    if varied and varied.get('signature'):
        row['signature'] = varied['signature']
    return row


def compare(control, varied, stable):
    """(differences, skip differences) of one run against the control, over stable tests only."""
    differences, skips = [], []
    for test_id in sorted(set(stable) | (set(varied) - set(control))):
        before, after = control.get(test_id), varied.get(test_id)
        if before is None or after is None:
            differences.append(_difference(test_id, before, after))
        elif 'skip' in (before['outcome'], after['outcome']):
            if (before['outcome'], before.get('skip_reason')) != (after['outcome'], after.get('skip_reason')):
                skips.append({'test_id': test_id, 'control': before.get('skip_reason'),
                              'varied': after.get('skip_reason')})
        elif before['outcome'] != after['outcome']:
            differences.append(_difference(test_id, before, after))
    return differences, skips


def _changed_ids(*rows):
    return {item['test_id'] for row in rows for item in row}


def _variation_effect(order, control_probe, varied_probe):
    effect = {}
    for name in order:
        key = PROBE_KEYS.get(name)
        before, after = control_probe.get(key), varied_probe.get(key)
        effect[name] = {'observed': None if before is None or after is None else before != after}
    return effect


def investigate(run, order, *, control_runs=DEFAULT_CONTROL_RUNS):
    """Controls first, then everything on, then shuffled accumulation. `run` maps enabled names to results."""
    if type(control_runs) is not int or not MIN_CONTROL_RUNS <= control_runs <= MAX_CONTROL_RUNS:
        raise UsageError('invalid_arguments')
    order = list(order)
    controls = [run(()) for _ in range(control_runs)]
    baseline = controls[0]['tests']
    every = set().union(*(set(item['tests']) for item in controls))
    unstable = sorted(test_id for test_id in every if any(
        item['tests'].get(test_id) is None or baseline.get(test_id) is None
        or compare({test_id: baseline[test_id]}, {test_id: item['tests'][test_id]}, [test_id]) != ([], [])
        for item in controls))
    stable = sorted(every - set(unstable))
    report = {'control': {'runs': control_runs, 'stable_tests': stable, 'unstable_tests': unstable},
              'always_skipped': [{'test_id': test_id, 'reason': baseline[test_id].get('skip_reason')}
                                 for test_id in stable if baseline[test_id]['outcome'] == 'skip'],
              'all_on': None, 'attribution': [], 'culprits': [], 'kept_without_difference': [],
              'unattributed_tests': [], 'attribution_depends_on_order': True,
              'variation_effect': {}, 'ineffective_variations': []}
    if not stable:
        report['verdict'] = 'control_unstable'
        return report
    usable = {test_id: baseline[test_id] for test_id in stable}
    ignored = set(unstable)

    def against_control(enabled):
        varied = run(tuple(enabled))
        tests = {test_id: value for test_id, value in varied['tests'].items() if test_id not in ignored}
        return (*compare(usable, tests, stable), varied)

    differences, skips, whole = against_control(order)
    report['all_on'] = {'enabled': order, 'differences': differences, 'skip_differences': skips}
    report['variation_effect'] = _variation_effect(order, controls[0].get('probe', {}), whole.get('probe', {}))
    report['ineffective_variations'] = [name for name in order
                                        if report['variation_effect'][name]['observed'] is False]
    if not differences and not skips:
        report['verdict'] = 'no_difference_observed'
        return report
    report['verdict'] = 'environment_sensitive'
    kept, explained = [], set()
    for name in order:
        enabled = [*kept, name]
        step_differences, step_skips, _ = against_control(enabled)
        culprit = bool(step_differences or step_skips)
        report['attribution'].append({'variation': name, 'enabled': enabled, 'culprit': culprit,
                                      'differences': step_differences, 'skip_differences': step_skips})
        if culprit:
            report['culprits'].append(name)
            explained |= _changed_ids(step_differences, step_skips)
        else:
            kept.append(name)
    report['kept_without_difference'] = kept
    report['unattributed_tests'] = sorted(_changed_ids(differences, skips) - explained)
    return report


class _Recorder(unittest.TestResult):
    """One row per test id; the worst outcome of its subtests wins."""

    def __init__(self, flaky):
        super().__init__()
        self.flaky = flaky
        self.rows = {}

    def _clean(self, text):
        first = (str(text).splitlines() or [''])[0]
        return self.flaky._PATH_RE.sub('<path>', self.flaky._QUOTED_PATH_RE.sub(r'\1<path>\1', first))[:MAX_REASON_CHARS]

    def _note(self, test, outcome, *, reason=None, err=None):
        test_id = getattr(test, 'test_case', test).id()
        row = self.rows.get(test_id)
        if row is None or RANK[outcome] > RANK[row['outcome']]:
            self.rows[test_id] = {'outcome': outcome, 'skip_reason': reason,
                                  'signature': self.flaky.signature(err[0], err[1]) if err else None}

    def addSuccess(self, test):
        super().addSuccess(test)
        self._note(test, 'pass')

    def addError(self, test, err):
        super().addError(test, err)
        self._note(test, 'error', err=err)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._note(test, 'fail', err=err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._note(test, 'fail' if issubclass(err[0], test.failureException) else 'error', err=err)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._note(test, 'skip', reason=self._clean(reason))

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._note(test, 'pass')

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._note(test, 'fail', err=(AssertionError, AssertionError('unexpected success')))


def _digest(text):
    return hashlib.sha256(text.encode('utf-8', 'surrogateescape')).hexdigest()


def _probe():
    """What a variation changes, as seen by the child. Paths stay out: only digests leave."""
    return {'utf8_mode': sys.flags.utf8_mode, 'preferred_encoding': locale.getpreferredencoding(False),
            'env_size': len(os.environ), 'home': _digest(os.path.expanduser('~')), 'cwd': _digest(os.getcwd()),
            'temp': _digest(tempfile.gettempdir()), 'timezone': time.timezone}


def run_child(selectors):
    """Run the selected tests here and write one ASCII JSON line."""
    flaky = _load_flaky_repeat()
    stdout, sink = sys.stdout, flaky._Discard()
    with redirect_stdout(sink), redirect_stderr(sink):
        probe = _probe()
        suite = unittest.TestSuite(unittest.TestLoader().loadTestsFromName(name) for name in selectors)
        result = _Recorder(flaky)
        suite.run(result)
    stdout.write(json.dumps({'tests': result.rows, 'probe': probe}) + '\n')
    return 0


def _failed_load(tests):
    return any(test_id.startswith('unittest.loader._FailedTest.') for test_id in tests)


class _Runner:
    """Start one child per run; each run gets new folders under `root`."""

    def __init__(self, selectors, paths, timeout, root):
        self.selectors, self.paths, self.timeout, self.root = selectors, paths, timeout, Path(root)
        self.base = dict(os.environ)
        self.control_cwd = str(ROOT)
        self.known = []
        self.count = 0

    def _child(self, enabled):
        self.count += 1
        env, cwd = build_environment(enabled, self.base, self.root / ('run-%03d' % self.count), self.control_cwd)
        command = [sys.executable, str(Path(__file__).resolve()), '--run-child', *self.selectors]
        for path in self.paths:
            command += ['--path', path]
        try:
            done = subprocess.run(command, capture_output=True, env=env, cwd=cwd, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return None, 'TimeoutExpired'
        lines = done.stdout.decode('ascii', 'replace').strip().splitlines()
        try:
            value = json.loads(lines[-1]) if lines else None
        except ValueError:
            value = None
        if done.returncode != 0 or not isinstance(value, dict) or not isinstance(value.get('tests'), dict):
            return None, 'ChildExit: %d' % done.returncode
        return value, None

    def __call__(self, enabled):
        value, failure = self._child(enabled)
        if not enabled:
            if value is None:
                raise UsageError('control_run_failed')
            if _failed_load(value['tests']):
                raise UsageError('invalid_test_id')
            if not value['tests'] or len(value['tests']) > MAX_TESTS:
                raise UsageError('invalid_test_selection')
            self.known = sorted(set(self.known) | set(value['tests']))
            return value
        if value is None:
            return {'tests': {test_id: {'outcome': 'error', 'skip_reason': None, 'signature': failure}
                              for test_id in self.known}, 'probe': {}}
        return value


def _text(report):
    lines = ['env_vary: verdict %s (seed %d)' % (report['verdict'], report['seed']),
             'shuffled order: ' + ', '.join(report['shuffled_order']),
             'control: %d runs, %d stable, %d unstable' % (report['control']['runs'],
                                                           len(report['control']['stable_tests']),
                                                           len(report['control']['unstable_tests']))]
    lines += ['  unstable: ' + test_id for test_id in report['control']['unstable_tests']]
    if report['all_on']:
        lines += ['  differs: %s (%s -> %s)' % (row['test_id'], row['control'], row['varied'])
                  for row in report['all_on']['differences']]
        lines += ['  skip differs: %s (%s -> %s)' % (row['test_id'], row['control'], row['varied'])
                  for row in report['all_on']['skip_differences']]
    if report['culprits']:
        lines.append('culprits, in order of accumulation: ' + ', '.join(report['culprits']))
    lines += ['  unattributed: ' + test_id for test_id in report['unattributed_tests']]
    lines += ['  always skipped: %s (%s)' % (row['test_id'], row['reason']) for row in report['always_skipped']]
    if report['ineffective_variations']:
        lines.append('no visible effect, so no evidence: ' + ', '.join(report['ineffective_variations']))
    return '\n'.join(lines).encode('ascii', 'backslashreplace').decode('ascii')


def _parser():
    parser = _Parser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tests', nargs='+', help='unittest ids: module, module.Class or module.Class.test_name')
    parser.add_argument('--variation', action='append', choices=VARIATIONS,
                        help='use only these variations (repeatable; default: all)')
    parser.add_argument('--seed', type=int, help='seed of the shuffled order (default: random, always reported)')
    parser.add_argument('--control-runs', type=int, default=DEFAULT_CONTROL_RUNS,
                        help='control repetitions, %d-%d (default %d)' % (MIN_CONTROL_RUNS, MAX_CONTROL_RUNS,
                                                                          DEFAULT_CONTROL_RUNS))
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
                        help='seconds per run (default %d)' % DEFAULT_TIMEOUT)
    parser.add_argument('--path', action='append', default=None,
                        help='directory added to sys.path (default: tests/ and the repository root); repeatable')
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    parser.add_argument('--run-child', action='store_true', help=argparse.SUPPRESS)
    return parser


def _fail(code):
    print(json.dumps({'ok': False, 'error': code}))
    return 2


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if len(args.tests) > MAX_SELECTORS or not all(SELECTOR_RE.fullmatch(name) for name in args.tests):
            raise UsageError('invalid_test_id')
        if not 0 < args.timeout <= MAX_TIMEOUT:
            raise UsageError('invalid_arguments')
        paths = [str(Path(path).resolve()) for path in (args.path or [str(path) for path in DEFAULT_PATHS])]
        for path in reversed(paths):
            if path not in sys.path:
                sys.path.insert(0, path)
        if args.run_child:
            return run_child(args.tests)
        selected = sorted(set(args.variation or VARIATIONS))
        seed = args.seed if args.seed is not None else secrets.randbelow(2 ** 32)
        order = shuffled(selected, seed)
        with tempfile.TemporaryDirectory(prefix='av-env-vary-', ignore_cleanup_errors=True) as root:
            result = investigate(_Runner(args.tests, paths, args.timeout, root), order,
                                 control_runs=args.control_runs)
    except UsageError as error:
        return _fail(str(error))
    report = {'tool': 'env_vary', 'version': 1, 'seed': seed, 'shuffled_order': order,
              'selected_tests': args.tests, **result}
    print(json.dumps(report, sort_keys=True) if args.json else _text(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
