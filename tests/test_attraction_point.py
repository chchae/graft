"""Soft geometry preference for new selections; synthetic poses are not affinity validation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database
from graft.config import attraction_point_settings
from graft.attraction_point import measure, prepare
from graft.states import CompoundState as State


@pytest.mark.parametrize('value', [False, True, '1,2,3', [1,2], [1,2,3,4], [1,True,3],
                                    [1,float('nan'),3], [1,float('inf'),3], {'x':1,'y':2,'z':3}])
def test_invalid_point_is_rejected(value):
    with pytest.raises(ValueError,match='attraction_point'):
        attraction_point_settings({'attraction_point':value})


def test_point_loads_as_optional_three_angstrom_coordinates(tmp_path):
    from test_project_config import PROJECT_TOML
    assert attraction_point_settings({}) == {'attraction_point':None}
    path = tmp_path/'project.toml'
    path.write_text(PROJECT_TOML+'\nattraction_point = [-52.102367, 2.138612, -28.106731]\n')
    assert _core.load_project_toml(path)['attraction_point'] == (-52.102367,2.138612,-28.106731)


def pose(cid,score,atoms):
    return SimpleNamespace(title=f'CMPID_{cid}',property={'r_i_docking_score':score},
                           atom=[SimpleNamespace(atomic_number=n,x=x,y=y,z=z) for n,x,y,z in atoms])


def request():
    return dict(point=[0.,0.,0.],source='best_poses.maegz',iteration=1,
                rows=[(1,'first',-12.),(2,'second',-11.),(3,'third',-10.)])


def structures():
    return [SimpleNamespace(title='receptor',property={},atom=[]),
            pose(1,-12.,[(6,3.,0.,0.),(1,0.,0.,0.)]),
            pose(2,-11.,[(7,2.,0.,0.)]),pose(3,-10.,[(8,.1,0.,0.)])]


def test_distance_uses_ligand_heavy_atoms_and_inclusive_boundary():
    original = structures()
    assert measure(request(),SimpleNamespace(StructureReader=lambda p:copy.deepcopy(original))) == {
        '1':3.,'2':2.,'3':.1}
    assert original[1].property == {'r_i_docking_score':-12.}


@pytest.mark.parametrize('problem', ['missing','duplicate','wrong_score','nan_score','no_heavy','nan_xyz'])
def test_ambiguous_or_unusable_saved_best_pose_stops_measurement(problem):
    poses = structures()
    if problem == 'missing':poses.pop()
    elif problem == 'duplicate':poses.append(copy.deepcopy(poses[-1]))
    elif problem == 'wrong_score':poses[-1].property['r_i_docking_score'] = -99.
    elif problem == 'nan_score':poses[-1].property['r_i_docking_score'] = float('nan')
    elif problem == 'no_heavy':poses[-1].atom[0].atomic_number = 1
    else:poses[-1].atom[0].x = float('nan')
    with pytest.raises(ValueError):
        measure(request(),SimpleNamespace(StructureReader=lambda p:iter(poses)))


def test_reference_titles_and_blank_title_fallback_are_mapped_without_renaming():
    req = dict(request(),iteration=0,rows=[(1,'REF_0001',None),(2,'original name',None)])
    poses = [pose(1,0,[(6,1,0,0)]),pose(2,0,[(6,4,0,0)])]
    poses[0].title=''
    poses[1].title='original name'
    assert measure(req,SimpleNamespace(StructureReader=lambda p:iter(poses))) == {'1':1.,'2':4.}
    assert poses[1].title == 'original name'


@pytest.fixture
def project(tmp_path):
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        for cid in range(1,7):
            conn.execute('INSERT INTO compound(id,name,smiles,iteration,state,docking_score) VALUES (?,?,?,1,?,?)',
                         (cid,f'CMPID_{cid}','C',State.DOCKED,-13.+cid))
    return SimpleNamespace(db_path=db,output=tmp_path,target_count=6,attraction_point=(0.,0.,0.),
                           schrodinger=Path('/suite'))


def freeze(args,stage,distances=None):
    distances = distances or {'1':4.,'2':2.,'3':.1,'4':3.,'5':4.,'6':5.}
    return database.freeze_attraction_policy(args.db_path,1,stage,args.attraction_point,distances,
                                              expected_rows=database.attraction_input_rows(args.db_path,1))


def test_amber_prefers_contact_then_energy_without_refilling_scored_positions(project):
    a=project
    freeze(a,'amber_gbsa')
    assert database.claim_amber_candidates(a.db_path,1,6,2) == [2,3]
    assert database.store_amber_results(a.db_path,1,[{'id':2,'score':-30},{'id':3,'error':'failed'}],[2,3]) == 1
    assert database.claim_amber_candidates(a.db_path,1,6,2) == []
    with database.connect(a.db_path) as conn:
        assert conn.execute('SELECT docking_score FROM compound WHERE id=2').fetchone() == (-11.,)
        assert conn.execute('SELECT amber_gbsa_score FROM compound WHERE id=1').fetchone() == (None,)


def test_prime_keeps_upstream_amber_pool_and_its_own_energy_order(project):
    a=project
    freeze(a,'amber_gbsa')
    ids=database.claim_amber_candidates(a.db_path,1,6,2)
    database.store_amber_results(a.db_path,1,[{'id':2,'score':-20},{'id':3,'score':-40}],ids)
    freeze(a,'prime_gbsa')
    assert [r[0] for r in database.prime_selection_rows(a.db_path,1,2,2)] == [3,2]
    assert database.claim_prime_gbsa_candidates(a.db_path,1,2,6,2) == [3,2]
    with database.connect(a.db_path) as conn:
        conn.execute('UPDATE compound SET prime_gbsa_score=-50,state=? WHERE id IN (2,3)',(State.PRIME_GBSA_DONE,))
    assert database.claim_prime_gbsa_candidates(a.db_path,1,2,6,2) == []


@pytest.mark.parametrize('source', ['prime_gbsa','amber_gbsa','docking'])
def test_md_priority_applies_to_whichever_score_source_is_used_and_freezes(project,source):
    a=project
    if source != 'docking':
        with database.connect(a.db_path) as conn:
            conn.execute(f'UPDATE compound SET {source}_score=docking_score-20,state=?',
                         (State.PRIME_GBSA_DONE if source=='prime_gbsa' else State.DOCKED,))
    policy=freeze(a,'md_gbsa')
    candidates=database.md_gbsa_candidates(a.db_path,1,count=3,prime_enabled=source=='prime_gbsa')
    assert candidates['ranking_source'] == source and candidates['ids'] == [2,3,1]
    saved=database.freeze_md_gbsa_selection(a.db_path,1,dict(protocol=dict(snapshots=1),replicates=1),
        count=3,target_count=6,prime_count=6,prime_enabled=source=='prime_gbsa')
    a.attraction_point=(99.,99.,99.)
    assert freeze(a,'md_gbsa',{'1':0.,'2':9.,'3':9.,'4':9.,'5':9.,'6':9.}) == policy
    assert database.freeze_md_gbsa_selection(a.db_path,1,{},count=6,prime_enabled=False) == saved


@pytest.mark.parametrize('source,column', [('docking','docking_score'),('amber','amber_gbsa_score'),
                                          ('prime','prime_gbsa_score'),('md','md_gbsa_score')])
def test_fep_uses_contact_priority_before_its_existing_score_order_and_retains_snapshot(project,source,column):
    a=project
    if source == 'md':
        with database.connect(a.db_path) as conn:
            conn.execute('UPDATE compound SET prime_gbsa_score=docking_score-20,state=?',(State.PRIME_GBSA_DONE,))
        selected=database.freeze_md_gbsa_selection(a.db_path,1,dict(protocol=dict(snapshots=1),replicates=1),
                                                 count=6,target_count=6,prime_count=6)
        for cid in selected['ids']:
            score=-33.+cid
            database.store_md_gbsa_result(a.db_path,1,cid,dict(id=cid,mean=score,std=0.,snapshots=[dict(frame=0,score=score)]))
    elif source != 'docking':
        with database.connect(a.db_path) as conn:
            conn.execute(f'UPDATE compound SET {column}=docking_score-20')
    freeze(a,'fep')
    rows=database.fep_candidates(a.db_path,1,3,target_count=6,docking_only=source=='docking')
    assert [r['id'] for r in rows] == [2,3,1]
    assert {r['ranking_source'] for r in rows} == {source}
    with database.connect(a.db_path) as conn:
        conn.execute(f'UPDATE compound SET {column}=-999 WHERE id=6')
    assert [r['id'] for r in database.fep_candidates(a.db_path,1,3,target_count=6)] == [2,3,1]


def test_legacy_started_stage_keeps_original_inputs_but_later_stage_can_opt_in(project,monkeypatch):
    a=project
    with database.connect(a.db_path) as conn:
        conn.execute('UPDATE compound SET amber_gbsa_score=-40 WHERE id=1')
    monkeypatch.setattr(_core,'run_command',lambda *a,**kw:pytest.fail('Recomputed a started stage'))
    assert prepare(a,1,'amber_gbsa')['point'] is None
    assert database.attraction_policy(a.db_path,1,'prime_gbsa') is None
    assert database.claim_amber_candidates(a.db_path,1,6,2) == [2]


def test_disabled_feature_does_not_read_poses_or_create_tables(project,monkeypatch):
    a=project
    a.attraction_point=None
    monkeypatch.setattr(_core,'run_command',lambda *a,**kw:pytest.fail('Unexpected geometry read'))
    assert prepare(a,1,'amber_gbsa') is None
    assert database.claim_amber_candidates(a.db_path,1,6,2) == [1,2]
    with database.connect(a.db_path) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_attraction_point'").fetchone()


def test_source_identity_race_and_missing_measurements_never_freeze_or_claim(project):
    a=project
    before=database.attraction_input_rows(a.db_path,1)
    with database.connect(a.db_path) as conn:
        conn.execute("UPDATE compound SET docking_score=-99 WHERE id=1")
    distances={'1':1.,'2':3.,'3':3.,'4':3.,'5':3.,'6':3.}
    with pytest.raises(RuntimeError,match='changed'):
        database.freeze_attraction_policy(a.db_path,1,'prime_gbsa',a.attraction_point,distances,expected_rows=before)
    assert database.attraction_policy(a.db_path,1,'prime_gbsa') is None
    with pytest.raises(ValueError,match='exact source population'):
        database.freeze_attraction_policy(a.db_path,1,'prime_gbsa',a.attraction_point,{'1':1.})
    assert database.attraction_policy(a.db_path,1,'prime_gbsa') is None


def test_worker_freezes_geometry_once_and_rejects_changed_published_poses(project, monkeypatch):
    from graft.paths import stage_directory
    a = project
    source = stage_directory(a.output, 1, 'glide')/'best_poses.maegz'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'immutable published best poses')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        req = json.loads(Path(command[-2]).read_text())
        assert req['point'] == [0.,0.,0.]
        assert req['source'] == str(source.resolve())
        assert len(req['rows']) == 6
        Path(command[-1]).write_text(json.dumps({'1':4.,'2':2.,'3':.1,'4':3.,'5':4.,'6':5.}))
    monkeypatch.setattr(_core,'run_command',run)
    first = prepare(a,1,'amber_gbsa')
    assert len(calls) == 1
    a.attraction_point = (9.,9.,9.)
    assert prepare(a,1,'amber_gbsa') == first
    assert len(calls) == 1
    source.write_bytes(b'changed pose file')
    with pytest.raises(RuntimeError,match='poses changed'):
        prepare(a,1,'amber_gbsa')
    assert database.attraction_policy(a.db_path,1,'amber_gbsa') == first


def test_fep_export_prepares_preference_before_freezing_candidates(project, monkeypatch):
    from graft.stages import fep
    from graft.paths import stage_directory
    a = project
    a.fep_input_count = 3
    a.prime_gbsa_enabled = False
    source = stage_directory(a.output,1,'glide')/'best_poses.maegz'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'published poses')
    def run(command, **kwargs):
        Path(command[-1]).write_text(json.dumps({'1':4.,'2':2.,'3':.1,'4':3.,'5':4.,'6':5.}))
    monkeypatch.setattr(_core,'run_command',run)
    fep.export_candidates(a,1)
    import csv
    with (stage_directory(a.output,1,'fep')/'fep_candidates.tsv').open() as stream:
        rows = list(csv.DictReader(stream,delimiter='\t'))
    assert [int(row['id']) for row in rows] == [2,3,1]
    assert {row['ranking_source'] for row in rows} == {'docking'}
