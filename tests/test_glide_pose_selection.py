"""Contact-first pose reduction; synthetic geometry does not validate affinity."""
import csv
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from graft import _core, database
from graft.glide_pose_selection import PoseSelector, PoseGeometryError, extract
from graft import glide_timeout_results
from test_glide_timeout_results import Pose, structure_module


def pose(cid, score, tag, distance, hydrogen=None):
    st = Pose(cid, score, tag)
    st.atom = [SimpleNamespace(atomic_number=6, x=distance, y=0., z=0.)]
    if hydrogen is not None:
        st.atom.append(SimpleNamespace(atomic_number=1, x=hydrogen, y=0., z=0.))
    return st


def library():
    return [pose(1,-12,'unbound_lowest',4), pose(1,-8,'contact',1),
            pose(1,-9,'best_contact_state',2), pose(1,-9,'tie_keeps_first',.5),
            pose(2,-8,'no_contact_lowest',3), pose(2,-7,'no_contact_higher',2.1),
            pose(3,-20,'only_hydrogen_contact',3,hydrogen=0), pose(3,-5,'heavy_contact',1),
            pose(4,float('nan'),'invalid',0), pose(4,float('inf'),'invalid',0)]


@pytest.mark.parametrize('point,expected',[(None,['unbound_lowest','no_contact_lowest','only_hydrogen_contact']),
                                        ([0,0,0],['best_contact_state','no_contact_lowest','heavy_contact'])])
def test_generated_native_extractor_pairs_exact_pose_and_score_across_states(tmp_path,monkeypatch,point,expected):
    module = structure_module({'input':library()})
    monkeypatch.setitem(sys.modules,'schrodinger',SimpleNamespace(structure=module))
    script = _core.create_score_extractor(tmp_path,attraction_point=point)
    scores, poses = tmp_path/'scores.tsv',tmp_path/'best.maegz'
    monkeypatch.setattr(sys,'argv',[str(script),'input',str(scores),str(poses)])
    runpy.run_path(str(script),run_name='__main__')
    rows=json.loads(poses.read_text())
    assert [r['properties']['tag'] for r in rows]==expected
    with scores.open() as stream:
        recorded={int(r['id']):float(r['docking_score']) for r in csv.DictReader(stream,delimiter='\t')}
    for row in rows:
        props=row['properties']
        assert recorded[props['i_user_compound_id']]==props['r_i_docking_score']
        if point is not None:
            assert props['b_graft_attraction_point_contact']==(props['r_graft_attraction_point_distance']<=2)


def test_contact_uses_full_3d_euclidean_distance_and_includes_two_angstrom_boundary():
    selector=PoseSelector([10,20,30])
    st=pose(1,-8,'three_dimensions',0)
    st.atom=[SimpleNamespace(atomic_number=7,x=10.,y=20.,z=32.)]
    selector.add(st,1)
    outside=pose(1,-20,'outside_diagonal',0)
    outside.atom=[SimpleNamespace(atomic_number=6,x=10.,y=22.,z=32.)]
    selector.add(outside,1)
    assert selector.poses[1].property['b_graft_attraction_point_contact']
    assert selector.poses[1].property['r_graft_attraction_point_distance']==pytest.approx(2)


@pytest.mark.parametrize('problem',['nan','missing_coordinates','no_heavy_atoms'])
def test_unknown_contact_geometry_cannot_silently_fall_back_or_overwrite_old_outputs(tmp_path,problem):
    st=pose(1,-10,'invalid',0)
    if problem=='nan':st.atom[0].x=float('nan')
    elif problem=='missing_coordinates':del st.atom[0].x
    else:st.atom[0].atomic_number=1
    scores,poses=tmp_path/'scores.tsv',tmp_path/'best.maegz'
    scores.write_text('previous scores');poses.write_text('previous poses')
    with pytest.raises(PoseGeometryError):
        extract('input',scores,poses,[0,0,0],structure_module({'input':[st]}))
    assert scores.read_text()=='previous scores' and poses.read_text()=='previous poses'
    with pytest.raises(PoseGeometryError):
        glide_timeout_results.collect(dict(inputs=['input'],ids=[1],output=str(tmp_path),
            attraction_point=[0,0,0]),structure_module({'input':[st]}))


def test_timeout_prefix_uses_same_contact_policy_across_sources(tmp_path):
    rows=library()
    inputs={'one':rows[:2]+[OSError('truncated tail')],'two':rows[2:]}
    report=glide_timeout_results.collect(dict(inputs=list(inputs),ids=[1,2,3,4],output=str(tmp_path),
        attraction_point=[0,0,0]),structure_module(inputs))
    selected=json.loads((tmp_path/'best_poses.maegz').read_text())
    assert [r['properties']['tag'] for r in selected]==['best_contact_state','no_contact_lowest','heavy_contact']
    assert report['read_errors']==[dict(path='one',error='truncated tail')]
    assert report['pose_selection']['policy']=='contact_then_score_v1'


