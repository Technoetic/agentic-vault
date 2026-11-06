from __future__ import annotations
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    bundle = importlib.import_module('vault_bundle')
except ModuleNotFoundError:
    bundle = None
import vault_state as state

def sha(raw): return hashlib.sha256(raw).hexdigest()

class BundleFixture:
    def setUp(self):
        self.assertIsNotNone(bundle, 'recoverable bundle implementation missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.write('00-meta/vault-config.json', json.dumps(dict(deny_zones=['private'], exclude_dirs=['excluded'], handoff_note='notes/handoff.md', hot_note='notes/hot.md', log_note='notes/log.md')).encode())
        self.original = 'α\r\nold\r\n'.encode()
        self.write('notes/a.md', self.original)
        self.write('notes/b.md', b'before\r\n')
        self.write('notes/source.md', b'evidence')
        self.request = dict(schema_version=1, targets=[dict(path='notes/a.md', expected_sha256=sha(self.original), edits=[dict(start=4,end=7,text='new')]), dict(path='notes/b.md',expected_sha256=sha(b'before\r\n'),edits=[dict(start=0,end=6,text='after')])], source_hashes={'notes/source.md':sha(b'evidence')})
    def write(self, name, raw):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path
    def prepare(self):
        preview = bundle.prepare_bundle(self.vault, self.request)
        self.assertEqual(preview['status'], 'preview')
        return bundle.prepare_bundle(self.vault,self.request,apply=True,expected_plan_sha256=preview['plan_sha256'])
class BundleTests(BundleFixture, unittest.TestCase):
    def test_preview_and_explicit_authorization(self):
        before = sorted(str(p) for p in self.vault.rglob('*'))
        result = bundle.prepare_bundle(self.vault,self.request)
        self.assertEqual(before,sorted(str(p) for p in self.vault.rglob('*')))
        self.assertEqual(result['status'],'preview')
        self.assertEqual(bundle.prepare_bundle(self.vault,self.request,apply=True)['status'],'unavailable')
    def test_happy_utf8_crlf_idempotent_and_dry_apply(self):
        prepared = self.prepare()
        kw = dict(expected_plan_sha256=prepared['plan_sha256'])
        self.assertEqual(bundle.apply_bundle(self.vault,prepared['bundle_id'],**kw)['status'],'pending')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
        result = bundle.apply_bundle(self.vault,prepared['bundle_id'],apply=True,**kw)
        self.assertEqual(result['status'],'completed')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),'α\r\nnew\r\n'.encode())
        self.assertEqual(bundle.recover_bundle(self.vault,prepared['bundle_id'],apply=True,**kw)['status'],'completed')
    def test_recovery_after_replace_before_receipt(self):
        prepared = self.prepare()
        real = bundle._write_receipt
        with mock.patch.object(bundle,'_write_receipt',side_effect=OSError('SECRET')):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'interrupted')
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),'α\r\nnew\r\n'.encode())
        self.assertEqual(bundle.bundle_status(self.vault,prepared['bundle_id'])['targets'][0]['state'],'applied')
        result=bundle.recover_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'completed')
    def test_preflight_unknown_blocks_all_writes(self):
        prepared=self.prepare()
        self.write('notes/b.md',b'unknown')
        result=bundle.recover_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
    def test_real_cooperating_lock_contention(self):
        prepared=self.prepare()
        with state.advisory_lock(self.vault,'note:notes/b.md'):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'interrupted')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
    def test_changed_source_and_config(self):
        for change in ('source','config'):
            with self.subTest(change=change):
                prepared=self.prepare()
                path=self.vault/('notes/source.md' if change=='source' else state.CONFIG_PATH)
                original=path.read_bytes()
                path.write_bytes(b'changed' if change=='source' else original+b' ')
                result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
                self.assertIn(result['status'],('conflict','unavailable'))
                self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
                path.write_bytes(original)
    def test_shape_bounds_and_aliases(self):
        for mutate in (lambda r:r.update(schema_version=True), lambda r:r['targets'].append(r['targets'][0]), lambda r:r['targets'][0]['edits'][0].update(start=True),lambda r:r['source_hashes'].update({'notes/a.md':sha(self.original)}),lambda r:r['targets'][0].update(path='../a.md')):
            r=json.loads(json.dumps(self.request)); mutate(r)
            self.assertEqual(bundle.prepare_bundle(self.vault,r)['status'],'unavailable')
    def test_journal_forged_progress_never_authorizes_unknown_bytes(self):
        prepared=self.prepare()
        path=self.vault/state.RUNTIME_DIR/('bundle-'+prepared['bundle_id']+'.json')
        journal=json.loads(path.read_bytes()); journal['progress']=[0,1]
        path.write_text(json.dumps(journal),encoding='utf-8')
        self.write('notes/a.md',b'unknown')
        result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'conflict')
    def test_journal_tamper_and_expected_digest(self):
        prepared=self.prepare()
        self.assertEqual(bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256='0'*64,apply=True)['status'],'unavailable')
        path=self.vault/state.RUNTIME_DIR/('bundle-'+prepared['bundle_id']+'.json')
        journal=json.loads(path.read_bytes()); journal['plan']['targets'][0]['after_text']='forged'
        path.write_text(json.dumps(journal),encoding='utf-8')
        self.assertEqual(bundle.bundle_status(self.vault,prepared['bundle_id'])['status'],'unavailable')


