"""Real temporary-file/SQLite cleanup; native status is mocked, no scientific jobs."""
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import shlex
from types import SimpleNamespace
import tarfile

import pytest

from graft import _core, amber_gbsa, database, md_gbsa, remote_cleanup
from graft import remote_cleanup_native as native
from graft.states import CompoundState as State


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def amber_run(tmp_path):
    base = tmp_path / 'amber'
    root = base / ('run-' + 'a' * 32)
    config = dict(entries=[dict(id=2), dict(id=1)])
    write(root / 'input.json', config)
    write(root / 'status.json', dict(status='done'))
    for cid in (1, 2):
        write(root / f'results/CMPID_{cid}.json', dict(id=cid, score=-cid))
        write(root / f'CMPID_{cid}/scratch.nc', {})
    request = dict(kind='amber', base=str(base), run_id=root.name, ids=[2, 1],
                   mode='ssh', job_id=None, input_sha256=native.digest(root / 'input.json'),
                   successful={'1': -1, '2': -2})
    return root, request


def test_amber_deletes_only_owned_completed_run_and_recovers_lost_reply(amber_run):
    root, request = amber_run
    neighbor = root.parent / ('run-' + 'b' * 32)
    write(neighbor / 'active.json', {})
    assert native.amber(request)['status'] == 'done'
    assert not root.exists() and neighbor.exists()
    assert native.amber(request)['status'] == 'done'


def test_amber_live_lock_and_changed_input_or_score_block_deletion(amber_run):
    root, request = amber_run
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            native.amber(request)
    changed = copy.deepcopy(request)
    changed['successful']['1'] = -99
    with pytest.raises(RuntimeError, match='score differs'):
        native.amber(changed)
    (root / 'input.json').write_text('{}')
    with pytest.raises(RuntimeError, match='ownership differs'):
        native.amber(request)
    assert (root / 'CMPID_1/scratch.nc').exists()


def test_amber_partial_cleanup_preserves_failure_and_retry_markers(amber_run):
    root, request = amber_run
    request['successful'] = {'1': -1}
    native.amber(request)
    assert root.exists() and not (root / 'CMPID_1').exists()
    assert (root / 'CMPID_2/scratch.nc').exists()
    assert (root / 'results/CMPID_1.json').exists()


def test_amber_resume_interrupted_tombstone_deletion(amber_run, monkeypatch):
    root, request = amber_run
    original = native.shutil.rmtree
    monkeypatch.setattr(native.shutil, 'rmtree', lambda *a: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OSError):
        native.amber(request)
    assert not root.exists() and root.with_name('.collected-' + root.name).exists()
    monkeypatch.setattr(native.shutil, 'rmtree', original)
    native.amber(request)
    assert not root.with_name('.collected-' + root.name).exists()


def test_amber_symlink_target_is_never_deleted(amber_run, tmp_path):
    root, request = amber_run
    elsewhere = tmp_path / 'foreign'
    root.rename(elsewhere)
    root.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        native.amber(request)
    assert (elsewhere / 'CMPID_1/scratch.nc').exists()


def array_request(root, request):
    request.update(mode='slurm_array_v1', job_id='123', successful={'1': -1})
    write(root / 'slurm-submission.json', dict(job_id='123', launching=False))
    for index, cid in enumerate(request['ids']):
        task = root / 'tasks' / str(index)
        write(task / 'input.json', dict(entries=[dict(id=cid)]))
        write(task / 'status.json', dict(status='done'))
        write(task / f'results/CMPID_{cid}.json', dict(id=cid, score=-cid))
        write(task / f'CMPID_{cid}/trajectory.nc', {})
    control = root / 'amber_slurm_control.py'
    control.write_text('def status(*args):\n    return {"active": False}\n')
    return control


def test_amber_array_partial_cleanup_keeps_snapshot_and_failed_work(amber_run):
    from graft import amber_array
    root, request = amber_run
    array_request(root, request)
    native.amber(request)
    assert (root / 'tasks/0/CMPID_2/trajectory.nc').exists()
    assert not (root / 'tasks/1/CMPID_1').exists()
    snapshot = amber_array.snapshot(root)
    assert not snapshot['active'] and snapshot['status'] == 'done'
    assert {r['id'] for r in snapshot['results']} == {1, 2}
    native.amber(request)  # Interrupted/repeated pruning is harmless.


