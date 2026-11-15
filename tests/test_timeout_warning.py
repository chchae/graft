"""Warnings use final per-compound runtime losses, never upstream populations."""
from types import SimpleNamespace

import pytest

from graft import _core, database, driver
from graft.stages._summary import timeout_warning


STAGES = {
    'ligprep': ('ligprep', None, 'LigPrep job exceeded 1800s; TIMEOUT'),
    'glide': ('glide', 'docking_score', 'Glide job exceeded 1800s; no valid score/pose recovered'),
    'amber-gbsa': ('amber_gbsa', 'amber_gbsa_score', 'Charge generation exceeded 1800 seconds (TIMEOUT)'),
    'prime_gbsa': ('prime_gbsa', 'prime_gbsa_score', 'Prime job exceeded 1800s; TIMEOUT'),
    'md-gbsa': ('md_gbsa', 'md_gbsa_score', 'Native calculation failed: Slurm TIMEOUT'),
}


def project(tmp_path, stage, errors, successful=1):
    args = SimpleNamespace(db_path=tmp_path/'test.sqlite', output=tmp_path,
                           target_count=100, prime_gbsa_input_count=100,
                           synthetic_feasibility_enabled=False, amber_gbsa_enabled=False,
                           md_gbsa_enabled=False, prime_gbsa_enabled=False)
    failed_stage, column, _ = STAGES[stage]
    with database.connect(args.db_path) as conn:
        _core.create_sqlite_schema(conn)
        for index, error in enumerate([None]*successful + errors, 1):
            conn.execute('INSERT INTO compound(id,name,smiles,iteration,state,failed_stage,failure_message,'
                         'docking_score,amber_gbsa_score,prime_gbsa_score,md_gbsa_score) '
                         "VALUES (?,?, 'CC',1,?,?,?,-10,-20,-30,-40)",
                         (index, f'CMPID_{index}', 'md_gbsa_done' if error is None else 'failed',
                          None if error is None else failed_stage, error))
            if error is not None:
                if column:
                    conn.execute(f'UPDATE compound SET {column}=NULL WHERE id=?', (index,))
                else:
                    conn.execute('UPDATE compound SET docking_score=NULL,amber_gbsa_score=NULL,'
                                 'prime_gbsa_score=NULL,md_gbsa_score=NULL WHERE id=?', (index,))
    return args


@pytest.mark.parametrize('stage', STAGES)
@pytest.mark.parametrize('timeouts,successes,warn', [(1,1,True), (2,1,True), (1,2,False), (0,1,False), (2,0,True)])
def test_runtime_threshold_including_exactly_half(tmp_path, stage, timeouts, successes, warn):
    args = project(tmp_path, stage, [STAGES[stage][2]]*timeouts, successes)
    message = timeout_warning(args,1,stage)
    assert bool(message) == warn
    if warn:
        assert '[WARNING]' in message
        assert f'timeout_failed={timeouts}/{timeouts+successes}' in message


@pytest.mark.parametrize('stage', STAGES)
@pytest.mark.parametrize('error', ['Atom typing failed', 'SSH connection TIMEOUT',
                                  'Timed out waiting for job status', 'subprocess.TimeoutExpired: 30',
                                  'Native status query TIMEOUT'])
def test_unrelated_failures_and_transport_waits_are_not_runtime_losses(tmp_path, stage, error):
    args = project(tmp_path,stage,[error],0)
    assert timeout_warning(args,1,stage) == ''


def test_recovered_scores_upstream_failures_and_replicas_do_not_inflate_count(tmp_path):
    stage='md-gbsa'
    args=project(tmp_path,stage,['replica 1: TIMEOUT; replica 2: TIMEOUT'],1)
    with database.connect(args.db_path) as conn:
        conn.execute("INSERT INTO compound(name,smiles,iteration,state,failed_stage,failure_message) "
                     "VALUES ('upstream','C',1,'failed','glide','Glide job exceeded 1800s; TIMEOUT')")
    assert 'timeout_failed=1/2 (50.0%)' in timeout_warning(args,1,stage)
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET md_gbsa_score=-30 WHERE id=2')
    assert timeout_warning(args,1,stage) == ''


@pytest.mark.parametrize('stage', STAGES)
def test_driver_emits_visible_warning_after_verified_stage_completion(tmp_path, monkeypatch, capsys, stage):
    args=project(tmp_path,stage,[STAGES[stage][2]],1)
    monkeypatch.setattr(driver,'stream_process',lambda *a:0)
    monkeypatch.setattr(database,'stage_completion_reason',lambda *a:None)
    monkeypatch.setattr(driver,'external_completion_reason',lambda *a:None)
    monkeypatch.setattr(driver,'md_stage_required',lambda *a:stage=='md-gbsa')
    monkeypatch.setattr(driver,'refresh_distribution_report',lambda *a:None)
    assert driver.supervise_iteration(args,tmp_path/'p.toml',1,[(stage,'module')],30,driver.StopFlag())
    output=capsys.readouterr().out
    assert output.count('[WARNING]')==1
    assert 'timeout_failed=1/2 (50.0%)' in output


def test_empty_iteration_and_noncalculation_stage_have_no_warning(tmp_path):
    args=project(tmp_path,'glide',[],0)
    assert timeout_warning(args,1,'glide') == ''
    assert timeout_warning(args,1,'generate') == ''


def test_driver_withholds_warning_while_native_work_is_pending(tmp_path, monkeypatch, capsys):
    args=project(tmp_path,'glide',[STAGES['glide'][2]],0)
    monkeypatch.setattr(driver,'stream_process',lambda *a:0)
    monkeypatch.setattr(database,'stage_completion_reason',lambda *a:None)
    monkeypatch.setattr(driver,'external_completion_reason',lambda *a:'native child still active')
    assert not driver.supervise_iteration(args,tmp_path/'p.toml',1,[('glide','module')],30,driver.StopFlag(),once=True)
    assert '[WARNING]' not in capsys.readouterr().out


def test_zero_score_md_warns_before_preserving_progression_block(tmp_path, monkeypatch, capsys):
    args=project(tmp_path,'md-gbsa',[STAGES['md-gbsa'][2]],0)
    args.md_gbsa_enabled=True
    monkeypatch.setattr(driver,'stream_process',lambda *a:0)
    monkeypatch.setattr(database,'stage_completion_reason',lambda *a:None)
    monkeypatch.setattr(driver,'external_completion_reason',lambda *a:None)
    monkeypatch.setattr(driver,'md_stage_required',lambda *a:True)
    monkeypatch.setattr(database,'md_gbsa_selection',lambda *a:{'ids':[1]})
    monkeypatch.setattr(database,'md_gbsa_completion_reason',lambda *a,**kw:'no finite MD scores')
    assert not driver.supervise_iteration(args,tmp_path/'p.toml',1,[('md-gbsa','module')],30,driver.StopFlag())
    output=capsys.readouterr().out
    assert 'timeout_failed=1/1 (100.0%)' in output
    assert 'progression blocked' in output
    assert 'stage complete' not in output


def test_amber_valid_timeout_checkpoint_is_a_success(tmp_path):
    args=project(tmp_path,'amber-gbsa',[STAGES['amber-gbsa'][2]],0)
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET amber_gbsa_score=-30 WHERE id=1')
    assert timeout_warning(args,1,'amber-gbsa') == ''
