"""SQLite connection ownership, explicit commits and legacy patch boundaries."""
from contextlib import closing
import sqlite3

import pytest

from graft import _core, database


def test_native_connection_context_commits_and_rolls_back_without_closing(tmp_path):
    conn=_core.open_sqlite(tmp_path/'ctx.sqlite')
    try:
        assert type(conn) is sqlite3.Connection
        assert conn.execute('PRAGMA journal_mode').fetchone()==('wal',)
        assert conn.execute('PRAGMA busy_timeout').fetchone()==(30000,)
        assert conn.execute('PRAGMA foreign_keys').fetchone()==(1,)
        conn.execute('CREATE TABLE sample(value)')
        with conn:conn.execute('INSERT INTO sample VALUES (1)')
        with pytest.raises(RuntimeError):
            with conn:
                conn.execute('INSERT INTO sample VALUES (2)')
                raise RuntimeError('rollback')
        assert conn.execute('SELECT * FROM sample').fetchall()==[(1,)]
    finally:conn.close()


def test_legacy_connection_and_cursor_contexts_translate_and_close(tmp_path):
    wrapper=_core.db_connect(tmp_path/'adapter.sqlite')
    with wrapper as conn:
        with conn.cursor() as cursor:
            cursor.execute('CREATE TABLE sample(value,stamp)')
            assert cursor.execute('INSERT INTO sample VALUES (%s,now())',(7,)) is cursor
            assert cursor.rowcount==1
        with pytest.raises(sqlite3.ProgrammingError):cursor.fetchone()
    with pytest.raises(sqlite3.ProgrammingError):wrapper._conn.execute('SELECT 1')
    with closing(sqlite3.connect(tmp_path/'adapter.sqlite')) as conn:
        assert conn.execute('SELECT value,stamp IS NOT NULL FROM sample').fetchall()==[(7,1)]


def test_schema_upgrade_explicitly_commits_the_callers_pending_transaction(tmp_path):
    with closing(sqlite3.connect(tmp_path/'upgrade.sqlite')) as conn:
        _core.create_sqlite_schema(conn);conn.commit()
        conn.execute("INSERT INTO compound(name,smiles,iteration,state) VALUES ('pending','CC',1,'failed')")
        assert conn.in_transaction
        assert _core.ensure_schema_columns(conn) is None
        assert not conn.in_transaction
        conn.rollback()
        assert conn.execute('SELECT name,state FROM compound').fetchall()==[('pending','failed')]


def test_schema_helper_imports_keep_capture_and_later_lookup_order(monkeypatch):
    events=[]
    def old_ownership(conn):events.append('original-ownership')
    def changed_ownership(conn):events.append('changed-ownership')
    def md(conn):events.append('current-md')
    def descriptor(conn):
        events.append('descriptors')
        monkeypatch.setattr(database,'ensure_druglike_selection_ownership',changed_ownership)
        monkeypatch.setattr(database,'ensure_md_gbsa_schema',md)
    monkeypatch.setattr(database,'ensure_rdkit_descriptor_columns',descriptor)
    monkeypatch.setattr(database,'ensure_druglike_selection_ownership',old_ownership)
    with closing(sqlite3.connect(':memory:')) as conn:_core.create_sqlite_schema(conn)
    assert events==['descriptors','original-ownership','current-md']


@pytest.mark.parametrize('empty',[[],()])
def test_empty_state_updates_do_not_require_path_or_open_connection(monkeypatch,empty):
    monkeypatch.setattr(_core,'open_sqlite',lambda *a:pytest.fail('Empty update opened a DB'))
    assert _core.set_compound_state(iter(empty),'failed') is None
    with pytest.raises(ValueError,match='db_path is required'):_core.set_compound_state([1],'failed')