def test_amber_array_native_activity_or_uncertain_history_blocks_cleanup(amber_run):
    root, request = amber_run
    control = array_request(root, request)
    control.write_text('def status(*args):\n    return {"active": True}\n')
    with pytest.raises(RuntimeError, match='active'):
        native.amber(request)
    control.write_text('def status(*args):\n    raise RuntimeError("missing history")\n')
    with pytest.raises(RuntimeError, match='missing history'):
        native.amber(request)
    assert (root / 'tasks/1/CMPID_1/trajectory.nc').exists()


@pytest.fixture
def md_run(tmp_path, monkeypatch):
    base = tmp_path / 'md' / ('a' * 32)
    root = base / 'CMPID_1'
    journal = dict(job_id='md-parent', downloaded=True, command=['/suite/utilities/multisim'])
    settings = dict(root=str(base), suite='/suite', protocol=dict(snapshots=1), replicates=1,
                    ssh_host='slurm')
    selection = dict(ids=[1], settings=settings)
    write(base / 'ready.json', selection)
    write(root / 'replica01/md/md.job.json', journal)
    write(root / 'replica01/input.json', dict(protocol=settings['protocol']))
    (root / 'replica01/md/md.log').write_text('MD completed\n')
    (root / 'replica01/md/md-out.cms').write_text('final system coordinates')
    write(root / 'replica01/md/md_trj/frame.dtr', {})
    write(root / 'replica01/md/md.cpt', {})
    jobs = [dict(jobId='md-parent', status='DONE'), dict(jobId='child', status='DONE')]
    monkeypatch.setattr(native.subprocess, 'check_output', lambda *a, **kw: '\n'.join(map(json.dumps, jobs)))
    request = dict(kind='md', action='archive', base=str(base), id=1,
                   native=[dict(md=journal)], selection=selection)
    return root, request, jobs


def test_md_archives_evidence_before_deleting_trajectory_and_recovers_reply(md_run, tmp_path):
    root, request, _ = md_run
    response = native.md(request)
    assert (root / 'replica01/md/md_trj/frame.dtr').exists()
    archive = tmp_path / 'collected.tar'
    shutil.copy2(root / 'cleanup-evidence.tar', archive)
    with tarfile.open(archive) as stream:
        names = stream.getnames()
    assert 'replica01/md/md.job.json' in names
    assert 'replica01/md/md-out.cms' in names
    assert not any('md_trj' in name or name.endswith('.cpt') for name in names)
    request.update(action='delete', archive_sha256=response['sha256'])
    assert native.md(request)['status'] == 'done'
    assert not root.exists() and archive.exists()
    assert native.md(request)['status'] == 'done'


@pytest.mark.parametrize('status', ['RUNNING', 'FAILED', 'UNKNOWN'])
def test_md_active_failed_or_uncertain_child_preserves_trajectory(md_run, status):
    root, request, jobs = md_run
    jobs[1]['status'] = status
    with pytest.raises(RuntimeError, match='active, failed or uncertain'):
        native.md(request)
    assert (root / 'replica01/md/md_trj/frame.dtr').exists()


def test_md_changed_owner_or_archive_blocks_deletion(md_run):
    root, request, _ = md_run
    response = native.md(request)
    changed = copy.deepcopy(request)
    changed['selection']['settings']['suite'] = '/other'
    with pytest.raises(RuntimeError, match='ownership differs'):
        native.md(changed)
    request.update(action='delete', archive_sha256=response['sha256'])
    (root / 'cleanup-evidence.tar').write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='not collected'):
        native.md(request)
    assert root.exists()


