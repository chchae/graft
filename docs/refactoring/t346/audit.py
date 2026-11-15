import ast, copy, hashlib, json, os, subprocess
from pathlib import Path
s=json.loads(Path('/tmp/graft-t346-active.json').read_text());R=Path(s['run']);P=Path(s['project']);B=R/'before';ROOT=Path(s['root'])
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def parse(base,file):return ast.parse((base/'src/graft'/file).read_text())
def fn(tree,name):return next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
def dump(x):return ast.dump(x,include_attributes=False)
def body(x):return x.body[1:] if x.body and isinstance(x.body[0],ast.Expr) and isinstance(x.body[0].value,ast.Constant) and isinstance(x.body[0].value.value,str) else x.body
def same(a,b,label):
 assert dump(ast.Module(body=a,type_ignores=[]))==dump(ast.Module(body=b,type_ignores=[])),label
 proof[label]=True
proof={}
class Uncore(ast.NodeTransformer):
 def visit_Attribute(self,n):
  if isinstance(n.value,ast.Name) and n.value.id=='_core':return ast.copy_location(ast.Name(id=n.attr,ctx=n.ctx),n)
  return self.generic_visit(n)
oldcore=parse(B,'_core.py');db=parse(P,'database.py')
names=['open_sqlite','create_sqlite_schema','ensure_schema_columns','set_compound_state','find_iteration_for_state','get_max_iteration','get_iteration_counts','get_existing_canonical_smiles','get_reference_name','state_counts','list_iteration_compounds']
for name in names:
 a=copy.deepcopy(fn(db,name));Uncore().visit(a);same(fn(oldcore,name).body,a.body,'T3 implementation '+name)
# T4 loader: path/filename facade keywords stay distinct; only the first assignment changes.
loader=copy.deepcopy(fn(parse(P,'config.py'),'load_project_toml'));next(n for n in ast.walk(body(loader)[0]) if isinstance(n,ast.Name) and n.id=='path').id='filename';same(fn(oldcore,'load_project_toml').body,loader.body,'T4 loader statements')
# T4 configure side effects are moved in place, not reordered.
newcore=parse(R/'t4-complete','_core.py');main=fn(newcore,'configure_project');init=fn(parse(P,'project.py'),'initialize_project');expanded=Uncore().visit(ast.Module(body=copy.deepcopy(body(init)),type_ignores=[])).body
for i,n in enumerate(main.body):
 if isinstance(n,ast.ImportFrom) and n.module=='graft.project':
  main.body[i:i+2]=expanded;break
same(fn(oldcore,'configure_project').body,main.body,'T4 project initialization sequence')
# Restore the two config helper bodies at their exact original call sites.
a=parse(P,'config.py');main=fn(a,'combichem_3d_settings');merge=fn(a,'_merge_combichem_project_keys');values=fn(a,'_validate_combichem_values')
class ExpandConfig(ast.NodeTransformer):
 def visit_Assign(self,n):
  if isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id==merge.name:
   return copy.deepcopy(body(merge)[:-1])+ast.parse('raw = merged').body
  return self.generic_visit(n)
 def visit_Expr(self,n):
  if isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id==values.name:return copy.deepcopy(body(values))
  return self.generic_visit(n)
ExpandConfig().visit(main);same(fn(parse(B,'config.py'),main.name).body,main.body,'T4 merge and validation sequence')
# T6: expand each helper into the original orchestration body.
def check_expand(file,main_name,helper_name,mode):
 old=parse(R/'t4-complete',file);new=parse(P,file);m=fn(new,main_name);h=fn(new,helper_name);b=copy.deepcopy(body(h))
 if mode=='return':b=b[:-1]
 class Expand(ast.NodeTransformer):
  def visit_If(self,n):
   if mode=='recover' and isinstance(n.test,ast.Compare) and isinstance(n.test.left,ast.Name) and n.test.left.id=='active' and len(n.body)==1 and isinstance(n.body[0],ast.Return):return copy.deepcopy(b)
   return self.generic_visit(n)
  def visit_Assign(self,n):
   if isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id==helper_name:
    if mode=='md':
     items=copy.deepcopy(b);assert isinstance(items[-1],ast.Return);items[-1]=ast.Assign(targets=n.targets,value=items[-1].value);return items
    return copy.deepcopy(b)
   return self.generic_visit(n)
  def visit_Expr(self,n):
   if isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id==helper_name:return copy.deepcopy(b)
   return self.generic_visit(n)
 Expand().visit(m);same(fn(old,main_name).body,m.body,'T6 '+main_name)
