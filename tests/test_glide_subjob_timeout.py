"""Child-only runtime limits and verified collection of completed peer results."""
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from graft import _core, database, driver, glide_recovery as recovery
from graft import glide_timeout_results as native
from graft.stages import glide
from test_glide_timeout_results import Pose, structure_module


def timestamp(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def test_old_parent_and_queued_or_young_children_never_trigger_group_timeout(tmp_path, monkeypatch):
    record = dict(job_id='parent', submitted_at=1, command=['glide', 'glide_03.in', '-NJOBS', '3'])
    recovery.save_record(tmp_path, record)
    jobs = [dict(jobId='parent', status='RUNNING', timeStarted=timestamp(1)),
            dict(jobId='queued', jobName='glide_03-0001', status='SUBMITTED', timeCreated=timestamp(1)),
            dict(jobId='young', jobName='glide_03-0002', status='RUNNING', timeStarted=timestamp(1999)),
            dict(jobId='missing-start', jobName='glide_03-0003', status='RUNNING')]
    monkeypatch.setattr(recovery, 'job_details', lambda *a: jobs)
    monkeypatch.setattr(recovery.time, 'time', lambda: 2000)
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *a, **kw: pytest.fail('Stopped eligible/uncertain work'))
    assert recovery.enforce_timeout('/suite', tmp_path, record, 1800) is None
    assert recovery.load_record(tmp_path) == record


@pytest.mark.parametrize('status,started', [('SUBMITTED', timestamp(1)), ('WAITING', None),
                                            ('RUNNING', None), ('RUNNING', 'invalid')])
def test_single_job_requires_proven_actual_running_time(tmp_path, monkeypatch, status, started):
    record = dict(job_id='parent', submitted_at=1)
    monkeypatch.setattr(recovery, 'job_details', lambda *a: [dict(jobId='parent',status=status,timeStarted=started)])
    monkeypatch.setattr(recovery.time, 'time', lambda: 9999)
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *a, **kw: pytest.fail('Counted queue time'))
    assert recovery.enforce_timeout('/suite', tmp_path, record, 1800) is None


def test_driver_before_child_discovery_is_not_a_serial_calculation(tmp_path, monkeypatch):
    record = dict(job_id='parent', submitted_at=1)
    job = dict(jobId='parent', status='RUNNING', timeStarted=timestamp(1),
               spec=dict(taskSpec=dict(commandLineArguments=['run', 'glide_driver.py'])))
    monkeypatch.setattr(recovery, 'job_details', lambda *a: [job])
    monkeypatch.setattr(recovery.time, 'time', lambda: 9999)
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *a, **kw: pytest.fail('Stopped preparing parent'))
    assert recovery.enforce_timeout('/suite', tmp_path, record, 1800) is None


@pytest.mark.parametrize('stop_error', [False, True])
def test_only_overrun_child_stops_with_durable_exclusion_and_restart(tmp_path, monkeypatch, stop_error):
    recovery.save_record(tmp_path, dict(job_id='parent', submitted_at=1))
    jobs = [dict(jobId='parent', status='RUNNING', timeStarted=timestamp(1)),
            dict(jobId='overrun', jobName='glide_03-0001', status='RUNNING',timeStarted=timestamp(10)),
            dict(jobId='peer', jobName='glide_03-0002', status='RUNNING',timeStarted=timestamp(100)),
            dict(jobId='done', jobName='glide_03-0003', status='DONE',timeStarted=timestamp(1))]
    monkeypatch.setattr(recovery, 'job_details', lambda *a: jobs)
    clock = [1810]
    monkeypatch.setattr(recovery.time, 'time', lambda: clock[0])
    calls = []
    def stop(cmd, **kw):
        saved = recovery.load_record(tmp_path)
        assert set(saved['subjob_timeouts']) == {'overrun'}
        assert 'timeout_requested_at' not in saved
        calls.append(cmd)
        if stop_error:
            raise OSError('lost stop response')
    monkeypatch.setattr(recovery.subprocess, 'run', stop)
    if stop_error:
        with pytest.raises(OSError, match='lost stop response'):
            recovery.enforce_timeout('/suite',tmp_path,recovery.load_record(tmp_path),1800)
        assert 'stop_sent_at' not in recovery.load_record(tmp_path)['subjob_timeouts']['overrun']
    else:
        assert recovery.enforce_timeout('/suite',tmp_path,recovery.load_record(tmp_path),1800) == 'RUNNING'
    assert calls == [['/suite/jsc','stop','overrun']]
    # A disabled limit after restart still reconciles an existing stop intent,
    # without stopping a peer which has now passed the disabled limit.
    clock[0] = 1900
    monkeypatch.setattr(recovery.subprocess, 'run', lambda cmd, **kw: calls.append(cmd))
    assert recovery.enforce_timeout('/suite',tmp_path,recovery.load_record(tmp_path),0) == 'RUNNING'
    assert calls[-1] == ['/suite/jsc','stop','overrun']
    jobs[1]['status'] = 'STOPPED'
    count = len(calls)
    assert recovery.enforce_timeout('/suite',tmp_path,recovery.load_record(tmp_path),0) == 'RUNNING'
    assert len(calls) == count


