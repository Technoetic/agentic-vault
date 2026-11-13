#!/usr/bin/env python3
"""Read-only review worklist over an explicitly declared complete claim DAG."""
from __future__ import annotations
import argparse
import heapq
import sys
from pathlib import Path
from vault_evidence import EvidenceError, decode, encode, digest, stable_read, stamp
from vault_healthcheck import HealthcheckError
from vault_memory import _bounded_input, MAX_JSON_BYTES
from vault_impact import inventory_schema, check_inventory, policy

ERRORS = (EvidenceError, HealthcheckError, OSError, ValueError, TypeError, KeyError,
          RecursionError, OverflowError)


def unavailable(reason='invalid_or_changed_plan'):
    return {'type':'knowledge_review_plan','authority':'data_only','schema_version':1,
            'status':'unavailable','reason':reason,'claims':[], 'review_order':[],
            'affected_notes':[], 'actions':[]}


def snapshot(value):
    _bounded_input([value])
    data = encode(value)
    if len(data) + 2 > MAX_JSON_BYTES:
        raise EvidenceError('input_too_large')
    return data, decode(data)


def topology(graph, ids):
    if (not isinstance(graph,dict) or set(graph) != {'schema_version','dependencies'}
            or type(graph['schema_version']) is not int or graph['schema_version'] != 1
            or not isinstance(graph['dependencies'],list)
            or len(graph['dependencies']) != len(ids)):
        raise EvidenceError('invalid_graph')
    parents = {}
    for item in graph['dependencies']:
        if not isinstance(item,dict) or set(item) != {'claim_id','depends_on'}:
            raise EvidenceError('invalid_graph')
        ident, values = item['claim_id'], item['depends_on']
        if (not isinstance(ident,str) or ident not in ids or ident in parents
                or not isinstance(values,list) or len(values)>8
                or any(not isinstance(v,str) or v not in ids or v==ident for v in values)
                or len(set(values)) != len(values)):
            raise EvidenceError('invalid_graph')
        parents[ident] = sorted(values)
    children = {i:[] for i in ids}; degrees = {i:len(parents[i]) for i in ids}
    for child, values in parents.items():
        for parent in values: children[parent].append(child)
    ready = [i for i in ids if not degrees[i]]; heapq.heapify(ready); order=[]
    while ready:
        ident=heapq.heappop(ready); order.append(ident)
        for child in sorted(children[ident]):
            degrees[child]-=1
            if not degrees[child]: heapq.heappush(ready,child)
    if len(order)!=len(ids): raise EvidenceError('cyclic_graph')
    return parents, order


def blocked_mark(p, path):
    # Observe metadata only: never open bytes rejected by source policy.
    try:
        return stamp(p.path(path).lstat())
    except (EvidenceError, OSError, ValueError):
        return None


def observe(vault, inventory):
    p=policy(vault); observations={}
    if p.config_info['sha256'] != inventory['config_sha256']:
        raise EvidenceError('configuration_changed')
    paths=sorted({b['path'] for c in inventory['claims'] for b in [c['note'],*c['sources']]})
    for path in paths:
        try:
            record, mark, _ = p.source(path)
            observations[path]=('read',record,mark)
        except FileNotFoundError:
            observations[path]=('missing',)
        except EvidenceError as exc:
            if str(exc) in ('file_changed_during_read','source_changed','configuration_changed','input_changed'):
                raise
            observations[path]=('blocked',str(exc),blocked_mark(p,path))
        except (OSError,ValueError):
            observations[path]=('blocked',blocked_mark(p,path))
    p.recheck()
    return p, observations


