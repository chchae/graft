from pathlib import Path

import pytest

from graft import _core, database, glide_recovery
from graft.config import glide_job_options, glide_molecules_per_job
from graft.stages.glide import iteration_batch_size


@pytest.mark.parametrize('mean,size', [(0,75), (10,50), (10.1,49), (15,37),
                                        (20,25), (29.9,1), (30,1), (40,1)])
def test_adaptive_formula_and_actual_submission(tmp_path, monkeypatch, mean, size):
    assert glide_molecules_per_job(mean) == size
    calls = []
    monkeypatch.setattr(glide_recovery, 'submit',
                        lambda suite, command, folder: calls.append(command) or 'job')
    _core.submit_glide(Path('/suite'), tmp_path/'glide_01.in', tmp_path,
                       'slurm-cpu:8', ligand_count=1001, molecules_per_job=size)
    command = calls[0]
    njobs = int(command[command.index('-NJOBS') + 1])
    assert njobs == (1001 + size - 1) // size
    assert (1001 + njobs - 1) // njobs <= size
    assert command[command.index('-HOST') + 1] == 'slurm-cpu:8'
    assert '-WAIT' not in command


@pytest.mark.parametrize('mean', [-1, float('nan'), float('inf'), True, '10', None])
def test_invalid_mean_is_rejected(mean):
    with pytest.raises(ValueError, match='mean_rotatable_bonds'):
        glide_molecules_per_job(mean)


@pytest.mark.parametrize('size', [0, -1, True, 2.5, '20'])
def test_invalid_batch_size_is_rejected(size):
    with pytest.raises(ValueError, match='molecules_per_job'):
        glide_job_options('cpu:8', 100, molecules_per_job=size)


def test_iteration_average_keeps_completed_inputs_and_excludes_upstream_failures(tmp_path):
    db = tmp_path/'test.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        for cid, iteration, state, failed_stage, rotb in [
                (1,1,'ligprepped',None,10), (2,1,'glide_running',None,20),
                (3,1,'docked',None,15), (4,1,'failed','glide',15),
                (5,1,'failed','ligprep',100), (6,1,'druglike_rejected',None,100),
                (7,2,'ligprepped',None,100), (8,1,'sa_rejected',None,100)]:
            conn.execute('INSERT INTO compound(id,name,smiles,iteration,state,failed_stage,'
                         'rdk_rotatable_bonds) VALUES (?,?,\'CCCC\',?,?,?,?)',
                         (cid, str(cid), iteration, state, failed_stage, rotb))
    assert iteration_batch_size(db, 1) == 37  # mean=15, never average only pending
    with database.connect(db) as conn:
        conn.execute("UPDATE compound SET state='docked',docking_score=-8 WHERE id=1")
        conn.execute("UPDATE compound SET state='failed',failed_stage='glide' WHERE id=2")
    assert iteration_batch_size(db, 1) == 37


def test_missing_legacy_rotb_uses_strict_smiles_descriptor_without_writes(tmp_path):
    db = tmp_path/'test.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(name,smiles,iteration,state) "
                     "VALUES ('test','CCCC',1,'ligprepped')")
    assert iteration_batch_size(db, 1) == 72  # strict butane RotB=1
    with database.connect(db) as conn:
        assert conn.execute('SELECT rdk_rotatable_bonds FROM compound').fetchone() == (None,)
