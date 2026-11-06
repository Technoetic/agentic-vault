import copy, importlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
import vault_impact as impact

class ReviewPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name).resolve()
        self.write('00-meta/vault-config.json', json.dumps({'required_keys':['title'], 'enums':{}, 'deny_zones':['90-assets']}))
        for name in ('a','b','c','d','z','source'):
            self.write('notes/'+name+'.md', name)
        self.inv = impact.capture_inventory(self.vault, {'schema_version':1,'claims':[
            {'claim_id':n, 'note':'notes/'+n+'.md','sources':['notes/source.md'] if n=='a' else ['notes/z.md']} for n in ('a','b','c','d')]})
        self.graph = {'schema_version':1,'dependencies':[
            {'claim_id':n,'depends_on':p} for n,p in [('d',['b','c']),('c',['a']),('b',['a']),('a',[])]]}
    def write(self,path,text):
        p=self.vault/path; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text,encoding='utf8')
    def plan(self, graph=None):
        module=importlib.import_module('vault_review_plan')
        return module.plan_review(self.vault,self.inv,self.graph if graph is None else graph)
    def test_transitive_diamond_preserves_direct_behavior(self):
        self.write('notes/source.md','changed')
        direct=impact.check_inventory(self.vault,self.inv)
        self.assertEqual([x['claim_id'] for x in direct['claims'] if x['status']=='review_required'],['a'])
        r=self.plan(); self.assertEqual(r['review_order'],['a','b','c','d'])
        d=r['claims'][-1]; self.assertEqual(d['direct']['status'],'current')
        self.assertEqual(d['upstream'],[{'root_id':'a','path':['a','b','d']}])
        self.assertEqual(d['status'],'review_required')
    def test_current_and_missing(self):
        self.assertEqual(self.plan()['status'],'current')
        (self.vault/'notes/source.md').unlink()
        self.assertEqual(self.plan()['review_order'],['a','b','c','d'])
    def test_invalid_graphs_have_no_partial_order(self):
        cases=[{}, {'schema_version':True,'dependencies':[]}, copy.deepcopy(self.graph)]
        cases[-1]['dependencies'][0]['depends_on']=['unknown']
        for kind in ('missing','duplicate','cycle','self','duplicate_edge','extra'):
            g=copy.deepcopy(self.graph)
            if kind=='missing': g['dependencies'].pop()
            if kind=='duplicate': g['dependencies'].append(g['dependencies'][0])
            if kind=='cycle': g['dependencies'][-1]['depends_on']=['d']
            if kind=='self': g['dependencies'][-1]['depends_on']=['a']
            if kind=='duplicate_edge': g['dependencies'][0]['depends_on']=['b','b']
            if kind=='extra': g['extra']=1
            cases.append(g)
        for g in cases:
            r=self.plan(g); self.assertEqual(r['status'],'unavailable'); self.assertEqual(r['review_order'],[])
    def test_output_limit_and_mutation(self):
        m=importlib.import_module('vault_review_plan')
        with patch.object(m,'MAX_JSON_BYTES',500): self.assertEqual(self.plan()['status'],'unavailable')
        original=m.check_inventory
        def mutate(*args):
            result=original(*args); self.graph['dependencies'].reverse(); return result
        with patch.object(m,'check_inventory',mutate): self.assertEqual(self.plan()['status'],'unavailable')
    def test_content_change_after_direct_check_is_unavailable(self):
        m=importlib.import_module('vault_review_plan'); original=m.check_inventory
        def mutate(*args):
            result=original(*args); self.write('notes/source.md','later'); return result
        with patch.object(m,'check_inventory',mutate): self.assertEqual(self.plan()['status'],'unavailable')
    def test_cli_readonly_strict_inputs_and_redaction(self):
        self.write('inventory.json',json.dumps(self.inv)); self.write('graph.json',json.dumps(self.graph))
        def run(*extra):
            return subprocess.run([sys.executable,'-B',str(SCRIPTS/'vault_review_plan.py'),'--vault',str(self.vault),*extra],capture_output=True,text=True)
        args=['--inventory','inventory.json','--graph','graph.json']
        self.assertEqual(run(*args).returncode,0)
        self.write('notes/source.md','SECRET_PAYLOAD')
        r=run(*args); self.assertEqual(r.returncode,1); self.assertNotIn('SECRET_PAYLOAD',r.stdout+r.stderr)
        for bad in (['--inventory','graph.json','--graph','graph.json'], args+['--graph','graph.json'],['--inv','inventory.json','--graph','graph.json']):
            self.assertEqual(run(*bad).returncode,2)
        self.write('graph.json','{"schema_version":1,"schema_version":1,"dependencies":[]}')
        self.assertEqual(run(*args).returncode,2)
    def test_direct_and_inherited_reasons_unaffected_branch_and_multiple_roots(self):
        self.graph['dependencies'][1]['depends_on']=[]
        self.write('notes/source.md','changed'); self.write('notes/b.md','owner changed')
        r=self.plan(); self.assertEqual(r['review_order'],['a','b','d'])
        claims={c['claim_id']:c for c in r['claims']}
        self.assertEqual(claims['c']['status'],'current')
        self.assertEqual(claims['b']['direct']['note_status'],'changed')
        self.assertEqual(claims['b']['upstream'][0]['root_id'],'a')
        self.assertEqual([p['root_id'] for p in claims['d']['upstream']],['a','b'])
    def test_blocked_root_does_not_read_denied_bytes(self):
        p=self.vault/'notes/source.md'; p.unlink()
        secret=self.vault/'90-assets/secret.md'; secret.parent.mkdir(); secret.write_text('PRIVATE',encoding='utf8')
        try: p.symlink_to(secret)
        except OSError: self.skipTest('symlink unavailable')
        r=self.plan(); self.assertEqual(r['review_order'],['a','b','c','d'])
        self.assertEqual(r['claims'][0]['direct']['sources'][0]['status'],'blocked')
        self.assertNotIn('PRIVATE',json.dumps(r))
    def test_claim_limit_and_parent_limit(self):
        m=importlib.import_module('vault_review_plan')
        declarations={'schema_version':1,'claims':[{'claim_id':str(i).zfill(3),'note':'notes/a.md','sources':['notes/source.md']} for i in range(128)]}
        inv=impact.capture_inventory(self.vault,declarations)
        g={'schema_version':1,'dependencies':[{'claim_id':c['claim_id'],'depends_on':[]} for c in declarations['claims']]}
        self.assertEqual(m.plan_review(self.vault,inv,g)['status'],'current')
        g['dependencies'][-1]['depends_on']=[str(i).zfill(3) for i in range(9)]
        self.assertEqual(m.plan_review(self.vault,inv,g)['status'],'unavailable')
        declarations['claims'].append({'claim_id':'overflow','note':'notes/a.md','sources':['notes/source.md']})
        self.assertEqual(impact.capture_inventory(self.vault,declarations)['status'],'unavailable')
    def test_config_drift_and_graph_shape_fail_closed(self):
        self.write('00-meta/vault-config.json','{}')
        r=self.plan(); self.assertEqual(r['status'],'unavailable'); self.assertEqual(r['review_order'],[])
    def test_inherited_status_is_explicit_and_help_is_available(self):
        self.write('notes/source.md','changed')
        r=self.plan()
        self.assertEqual(r['claims'][0].get('inherited_status'),'current')
        self.assertEqual(r['claims'][-1].get('inherited_status'),'review_required')
        result=subprocess.run([sys.executable,'-B',str(SCRIPTS/'vault_review_plan.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0); self.assertIn('--inventory',result.stdout)

    def test_blocked_invalid_utf8_content_change_is_unavailable(self):
        m=importlib.import_module('vault_review_plan'); original=m.check_inventory
        (self.vault/'notes/source.md').write_bytes(b'\xff')
        def mutate(*args):
            r=original(*args); (self.vault/'notes/source.md').write_bytes(b'\xff\xfe'); return r
        with patch.object(m,'check_inventory',mutate):
            self.assertEqual(self.plan()['status'],'unavailable')
