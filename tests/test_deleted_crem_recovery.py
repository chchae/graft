"""Deleted population recovery fences native work; no scientific validation."""
import json
import io
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from test_crem_pipeline import project
from test_combichem_crem import project as growth_project
from graft import database, driver, schrodinger_guard as guard
from graft.paths import iteration_directory
from graft.stages import generate, druglike, _deleted_crem
from graft.stages._workspace import prepare_iteration_directory, WorkspaceReconciliationPending


@pytest.fixture
def deleted(project, monkeypatch):
    project.target_count = 2
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    database.start_stage_timer(project.db_path, 1, 'ligprep')
    root = iteration_directory(project.output, 1)
    shutil.rmtree(root)
    with sqlite3.connect(project.db_path) as conn:
        conn.execute('DELETE FROM compound WHERE iteration=1')
    monkeypatch.setattr(guard, 'active_jobs', lambda *a: [])
    monkeypatch.setattr(_deleted_crem, 'native_history', lambda *a:
                        dict(servers=['native:8030'], history_ids=['parent'], active_ids=[], checked_at=1))
    monkeypatch.setattr(guard, 'job_details', lambda *a: [
        dict(jobId='parent', status='DONE', spec=dict(launchParams=dict(launchDirectory=str(root/'05-ligprep')))),
        dict(jobId='child', status='DONE', parentJobId='parent')])
    return project


def test_deleted_population_and_directory_select_generate_then_back_up_and_reset(deleted):
    database.freeze_attraction_policy(deleted.db_path, 1, 'amber_gbsa', None, {})
    assert driver.choose_iteration(deleted) == 1
    assert database.iteration_was_deleted(deleted.db_path, 1)
    assert database.generation_readiness_reason(deleted.db_path, 1, 2) is not None
    archive = prepare_iteration_directory(deleted, 1)
    assert database.crem_generation(deleted.db_path, 1) is None
    assert database.attraction_policy(deleted.db_path, 1, 'amber_gbsa') is None
    assert not database.iteration_was_deleted(deleted.db_path, 1)
    proof = json.loads((archive/'deleted_crem_reconciliation.json').read_text())
    assert len(proof['native']) == 2
    backup, = archive.glob('database-before-reset-*.sqlite')
    with sqlite3.connect(backup) as conn:
        assert conn.execute('SELECT population_count FROM iteration_crem WHERE iteration=1').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM crem_compound_pose WHERE iteration=1').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM iteration_attraction_point WHERE iteration=1').fetchone()[0] == 1
    with database.connect(deleted.db_path) as conn:
        assert conn.execute('SELECT count(*) FROM compound WHERE iteration=0').fetchone()[0] > 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    generate._run_stage(deleted, 1)
    assert database.crem_generation(deleted.db_path, 1)['population_count'] == 2
    assert not database.iteration_was_deleted(deleted.db_path, 1)


@pytest.mark.parametrize('status', ['RUNNING', 'UNKNOWN'])
def test_live_or_uncertain_child_defers_reset(deleted, monkeypatch, status):
    root = iteration_directory(deleted.output, 1)
    monkeypatch.setattr(guard, 'job_details', lambda *a: [
        dict(jobId='parent',status='DONE',commandLine=str(root/'glide')),
        dict(jobId='child',status=status)])
    with pytest.raises(WorkspaceReconciliationPending, match='live/uncertain'):
        prepare_iteration_directory(deleted, 1)
    assert database.crem_generation(deleted.db_path, 1)['population_count'] == 2
    assert not root.exists()
    assert not (Path(deleted.output)/'.previous').exists()


def test_remaining_directory_is_cleared_and_archived_before_regeneration(deleted):
    root = iteration_directory(deleted.output, 1)
    root.mkdir()
    (root/'evidence').write_text('retain')
    archive = prepare_iteration_directory(deleted, 1)
    assert list(root.iterdir()) == []
    assert (archive/'evidence').read_text() == 'retain'
    assert database.crem_generation(deleted.db_path, 1) is None


def test_missing_native_history_uses_complete_active_snapshot_and_backs_up(deleted, monkeypatch):
    monkeypatch.setattr(_deleted_crem, 'native_history', lambda *a:
                        dict(servers=['native:8030'], history_ids=[], active_ids=[], checked_at=1))
    archive = prepare_iteration_directory(deleted, 1)
    proof = json.loads((archive/'deleted_crem_reconciliation.json').read_text())
    assert proof['native_history_missing'] is True
    assert proof['native'] == []
    assert proof['native_query']['servers'] == ['native:8030']
    assert next(archive.glob('database-before-reset-*.sqlite')).exists()
    assert database.crem_generation(deleted.db_path, 1) is None


