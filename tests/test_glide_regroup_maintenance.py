"""Explicit regrouping preserves science and every submitted/uncertain task."""
import json

import pytest

from graft import database, glide_recovery
from test_glide_shared import shared_project, project


def snapshot(path):
    with database.connect(path) as conn:
        return (conn.execute('SELECT * FROM compound ORDER BY id').fetchall(),
                conn.execute('SELECT * FROM glide_license_retry ORDER BY id').fetchall(),
                conn.execute('SELECT * FROM glide_shared_plan').fetchall())


def test_regroup_preserves_every_state_and_scientific_row(shared_project):
    args,root,_,_,_ = shared_project
    before = snapshot(args.db_path)
    result = database.regroup_unsubmitted_glide_tasks(args.db_path,1,root,2)
    assert result['retired_unsubmitted_tasks'] == 3
    assert result['new_tasks'] == 1
    after = snapshot(args.db_path)
    assert after[0] == before[0] and after[2] == before[2]
    rows = database.glide_license_retries(args.db_path,1)
    assert len(rows) == 1
    assert rows[0]['payload']['ids'] == [1,2,3]
    assert sorted(rows[0]['payload']['indices']) == [1,2,3,4]
    assert rows[0]['payload']['regrouped_from_task_ids'] == [r[0] for r in before[1]]
    assert rows[0]['id'] > max(r[0] for r in before[1])
    assert rows[0]['attempts'] == 0
    assert rows[0]['source_job_id'].startswith('regrouped:')
    assert database.regroup_unsubmitted_glide_tasks(args.db_path,1,root,2)['new_tasks'] == 0


def test_submitted_uncertain_and_scored_work_is_retained(shared_project):
    args,root,_,_,_ = shared_project
    tid = database.claim_glide_license_retry(args.db_path,1,0)
    task = database.glide_license_retries(args.db_path,1,task_id=tid)[0]
    directory = root/'license-retries'/f'task_{tid}'/'attempt_1'
    directory.mkdir(parents=True)
    glide_recovery.save_record(directory,dict(job_id=None,submitted_at=1))
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET docking_score=-5,state='docked' WHERE id=1")
    before = snapshot(args.db_path)
    result = database.regroup_unsubmitted_glide_tasks(args.db_path,1,root,2)
    assert result['retired_unsubmitted_tasks'] == 2
    assert database.glide_license_retries(args.db_path,1,task_id=tid)[0] == task
    assert glide_recovery.load_record(directory)['job_id'] is None
    assert snapshot(args.db_path)[0] == before[0]
    assert sorted(t['payload']['ids'] for t in database.glide_license_retries(args.db_path,1)) == [[1],[2,3]]


def test_different_references_are_never_combined(shared_project):
    args,root,_,_,plan = shared_project
    payload = dict(next(iter(plan['tasks'].values())),ids=[4],indices=[1])
    with database.connect(args.db_path) as conn:
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(20,'REF20','C',0,'reference')")
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) VALUES(4,'CMPID_4','CC',1,'glide_running',20)")
    database.freeze_glide_shared_plan(args.db_path,1,20,'other-reference',{'batch':payload},[])
    database.regroup_unsubmitted_glide_tasks(args.db_path,1,root,10)
    rows = database.glide_license_retries(args.db_path,1)
    assert sorted((t['reference_id'],t['payload']['ids']) for t in rows) == [(10,[1,2,3]),(20,[4])]


@pytest.mark.parametrize('problem',['artifact','replacement_artifact','runtime','scored','downstream','later','overlapping_ids','overlapping_states'])
def test_unsafe_regroup_is_atomic_and_refused(shared_project,problem):
    args,root,_,_,_ = shared_project
    tasks = database.glide_license_retries(args.db_path,1)
    tid = tasks[0]['id']
    with database.connect(args.db_path) as conn:
        if problem == 'artifact':
            (root/'license-retries'/f'task_{tid}').mkdir(parents=True)
        elif problem == 'replacement_artifact':
            replacement = max(t['id'] for t in tasks)+1
            (root/'license-retries'/f'task_{replacement}').mkdir(parents=True)
        elif problem == 'runtime':
            conn.execute('INSERT INTO glide_batch_runtime VALUES (?,1,1,10,1)',(tid,))
        elif problem in ('scored','downstream'):
            col = 'docking_score' if problem=='scored' else 'prime_gbsa_score'
            conn.execute(f'UPDATE compound SET {col}=-10 WHERE id=1')
        elif problem == 'later':
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(99,'later','CC',2,'generated')")
        else:
            payload = tasks[-1]['payload']
            payload['ids' if problem=='overlapping_ids' else 'indices'] = [1]
            conn.execute('UPDATE glide_license_retry SET payload=? WHERE id=?',(json.dumps(payload),tasks[-1]['id']))
    before = snapshot(args.db_path)
    with pytest.raises(RuntimeError):
        database.regroup_unsubmitted_glide_tasks(args.db_path,1,root,2)
    assert snapshot(args.db_path) == before