def test_stopped_child_does_not_complete_or_download_while_parent_still_merges(tmp_path, monkeypatch):
    recovery.save_record(tmp_path, dict(job_id='parent',timeout_seconds=1800,
        subjob_timeouts={'child':dict(job_name='glide_03-0001',requested_at=1)}))
    jobs=[dict(jobId='parent',status='RUNNING'),dict(jobId='child',status='STOPPED'),dict(jobId='peer',status='DONE')]
    monkeypatch.setattr(recovery,'job_details',lambda *a:jobs)
    calls=[]
    monkeypatch.setattr(recovery.subprocess,'run',lambda cmd,**kw:calls.append(cmd))
    assert recovery.group_status('/suite',tmp_path,tmp_path/'glide.log',tmp_path/'glide_lib.maegz',[],1800)=='RUNNING'
    assert calls==[]
    jobs[0]['status']='DONE'
    assert recovery.group_status('/suite',tmp_path,tmp_path/'glide.log',tmp_path/'glide_lib.maegz',[],1800)=='DONE'
    assert calls==[['/suite/jsc','download','--cwd','parent']]
    assert not recovery.load_record(tmp_path).get('timeout_results_imported')


def archive(folder, name='glide_03-0002', text='LIGAND_START 1\nLIGAND_END 2\nLIGAND_OFFSET 2\n'):
    path=folder/'glide_03_subjobs.tar.gz'
    with tarfile.open(path,'w:gz') as tar:
        member=tarfile.TarInfo(name+'.inp')
        data=text.encode();member.size=len(data)
        tar.addfile(member,io.BytesIO(data))
    return path


def native_request(folder):
    prepared=folder/'ligprep_group.maegz';prepared.write_text('native input')
    source=folder/'glide_03_lib.maegz';source.write_text('native merged output')
    request=dict(inputs=[str(source)],ids=[2,3,4,5],output=str(folder/'extracted'),
        exclude_subjobs=['glide_03-0002'],subjob_archive=str(archive(folder)),ligprep_input=str(prepared))
    inputs={str(prepared):[Pose(2,0,'input'),Pose(3,0,'input'),Pose(2,0,'input'),Pose(4,0,'input'),Pose(5,0,'input')]}
    poses=[Pose(2,-8,'complete'),Pose(3,-7,'complete'),Pose(2,-99,'excluded-partial'),Pose(4,-98,'excluded-partial')]
    for index,pose in enumerate(poses,1):pose.property['i_i_glide_lignum']=index
    inputs[str(source)]=poses
    return request,inputs


def test_excludes_every_pose_of_overrun_child_before_lowest_state_reduction(tmp_path):
    request,inputs=native_request(tmp_path)
    report=native.collect(request,structure_module(inputs))
    rows=json.loads((tmp_path/'extracted/best_poses.maegz').read_text())
    assert [(row['title'],row['properties']['r_i_docking_score']) for row in rows]==[('CMPID_2',-8),('CMPID_3',-7)]
    assert report['timeout_ids']==[2,4] and report['excluded_poses']==2
    assert report['excluded_subjobs']==[dict(job_name='glide_03-0002',first=3,last=4)]
    assert len(report['source_checksums'])==3


@pytest.mark.parametrize('problem',['missing_archive','wrong_range','missing_index','wrong_cmpid'])
def test_ambiguous_native_exclusion_does_not_publish_scores(tmp_path,problem):
    request,inputs=native_request(tmp_path)
    if problem=='missing_archive':Path(request['subjob_archive']).unlink()
    elif problem=='wrong_range':archive(tmp_path,text='LIGAND_START 1\nLIGAND_END 100\nLIGAND_OFFSET 2\n')
    elif problem=='missing_index':inputs[request['inputs'][0]][0].property.pop('i_i_glide_lignum')
    else:inputs[request['inputs'][0]][0].title='CMPID_3'
    with pytest.raises((ValueError,RuntimeError,OSError)):
        native.collect(request,structure_module(inputs))
    assert not (tmp_path/'extracted/docking_scores.tsv').exists()


@pytest.fixture
def project(tmp_path):
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(1,'REF','CC',0,'reference')")
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) VALUES(?,?,'CC',1,'glide_running',1)",
                         [(cid,f'CMPID_{cid}') for cid in (2,3,4,5)])
    folder=tmp_path/'iter01/06-glide/ref_1';folder.mkdir(parents=True)
    request,inputs=native_request(folder)
    recovery.save_record(folder,dict(job_id='parent',command=['glide',str(folder/'glide_03.in')],downloaded=True,
        timeout_seconds=1800,subjob_timeouts={'slow':dict(job_name='glide_03-0002',requested_at=1)}))
    args=SimpleNamespace(db_path=db,output=tmp_path,schrodinger=Path('/suite'))
    job=dict(group_dir=folder,reference_id=1,compounds=database.glide_timeout_rows(db,1,1))
    return args,job,inputs


