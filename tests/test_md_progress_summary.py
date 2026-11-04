"""Read-only per-compound progress; calculation completion is not a DB commit."""
import json
from types import SimpleNamespace

import pytest

from graft import _core, database, driver
from graft.md_gbsa_native import save
from graft.stages._summary import md_pending_summary


@pytest.fixture
def project(tmp_path):
    args = SimpleNamespace(db_path=tmp_path/'project.sqlite', output=tmp_path,
                           target_count=24, prime_gbsa_input_count=24,
                           synthetic_feasibility_enabled=False, md_gbsa_enabled=True)
    with database.connect(args.db_path) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state,docking_score,prime_gbsa_score) "
                         "VALUES (?,?,'CC',1,'prime_gbsa_done',-10,-50)",
                         [(cid, f'C{cid}') for cid in range(1, 25)])
    database.freeze_md_gbsa_selection(args.db_path, 1,
        dict(protocol=dict(snapshots=1, seed=1), replicates=2), count=24, target_count=24, prime_count=24)
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET md_gbsa_score=-30 WHERE id<=12')
    path = tmp_path/'iter01/09-md_gbsa/progress.json'
    path.parent.mkdir(parents=True)
    progress = {str((cid, r)): dict(status='done') for cid in range(1, 15) for r in range(2)}
    # A stale worker record cannot override a finite committed database score.
    progress['(1, 0)'] = dict(phase='md', status='running')
    for cid, phase in [(15, 'md'), (16, 'prime'), (17, 'frames'), (18, 'md'), (19, 'prime'), (20, 'md')]:
        progress[str((cid, 0))] = dict(phase=phase, status='pending')
    progress['(18, 1)'] = dict(status='done')
    progress['(21, 0)'] = dict(phase='md', status='unstarted')
    save(path, progress)
    return args, path, progress


def test_counts_originals_across_replicas_and_distinguishes_uncommitted_results(project):
    args, path, progress = project
    assert md_pending_summary(args, 1) == (
        'compounds=24 (queued=4, running=6, calculated=14, db_committed=12, db_pending=2)')
    # Reading a snapshot never commits results or reclassifies the real state.
    assert len(database.completed_md_ids(args.db_path, 1)) == 12
    assert json.loads(path.read_text()) == progress


@pytest.mark.parametrize('content', ['{invalid', '[]'])
def test_unreadable_progress_preserves_committed_count_and_marks_unknown(project, content):
    args, path, _ = project
    path.write_text(content)
    result = md_pending_summary(args, 1)
    assert 'calculated=12, db_committed=12, db_pending=0' in result
    assert 'unknown=12' in result


def test_failures_missing_selected_rows_and_unrelated_compounds(project):
    args, path, progress = project
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET state='failed',failed_stage='md_gbsa' WHERE id=24")
        conn.execute('DELETE FROM compound WHERE id=23')
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,md_gbsa_score) "
                     "VALUES (25,'foreign','C',1,'md_gbsa_done',-90)")
    progress['(22, 0)'] = dict(status='done')
    progress['(22, 1)'] = dict(status='failed', phase='md')
    save(path, progress)
    summary = md_pending_summary(args, 1)
    assert summary.startswith('compounds=24 (queued=1, running=6, calculated=14, db_committed=12')
    assert 'failed=2, unknown=1' in summary


def test_only_md_completion_is_not_calculated_and_all_replicas_must_finish(project):
    args, path, progress = project
    progress['(13, 1)'] = dict(phase='frames', status='pending')
    save(path, progress)
    assert 'running=7, calculated=13, db_committed=12, db_pending=1' in md_pending_summary(args, 1)


def test_missing_progress_is_unrecorded_queue_except_committed_results(project):
    args, path, _ = project
    path.unlink()
    assert 'queued=12, running=0, calculated=12, db_committed=12' in md_pending_summary(args, 1)
    assert md_pending_summary(args, 0) == ''


def test_legacy_selection_without_replica_settings_reports_unknown(project):
    args, _, _ = project
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE iteration_md_gbsa_selection SET settings='{}' WHERE iteration=1")
    summary = md_pending_summary(args, 1)
    assert 'calculated=12, db_committed=12' in summary
    assert 'unknown=12' in summary


@pytest.mark.parametrize('rc', [0, 1])
def test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error(project, monkeypatch, capsys, rc):
    args, _, _ = project
    monkeypatch.setattr(driver, 'stream_process', lambda *a: rc)
    assert not driver.supervise_iteration(args, args.output/'project.toml', 1,
        [('md-gbsa', 'graft.stages.md_gbsa')], 30, driver.StopFlag(), once=True)
    line = capsys.readouterr().out
    assert 'md-gbsa pending:' in line
    assert 'compounds=24 (queued=4, running=6, calculated=14, db_committed=12, db_pending=2)' in line
    assert 'elapsed=' in line