def test_md_local_worker_lock_preserves_trajectory(md_run):
    root, request, _ = md_run
    with (root / 'replica01/controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            native.md(request)
    assert root.exists()


def md_database(tmp_path, settings):
    path = tmp_path / 'project.sqlite'
    with database.connect(path) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute('INSERT INTO compound(name,smiles,iteration,state,docking_score,prime_gbsa_score) '
                     'VALUES (?,?,1,?,-10,-20)', ('CMPID_1', 'C', State.PRIME_GBSA_DONE))
    database.freeze_md_gbsa_selection(path, 1, settings, count=1, target_count=1, prime_count=1)
    return SimpleNamespace(db_path=path)


def test_md_commit_cleanup_gate_and_recovery_after_lost_delete_reply(md_run, tmp_path, monkeypatch):
    root, request, _ = md_run
    settings = request['selection']['settings']
    args = md_database(tmp_path, settings)
    directory = tmp_path / 'output'
    data = dict(id=1, mean=-50., std=0., snapshots=[dict(frame=0, score=-50.)],
                native=request['native'])
    calls = []
    lost = [True]
    def ssh(host, command, *, input):
        value = json.loads(input)
        calls.append(value['action'])
        result = native.md(value)
        if value['action'] == 'delete' and lost[0]:
            lost[0] = False
            raise RuntimeError('lost reply')
        return json.dumps(result)
    monkeypatch.setattr(md_gbsa, 'ssh', ssh)
    def download(command, **kwargs):
        assert command[-1].startswith('cat -- ')
        kwargs['stdout'].write((root / 'cleanup-evidence.tar').read_bytes())
    monkeypatch.setattr(remote_cleanup.subprocess, 'run', download)
    remote_cleanup.md(args, 1, settings, directory)
    assert calls == [] and root.exists()  # No score -> no destructive request.
    database.store_md_gbsa_result(args.db_path, 1, 1, data)
    assert 'cleanup pending' in database.md_gbsa_completion_reason(args.db_path, 1)
    with pytest.raises(RuntimeError, match='lost reply'):
        remote_cleanup.md(args, 1, settings, directory)
    assert not root.exists() and database.pending_md_remote_cleanup_ids(args.db_path, 1) == {1}
    remote_cleanup.md(args, 1, settings, directory)
    assert database.md_gbsa_completion_reason(args.db_path, 1) is None
    assert calls == ['archive', 'delete', 'delete']
    assert database.md_cleanup_result(args.db_path, 1, 1) == data


def test_amber_postcommit_intent_recovery_does_not_repoll_deleted_run(amber_run, tmp_path, monkeypatch):
    root, request = amber_run
    db = tmp_path / 'amber.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany('INSERT INTO compound(id,name,smiles,iteration,state,amber_gbsa_score) '
                         'VALUES (?,?,?,1,?,?)', [(i, f'CMPID_{i}', 'C', State.DOCKED, -i) for i in (1, 2)])
    args = SimpleNamespace(db_path=db)
    directory = tmp_path / 'stage'
    folder = directory / root.name
    folder.mkdir(parents=True)
    shutil.copy2(root / 'input.json', folder / 'input.json')
    write(directory / 'remote_snapshot.json', dict(results=[dict(id=i, score=-i) for i in (1, 2)]))
    record = dict(run_id=root.name, compound_ids=[1, 2], settings=dict(remote_work_dir=str(root.parent)),
                  remote_dir=str(root), iteration=1, db_path=str(db.resolve()))
    write(directory / 'amber_job.json', record)
    def ssh(settings, command, *, input, **kwargs):
        result = native.amber(json.loads(input))
        if root.exists():
            pytest.fail('Expected successful run removal')
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(amber_gbsa, 'ssh', ssh)
    remote_cleanup.amber(args, 1, directory, record)
    assert not root.exists()
    assert amber_gbsa.poll(args, 1, directory, record)
    assert (directory / (root.name + '.completed.json')).exists()
    assert not (directory / 'amber_job.json').exists()


def collected_md_run(md_run):
    root, request, jobs = md_run
    response = native.md(request)
    request.update(action='delete', archive_sha256=response['sha256'])
    native.md(request)
    base = root.parent
    (base / 'native.py').write_text('# frozen scientific helper\n')
    (base / 'prime_final.maegz').write_bytes(b'original input')
    (base / 'control').mkdir()
    (base / 'control/adapter.py').write_text('# operational helper\n')
    return base, dict(kind='md_run', action='archive', base=str(base),
                      selection=request['selection'], receipts={'1': hashlib.sha256(
                          json.dumps(request, sort_keys=True).encode()).hexdigest()}), jobs


def test_completed_md_run_removes_helpers_after_archiving_and_recovers_lost_reply(md_run, tmp_path):
    root, request, _ = collected_md_run(md_run)
    neighbor = root.with_name('b' * 32)
    write(neighbor / 'ready.json', {})
    response = native.md_run(request)
    archive = tmp_path / 'run-evidence.tar'
    shutil.copy2(root / 'cleanup-run-evidence.tar', archive)
    with tarfile.open(archive) as stream:
        assert {'ready.json', 'native.py', 'prime_final.maegz', 'control/adapter.py'} <= set(stream.getnames())
    request.update(action='delete', archive_sha256=response['sha256'])
    assert native.md_run(request)['status'] == 'done'
    assert not root.exists() and neighbor.exists() and archive.exists()
    assert native.md_run(request)['status'] == 'done'


