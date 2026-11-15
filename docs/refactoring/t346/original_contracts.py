import json,os,subprocess,time
from pathlib import Path
s=json.loads(Path('/tmp/graft-t346-active.json').read_text());R=Path(s['run']);P=Path(s['project']);tmp=R/'test-tmp/original-added-contracts';tmp.mkdir(parents=True,exist_ok=True)
env=dict(os.environ,PYTHONPATH=str(R)+':'+str(R/'before/src'),TMPDIR=str(tmp),GRAFT_REFACTOR_TEST_TMP=str(tmp))
probe=subprocess.run([s['python'],'-c','from graft import _core; print(_core.__file__)'],cwd=P,env=env,text=True,capture_output=True,check=True);assert str(R/'before/src') in probe.stdout
cmd=[s['python'],'-m','pytest','-p','graft_refactor_isolation','-q','-ra','--junitxml='+str(R/'original-added-contracts.xml'),'tests/test_database_boundary_contracts.py','tests/test_project_boundary_contracts.py','tests/test_orchestration_followup_contracts.py','-k','not test_config_import_is_independent_of_core_and_workflow_io']
start=time.monotonic()
with (R/'original-added-contracts.log').open('w') as log:r=subprocess.run(cmd,cwd=P,env=env,stdout=log,stderr=subprocess.STDOUT)
report=dict(command=cmd,exit_code=r.returncode,seconds=time.monotonic()-start,source_file=probe.stdout.strip(),architecture_test_excluded=True)
(R/'original-added-contracts.json').write_text(json.dumps(report,indent=2));assert not r.returncode;(print('\n'.join((R/'original-added-contracts.log').read_text().splitlines()[-5:])))
