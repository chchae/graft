"""Explicit stopped-only recovery never resets scientific results or live claims."""
import copy

import pytest

from graft import database, glide_recovery
from graft.stages import glide_shared as shared
from test_glide_shared import shared_project, project


def stopped(shared_project):
    args, root, _, _, _ = shared_project
    shared.service_pass(args, 1, root, 'merger', lambda *a: None)
    task = database.glide_license_retries(args.db_path, 1, state='running')[0]
    folder = shared.attempt_directory(root, task)
    record = glide_recovery.load_record(folder)
    return args, task, dict(task_id=task['id'], attempt=task['attempts'], directory=str(folder),
        job_id=record['job_id'], native=[dict(jobId=record['job_id'], status='STOPPED')])


def test_explicit_stopped_requeue_preserves_results_payload_and_attempt(shared_project):
    args, task, evidence = stopped(shared_project)
    with database.connect(args.db_path) as conn:
        compounds = conn.execute('SELECT * FROM compound ORDER BY id').fetchall()
    original = glide_recovery.load_record(evidence['directory'])
    assert database.requeue_stopped_glide_tasks(args.db_path, 1, [evidence]) == 1
    updated = database.glide_license_retries(args.db_path, 1, task_id=task['id'])[0]
    assert updated['state'] == 'pending'
    assert updated['payload'] == task['payload']
    assert updated['attempts'] == task['attempts']
    assert updated['kind'] == task['kind']
    assert glide_recovery.load_record(evidence['directory']) == original
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT * FROM compound ORDER BY id').fetchall() == compounds
    assert database.claim_glide_license_retry(args.db_path, 1, 0, max_running=3) != task['id']
    assert database.claim_glide_license_retry(args.db_path, 1, 0, max_running=3) == task['id']
    assert database.glide_license_retries(args.db_path, 1, task_id=task['id'])[0]['attempts'] == 2


@pytest.mark.parametrize('problem', ['active', 'unknown', 'child_active', 'identity', 'claim', 'scored', 'downstream', 'timeout', 'duplicate'])
def test_stopped_maintenance_refuses_unsafe_or_changed_work(shared_project, problem):
    args, task, evidence = stopped(shared_project)
    if problem in ('active', 'unknown'):
        evidence['native'][0]['status'] = 'RUNNING' if problem == 'active' else 'UNKNOWN'
    elif problem == 'child_active':
        evidence['native'].append(dict(jobId='child', status='RUNNING'))
    elif problem == 'identity':
        evidence['job_id'] = 'foreign-job'
    elif problem == 'claim':
        evidence['attempt'] += 1
    elif problem in ('scored', 'downstream'):
        column = 'docking_score' if problem == 'scored' else 'prime_gbsa_score'
        with database.connect(args.db_path) as conn:
            conn.execute(f'UPDATE compound SET {column}=-10 WHERE id=?', (task['payload']['ids'][0],))
    elif problem == 'timeout':
        record = glide_recovery.load_record(evidence['directory'])
        record['timeout_requested_at'] = 1
        glide_recovery.save_record(evidence['directory'], record)
    entries = [evidence, copy.deepcopy(evidence)] if problem == 'duplicate' else [evidence]
    with pytest.raises(RuntimeError):
        database.requeue_stopped_glide_tasks(args.db_path, 1, entries)
    assert database.glide_license_retries(args.db_path, 1, task_id=task['id'])[0]['state'] == 'running'
