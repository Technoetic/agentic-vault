#!/usr/bin/env python3
"""Explicit, data-only knowledge dependency inventory and change review."""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from vault_evidence import EvidenceError, decode, encode, digest, stable_read
from vault_healthcheck import HealthcheckError
from vault_memory import _Policy, _bounded_input, MAX_JSON_BYTES, MAX_FILE_BYTES
from vault_paths import relative_parts

HASH = re.compile(r'[0-9a-f]{64}\Z')
MAX_CLAIMS = 128
MAX_SOURCES = 8


def unavailable(reason):
    return {'type':'knowledge_impact_review', 'authority':'data_only', 'schema_version':1,
            'status':'unavailable', 'affected_notes':[], 'claims':[], 'reason':reason}


def policy(vault):
    if not isinstance(vault, Path):
        raise EvidenceError('invalid_vault')
    return _Policy(vault, {'diagnostics':{'binding_bytes_read':0}})


def path_name(value):
    parts = relative_parts(value, 'path')
    if len(value) > 1024 or not value.endswith('.md') or '\\' in value:
        raise EvidenceError('invalid_path')
    return '/'.join(parts)


def claim_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise EvidenceError('invalid_claim_id')
    return value


def declarations(value):
    _bounded_input([value])
    if not isinstance(value, dict) or set(value) != {'schema_version','claims'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise EvidenceError('invalid_declarations')
    claims = value['claims']
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
        raise EvidenceError('invalid_claims')
    seen = set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'claim_id','note','sources'}:
            raise EvidenceError('invalid_claim')
        ident = claim_id(claim['claim_id'])
        if ident in seen: raise EvidenceError('duplicate_claim')
        seen.add(ident); note = path_name(claim['note'])
        sources = claim['sources']
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
            raise EvidenceError('invalid_sources')
        paths = [path_name(p) for p in sources]
        if len({p.casefold() for p in paths}) != len(paths) or note.casefold() in {p.casefold() for p in paths}:
            raise EvidenceError('duplicate_path')
    return claims


def capture_inventory(vault: Path, declarations_data: dict) -> dict:
    try:
        claims = declarations(declarations_data); p = policy(vault)
        records = []
        for claim in sorted(claims, key=lambda c:c['claim_id']):
            note = p.source(claim['note'])[0]
            sources = [p.source(s)[0] for s in sorted(claim['sources'])]
            records.append({'claim_id':claim['claim_id'], 'note':note, 'sources':sources})
        p.recheck()
        result = {'type':'knowledge_dependency_inventory', 'schema_version':1,
                  'config_sha256':p.config_info['sha256'], 'claims':records}
        result['inventory_sha256'] = digest(encode(result))
        inventory_schema(result)
        # Reserve portable CRLF stdout bytes, including Windows translation.
        if len(encode(result)) + 2 > MAX_JSON_BYTES: raise EvidenceError('inventory_too_large')
        return result
    except (EvidenceError, HealthcheckError, OSError, ValueError, TypeError, RecursionError, OverflowError):
        return unavailable('capture_unavailable')


def inventory_schema(inv):
    _bounded_input([inv])
    if not isinstance(inv, dict) or set(inv) != {'type','schema_version','config_sha256','claims','inventory_sha256'} or inv['type'] != 'knowledge_dependency_inventory' or type(inv['schema_version']) is not int or inv['schema_version'] != 1:
        raise EvidenceError('invalid_inventory')
    for key in ('config_sha256','inventory_sha256'):
        if not isinstance(inv[key], str) or not HASH.fullmatch(inv[key]): raise EvidenceError('invalid_hash')
    if digest(encode({k:v for k,v in inv.items() if k != 'inventory_sha256'})) != inv['inventory_sha256']:
        raise EvidenceError('integrity_mismatch')
    simple = []
    fingerprints = {}
    for claim in inv['claims'] if isinstance(inv['claims'], list) else []:
        if not isinstance(claim, dict) or set(claim) != {'claim_id','note','sources'} or not isinstance(claim['sources'], list): raise EvidenceError('invalid_claim')
        for record in [claim['note'], *claim['sources']]:
            if not isinstance(record, dict) or set(record) != {'path','sha256','size'}: raise EvidenceError('invalid_binding')
            path_name(record['path'])
            if not isinstance(record['sha256'], str) or not HASH.fullmatch(record['sha256']) or type(record['size']) is not int or not 0 <= record['size'] <= MAX_FILE_BYTES: raise EvidenceError('invalid_binding')
            folded = record['path'].casefold()
            if folded in fingerprints and fingerprints[folded] != record: raise EvidenceError('conflicting_binding')
            fingerprints[folded] = record
        simple.append({'claim_id':claim['claim_id'], 'note':claim['note']['path'], 'sources':[s['path'] for s in claim['sources']]})
    declarations({'schema_version':1,'claims':simple})


