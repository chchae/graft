"""Process-scoped SLM order; fake calculations do not validate checkout capacity."""
from contextlib import nullcontext
import json
import os
import shlex
import subprocess
import sys
import threading

import pytest

from graft import _core, driver, glide_recovery, ligprep_recovery, md_gbsa, schrodinger_retry
from graft.config import SCHRODINGER_LICENSE_SERVERS, schrodinger_environment
from graft.stages import _logging


@pytest.fixture
def parent_env(monkeypatch):
    monkeypatch.setenv('SCHRODINGER_LICENSE_SERVERS','atlas:53000;virusgpu1:53000')
    monkeypatch.setenv('GRAFT_TEST_ENV_SENTINEL','retained')
    return {k:v for k,v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}


def probe_script(output):
    return ('import json,os; from pathlib import Path; '
            f'Path({str(output)!r}).write_text(json.dumps(dict('
            'priority=os.environ.get("SCHRODINGER_LICENSE_SERVERS"),'
            'sentinel=os.environ.get("GRAFT_TEST_ENV_SENTINEL")))); '
            'print("JobId: fake-native",flush=True)')


def check_probe(path,parent_env):
    assert json.loads(path.read_text())==dict(priority=SCHRODINGER_LICENSE_SERVERS,sentinel='retained')
    assert {k:v for k,v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}==parent_env


def test_environment_is_copied_without_import_or_parent_mutation(parent_env):
    child=schrodinger_environment()
    assert child['SCHRODINGER_LICENSE_SERVERS']==SCHRODINGER_LICENSE_SERVERS
    child['GRAFT_TEST_ENV_SENTINEL']='changed child'
    assert {k:v for k,v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}==parent_env


@pytest.mark.parametrize('verbose',[False,True])
def test_common_scientific_runner_uses_priority_in_both_logging_modes(
        tmp_path,monkeypatch,parent_env,verbose):
    monkeypatch.setattr(_logging,'enabled',lambda level:verbose)
    path=tmp_path/'env.json'
    _core.run_command([sys.executable,'-c',probe_script(path)],cwd=tmp_path)
    check_probe(path,parent_env)


def test_supervised_worker_gets_priority_without_changing_driver_environment(tmp_path,parent_env):
    path=tmp_path/'env.json'
    assert driver.stream_process('glide',[sys.executable,'-c',probe_script(path)],
                                 tmp_path,threading.Event())==0
    check_probe(path,parent_env)


def test_glide_submission_keeps_exact_command_and_applies_child_environment(tmp_path,monkeypatch,parent_env):
    monkeypatch.setattr(glide_recovery,'submission_guard',lambda *a:nullcontext([]))
    path=tmp_path/'env.json'
    command=[sys.executable,'-c',probe_script(path)]
    assert glide_recovery.submit('/unused',command,tmp_path)=='fake-native'
    saved=glide_recovery.load_record(tmp_path)
    assert saved['command']==command and saved['job_id']=='fake-native'
    check_probe(path,parent_env)
    before=glide_recovery.record_file(tmp_path).read_bytes()
    with pytest.raises(RuntimeError,match='still running'):
        monkeypatch.setattr(glide_recovery,'job_details',lambda *a:[dict(jobId='fake-native',status='RUNNING')])
        glide_recovery.submit('/unused',command,tmp_path)
    assert glide_recovery.record_file(tmp_path).read_bytes()==before


def test_ligprep_submission_keeps_journal_and_recovery_does_not_relaunch(tmp_path,monkeypatch,parent_env):
    path=tmp_path/'env.json'
    output=tmp_path/'ligprep.maegz'
    script=probe_script(path)+f'; Path({str(output)!r}).write_text("fake prepared structures")'
    command=[sys.executable,'-c',script]
    assert ligprep_recovery.run_or_recover(command,tmp_path,[1],output)
    saved=ligprep_recovery.load_job(tmp_path)
    assert saved['command']==command and saved['job_id']=='fake-native'
    check_probe(path,parent_env)
    before=ligprep_recovery.job_file(tmp_path).read_bytes()
    monkeypatch.setattr(subprocess,'run',lambda cmd,**kw:subprocess.CompletedProcess(
        cmd,0,json.dumps(dict(jobId='fake-native',status='RUNNING'))))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('Saved job was relaunched'))
    assert not ligprep_recovery.run_or_recover(command,tmp_path,[1],output)
    assert ligprep_recovery.job_file(tmp_path).read_bytes()==before


def test_prime_launch_uses_priority_and_preserves_jobid_checkpoint(tmp_path,parent_env):
    path=tmp_path/'env.json'
    seen=[]
    code,text=schrodinger_retry._run_attempt([sys.executable,'-c',probe_script(path)],tmp_path,seen.append)
    assert code==0 and 'JobId: fake-native' in text and seen==['JobId: fake-native\n']
    check_probe(path,parent_env)


def test_ssh_exports_priority_for_frozen_helpers_without_mutating_parent(tmp_path,monkeypatch,parent_env):
    path=tmp_path/'env with spaces.json'
    real_run=subprocess.run
    def local_ssh(cmd,**kwargs):
        assert cmd[:2]==['ssh','-o']
        # Execute the actual quoted remote shell payload in a disposable child.
        return real_run(['bash','-c',cmd[-1]],**kwargs)
    monkeypatch.setattr(subprocess,'run',local_ssh)
    result=md_gbsa.ssh('unused-host',shlex.join([sys.executable,'-c',probe_script(path)]))
    assert 'JobId: fake-native' in result
    check_probe(path,parent_env)


def test_ssh_stdin_is_preserved_for_streamed_readonly_helpers(monkeypatch,parent_env):
    real_run=subprocess.run
    monkeypatch.setattr(subprocess,'run',lambda cmd,**kwargs:real_run(['bash','-c',cmd[-1]],**kwargs))
    result=md_gbsa.ssh('unused-host',shlex.join([sys.executable,'-']),input=(
        'import os; print(os.environ["SCHRODINGER_LICENSE_SERVERS"])\n'))
    assert result.strip()==SCHRODINGER_LICENSE_SERVERS
    assert {k:v for k,v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}==parent_env
