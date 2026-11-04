"""Slurm lifecycle and pure-pool tests; native scheduling is tested separately."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from graft.config import combichem_3d_settings
from graft.stages import combichem_slurm as slurm
from graft.stages import combichem_slurm_control as control


@pytest.fixture
def project(tmp_path):
    script=Path(__file__).resolve().parents[1]/'examples/combichem-3d/make_toy_inputs.py'
    spec=importlib.util.spec_from_file_location('slurm_toy',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    filename=module.create(tmp_path/'toy',0,2)
    extra='''execution-backend = "slurm"
remote-env-script = "/env/amber.sh"
worker-hosts = { cpu1 = 1, cpu2 = 1 }
slurm-time-limit = 300
slurm-memory-mb = 2048
'''
    filename.write_text(filename.read_text().replace('[filters]',extra+'[filters]'))
    return filename,combichem_3d_settings(filename)


def flags():
    return SimpleNamespace(export_only=False,debug=False,trace=False)


def test_slurm_configuration_and_generated_output_do_not_change_submission_identity(project):
    filename,cfg=project
    assert cfg['processes']==2 and cfg['slurm-partition']=='cpu'
    before=slurm.signature(cfg,[])
    generated=Path(cfg['reference-smiles'])
    cfg['reference-smiles']=str(Path(cfg['out-dir'])/'input-cache/references.smi')
    before=slurm.signature(cfg,[])
    target=Path(cfg['reference-smiles']);target.parent.mkdir(parents=True);target.write_text('CC\n')
    assert slurm.signature(cfg,[])==before
    filename.write_text(filename.read_text()+'\n# Changed queued input\n')
    assert slurm.signature(cfg,[])!=before


@pytest.mark.parametrize('line',[
    'slurm-time-limit = 0','slurm-memory-mb = -1','slurm-poll-seconds = 0',
    'slurm-poll-seconds = true','slurm-host = "-bad"','slurm-partition = "cpu;exit"',
    'worker-hosts = { cpu1 = 1, cpu2 = 2 }','execution-host = "cpu2"'])
def test_invalid_slurm_resources_are_rejected(project,line):
    filename,_=project
    key=line.split('=')[0].strip()
    text='\n'.join(row for row in filename.read_text().splitlines() if not row.startswith(key+' ='))
    filename.write_text(text.replace('[filters]',line+'\n[filters]'))
    with pytest.raises(ValueError):combichem_3d_settings(filename)


def test_submission_is_native_and_idempotent(project,monkeypatch,tmp_path):
    _,cfg=project
    root=tmp_path/'native'
    slurm.create_request(cfg,[],root,'digest')
    submit=Mock(return_value='123;cluster\n')
    monkeypatch.setattr(control.subprocess,'check_output',submit)
    assert control.submit(root)['job_id']=='123'
    assert control.submit(root)['job_id']=='123'
    assert submit.call_count==1
    command=submit.call_args.args[0]
    assert '--hint=nomultithread' in command and '--ntasks-per-node=1' in command
    assert command[command.index('--nodelist')+1]=='cpu1,cpu2'
    assert command[command.index('--cpus-per-task')+1]=='1'
    assert '--no-requeue' in command


def test_lost_job_id_is_recovered_without_duplicate_submission(project,monkeypatch,tmp_path):
    _,cfg=project;root=tmp_path/'native'
    slurm.create_request(cfg,[],root,'digest')
    submit=Mock(return_value='response lost\n');monkeypatch.setattr(control.subprocess,'check_output',submit)
    with pytest.raises(RuntimeError,match='unambiguous'):control.submit(root)
    monkeypatch.setattr(control,'recover',lambda record:'124')
    assert control.submit(root)['job_id']=='124'
    assert submit.call_count==1


def test_uncertain_scheduler_history_never_resubmits(project,monkeypatch,tmp_path):
    _,cfg=project;root=tmp_path/'native'
    slurm.create_request(cfg,[],root,'digest')
    control.save(root/'submission.json',dict(launching=True,job_id=None,job_name='combichem-test',started_at=0))
    monkeypatch.setattr(control,'recover',Mock(side_effect=RuntimeError('accounting unavailable')))
    submit=Mock();monkeypatch.setattr(control.subprocess,'check_output',submit)
    with pytest.raises(RuntimeError,match='unavailable'):control.submit(root)
    submit.assert_not_called()
    assert json.loads((root/'submission.json').read_text())['launching']


def test_native_completion_alone_does_not_establish_workflow_completion(project,monkeypatch,tmp_path):
    _,cfg=project
    root=tmp_path/'native';request=slurm.create_request(cfg,[],root,'digest')
    monkeypatch.setattr(slurm,'_select_allocation',lambda *args:(root,request,'123'))
    monkeypatch.setattr(slurm,'_await_ready',lambda *args:None)
    finish=Mock();monkeypatch.setattr(slurm,'_finish_allocation',finish)
    with pytest.raises(RuntimeError,match='workflow result'):
        with slurm.allocation(cfg,flags()):pass
    finish.assert_called_once()
    assert not (root/'completed.json').exists()


def test_changed_input_never_replaces_an_active_allocation(project,monkeypatch):
    _,cfg=project
    directory=Path(cfg['out-dir'])/'slurm';directory.mkdir(parents=True)
    root=directory/'run-test';slurm.create_request(cfg,[],root,'different input')
    slurm.atomic_save(directory/'current.json',dict(session=root.name))
    monkeypatch.setattr(slurm,'control',lambda cfg,action,value:dict(job_id='123') if action=='submit'
                        else dict(active=True,state='RUNNING'))
    with pytest.raises(RuntimeError,match='different inputs'):slurm._select_allocation(cfg,[],slurm.signature(cfg,[]),directory)
    assert json.loads((directory/'current.json').read_text())['session']=='run-test'


def test_slurm_export_does_not_submit(project,monkeypatch):
    _,cfg=project;ns=flags();ns.export_only=True
    submit=Mock();monkeypatch.setattr(slurm,'control',submit)
    assert not slurm.dispatch(cfg,ns)
    with slurm.allocation(cfg,ns) as session:assert session is None
    submit.assert_not_called()


def test_node_pool_transport_preserves_pose_and_never_opens_sqlite(project,monkeypatch,tmp_path):
    from graft import database
    from graft.stages.combichem_slurm_pool import main
    from graft.chemistry.combichem3d import history
    from graft.chemistry.combichem3d.assembly import assemble
    from graft.chemistry.combichem3d.clashes import BumpChecker,load_receptor
    from graft.chemistry.combichem3d.output import versions
    from graft.chemistry.combichem3d.search import search
    _,cfg=project
    cfg.update({'runtime-versions':versions(),'conformers':1,'torsion-trials':0,'slurm-runtime-dir':str(tmp_path),'slurm-job-id':'42'})
    parent,h=history.load_parents(cfg['parents'],cfg['history'])[0]
    product,birth=assemble(parent,h,h['attachments'],['*C','*CC'],'native-test')
    local=search(product,birth,BumpChecker(load_receptor(cfg['receptor'],cfg),cfg),cfg,37)
    monkeypatch.setenv('SLURM_JOB_ID','42');monkeypatch.setenv('SLURMD_NODENAME',socket.gethostname())
    pools=tmp_path/'pools';root=pools/socket.gethostname();root.mkdir(parents=True);(root/'token').write_text('private-test-token')
    monkeypatch.setattr(database,'connect',Mock(side_effect=AssertionError('Pure pool must not access SQLite')))
    process=multiprocessing.get_context('fork').Process(target=main,args=(pools,1))
    process.start();remote=slurm.SlurmSearch(socket.gethostname(),cfg)
    try:
        until=time.monotonic()+10
        while not (root/'endpoint.json').exists():
            assert process.is_alive() and time.monotonic()<until
            time.sleep(.05)
        endpoint=json.loads((root/'endpoint.json').read_text())
        with socket.create_connection((endpoint['host'],endpoint['port'])) as rejected:
            rejected.sendall(b'wrong-token\n')
            assert rejected.recv(1)==b''
        result=remote(product,birth,None,cfg,37)
        assert local['status']==result['status']=='PASS'
        np.testing.assert_array_equal(local['pose'].GetConformer().GetPositions(),result['pose'].GetConformer().GetPositions())
        assert history.snapshot(local['pose'],birth)==history.snapshot(result['pose'],birth)
    finally:
        remote.close();(root/'stop').touch();process.join(15)
        if process.is_alive():process.terminate();process.join()
    assert process.exitcode==0


def test_slurm_coordinator_relaunch_is_local_not_ssh(project,monkeypatch):
    _,cfg=project
    monkeypatch.delenv('GRAFT_COMBICHEM_COORDINATOR',raising=False)
    run=Mock();monkeypatch.setattr(slurm.subprocess,'run',run)
    assert slurm.dispatch(cfg,flags())
    command=run.call_args.args[0]
    assert command[:2]==['bash','-c'] and 'GRAFT_COMBICHEM_COORDINATOR=1' in command[2]
    assert 'ssh ' not in command[2]
    monkeypatch.setenv('GRAFT_COMBICHEM_COORDINATOR','1')
    assert not slurm.dispatch(cfg,flags())


def test_database_is_initialized_before_scheduler_metadata(project,monkeypatch):
    from contextlib import contextmanager
    from graft.stages import combichem_3d as stage
    filename,cfg=project
    monkeypatch.setenv('GRAFT_COMBICHEM_COORDINATOR','1')
    @contextmanager
    def allocated(config,ns):
        assert (Path(config['out-dir'])/'combichem3d.sqlite').is_file()
        raise RuntimeError('allocation reached after valid project initialization')
        yield
    monkeypatch.setattr(slurm,'allocation',allocated)
    with pytest.raises(RuntimeError,match='allocation reached'):
        stage._main([str(filename)])


@pytest.fixture
def automatic_project(project):
    filename,_=project
    removed=('processes','worker-hosts','worker-ssh-known-hosts')
    filename.write_text('\n'.join(line for line in filename.read_text().splitlines()
        if not any(line.startswith(key+' =') for key in removed))+'\n')
    return filename,combichem_3d_settings(filename)


def test_slurm_needs_no_processes_or_named_hosts(automatic_project):
    filename,cfg=automatic_project
    assert 'processes =' not in filename.read_text()
    assert 'worker-hosts' not in cfg and 'worker-ssh-known-hosts' not in cfg
    assert cfg['slurm-nodes']==2 and cfg['slurm-cpus-per-node']==192
    assert cfg['processes']==384 and 1<=cfg['coordinator-processes']<=16


@pytest.mark.parametrize('line',[
    'slurm-nodes = 0','slurm-nodes = true','slurm-nodes = 1.5',
    'slurm-cpus-per-node = 0','slurm-cpus-per-node = true','slurm-cpus-per-node = 193'])
def test_invalid_automatic_resource_requests_are_rejected(automatic_project,line):
    filename,_=automatic_project
    filename.write_text(filename.read_text().replace('[filters]',line+'\n[filters]'))
    with pytest.raises(ValueError):combichem_3d_settings(filename)


@pytest.mark.parametrize('line',['slurm-nodes = 3','slurm-cpus-per-node = 2'])
def test_conflicting_explicit_hosts_and_resources_are_rejected(project,line):
    filename,_=project
    filename.write_text(filename.read_text().replace('[filters]',line+'\n[filters]'))
    with pytest.raises(ValueError,match='conflicts'):combichem_3d_settings(filename)


@pytest.mark.parametrize('named',[False,True])
def test_three_node_slurm_request_supports_576_slots(automatic_project,named):
    filename,_=automatic_project
    line=('worker-hosts = { cpu1 = 192, cpu2 = 192, cpu3 = 192 }' if named else 'slurm-nodes = 3')
    filename.write_text(filename.read_text().replace('[filters]',line+'\n[filters]'))
    cfg=combichem_3d_settings(filename)
    assert cfg['slurm-nodes']==3 and cfg['processes']==576
    assert cfg['slurm-cpus-per-node']==192


def test_automatic_submission_lets_slurm_select_nodes(automatic_project,monkeypatch,tmp_path):
    _,cfg=automatic_project
    root=tmp_path/'auto'
    request=slurm.create_request(cfg,[],root,'digest')
    assert request['hosts']=={} and request['nodes']==2 and request['cpus_per_node']==192
    submit=Mock(return_value='555\n');monkeypatch.setattr(control.subprocess,'check_output',submit)
    assert control.submit(root)['job_id']=='555'
    assert control.submit(root)['job_id']=='555' and submit.call_count==1
    command=submit.call_args.args[0]
    assert '--nodelist' not in command
    assert command[command.index('--nodes')+1]=='2'
    assert command[command.index('--cpus-per-task')+1]=='192'


def test_native_allocation_discovers_and_validates_cpu3(automatic_project,monkeypatch,tmp_path):
    from graft.chemistry.combichem3d.output import versions
    from graft.stages.combichem_slurm_worker import assigned_hosts
    _,cfg=automatic_project
    root=tmp_path/'auto';slurm.create_request(cfg,[],root,'digest')
    monkeypatch.setenv('SLURM_JOB_ID','555');monkeypatch.setenv('SLURM_JOB_NODELIST','cpu[2-3]')
    monkeypatch.setenv('SLURM_CPUS_PER_TASK','192')
    monkeypatch.setattr(slurm.subprocess,'check_output',Mock(return_value='cpu2\ncpu3\n'))
    native=Mock();monkeypatch.setattr(slurm.subprocess,'run',native)
    slurm.launch(root)
    record=json.loads((root/'allocation.json').read_text())
    assert record=={'job_id':'555','hosts':{'cpu2':192,'cpu3':192}}
    for host in record['hosts']:
        directory=root/'pools'/host
        token=directory/'token';assert len(token.read_text())==64
        slurm.atomic_save(directory/'endpoint.json',dict(host=host,job_id='555',slots=192,versions=versions()))
    command=native.call_args.args[0]
    assert command[:2]==['srun','--exact']
    monkeypatch.setattr(slurm,'control',lambda *args:dict(state='RUNNING',active=True))
    assert slurm._await_ready(cfg,root,'555')==record['hosts']
    assert 'worker-hosts' not in cfg  # requests remain separate from runtime identity
    before=slurm.signature(cfg,[])
    cfg.update({'slurm-allocated-hosts':record['hosts'],'slurm-worker-index':0})
    assert slurm.signature(cfg,[])==before
    assigned=[host for i in range(cfg['coordinator-processes']) for host in assigned_hosts(cfg,i)]
    assert assigned.count('cpu2')==192 and assigned.count('cpu3')==192
    # Re-launch verification must preserve existing authentication tokens.
    token_before=(root/'pools/cpu3/token').read_text()
    slurm.launch(root)
    assert (root/'pools/cpu3/token').read_text()==token_before


@pytest.mark.parametrize('hosts,job_id',[
    ({'cpu2':192,'cpu3':192},'556'),({'cpu2':192},'555'),
    ({'cpu2':192,'cpu3':191},'555'),({'../cpu2':192,'cpu3':192},'555')])
def test_wrong_saved_auto_allocation_is_never_used(automatic_project,monkeypatch,tmp_path,hosts,job_id):
    _,cfg=automatic_project
    root=tmp_path/'auto';slurm.create_request(cfg,[],root,'digest')
    slurm.atomic_save(root/'allocation.json',dict(job_id=job_id,hosts=hosts))
    monkeypatch.setattr(slurm,'control',lambda *args:dict(state='RUNNING',active=True))
    with pytest.raises(RuntimeError,match='identity mismatch'):slurm._await_ready(cfg,root,'555')


def test_automatic_dispatch_installs_remote_workers_without_named_hosts(automatic_project,monkeypatch):
    from graft.stages import combichem_remote,combichem_distributed,combichem_3d
    _,cfg=automatic_project
    install=Mock();monkeypatch.setattr(combichem_distributed,'install',install)
    monkeypatch.setattr(slurm,'dispatch',lambda *args:False)
    assert not combichem_remote.dispatch(cfg,flags())
    install.assert_called_once_with(combichem_3d)
    assert 'combichem_slurm_worker.py' in cfg['remote-implementation-sha256']


def test_legacy_named_request_without_new_resource_fields_is_supported(project,monkeypatch,tmp_path):
    _,cfg=project
    root=tmp_path/'legacy';request=slurm.create_request(cfg,[],root,'digest')
    request.pop('nodes');request.pop('cpus_per_node');slurm.atomic_save(root/'request.json',request)
    submit=Mock(return_value='123\n');monkeypatch.setattr(control.subprocess,'check_output',submit)
    assert control.submit(root)['job_id']=='123'
    assert '--nodelist' in submit.call_args.args[0]


def test_automatic_allocation_reattaches_without_new_submission(automatic_project,monkeypatch):
    _,cfg=automatic_project
    directory=Path(cfg['out-dir'])/'slurm';directory.mkdir(parents=True)
    digest=slurm.signature(cfg,[])
    root=directory/'run-saved';slurm.create_request(cfg,[],root,digest)
    slurm.atomic_save(directory/'current.json',dict(session=root.name))
    calls=[]
    def scheduler(config,action,value):
        calls.append(action)
        return dict(job_id='123') if action=='submit' else dict(state='RUNNING',active=True)
    monkeypatch.setattr(slurm,'control',scheduler)
    create=Mock(side_effect=AssertionError('Must not replace active allocation'))
    monkeypatch.setattr(slurm,'create_request',create)
    selected,_,job=slurm._select_allocation(cfg,[],digest,directory)
    assert selected==root and job=='123' and calls==['submit','status']


def test_cleanup_stops_only_discovered_allocation_nodes(automatic_project,monkeypatch,tmp_path):
    _,cfg=automatic_project
    cfg['slurm-allocated-hosts']={'cpu2':192,'cpu3':192}
    for host in ('cpu1','cpu2','cpu3'):(tmp_path/'pools'/host).mkdir(parents=True)
    monkeypatch.setattr(slurm,'control',lambda *args:dict(active=False,successful=True,state='COMPLETED'))
    slurm._finish_allocation(cfg,tmp_path,'555')
    assert not (tmp_path/'pools/cpu1/stop').exists()
    assert (tmp_path/'pools/cpu2/stop').exists() and (tmp_path/'pools/cpu3/stop').exists()
