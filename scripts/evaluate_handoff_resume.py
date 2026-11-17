"""Compare uninterrupted work with a fresh receiver resuming from a persisted handoff."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


WORKER = Path(__file__).resolve().with_name('evaluate_handoff_resume_worker.py')
ARMS = ('uninterrupted', 'resumed')
BRIEF_A = {'task': 'batch-a', 'source': 'a-r2.json'}
BRIEF_B = {'task': 'batch-b', 'source': 'b-r2.json'}
# ID, accepted status, units. Both arms start with the same three files.
SNAPSHOTS = {
    'a-r2.json': [('a17', True, 7), ('a23', False, 4), ('a31', True, 5)],
    'a-r1.json': [('a17', True, 7), ('a23', True, 4), ('a31', False, 5)],
    'b-r2.json': [('b11', True, 3), ('b19', True, 8), ('b25', False, 8)],
}
# Hand-calculated, held by the scorer. The worker does not import this module.
FOCUS_GOLD = {'task': 'batch-a', 'source': 'a-r2.json', 'accepted_ids': ['a17', 'a31'], 'total': 12}
INTERRUPT_GOLD = {'task': 'batch-b', 'source': 'b-r2.json', 'accepted_ids': ['b11', 'b19'], 'total': 11}
MAX_RESULT_BYTES = 16 * 1024


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def prepare(root):
    root.mkdir()
    for arm in ARMS:
        directory = root / arm
        directory.mkdir()
        for name, rows in SNAPSHOTS.items():
            write_json(directory / name, [{'id': key, 'accepted': accepted, 'units': units}
                                          for key, accepted, units in rows])
    write_json(root / 'brief-a.json', BRIEF_A)
    write_json(root / 'brief-b.json', BRIEF_B)


def unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate_field')
        obj[key] = value
    return obj


def grade(directory, filename, gold):
    path = directory / filename
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
            raise ValueError('invalid_result_file')
        with path.open('rb') as stream:
            data = stream.read(MAX_RESULT_BYTES + 1)
        if len(data) > MAX_RESULT_BYTES:
            raise ValueError('invalid_result_size')
        artifact = json.loads(data.decode('utf-8'), object_pairs_hook=unique_keys)
        if not isinstance(artifact, dict):
            raise ValueError('invalid_result_object')
    except FileNotFoundError:
        return {'passed': False, 'status': 'missing', 'checks': {}}
    except (OSError, ValueError, RecursionError):
        return {'passed': False, 'status': 'invalid', 'checks': {}}
    checks = {key: type(artifact.get(key)) is type(expected) and artifact[key] == expected
              for key, expected in gold.items()}
    return {'passed': all(checks.values()), 'status': 'scored', 'checks': checks}


def score(root, uninterrupted_seconds=None, resumed_seconds=None):
    uninterrupted = grade(root / 'uninterrupted', 'focus-result.json', FOCUS_GOLD)
    resumed = grade(root / 'resumed', 'focus-result.json', FOCUS_GOLD)
    interruption = grade(root / 'resumed', 'interrupt-result.json', INTERRUPT_GOLD)
    return {'schema_version': 1, 'evidence': 'externally_supplied_artifacts', 'synthetic_task': True,
            'uninterrupted': uninterrupted, 'resumed': resumed, 'interruption': interruption,
            'reported_seconds': {'uninterrupted': uninterrupted_seconds, 'resumed': resumed_seconds},
            'passed': all(result['passed'] for result in (uninterrupted, resumed, interruption))}


def run_worker(stage, directory, stages, briefing=None):
    subprocess.run([sys.executable, '-I', '-B', str(WORKER), stage], cwd=directory,
                   input=json.dumps(briefing), text=True, capture_output=True, check=True, timeout=30)
    stages.append(stage)


def demo():
    with tempfile.TemporaryDirectory(prefix='av-resume-') as tmp:
        root = Path(tmp) / 'pair'
        prepare(root)
        uninterrupted, switched = (root / arm for arm in ARMS)
        stages = []
        run_worker('uninterrupted', uninterrupted, stages, BRIEF_A)
        run_worker('checkpoint', switched, stages, BRIEF_A)
        run_worker('interrupt', switched, stages, BRIEF_B)
        baseline = grade(uninterrupted, 'focus-result.json', FOCUS_GOLD)
        interruption = grade(switched, 'interrupt-result.json', INTERRUPT_GOLD)
        outcomes = {}
        for control in ('correct', 'wrong_task', 'stale_source'):
            trial = Path(tmp) / control
            shutil.copytree(switched, trial)
            handoff = trial / 'handoff.json'
            if control == 'wrong_task':
                shutil.copyfile(trial / 'interrupt-checkpoint.json', handoff)
            elif control == 'stale_source':
                checkpoint = json.loads(handoff.read_text(encoding='utf-8'))
                checkpoint['source'] = 'a-r1.json'
                write_json(handoff, checkpoint)
            run_worker('resume', trial, stages)
            outcomes[control] = grade(trial, 'focus-result.json', FOCUS_GOLD)
        # An invalid or absent negative artifact would not demonstrate a detected error.
        wrong = outcomes['wrong_task']['checks']
        stale = outcomes['stale_source']['checks']
        passed = (baseline['passed'] and interruption['passed'] and outcomes['correct']['passed']
                  and wrong == dict.fromkeys(FOCUS_GOLD, False)
                  and stale == {'task': True, 'source': False, 'accepted_ids': False, 'total': False})
        return {'schema_version': 1, 'evidence': 'scripted_subprocesses', 'synthetic_task': True,
                'process_stages': stages, 'uninterrupted': baseline, 'interruption': interruption,
                'resumed': outcomes, 'passed': passed}


def seconds(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError('seconds must be finite and nonnegative')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('demo', help='run the scripted positive and negative controls')
    setup = sub.add_parser('prepare', help='create two matched workspaces in a new directory')
    setup.add_argument('directory', type=Path)
    observe = sub.add_parser('score', help='grade participant-produced files from both workspaces')
    observe.add_argument('directory', type=Path)
    for arm in ARMS:
        observe.add_argument(f'--{arm}-seconds', type=seconds)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(args.directory)
            report = {'schema_version': 1, 'prepared': True}
        elif args.command == 'score':
            report = score(args.directory, args.uninterrupted_seconds, args.resumed_seconds)
        else:
            report = demo()
    except FileExistsError:
        report = {'error': 'destination_exists'}
    except (OSError, subprocess.SubprocessError):
        report = {'error': 'experiment_io_failed'}
    print(json.dumps(report, sort_keys=True))
    return int('error' in report or report.get('passed') is False)


if __name__ == '__main__':
    raise SystemExit(main())
