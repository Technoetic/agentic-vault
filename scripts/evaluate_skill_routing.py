#!/usr/bin/env python3
"""Offline routing catalog, caller-observation grading and bounded report diffs.

This program never runs a model or a skill. Report hashes bind caller-supplied
data and evaluator source; they do not independently prove host events occurred.
Comparison requires trusted grader outputs: it does not regrade omitted
expectations or authenticate caller-attested observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from skill_routing_catalog import analyze_catalog, build_catalog
from skill_routing_contract import (RoutingError, digest_document, evaluate,
                                    read_document_with_sha256)


_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_SKILL_ID = re.compile(r'[a-z0-9_-]{1,64}:[a-z0-9_-]{1,64}\Z', re.ASCII)
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z', re.ASCII)
_MODULES = ('evaluate_skill_routing.py', 'skill_routing_contract.py',
            'skill_routing_catalog.py')
_VIOLATIONS = ('missing_required', 'forbidden', 'unexpected',
               'order_violations', 'entrypoint_violations')
_REASONS = ('missing_required', 'forbidden', 'unexpected',
            'order_violation', 'entrypoint_violation')
_UNMEASURED_REASONS = {'missing_observation', 'observation_unavailable',
                       'observation_error', 'unsupported_observation_kind'}


def _require(condition, code='invalid_report'):
    if not condition:
        raise RoutingError(code)


def _keys(value, expected):
    _require(type(value) is dict and set(value) == set(expected))


def _text(value, maximum=256):
    return type(value) is str and 0 < len(value) <= maximum and bool(value.strip())


def _identifier(value):
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def _digest(value):
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _ids(value, maximum=256, unique=True):
    _require(type(value) is list and len(value) <= maximum)
    _require(all(type(item) is str and len(item) <= 256 and _SKILL_ID.fullmatch(item)
                 for item in value))
    _require(not unique or len(value) == len(set(value)))
    return value


def evaluator_sha256():
    """Bind all grading, parsing, catalog and CLI behavior to raw source bytes."""
    directory = Path(__file__).resolve().parent
    sources = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
               for name in _MODULES}
    return digest_document(sources)


def _seal(report, raw_inputs):
    report['evaluator_sha256'] = evaluator_sha256()
    report['raw_input_sha256'] = raw_inputs
    report['report_sha256'] = digest_document(report)
    return report


def validate_evaluation_report(report):
    """Validate untrusted report shape and derivable consistency before diffing.

    Expectations are deliberately absent from reports. These checks establish
    internal consistency, not the truth of an observation or its attribution.
    A caller can recompute hashes after altering grade data. This validator
    therefore requires trusted grader outputs rather than self-authored grades.
    """
    _keys(report, ('kind', 'schema_version', 'suite_id', 'suite_sha256',
                   'catalog_sha256', 'catalog_skill_ids', 'source_kind',
                   'observation_kind', 'runtime', 'summary', 'cases',
                   'model_run_performed', 'evaluator_sha256', 'raw_input_sha256',
                   'report_sha256'))
    _require(report['kind'] == 'skill_routing_evaluation')
    _require(type(report['schema_version']) is int and report['schema_version'] == 1)
    _require(_identifier(report['suite_id']))
    _require(all(_digest(report[key]) for key in
                 ('suite_sha256', 'catalog_sha256', 'evaluator_sha256', 'report_sha256')))
    _require(report['report_sha256'] == digest_document(
        {key: value for key, value in report.items() if key != 'report_sha256'}),
        'report_integrity_mismatch')
    catalog_ids = _ids(report['catalog_skill_ids'])
    _require(bool(catalog_ids))
    known = set(catalog_ids)
    _require(report['source_kind'] in ('scripted_control', 'host_observation'))
    _require(report['observation_kind'] in ('skill_invocation', 'skill_file_read', 'self_report'))
    _require(report['model_run_performed'] is False)
    _keys(report['runtime'], ('host', 'version', 'model'))
    _require(all(_text(value) for value in report['runtime'].values()))
    _keys(report['raw_input_sha256'], ('catalog', 'cases', 'trace'))
    _require(all(_digest(value) for value in report['raw_input_sha256'].values()))
    _keys(report['summary'], ('cases', 'scored', 'unmeasured', 'passed', 'failed'))
    _require(all(type(value) is int and 0 <= value <= 1000
                 for value in report['summary'].values()))
    rows = report['cases']
    _require(type(rows) is list and 1 <= len(rows) <= 1000)
    case_ids = set()
    counts = {'pass': 0, 'fail': 0, 'unmeasured': 0}
    for row in rows:
        _keys(row, ('id', 'status', 'observed', *_VIOLATIONS, 'reason_codes'))
        _require(_identifier(row['id']) and row['id'] not in case_ids)
        case_ids.add(row['id'])
        _require(type(row['status']) is str and row['status'] in counts)
        counts[row['status']] += 1
        observed = _ids(row['observed'], 128, unique=False)
        seen = set(observed)
        for key in ('missing_required', 'forbidden', 'unexpected', 'entrypoint_violations'):
            _ids(row[key], 256)
        _require(len(row['entrypoint_violations']) <= 1)
        _require(set(row['missing_required']) <= known
                 and not (set(row['missing_required']) & seen))
        _require(set(row['forbidden']) <= known & seen)
        _require(set(row['entrypoint_violations']) <= seen)
        # Catalog-known skills can still be undeclared for a particular case.
        # Without its expectation sets, only membership and order are derivable.
        unexpected = set(row['unexpected'])
        _require(unexpected <= seen and seen - known <= unexpected)
        _require(not (unexpected & set(row['forbidden'])))
        _require(row['unexpected'] == list(dict.fromkeys(
            item for item in observed if item in unexpected)))
        pairs = row['order_violations']
        _require(type(pairs) is list and len(pairs) <= 65536)
        distinct_pairs = set()
        for pair in pairs:
            _require(type(pair) is list and len(pair) == 2)
            _ids(pair, 2)
            earlier, later = pair
            _require(earlier in known and later in known and tuple(pair) not in distinct_pairs)
            _require(not (set(pair) & (unexpected | set(row['forbidden']))))
            distinct_pairs.add(tuple(pair))
            # Every later invocation requires an earlier occurrence before it.
            _require(later in seen and (earlier not in seen
                                       or observed.index(later) < observed.index(earlier)))
        reasons = row['reason_codes']
        _require(type(reasons) is list and all(type(value) is str for value in reasons))
        if row['status'] == 'unmeasured':
            _require(not observed and not any(row[key] for key in _VIOLATIONS))
            _require(len(reasons) == 1 and reasons[0] in _UNMEASURED_REASONS)
        else:
            expected_reasons = [reason for key, reason in zip(_VIOLATIONS, _REASONS) if row[key]]
            _require(reasons == expected_reasons)
            _require((row['status'] == 'pass') == (not expected_reasons))
            _require(report['observation_kind'] != 'self_report')
    _require(report['summary'] == {'cases': len(rows),
                                   'scored': counts['pass'] + counts['fail'],
                                   'unmeasured': counts['unmeasured'],
                                   'passed': counts['pass'], 'failed': counts['fail']})
    return report


def compare_reports(before, after):
    """Compare reported grades without regrading or authenticating observations.

    Only the supplied report contents are read. Omitted expectations cannot be
    reconstructed from source hashes, and no report locator is opened as proof.
    """
    before, after = validate_evaluation_report(before), validate_evaluation_report(after)
    for key in ('suite_id', 'suite_sha256', 'evaluator_sha256', 'source_kind',
                'observation_kind', 'runtime'):
        _require(before[key] == after[key], 'incompatible_reports')
    _require(set(before['catalog_skill_ids']) == set(after['catalog_skill_ids']),
             'incompatible_reports')
    _require([row['id'] for row in before['cases']] == [row['id'] for row in after['cases']],
             'incompatible_reports')
    changes = {'improved': [], 'regressed': [], 'unchanged': [], 'unmeasured': []}
    for previous, current in zip(before['cases'], after['cases']):
        if 'unmeasured' in (previous['status'], current['status']):
            category = 'unmeasured'
        elif previous['status'] == current['status']:
            category = 'unchanged'
        elif current['status'] == 'pass':
            category = 'improved'
        else:
            category = 'regressed'
        changes[category].append(current['id'])
    return {'kind': 'skill_routing_comparison', 'schema_version': 1,
            'suite_id': before['suite_id'], 'suite_sha256': before['suite_sha256'],
            'compared_evaluator_sha256': before['evaluator_sha256'],
            'before_report_sha256': before['report_sha256'],
            'after_report_sha256': after['report_sha256'],
            'before_catalog_sha256': before['catalog_sha256'],
            'after_catalog_sha256': after['catalog_sha256'],
            'catalog_skill_ids': list(before['catalog_skill_ids']),
            'source_kind': before['source_kind'], 'observation_kind': before['observation_kind'],
            'runtime': dict(before['runtime']), 'model_run_performed': False,
            'regrades_expectations': False, 'authenticates_observations': False,
            'summary': {'cases': len(before['cases']),
                        **{key: len(value) for key, value in changes.items()}}, **changes}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default includes caller-controlled argument contents.
        raise RoutingError('invalid_arguments')


def _parser():
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    catalog = commands.add_parser('catalog', help='collect explicitly named manifest roots')
    catalog.add_argument('--root', action='append', required=True, metavar='OWNER=PATH')
    catalog.add_argument('--output', metavar='NEWFILE')
    catalog.add_argument('--analysis-output', metavar='NEWFILE')
    grade = commands.add_parser('evaluate', help='grade caller-supplied ordered observations')
    grade.add_argument('--catalog', required=True)
    grade.add_argument('--cases', required=True)
    grade.add_argument('--trace', required=True)
    grade.add_argument('--observation-kind', choices=('skill_invocation', 'skill_file_read'),
                       default='skill_invocation')
    grade.add_argument('--output', metavar='NEWFILE')
    comparison = commands.add_parser('compare', help='compare compatible source-bound reports')
    comparison.add_argument('--before', required=True)
    comparison.add_argument('--after', required=True)
    comparison.add_argument('--output', metavar='NEWFILE')
    return parser


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       indent=2) + '\n').encode('utf-8')


def _new_output_path(value):
    raw = os.fspath(value)
    _require(type(raw) is str and bool(raw) and '\x00' not in raw, 'unsafe_output_path')
    portable = raw.replace('\\', '/')
    _require(not portable.startswith(('//', '/??/')), 'unsafe_output_path')
    tail = portable[3:] if re.match(r'^[A-Za-z]:/', portable) else portable.lstrip('/')
    parts = tail.split('/')
    _require(':' not in tail and '..' not in parts, 'unsafe_output_path')
    _require(not any(any(char in part for char in '<>"|?*') or part.endswith((' ', '.'))
                     for part in parts if part not in ('', '.')), 'unsafe_output_path')
    reserved = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)
    _require(not any(reserved.match(part) for part in parts), 'unsafe_output_path')
    path = Path(raw).absolute()
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISLNK(info.st_mode)
                 and not (getattr(info, 'st_file_attributes', 0)
                          & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)),
                 'unsafe_output_path')
    _require(not path.exists(), 'output_exists')
    _require(path.parent.is_dir(), 'invalid_output_directory')
    return path


def _write_outputs(outputs):
    """Reserve new destinations exclusively; never truncate an existing file."""
    pending = [(_new_output_path(path), _json_bytes(value)) for path, value in outputs]
    _require(len({os.path.normcase(str(path)) for path, _ in pending}) == len(pending),
             'duplicate_output_path')
    opened = []
    try:
        for path, data in pending:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            descriptor = os.open(path, flags, 0o600)
            opened.append((path, descriptor, data))
        for path, descriptor, data in opened:
            with os.fdopen(descriptor, 'wb', closefd=False) as stream:
                stream.write(data)
    except OSError as error:
        for path, _, _ in opened:
            try:
                path.unlink()
            except OSError:
                pass
        raise RoutingError('output_write_failed') from error
    finally:
        for _, descriptor, _ in opened:
            os.close(descriptor)


def _stdout(value):
    data = _json_bytes(value)
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout.buffer.write(data)
    else:
        sys.stdout.write(data.decode('utf-8'))


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        outputs = []
        if args.command == 'catalog':
            roots = []
            for root in args.root:
                _require('=' in root, 'invalid_root_argument')
                owner, path = root.split('=', 1)
                _require(bool(owner) and bool(path), 'invalid_root_argument')
                roots.append((owner, Path(path)))
            report = build_catalog(roots)
            if args.analysis_output:
                outputs.append((args.analysis_output, analyze_catalog(report)))
            exit_code = 0
        elif args.command == 'evaluate':
            documents, raw_inputs = {}, {}
            for key, path in (('catalog', args.catalog), ('cases', args.cases), ('trace', args.trace)):
                documents[key], raw_inputs[key] = read_document_with_sha256(path)
            report = _seal(evaluate(documents['catalog'], documents['cases'], documents['trace'],
                                    observation_kind=args.observation_kind), raw_inputs)
            exit_code = (2 if report['summary']['unmeasured']
                         else 1 if report['summary']['failed'] else 0)
        else:
            before, before_hash = read_document_with_sha256(args.before)
            after, after_hash = read_document_with_sha256(args.after)
            report = _seal(compare_reports(before, after), {'before': before_hash, 'after': after_hash})
            exit_code = 2 if report['unmeasured'] else 1 if report['regressed'] else 0
        if args.output:
            outputs.insert(0, (args.output, report))
        _write_outputs(outputs)
        _stdout(report)
        return exit_code
    except RoutingError as error:
        _stdout({'kind': 'skill_routing_error', 'schema_version': 1, 'code': error.code})
        return 2
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        _stdout({'kind': 'skill_routing_error', 'schema_version': 1, 'code': 'operation_failed'})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
