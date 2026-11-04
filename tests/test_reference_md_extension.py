"""Explicit additional reference work preserves completed original MD results."""
import copy
import pytest
from graft import database, _core
from graft.states import CompoundState as State

@pytest.fixture
def reference_selection(tmp_path):
    path=tmp_path/'project.sqlite'
    with database.connect(path) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(name,smiles,iteration,state,prime_gbsa_score) VALUES (?,'C',0,?,-60)",
                         [('a',State.PRIME_GBSA_DONE),('b',State.PRIME_GBSA_DONE)])
    settings=dict(root='/old',source_sha256='same',protocol=dict(snapshots=20),replicates=1)
    saved=database.freeze_md_gbsa_selection(path,0,settings,count=1)
    with database.connect(path) as conn:
        conn.execute('UPDATE compound SET md_gbsa_score=-55,md_gbsa_std=2,md_gbsa_snapshots=20,state=? WHERE id=1', (State.MD_GBSA_DONE,))
    return path,saved,dict(settings,root='/new')

def test_extension_preserves_completed_scores_and_pins_old_selection(reference_selection):
    path,old,new=reference_selection
    with database.connect(path) as conn: before=conn.execute('SELECT * FROM compound WHERE id=1').fetchone()
    saved=database.extend_reference_md_gbsa_selection(path,old,new,[2])
    assert saved['ids']==[1,2]
    assert saved['settings']['root']=='/new'
    with database.connect(path) as conn:
        assert conn.execute('SELECT * FROM compound WHERE id=1').fetchone()==before
        assert conn.execute('SELECT state FROM compound WHERE id=2').fetchone()[0]==State.MD_GBSA_RUNNING
        assert conn.execute('SELECT candidates,added_ids FROM md_gbsa_selection_history').fetchone()==('[1]','[2]')
    assert database.md_gbsa_completion_reason(path,0) is not None
    with pytest.raises(RuntimeError,match='selection changed'):
        database.extend_reference_md_gbsa_selection(path,old,new,[2])

@pytest.mark.parametrize('change',['protocol','replicates','source_sha256','root','active','scored','wrong_iteration'])
def test_extension_rejects_changed_science_or_unsafe_claims(reference_selection,change):
    path,old,new=reference_selection
    if change in ['protocol','replicates','source_sha256','root']:
        new[change]={'snapshots':10} if change=='protocol' else 2 if change=='replicates' else 'different' if change=='source_sha256' else '/old'
    else:
        with database.connect(path) as conn:
            if change=='active':conn.execute('UPDATE compound SET state=? WHERE id=1',(State.MD_GBSA_RUNNING,))
            elif change=='scored':conn.execute('UPDATE compound SET md_gbsa_score=-50 WHERE id=2')
            else:conn.execute('UPDATE compound SET iteration=1 WHERE id=2')
    with pytest.raises(RuntimeError):database.extend_reference_md_gbsa_selection(path,old,new,[2])
    assert database.md_gbsa_selection(path,0)==old

@pytest.mark.parametrize('ids',[[1],[2,2],[],[True]])
def test_extension_rejects_invalid_or_existing_ids(reference_selection,ids):
    path,old,new=reference_selection
    with pytest.raises((ValueError,RuntimeError)):database.extend_reference_md_gbsa_selection(path,old,new,ids)
    assert database.md_gbsa_selection(path,0)==old