class BundleAdversarialTests(BundleFixture, unittest.TestCase):
    def test_failure_at_each_replace_or_receipt_boundary(self):
        for boundary in ('replace0','replace1','receipt0','receipt1'):
            with self.subTest(boundary=boundary):
                self.write('notes/a.md',self.original); self.write('notes/b.md',b'before\r\n')
                prepared=self.prepare(); real_replace=os.replace; real_receipt=bundle._write_receipt
                counts={'replace':0,'receipt':0}
                def replace(src,dst):
                    if Path(dst).suffix=='.md':
                        number=counts['replace']; counts['replace']+=1
                        if boundary=='replace'+str(number): raise OSError('private-body')
                    return real_replace(src,dst)
                def receipt(*args):
                    number=counts['receipt']; counts['receipt']+=1
                    if boundary=='receipt'+str(number): raise OSError('private-body')
                    return real_receipt(*args)
                with mock.patch.object(bundle.os,'replace',side_effect=replace), mock.patch.object(bundle,'_write_receipt',side_effect=receipt):
                    result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
                self.assertEqual(result['status'],'interrupted')
                self.assertNotIn('private-body',json.dumps(result))
                result=bundle.recover_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
                self.assertEqual(result['status'],'completed')
    def test_unknown_appears_after_first_receipt_halts_next_write(self):
        prepared=self.prepare(); real=bundle._write_receipt
        def receipt(*args):
            value=real(*args)
            self.write('notes/a.md',b'unknown')
            return value
        with mock.patch.object(bundle,'_write_receipt',side_effect=receipt):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),b'unknown')
        self.assertEqual((self.vault/'notes/b.md').read_bytes(),b'before\r\n')
    def test_noop_never_replaces_notes(self):
        request=json.loads(json.dumps(self.request))
        for target in request['targets']: target['edits']=[]
        p=bundle.prepare_bundle(self.vault,request)
        prepared=bundle.prepare_bundle(self.vault,request,apply=True,expected_plan_sha256=p['plan_sha256'])
        real=os.replace
        def replace(src,dst):
            self.assertNotEqual(Path(dst).suffix,'.md')
            return real(src,dst)
        with mock.patch.object(bundle.os,'replace',side_effect=replace):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=p['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'completed')
    def test_candidate_and_target_and_edit_and_source_caps(self):
        for kind in ('candidate','target','edits','sources','existing'):
            request=json.loads(json.dumps(self.request))
            if kind=='candidate':
                for t in request['targets']: t['edits']=[dict(start=0,end=0,text='x'*(129*1024))]
            elif kind=='target': request['targets']*=5
            elif kind=='edits': request['targets'][0]['edits']=[dict(start=0,end=0,text='')]*129
            elif kind=='sources': request['source_hashes']={f'notes/s{i}.md':sha(b'x') for i in range(17)}
            else:
                self.write('notes/a.md',b'x'*(state.MAX_BYTES+1)); request['targets'][0]['expected_sha256']=sha(b'x'*(state.MAX_BYTES+1))
            self.assertEqual(bundle.prepare_bundle(self.vault,request)['status'],'unavailable',kind)
    def test_hardlink_and_symlink_never_followed(self):
        for kind in ('hardlink','symlink'):
            with self.subTest(kind=kind):
                target=self.vault/'notes/a.md'; target.unlink(); outside=self.vault/'outside.md'; outside.write_bytes(self.original)
                try:
                    if kind=='hardlink': os.link(outside,target)
                    else: target.symlink_to(outside)
                except OSError:
                    self.write('notes/a.md',self.original)
                    continue
                self.assertEqual(bundle.prepare_bundle(self.vault,self.request)['status'],'unavailable')
                self.assertEqual(outside.read_bytes(),self.original)
                target.unlink(); self.write('notes/a.md',self.original)
    def test_denied_target_or_source_during_recovery(self):
        for selected in ('notes/a.md','notes/source.md'):
            prepared=self.prepare(); config=self.vault/state.CONFIG_PATH; original=config.read_bytes(); value=json.loads(original)
            value['deny_zones'].append(selected); config.write_text(json.dumps(value),encoding='utf-8')
            result=bundle.recover_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
            self.assertEqual(result['status'],'unavailable')
            self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
            config.write_bytes(original)
    def test_lease_expiry_and_interrupted_guard(self):
        prepared=self.prepare()
        with mock.patch.object(state,'_check_lease',side_effect=state.StateError('lock_expired')):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'interrupted')
        key='note:notes/a.md'; guard=self.vault/state.RUNTIME_DIR/('lock-'+sha(key.encode())+'.json.guard')
        guard.write_text('{}',encoding='utf-8')
        result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'interrupted')
        self.assertTrue(guard.exists())
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
    def test_journal_strict_json_and_bounded_read(self):
        prepared=self.prepare(); path=self.vault/state.RUNTIME_DIR/('bundle-'+prepared['bundle_id']+'.json')
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"schema_version":NaN}', b'['*20+b']'*20, b' '*(bundle.MAX_JOURNAL_BYTES+1)):
            path.write_bytes(raw)
            self.assertEqual(bundle.bundle_status(self.vault,prepared['bundle_id'])['status'],'unavailable')
    def test_cli_preview_dry_flags_and_strict_arguments(self):
        import contextlib, io
        self.write('input.json',json.dumps(self.request).encode())
        before=sorted(str(p) for p in self.vault.rglob('*'))
        output=io.StringIO()
        with contextlib.redirect_stdout(output): code=bundle.main(['--vault',str(self.vault),'prepare','--input','input.json'])
        self.assertEqual(code,0); self.assertEqual(json.loads(output.getvalue())['status'],'preview')
        self.assertEqual(before,sorted(str(p) for p in self.vault.rglob('*')))
        for tail in (['--inp','input.json'],['--input','input.json','--input','input.json'],['--input','../private.json'],['--input','input.json','--apply','--apply']):
            with contextlib.redirect_stdout(io.StringIO()): code=bundle.main(['--vault',str(self.vault),'prepare']+tail)
            self.assertEqual(code,2)
    def test_input_mutation_while_observing_is_unavailable(self):
        real=state._Policy.read_note; changed=False
        def read(policy,relative):
            nonlocal changed
            result=real(policy,relative)
            if relative=='notes/b.md' and not changed:
                changed=True; self.write('notes/a.md',b'unknown')
            return result
        with mock.patch.object(state._Policy,'read_note',read): result=bundle.prepare_bundle(self.vault,self.request)
        self.assertEqual(result['status'],'conflict')
    def test_target_changes_during_final_source_read_never_clobbered(self):
        prepared=self.prepare(); real_read=state._Policy.read_note; real_atomic=state._atomic; armed=False; changed=False
        def atomic(path,*args,**kwargs):
            nonlocal armed
            if path.suffix=='.md': armed=True
            return real_atomic(path,*args,**kwargs)
        def read(policy,relative):
            nonlocal changed
            value=real_read(policy,relative)
            if armed and not changed and relative=='notes/source.md':
                changed=True; self.write('notes/a.md',b'unknown')
            return value
        with mock.patch.object(state,'_atomic',side_effect=atomic), mock.patch.object(state._Policy,'read_note',read):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),b'unknown')
        self.assertEqual((self.vault/'notes/b.md').read_bytes(),b'before\r\n')
    def test_nonboolean_apply_is_not_authority(self):
        preview=bundle.prepare_bundle(self.vault,self.request)
        result=bundle.prepare_bundle(self.vault,self.request,apply='yes',expected_plan_sha256=preview['plan_sha256'])
        self.assertEqual(result['status'],'unavailable')
    def test_duplicate_apply_flag_with_exact_preview_is_rejected(self):
        import contextlib, io
        self.write('input.json',json.dumps(self.request).encode())
        p=bundle.prepare_bundle(self.vault,self.request)
        with contextlib.redirect_stdout(io.StringIO()):
            code=bundle.main(['--vault',str(self.vault),'prepare','--input','input.json','--expected-plan-sha256',p['plan_sha256'],'--apply','--apply'])
        self.assertEqual(code,2)
    def test_rewritten_journal_after_note_replace_never_overwritten(self):
        prepared=self.prepare(); real=os.replace
        journal_path=self.vault/state.RUNTIME_DIR/('bundle-'+prepared['bundle_id']+'.json')
        def replace(src,dst):
            result=real(src,dst)
            if Path(dst).suffix=='.md':
                value=json.loads(journal_path.read_bytes()); value['progress']=[1]
                journal_path.write_text(json.dumps(value),encoding='utf-8')
            return result
        with mock.patch.object(bundle.os,'replace',side_effect=replace):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual(json.loads(journal_path.read_bytes())['progress'],[1])
        self.assertEqual((self.vault/'notes/b.md').read_bytes(),b'before\r\n')
    def test_missing_source_and_target_fail_before_writes(self):
        for selected in ('notes/a.md','notes/b.md','notes/source.md'):
            prepared=self.prepare(); path=self.vault/selected; raw=path.read_bytes(); path.unlink()
            result=bundle.recover_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
            self.assertEqual(result['status'],'unavailable')
            path.write_bytes(raw)
            self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)
    @unittest.skipUnless(os.name=='nt','Windows sharing retry contract')
    def test_windows_sharing_retry_revalidates_before_replacement(self):
        prepared=self.prepare(); real=os.replace; attempted=False
        def replace(src,dst):
            nonlocal attempted
            if Path(dst).suffix=='.md' and not attempted:
                attempted=True; self.write('notes/a.md',b'unknown')
                error=PermissionError('secret'); error.winerror=32; raise error
            return real(src,dst)
        with mock.patch.object(bundle.os,'replace',side_effect=replace):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'conflict')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),b'unknown')
    def test_parent_replacement_during_stage_is_rejected(self):
        prepared=self.prepare(); real=state._atomic; changed=False
        def atomic(path,*args,**kwargs):
            nonlocal changed
            if path.suffix=='.md' and not changed:
                changed=True
                old=path.parent.with_name('old-notes'); path.parent.rename(old); path.parent.mkdir()
                for source in old.iterdir():
                    (path.parent/source.name).write_bytes(source.read_bytes())
            return real(path,*args,**kwargs)
        with mock.patch.object(state,'_atomic',side_effect=atomic):
            result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=prepared['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual((self.vault/'notes/a.md').read_bytes(),self.original)

    def test_four_targets_digest_deterministic_and_large_private_journal(self):
        request=json.loads(json.dumps(self.request))
        for name in ('c','d'):
            self.write('notes/'+name+'.md',b'old')
            request['targets'].append(dict(path='notes/'+name+'.md',expected_sha256=sha(b'old'),edits=[dict(start=0,end=3,text='x'*40000)]))
        preview=bundle.prepare_bundle(self.vault,request)
        reversed_request=json.loads(json.dumps(request)); reversed_request['targets'].reverse()
        self.assertEqual(preview['plan_sha256'],bundle.prepare_bundle(self.vault,reversed_request)['plan_sha256'])
        prepared=bundle.prepare_bundle(self.vault,request,apply=True,expected_plan_sha256=preview['plan_sha256'])
        journal=self.vault/state.RUNTIME_DIR/('bundle-'+prepared['bundle_id']+'.json')
        self.assertGreater(journal.stat().st_size,state.MAX_RUNTIME_BYTES)
        result=bundle.apply_bundle(self.vault,prepared['bundle_id'],expected_plan_sha256=preview['plan_sha256'],apply=True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['target_count'],4)
    def test_encoded_journal_local_bound_and_invalid_uuid(self):
        request=json.loads(json.dumps(self.request))
        request['targets'][0]['edits']=[dict(start=0,end=len(self.original),text='\x00'*180000)]
        self.assertEqual(bundle.prepare_bundle(self.vault,request)['status'],'unavailable')
        self.assertFalse((self.vault/state.RUNTIME_DIR).exists())
        for value in ('../escape','f'*31,'F'*32,True):
            self.assertEqual(bundle.bundle_status(self.vault,value)['status'],'unavailable')

    @unittest.skipUnless(os.name=='nt','Windows junction policy')
    def test_junction_ancestor_is_rejected_without_reading_target(self):
        try:
            from _winfs import make_junction
        except ImportError:
            from tests._winfs import make_junction
        original=self.vault/'notes'; moved=self.vault/'real-notes'; original.rename(moved)
        make_junction(original,moved)
        self.addCleanup(lambda: original.rmdir() if original.exists() else None)
        result=bundle.prepare_bundle(self.vault,self.request)
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual((moved/'a.md').read_bytes(),self.original)

    def test_source_change_while_runtime_is_ensured_prevents_prepare_journal(self):
        preview=bundle.prepare_bundle(self.vault,self.request); real=state._ensure_runtime
        def ensure(policy):
            result=real(policy)
            import inspect
            callers=[frame.function for frame in inspect.stack() if frame.filename in (state.__file__,bundle.__file__)]
            if callers[0]=='_prepare':
                self.write('notes/source.md',b'changed')
            return result
        with mock.patch.object(state,'_ensure_runtime',side_effect=ensure):
            result=bundle.prepare_bundle(self.vault,self.request,apply=True,expected_plan_sha256=preview['plan_sha256'])
        self.assertEqual(result['status'],'conflict')
        self.assertFalse(list((self.vault/state.RUNTIME_DIR).glob('bundle-*.json')))

    def test_cli_input_change_after_leases_prevents_persistence(self):
        import contextlib, io
        self.write('input.json',json.dumps(self.request).encode())
        preview=bundle.prepare_bundle(self.vault,self.request); real=bundle._leases
        def leases(*args):
            result=real(*args); self.write('input.json',b'{}'); return result
        with mock.patch.object(bundle,'_leases',side_effect=leases), contextlib.redirect_stdout(io.StringIO()):
            code=bundle.main(['--vault',str(self.vault),'prepare','--input','input.json','--expected-plan-sha256',preview['plan_sha256'],'--apply'])
        self.assertEqual(code,2)
        self.assertFalse(list((self.vault/state.RUNTIME_DIR).glob('bundle-*.json')))

    def test_cli_receipt_io_failure_is_interrupted_exit_one(self):
        import contextlib, io
        prepared=self.prepare(); output=io.StringIO()
        with mock.patch.object(bundle,'_write_receipt',side_effect=OSError('secret')), contextlib.redirect_stdout(output):
            code=bundle.main(['--vault',str(self.vault),'apply','--bundle-id',prepared['bundle_id'],'--expected-plan-sha256',prepared['plan_sha256'],'--apply'])
        self.assertEqual(code,1)
        self.assertEqual(json.loads(output.getvalue())['status'],'interrupted')
        self.assertNotIn('secret',output.getvalue())


if __name__ == '__main__':
    unittest.main()
