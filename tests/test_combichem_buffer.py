"""Persistence/recovery regressions; artificial records, no receptor validation."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3

import pytest
from graft import database
from graft.config import combichem_3d_settings
from graft.stages import combichem_3d as stage
from graft.stages import combichem_buffer as buffering


@pytest.fixture
def project(tmp_path):
    script = Path(__file__).resolve().parents[1]/'examples/combichem-3d/make_toy_inputs.py'
    spec = importlib.util.spec_from_file_location('buffer_toy', script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    cfg = combichem_3d_settings(module.create(tmp_path/'toy', 0, 1))
    db, _ = stage.prepare_project(cfg)
    database.growth_start_session(db, cfg)
    job = database.growth_claim(db, 0, 16, 1)
    database.growth_save_input(db, job['parent_pose'], {}, True, {})
    job = database.growth_claim(db, 1, 16, 1)
    database.growth_save_plan(db, job['parent_pose'], dict(sites=['a'], pools=[['-H']], total=16), [])
    job = database.growth_claim(db, 1, 16, 1)
    return db, job, cfg


def report():
    return dict(status='CLASH_REJECT', iteration=1, reason='synthetic clash')


def test_sixteen_results_use_one_transaction_and_replay_is_idempotent(project, monkeypatch):
    db, job, _ = project
    original = database.connect
    transactions = []
    def connect(path):
        conn = original(path)
        conn.set_trace_callback(lambda sql: transactions.append(sql) if sql=='BEGIN IMMEDIATE' else None)
        return conn
    monkeypatch.setattr(database, 'connect', connect)
    rows = [(i, report(), None) for i in range(16)]
    database.growth_save_attempts(db, job, rows)
    assert transactions == ['BEGIN IMMEDIATE']
    database.growth_save_attempts(db, job, rows)
    assert database.growth_run_info(db)['attempted'] == 16
    assert database.growth_rows(db, 'combichem3d_batch')[0]['cursor'] == '16'


def test_failed_bulk_transaction_rolls_back_every_result_then_retries(project, monkeypatch):
    db, job, _ = project
    writes = []
    class Busy(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('UPDATE combichem3d_run SET attempted='):
                writes.append(1)
                if len(writes)==2:
                    exc = sqlite3.OperationalError('database is locked')
                    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
                    raise exc
            return super().execute(sql, parameters)
    monkeypatch.setattr(database, 'connect', lambda path: sqlite3.connect(path, factory=Busy))
    database.growth_save_attempts(db, job, [(i, report(), None) for i in range(3)])
    assert len(writes) == 5
    assert database.growth_run_info(db)['attempted'] == 3
    assert len(database.growth_rows(db, 'combichem3d_attempt')) == 3


def test_killed_worker_journal_recovers_without_recalculation(project):
    db, job, cfg = project
    def interrupted():
        buffer = buffering.ResultBuffer(db, job, cfg | {'result-buffer-seconds':1000})
        buffer.append(0, report(), None)
        buffer.append(1, report(), None)
        os._exit(1)
    process = multiprocessing.get_context('fork').Process(target=interrupted)
    process.start(); process.join(10)
    assert process.exitcode == 1
    assert database.growth_run_info(db)['attempted'] == 0
    buffering.recover(db)
    assert database.growth_run_info(db)['attempted'] == 2
    assert database.growth_rows(db, 'combichem3d_batch')[0]['cursor'] == '2'
    assert not list((Path(db).parent/'result-buffer').glob('*.jsonl'))


def test_recovery_after_commit_before_journal_removal_is_idempotent(project, monkeypatch):
    db, job, cfg = project
    buffer = buffering.ResultBuffer(db, job, cfg)
    buffer.append(0, report(), None)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', lambda self: (_ for _ in ()).throw(OSError('simulated kill after commit')))
        with pytest.raises(OSError):
            buffer.flush()
    assert buffer.path.exists()
    buffering.recover(db)
    assert database.growth_run_info(db)['attempted'] == 1


def test_only_incomplete_tail_is_recalculated(project):
    db, job, cfg = project
    buffer = buffering.ResultBuffer(db, job, cfg)
    buffer.append(0, report(), None)
    with buffer.path.open('ab') as stream:
        stream.write(b'{"ordinal":1')
    buffering.recover(db)
    assert database.growth_run_info(db)['attempted'] == 1
    assert database.growth_rows(db, 'combichem3d_batch')[0]['cursor'] == '1'


def test_invalid_complete_journal_is_retained_without_advancing_cursor(project):
    db, job, cfg = project
    buffer = buffering.ResultBuffer(db, job, cfg)
    buffer.path.parent.mkdir(exist_ok=True)
    buffer.path.write_text(json.dumps(dict(batch=job, ordinal=999, report=report(), payload=None))+'\n')
    with pytest.raises(ValueError, match='outside'):
        buffering.recover(db)
    assert buffer.path.exists()
    assert database.growth_run_info(db)['attempted'] == 0


def test_interval_and_exception_exit_flush_pending_results(project, monkeypatch):
    db, job, cfg = project
    clock = [0.]
    monkeypatch.setattr(buffering.time, 'monotonic', lambda: clock[0])
    with pytest.raises(OSError):
        with buffering.ResultBuffer(db, job, cfg) as buffer:
            buffer.append(0, report(), None)
            assert database.growth_run_info(db)['attempted'] == 0
            clock[0] = 3.
            buffer.append(1, report(), None)
            assert database.growth_run_info(db)['attempted'] == 2
            buffer.append(2, report(), None)
            raise OSError('transport failure')
    assert database.growth_run_info(db)['attempted'] == 3


def test_supervisor_recovers_complete_batch_before_claiming_work(project, monkeypatch):
    db, job, cfg = project
    buffer = buffering.ResultBuffer(db, job, cfg | {'result-buffer-size':256, 'result-buffer-seconds':1000})
    for ordinal in range(16):
        buffer.append(ordinal, report(), None)
    def unexpected_calculation(*args, **kwargs):
        raise AssertionError('Recovered results must not be calculated again')
    monkeypatch.setattr(stage, 'search', unexpected_calculation)
    with database.stage_lock(db, 'combichem3d'):
        stage.supervise(cfg, db, 0)
    assert database.growth_run_info(db)['attempted'] == 16
    assert database.growth_run_info(db)['stop_reason'] == 'exhausted'
    assert database.growth_rows(db, 'combichem3d_batch')[0]['state'] == 'done'
    assert not buffer.path.exists()


def test_bulk_target_admission_preserves_all_completed_poses(project):
    from rdkit import Chem
    from graft.chemistry.combichem3d.assembly import assemble
    from graft.chemistry.combichem3d import history
    db, job, cfg = project
    database.growth_start_session(db, cfg | {'stop-mode':'target', 'target-count':1})
    row = database.growth_parent(db, job['parent_pose'])
    mol = stage.restore_parent(row)
    records = []
    for ordinal, fragment in enumerate(['*C', '*CC', '*CCC']):
        product, birth = assemble(mol, row['history'], row['history']['attachments'], [fragment], f'b{ordinal}')
        payload = dict(smiles=history.canonical_smiles(product), molblock=Chem.MolToMolBlock(product),
                       history=birth, properties={}, output_passed=True, pruned=False)
        records.append((ordinal, dict(status='PASS', iteration=1, reason='artificial pose', output_passed=True), payload))
    database.growth_save_attempts(db, job, records)
    poses = [r for r in database.growth_rows(db, 'combichem3d_pose') if r['generated']]
    assert len(poses) == 3 and sum(r['output_passed'] for r in poses) == 1
    assert database.growth_run_info(db)['unique_count'] == 1
    assert database.growth_run_info(db)['attempted'] == 3


def test_idle_frame_wait_does_not_take_writer_lock(project):
    db, job, cfg = project
    with database.growth_connection(db) as conn:
        conn.execute("UPDATE combichem3d_batch SET state='done',cursor=stop")
        conn.execute("UPDATE combichem3d_pose SET plan_state='done'")
        conn.execute("INSERT INTO combichem3d_cycle VALUES (2,'running',NULL,'{}',123)")
    with database.growth_connection(db) as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert database.growth_claim(db,2,16,999)==dict(kind='wait')


def test_probe_updates_share_transaction_and_replay_preserves_history(project, monkeypatch):
    db, job, cfg = project
    parent = job['parent_pose']
    original = database.connect
    transactions = []
    def connect(path):
        conn = original(path)
        conn.set_trace_callback(lambda sql: transactions.append(sql) if sql=='BEGIN IMMEDIATE' else None)
        return conn
    monkeypatch.setattr(database, 'connect', connect)
    with buffering.ProbeBuffer(db, parent, cfg | {'result-buffer-size':64, 'result-buffer-seconds':1000}) as buffer:
        probe = dict(status='PASS', reason='synthetic probe')
        buffer.save('a', probe)
        probe['crem_candidates'] = [dict(fragment='*C')]
        buffer.save('a', probe)
        buffer.save('b', dict(status='CLASH_REJECT', reason='synthetic clash'))
        assert 'a' not in database.growth_parent(db, parent)['probes']
        assert transactions == []
        journal = buffer.path.read_bytes()
    assert transactions == ['BEGIN IMMEDIATE']
    saved = database.growth_parent(db, parent)['probes']
    assert saved['a']['crem_candidates'] == [dict(fragment='*C')]
    assert saved['a']['previous_reports'] == [dict(status='PASS', reason='synthetic probe')]
    buffer.path.write_bytes(journal)  # Killed after commit, before journal removal.
    buffering.recover(db)
    assert database.growth_parent(db, parent)['probes'] == saved
    assert not buffer.path.exists()


def test_killed_probe_worker_recovers_before_claims_without_marking_plan_ready(project, monkeypatch):
    db, job, cfg = project
    parent = job['parent_pose']
    before = database.growth_rows(db, 'combichem3d_pose')[0]['plan_state']
    def interrupted():
        buffer = buffering.ProbeBuffer(db, parent, cfg | {'result-buffer-seconds':1000})
        buffer.save('a', dict(status='PASS', reason='completed probe'))
        os._exit(1)
    process = multiprocessing.get_context('fork').Process(target=interrupted)
    process.start(); process.join(10)
    assert process.exitcode == 1
    assert 'a' not in database.growth_parent(db, parent)['probes']
    original = database.growth_start_session
    def start(db, cfg):
        assert database.growth_parent(db, parent)['probes']['a']['status']=='PASS'
        return original(db, cfg)
    monkeypatch.setattr(database, 'growth_start_session', start)
    with database.stage_lock(db, 'combichem3d'):
        stage.supervise(cfg | {'max-iteration':0}, db, 1)
    assert database.growth_rows(db, 'combichem3d_pose')[0]['plan_state'] == before
    assert not list((Path(db).parent/'result-buffer').glob('*.jsonl'))


def test_probe_buffer_threshold_interval_and_early_exit(project, monkeypatch):
    db, job, cfg = project
    clock = [0.]
    monkeypatch.setattr(buffering.time, 'monotonic', lambda: clock[0])
    with pytest.raises(RuntimeError, match='early stop'):
        with buffering.ProbeBuffer(db, job['parent_pose'], cfg | {'result-buffer-size':2}) as buffer:
            buffer.save('a', dict(status='PASS'))
            assert 'a' not in database.growth_parent(db, job['parent_pose'])['probes']
            buffer.save('b', dict(status='UNRESOLVED'))
            assert not buffer.path.exists()
            clock[0] = 3.
            buffer.save('c', dict(status='CLASH_REJECT'))
            assert not buffer.path.exists()
            buffer.save('d', dict(status='PASS', budget_interrupted=True))
            raise RuntimeError('early stop')
    probes = database.growth_parent(db, job['parent_pose'])['probes']
    assert all(uid in probes for uid in 'abcd')
    assert probes['d']['budget_interrupted']


def test_probe_commit_failure_keeps_journal_for_recovery(project, monkeypatch):
    db, job, cfg = project
    buffer = buffering.ProbeBuffer(db, job['parent_pose'], cfg)
    buffer.save('a', dict(status='PASS'))
    with monkeypatch.context() as patch:
        def fail(*args):
            raise sqlite3.OperationalError('synthetic commit failure')
        patch.setattr(database, 'growth_save_probes', fail)
        with pytest.raises(sqlite3.OperationalError):
            buffer.flush()
    assert buffer.path.exists()
    assert 'a' not in database.growth_parent(db, job['parent_pose'])['probes']
    buffering.recover(db)
    assert database.growth_parent(db, job['parent_pose'])['probes']['a']['status']=='PASS'


def test_probe_bulk_busy_rolls_back_and_retries_all_updates(project, monkeypatch):
    db, job, cfg = project
    writes = []
    class Busy(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('INSERT OR REPLACE INTO combichem3d_probe'):
                writes.append(1)
                if len(writes)==2:
                    exc = sqlite3.OperationalError('database is locked')
                    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
                    raise exc
            return super().execute(sql, parameters)
    monkeypatch.setattr(database, 'connect', lambda path: sqlite3.connect(path, factory=Busy))
    database.growth_save_probes(db, job['parent_pose'], [('a', dict(status='PASS')), ('b', dict(status='UNRESOLVED'))])
    assert len(writes)==4
    probes = database.growth_parent(db, job['parent_pose'])['probes']
    assert probes['a']==dict(status='PASS') and probes['b']==dict(status='UNRESOLVED')


def test_probe_incomplete_tail_recovers_only_complete_records(project):
    db, job, cfg = project
    buffer = buffering.ProbeBuffer(db, job['parent_pose'], cfg)
    buffer.save('a', dict(status='PASS'))
    with buffer.path.open('ab') as stream:
        stream.write(b'{"site":"b"')
    buffering.recover(db)
    probes = database.growth_parent(db, job['parent_pose'])['probes']
    assert 'a' in probes and 'b' not in probes


def test_mismatched_probe_journal_is_retained(project):
    db, job, cfg = project
    buffer = buffering.ProbeBuffer(db, job['parent_pose'], cfg)
    buffer.save('a', dict(status='PASS'))
    with buffer.path.open('a') as stream:
        stream.write(json.dumps(dict(parent_pose=999, site='b', report=dict(status='PASS')))+'\n')
    with pytest.raises(ValueError, match='Inconsistent probe journal'):
        buffering.recover(db)
    assert buffer.path.exists()
    assert 'a' not in database.growth_parent(db, job['parent_pose'])['probes']
