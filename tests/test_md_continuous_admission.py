"""A blocked CPU transfer must not leave completed MD GPU slots idle."""
from contextlib import nullcontext
import json
import threading
from types import SimpleNamespace

import pytest

from graft import _core, database, md_gbsa, md_gbsa_local
from graft import md_gbsa_native as native, schrodinger_guard as guard


@pytest.mark.parametrize('local_compute', [False, True])
def test_refill_during_cpu_work_and_process_newly_ready_earlier_task(tmp_path, monkeypatch, local_compute):
    db = tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany('INSERT INTO compound(name,smiles,iteration,state,docking_score,prime_gbsa_score) '
                         "VALUES (?,'CC',1,'prime_gbsa_done',-10,?)", [('c1', -103), ('c2', -102), ('c3', -101)])
    settings = dict(protocol=dict(snapshots=5, seed=1), replicates=1, root='/frozen', gpus=1)
    if local_compute:
        settings['local_compute'] = dict(root=str(tmp_path/'share'))
        monkeypatch.setattr(md_gbsa_local, 'prepare', lambda *a: None)
        monkeypatch.setattr(md_gbsa_local, 'cleanup_committed', lambda *a: None)
    saved = database.freeze_md_gbsa_selection(db, 1, settings, count=3, target_count=3, prime_count=3)
    first, ready, queued = saved['ids']
    directory = tmp_path/'iter01/09-md_gbsa'
    directory.mkdir(parents=True)
    native.save(directory/'progress.json', {
        str((first, 0)): dict(phase='md', status='running'),
        str((ready, 0)): dict(phase='frames', status='pending'),
        str((queued, 0)): dict(phase='md', status='unstarted')})
    cpu_busy = threading.Event()
    refilled = threading.Event()
    events = []
    def cpu(cid):
        events.append(('prime', cid))
        if cid == ready:
            cpu_busy.set()
            assert refilled.wait(3), 'GPU refill waited for CPU processing to return'
        return dict(phase='prime', status='running')
    def remote(config, action, root):
        cid = int(root.split('CMPID_')[1].split('/')[0])
        events.append((action, cid))
        if action == 'md-status':
            if cid == first:
                state = dict(phase='frames', status='pending') if cpu_busy.is_set() else dict(phase='md', status='running')
            else:
                state = dict(phase='md', status='unstarted')
        elif action == 'md-start':
            assert cid == queued and cpu_busy.is_set()
            # The launch intent must already be durable before remote submission.
            assert json.loads((directory/'progress.json').read_text())[str((cid, 0))]['status'] == 'pending'
            refilled.set()
            state = dict(phase='md', status='running')
        else:
            assert action == 'poll'
            state = cpu(cid)
        return 'GRAFT_MD='+json.dumps(state)
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    monkeypatch.setattr(md_gbsa_local, 'poll', lambda settings, cid, *a, **kw: cpu(cid))
    monkeypatch.setattr(md_gbsa, '_MD_ADMISSION_INTERVAL', .01, raising=False)
    monkeypatch.setattr(md_gbsa, 'prepare', lambda *a: None)
    monkeypatch.setattr(guard, 'register_md_remote', lambda *a: None)
    monkeypatch.setattr(guard, 'active_jobs', lambda *a: [])
    monkeypatch.setattr(guard, 'submission_guard', lambda *a, **kw: nullcontext())
    md_gbsa.run(SimpleNamespace(db_path=db, output=tmp_path, schrodinger='/suite'), 1)
    assert [cid for action, cid in events if action == 'prime'] == [ready, first]
    assert [cid for action, cid in events if action == 'md-start'] == [queued]
    progress = json.loads((directory/'progress.json').read_text())
    assert progress[str((first, 0))]['phase'] == 'prime'
    assert progress[str((ready, 0))]['phase'] == 'prime'
    assert progress[str((queued, 0))]['status'] == 'running'
    assert not any(t.name == 'graft-md-admission' for t in threading.enumerate())
    assert database.completed_md_ids(db, 1) == set()


