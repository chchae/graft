"""A failed native query owns uncertainty, not a scientific failure or new job."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from graft import _core, database, glide_recovery as recovery, schrodinger_guard as guard
from graft.stages import glide


@pytest.mark.parametrize('kind', ['exit', 'timeout', 'io', 'json', 'missing_parent', 'nonobject'])
def test_failed_info_keeps_saved_group_unknown_and_never_uses_cached_output(tmp_path, monkeypatch, capsys, kind):
    record = dict(job_id='original', submitted_at=1, downloaded=False)
    recovery.save_record(tmp_path,record)
    before = recovery.record_file(tmp_path).read_bytes()
    log,out=tmp_path/'glide.log',tmp_path/'glide_lib.maegz'
    log.write_text('Finished at: old job\n');out.write_text('cached old poses')
    calls=[]
    def native(command,**kw):
        calls.append(command)
        assert command[1]=='info'  # No stop, download, launch or other mutation.
        if kind=='exit':raise subprocess.CalledProcessError(1,command,stderr='JobServer connection reset by peer')
        if kind=='timeout':raise subprocess.TimeoutExpired(command,30,stderr='query deadline exceeded')
        if kind=='io':raise OSError('JobServer unavailable')
        text={'json':'broken-json','missing_parent':json.dumps(dict(jobId='child',status='DONE')),
              'nonobject':'[]'}[kind]
        return subprocess.CompletedProcess(command,0,text,'')
    monkeypatch.setattr(guard.subprocess,'run',native)
    monkeypatch.setattr(recovery,'job_details',guard.job_details)
    assert recovery.group_status('/suite',tmp_path,log,out,[],1800)=='UNKNOWN'
    assert recovery.record_file(tmp_path).read_bytes()==before
    assert out.read_text()=='cached old poses'
    assert len(calls)==1
    warning=capsys.readouterr().out
    assert '[WARNING]' in warning and 'original' in warning and 'UNKNOWN' in warning
    if kind=='exit':assert 'connection reset by peer' in warning


def test_transient_failure_retries_same_job_and_downloads_only_after_terminal_tree(tmp_path,monkeypatch):
    recovery.save_record(tmp_path,dict(job_id='saved',downloaded=False))
    log,out=tmp_path/'glide.log',tmp_path/'glide_lib.maegz'
    out.write_text('old poses')
    calls=[]
    def native(command,**kw):
        calls.append(command)
        if len(calls)==1:
            raise subprocess.CalledProcessError(1,command,stderr='temporary failure')
        if command[1]=='info':
            jobs=[dict(jobId='saved',status='DONE'),dict(jobId='child',status='DONE')]
            return subprocess.CompletedProcess(command,0,'\n'.join(map(json.dumps,jobs)),'')
        assert command==['/suite/jsc','download','--cwd','saved']
        out.write_text('verified new poses')
        return subprocess.CompletedProcess(command,0,'','')
    monkeypatch.setattr(guard.subprocess,'run',native)
    monkeypatch.setattr(recovery,'job_details',guard.job_details)
    assert recovery.group_status('/suite',tmp_path,log,out,[],1800)=='UNKNOWN'
    assert not recovery.load_record(tmp_path)['downloaded']
    assert recovery.group_status('/suite',tmp_path,log,out,[],1800)=='DONE'
    assert [cmd[1] for cmd in calls]==['info','info','download']
    assert recovery.load_record(tmp_path)['job_id']=='saved'
    assert recovery.load_record(tmp_path)['downloaded']


@pytest.mark.parametrize('global_failure',[False,True])
def test_unknown_peer_does_not_block_verified_group_pose_and_db_publication(tmp_path,monkeypatch,global_failure):
    db=tmp_path/'test.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (?,?,'CC',4,'glide_running')",
                         [(1,'CMPID_1'),(2,'CMPID_2')])
    jobs=[]
    for cid in (1,2):
        folder=tmp_path/f'ref_{cid}';folder.mkdir()
        recovery.save_record(folder,dict(job_id=f'job-{cid}',downloaded=True))
        (folder/'glide_lib.maegz').write_text(f'poses {cid}')
        jobs.append(dict(reference_id=cid,group_dir=folder,compounds=[(cid,)]))
    original=recovery.record_file(jobs[0]['group_dir']).read_bytes()
    def active(*a):
        if global_failure:raise guard.JobStatusUnavailable('global lookup failed')
        return []
    monkeypatch.setattr(glide,'active_jobs',active)
    def details(suite,jobid,directory):
        if jobid=='job-1':raise guard.JobStatusUnavailable('query job-1 failed')
        return [dict(jobId=jobid,status='DONE')]
    monkeypatch.setattr(recovery,'job_details',details)
    def extract(suite,helper,output,folder):
        assert folder==jobs[1]['group_dir']
        scores=folder/'docking_scores.tsv';scores.write_text('id\tdocking_score\n2\t-8.5\n')
        best=folder/'best_poses.maegz';best.write_text('selected pose 2')
        return scores,best
    monkeypatch.setattr(_core,'extract_scores_and_best_poses',extract)
    def publish(args,root,helper):
        (root/'best_poses.maegz').write_text((jobs[1]['group_dir']/'best_poses.maegz').read_text())
    monkeypatch.setattr(glide,'merge_current_best',publish)
    args=SimpleNamespace(schrodinger=Path('/suite'),db_path=db,glide_job_timeout=1800)
    pending,finished=glide.collect_finished_groups(4,args,jobs,'extractor','merger',tmp_path)
    assert pending==[jobs[0]] and finished==1
    with database.connect(db) as conn:
        assert conn.execute('SELECT id,state,docking_score,failed_stage FROM compound ORDER BY id').fetchall()==[
            (1,'glide_running',None,None),(2,'docked',-8.5,None)]
    assert (tmp_path/'best_poses.maegz').read_text()=='selected pose 2'
    assert recovery.record_file(jobs[0]['group_dir']).read_bytes()==original


def test_uncertain_info_still_fences_all_new_submissions(monkeypatch):
    monkeypatch.setattr(guard,'active_jobs',lambda *a:(_ for _ in ()).throw(guard.JobStatusUnavailable('query failed')))
    with pytest.raises(guard.SubmissionBlocked,match='Cannot verify'):
        with guard.submission_guard('/suite','glide'):
            pytest.fail('Submitted under uncertain native status')


@pytest.mark.parametrize('native',[True,False])
def test_timeout_recovery_defers_native_uncertainty_but_propagates_scientific_errors(tmp_path,monkeypatch,native):
    error=guard.JobStatusUnavailable('temporary native query error') if native else ValueError('pose identity mismatch')
    monkeypatch.setattr(glide,'process_timeout_job',lambda *a:(_ for _ in ()).throw(error))
    job=dict(group_dir=tmp_path)
    if native:
        assert glide.try_timeout_collection(4,None,job,None,tmp_path) is False
    else:
        with pytest.raises(ValueError,match='pose identity mismatch'):
            glide.try_timeout_collection(4,None,job,None,tmp_path)


def test_partial_recovery_download_error_preserves_timeout_evidence(tmp_path,monkeypatch):
    record=dict(job_id='parent',timeout_requested_at=1,timeout_seconds=1800,downloaded=False)
    recovery.save_record(tmp_path,record)
    monkeypatch.setattr(recovery,'job_details',lambda *a:[dict(jobId='parent',status='STOPPED')])
    def download(cmd,**kw):
        raise subprocess.CalledProcessError(1,cmd,stderr='download not ready')
    monkeypatch.setattr(recovery.subprocess,'run',download)
    with pytest.raises(guard.JobStatusUnavailable,match='download not ready'):
        recovery.timeout_pose_files('/suite',tmp_path)
    assert recovery.load_record(tmp_path)==record


def test_terminal_timeout_group_with_unavailable_recovery_keeps_peer_collection_running(tmp_path,monkeypatch):
    jobs=[]
    for cid in (1,2):
        folder=tmp_path/f'ref_{cid}';folder.mkdir()
        record=dict(job_id=f'job-{cid}',downloaded=True)
        if cid==1:record['subjob_timeouts']={'child':dict(job_name='glide_01-0001')}
        recovery.save_record(folder,record)
        (folder/'glide_lib.maegz').write_text('poses')
        jobs.append(dict(group_dir=folder,reference_id=cid))
    monkeypatch.setattr(glide,'active_jobs',lambda *a:[])
    monkeypatch.setattr(glide,'group_status',lambda *a,**kw:'DONE')
    monkeypatch.setattr(glide,'process_timeout_job',lambda *a:(_ for _ in ()).throw(guard.JobStatusUnavailable('query failed')))
    committed=[]
    monkeypatch.setattr(glide,'process_completed_job',lambda i,a,job,*rest:committed.append(job['reference_id']))
    pending,finished=glide.collect_finished_groups(4,SimpleNamespace(schrodinger=Path('/suite')),jobs,None,None,tmp_path)
    assert pending==[jobs[0]] and finished==1 and committed==[2]
    assert 'subjob_timeouts' in recovery.load_record(jobs[0]['group_dir'])


def test_local_journal_write_failure_is_not_hidden_as_a_native_query_failure(tmp_path,monkeypatch):
    log,out=tmp_path/'glide.log',tmp_path/'glide_lib.maegz'
    log.write_text('JobId: recovered\n')
    def fail(*a):raise OSError('local disk full')
    monkeypatch.setattr(recovery,'save_record',fail)
    with pytest.raises(OSError,match='local disk full'):
        recovery.group_status('/suite',tmp_path,log,out,[])
