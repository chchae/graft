"""Optional upstream scoring and unchanged MD inputs/recovery; no native science."""
import copy
import csv
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database, driver, md_gbsa, md_gbsa_native
from graft.config import amber_settings, md_gbsa_settings
from graft.paths import stage_directory
from graft.states import CompoundState as State
from test_md_gbsa import db, result


@pytest.mark.parametrize('iteration', [0, 1])
@pytest.mark.parametrize('source', ['amber_gbsa', 'docking'])
def test_md_without_prime_ranks_one_finite_source_and_retains_selection(db, iteration, source):
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET iteration=?,state=?',
                     (iteration, State.REFERENCE if iteration == 0 else State.DOCKED))
        if source == 'amber_gbsa':
            conn.execute('UPDATE compound SET amber_gbsa_score=?', (float('inf'),))
            conn.execute('UPDATE compound SET amber_gbsa_score=-100 WHERE id IN (3,4)')
    assert database.md_gbsa_readiness_reason(db, iteration, target_count=4, prime_enabled=False) is None
    ranking = database.md_gbsa_candidates(db, iteration, count=2, prime_enabled=False)
    assert ranking['ranking_source'] == source
    expected = [3, 4] if source == 'amber_gbsa' else [1, 2]
    assert ranking['ids'] == expected
    settings = dict(ranking_source=source, ranking_ids=expected, protocol=dict(snapshots=20), replicates=1)
    saved = database.freeze_md_gbsa_selection(db, iteration, settings, count=2,
                                             target_count=4, prime_enabled=False)
    assert saved['ids'] == expected
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET prime_gbsa_score=-999,amber_gbsa_score=-999 WHERE id=2')
    assert database.freeze_md_gbsa_selection(db, iteration, {}, count=4, prime_enabled=True) == saved
    assert database.md_gbsa_selection(db, iteration) == saved


@pytest.mark.parametrize('state', [State.GLIDE_RUNNING, State.AMBER_RUNNING, State.PRIME_GBSA_RUNNING,
                                   State.MD_GBSA_RUNNING, State.FEP_RUNNING])
def test_disabled_prime_does_not_bypass_unfinished_work(db, state):
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET state=? WHERE id=4', (state,))
    assert database.md_gbsa_readiness_reason(db, 1, target_count=4, prime_enabled=False)
    with pytest.raises(RuntimeError, match='waiting'):
        database.freeze_md_gbsa_selection(db, 1, {}, target_count=4, prime_enabled=False)
    assert database.md_gbsa_selection(db, 1) is None


def test_enabled_amber_must_finish_before_docking_fallback(db):
    assert database.md_gbsa_readiness_reason(db, 1, target_count=4, prime_enabled=False,
                                           amber_keep_count=4)
    with database.connect(db) as conn:
        conn.execute("UPDATE compound SET state='failed',failed_stage='amber_gbsa'")
    assert database.md_gbsa_readiness_reason(db, 1, target_count=4, prime_enabled=False,
                                           amber_keep_count=4) is None
    assert database.md_gbsa_candidates(db, 1, count=2, prime_enabled=False)['ranking_source'] == 'docking'


@pytest.mark.parametrize('change', ['score_order', 'higher_source'])
def test_ranking_change_during_input_preparation_never_claims(db, change):
    ranking = database.md_gbsa_candidates(db, 1, count=2, prime_enabled=False)
    settings = dict(ranking_source=ranking['ranking_source'], ranking_ids=ranking['ids'])
    with database.connect(db) as conn:
        if change == 'score_order':
            conn.execute('UPDATE compound SET docking_score=-999 WHERE id=4')
        else:
            conn.execute('UPDATE compound SET amber_gbsa_score=-999 WHERE id=4')
    with pytest.raises(RuntimeError, match='ranking changed'):
        database.freeze_md_gbsa_selection(db, 1, settings, count=2, target_count=4, prime_enabled=False)
    assert database.md_gbsa_selection(db, 1) is None
    with database.connect(db) as conn:
        assert not conn.execute('SELECT 1 FROM compound WHERE state=?', (State.MD_GBSA_RUNNING,)).fetchone()