def test_failed_pose_copy_does_not_replace_its_matching_score():
    selected=PoseSelector([0,0,0])
    selected.add(pose(1,-8,'old_contact',1),1)
    broken=pose(1,-10,'broken_better_contact',1)
    def fail():raise OSError('Cannot copy decoded pose')
    broken.copy=fail
    with pytest.raises(OSError):selected.add(broken,1)
    assert selected.scores[1]==-8 and selected.poses[1].property['tag']=='old_contact'


def test_excluded_child_poses_cannot_win_contact_selection(tmp_path):
    from test_glide_subjob_timeout import native_request
    request,inputs=native_request(tmp_path)
    for st in inputs[request['inputs'][0]]:
        st.atom=[SimpleNamespace(atomic_number=6,x=0 if 'excluded' in st.property['tag'] else 4,y=0,z=0)]
    request['attraction_point']=[0,0,0]
    report=glide_timeout_results.collect(request,structure_module(inputs))
    selected=json.loads((tmp_path/'extracted/best_poses.maegz').read_text())
    assert [r['properties']['tag'] for r in selected]==['complete','complete']
    assert report['excluded_poses']==2
    assert all(not r['properties']['b_graft_attraction_point_contact'] for r in selected)


def test_glide_pose_policy_is_immutable_and_old_scored_iterations_stay_legacy(tmp_path):
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(name,smiles,iteration,state,docking_score) VALUES ('old','C',1,'docked',-9)")
        conn.execute("INSERT INTO compound(name,smiles,iteration,state) VALUES ('new','C',2,'ligprepped')")
    assert database.freeze_glide_pose_policy(db,1,[0,0,0]) is None
    assert database.freeze_glide_pose_policy(db,2,[0,0,0])==[0.,0.,0.]
    assert database.freeze_glide_pose_policy(db,2,[99,99,99])==[0.,0.,0.]
    assert database.freeze_glide_pose_policy(db,2,None)==[0.,0.,0.]
    assert database.freeze_glide_pose_policy(db,3,None) is None
    assert database.freeze_glide_pose_policy(db,3,[0,0,0]) is None
    assert database.freeze_glide_pose_policy(db,0,[0,0,0]) is None
    with database.connect(db) as conn:
        assert conn.execute('SELECT docking_score FROM compound WHERE iteration=1').fetchone()[0]==-9


def test_contact_native_inputs_retain_multiple_poses_and_constraints(tmp_path):
    group=tmp_path/'ref_1';group.mkdir()
    path=_core.write_constrained_glide_input(group,Path('grid.zip'),Path('lig.mae'),Path('ref.mae'),
        'CC','1,2',poses_per_lig=10)
    text=path.read_text()
    assert 'POSES_PER_LIG 10' in text and 'CORECONS_FALLBACK False' in text and 'PRECISION SP' in text


def test_group_collection_publishes_contact_pose_before_committing_its_score(tmp_path,monkeypatch):
    from graft.stages import glide
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (10,'REF','C',0,'reference')")
        for cid in [1,2,3]:
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) VALUES (?,?,'C',1,'glide_running',10)",
                         (cid,f'CMPID_{cid}'))
    root=tmp_path/'glide';group=root/'ref_10';group.mkdir(parents=True)
    source=group/'glide_lib.maegz';source.write_text('native')
    module=structure_module({str(source):library()})
    monkeypatch.setitem(sys.modules,'schrodinger',SimpleNamespace(structure=module))
    def run(command,**kw):
        monkeypatch.setattr(sys,'argv',[str(x) for x in command[1:]])
        runpy.run_path(str(command[1]),run_name='__main__')
    monkeypatch.setattr(_core,'run_command',run)
    def merge(suite,script,inputs,out,cwd):
        out.write_text(Path(inputs[0]).read_text())
    monkeypatch.setattr(_core,'merge_best_pose_files',merge)
    update=_core.update_scores
    def persist(scores,path):
        published=json.loads((root/'best_poses.maegz').read_text())
        assert published[0]['properties']['tag']=='best_contact_state'
        assert published[0]['properties']['r_i_docking_score']==-9
        return update(scores,path)
    monkeypatch.setattr(_core,'update_scores',persist)
    args=SimpleNamespace(schrodinger=Path('/suite'),db_path=db,attraction_point=[0,0,0])
    point=glide.pose_selection_point(args,1)
    extractor=_core.create_score_extractor(root,attraction_point=point)
    log=group/'glide.log';log.write_text('')
    job=dict(group_dir=group,output_file=source,reference_id=10,log_file=log,
             compounds=[(cid,f'CMPID_{cid}','C',None) for cid in [1,2,3]])
    glide.process_completed_job(1,args,job,extractor,Path('merger.py'),root)
    with database.connect(db) as conn:
        assert conn.execute('SELECT id,docking_score,state FROM compound WHERE iteration=1 ORDER BY id').fetchall()==[
            (1,-9.,'docked'),(2,-8.,'docked'),(3,-5.,'docked')]