check_expand('_core.py','run_iteration_prime_gbsa','_recover_prime_gbsa_work','recover')
check_expand('_core.py','write_elite_libinvent_training_files','_elite_training_rows','return')
check_expand('md_gbsa.py','run','_freeze_new_md_selection','md')
check_expand('chemistry/scaffold.py','prepare_iteration_scaffold','_create_iteration_scaffold','return')
check_expand('stages/glide.py','_run_stage','_recover_reference_groups','return')
check_expand('driver.py','supervise_iteration','_report_completed_stage','expr')
initial=json.loads((R/'input-manifest.json').read_text());changed=sorted(n for n,h in initial.items() if n.startswith('src/') and sha(P/n)!=h);added=sorted(str(p.relative_to(P)) for p in (P/'src').rglob('*.py') if str(p.relative_to(P)) not in initial)
assert added==['src/graft/project.py'],added
assert set(changed)=={'src/graft/_core.py','src/graft/database.py','src/graft/cli.py','src/graft/driver.py','src/graft/config.py','src/graft/md_gbsa.py','src/graft/chemistry/scaffold.py','src/graft/stages/glide.py'},changed
protected=json.loads((ROOT/'docs/refactoring/t2-t9/source-audit.json').read_text())['protected_files'];protected_changes=sorted(n for n in protected if sha(P/n)!=sha(B/n));assert protected_changes==['src/graft/config.py','src/graft/database.py'],protected_changes
signatures=0
for file in changed:
 old=parse(B,str(Path(file).relative_to('src/graft')));new=parse(P,str(Path(file).relative_to('src/graft')))
 def defs(tree,prefix=''):
  found={}
  for n in tree.body:
   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):found[prefix+n.name]=n
   elif isinstance(n,ast.ClassDef):found.update(defs(n,prefix+n.name+'.'))
  return found
 nd=defs(new)
 for name,node in defs(old).items():
  assert name in nd,(file,name);assert dump(node.args)==dump(nd[name].args),(file,name,'args');assert (dump(node.returns) if node.returns else None)==(dump(nd[name].returns) if nd[name].returns else None),(file,name,'returns');signatures+=1
for name,h in initial.items():
 if name.startswith('tests/'):assert sha(P/name)==h,('existing test changed',name)
checks=[];env=dict(os.environ,PYTHONPATH=str(P/'src'),TMPDIR=str(R/'audit-tmp'));Path(env['TMPDIR']).mkdir(exist_ok=True)
for label,args in [('compileall',['-m','compileall','-q','src','tests']),('imports',['-c','import graft;import graft.cli;import graft.database;import graft.config;import graft.project;import graft.md_gbsa;import graft.chemistry.scaffold;import graft.stages.glide'])]:
 result=subprocess.run([s['python']]+args,cwd=P,env=env,capture_output=True,text=True);assert not result.returncode,(label,result.stderr);checks.append(dict(check=label,exit_code=0))
for mod in ('graft','graft.driver','graft.stages.glide','graft.stages.prime_gbsa','graft.stages.md_gbsa'):
 out=[]
 for base in (B,P):
  result=subprocess.run([s['python'],'-m',mod,'--help'],cwd=P,env=dict(env,PYTHONPATH=str(base/'src')),capture_output=True,text=True);assert not result.returncode,(mod,result.stderr);out.append((result.stdout,result.stderr))
 assert out[0]==out[1],mod;checks.append(dict(check=mod+' CLI help stdout/stderr bytes',unchanged=True))
report=dict(ast_reconstruction=proof,changed_runtime=changed,added_runtime=added,existing_signatures_preserved=signatures,existing_tests_unchanged=True,protected_source_count=len(protected),protected_changes=protected_changes,protected_unchanged=len(protected)-len(protected_changes),checks=checks)
(R/'source-audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
