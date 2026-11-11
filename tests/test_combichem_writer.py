"""Single-writer batch persistence, failure and restart recovery regressions."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from graft import database
from graft.stages import combichem_buffer as buffering, combichem_writer as writer
from graft.stages import combichem_3d as stage
from test_combichem_buffer import project, report


def state():
    ctx = multiprocessing.get_context('fork')
    return {name:ctx.Event() for name in ('stop','wake','failed')}


def start(db, control):
    process = multiprocessing.get_context('fork').Process(target=writer.run, args=(db,control,os.getpid()))
    process.start()
    return process


def stop(process, control):
    control['stop'].set()
    control['wake'].set()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('Writer did not finish draining results')


def publish(buffer):
    path = buffer.path.with_suffix('.ready')
    path.parent.mkdir(exist_ok=True)
    data = buffer.path.read_bytes() if buffer.path.exists() else b''
    writer._publish(path, dict(schema='graft_completed_batch_v1', batch=buffer.batch,
        count=len(buffer.records), next_cursor=int(buffer.batch['cursor'])+len(buffer.records),
        sha256=hashlib.sha256(data).hexdigest()))
    return path


def test_worker_keeps_whole_batch_and_only_writer_commits(project, monkeypatch):
    db, job, cfg = project
    calls = Path(db).parent/'writer-calls.jsonl'
    original = database.growth_save_attempts
    def observed(*args, **kwargs):
        with calls.open('a') as stream:
            stream.write(json.dumps(dict(pid=os.getpid(), count=len(args[2]), finish=kwargs.get('finish')))+'\n')
        return original(*args, **kwargs)
    monkeypatch.setattr(database,'growth_save_attempts',observed)
    control = state()
    process = start(db,control)
    try:
        monkeypatch.setattr(database,'growth_save_attempts',lambda *a, **k:pytest.fail('Calculator wrote results'))
        with buffering.ResultBuffer(db,job,cfg | {'result-buffer-size':1,'result-buffer-seconds':0},writer=control) as buffer:
            for ordinal in range(16):
                buffer.append(ordinal,report(),None)
            assert len(buffer.records)==16
            assert database.growth_run_info(db)['attempted']==0
        assert database.growth_run_info(db)['attempted']==16
        assert database.growth_rows(db,'combichem3d_batch')[0]['state']=='done'
        assert [json.loads(line) for line in calls.read_text().splitlines()]==[
            dict(pid=process.pid,count=16,finish=True)]
        assert not list(buffer.path.parent.glob('batch-*.*'))
    finally:
        stop(process,control)
    assert process.exitcode==0


def test_multiple_calculators_commit_through_same_writer_without_duplicate_results(project,monkeypatch):
    db,first,cfg=project
    database.growth_save_plan(db,first['parent_pose'],dict(sites=['a'],pools=[['-H']],total=32),[])
    second=database.growth_claim(db,1,16,2)
    calls=Path(db).parent/'writer-calls.jsonl'
    original=database.growth_save_attempts
    def observed(*args,**kwargs):
        with calls.open('a') as stream:stream.write(json.dumps(dict(pid=os.getpid(),count=len(args[2])))+'\n')
        return original(*args,**kwargs)
    monkeypatch.setattr(database,'growth_save_attempts',observed)
    control=state();process=start(db,control)
    def calculate(job):
        def forbidden(*a,**k):raise AssertionError('Calculator wrote product results')
        database.growth_save_attempts=forbidden
        with buffering.ResultBuffer(db,job,cfg,writer=control) as buffer:
            for ordinal in range(int(job['cursor']),int(job['stop'])):
                buffer.append(ordinal,report(),None)
    producers=[multiprocessing.get_context('fork').Process(target=calculate,args=(job,)) for job in (first,second)]
    try:
        for producer in producers:producer.start()
        for producer in producers:
            producer.join(10)
            assert producer.exitcode==0
        rows=[json.loads(line) for line in calls.read_text().splitlines()]
        assert len(rows)==2 and all(r==dict(pid=process.pid,count=16) for r in rows)
        assert database.growth_run_info(db)['attempted']==32
        attempts=database.growth_rows(db,'combichem3d_attempt')
        assert len(attempts)==len({(r['parent_pose'],r['ordinal']) for r in attempts})==32
        assert all(r['state']=='done' for r in database.growth_rows(db,'combichem3d_batch'))
    finally:
        for producer in producers:
            if producer.is_alive():producer.terminate()
            producer.join(5)
        stop(process,control)
    assert process.exitcode==0


def test_installed_distributed_adapter_forwards_local_writer_state(monkeypatch):
    from graft.stages import combichem_distributed as distributed
    control=state();seen=[]
    def local(db,cfg,iteration,result_writer=None):
        seen.append((db,iteration,result_writer))
    monkeypatch.setattr(distributed,'_ORIGINAL_WORKER',local)
    distributed.worker_loop('local.sqlite',{},2,control)
    assert seen==[('local.sqlite',2,control)]


def test_writer_does_not_read_a_live_prefix_but_drains_it_after_worker_exit(project):
    db, job, cfg = project
    control = state();process=start(db,control)
    try:
        buffer=buffering.ResultBuffer(db,job,cfg,writer=control)
        buffer.append(0,report(),None)
        control['wake'].set()
        time.sleep(.12)
        assert database.growth_run_info(db)['attempted']==0
        # Simulate a killed calculator: no publication, no more journal writes.
    finally:
        stop(process,control)
    assert process.exitcode==0
    assert database.growth_run_info(db)['attempted']==1
    assert not buffer.path.exists()
    assert database.growth_rows(db,'combichem3d_batch')[0]['cursor']=='1'


def test_batch_finish_and_results_are_one_atomic_transaction(project, monkeypatch):
    db,job,cfg=project
    buffer=buffering.ResultBuffer(db,job,cfg,writer=state())
    for ordinal in range(16):buffer.append(ordinal,report(),None)
    ready=publish(buffer)
    original=database.connect;transactions=[]
    def connect(path):
        conn=original(path)
        conn.set_trace_callback(lambda sql:transactions.append(sql) if sql=='BEGIN IMMEDIATE' else None)
        return conn
    monkeypatch.setattr(database,'connect',connect)
    writer.commit_ready(db,ready)
    assert transactions==['BEGIN IMMEDIATE']
    assert database.growth_rows(db,'combichem3d_batch')[0]['state']=='done'


def test_crash_after_journal_unlink_recovers_ack_from_db_without_recount(project,monkeypatch):
    db,job,cfg=project
    buffer=buffering.ResultBuffer(db,job,cfg,writer=state())
    for ordinal in range(16):buffer.append(ordinal,report(),None)
    ready=publish(buffer)
    unlink=Path.unlink
    with monkeypatch.context() as patch:
        def crash(path,*a,**kw):
            if path==ready:raise OSError('Killed before acknowledgement')
            return unlink(path,*a,**kw)
        patch.setattr(Path,'unlink',crash)
        with pytest.raises(OSError,match='acknowledgement'):writer.commit_ready(db,ready)
    assert ready.exists() and not buffer.path.exists()
    buffering.recover(db)
    assert not ready.exists()
    assert database.growth_run_info(db)['attempted']==16
    assert database.growth_rows(db,'combichem3d_batch')[0]['state']=='done'


@pytest.mark.parametrize('fault',['checksum','ordinal','lost_uncommitted_journal'])
def test_invalid_publication_stays_durable_and_cannot_advance_batch(project,fault):
    db,job,cfg=project
    buffer=buffering.ResultBuffer(db,job,cfg,writer=state())
    buffer.append(0,report(),None)
    if fault=='ordinal':
        row=json.loads(buffer.path.read_text());row['ordinal']=999
        buffer.path.write_text(json.dumps(row)+'\n')
    ready=publish(buffer)
    if fault=='checksum':buffer.path.write_text('changed')
    elif fault=='lost_uncommitted_journal':buffer.path.unlink()
    with pytest.raises((ValueError,RuntimeError)):
        buffering.recover(db)
    assert ready.exists()
    assert database.growth_run_info(db)['attempted']==0


def test_writer_failure_preserves_published_result_and_unblocks_calculator(project):
    db,job,cfg=project
    control=state();control['failed'].set()
    buffer=buffering.ResultBuffer(db,job,cfg,writer=control)
    buffer.append(0,report(),None)
    with pytest.raises(RuntimeError,match='writer failed'):buffer.flush()
    assert buffer.path.exists() and buffer.path.with_suffix('.ready').exists()
    assert database.growth_run_info(db)['attempted']==0
    buffering.recover(db)
    assert database.growth_run_info(db)['attempted']==1


def test_writer_storage_failure_signals_failure_and_recovery_replays_once(project,monkeypatch):
    db,job,cfg=project
    control=state()
    buffer=buffering.ResultBuffer(db,job,cfg,writer=control)
    buffer.append(0,report(),None)
    ready=publish(buffer)
    with monkeypatch.context() as patch:
        def unavailable(*a,**k):raise OSError('Storage unavailable')
        patch.setattr(database,'growth_save_attempts',unavailable)
        process=start(db,control)
        process.join(10)
        if process.is_alive():process.terminate();process.join(5);pytest.fail('Writer failure hung')
    assert process.exitcode!=0 and control['failed'].is_set()
    assert ready.exists() and buffer.path.exists()
    assert database.growth_run_info(db)['attempted']==0
    buffering.recover(db)
    buffering.recover(db)
    assert database.growth_run_info(db)['attempted']==1


def test_writer_orphan_does_not_read_still_active_calculator_journals(project):
    db,job,cfg=project
    control=state()
    buffer=buffering.ResultBuffer(db,job,cfg,writer=control)
    buffer.append(0,report(),None)
    process=multiprocessing.get_context('fork').Process(target=writer.run,args=(db,control,-1))
    process.start();process.join(10)
    if process.is_alive():process.terminate();process.join(5);pytest.fail('Orphan writer hung')
    assert process.exitcode!=0 and control['failed'].is_set()
    assert buffer.path.exists()
    assert database.growth_run_info(db)['attempted']==0


def test_writer_retains_all_computed_poses_while_admitting_only_target(project):
    from rdkit import Chem
    from graft.chemistry.combichem3d.assembly import assemble
    from graft.chemistry.combichem3d import history
    db,job,cfg=project
    database.growth_start_session(db,cfg | {'stop-mode':'target','target-count':1})
    parent=database.growth_parent(db,job['parent_pose'])
    mol=stage.restore_parent(parent)
    buffer=buffering.ResultBuffer(db,job,cfg,writer=state())
    for ordinal,fragment in enumerate(['*C','*CC','*CCC']):
        product,birth=assemble(mol,parent['history'],parent['history']['attachments'],[fragment],f'b{ordinal}')
        payload=dict(smiles=history.canonical_smiles(product),molblock=Chem.MolToMolBlock(product),
            history=birth,properties={},output_passed=True,pruned=False)
        buffer.append(ordinal,dict(status='PASS',iteration=1,output_passed=True),payload)
    writer.commit_ready(db,publish(buffer))
    poses=[r for r in database.growth_rows(db,'combichem3d_pose') if r['generated']]
    assert len(poses)==3 and sum(r['output_passed'] for r in poses)==1
    assert database.growth_run_info(db)['attempted']==3
    assert database.growth_run_info(db)['unique_count']==1


def test_empty_reclaimed_completed_batch_does_not_wait_forever(project):
    db,job,cfg=project
    database.growth_save_attempts(db,job,[(i,report(),None) for i in range(16)])
    buffer=buffering.ResultBuffer(db,dict(job,cursor='16'),cfg,writer=state())
    ready=publish(buffer)
    writer.commit_ready(db,ready)
    assert not ready.exists()
    assert database.growth_rows(db,'combichem3d_batch')[0]['state']=='done'


@pytest.mark.parametrize('target_reached',[False,True])
def test_supervisor_detects_killed_writer_and_stops_without_hanging(project,monkeypatch,target_reached):
    db,job,cfg=project
    def killed(*args):
        if target_reached:database.growth_request_stop(db,'target_count')
        os._exit(3)
    monkeypatch.setattr(writer,'run',killed)
    def calculator(db,cfg,iteration,result_writer=None):
        assert result_writer['failed'].wait(5)
    monkeypatch.setattr(stage,'worker_loop',calculator)
    started=time.monotonic()
    with pytest.raises(RuntimeError,match='completion withheld'):
        stage.supervise(cfg | {'max-iteration':1,'processes':1},db,1)
    assert time.monotonic()-started<5
    assert database.growth_run_info(db)['stop_reason']==('target_count' if target_reached else 'worker_error')