def plan_review(vault: Path, inventory: dict, graph: dict) -> dict:
    try:
        inv_bytes, inv=snapshot(inventory); graph_bytes, dag=snapshot(graph)
        inventory_schema(inv)
        ids={c['claim_id'] for c in inv['claims']}
        parents, order=topology(dag,ids)
        first, observed=observe(vault,inv)
        direct=check_inventory(vault,inv)
        if direct['status']=='unavailable': raise EvidenceError('inventory_unavailable')
        by_id={c['claim_id']:c for c in direct['claims']}
        paths={}; results=[]; affected=[]
        # At most 128 roots * 128 nodes * 8 parent candidates; never enumerate paths.
        for ident in order:
            upstream={}
            for parent in parents[ident]:
                candidates=dict(paths[parent])
                if by_id[parent]['status']=='review_required': candidates[parent]=[parent]
                for root,path in candidates.items():
                    candidate=path+[ident]
                    if root not in upstream or (len(candidate),candidate)<(len(upstream[root]),upstream[root]):
                        upstream[root]=candidate
            paths[ident]=upstream
            status='review_required' if upstream or by_id[ident]['status']=='review_required' else 'current'
            if status=='review_required': affected.append(ident)
            results.append({'claim_id':ident,'status':status,'direct':by_id[ident],
                            'inherited_status':'review_required' if upstream else 'current',
                            'upstream':[{'root_id':r,'path':upstream[r]} for r in sorted(upstream)]})
        result={'type':'knowledge_review_plan','authority':'data_only','schema_version':1,
                'status':'review_required' if affected else 'current',
                'config_sha256':inv['config_sha256'],'inventory_sha256':inv['inventory_sha256'],
                'graph_sha256':digest(graph_bytes),'claims':results,'review_order':affected,
                'affected_notes':sorted({by_id[i]['note'] for i in affected}),
                'actions':[{'claim_id':i,'note':by_id[i]['note'],'action':'review_then_recapture'} for i in affected]}
        if len(encode(result))+2>MAX_JSON_BYTES: raise EvidenceError('output_too_large')
        second, observed2=observe(vault,inv)
        if observed!=observed2 or first.config_info!=second.config_info or first.config_mark!=second.config_mark:
            raise EvidenceError('observations_changed')
        first.fresh(); second.fresh()
        if encode(inventory)!=inv_bytes or encode(graph)!=graph_bytes:
            raise EvidenceError('caller_input_changed')
        return result
    except ERRORS:
        return unavailable()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EvidenceError('invalid_arguments')


def main(argv=None):
    try:
        parser=Parser(description=__doc__,allow_abbrev=False)
        parser.add_argument('--vault',required=True,type=Path)
        parser.add_argument('--inventory',required=True)
        parser.add_argument('--graph',required=True)
        tokens=list(sys.argv[1:] if argv is None else argv)
        for flag in ('--vault','--inventory','--graph'):
            if '--help' not in tokens and '-h' not in tokens and sum(t==flag or t.startswith(flag+'=') for t in tokens)!=1:
                raise EvidenceError('invalid_arguments')
        args=parser.parse_args(tokens); p=policy(args.vault)
        captures=[]; identities=set(); names=set(); objects=[]
        for name in (args.inventory,args.graph):
            path=p.path(name,'.json'); canonical=path.relative_to(p.root).as_posix().casefold()
            info,mark,data=stable_read(lambda:p.path(name,'.json'),MAX_JSON_BYTES,contents=True)
            if canonical in names or mark[:2] in identities: raise EvidenceError('input_alias')
            names.add(canonical); identities.add(mark[:2]); captures.append((name,info,mark)); objects.append(decode(data))
        result=plan_review(args.vault,*objects)
        for name,info,mark in captures:
            latest,newmark,_=stable_read(lambda:p.path(name,'.json'),MAX_JSON_BYTES)
            if latest!=info or newmark!=mark: raise EvidenceError('input_changed')
        p.fresh()
    except ERRORS:
        result=unavailable('invalid_or_changed_input')
    print(encode(result).decode('ascii'))
    return 2 if result['status']=='unavailable' else 1 if result['status']=='review_required' else 0

if __name__=='__main__': sys.exit(main())