def test_background_lost_reply_retains_intent_and_propagates_on_exit(tmp_path, monkeypatch):
    progress = {'(1, 0)': dict(phase='md', status='unstarted'),
                '(2, 0)': dict(phase='md', status='unstarted')}
    path = tmp_path/'progress.json'
    failed = threading.Event()
    calls = []
    def remote(settings, action, root):
        calls.append((action, root))
        assert action == 'md-start'
        failed.set()
        raise RuntimeError('SSH lost reply')
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    monkeypatch.setattr(md_gbsa, '_MD_ADMISSION_INTERVAL', .001)
    with pytest.raises(RuntimeError, match='SSH lost reply'):
        with md_gbsa._continuous_md_admission(dict(gpus=1), [(1, 0, '/one'), (2, 0, '/two')], progress, path):
            assert failed.wait(2)
    assert calls == [('md-start', '/one')]
    assert json.loads(path.read_text())['(1, 0)']['status'] == 'pending'
    assert progress['(2, 0)']['status'] == 'unstarted'
    assert not any(t.name == 'graft-md-admission' for t in threading.enumerate())


def test_cpu_error_stops_and_joins_inflight_status_without_new_launch(tmp_path, monkeypatch):
    progress = {'(1, 0)': dict(phase='md', status='running'),
                '(2, 0)': dict(phase='md', status='unstarted')}
    querying = threading.Event()
    calls = []
    def remote(settings, action, root):
        calls.append((action, root))
        assert action == 'md-status'
        querying.set()
        # Ensure the main thread has raised and signalled monitor shutdown.
        assert stopped.wait(2)
        return 'GRAFT_MD='+json.dumps(dict(phase='frames', status='pending'))
    stopped = threading.Event()
    real_event = threading.Event
    # Capture shutdown without sleeping or timing a production native query.
    class StopEvent:
        def __init__(self):
            self.event = real_event()
        def wait(self, seconds):
            return self.event.wait(seconds)
        def is_set(self):
            return self.event.is_set()
        def set(self):
            self.event.set()
            stopped.set()
    # Patch only the module's threading namespace, not global threading.Event.
    monkeypatch.setattr(md_gbsa, 'threading', SimpleNamespace(Event=StopEvent, Lock=threading.Lock, Thread=threading.Thread))
    monkeypatch.setattr(md_gbsa, '_MD_ADMISSION_INTERVAL', .001)
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    with pytest.raises(ValueError, match='CPU failed'):
        with md_gbsa._continuous_md_admission(dict(gpus=1), [(1, 0, '/one'), (2, 0, '/two')], progress, tmp_path/'progress.json'):
            assert querying.wait(2)
            raise ValueError('CPU failed')
    assert calls == [('md-status', '/one')]
    assert progress['(2, 0)']['status'] == 'unstarted'
    assert not any(t.name == 'graft-md-admission' for t in threading.enumerate())


def test_progress_checkpoints_serialize_both_writers(tmp_path, monkeypatch):
    progress = {}
    path = tmp_path/'progress.json'
    lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()
    real_save = native.save
    writes = []
    def save(path, value):
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
        # Serial publication must preserve the other thread's key.
        writes.append(dict(value))
        real_save(path, value)
    monkeypatch.setattr(native, 'save', save)
    t = threading.Thread(target=md_gbsa._record_progress,
                         args=(progress, 'md', {'status': 'running'}, path, lock))
    t.start()
    assert entered.wait(2)
    second = threading.Thread(target=md_gbsa._record_progress,
                              args=(progress, 'prime', {'status': 'done'}, path, lock))
    second.start()
    release.set()
    t.join(2)
    second.join(2)
    assert not t.is_alive() and not second.is_alive()
    assert writes == [{'md': {'status': 'running'}},
                      {'md': {'status': 'running'}, 'prime': {'status': 'done'}}]
    assert json.loads(path.read_text()) == progress