def test_native_lookup_failure_is_pending_without_reset(deleted, monkeypatch):
    def unavailable(*a):
        raise OSError('JobServer unavailable')
    monkeypatch.setattr(guard, 'job_details', unavailable)
    with pytest.raises(WorkspaceReconciliationPending, match='JobServer unavailable'):
        prepare_iteration_directory(deleted, 1)
    assert database.crem_generation(deleted.db_path, 1) is not None


@pytest.mark.parametrize('diagnostic', [
    'Traceback (most recent call last):\n  File "<string>", line 25\nRuntimeError: Amber job remains active: 123\n',
    b'RuntimeError: Amber job remains active: 123\n',
])
def test_remote_error_shows_cause_without_inline_program_or_reset(deleted,monkeypatch,diagnostic):
    def active(*args):
        raise subprocess.CalledProcessError(1,['ssh','python3 -c INLINE_PROGRAM'],stderr=diagnostic)
    monkeypatch.setattr(_deleted_crem,'reconcile',active)
    from graft.stages import generate
    # The real Generate boundary converts pending reconciliation into a normal
    # worker return so the supervisor can retry without a traceback.
    with pytest.raises(WorkspaceReconciliationPending) as error:
        prepare_iteration_directory(deleted,1)
    assert str(error.value).endswith('RuntimeError: Amber job remains active: 123')
    assert 'INLINE_PROGRAM' not in str(error.value)
    assert 'Traceback' not in str(error.value)
    assert generate.prepare_workspace(deleted,1) is False
    assert database.crem_generation(deleted.db_path,1)['population_count']==2


def test_remote_error_without_diagnostics_stays_compact():
    from graft.stages._workspace import reconciliation_error
    error=subprocess.CalledProcessError(255,['ssh','python3 -c INLINE_PROGRAM'])
    assert reconciliation_error(error)=='External workspace inspection failed (exit status 255); recovery withheld'
    assert 'timed out' in reconciliation_error(subprocess.TimeoutExpired(['ssh','INLINE_PROGRAM'],30))


def test_active_amber_is_checked_before_native_history_scan(deleted,monkeypatch):
    database.start_stage_timer(deleted.db_path,1,'amber-gbsa')
    def active(*a):
        raise RuntimeError('Amber job remains active: 123')
    monkeypatch.setattr(_deleted_crem,'reconcile_amber',active)
    monkeypatch.setattr(_deleted_crem.subprocess,'run',lambda *a,**kw:pytest.fail('queried history before active Amber'))
    with pytest.raises(WorkspaceReconciliationPending,match='Amber job remains active: 123'):
        prepare_iteration_directory(deleted,1)


def test_later_iteration_metadata_blocks_reset_and_restores_absence(deleted):
    database.mark_iteration_workspace_prepared(deleted.db_path, 2)
    with pytest.raises(WorkspaceReconciliationPending, match='Later saved'):
        prepare_iteration_directory(deleted, 1)
    assert not iteration_directory(deleted.output, 1).exists()
    assert database.crem_generation(deleted.db_path, 1) is not None


def test_durable_md_selection_is_retained(deleted):
    with database.connect(deleted.db_path) as conn:
        conn.execute("INSERT INTO iteration_md_gbsa_selection VALUES (1,'[]','{}')")
    with pytest.raises(WorkspaceReconciliationPending, match='MD selection'):
        prepare_iteration_directory(deleted, 1)
    assert database.md_gbsa_selection(deleted.db_path, 1) is not None


def test_partial_population_still_raises_and_is_never_reset(project):
    project.target_count = 2
    generate._run_stage(project, 1)
    with sqlite3.connect(project.db_path) as conn:
        conn.execute('DELETE FROM compound WHERE iteration=1 AND id=(SELECT max(id) FROM compound)')
    assert not database.iteration_was_deleted(project.db_path, 1)
    with pytest.raises(RuntimeError, match='population changed'):
        database.generation_readiness_reason(project.db_path, 1, 2)


