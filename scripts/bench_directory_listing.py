#!/usr/bin/env python3
"""Wall-clock cost of the directory listing fingerprint (limit A11).

Builds synthetic vaults at the engine caps inside a temporary directory and
times three calls, each with the listing step as shipped and with it replaced
by no-op stand-ins (the baseline, equal to the earlier time-only checks):

* ``recall_expand_query``: vault_recall.recall(..., expand_query=True), the
  integrated alias inventory (marks during the walk, final verification);
* ``expand_query``: the standalone vault_query_expansion.expand_query;
* ``instruction_budget``: vault_instruction_budget.diagnose_instructions.

Runs alternate between the two variants and the medians are reported with the
added latency in total and per marked directory. The baseline still builds the
walk's name list (one list per directory); only hashing and the final
re-listing are removed. Stdlib only, no network; nothing is written outside
the temporary directory, which is removed afterwards. Timings depend on the
machine and its load: run it on an idle machine and compare medians, not
single runs.

    python scripts/bench_directory_listing.py [--runs 7] [--directories 200] [--no-fill]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / 'skills' / 'agentic-vault' / 'scripts'
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import vault_instruction_budget as budget  # noqa: E402
import vault_query_expansion as expansion  # noqa: E402
import vault_recall as recall  # noqa: E402

QUERY = 'benchanchor'
MAX_RUNS = 51
MAX_DIRECTORIES = 1_000


def build_recall_vault(root: Path, directories: int, fill: bool) -> dict:
    """Notes at recall.MAX_FILES; optional filler entries up to the entry cap."""
    (root / '00-meta').mkdir(parents=True)
    (root / '00-meta' / 'vault-config.json').write_text(
        json.dumps({'deny_zones': [], 'exclude_dirs': []}), encoding='utf-8')
    notes = recall.MAX_FILES
    knowledge = root / '20-knowledge'
    # Entries the walk counts: 2 at the root, 1 in 00-meta, one per directory
    # in 20-knowledge, then every note and filler file.
    fixed = 3 + directories + notes
    filler = max(0, recall.MAX_DIRECTORY_ENTRIES - fixed) // directories if fill else 0
    for index in range(directories):
        folder = knowledge / f'd{index:04d}'
        folder.mkdir(parents=True)
        for number in range(filler):
            (folder / f'attachment-{number:04d}.txt').write_bytes(b'')
    for number in range(notes):
        folder = knowledge / f'd{number % directories:04d}'
        text = f'# Note {number}\nSynthetic body {number}.\n'
        if number == 0:
            text = '---\naliases: ["' + QUERY + '"]\n---\n' + text
        (folder / f'note-{number:04d}.md').write_text(text, encoding='utf-8')
    return {'notes': notes, 'directories': directories + 3,
            'entries': fixed + filler * directories, 'entry_cap': recall.MAX_DIRECTORY_ENTRIES,
            'file_cap': recall.MAX_FILES}


def build_budget_vault(root: Path) -> dict:
    """Rule files at budget.MAX_FILES and walked entries up to budget.MAX_ENTRIES."""
    root.mkdir(parents=True)
    (root / 'AGENTS.md').write_text('root\n', encoding='utf-8')
    groups = 16
    rules = budget.MAX_FILES - 1
    filler = (budget.MAX_ENTRIES - groups - rules - 1) // groups
    base = root / '.claude' / 'rules'
    for index in range(groups):
        folder = base / f'g{index:02d}'
        folder.mkdir(parents=True)
        for number in range(filler):
            (folder / f'note-{number:03d}.txt').write_bytes(b'')
    for number in range(rules):
        (base / f'g{number % groups:02d}' / f'rule-{number:03d}.md').write_text(f'rule {number}\n', encoding='utf-8')
    return {'rule_files': rules + 1, 'directories': groups + 1,
            'entries': groups + rules + filler * groups, 'entry_cap': budget.MAX_ENTRIES,
            'file_cap': budget.MAX_FILES}


def recall_baseline(stack: ExitStack) -> None:
    stack.enter_context(mock.patch.object(expansion, 'listing_digest', lambda names: ''))
    stack.enter_context(mock.patch.object(expansion, 'directory_names', lambda path, limit: ()))


def budget_baseline(stack: ExitStack) -> None:
    # A missing digest skips the final re-listing, as before the fingerprint.
    stack.enter_context(mock.patch.object(budget, '_listing_digest', lambda names: None))


def check_recall(result: dict, expected_directories: int) -> int:
    checked = result['diagnostics']['query_expansion'].get('inventory_verification')
    if result['diagnostics']['status'] != 'ok' or not checked or checked['status'] != 'ok':
        raise RuntimeError('synthetic recall did not verify its alias inventory')
    if checked['directories_checked'] != expected_directories:
        raise RuntimeError('unexpected marked directory count')
    return checked['directories_checked']


def check_expand(result: dict, expected_directories: int) -> int:
    checked = result.get('inventory_verification')
    if result['status'] != 'ok' or not checked or checked['directories_checked'] != expected_directories:
        raise RuntimeError('synthetic expansion did not verify its alias inventory')
    return checked['directories_checked']


def check_budget(report: dict, expected_directories: int) -> int:
    if report['status'] != 'complete' or report['totals']['unique_files'] != budget.MAX_FILES:
        raise RuntimeError('synthetic instruction inventory was not complete')
    return expected_directories


def measure(call, baseline, check, expected_directories: int, runs: int) -> dict:
    timings = {'listing': [], 'baseline': []}
    order = ('listing', 'baseline')
    for run in range(runs + 1):
        for variant in (order if run % 2 == 0 else order[::-1]):
            with ExitStack() as stack:
                if variant == 'baseline':
                    baseline(stack)
                started = time.perf_counter()
                result = call()
                elapsed = (time.perf_counter() - started) * 1000
            check(result, expected_directories)
            if run:  # the first round only warms caches
                timings[variant].append(round(elapsed, 3))
    with_listing = statistics.median(timings['listing'])
    without = statistics.median(timings['baseline'])
    added = with_listing - without
    return {'marked_directories': expected_directories,
            'median_ms_with_listing': round(with_listing, 3),
            'median_ms_without_listing': round(without, 3),
            'added_ms_total': round(added, 3),
            'added_us_per_directory': round(added * 1000 / expected_directories, 2),
            'runs_ms': timings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--runs', type=int, default=7, help=f'measured runs per variant (1-{MAX_RUNS}, default 7)')
    parser.add_argument('--directories', type=int, default=200,
                        help=f'note directories in the recall vault (1-{MAX_DIRECTORIES}, default 200)')
    parser.add_argument('--no-fill', action='store_true', help='skip filler files up to the recall entry cap')
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= MAX_RUNS or not 1 <= args.directories <= MAX_DIRECTORIES:
        parser.error('runs or directories out of range')
    with tempfile.TemporaryDirectory(prefix='av-listing-bench-') as temporary:
        root = Path(temporary)
        started = time.perf_counter()
        recall_vault = root / 'recall'
        recall_vault.mkdir()
        recall_shape = build_recall_vault(recall_vault, args.directories, not args.no_fill)
        budget_shape = build_budget_vault(root / 'budget')
        build_seconds = time.perf_counter() - started
        directories = recall_shape['directories']
        report = {
            'benchmark': 'directory_listing_fingerprint',
            'python': platform.python_version(), 'platform': platform.platform(),
            'cpu_count': os.cpu_count(), 'runs_per_variant': args.runs,
            'build_seconds': round(build_seconds, 2),
            'recall_vault': recall_shape, 'budget_vault': budget_shape,
            'recall_expand_query': measure(
                lambda: recall.recall(recall_vault, QUERY, expand_query=True),
                recall_baseline, check_recall, directories, args.runs),
            'expand_query': measure(
                lambda: expansion.expand_query(recall_vault, QUERY, enabled=True),
                recall_baseline, check_expand, directories, args.runs),
            'instruction_budget': measure(
                lambda: budget.diagnose_instructions(root / 'budget', {
                    'deny_zones': [], 'exclude_dirs': [], 'hot_note': '', 'handoff_note': ''}),
                budget_baseline, check_budget, budget_shape['directories'], args.runs),
        }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
