"""Replay independently authored structured facts and synthetic vault changes offline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'agentic-vault' / 'scripts'))
DEFAULT = ROOT / 'tests' / 'fixtures' / 'knowledge_evolution' / 'history.json'
LABELS = ('supported', 'stale', 'invented', 'unverified')


def classify_claim(key, value, current, history):
    if key not in current:
        return 'unverified'
    if current[key] == value:
        return 'supported'
    if any(key in facts and facts[key] == value for facts in history):
        return 'stale'
    return 'invented'


def load_fixture(path):
    with path.open('rb') as handle:
        data = handle.read(65537)
    if len(data) > 65536:
        raise ValueError('fixture too large')
    obj = json.loads(data.decode('utf-8-sig'))
    if not isinstance(obj, dict) or obj.get('schema_version') != 1:
        raise ValueError('invalid fixture')
    checkpoints = obj.get('checkpoints')
    if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 20:
        raise ValueError('invalid checkpoints')
    for c in checkpoints:
        if not isinstance(c, dict) or set(c) != {'name', 'source', 'owner', 'facts', 'claims', 'expected_status', 'expected_source', 'expected_owner'}:
            raise ValueError('invalid checkpoint')
        if not isinstance(c['name'], str) or not c['name'].isalnum() or len(c['name']) > 40:
            raise ValueError('invalid checkpoint name')
        if c['source'] is not None and (not isinstance(c['source'], str) or len(c['source']) > 4096):
            raise ValueError('invalid source')
        if not isinstance(c['owner'], str) or len(c['owner']) > 4096:
            raise ValueError('invalid owner')
        if not isinstance(c['facts'], dict) or len(c['facts']) > 30 or not all(isinstance(k, str) and isinstance(v, str) and len(k) <= 100 and len(v) <= 100 for k, v in c['facts'].items()):
            raise ValueError('invalid facts')
        if not isinstance(c['claims'], list) or len(c['claims']) > 30:
            raise ValueError('invalid claims')
        for claim in c['claims']:
            if not isinstance(claim, dict) or set(claim) != {'key', 'value', 'expected'} or not all(isinstance(claim[k], str) and len(claim[k]) <= 100 for k in claim) or claim['expected'] not in LABELS:
                raise ValueError('invalid claim')
        if c['expected_status'] not in ('current', 'review_required', 'unavailable') or c['expected_source'] not in ('current', 'changed', 'missing', 'blocked') or c['expected_owner'] not in ('current', 'changed', 'missing', 'blocked'):
            raise ValueError('invalid expectations')
    if checkpoints[0]['source'] is None:
        raise ValueError('baseline source missing')
    return checkpoints


def evaluate(checkpoints):
    from vault_impact import capture_inventory, check_inventory
    from vault_okf import ExportError, export_bundle
    history, results = [], []
    pooled = {label: 0 for label in LABELS}
    matches = True
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp).resolve()
        vault = base / 'vault'
        (vault / '00-meta').mkdir(parents=True)
        (vault / '20-knowledge').mkdir()
        (vault / '00-meta/vault-config.json').write_text('{"required_keys":["title"],"enums":{}}', encoding='utf-8')
        owner, source = vault / '20-knowledge/owner.md', vault / '20-knowledge/source.md'
        owner.write_text(checkpoints[0]['owner'], encoding='utf-8')
        source.write_text(checkpoints[0]['source'], encoding='utf-8')
        decl = {'schema_version': 1, 'claims': [{'claim_id': 'color', 'note': '20-knowledge/owner.md', 'sources': ['20-knowledge/source.md']}]}
        inventory = capture_inventory(vault, decl)
        for i, c in enumerate(checkpoints):
            owner.write_text(c['owner'], encoding='utf-8')
            if c['source'] is None:
                source.unlink(missing_ok=True)
            else:
                source.write_text(c['source'], encoding='utf-8')
            impact = check_inventory(vault, inventory)
            item = impact.get('claims', [{}])[0]
            actual = {'status': impact.get('status'), 'source': item.get('sources', [{}])[0].get('status'), 'owner': item.get('note_status'), 'affected_notes': impact.get('affected_notes')}
            expected = {'status': c['expected_status'], 'source': c['expected_source'], 'owner': c['expected_owner'], 'affected_notes': [] if c['expected_status'] == 'current' else ['20-knowledge/owner.md']}
            ok = actual == expected
            counts = {label: 0 for label in LABELS}
            assessments = []
            for claim in c['claims']:
                label = classify_claim(claim['key'], claim['value'], c['facts'], history)
                counts[label] += 1
                pooled[label] += 1
                assessments.append({'key': claim['key'], 'value': claim['value'], 'actual': label, 'expected': claim['expected']})
                ok = ok and label == claim['expected']
            notes = ['20-knowledge/owner.md'] + (['20-knowledge/source.md'] if source.exists() else [])
            dest = base / ('bundle' + str(i))
            try:
                manifest = export_bundle(vault, notes, dest)
            except ExportError as exc:
                raise ValueError('export unavailable') from exc
            hashes_ok = all(hashlib.sha256((dest / x['path']).read_bytes()).hexdigest() == x['sha256'] for x in manifest['outputs'])
            hashes_ok = hashes_ok and all(hashlib.sha256((vault / x['path']).read_bytes()).hexdigest() == x['sha256'] for x in manifest['sources'])
            exported = (dest / manifest['path_mapping']['20-knowledge/owner.md']).read_text(encoding='utf-8')
            link_ok = ('[Source](source.md)' in exported) if source.exists() else ('[Source](source.md)' not in exported)
            ok = ok and hashes_ok and link_ok
            matches = matches and ok
            results.append({'name': c['name'], 'impact': actual, 'expected_impact': expected, 'export_hashes_match': hashes_ok, 'export_link_invariant': link_ok, 'claims': assessments, 'counts': counts, 'denominator': len(c['claims']), 'supported_rate': counts['supported'] / len(c['claims']) if c['claims'] else None, 'matches': ok})
            history.append(c['facts'])
    denominator = sum(pooled.values())
    return {'matches': matches, 'checkpoints': results, 'pooled': {'counts': pooled, 'denominator': denominator, 'supported_rate': pooled['supported'] / denominator if denominator else None}, 'limitations': 'Explicit authored facts only; no natural-language truth extraction. Completeness and topic coverage are limited to the fixture claims and sources.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', nargs='?', type=Path, default=DEFAULT)
    args = parser.parse_args(argv)
    try:
        result = evaluate(load_fixture(args.fixture))
    except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError, RecursionError, json.JSONDecodeError):
        print(json.dumps({'error': 'invalid or unavailable fixture or evaluator'}))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result['matches'] else 1


if __name__ == '__main__':
    sys.exit(main())