@pytest.mark.parametrize('issue', ['active_native', 'active_lock', 'uncertain_submission', 'mixed_ids', 'none'])
def test_amber_probe_requires_owned_terminal_tasks_and_retains_results(tmp_path, monkeypatch, capsys, issue):
    base = tmp_path/'amber'
    root = base/'run-test'
    root.mkdir(parents=True)
    (root/'input.json').write_text(json.dumps(dict(entries=[dict(id=7),dict(id=99 if issue=='mixed_ids' else 8)])))
    (root/'slurm-submission.json').write_text(json.dumps(dict(job_id='123',launching=issue=='uncertain_submission')))
    scientific = root/'result.dat'
    scientific.write_text('retain calculation')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(dict(base=str(base),ids=[7,8]))))
    def query(command, **kw):
        if 'status' in command:
            return json.dumps(dict(active=issue=='active_native',state='COMPLETED'))
        return json.dumps(dict(active=issue=='active_lock',tasks=[{},{}],results=[dict(id=7,score=-10)]))
    monkeypatch.setattr(subprocess, 'check_output', query)
    if issue == 'none':
        exec(_deleted_crem.AMBER_PROBE, {})
        proof = json.loads(capsys.readouterr().out)
        assert proof[0]['snapshot']['results'][0]['score'] == -10
    else:
        with pytest.raises(RuntimeError):
            exec(_deleted_crem.AMBER_PROBE, {})
    assert scientific.read_text() == 'retain calculation'


def test_amber_missing_or_active_evidence_defers_recovery(deleted, monkeypatch):
    database.start_stage_timer(deleted.db_path, 1, 'amber-gbsa')
    deleted.use_slurm = True
    deleted.amber_remote = dict(host='slurm', remote_work_dir='/scratch/amber')
    from graft import amber_gbsa
    monkeypatch.setattr(amber_gbsa, 'ssh', lambda *a,**kw: subprocess.CompletedProcess([],0,'[]',''))
    with pytest.raises(WorkspaceReconciliationPending, match='No surviving Amber'):
        prepare_iteration_directory(deleted, 1)
    assert database.crem_generation(deleted.db_path, 1) is not None


@pytest.mark.parametrize('complete', [True, False])
def test_retired_amber_receipt_requires_complete_results_and_native_retirement(tmp_path,monkeypatch,capsys,complete):
    base=tmp_path/'amber'
    base.mkdir()
    saved=dict(base=str(base),run_id='run-'+'a'*32,ids=[7,8],job_id='123',
               successful={'7':-20,'8':-30} if complete else {'7':-20})
    receipt=base/('.cleanup-'+saved['run_id']+'.json')
    receipt.write_text(json.dumps(saved))
    request=dict(base=str(base),ids=[7,8],status_source='read-only status source')
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(request)))
    monkeypatch.setattr(subprocess,'run',lambda cmd,**kw:
                        subprocess.CompletedProcess(cmd,0,json.dumps(dict(active=False,state='COMPLETED')),''))
    if complete:
        exec(_deleted_crem.AMBER_PROBE,{})
        proof=json.loads(capsys.readouterr().out)
        assert proof[0]['request']==saved
    else:
        with pytest.raises(RuntimeError,match='incomplete or foreign'):
            exec(_deleted_crem.AMBER_PROBE,{})
    assert json.loads(receipt.read_text())==saved


def test_history_queries_every_registered_server_and_all_active_ages(monkeypatch):
    calls=[]
    empty='No jobs matching your search criteria were found.\n'
    def run(command,**kw):
        calls.append(command)
        if command[1:]==['cert','list']:
            return subprocess.CompletedProcess(command,0,
                'User certificates exist for the following server addresses:\n'
                '  local:8030 (user: test)\n  slurm:8030 (user: test)\n','')
        assert '--server' in command, 'Aggregate queries can hide an inaccessible server'
        if '--any-status' in command:
            assert '--with-deleted-jobs' in command and '--created-after' in command
            return subprocess.CompletedProcess(command,1,'',empty)
        assert '--created-after' not in command and '--without-subjobs' not in command
        value='orphan\n' if command[command.index('--server')+1]=='slurm:8030' else ''
        return subprocess.CompletedProcess(command,0,value,'')
    monkeypatch.setattr(_deleted_crem.subprocess,'run',run)
    proof=_deleted_crem.native_history('/suite',100000)
    assert proof['servers']==['local:8030','slurm:8030']
    assert proof['history_ids']==[] and proof['active_ids']==['orphan']
    assert len(calls)==5