@pytest.mark.parametrize('problem', ['remaining_compound', 'unknown_file', 'symlink', 'receipt', 'live_child'])
def test_md_run_retains_uncollected_foreign_or_active_work(md_run, tmp_path, problem):
    root, request, jobs = collected_md_run(md_run)
    if problem == 'remaining_compound':
        write(root / 'CMPID_1/failed.json', {})
    elif problem == 'unknown_file':
        (root / 'unidentified-job.log').touch()
    elif problem == 'symlink':
        (root / 'control/neighbor').symlink_to(tmp_path)
    elif problem == 'receipt':
        request['receipts']['1'] = 'changed'
    else:
        jobs[1]['status'] = 'RUNNING'
    with pytest.raises(RuntimeError):
        native.md_run(request)
    assert root.exists()


def test_md_run_recovers_interrupted_tombstone_removal(md_run, monkeypatch):
    root, request, _ = collected_md_run(md_run)
    response = native.md_run(request)
    request.update(action='delete', archive_sha256=response['sha256'])
    original = native.shutil.rmtree
    monkeypatch.setattr(native.shutil, 'rmtree', lambda *a: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OSError, match='interrupted'):
        native.md_run(request)
    assert root.with_name('.collected-' + root.name).exists()
    monkeypatch.setattr(native.shutil, 'rmtree', original)
    native.md_run(request)
    assert not root.with_name('.collected-' + root.name).exists()


def test_md_run_commit_gate_and_lost_delete_reply_recovery(md_run, tmp_path, monkeypatch):
    root, compound_request, _ = md_run
    settings = compound_request['selection']['settings']
    args = md_database(tmp_path, settings)
    directory = tmp_path / 'output'
    data = dict(id=1, mean=-50., std=0., snapshots=[dict(frame=0, score=-50.)],
                native=compound_request['native'])
    calls = []
    lost = [True]

    def ssh(host, command, *, input):
        request = json.loads(input)
        calls.append((request['kind'], request['action']))
        result = {'md': native.md, 'md_run': native.md_run}[request['kind']](request)
        if request['kind'] == 'md_run' and request['action'] == 'delete' and lost[0]:
            lost[0] = False
            raise RuntimeError('lost reply')
        return json.dumps(result)

    def download(command, **kwargs):
        kwargs['stdout'].write(Path(shlex.split(command[-1])[-1]).read_bytes())

    monkeypatch.setattr(md_gbsa, 'ssh', ssh)
    monkeypatch.setattr(remote_cleanup.subprocess, 'run', download)
    remote_cleanup.md_run(args, 1, settings, directory)
    assert not calls  # No score or compound cleanup -> no remote deletion request.
    database.store_md_gbsa_result(args.db_path, 1, 1, data)
    assert not database.md_run_cleanup_ready(args.db_path, 1)
    remote_cleanup.md(args, 1, settings, directory)
    assert database.md_run_cleanup_ready(args.db_path, 1)
    with pytest.raises(RuntimeError, match='lost reply'):
        remote_cleanup.md_run(args, 1, settings, directory)
    assert not root.parent.exists()
    assert 'run directory cleanup pending' in database.md_gbsa_completion_reason(args.db_path, 1)
    remote_cleanup.md_run(args, 1, settings, directory)
    assert database.md_gbsa_completion_reason(args.db_path, 1) is None
    assert database.md_run_cleanup_record(args.db_path, 1)['completed']
    remote_cleanup.md_run(args, 1, settings, directory)
    assert calls.count(('md_run', 'delete')) == 2


def test_historical_scores_without_cleanup_receipts_do_not_enroll_old_run(md_run, tmp_path):
    _, request, _ = md_run
    args = md_database(tmp_path, request['selection']['settings'])
    data = dict(id=1, mean=-50., std=0., snapshots=[dict(frame=0, score=-50.)])
    database.store_md_gbsa_result(args.db_path, 1, 1, data)
    assert not database.md_run_cleanup_ready(args.db_path, 1)
    assert database.md_run_cleanup_record(args.db_path, 1) is None


def test_md_run_changed_file_after_archive_blocks_deletion(md_run):
    root, request, _ = collected_md_run(md_run)
    response = native.md_run(request)
    request.update(action='delete', archive_sha256=response['sha256'])
    (root / 'native.py').write_text('# changed after collection')
    with pytest.raises(RuntimeError, match='changed after archival'):
        native.md_run(request)
    assert root.exists()


def test_md_run_database_gate_rejects_partial_or_uncollected_results(md_run, tmp_path):
    _, request, _ = md_run
    args = md_database(tmp_path, request['selection']['settings'])
    final = dict(kind='md_run', action='archive', base=request['base'],
                 selection=request['selection'], receipts={})
    with pytest.raises(RuntimeError, match='committed and collected'):
        database.begin_md_run_cleanup(args.db_path, 1, final)
    assert database.md_run_cleanup_record(args.db_path, 1) is None
