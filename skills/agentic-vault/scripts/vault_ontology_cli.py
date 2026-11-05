#!/usr/bin/env python3
"""Explicit read-only ontology capture, currentness, lookup and plan preflight."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from vault_evidence import EvidenceError, decode, encode, regular, stable_read, stamp
from vault_healthcheck import HealthcheckError
from vault_ontology import (
    MAX_JSON_BYTES, _with_registry, bounded_json, capture_registry, check_registry, policy,
    unavailable,
)
import vault_ontology_query as query


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EvidenceError('invalid_arguments')


def _load_input(reader, relative, inputs):
    path = reader.path(relative, '.json')
    canonical = path.relative_to(reader.root).as_posix()
    if canonical in inputs:
        return inputs[canonical][2]
    info, mark, data = stable_read(lambda: reader.path(canonical, '.json'),
                                   MAX_JSON_BYTES, contents=True)
    value = decode(data)
    bounded_json(value)
    inputs[canonical] = (info, mark, value)
    return value


def _recheck_inputs(reader, inputs):
    for relative, (expected, mark, _value) in inputs.items():
        info, current_mark, _data = stable_read(lambda: reader.path(relative, '.json'),
                                              MAX_JSON_BYTES, contents=False)
        if info != expected or current_mark != mark:
            raise EvidenceError('input_changed')
    # A later hash read must not leave a previously inspected input replaced.
    for relative, (_expected, mark, _value) in inputs.items():
        if stamp(reader.path(relative, '.json').lstat()) != mark:
            raise EvidenceError('input_changed')
    reader.recheck()


def _sweep_inputs(reader, inputs):
    """Metadata-only final sweep; no hash/config/source reads follow this phase."""
    for relative, (_expected, mark, _value) in inputs.items():
        metadata = reader.path(relative, '.json').lstat()
        regular(metadata)
        if stamp(metadata) != mark:
            raise EvidenceError('input_changed')
    from vault_memory import CONFIG_PATH
    metadata = reader.path(CONFIG_PATH, '.json').lstat()
    regular(metadata)
    if stamp(metadata) != reader.config_mark:
        raise EvidenceError('configuration_changed')


def run_operation(vault, command, *, input_path=None, registry_path=None,
                  text=None, term_id=None):
    """Run one allowlisted operation, binding every explicitly named JSON file."""
    try:
        if not isinstance(vault, Path):
            raise EvidenceError('invalid_vault')
        if command == 'capture':
            # Capture already binds its single JSON input and all sources in
            # one policy session, and sweeps them immediately before return.
            return capture_registry(vault, input_path)
        reader = policy(vault)
        inputs = {}
        if command not in {'check', 'search-terms', 'get-term', 'check-answerable'}:
            raise EvidenceError('invalid_operation')
        registry = _load_input(reader, registry_path, inputs)
        request = (_load_input(reader, input_path, inputs)
                   if command == 'check-answerable' else None)

        def build(current):
            if command == 'check':
                result = check_registry(vault, current)
            elif command == 'search-terms':
                result = query.search_terms(vault, current, text)
            elif command == 'get-term':
                result = query.get_term(vault, current, term_id)
            else:
                result = query.check_answerable(vault, current, request)
            # The outer session still owns all source/declaration marks. These
            # JSON hashes must finish BEFORE its final source sweep, never after.
            _recheck_inputs(reader, inputs)
            if result.get('status') == 'unavailable':
                raise EvidenceError('unavailable_operation')
            return result

        result = _with_registry(vault, registry, build)
        # A source hash read in the outer session must not leave an earlier
        # JSON input silently replaced. Finish with metadata only, not another
        # alternating round of independent JSON/source hash reads.
        _sweep_inputs(reader, inputs)
        return result
    except (EvidenceError, HealthcheckError, OSError, ValueError, TypeError,
            KeyError, RecursionError, OverflowError, RuntimeError):
        return unavailable('invalid_or_changed_input')


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument('--vault', required=True, type=Path)
        sub = parser.add_subparsers(dest='command', required=True)
        sub.add_parser('capture').add_argument('--input', required=True)
        sub.add_parser('check').add_argument('--registry', required=True)
        search = sub.add_parser('search-terms')
        search.add_argument('--registry', required=True)
        search.add_argument('--text', required=True)
        get = sub.add_parser('get-term')
        get.add_argument('--registry', required=True)
        get.add_argument('--id', required=True, dest='term_id')
        answer = sub.add_parser('check-answerable')
        answer.add_argument('--registry', required=True)
        answer.add_argument('--input', required=True)
        args = parser.parse_args(argv)
        result = run_operation(args.vault, args.command,
                               input_path=getattr(args, 'input', None),
                               registry_path=getattr(args, 'registry', None),
                               text=getattr(args, 'text', None),
                               term_id=getattr(args, 'term_id', None))
    except (EvidenceError, ValueError, TypeError):
        result = unavailable('invalid_arguments')
    data = encode(result) + b'\n'
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout.buffer.write(data)
    else:
        sys.stdout.write(data.decode('ascii'))
    if result.get('status') == 'unavailable':
        return 2
    if result.get('type') == 'ontology_registry' or result.get('status') in {
            'current', 'matched', 'ready_for_evidence'}:
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