def test_prime_disabled_amber_count_is_independent_of_unused_prime_count(tmp_path):
    assert md_gbsa_settings({'md-gbsa': True, 'prime-gbsa': False})['md_gbsa_enabled']
    cfg = amber_settings({'amber-gbsa': True, 'amber-input-count': 2,
                          'prime-gbsa': False, 'prime-gbsa-input-count': 100, 'out-dir': 'output'}, tmp_path)
    assert cfg['amber_keep_count'] == 2


class Structure:
    def __init__(self, title, coordinates):
        self.title, self.property = title, {}
        self.atom = [SimpleNamespace(index=i+1, xyz=xyz, property={}) for i, xyz in enumerate(coordinates)]

    @property
    def atom_total(self):
        return len(self.atom)

    def copy(self):
        return copy.deepcopy(self)

    def merge(self, other, copy_props=True):
        combined = self.copy()
        combined.atom += copy.deepcopy(other.atom)
        for i, atom in enumerate(combined.atom, 1):
            atom.index = i
        return combined

    def write(self, filename):
        Path(filename).write_text(json.dumps(dict(title=self.title, atoms=[vars(a) for a in self.atom])))


@pytest.fixture
def pose_inputs(db, monkeypatch):
    args = SimpleNamespace(**md_gbsa_settings({'md-gbsa': True, 'prime-gbsa': False}),
        db_path=db, output=db.parent, schrodinger=Path('/suite'), prime_gbsa_receptor=None,
        amber_receptor=db.parent/'receptor.maegz', reference_poses=db.parent/'references.maegz',
        prime_gbsa_enabled=False, target_count=4, prime_gbsa_input_count=4)
    poses = stage_directory(args.output, 1, 'glide')/'best_poses.maegz'
    poses.parent.mkdir(parents=True)
    data = {str(args.amber_receptor): [Structure('receptor', [(0,0,0), (1,1,1)])],
            str(poses): [Structure(f'CMPID_{i}', [(i,2,3)]) for i in range(1,5)],
            str(args.reference_poses): [Structure(f'cmp{i-1}', [(i,2,3)]) for i in range(1,5)]}
    for filename in data:
        Path(filename).write_text('original structures')
    class Writer:
        def __init__(self, filename):
            self.path, self.rows = str(filename), []
        def append(self, st):
            self.rows.append(st.copy())
        def close(self):
            data[self.path] = self.rows
            Path(self.path).write_text(json.dumps([st.title for st in self.rows]))
    monkeypatch.setitem(sys.modules, 'schrodinger', SimpleNamespace(structure=SimpleNamespace(
        StructureReader=lambda filename: iter(copy.deepcopy(data[str(filename)])), StructureWriter=Writer)))
    def execute(command, **kw):
        if Path(command[1]).name == 'md_gbsa_native.py':
            md_gbsa_native.export_pairs(Path(command[5]), Path(command[3]), json.loads(command[7]))
        else:
            monkeypatch.setattr(sys, 'argv', list(map(str, command[1:])))
            runpy.run_path(str(command[1]), run_name='__main__')
    monkeypatch.setattr(_core, 'run_command', execute)
    return args, data, poses


@pytest.mark.parametrize('iteration', [0, 1])
def test_original_pose_pairing_is_validated_without_prime_coordinates(pose_inputs, iteration):
    args, data, poses = pose_inputs
    if iteration == 0:
        with database.connect(args.db_path) as conn:
            conn.execute('UPDATE compound SET iteration=0,state=?', (State.REFERENCE,))
    ranking = database.md_gbsa_candidates(args.db_path, iteration, count=2, prime_enabled=False)
    settings = md_gbsa.settings_for(args, iteration, ranking)
    assert settings['ranking_source'] == 'docking'
    assert settings['ranking_ids'] == [1,2]
    assert not (stage_directory(args.output, iteration, 'prime_gbsa')/'prime_gbsa_final.maegz').exists()
    for cid in (1,2):
        pair = json.loads((Path(settings['source']).parent/f'validated/CMPID_{cid}/complex.maegz').read_text())
        assert pair['title'] == f'CMPID_{cid}'
        assert [a['xyz'] for a in pair['atoms']] == [[0,0,0],[1,1,1],[cid,2,3]]
        assert [a['property'][md_gbsa_native.LIGAND] for a in pair['atoms']] == [0,0,1]
    assert all(not st.property for st in data[str(poses)])


