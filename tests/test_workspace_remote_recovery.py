"""Archive verified retired remote work; cached files never override live work."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_iteration_workspace import workspace, deleted
from graft import database, reinvent_remote
from graft.reinvent_remote_status import active_processes
from graft.stages import generate
from graft.stages._workspace import (prepare_iteration_directory,
    WorkspaceReconciliationPending, reconcile_amber_job)


@pytest.fixture
def completed_remote(deleted, tmp_path, monkeypatch):
    root=deleted.output/'iter1'
    folder=root/'reinvent';folder.mkdir()
    remote=tmp_path/'remote run';remote.mkdir()
    output=remote/'generated.csv';output.write_text('SMILES\nCC\n')
    text='run_type = "sampling"\n[parameters]\noutput_file = '+json.dumps(str(output))+'\n'
    (remote/'sampling.toml').write_text(text)
    (folder/'sampling.remote.toml').write_text(text)
    (folder/'sampling.remote-dir.txt').write_text('tensor:'+str(remote)+'\n')
    (remote/'sampling.log').write_text('Finished REINVENT on 2026-11-04\n')
    (folder/'generated.csv').write_bytes(output.read_bytes())
    actual_run=subprocess.run
    def transport(settings,command,**kwargs):
        assert settings['host']=='tensor'
        return actual_run(['bash','-c',command],check=True,**kwargs)
    monkeypatch.setattr(reinvent_remote,'_ssh',transport)
    return deleted,folder,remote


def test_retired_remote_marker_no_longer_blocks_deleted_population_reset(completed_remote):
    args,folder,remote=completed_remote
    archive=prepare_iteration_directory(args,1)
    assert archive is not None
    assert (archive/'reinvent/sampling.remote-dir.txt').is_file()
    evidence=json.loads((archive/'workspace_remote_reconciliation.json').read_text())
    assert evidence['reinvent/sampling.remote-dir.txt']['terminal'] is True
    assert next(archive.glob('database-before-reset-*.sqlite')).is_file()
    assert (remote/'generated.csv').read_text()=='SMILES\nCC\n'
    assert not database.iteration_was_deleted(args.db_path,1)
    assert database.iteration_compound_count(args.db_path,0)==1
    assert list((args.output/'iter1').iterdir())==[]


def test_cached_terminal_log_cannot_override_live_remote_process(completed_remote):
    args,folder,remote=completed_remote
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],cwd=remote)
    try:
        with pytest.raises(WorkspaceReconciliationPending,match='processes remain active'):
            prepare_iteration_directory(args,1)
        assert process.poll() is None
        assert database.iteration_was_deleted(args.db_path,1)
        assert (folder/'sampling.remote-dir.txt').exists()
        assert not (args.output/'.previous').exists()
    finally:
        process.terminate();process.wait(timeout=5)


@pytest.mark.parametrize('change',['config','local_output','no_terminal_log','transport'])
def test_uncertain_remote_identity_or_completion_retains_everything(completed_remote,monkeypatch,change):
    args,folder,remote=completed_remote
    if change=='config':(remote/'sampling.toml').write_text('changed')
    elif change=='local_output':(folder/'generated.csv').write_text('stale cached data')
    elif change=='no_terminal_log':(remote/'sampling.log').write_text('still calculating')
    else:
        def offline(*a,**k):raise OSError('SSH unavailable')
        monkeypatch.setattr(reinvent_remote,'_ssh',offline)
    with pytest.raises(WorkspaceReconciliationPending):prepare_iteration_directory(args,1)
    assert database.iteration_was_deleted(args.db_path,1)
    assert (folder/'sampling.remote-dir.txt').exists()
    assert not (args.output/'.previous').exists()


def test_native_failed_exit_receipt_allows_retirement_without_using_partial_outputs(completed_remote):
    args,folder,remote=completed_remote
    (remote/'sampling.log').unlink();(remote/'generated.csv').unlink()
    receipt=dict(remote='tensor:'+str(remote),exit_code=1,
        config_sha256=hashlib.sha256((folder/'sampling.remote.toml').read_bytes()).hexdigest())
    (folder/'sampling.remote-status.json').write_text(json.dumps(receipt))
    archive=prepare_iteration_directory(args,1)
    proof=json.loads((archive/'workspace_remote_reconciliation.json').read_text())
    assert proof['reinvent/sampling.remote-dir.txt']['exit_code']==1
    assert database.iteration_compound_count(args.db_path,1)==0


@pytest.mark.parametrize('code',[255,True,-1])
def test_ambiguous_exit_receipts_do_not_establish_termination(completed_remote,code):
    args,folder,remote=completed_remote
    receipt=dict(remote='tensor:'+str(remote),exit_code=code,
        config_sha256=hashlib.sha256((folder/'sampling.remote.toml').read_bytes()).hexdigest())
    (folder/'sampling.remote-status.json').write_text(json.dumps(receipt))
    with pytest.raises(WorkspaceReconciliationPending):prepare_iteration_directory(args,1)
    assert database.iteration_was_deleted(args.db_path,1)


def test_generate_waits_without_traceback_or_sampling_when_reconciliation_is_pending(completed_remote,monkeypatch,capsys):
    args,folder,remote=completed_remote
    args.target_count=2;args.max_iteration=2
    (remote/'sampling.log').write_text('no verified terminal status')
    monkeypatch.setattr(generate.c,'run_libinvent_sampling',lambda *a,**k:pytest.fail('sampled before safe cleanup'))
    generate._run_stage(args,1)
    output=capsys.readouterr().out
    assert 'generation waiting for workspace reconciliation' in output
    assert 'Traceback' not in output
    assert database.iteration_was_deleted(args.db_path,1)


@pytest.mark.parametrize('native_active,locks_active,task_count,success',[
    (True,False,2,False),(False,True,2,False),(False,False,1,False),(False,False,2,True)])
def test_amber_retirement_requires_native_terminal_and_all_locks_released(tmp_path,monkeypatch,native_active,locks_active,task_count,success):
    from graft import amber_gbsa,amber_slurm
    path=tmp_path/'amber_job.json'
    path.write_text(json.dumps(dict(mode=amber_slurm.ARRAY,slurm_job_id='123',
        settings={},remote_dir='/remote/run',compound_ids=[1,2])))
    monkeypatch.setattr(amber_slurm,'job_state',lambda *a:dict(active=native_active))
    def ssh(*a,**k):
        assert not native_active
        return SimpleNamespace(stdout=json.dumps(dict(active=locks_active,tasks=[{}]*task_count,results=[])))
    monkeypatch.setattr(amber_gbsa,'ssh',ssh)
    if success:assert reconcile_amber_job(path)['snapshot']['active'] is False
    else:
        with pytest.raises(RuntimeError):reconcile_amber_job(path)
    assert path.is_file()


@pytest.mark.parametrize('comm,argv,permitted',[
    ('(sd-pam)','(sd-pam)',True),('ssh-agent','/usr/bin/ssh-agent',True),
    ('gnome-keyring-d','/usr/bin/gnome-keyring-daemon',True),
    ('sshd-session','sshd-session: chchae@notty',True),
    ('python','/usr/bin/python',False),('ssh-agent','/tmp/calculator',False)])
def test_inaccessible_scientific_process_stays_uncertain(tmp_path,monkeypatch,comm,argv,permitted):
    proc=tmp_path/'999999999';proc.mkdir()
    (proc/'cmdline').write_bytes(argv.encode()+b'\0')
    (proc/'comm').write_text(comm+'\n')
    actual_iter=Path.iterdir
    monkeypatch.setattr(Path,'iterdir',lambda path:iter([proc]) if path==Path('/proc') else actual_iter(path))
    import os
    actual_readlink=os.readlink
    def readlink(path,*a,**k):
        if Path(path)==proc/'cwd':raise PermissionError('protected process')
        return actual_readlink(path,*a,**k)
    monkeypatch.setattr(os,'readlink',readlink)
    if permitted:assert active_processes(tmp_path/'remote')==[]
    else:
        with pytest.raises(PermissionError):active_processes(tmp_path/'remote')