def check_inventory(vault: Path, inventory: dict) -> dict:
    try:
        inventory_schema(inventory); p = policy(vault)
        if p.config_info['sha256'] != inventory['config_sha256']: raise EvidenceError('configuration_changed')
        states = {}
        bindings = {}
        def observe(reader, binding):
            try:
                observed = reader.source(binding['path'])[0]
                return ('current' if observed == binding else 'changed', observed)
            except FileNotFoundError:
                return ('missing', None)
            except EvidenceError as exc:
                if str(exc) in ('file_changed_during_read', 'source_changed', 'configuration_changed', 'input_changed'):
                    raise
                return ('blocked', None)
            except (OSError, ValueError):
                return ('blocked', None)
        def state(binding):
            path = binding['path']
            if path not in states:
                status, observed = observe(p, binding)
                states[path] = status
                bindings[path] = (binding, observed)
            return states[path]
        results = []
        for claim in sorted(inventory['claims'], key=lambda c:c['claim_id']):
            note = claim['note']['path']; note_status = state(claim['note'])
            sources = [{'path':s['path'], 'status':state(s)} for s in sorted(claim['sources'], key=lambda s:s['path'])]
            status = 'current' if note_status == 'current' and all(s['status'] == 'current' for s in sources) else 'review_required'
            results.append({'claim_id':claim['claim_id'],'note':note,'status':status,'note_status':note_status,'sources':sources})
        p.recheck()
        second = policy(vault)
        if second.config_info != p.config_info: raise EvidenceError('configuration_changed')
        for path, (binding, observed) in bindings.items():
            status2, observed2 = observe(second, binding)
            if status2 != states[path] or observed2 != observed: raise EvidenceError('source_changed')
        second.recheck()
        p.fresh()
        affected = sorted({c['note'] for c in results if c['status'] != 'current'})
        return {'type':'knowledge_impact_review','authority':'data_only','schema_version':1,
                'status':'review_required' if affected else 'current','affected_notes':affected,'claims':results}
    except (EvidenceError, HealthcheckError, OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return unavailable('invalid_or_changed_inventory')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', required=True, type=Path)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('capture','check'):
        subs.add_parser(command).add_argument('--input', required=True)
    args = parser.parse_args(argv)
    try:
        p = policy(args.vault)
        info, mark, data = stable_read(lambda:p.path(args.input, '.json'), MAX_JSON_BYTES, contents=True)
        obj = decode(data)
        result = capture_inventory(args.vault, obj) if args.command == 'capture' else check_inventory(args.vault, obj)
        new_info, new_mark, _ = stable_read(lambda:p.path(args.input, '.json'), MAX_JSON_BYTES)
        p.fresh()
        if info != new_info or mark != new_mark: result = unavailable('input_changed')
    except (EvidenceError, HealthcheckError, OSError, ValueError, TypeError, RecursionError, OverflowError):
        result = unavailable('invalid_input')
    print(encode(result).decode('ascii'))
    return 2 if result.get('status') == 'unavailable' else 1 if result.get('status') == 'review_required' else 0

if __name__ == '__main__': sys.exit(main())