@pytest.mark.parametrize('problem', ['missing', 'duplicate'])
def test_missing_or_duplicate_pose_stops_before_claims_or_ssh(pose_inputs, problem, monkeypatch):
    args, data, poses = pose_inputs
    if problem == 'missing':
        data[str(poses)] = data[str(poses)][1:]
    else:
        data[str(poses)].append(data[str(poses)][0].copy())
    monkeypatch.setattr(driver, 'external_completion_reason', lambda *a: None)
    monkeypatch.setattr(md_gbsa, 'ssh', lambda *a, **kw: pytest.fail('Contacted remote before pose validation'))
    with pytest.raises(ValueError, match='Missing optimized Prime pairs|Duplicate final Prime pose'):
        md_gbsa.run(args, 1)
    assert database.md_gbsa_selection(args.db_path, 1) is None


def test_fep_export_uses_completed_md_even_when_prime_is_disabled(db, tmp_path):
    from graft.stages import fep
    settings = dict(protocol=dict(snapshots=20), replicates=1)
    database.freeze_md_gbsa_selection(db, 1, settings, count=2, target_count=4, prime_enabled=False)
    for cid in (1,2):
        database.store_md_gbsa_result(db, 1, cid, result(cid, -50-cid))
    args = SimpleNamespace(db_path=db, output=tmp_path, fep_input_count=2,
                           target_count=4, prime_gbsa_enabled=False, amber_gbsa_enabled=False)
    with fep.export_candidates(args,1).open() as stream:
        rows = list(csv.DictReader(stream, delimiter='\t'))
    assert [r['id'] for r in rows] == ['2','1']
    assert {r['ranking_source'] for r in rows} == {'md'}


@pytest.mark.parametrize('amber', [True, False])
def test_worker_freezes_fallback_inputs_and_recovers_them_when_flags_change(pose_inputs, monkeypatch, amber):
    args, _, _ = pose_inputs
    args.amber_gbsa_enabled = amber
    if amber:
        args.amber_keep_count = 4
        with database.connect(args.db_path) as conn:
            conn.execute('UPDATE compound SET amber_gbsa_score=-40')
            conn.execute('UPDATE compound SET amber_gbsa_score=-80 WHERE id=4')
    ranking = database.md_gbsa_candidates(args.db_path, 1, prime_enabled=False)
    monkeypatch.setattr(driver, 'external_completion_reason', lambda *a: None)
    monkeypatch.setattr(md_gbsa, 'ssh', lambda *a, **kw: '')
    monkeypatch.setattr(md_gbsa, 'upload', lambda *a: None)
    monkeypatch.setattr(md_gbsa, 'remote_call', lambda *a: 'GRAFT_MD={"status":"ready"}')
    calls = []
    def before_jobs(settings, ids):
        calls.append((settings, ids))
        raise RuntimeError('stop before native jobs')
    monkeypatch.setattr(md_gbsa, 'prepare', before_jobs)
    with pytest.raises(RuntimeError, match='stop before native jobs'):
        md_gbsa.run(args, 1)
    saved = database.md_gbsa_selection(args.db_path, 1)
    assert saved['ids'] == ranking['ids']
    assert saved['settings']['ranking_source'] == ('amber_gbsa' if amber else 'docking')
    assert saved['settings']['ranking_ids'] == ranking['ids']
    args.md_gbsa_enabled = False
    args.prime_gbsa_enabled = True
    monkeypatch.setattr(md_gbsa, 'settings_for', lambda *a: pytest.fail('Replaced frozen input settings'))
    with pytest.raises(RuntimeError, match='stop before native jobs'):
        md_gbsa.run(args, 1)
    assert calls[0] == calls[1]
    assert database.md_gbsa_selection(args.db_path, 1) == saved


def test_concurrent_fallback_claims_keep_one_selection(db):
    from concurrent.futures import ThreadPoolExecutor
    def claim():
        return database.freeze_md_gbsa_selection(db, 1, dict(protocol={}, replicates=1),
                                                count=2, target_count=4, prime_enabled=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: claim(), range(2)))
    assert first == second
    assert first['ids'] == [1,2]
    with database.connect(db) as conn:
        assert [r[0] for r in conn.execute('SELECT id FROM compound WHERE state=? ORDER BY id',
                                          (State.MD_GBSA_RUNNING,))] == [1,2]
