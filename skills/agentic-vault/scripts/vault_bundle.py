#!/usr/bin/env python3
"""Opt-in forward-recoverable Markdown bundles; no multi-file atomic visibility."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import os
import uuid

import vault_state as state
from vault_evidence import EvidenceError, decode, regular
from vault_paths import relative_parts, resolve_note_path

MAX_JOURNAL_BYTES = 1024 * 1024
MAX_TARGETS = 8
MAX_SOURCES = 16
MAX_EDITS = 128
MAX_AFTER_BYTES = 256 * 1024
MAX_INPUT_BYTES = 1024 * 1024

class _Failure(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason

def _fail(reason, status='unavailable'):
    raise _Failure(status, reason)

def _bounded(value, limit=MAX_INPUT_BYTES):
    # Bound structure before serialization (including hostile Python API inputs).
    count = 0
    def walk(item, depth=0):
        nonlocal count
        count += 1
        if depth > 10 or count > 4096:
            _fail('input_bounds')
        if type(item) is dict:
            for key, val in item.items():
                if type(key) is not str: _fail('invalid_input')
                walk(key, depth+1); walk(val, depth+1)
        elif type(item) is list:
            for val in item: walk(val, depth+1)
        elif type(item) not in (str, int, bool, type(None)):
            _fail('invalid_input')
        elif type(item) is str and len(item) > limit:
            _fail('input_bounds')
    walk(value)
    raw = state._encode(value)
    if len(raw) + 1 > limit: _fail('input_bounds')
    return decode(raw)

def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields): _fail('invalid_shape')

def _hash(value):
    return state._hash(value)

def _name(bundle_id):
    if not isinstance(bundle_id, str) or not state.TOKEN_RE.fullmatch(bundle_id):
        _fail('invalid_bundle_id')
    return 'bundle-' + bundle_id + '.json'

def _parent(path):
    info = path.parent.lstat()
    return info.st_dev, info.st_ino

def _unique(policy, paths):
    names, identities = set(), set()
    observations = {}
    for relative in paths:
        info, mark, raw = policy.read_note(relative)
        canonical = info['path']
        # Canonical spelling is required; aliases cannot authorize another name.
        if relative != canonical: _fail('noncanonical_path')
        key, identity = canonical.casefold(), mark[:2]
        if key in names or identity in identities: _fail('duplicate_path')
        names.add(key); identities.add(identity)
        observations[relative] = (info, mark, raw)
    return observations

def _build(policy, request):
    r = _bounded(request)
    _shape(r, ('schema_version', 'targets', 'source_hashes'))
    if type(r['schema_version']) is not int or r['schema_version'] != 1: _fail('invalid_schema')
    targets, sources = r['targets'], r['source_hashes']
    if type(targets) is not list or not 1 <= len(targets) <= MAX_TARGETS: _fail('target_bounds')
    if type(sources) is not dict or len(sources) > MAX_SOURCES: _fail('source_bounds')
    paths, edits_count = [], 0
    for target in targets:
        _shape(target, ('path', 'expected_sha256', 'edits'))
        _hash(target['expected_sha256'])
        if type(target['edits']) is not list: _fail('invalid_edits')
        edits_count += len(target['edits'])
        for edit in target['edits']:
            _shape(edit, ('start', 'end', 'text'))
            if type(edit['text']) is not str: _fail('invalid_edit')
        paths.append(target['path'])
    if edits_count > MAX_EDITS: _fail('edit_bounds')
    for relative, expected in sources.items(): _hash(expected)
    observed = _unique(policy, paths + list(sources))
    result, total_before, total_after = [], 0, 0
    for target in targets:
        path = target['path']; info, _, raw = observed[path]
        if info['sha256'] != target['expected_sha256']: _fail('stale_base', 'conflict')
        edits = [dict(start=e['start'],end=e['end'],replacement=e['text']) for e in target['edits']]
        after, _ = state._edited(raw, edits)
        total_before += len(raw); total_after += len(after)
        result.append(dict(path=path,before_sha256=info['sha256'],after_sha256=state._sha(after),after_text=after.decode('utf-8')))
    if total_before > 2 * 1024 * 1024 or total_after > MAX_AFTER_BYTES: _fail('bundle_bounds')
    for relative, expected in sources.items():
        if observed[relative][0]['sha256'] != expected: _fail('source_changed', 'conflict')
    plan = dict(schema_version=1, config_sha256=policy.config_sha256,
                targets=sorted(result,key=lambda t:t['path'].casefold()),
                source_hashes=dict(sorted(sources.items(), key=lambda p:p[0].casefold())))
    _validate_plan(plan)
    # Preparation also closes the observation window before returning a digest.
    for relative, old in observed.items():
        current = policy.fresh().read_note(relative)
        if current[:2] != old[:2]: _fail('input_changed', 'conflict')
    return plan

def _validate_plan(plan):
    _bounded(plan, MAX_JOURNAL_BYTES)
    _shape(plan, ('schema_version','config_sha256','targets','source_hashes'))
    if type(plan['schema_version']) is not int or plan['schema_version'] != 1: _fail('invalid_journal')
    _hash(plan['config_sha256'])
    ts, sources = plan['targets'], plan['source_hashes']
    if type(ts) is not list or not 1 <= len(ts) <= MAX_TARGETS: _fail('invalid_journal')
    if type(sources) is not dict or len(sources) > MAX_SOURCES: _fail('invalid_journal')
    paths, total = [], 0
    for t in ts:
        _shape(t, ('path','before_sha256','after_sha256','after_text'))
        _hash(t['before_sha256']); _hash(t['after_sha256'])
        if type(t['path']) is not str or type(t['after_text']) is not str: _fail('invalid_journal')
        raw = t['after_text'].encode('utf-8')
        if len(raw) > state.MAX_BYTES or state._sha(raw) != t['after_sha256']: _fail('invalid_journal')
        total += len(raw); paths.append(t['path'])
    if total > MAX_AFTER_BYTES: _fail('bundle_bounds')
    for relative, expected in sources.items():
        if type(relative) is not str: _fail('invalid_journal')
        _hash(expected)
    all_paths = paths + list(sources)
    if len({p.casefold() for p in all_paths}) != len(all_paths): _fail('duplicate_path')
    if paths != sorted(paths,key=str.casefold): _fail('invalid_journal')

def _digest(plan): return state._sha(state._encode(plan))

def _journal(policy, bundle_id, expected=None):
    name = _name(bundle_id)
    info, mark, raw = state._read(lambda:policy.runtime(name), MAX_JOURNAL_BYTES)
    journal = _bounded(decode(raw), MAX_JOURNAL_BYTES)
    _shape(journal, ('schema_version','bundle_id','plan','plan_sha256','progress'))
    if type(journal['schema_version']) is not int or journal['schema_version'] != 1 or journal['bundle_id'] != bundle_id:
        _fail('invalid_journal')
    _validate_plan(journal['plan'])
    digest = _digest(journal['plan'])
    if journal['plan_sha256'] != digest: _fail('invalid_journal')
    progress = journal['progress']
    if type(progress) is not list or len(progress) > MAX_TARGETS or any(type(i) is not int or not 0 <= i < len(journal['plan']['targets']) for i in progress) or sorted(set(progress)) != progress:
        _fail('invalid_journal')
    if expected is not None and _hash(expected) != digest: _fail('plan_mismatch')
    return journal, (info, mark)

def _observe(policy, plan):
    if policy.fresh().config_sha256 != plan['config_sha256']: _fail('configuration_changed')
    paths = [t['path'] for t in plan['targets']] + list(plan['source_hashes'])
    obs = _unique(policy.fresh(), paths)
    for relative, expected in plan['source_hashes'].items():
        if obs[relative][0]['sha256'] != expected: _fail('source_changed', 'conflict')
    states = []
    for t in plan['targets']:
        actual = obs[t['path']][0]['sha256']
        kind = 'applied' if actual == t['after_sha256'] else 'pending' if actual == t['before_sha256'] else 'conflict'
        states.append(dict(state=kind,sha256=actual))
    # Close observations after all sources have been read. In particular a
    # source read must not leave an earlier target observation stale.
    for relative, prior in obs.items():
        latest = policy.fresh().read_note(relative)
        if latest[:2] != prior[:2]: _fail('input_changed', 'conflict')
    policy.fresh()
    return states

def _summary(journal, states, status=None):
    return dict(status=status or ('conflict' if any(t['state']=='conflict' for t in states) else 'completed' if all(t['state']=='applied' for t in states) else 'pending'),
                bundle_id=journal['bundle_id'],plan_sha256=journal['plan_sha256'],target_count=len(states),
                targets=[dict(index=i,**t) for i,t in enumerate(states)])

def _leases(stack, policy, bundle_id, plan):
    keys = ['bundle:'+bundle_id] + ['note:'+p.casefold() for p in [t['path'] for t in plan['targets']] + list(plan['source_hashes'])]
    return [stack.enter_context(state.advisory_lock(policy.vault,key,wait_seconds=0)) for key in sorted(keys)]

def _guarded(stack, policy, leases):
    for lease in leases:
        stack.enter_context(state._lease_guard(policy,lease))
        state._check_lease(policy.fresh(),lease)

def _check(policy, bundle_id, expected, observation, leases):
    current = policy.fresh()
    for lease in leases: state._check_lease(current,lease)
    journal, latest = _journal(current,bundle_id,expected)
    if latest != observation: _fail('journal_changed','conflict')
    states = _observe(current,journal['plan'])
    if any(t['state']=='conflict' for t in states): _fail('target_changed','conflict')
    # The reads above may have consumed the lease or config generation.
    current.fresh()
    for lease in leases: state._check_lease(current,lease)
    if _journal(current,bundle_id,expected)[1] != observation:
        _fail('journal_changed','conflict')
    return journal, states

def _write_receipt(policy, journal, observation, leases):
    name = _name(journal['bundle_id']); path = policy.runtime(name); parent = _parent(path)
    def replace(temporary):
        with ExitStack() as guards:
            _guarded(guards,policy,leases)
            current, states = _check(policy,journal['bundle_id'],journal['plan_sha256'],observation,leases)
            expected_progress = [i for i,t in enumerate(states) if t['state']=='applied']
            if journal['progress'] != expected_progress: _fail('state_changed','conflict')
            checked = policy.fresh().runtime(name)
            if checked != path or _parent(checked) != parent: _fail('unsafe_parent')
            regular(temporary.lstat()); policy.fresh()
            os.replace(temporary,checked)
    data = state._encode(journal)+b'\n'
    if len(data)>MAX_JOURNAL_BYTES: _fail('journal_bounds')
    state._atomic(path,data,replace,cleanup_guard=lambda t:state._cleanup_temporary(policy,name,t,runtime=True))
    return _journal(policy.fresh(),journal['bundle_id'],journal['plan_sha256'])[1]

def _prepare(vault, request, apply, expected, *, input_check=None):
    if type(apply) is not bool: _fail('invalid_apply')
    policy = state._Policy(vault); plan = _build(policy,request); digest = _digest(plan)
    preview = dict(status='preview',plan_sha256=digest,target_count=len(plan['targets']),source_count=len(plan['source_hashes']))
    if not apply: return preview
    if expected is None or _hash(expected) != digest: _fail('plan_mismatch')
    bundle_id = uuid.uuid4().hex
    journal = dict(schema_version=1,bundle_id=bundle_id,plan=plan,plan_sha256=digest,progress=[])
    data = state._encode(journal)+b'\n'
    if len(data)>MAX_JOURNAL_BYTES: _fail('journal_bounds')
    with ExitStack() as stack:
        leases = _leases(stack,policy,bundle_id,plan)
        with ExitStack() as guards:
            _guarded(guards,policy,leases)
            states = _observe(policy,plan)
            if any(t['state']=='conflict' for t in states): _fail('target_changed','conflict')
            if any(t['sha256'] != p['before_sha256'] for t,p in zip(states,plan['targets'])):
                _fail('stale_base','conflict')
            state._ensure_runtime(policy)
            name = _name(bundle_id); path = policy.runtime(name); parent = _parent(path)
            # Runtime creation and lease acquisition can take time; close all
            # source/target observations again before persisting authorization.
            states = _observe(policy,plan)
            if any(t['sha256'] != p['before_sha256'] for t,p in zip(states,plan['targets'])):
                _fail('stale_base','conflict')
            if input_check is not None: input_check()
            policy.fresh()
            for lease in leases: state._check_lease(policy,lease)
            flags = os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
            fd = os.open(path,flags,0o600)
            with os.fdopen(fd,'wb') as stream:
                regular(os.fstat(stream.fileno()))
                if policy.fresh().runtime(name) != path or _parent(path)!=parent: _fail('unsafe_parent')
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            _journal(policy.fresh(),bundle_id,digest)
    return dict(preview,status='prepared',bundle_id=bundle_id)

def _run(vault,bundle_id,expected,apply):
    if type(apply) is not bool: _fail('invalid_apply')
    policy=state._Policy(vault)
    journal, observation=_journal(policy,bundle_id,expected)
    states=_observe(policy,journal['plan'])
    if not apply or any(t['state']=='conflict' for t in states): return _summary(journal,states)
    if expected is None: _fail('expected_plan_required')
    with ExitStack() as stack:
        leases=_leases(stack,policy,bundle_id,journal['plan'])
        journal, states=_check(policy,bundle_id,expected,observation,leases)
        for index, target in enumerate(journal['plan']['targets']):
            journal, states=_check(policy,bundle_id,expected,observation,leases)
            if states[index]['state']=='pending':
                path=policy.note(target['path']); parent=_parent(path)
                def replace(temporary):
                    with ExitStack() as guards:
                        _guarded(guards,policy,leases)
                        _, actual=_check(policy,bundle_id,expected,observation,leases)
                        if actual[index]['state']!='pending': _fail('state_changed','conflict')
                        checked=policy.fresh().note(target['path'])
                        if checked != path or _parent(checked)!=parent: _fail('unsafe_parent')
                        regular(temporary.lstat()); policy.fresh()
                        os.replace(temporary,checked)
                state._atomic(path,target['after_text'].encode('utf-8'),replace,
                    cleanup_guard=lambda t:state._cleanup_temporary(policy,target['path'],t))
            # Derive receipt from ALL actual hashes, never a stored progress flag.
            journal, states=_check(policy,bundle_id,expected,observation,leases)
            journal['progress']=[i for i,t in enumerate(states) if t['state']=='applied']
            observation=_write_receipt(policy,journal,observation,leases)
        journal, states=_check(policy,bundle_id,expected,observation,leases)
        return _summary(journal,states)

def _safe(call):
    try: return call()
    except _Failure as exc: return dict(status=exc.status,reason=exc.reason)
    except state.StateError as exc:
        if str(exc) in ('lock_busy','lock_expired','lock_owner_changed','invalid_lease','replace_failed'):
            return dict(status='interrupted',reason='lease_or_replace_failed')
        return dict(status='unavailable',reason='unsafe_or_changed_input')
    except (OSError, EvidenceError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, RuntimeError):
        return dict(status='unavailable',reason='invalid_or_unavailable')

def prepare_bundle(vault,request,*,apply=False,expected_plan_sha256=None):
    return _safe(lambda:_prepare(vault,request,apply,expected_plan_sha256))

def bundle_status(vault,bundle_id,*,expected_plan_sha256=None):
    return _safe(lambda:_run(vault,bundle_id,expected_plan_sha256,False))

def apply_bundle(vault,bundle_id,*,expected_plan_sha256,apply=False):
    def run():
        try: return _run(vault,bundle_id,expected_plan_sha256,apply)
        except OSError:
            return dict(status='interrupted',reason='io_interrupted')
    return _safe(run)

def recover_bundle(vault,bundle_id,*,expected_plan_sha256,apply=False):
    return apply_bundle(vault,bundle_id,expected_plan_sha256=expected_plan_sha256,apply=apply)

class _Parser(argparse.ArgumentParser):
    def error(self,message):
        raise _Failure('unavailable','invalid_arguments')

class _Once(argparse.Action):
    def __call__(self,parser,namespace,values,option_string=None):
        if getattr(namespace,self.dest,None) is not None: _fail('duplicate_argument')
        setattr(namespace,self.dest,values)

class _OnceFlag(argparse.Action):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,nargs=0,default=False,**kwargs)
    def __call__(self,parser,namespace,values,option_string=None):
        if getattr(namespace,self.dest): _fail('duplicate_argument')
        setattr(namespace,self.dest,True)

def main(argv=None):
    def execute():
        parser=_Parser(description=__doc__,allow_abbrev=False)
        parser.add_argument('--vault',required=True,action=_Once)
        subs=parser.add_subparsers(dest='command',required=True,parser_class=_Parser)
        for command in ('prepare','status','apply','recover'):
            sub=subs.add_parser(command,allow_abbrev=False)
            sub.add_argument('--expected-plan-sha256',action=_Once)
            if command!='status': sub.add_argument('--apply',action=_OnceFlag)
            sub.add_argument('--input' if command=='prepare' else '--bundle-id',required=True,action=_Once)
        args=parser.parse_args(argv)
        if args.command=='prepare':
            policy=state._Policy(args.vault)
            parts=relative_parts(args.input,'input')
            if parts[-1].casefold().endswith('.json') is False: _fail('invalid_input_path')
            path=lambda:resolve_note_path(policy.vault,args.input,policy.rules)
            info, mark, raw=state._read(path,MAX_INPUT_BYTES)
            request=decode(raw)
            # Do not allow input aliases of config, target or source paths.
            plan=_build(policy,request)
            input_path=path()
            reserved=[state.CONFIG_PATH]+[t['path'] for t in plan['targets']]+list(plan['source_hashes'])
            if input_path.relative_to(policy.root).as_posix()!=args.input or any(input_path==policy.root/p for p in reserved): _fail('input_alias')
            def input_check():
                latest=state._read(path,MAX_INPUT_BYTES)
                if latest[:2]!=(info,mark): _fail('input_changed')
                policy.fresh()
            input_check()
            return _prepare(args.vault,request,args.apply,args.expected_plan_sha256,
                            input_check=input_check)
        if args.command in ('apply','recover'):
            return apply_bundle(args.vault,args.bundle_id,
                                expected_plan_sha256=args.expected_plan_sha256,apply=args.apply)
        return _run(args.vault,args.bundle_id,args.expected_plan_sha256,False)
    result=_safe(execute)
    print(state._encode(result).decode('ascii'))
    return 0 if result['status'] in ('preview','prepared','completed','pending') else 1 if result['status'] in ('conflict','interrupted','review_required') else 2

if __name__=='__main__': raise SystemExit(main())
