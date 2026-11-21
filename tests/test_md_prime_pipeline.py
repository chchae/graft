"""Native batching and worker I/O scheduling; fake energies do not validate science."""
from contextlib import nullcontext
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from graft import _core, database, md_gbsa_local as local
from graft.md_gbsa_native import save
from graft.stages import md_gbsa_pipeline as pipeline


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, '_POLL_SECONDS', 0.)
    def create(count, *, slow_failure=False, running_first=False):
        db = tmp_path/'project.sqlite'
        with database.connect(db) as conn:
            _core.create_sqlite_schema(conn)
            conn.executemany("INSERT INTO compound(name,smiles,iteration,state,prime_gbsa_score) "
                             "VALUES (?,'CC',1,'prime_gbsa_done',-50)",
                             [(f'c{i}',) for i in range(count)])
        settings = dict(root='/md', suite='/suite', protocol=dict(snapshots=5, seed=1), replicates=1,
                        local_compute=dict(root=str(tmp_path/'share'), slots=192))
        saved = database.freeze_md_gbsa_selection(db, 1, settings, count=count,
                                                  target_count=count, prime_count=count)
        tasks = [(cid, 0, f'/md/CMPID_{cid}/replica01') for cid in saved['ids']]
        progress = {str((cid, r)): dict(phase='frames', status='pending') for cid, r, _ in tasks}
        directory = tmp_path/'iter01/09-md_gbsa'
        directory.mkdir(parents=True)
        state = dict(batches=[], prepared_threads=set(), collected_threads=set(),
                     committed=[], started=threading.Event(), guard_queries=0, polls=[])
        owner = threading.get_ident()
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            save(path, value)
        def prepare(config, cid, replica, root):
            state['prepared_threads'].add(threading.get_ident())
            if slow_failure and cid == count:
                assert state['started'].wait(5), 'full batch waited for an unrelated slow frame'
                raise RuntimeError('later frame transfer failed')
            write(local.task_root(config, cid, replica)/'input.json', dict(id=cid))
            return dict(phase='prime', status='pending', frames_prepared=True)
        def result(cid):
            return dict(status='done', id=cid, protocol=settings['protocol'], mean=-50., std=0.,
                        snapshots=[dict(frame=i, score=-50., ligand_rmsd=.2) for i in range(5)])
        def poll(config, cid, replica, root, archive, **kw):
            assert threading.get_ident() == owner
            assert kw['prepared'] and not kw['collect']
            state['polls'].append(kw)
            current = local.task_root(config, cid, replica)
            if (current/'prime/pool.json').exists():
                assert kw.get('existing'), 'identified parent used the submission path'
                batch = json.loads((current/'prime/pool.json').read_text())['batch']
                for p in Path(config['local_compute']['root']).glob('CMPID_*/replica*/prime/pool.json'):
                    if json.loads(p.read_text())['batch'] == batch:
                        identity = json.loads((p.parent.parent/'input.json').read_text())['id']
                        write(p.parent.parent/'result.json', result(identity))
                return result(cid)
            members = [p.parent for p in Path(config['local_compute']['root']).glob('CMPID_*/replica*/input.json')
                       if not (p.parent/'prime/pool.json').exists()]
            batch = f'batch-{len(state["batches"]):032x}'
            state['batches'].append([json.loads((p/'input.json').read_text())['id'] for p in members])
            journal = dict(job_id='saved-'+batch)
            write(Path(config['local_compute']['root'])/'pools'/batch/'prime/prime.job.json', journal)
            for p in members:
                identity = json.loads((p/'input.json').read_text())['id']
                write(p/'prime/pool.json', dict(batch=batch))
                write(p/'prime/prime.job.json', journal)
                if not running_first:
                    write(p/'result.json', result(identity))
            state['started'].set()
            if running_first:
                return dict(phase='prime', status='running')
            return result(cid)
        def collect(config, cid, replica, archive, native_result=None):
            state['collected_threads'].add(threading.get_ident())
            return native_result or json.loads((local.task_root(config, cid, replica)/'result.json').read_text())
        original_store = database.store_md_gbsa_result
        def store(*args):
            assert threading.get_ident() == owner, 'SQLite escaped the owner thread'
            original_store(*args)
            state['committed'].append(args[2])
        monkeypatch.setattr(local, 'prepare_frames', prepare)
        monkeypatch.setattr(local, 'poll', poll)
        monkeypatch.setattr(local, 'collect_result', collect)
        monkeypatch.setattr(database, 'store_md_gbsa_result', store)
        monkeypatch.setattr(pipeline.guard, 'submission_guard', lambda *a, **kw: nullcontext())
        def active_jobs(*a):
            state['guard_queries'] += 1
            return []
        monkeypatch.setattr(pipeline.guard, 'active_jobs', active_jobs)
        args = SimpleNamespace(db_path=db, schrodinger='/suite')
        def run():
            pipeline.run(args, 1, settings, tasks, progress, directory/'progress.json',
                         directory, set(), threading.Lock(), [])
        return run, state, db, settings, progress
    return create


def test_full_192_slot_wave_and_immediate_owner_commits(project):
    run, state, db, _, _ = project(40)
    run()
    assert len(state['batches'][0])*5 >= 192
    assert sorted(cid for batch in state['batches'] for cid in batch) == list(range(1, 41))
    assert len(state['committed']) == 40
    assert database.completed_md_ids(db, 1) == set(range(1, 41))
    assert threading.get_ident() not in state['prepared_threads'] | state['collected_threads']
    assert not any(t.name.startswith(('graft-md-frames', 'graft-md-collect')) for t in threading.enumerate())


def test_small_final_wave_does_not_wait_for_192_snapshots(project):
    run, state, db, _, _ = project(3)
    run()
    assert len(state['batches']) == 1 and len(state['batches'][0]) == 3
    assert database.completed_md_ids(db, 1) == {1, 2, 3}


def test_identified_batch_poll_avoids_global_admission_queries(project):
    run, state, db, _, _ = project(3, running_first=True)
    run()
    assert state['guard_queries'] == 1
    assert len(state['polls']) == 2
    assert state['polls'][1]['existing'] and not state['polls'][1]['allow_submit']
    assert database.completed_md_ids(db, 1) == {1, 2, 3}


def test_full_wave_and_results_do_not_wait_for_later_slow_failed_frame(project):
    run, state, db, _, _ = project(40, slow_failure=True)
    with pytest.raises(RuntimeError, match='later frame transfer failed'):
        run()
    assert len(state['batches']) == 1
    assert len(state['batches'][0]) == 39
    assert database.completed_md_ids(db, 1) == set(range(1, 40))
    assert not any(t.name.startswith(('graft-md-frames', 'graft-md-collect')) for t in threading.enumerate())


def test_preparation_cannot_import_cached_ensemble_as_complete(project, monkeypatch):
    run, _, db, _, _ = project(1)
    monkeypatch.setattr(local, 'prepare_frames', lambda *a: dict(status='done', id=1))
    with pytest.raises(RuntimeError, match='cannot establish ensemble completion'):
        run()
    assert not database.completed_md_ids(db, 1)
