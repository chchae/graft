"""Shared capacity, native-fenced waves and recoverable atomic publication."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import time

import pytest

from graft import _core, database, glide_capacity, glide_recovery
from graft import glide_license_inputs as native
from graft.stages import glide_shared as shared
from test_glide_license_retry import project, structures, Pose, write_poses


def status(main, sp, main_used=0, sp_used=0):
    return (f'Users of GLIDE_MAIN: (Total of {main} licenses issued; Total of {main_used} licenses in use; Total of {main-main_used} licenses available)\n'
            f'Users of GLIDE_SP_DOCKING: (Total of {sp} licenses issued; Total of {sp_used} licenses in use; Total of {sp-sp_used} licenses available)\n')


def test_verified_native_license_units_and_invalid_inventory():
    assert glide_capacity.parse_license_status(status(125,425)) == dict(total=106,available=106)
    assert glide_capacity.parse_license_status(status(532,432,sp_used=4)) == dict(total=108,available=107)
    with pytest.raises(ValueError): glide_capacity.parse_license_status('not a license snapshot')
    with pytest.raises(ValueError): glide_capacity.parse_license_status(status(5,20).replace('Total of 20 licenses available','Total of 21 licenses available'))


def test_whole_compound_states_are_kept_together_and_plan_is_immutable(project):
    args, root, group = project
    result = native.ordinary_plan(dict(input=str(group/'glide_10.in'), ligands=str(group/'ligprep_group.maegz'),
                                      ids=[1,2,3], batch_size=2), structures())
    assert [r['ids'] for r in result['tasks'].values()] == [[1],[2],[3]]
    assert [r['indices'] for r in result['tasks'].values()] == [[1],[2,3],[4]]
    inp=group/'glide_10.in'
    inp.write_text(inp.read_text().replace('PRECISION SP','PRECISION XP'))
    with pytest.raises(ValueError,match='SP protocol'):
        native.ordinary_plan(dict(input=str(inp),ligands=str(group/'ligprep_group.maegz'),
                                  ids=[1,2,3],batch_size=2),structures())


@pytest.fixture
def shared_project(project, monkeypatch):
    args, root, group = project
    (group/'glide_job.json').unlink();(group/'best_poses.maegz').unlink()
    args.glide_cpus = 2;args.glide_shared_queue = True
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET state='glide_running',docking_score=NULL,failed_stage=NULL,failure_message=NULL WHERE iteration=1")
    plan = native.ordinary_plan(dict(input=str(group/'glide_10.in'), ligands=str(group/'ligprep_group.maegz'),
                                    ids=[1,2,3], batch_size=2), structures())
    database.freeze_glide_shared_plan(args.db_path,1,10,plan['source'],plan['tasks'],plan['missing'])
    launches = []
    @contextmanager
    def batch(suite):
        def launch(suite, command, directory):
            jid = f'native-{len(launches)}'
            launches.append((jid,command,directory))
            glide_recovery.save_record(directory,dict(job_id=jid,command=[str(x) for x in command],downloaded=False))
            return jid
        yield [],launch
    monkeypatch.setattr(shared,'submission_batch',batch)
    monkeypatch.setattr(shared,'license_capacity',lambda *a:dict(total=100,available=100))
    monkeypatch.setattr(shared,'group_status',lambda *a,**k:'RUNNING')
    monkeypatch.setattr(shared,'_runtime',lambda *a:None)
    return args,root,group,launches,plan


def test_pool_uses_global_cpu_budget_and_multiple_concurrent_claims(shared_project, capsys):
    args,root,group,launches,plan = shared_project
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    tasks=database.glide_license_retries(args.db_path,1)
    assert len(launches)==2  # serial NJOBS=1 uses one native calculation per slot
    assert sum(t['state']=='running' for t in tasks)==2
    assert sum(t['state']=='pending' for t in tasks)==1
    assert all(c[c.index('-HOST')+1]=='slurm-cpu:1' for _,c,_ in launches)
    assert all(c[c.index('-NJOBS')+1]=='1' for _,c,_ in launches)
    submission_lines = [line for line in capsys.readouterr().out.splitlines() if 'submitting task' in line]
    assert len(submission_lines) == 2
    for task in tasks:
        if task['state'] == 'running':
            # CMPID_2 has two prepared states but is one original compound.
            assert (f'Glide shared pool: submitting task {task["id"]}, reference 10, compounds=1.'
                    in submission_lines)
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    assert len(launches)==2
    with pytest.raises(RuntimeError,match='plan changed'):
        database.freeze_glide_shared_plan(args.db_path,1,10,'changed',plan['tasks'],[])


def test_pool_honors_licenses_and_retains_uncertain_launch(shared_project,monkeypatch):
    args,root,group,launches,_=shared_project
    monkeypatch.setattr(shared,'license_capacity',lambda *a:dict(total=1,available=1))
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    assert len(launches)==1
    task=next(t for t in database.glide_license_retries(args.db_path,1) if t['state']=='running')
    folder=shared.attempt_directory(root,task)
    record=glide_recovery.load_record(folder);record['job_id']=None;glide_recovery.save_record(folder,record)
    monkeypatch.setattr(shared,'license_capacity',lambda *a:dict(total=100,available=100))
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    assert len(launches)==1


def test_license_shortage_waits_without_claiming_and_refills_when_available(shared_project, monkeypatch, capsys):
    args,root,_,launches,_ = shared_project
    inventory = [dict(total=2,available=0), dict(total=2,available=2)]
    monkeypatch.setattr(shared, 'license_capacity', lambda *a: inventory.pop(0))
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    assert not launches
    assert database.glide_queue_counts(args.db_path,1,0) == dict(pending=3,running=0,done=0,ready=3)
    assert 'waiting for free licenses; recheck in 30s.' in capsys.readouterr().out
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    assert len(launches) == 2


def test_multiple_claimers_respect_one_global_limit(shared_project):
    args,_,_,_,_=shared_project
    with ThreadPoolExecutor(max_workers=3) as pool:
        claims=list(pool.map(lambda _:database.claim_glide_license_retry(args.db_path,1,0,max_running=2),range(3)))
    assert len({x for x in claims if x is not None})==2
    assert claims.count(None)==1


def test_batch_ordinals_share_capacity_across_references(shared_project):
    args,_,_,_,plan=shared_project
    with database.connect(args.db_path) as conn:
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(20,'REF20','C',0,'reference')")
        for cid in (4,5):
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) VALUES(?,?,?,1,'glide_running',20)",
                         (cid,f'CMPID_{cid}','CC'))
    first=next(iter(plan['tasks'].values()))
    tasks={f'glide_batch_{i:06d}':dict(first,ids=[cid]) for i,cid in enumerate((4,5),1)}
    database.freeze_glide_shared_plan(args.db_path,1,20,'second-reference',tasks,[])
    order=[]
    for _ in range(5):
        task_id=database.claim_glide_license_retry(args.db_path,1,0,max_running=5)
        task=database.glide_license_retries(args.db_path,1,task_id=task_id)[0]
        order.append(task['payload']['ids'][0])
    assert order==[1,4,2,5,3]


def test_free_slot_refills_without_waiting_for_other_active_jobs(shared_project):
    args,root,_,launches,_=shared_project
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    tasks=database.glide_license_retries(args.db_path,1,state='running')
    database.finish_glide_license_retry(args.db_path,tasks[0]['id'],license_error=True,now=time.time())
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    assert len(launches)==3
    counts=database.glide_queue_counts(args.db_path,1,time.time())
    assert counts==dict(pending=1,running=2,done=0,ready=0)
    failed=database.glide_license_retries(args.db_path,1,task_id=tasks[0]['id'])[0]
    assert failed['kind']=='license' and failed['attempts']==1


def test_zero_and_expired_capacity_hints_refresh_inventory(shared_project,monkeypatch):
    args,root,_,launches,_=shared_project
    hint=[0,time.monotonic()]
    shared.service_pass(args,1,root,'merger',lambda *a:None,capacity_hint=hint)
    assert len(launches)==2 and hint[0]==2
    called=[]
    monkeypatch.setattr(shared,'license_capacity',lambda *a:called.append(True) or dict(total=100,available=100))
    shared.service_pass(args,1,root,'merger',lambda *a:None,capacity_hint=hint)
    assert not called
    hint[1]-=31
    shared.service_pass(args,1,root,'merger',lambda *a:None,capacity_hint=hint)
    assert called==[True] and len(launches)==2


def test_group_pose_publication_and_root_barrier_survive_crash(shared_project,monkeypatch):
    args,root,group,launches,_=shared_project
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    tasks=database.glide_license_retries(args.db_path,1)
    for task in tasks:
        if task['state']!='running':continue
        folder=shared.attempt_directory(root,task);record=glide_recovery.load_record(folder)
        record['downloaded']=True;glide_recovery.save_record(folder,record)
        log,output=glide_recovery.output_paths(folder);log.write_text('native DONE')
        poses=[Pose(cid,-10-cid,index=i+1) for i,cid in enumerate(task['payload']['ids'])]
        # All states of CMPID_2 are consecutive; its first state is input 1.
        write_poses(output,poses)
    monkeypatch.setattr(shared,'group_status',lambda *a,**k:'DONE')
    def broken(*a):raise OSError('root publication interrupted')
    with pytest.raises(OSError):shared.reconcile(args,1,root,'merge',broken)
    assert database.glide_shared_publication(args.db_path,1)
    assert database.docking_completion_reason(args.db_path,1,3) is not None
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT count(*) FROM compound WHERE iteration=1 AND docking_score IS NOT NULL').fetchone()[0]==2
    called=[];shared.reconcile(args,1,root,'merge',lambda *a:called.append(True))
    assert called==[True] and not database.glide_shared_publication(args.db_path,1)


def test_runtime_feedback_only_changes_new_plans(shared_project):
    args,_,_,_,plan=shared_project
    task=database.glide_license_retries(args.db_path,1)[0];task['attempts']=1
    database.record_glide_batch_runtime(args.db_path,task,1,120)
    assert database.glide_observed_batch_size(args.db_path,2,24,300)==2
    assert database.glide_observed_batch_size(args.db_path,1,24,300)==24
    assert database.glide_shared_references(args.db_path,1)=={10}


def test_timed_out_serial_batch_imports_fresh_partial_poses(shared_project,monkeypatch):
    args,root,_,_,_=shared_project
    shared.service_pass(args,1,root,'merger',lambda *a:None)
    task=database.glide_license_retries(args.db_path,1,state='running')[0]
    folder=shared.attempt_directory(root,task)
    record=glide_recovery.load_record(folder)
    record.update(downloaded=True,timeout_requested_at=1)
    glide_recovery.save_record(folder,record)
    log,output=glide_recovery.output_paths(folder)
    log.write_text('Native job stopped at timeout')
    write_poses(output,[Pose(999,-99,index=1)])  # cached output cannot establish a result
    fresh=folder/'downloaded_lib.maegz'
    write_poses(fresh,[Pose(1,-11,index=1)])
    monkeypatch.setattr(shared,'group_status',lambda suite,directory,*a,**k:'STOPPED' if directory==folder else 'RUNNING')
    monkeypatch.setattr(shared,'timeout_pose_files',lambda *a:[fresh])
    shared.reconcile(args,1,root,'merger',lambda *a:None)
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT state,docking_score FROM compound WHERE id=1').fetchone()==('docked',-11)
    assert glide_recovery.load_record(folder)['timeout_results_imported']
    assert database.glide_queue_counts(args.db_path,1,0)['running']==1


def test_worker_discovers_queue_and_publication_without_unscored_compound_states(shared_project):
    args,_,_,_,_=shared_project
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET state='failed',failed_stage='glide' WHERE iteration=1")
    assert database.find_iteration_for_state('glide',args.db_path)==1
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE glide_license_retry SET state='done'")
    database.glide_shared_publication(args.db_path,1,True)
    assert database.find_iteration_for_state('glide',args.db_path)==1
    database.glide_shared_publication(args.db_path,1,False)
    assert database.find_iteration_for_state('glide',args.db_path) is None


def test_native_batch_fence_cannot_escape_context_or_duplicate_identity(tmp_path,monkeypatch):
    calls=[]
    @contextmanager
    def fence(*a):yield []
    monkeypatch.setattr(glide_recovery,'submission_guard',fence)
    monkeypatch.setattr(glide_recovery.subprocess,'run',lambda *a,**k:type('R',(),dict(stdout='JobId: saved\n',stderr=''))())
    with glide_recovery.submission_batch('/suite') as (live,launch):
        folder=tmp_path/'one';folder.mkdir()
        launch('/suite',['/suite/glide','input.in'],folder)
        assert len(live)==1
        with pytest.raises(RuntimeError,match='live external'):
            launch('/suite',['/suite/glide','input.in'],folder)
    with pytest.raises(RuntimeError,match='Expired'):
        launch('/suite',['/suite/glide','input.in'],folder)
