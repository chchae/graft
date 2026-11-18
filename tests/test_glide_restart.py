"""Explicit, backed-up latest-iteration Glide maintenance."""
import sqlite3
import pytest
from graft import database
from test_glide_license_retry import project, enroll


def test_reset_glide_keeps_upstream_identity_and_archives_old_scores(project,tmp_path):
    args,_,_=project
    enroll(project)
    database.glide_shared_publication(args.db_path,1,True)
    backup=tmp_path/'before.sqlite'
    with database.connect(args.db_path) as conn:
        before=conn.execute('SELECT id,name,smiles,iteration,similar_to,similarity FROM compound ORDER BY id').fetchall()
    assert database.reset_latest_glide_after_native_retirement(args.db_path,1,backup)==3
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT id,name,smiles,iteration,similar_to,similarity FROM compound ORDER BY id').fetchall()==before
        assert conn.execute('SELECT DISTINCT state,docking_score,failed_stage,failure_message FROM compound WHERE iteration=1').fetchall()==[('ligprepped',None,None,None)]
        assert conn.execute('SELECT state FROM compound WHERE id=10').fetchone()==('reference',)
        assert conn.execute('SELECT count(*) FROM glide_license_retry').fetchone()==(0,)
        assert conn.execute('SELECT count(*) FROM glide_license_audit').fetchone()==(0,)
    assert not database.glide_shared_publication(args.db_path,1)
    with sqlite3.connect(backup) as conn:
        assert conn.execute('SELECT docking_score FROM compound WHERE id=1').fetchone()==(-5,)
    with pytest.raises(FileExistsError):
        database.reset_latest_glide_after_native_retirement(args.db_path,1,backup)


@pytest.mark.parametrize('change',[
    "UPDATE compound SET prime_gbsa_score=-7 WHERE id=1",
    "UPDATE compound SET state='generated' WHERE id=2",
    "UPDATE compound SET failed_stage=NULL WHERE id=2",
    "INSERT INTO compound(name,smiles,iteration,state) VALUES('next','C',2,'generated')",
])
def test_reset_refuses_downstream_upstream_and_later_iteration_conflicts(project,tmp_path,change):
    args,_,_=project
    with database.connect(args.db_path) as conn:conn.execute(change)
    backup=tmp_path/'refused.sqlite'
    with pytest.raises(RuntimeError):
        database.reset_latest_glide_after_native_retirement(args.db_path,1,backup)
    assert not backup.exists()
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score FROM compound WHERE id=1').fetchone()==(-5,)