@pytest.mark.parametrize('code,stdout,stderr',[
    (1,'','All existing job servers are inaccessible.'),
    (0,'parent\n','rpc error: TLS authentication failed'),
    (0,'rpc error: certificate signed by unknown authority',''),
])
def test_partial_or_failed_server_query_never_becomes_empty_history(monkeypatch,code,stdout,stderr):
    def run(command,**kw):
        if command[1:]==['cert','list']:
            return subprocess.CompletedProcess(command,0,
                'User certificates exist for the following server addresses:\n  slurm:8030 (user: test)\n','')
        return subprocess.CompletedProcess(command,code,stdout,stderr)
    monkeypatch.setattr(_deleted_crem.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='failed|invalid'):
        _deleted_crem.native_history('/suite',None)


def test_missing_certificate_inventory_blocks_recovery(monkeypatch):
    monkeypatch.setattr(_deleted_crem.subprocess,'run',lambda cmd,**kw:
                        subprocess.CompletedProcess(cmd,0,'No certificates found.\n',''))
    with pytest.raises(RuntimeError,match='enumerate'):
        _deleted_crem.native_history('/suite',None)


@pytest.mark.parametrize('status',['RUNNING','UNKNOWN'])
def test_no_history_with_owned_orphaned_child_still_blocks_reset(deleted,monkeypatch,status):
    root=iteration_directory(deleted.output,1)
    monkeypatch.setattr(_deleted_crem,'native_history',lambda *a:
        dict(servers=['native:8030'],history_ids=[],active_ids=['orphan'],checked_at=1))
    orphan = dict(jobId='orphan', status=status, parentJobId='lost-parent',
                  spec={'taskSpec': {'input': {'inputFiles': [
                      {'contentUri': 'file://cpu1'+str(root/'06-glide/ref_1/_reference.maegz')}
                  ]}}})
    monkeypatch.setattr(guard,'job_details',lambda *a:[orphan])
    with pytest.raises(WorkspaceReconciliationPending,match='live/uncertain'):
        prepare_iteration_directory(deleted,1)
    assert database.crem_generation(deleted.db_path,1) is not None
    assert not root.exists()


def test_no_history_can_reset_without_touching_another_projects_live_job(deleted,monkeypatch):
    monkeypatch.setattr(_deleted_crem,'native_history',lambda *a:
        dict(servers=['native:8030'],history_ids=[],active_ids=['other'],checked_at=1))
    monkeypatch.setattr(guard,'job_details',lambda *a:[dict(jobId='other',status='RUNNING',
        spec=dict(launchParams=dict(launchDirectory='/another-project/iter01/06-glide/ref_1')))])
    archive=prepare_iteration_directory(deleted,1)
    assert archive.exists() and database.crem_generation(deleted.db_path,1) is None


def test_deleted_iteration_clears_nested_caches_but_retains_uncertain_launch(deleted):
    root=iteration_directory(deleted.output,1)
    folder=root/'06-glide/ref_1';folder.mkdir(parents=True)
    (folder/'glide_job.json').write_text('{"job_id":null}')
    (folder/'cached.maegz').write_text('uncertain scientific output')
    with pytest.raises(WorkspaceReconciliationPending,match='uncertain submission'):
        prepare_iteration_directory(deleted,1)
    assert (folder/'cached.maegz').read_text()=='uncertain scientific output'
    assert database.crem_generation(deleted.db_path,1) is not None


@pytest.mark.parametrize('job_line',['123|glide_03|RUNNING|/var/tmp/native\n',
                                    '124|custom-job|PENDING|/project/iter01/06-glide\n'])
def test_history_loss_checks_slurm_tasks_not_only_the_reinstalled_jobserver(monkeypatch,job_line):
    from types import SimpleNamespace
    from graft import amber_gbsa
    args=SimpleNamespace(use_slurm=True,amber_remote=dict(host='slurm'))
    monkeypatch.setattr(amber_gbsa,'ssh',lambda *a,**kw:subprocess.CompletedProcess([],0,job_line,''))
    with pytest.raises(RuntimeError,match='remains after native history loss'):
        _deleted_crem.scheduler_snapshot(args,Path('/project/iter01'))


def test_empty_slurm_snapshot_is_archived_as_reinstallation_evidence(monkeypatch):
    from types import SimpleNamespace
    from graft import amber_gbsa
    args=SimpleNamespace(use_slurm=True,amber_remote=dict(host='slurm'))
    commands=[]
    def ssh(settings,command,**kw):
        commands.append(command)
        return subprocess.CompletedProcess([],0,'','')
    monkeypatch.setattr(amber_gbsa,'ssh',ssh)
    proof=_deleted_crem.scheduler_snapshot(args,Path('/project/iter01'))
    assert proof['host']=='slurm' and proof['tasks']==[]
    assert 'squeue --me --noheader' in commands[0]