@pytest.mark.parametrize('raced_done',[False,True])
def test_good_peers_commit_atomically_and_survive_restart(project,monkeypatch,raced_done):
    args,job,inputs=project;folder=job['group_dir']
    native_rows=[dict(jobId='parent',status='DONE'),dict(jobId='slow',jobName='glide_03-0002',status='DONE' if raced_done else 'STOPPED'),
                 dict(jobId='peer',jobName='glide_03-0001',status='DONE')]
    monkeypatch.setattr(glide,'job_details',lambda *a:native_rows)
    def collect(command,**kw):
        request=json.loads(Path(command[-1]).read_text())
        native.collect(request,structure_module(inputs))
    monkeypatch.setattr(_core,'run_command',collect)
    def publish(args,root,merger):
        with database.connect(args.db_path) as conn:
            assert conn.execute('SELECT COUNT(docking_score) FROM compound WHERE iteration=1').fetchone()[0]==0
        assert database.docking_completion_reason(args.db_path,1,4)=='Glide timeout result collection pending'
        assert (folder/'best_poses.maegz').exists()
    monkeypatch.setattr(glide,'merge_current_best',publish)
    glide.process_timeout_job(1,args,job,'merger',folder.parent)
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score,state FROM compound WHERE id=2').fetchone()==(-99 if raced_done else -8,'docked')
        assert conn.execute('SELECT state,failure_message FROM compound WHERE id=4').fetchone()==(
            ('docked',None) if raced_done else ('failed','Glide job exceeded 1800s; no valid score/pose recovered'))
        assert conn.execute('SELECT state,failure_message FROM compound WHERE id=5').fetchone()==('failed','No Glide pose')
    assert database.glide_timeout_failed_ids(args.db_path,1)==(set() if raced_done else {4})
    record=recovery.load_record(folder);record.pop('timeout_results_imported');recovery.save_record(folder,record)
    monkeypatch.setattr(glide,'job_details',lambda *a:pytest.fail('Repeated committed collection'))
    monkeypatch.setattr(_core,'run_command',lambda *a,**kw:pytest.fail('Repeated extraction'))
    glide.process_timeout_job(1,args,job,'merger',folder.parent)
    assert recovery.load_record(folder)['timeout_results_imported']


def test_driver_blocks_unimported_child_timeouts_even_with_cached_download(project):
    args,job,_=project
    assert 'timeout results require recovery' in driver.external_completion_reason(args,1,'glide')


@pytest.mark.parametrize('parent_status,child_status', [('RUNNING','STOPPED'),('DONE','RUNNING'),('FAILED','STOPPED')])
def test_collection_refuses_unfinished_or_failed_parent_without_using_cached_poses(
        project,monkeypatch,parent_status,child_status):
    args,job,_=project
    monkeypatch.setattr(glide,'job_details',lambda *a:[dict(jobId='parent',status=parent_status),
        dict(jobId='slow',jobName='glide_03-0002',status=child_status)])
    monkeypatch.setattr(_core,'run_command',lambda *a,**kw:pytest.fail('Used unverified merged poses'))
    with pytest.raises(RuntimeError,match='waits|must finish'):
        glide.process_timeout_job(1,args,job,'merger',job['group_dir'].parent)
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT COUNT(docking_score) FROM compound WHERE iteration=1').fetchone()[0]==0
    assert database.docking_completion_reason(args.db_path,1,4)=='Glide timeout result collection pending'


def test_zero_poses_marks_only_excluded_inputs_as_timeout(project,monkeypatch):
    args,job,inputs=project;folder=job['group_dir']
    (folder/'glide_03_lib.maegz').unlink()
    monkeypatch.setattr(glide,'job_details',lambda *a:[dict(jobId='parent',status='DONE'),
        dict(jobId='slow',jobName='glide_03-0002',status='STOPPED')])
    def collect(command,**kw):
        request=json.loads(Path(command[-1]).read_text())
        assert request['inputs']==[]
        native.collect(request,structure_module(inputs))
    monkeypatch.setattr(_core,'run_command',collect)
    monkeypatch.setattr(glide,'merge_current_best',lambda *a:pytest.fail('Published nonexistent poses'))
    glide.process_timeout_job(1,args,job,'merger',folder.parent)
    assert database.glide_timeout_failed_ids(args.db_path,1)=={2,4}
    assert database.docking_completion_reason(args.db_path,1,4) is None


def test_foreign_timeout_members_roll_back_score_transaction(project):
    args,job,_=project
    database.begin_glide_timeout_collection(args.db_path,1,1,'parent')
    with pytest.raises(ValueError,match='foreign'):
        database.store_glide_timeout_scores(args.db_path,1,1,'parent',{2:-8},1800,{},timeout_ids=[999])
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score,state FROM compound WHERE id=2').fetchone()==(None,'glide_running')
        assert conn.execute('SELECT completed FROM glide_timeout_collection').fetchone()==(0,)
