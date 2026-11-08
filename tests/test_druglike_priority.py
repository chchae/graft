"""Priority admission, ordering and explicit recovery without scientific resets."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from graft import _core, database
from graft.chemistry.druglike import filter_druglike, rank_druglike
from graft.stages import glide


@pytest.fixture
def population(tmp_path):
    db = tmp_path / 'priority.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany(
            'INSERT INTO compound(id,name,smiles,iteration,state,rdk_ro5_violations,rdk_qed,rdk_sa_score) '
            "VALUES (?,?,'CCO',1,'generated',?,?,?)",
            [(1, 'poor', 4, .9, 1), (2, 'tie', 2, .6, 3),
             (3, 'best', 2, .8, 5), (4, 'sa', 2, .6, 2), (5, 'tie2', 2, .6, 3)])
    database.mark_iteration_workspace_prepared(db, 1)
    return db


def test_rank_never_rejects_even_all_ro5_failures(population):
    rows = database.druglike_population(population, 1)
    original = [dict(row) for row in rows]
    result = rank_druglike(rows)
    assert rows == original
    assert all(row['druglike_selected'] and row['druglike_reject_reason'] is None for row in result)
    assert [row['id'] for row in sorted(result, key=lambda row: row['druglike_priority'])] == [3, 4, 2, 5, 1]
    assert rank_druglike([]) == []


def test_atomic_admission_ignores_sa_cutoff_and_orders_native_inputs(population):
    selections = rank_druglike(database.druglike_population(population, 1))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: database.apply_druglike_selection(
            population, 1, 5, selections, .1, priority_only=True), range(2)))
    assert results == [(5, 5, 1.0)] * 2
    assert [row[0] for row in database.ligprep_pending_rows(population, 1, False)] == [3, 4, 2, 5, 1]
    with database.connect(population) as conn:
        conn.execute("UPDATE compound SET state='ligprepped',similar_to=1 WHERE id IN (1,3)")
        conn.execute("UPDATE compound SET state='ligprepped',similar_to=2 WHERE id IN (2,4,5)")
    groups = glide.query_groups(1, population)
    assert list(groups) == [1, 2]
    assert [row[0] for row in groups[1]] == [3, 1]
    assert [row[0] for row in groups[2]] == [4, 2, 5]
    assert database.docking_completion_reason(population, 1, 5)


def test_explicit_conversion_archives_old_decisions_and_restarts_only_rejections(population):
    rows = database.druglike_population(population, 1)
    old = filter_druglike(rows)
    assert database.apply_druglike_selection(population, 1, 5, old) == (5, 0, 1.0)
    saved = database.druglike_report(population, 1)
    # A routine restart preserves the historical decision.
    assert database.apply_druglike_selection(population, 1, 5, rank_druglike(rows), priority_only=True) == (5, 0, 1.0)
    assert database.druglike_report(population, 1) == saved
    assert database.apply_druglike_selection(population, 1, 5, rank_druglike(rows),
        priority_only=True, replace_unstarted=True) == (5, 5, 1.0)
    with database.connect(population) as conn:
        audit = json.loads(conn.execute('SELECT previous_selection FROM iteration_druglike_priority').fetchone()[0])
        assert audit['summary'] == [5, 0, 1.0]
        assert len(audit['compounds']) == 5
        assert conn.execute("SELECT COUNT(*) FROM compound WHERE state='generated'").fetchone()[0] == 5
    assert database.apply_druglike_selection(population, 1, 5, rank_druglike(rows),
        priority_only=True, replace_unstarted=True) == (5, 5, 1.0)


@pytest.mark.parametrize('change', [
    "UPDATE compound SET state='ligprep_running' WHERE id=1",
    "UPDATE compound SET state='failed',failed_stage='ligprep',failure_message='old' WHERE id=1",
    "UPDATE compound SET docking_score=-10 WHERE id=1",
    "UPDATE compound SET md_gbsa_score=-50 WHERE id=1",
    "UPDATE compound SET synthetic_feasibility=1 WHERE id=1",
    "INSERT INTO compound(name,smiles,iteration,state) VALUES ('later','CC',2,'generated')",
    "INSERT INTO iteration_md_gbsa_selection VALUES (1, '[]', '{}')",
])
def test_conversion_refuses_downstream_results_jobs_failures_and_later_work(population, change):
    rows = database.druglike_population(population, 1)
    database.apply_druglike_selection(population, 1, 5, filter_druglike(rows))
    with database.connect(population) as conn:
        conn.execute(change)
        before = conn.execute('SELECT * FROM compound').fetchall()
    saved = database.druglike_report(population, 1)
    with pytest.raises(RuntimeError, match='Cannot reprioritize'):
        database.apply_druglike_selection(population, 1, 5, rank_druglike(rows),
            priority_only=True, replace_unstarted=True)
    assert database.druglike_report(population, 1) == saved
    with database.connect(population) as conn:
        assert conn.execute('SELECT * FROM compound').fetchall() == before


def test_missing_descriptors_roll_back_admission(population):
    rows = rank_druglike(database.druglike_population(population, 1))
    with database.connect(population) as conn:
        conn.execute('UPDATE compound SET rdk_qed=NULL WHERE id=1')
    with pytest.raises(RuntimeError, match='finite descriptors'):
        database.apply_druglike_selection(population, 1, 5, rows, priority_only=True)
    assert database.druglike_completion_reason(population, 1)


def test_scarce_glide_slots_follow_priority_without_exceeding_live_budget():
    from graft.config import glide_group_resources
    counts = {9: 1, 2: 100, 4: 50}
    assert glide_group_resources('host:2', None, counts, ['host:1'], priority_order=True) == {9: 'host:1'}
    assert glide_group_resources('host:2', None, counts, [None], priority_order=True) == {}
    assert glide_group_resources('host:2', None, counts, ['host:1']) == {2: 'host:1'}