@pytest.mark.parametrize('fail',[False,True])
def test_state_update_preserves_core_connection_patch_and_commit_order(tmp_path,monkeypatch,fail):
    path=tmp_path/'state.sqlite'
    conn=sqlite3.connect(path)
    try:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (?,?,'CC',1,'docked')",[(1,'one'),(2,'two')]);conn.commit()
        events=[];failure=RuntimeError('update unavailable')
        class Probe:
            def __enter__(self):events.append('enter');conn.__enter__();return self
            def __exit__(self,*args):events.append(('exit',args[0]));return conn.__exit__(*args)
            def execute(self,sql,params):
                events.append(('execute',params))
                if fail:raise failure
                return conn.execute(sql,params)
            def commit(self):events.append('commit');conn.commit()
        def opening(given):assert given is path;events.append('open');return Probe()
        monkeypatch.setattr(_core,'open_sqlite',opening)
        if fail:
            with pytest.raises(RuntimeError) as caught:_core.set_compound_state(iter([2,2]),'failed','glide','diagnostic',path)
            assert caught.value is failure
        else:assert _core.set_compound_state(iter([2,2]),'failed','glide','diagnostic',path) is None
        assert events==['open','enter',('execute',['failed','glide','diagnostic',2,2])]+([] if fail else ['commit'])+[('exit',RuntimeError if fail else None)]
        assert conn.execute('SELECT id,state,failed_stage,failure_message FROM compound ORDER BY id').fetchall()==[(1,'docked',None,None),(2,'docked' if fail else 'failed',None if fail else 'glide',None if fail else 'diagnostic')]
    finally:conn.close()


def test_database_connect_uses_the_current_core_connection_binding(tmp_path,monkeypatch):
    marker=object();calls=[]
    monkeypatch.setattr(_core,'open_sqlite',lambda path:calls.append(path) or marker)
    assert database.connect(str(tmp_path/'db.sqlite')) is marker
    assert calls==[tmp_path/'db.sqlite']


def test_legacy_small_queries_preserve_rows_scores_and_canonicalizer_patch(tmp_path,monkeypatch):
    path=tmp_path/'queries.sqlite'
    with closing(sqlite3.connect(path)) as conn,conn:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state,docking_score,similar_to,similarity) VALUES (?,?,?,?,?,?,?,?)",[
            (1,'REF','OCC',0,'reference',None,None,None),
            (2,'A','CCN',7,'docked',-8.,1,.25),
            (3,'B','not_smiles',7,'ligprep_running',None,None,None),
            (4,'C','OCC',8,'generated',None,None,None)])
    assert _core.get_max_iteration(path)==8
    assert _core.get_iteration_counts(7,path)==(2,1)
    assert sorted(_core.state_counts(7,path))==[('docked',1),('ligprep_running',1)]
    assert _core.list_iteration_compounds(7,path)==[(2,'A','CCN',1,.25),(3,'B','not_smiles',None,None)]
    assert _core.list_iteration_compounds(7,path,"state='docked'")==[(2,'A','CCN',1,.25)]
    assert _core.get_reference_name(1,path)=='REF'
    with pytest.raises(RuntimeError,match='Reference id=2 not found'):_core.get_reference_name(2,path)
    original=_core.canonicalize_smiles;seen=[]
    monkeypatch.setattr(_core,'canonicalize_smiles',lambda smiles:seen.append(smiles) or original(smiles))
    with _core.db_connect(path) as conn:
        with conn.cursor() as cursor:
            assert _core.get_existing_canonical_smiles(cursor)=={'CCO','CCN'}
            assert cursor.execute('SELECT 1').fetchone()==(1,)
    assert sorted(seen)==['CCN','OCC','OCC','not_smiles']


def test_empty_legacy_queries_and_unknown_stage_do_not_add_new_io(tmp_path,monkeypatch):
    path=tmp_path/'empty.sqlite'
    with closing(sqlite3.connect(path)) as conn:_core.create_sqlite_schema(conn)
    assert _core.get_max_iteration(path)==0
    assert _core.get_iteration_counts(9,path)==(0,0)
    assert _core.state_counts(9,path)==[]
    assert _core.list_iteration_compounds(9,path)==[]
    assert _core.find_iteration_for_state('glide',path) is None
    monkeypatch.setattr(_core,'open_sqlite',lambda *a:pytest.fail('Unknown stage accessed SQLite'))
    with pytest.raises(ValueError) as caught:_core.find_iteration_for_state('unknown',path)
    assert caught.value.args==('unknown',)
