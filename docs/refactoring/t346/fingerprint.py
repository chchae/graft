import hashlib,json,os,shutil,subprocess
from pathlib import Path
s=json.loads(Path('/tmp/graft-t346-active.json').read_text());R=Path(s['run']);P=Path(s['project']);B=R/'before';probe=R/'fingerprint-probe';probe.mkdir(exist_ok=True)
shutil.copytree(B/'src',probe/'src',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
cfgfile=probe/'synthetic.toml';cfgfile.write_text('project = "fingerprint-probe"\n');(probe/'output').mkdir(exist_ok=True)
script="""import json
from graft.stages.combichem_slurm import signature
from pathlib import Path
root=Path.cwd()
cfg={'config-path':str(root/'synthetic.toml'),'out-dir':str(root/'output')}
print(signature(cfg,{'debug':False,'trace':False}))
"""
def calculate():
 r=subprocess.run([s['python'],'-c',script],cwd=probe,env=dict(os.environ,PYTHONPATH=str(probe/'src')),text=True,capture_output=True,check=True);return r.stdout.strip()
old=calculate();changed={}
for name in ('config.py','database.py'):
 rel=Path('src/graft')/name;dst=probe/rel;changed[str(rel)]={'before':hashlib.sha256(dst.read_bytes()).hexdigest(),'after':hashlib.sha256((P/rel).read_bytes()).hexdigest()};dst.write_bytes((P/rel).read_bytes())
new=calculate()
for name in changed:(probe/name).write_bytes((B/name).read_bytes())
restored=calculate();assert old!=new and old==restored
r=dict(method='Real unchanged combichem_slurm.signature at identical absolute paths, synthetic config, no guard mock',probe_root=str(probe),before=old,after=new,restored=restored,source_guard_changes_signature=True,restoring_original_bytes_restores_signature=True,files=changed,production_data_touched=False,live_recovery_validated=False)
(R/'fingerprint-probe.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
